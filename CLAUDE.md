# resume-builder

A job-application toolkit. Core feature: generate a tailored PDF resume (and
cover letter) from YAML data files and LaTeX Jinja2 templates. It also keeps an
**application question bank** for the free-text questions that recur across
applications, with interview prep planned next (see `TODO.md`).

## Setup

```bash
pip install -r requirements.txt
```

pdflatex must be installed (`sudo apt install texlive-latex-extra` if missing).

## Build

```bash
python build.py
# output/resume.pdf
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--data` | `data/resume.yaml` | YAML content file |
| `--template` | `resume_template.tex` | Template in `templates/` |
| `--output` | `resume` | Output filename (no extension) |
| `--output-dir` | `output` | Directory to write to (created if missing) |

## Creating a variant

1. Copy `data/resume.yaml` → e.g. `data/resume_ml.yaml`
2. Edit the copy — trim or reorder sections for the target role
3. Run: `python build.py --data data/resume_ml.yaml --output resume_ml`

## Project library

`data/projects_library.yaml` is the master catalog of real, resume-worthy
projects (sourced from the candidate's GitHub + personal website). The default
`resume.yaml` shows a fixed three; this file holds the full set so each tailored
variant can swap in the projects that actually fit the role.

When tailoring `resume_<job_id>.yaml`, **pick the 2–3 `verified` entries whose
`relevance` tags best match the JD** (and that cover ground the work-experience
section doesn't), then copy their `name`/`url`/`tech`/`date`/`bullets` into the
resume's `projects:` section (drop `relevance`; trim bullets to stay one page).
Entries under `candidates:` are repos not yet written up — read the README and
move one to `verified` with truthful bullets before putting it on a resume.
Never fabricate project content.

## Matching against a job description

`match.py` scores a resume YAML against a job description and lists the JD
keywords the resume is missing. It's the deterministic half of a
human-in-the-loop tailoring cycle: **match.py reports, you (with Claude in the
loop) edit, rebuild, and re-score** until the number is where you want it. It
never edits anything itself, and needs no LLM/API key — the scoring is plain
Python (only `pyyaml`).

The headline number is the **cosine similarity** between the resume and JD as
sublinear term-frequency vectors over their shared vocabulary (stopwords
removed). Alongside it, **keyword coverage** reports how many of the top JD
terms appear in the resume, and the MISSING list is your edit target.

Paste the posting into a text file (or pipe it on stdin):

```bash
python match.py --data data/resume.yaml --jd data/jd_deloitte_ds.txt
python match.py --job-id deloitte_ds          # resume_<id>.yaml + jd_<id>.txt
pbpaste | python match.py --data data/resume_ml.yaml   # JD from stdin
python match.py --job-id deloitte_ds --top 40 --show-present
python match.py --job-id deloitte_ds --digest          # semantic prep for Claude
```

| Flag | Default | Description |
|------|---------|-------------|
| `--data` | `data/resume.yaml` | Resume YAML (or `resume_<job-id>.yaml` via `--job-id`) |
| `--jd` | — | JD text file (or `jd_<job-id>.txt` via `--job-id`, or stdin) |
| `--job-id` | — | Shortcut for `resume_<id>.yaml` + `jd_<id>.txt` under `data/` |
| `--top` | `25` | Number of top JD keywords to analyze |
| `--show-present` | off | Also list the keywords already covered |
| `--digest` | off | Emit a SEMANTIC PREP block for Claude (see below) |

**Synonym awareness (`data/skill_aliases.yaml`).** Cosine over raw words is
vocabulary-bound — `ML` vs `machine learning`, `Postgres` vs `PostgreSQL` would
count as misses. Before scoring, both sides are folded through
`data/skill_aliases.yaml` (a committed, editable `canonical: [variants]` map)
and conservatively singularized (`models` → `model`). Add a line to that file to
teach it a new synonym pair — no code change. Variants must be single tokens;
only add pairs whose surface forms actually differ (terms both sides spell the
same, like `python`/`aws`, need no entry). Verb tense is deliberately *not*
stemmed (naive `-ed`/`-ing` stripping corrupts e-stem verbs like `created`); that
soft-matching is the `--digest` pass's job.

**`--digest` — the semantic pass (Claude as the LLM).** A bag-of-words can't see
that "led a team" ≈ "managed engineers". Rather than bolt on an embedding model,
`--digest` splits the JD into requirement lines, pairs each with its best
*lexical* resume bullet, and sorts weakest-coverage-first. **Claude, in the
session, reads that block and overlays the real semantic judgment** (covered /
partial / missing) plus concrete, truthful edit suggestions. Zero extra deps.

**The two-pass loop.** Drop the posting in `data/jd_<job_id>.txt`, then:
1. `python match.py --job-id <id>` — fast lexical score + MISSING keywords.
2. `python match.py --job-id <id> --digest` — ask Claude to work the weak
   requirements + MISSING list into `resume_<id>.yaml` (only claims that are
   genuinely true).
3. `python build.py` (or `tailor.py`), then re-run step 1 to confirm the score rose.

Repeat until satisfied. Note: a higher score means better keyword/term alignment
for ATS keyword search — it is not a guarantee against rejection (eligibility
filters like work-auth or years-of-experience are separate).

## Application question bank

Reusable answers for the free-text questions that recur on applications
(location rationale, work authorization, travel, salary, etc.) live in
`data/application_bank.yaml`. Each entry has an `id`, the `question`, a reusable
`answer`, `tags`, optional `notes`, and a `tailor` flag for answers that should be
customized per posting. Placeholders start with `TODO:`.

Look them up with `answers.py` (prints clean, copy-paste-ready text):

```bash
python answers.py                 # list every question
python answers.py location        # show answers matching a term
python answers.py --id sponsorship
python answers.py --todo          # only answers still needing your input
```

To add a question, append an entry to `application_bank.yaml` — no code change needed.

**Per-job answers.** Questions whose answer changes per posting (e.g. `why_company`,
`how_heard`) live in a per-job file `data/application_<job_id>.yaml` that overlays the
generic bank. Run with `--job-id` to merge them — entries with a matching `id` override
the generic default, and job-only ids are appended:

```bash
python answers.py --job-id deloitte_ds            # generic bank + Deloitte overrides
python answers.py --job-id deloitte_ds why_company
```

## Ship gate (pre-submission proofread)

The last step before sending an application is a **hybrid gate**: a deterministic
linter establishes ground-truth facts, then the LLM (Claude) layers judgment on
top and gives one ready/not-ready verdict. The two never overlap — `lint.py`
owns everything reducible to a fixed rule; Claude owns only what needs judgment
and treats lint's output as fact rather than re-deriving it.

**Step 1 — deterministic (`lint.py`).** Run the rule-based checks; same inputs
always yield the same findings:

```bash
python lint.py --job-id 350452            # human report; exit code = error count
python lint.py --job-id 350452 --json     # machine-readable, for Claude to consume
```

It verifies the YAML + PDFs exist, contact fields match between resume and cover
letter (and flags drift from the base resume), no unescaped LaTeX specials
(`%` silently eats a line; `& # $` break the build), no leftover `TODO:`, all
required fields present, each doc renders to one page, no line spills past the
text block into the page margin, and every money/percent figure in the cover
letter also appears in the resume (so a number in the letter can't be
fabricated). Severity: **error** blocks shipping; **warn** needs review.

The **margin check** is the deterministic guard against content running off the
edge (a long tech list colliding with its date, a URL past the margin). `pdflatex`
records these as `Overfull \hbox (Npt too wide)` in its `.log`, so `build.py`
**keeps the `.log`** next to each PDF and `lint.py` parses it — the same pattern
as the page-count check reading the PDF via `pdfinfo`. Overflow above
`OVERFULL_ERROR_PT` (2pt in `lint.py`; below that is sub-pixel and invisible) is
an **error**. The fix is always to shorten the offending line, then rebuild &
re-lint.

**Step 2 — judgment (Claude).** Claude reads the `--json` findings as fact, then
checks only what a rule can't:
- **Truthfulness / traceability** — every claim in the resume and cover letter
  must trace to the base resume or to something the user actually stated; no
  invented metrics, titles, or tools. (lint catches fabricated *figures*; Claude
  catches fabricated *claims*.)
- **Tone, grammar, narrative consistency** — clean prose; resume and cover letter
  tell the same story; the letter targets the right company/role.

**Verdict.** Ship only if **lint has zero errors AND Claude finds no truthfulness
red flag.** lint warnings (e.g. an intended email change) are resolved by
judgment, not auto-blocked. This keeps the deterministic/learned boundary clean:
a regression in escaping or page count fails the same way every time, while
overclaim and tone stay with the LLM.

## Autofill (human-in-the-loop)

`autofill.py` opens an application URL in a real browser and fills the fields it
recognizes from your data, then waits for you. **You** handle login + CAPTCHA and
**you** click Submit — it never submits.

```bash
playwright install chromium          # one-time, after pip install
python autofill.py <url> --job-id deloitte_ds
```

Flow: it opens the URL → you log in / solve CAPTCHA / navigate to the form →
**continue** (press Enter in the terminal, or — when launched without an
interactive terminal — click the green button the script injects at the top of
the page) → it fills recognized fields on the current page (green outline) and
attaches the resume/cover letter PDFs, flagging anything it skipped (orange) →
you review.

It works **page by page**: after each page it pauses, and on multi-page forms
(Workday, multi-step Greenhouse) you advance to the next page and continue to
autofill it too — repeat until you finish & submit yourself. End the loop by
pressing `q` in the terminal, or by closing the browser window (no-terminal mode).
Note: heavy SPA forms like Workday use custom widgets and type-ahead
autocompletes, so per-page coverage is partial — text fields fill, exotic
controls are left orange for you.

- `app_profile.py` flattens `resume_<job_id>.yaml` + the answer bank + per-job
  answers into one `{field: value}` profile (including education pulled from the
  resume), and finds the job's PDFs in `output/<job_id>/` by keyword
  (`resume` / `cover`). Inspect with `python app_profile.py --job-id <id>`.
- Field matching lives in `ALIASES` in `autofill.py` (whole-word matching) — add
  label keywords there to recognize more fields. Native inputs, textareas,
  `<select>`, and file inputs are handled, including forms inside an iframe
  (Greenhouse/Lever).
- **Choice fields** (`<select>`, radio groups, and best-effort custom React
  dropdowns) are auto-selected **only for an explicit allowlist** of stable
  demographic/eligibility answers — `CHOICE_HINTS` in `autofill.py`: work
  authorization, sponsorship, relocation, travel, country, gender, Hispanic/Latino
  ethnicity, race, veteran status, disability status. Option matching is
  exact-then-startswith only (never a loose substring), so e.g. `male` can't
  select `Female`. Their values live in `data/application_bank.yaml`.
- Still left for you: checkboxes, any choice **outside** the allowlist, and custom
  dropdowns the script can't open/match (flagged orange). You always review every
  field — especially eligibility/EEO — before submitting.

## Template syntax

The template uses `<< var >>` (variables) and `<% block %>` (loops/conditionals)
instead of `{{ }}` / `{% %}` to avoid clashing with LaTeX braces.

**Inline LaTeX is supported in body text.** Rendering runs with `autoescape=False`
(`build.py`), so raw LaTeX in a YAML string passes through verbatim. To draw a
recruiter's eye, wrap key metrics or JD-critical skills in `\textbf{...}` directly
in a bullet — e.g. `Cut runtime \textbf{40\%}`. This is a human-skim optimization
only (ATS strips formatting; it won't move the `match.py` score), so keep it
sparse: roughly ≤1 bold span per bullet, applied per-job in `resume_<job_id>.yaml`
*after* the score is settled, not in the base `resume.yaml`. `lint.py` doesn't
flag `\` or `{ }`, but the four specials (`% & # $`) still need escaping inside the
braces — so bolding a figure means `\textbf{\$12B}`, not `\textbf{$12B}`.

## Cold outreach (human-in-the-loop)

After you apply, `outreach.py` drafts a short **cold-intro email** to a recruiter
or hiring manager letting them know you applied. Same hybrid split as the ship
gate: the script owns the deterministic half (loads the contact, pulls your real
facts from the resume via `app_profile`, optionally resolves the email with
Hunter.io, assembles a truthful skeleton), and **Claude owns the judgment half** —
writing the one specific `[HOOK]` sentence and creating the Gmail *draft*. Nothing
auto-sends: you review the draft and hit send yourself, mirroring autofill's "you
click Submit."

Contacts for a job live in `data/contacts_<job_id>.yaml` (git-ignored PII; copy
`data/contacts.example.yaml`). Each has `name`, `title`, `email` (or null until
found), `domain`, `linkedin`, `source` (`linkedin`/`hunter`/`manual`), and a
`status` you update (`draft`/`sent`/`replied`). Top-level `company`/`role`/`domain`
apply to all contacts.

**Contact bank (cross-job memory).** The per-job files are siloed, so on its own
`outreach.py` can't tell you already emailed a recruiter for a *different* role at
the same company. `contact_bank.py` closes that gap: it aggregates every per-job
contact into one store, `data/contacts.yaml` (git-ignored PII), keyed by email.
Each draft run upserts the run's contacts (a `history` entry per `job_id`+channel),
and before drafting, `outreach.py` looks each person up — if there's a prior touch
for another job, it prints a `⚠ Prior contact` notice (with a recency guard) and
switches the skeleton from a cold intro to a **warm follow-up** that references the
earlier role. Inspect/curate the bank directly:

```bash
python contact_bank.py                 # list everyone + touch history
python contact_bank.py pwc             # filter by name / email / company
python contact_bank.py --mark jane@x.com --job R123 --status sent   # update status
```

```bash
python outreach.py --search-people "recruiter data" --job-id deloitte_ds  # find who
python outreach.py --find-email --name "Jane Smith" --job-id deloitte_ds --save
python outreach.py --job-id deloitte_ds            # draft package(s) + PREP block
python outreach.py --job-id deloitte_ds --json     # machine-readable, for Claude
python outreach.py --domain-search deloitte.com --dept it   # discover people (Hunter)
python outreach.py --verify jane.smith@deloitte.com
```

The pipeline is **find who → resolve the email → draft → you send**. Keys are read
from a git-ignored `.env` (auto-loaded — no `source` needed; see `.env.example`).

- **Find who — `--search-people` (Serper.dev web search).** LinkedIn has no
  people-search API (deprecated 2015; recruiter data is gated enterprise), and
  scraping it risks your account. So this searches *Google* (via Serper's JSON
  API), restricted to public `linkedin.com/in` profiles — never LinkedIn's API or
  your login. Returns name/headline/profile-URL for you to eyeball. Needs
  `SERPER_API_KEY` (free credits at serper.dev). You still decide *who* — it only
  surfaces candidates. (Backend note: Google's own Custom Search JSON API is
  closed to new Cloud projects — a fresh project 403s with "does not have access"
  no matter how it's configured — so Serper is the drop-in replacement. Swapping
  backends is a ~15-line change in `web_search()`.)
- **Resolve the email — Hunter.io** (`HUNTER_API_KEY`, never commit). `--find-email`
  turns a name + company domain into an address (Email Finder), `--domain-search`
  lists people at a company, `--verify` checks deliverability. `--save` writes the
  result into `contacts_<job_id>.yaml`. Works without a key too — paste an email
  you found manually.
- **LinkedIn automation is still off-limits.** `--search-people` goes *around*
  LinkedIn via Google; don't add scrapers that hit LinkedIn directly.
- **The `[HOOK]` is deliberately left blank by the script** (same no-fabrication
  rule as everywhere else). The default output prints a PREP block — your real
  role bullets + projects + a JD excerpt — and Claude writes ONE truthful sentence
  tying your strongest fact to the role, then creates the Gmail draft. Keep it 1:1
  and personal (not bulk) — that's what gets replies and stays clear of spam.
- Reuses `app_profile.build_profile` for your sender facts and `find_attachments`
  to attach the resume PDF (resume only — the cover letter is application-specific).

## Structure

```
resume-builder/
├── build.py               # Entry point — renders one YAML + template → PDF
├── tailor.py              # Builds resume + cover letter for a job id
├── match.py               # Scores a resume YAML against a JD (cosine + keyword gaps)
├── lint.py                # Deterministic pre-submission checks (ship gate, step 1)
├── answers.py             # Looks up reusable application answers
├── app_profile.py         # Flattens resume + answers into an autofill profile
├── autofill.py            # Human-in-the-loop browser autofiller (Playwright)
├── outreach.py            # Cold-intro drafter + people search (Serper) + email lookup (Hunter)
├── contact_bank.py        # Cross-job contact memory (cold intro vs. warm follow-up)
├── catalog.py             # Readable index over the opaque output/<job_id> folders
├── .env.example           # Template for API keys (copy to .env; git-ignored)
├── requirements.txt
├── data/
│   ├── resume.yaml        # Resume content
│   ├── jd_<job_id>.txt    # Pasted job-description text for match.py (git-ignored)
│   ├── skill_aliases.yaml # Synonym map for match.py (committed, editable)
│   ├── application_bank.yaml      # Reusable application question answers
│   ├── application_<job_id>.yaml  # Per-job answer overrides (git-ignored)
│   ├── contacts.example.yaml      # Template for the outreach contacts file
│   ├── contacts_<job_id>.yaml     # Per-job outreach contacts (git-ignored)
│   ├── contacts.yaml              # Central cross-job contact bank (git-ignored PII)
│   ├── jobs.example.yaml          # Template for the job manifest (committed)
│   └── jobs.yaml                  # Job manifest: id → company/role/status (git-ignored)
├── templates/
│   ├── resume.tex         # Original static template (reference)
│   └── resume_template.tex  # Jinja2 template
└── output/                # Generated PDFs (git-ignored)
    ├── INDEX.md           # Generated by catalog.py — readable map of all jobs
    └── <job_id>/          # tailor.py groups each job's docs here
        ├── AlexRivera_Resume.pdf
        └── AlexRivera_CoverLetter.pdf
```

## Output organization

`tailor.py --job-id <id>` organizes on disk by job id but gives each file a
send-ready, recruiter-facing name derived from the `name:` field in the YAML:

```
output/<id>/<Name>_Resume.pdf
output/<id>/<Name>_CoverLetter.pdf
```

The job id / company live in the *folder* (your organization); the *filename*
carries only the candidate's name + document type (what a recruiter sees in a
folder of downloads). A direct `build.py` run defaults to flat `output/`; pass
`--output-dir` to redirect (e.g. `--output-dir output/<id>`).

### Making the folders readable — `catalog.py`

The `output/<job_id>` folder names are **load-bearing**: `tailor.py`, `lint.py`,
`outreach.py`, and `app_profile.find_attachments` all resolve them by exact job
id, so they can't be renamed to something human-readable without breaking the
pipeline. Instead, `catalog.py` adds a readable layer *on top* of the ids.

The single source of truth is `data/jobs.yaml` (git-ignored PII — it reveals
your whole search; copy `data/jobs.example.yaml`), a manifest keyed by job id:

```yaml
jobs:
  deloitte_ds:
    company: Deloitte
    role: Data Scientist
    status: applied          # built | applied | interview | offer | rejected | withdrawn
```

`catalog.py` joins that manifest with the folders on disk and prints a table
(sorted by status then date), then regenerates `output/INDEX.md`:

```bash
python catalog.py                 # full table + (re)write output/INDEX.md
python catalog.py pwc             # filter by id / company / role substring
python catalog.py --status applied
python catalog.py --no-write      # print only; don't touch INDEX.md
```

By default it's read-only over the folders — never renames or moves anything. The
`DOCS` column shows what's built per folder (`resume` / `cover`), and it flags
drift: folders in `output/` with no manifest entry, manifest entries with no
folder, and companies still left as `TODO:`. Add a job by appending to
`jobs.yaml`; update its `status` as the application progresses and re-run to
refresh the index.

**Archiving closed applications.** As the list grows, sweep the dead ones out of
the top level so only live jobs remain:

```bash
python catalog.py --archive          # move rejected/withdrawn folders → output/_archive/
python catalog.py --unarchive R14545 # move one back to output/
```

`--archive` physically moves every `output/<id>` whose manifest `status` is a
**closed** status (`rejected` or `withdrawn`) into `output/_archive/<id>`; mark a
job closed in `jobs.yaml` first, then sweep. It's the only mutating mode. Archived
jobs stay in the catalog — they move to a separate **Archived** section in the
table and in `INDEX.md`, so nothing is lost — and the pipeline tools (which look
in `output/<id>`, not `_archive/`) simply stop resolving them, which is fine for a
job you're done with. Restore any time with `--unarchive`. Neither direction
overwrites an existing destination folder.
