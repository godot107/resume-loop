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
required fields present, each doc renders to one page, and every money/percent
figure in the cover letter also appears in the resume (so a number in the letter
can't be fabricated). Severity: **error** blocks shipping; **warn** needs review.

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

Flow: it opens the URL → you log in / solve CAPTCHA / navigate to the form and
press Enter → it fills recognized fields (green outline) and attaches the
resume/cover letter PDFs, flagging anything it skipped (orange) → you review and
submit.

- `app_profile.py` flattens `resume_<job_id>.yaml` + the answer bank + per-job
  answers into one `{field: value}` profile, and finds the job's PDFs in
  `output/<job_id>/` by keyword (`resume` / `cover`). Inspect with
  `python app_profile.py --job-id <id>`.
- Field matching lives in `ALIASES` in `autofill.py` — add label keywords there to
  recognize more fields. Native inputs, textareas, `<select>`, and file inputs are
  handled, including forms inside an iframe (Greenhouse/Lever).
- Not handled on purpose: radio buttons, checkboxes, and custom React dropdowns —
  these are left highlighted for you, so sensitive eligibility answers are never
  mis-selected automatically.

## Template syntax

The template uses `<< var >>` (variables) and `<% block %>` (loops/conditionals)
instead of `{{ }}` / `{% %}` to avoid clashing with LaTeX braces.

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
├── requirements.txt
├── data/
│   ├── resume.yaml        # Resume content
│   ├── jd_<job_id>.txt    # Pasted job-description text for match.py (git-ignored)
│   ├── skill_aliases.yaml # Synonym map for match.py (committed, editable)
│   ├── application_bank.yaml      # Reusable application question answers
│   └── application_<job_id>.yaml  # Per-job answer overrides (git-ignored)
├── templates/
│   ├── resume.tex         # Original static template (reference)
│   └── resume_template.tex  # Jinja2 template
└── output/                # Generated PDFs (git-ignored)
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
