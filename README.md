# resume-loop

**An AI-in-the-loop toolkit for tailoring resumes and cover letters — and getting past ATS keyword filters — that you drive with [Claude Code](https://claude.com/claude-code).**

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)

You keep your resume as structured YAML. Small, **deterministic** Python tools do
the mechanical work — render a polished LaTeX PDF, score your resume against a job
description, and run pre-submission checks. **Claude Code** does the judgment work
— tailoring bullets to a posting (truthfully), reasoning about semantic fit, and
proofreading before you hit submit. No API key: the intelligence is your Claude
Code session itself.

The design is the point: **every step is either a fixed rule or a judgment call,
and the two never blur.** Rules live in Python (reproducible, inspectable);
judgment lives with you + Claude. That boundary is what makes this a clean,
practical example of putting an AI *in the loop* rather than over the top.

---

## Why this exists

Most resumes die in an **Applicant Tracking System (ATS)** that ranks you on
keyword overlap with the job description before a human ever looks. The usual
advice — "tailor every application" — is real but tedious. This toolkit makes the
loop fast and honest:

> **score → see what's missing → edit truthfully → re-score → proofread → ship.**

It will *not* keyword-stuff or invent experience for you. A higher score means
better keyword alignment for ATS search; it can't move hard eligibility filters
(work authorization, years of experience), and the tools are built to keep you
honest about exactly that.

## The loop

```
            your resume (data/resume.yaml)  +  a job description (text)
                                   │
                 ┌─────────────────┴──────────────────┐
                 ▼                                     ▼
        match.py  (deterministic)            Claude Code  (judgment)
   cosine score + missing keywords     reads the gap, tailors bullets to the
   synonym-aware, --digest semantic    posting — only claims that are TRUE
   prep for Claude                                  │
                 └─────────────────┬──────────────────┘
                                   ▼
                       data/resume_<job>.yaml
                       data/cover_letter_<job>.yaml
                                   │
                       tailor.py → build.py → pdflatex
                                   ▼
                  output/<job>/<Name>_Resume.pdf  + _CoverLetter.pdf
                                   │
                 ┌─────────────────┴──────────────────┐
                 ▼                                     ▼
         lint.py  (deterministic)            Claude Code  (judgment)
   contact match, LaTeX escaping,      truthfulness, tone, narrative — the
   1-page, figure traceability         final "ready to ship?" verdict
                                   ▼
                              submit 🚀
```

## What's in the box

| Tool | Kind | What it does |
|------|------|--------------|
| `build.py` | deterministic | Render one resume YAML + a LaTeX/Jinja2 template → PDF |
| `tailor.py` | deterministic | Build a job's resume **and** cover letter with recruiter-facing filenames |
| `match.py` | deterministic | Score a resume against a job description (cosine + keyword gaps); `--digest` preps a semantic review for Claude |
| `lint.py` | deterministic | Pre-submission "ship gate" checks (contact consistency, LaTeX escaping, page count, figure traceability) |
| `answers.py` | deterministic | Reusable answers for the free-text questions that recur on applications |
| `autofill.py` | deterministic | Human-in-the-loop browser autofiller (you log in & submit; it never does) |
| **Claude Code** | judgment | Tailors bullets, judges semantic fit, proofreads — the AI in the loop |

## Quickstart

```bash
git clone https://github.com/godot107/resume-loop.git
cd resume-loop
pip install -r requirements.txt

# pdflatex is required to render PDFs:
sudo apt install texlive-latex-extra      # Debian/Ubuntu

# Start from the example persona, then make it yours:
cp data/resume.example.yaml          data/resume.yaml
cp data/application_bank.example.yaml data/application_bank.yaml
# edit data/resume.yaml with your real background (it's git-ignored)

python build.py            # → output/resume.pdf
```

Then open this folder in Claude Code and ask it to tailor for a posting (below).
Your real `data/resume.yaml` and `data/application_bank.yaml` are git-ignored, so
your personal details never get committed.

## Tailoring for a job (the core workflow)

1. **Paste the job description** into `data/jd_<job-id>.txt`.
2. **Score it** to see the keyword gap:
   ```bash
   python match.py --data data/resume.yaml --jd data/jd_<job-id>.txt
   ```
   You get a cosine **match score**, **keyword coverage**, and a **MISSING** list.
   It's synonym-aware — `ML`/`machine learning`, `Postgres`/`PostgreSQL` are folded
   via `data/skill_aliases.yaml` (add a line to teach it more).
3. **Go semantic** for the soft matches a bag-of-words can't see:
   ```bash
   python match.py --data data/resume.yaml --jd data/jd_<job-id>.txt --digest
   ```
   This pairs each JD requirement with your best-matching bullet; **Claude reads it
   and judges true coverage**, then edits `data/resume_<job-id>.yaml` —
   reordering and rephrasing to hit the JD's language, **never fabricating**.
4. **Build** the PDFs:
   ```bash
   python tailor.py --job-id <job-id>
   # output/<job-id>/<Name>_Resume.pdf  +  <Name>_CoverLetter.pdf
   ```
5. **Run the ship gate** before you submit:
   ```bash
   python lint.py --job-id <job-id>          # 0 errors = deterministic gate clear
   ```
   Then Claude does the judgment pass (truthfulness, tone) for a final
   ready/not-ready call. Re-score and repeat until you're satisfied.

## The idea: deterministic vs. judgment

Each check belongs to exactly one side, decided by a simple test — *can you write
it as a fixed rule?*

- **Yes → a Python tool.** String equality, regex, counting, cosine arithmetic.
  Same inputs always produce the same output; you can point at the line that
  decided it. (`match.py`, `lint.py` — neither calls an LLM.)
- **No → Claude.** "Is this bullet an overclaim?" "Does the tone fit?" "Is this a
  true synonym?" These need judgment, so they stay with the human + AI.

`lint.py` and Claude form a **hybrid ship gate**: the linter establishes
ground-truth facts (and emits `--json`), Claude consumes those as fact and adds
only the judgment layer. Ship only when **lint has zero errors AND Claude finds no
truthfulness red flag.** Neither re-does the other's job — that's what keeps it
non-redundant.

## Application question bank

Most applications ask the same free-text questions (location, work authorization,
travel, salary, "why this company"). Keep reusable answers in
`data/application_bank.yaml` and pull copy-paste-ready text with `answers.py`:

```bash
python answers.py                 # list every stored question
python answers.py location        # answers matching a term
python answers.py --todo          # answers still needing your input
```

Per-posting answers (e.g. *why this company*) live in
`data/application_<job-id>.yaml` and overlay the generic bank via `--job-id`.

## Autofilling forms (human-in-the-loop)

`autofill.py` opens an application in a real browser, fills the fields it
recognizes from your data, and attaches your PDFs — then **stops**. You handle
login, CAPTCHA, sensitive eligibility answers, and Submit.

```bash
playwright install chromium                       # one-time
python autofill.py <application-url> --job-id <job-id>
```

Radio buttons, checkboxes, and custom dropdowns are intentionally left for you, so
eligibility answers are never auto-selected. Your data stays local — no third-party
extension or cloud profile.

## Optional: track jobs in a Google Sheet

If you drive Claude Code with the Google Drive MCP, you can keep a jobs sheet
(Job ID, Company, Title, Link, Description, Status, Notes) and ask Claude to read a
row and tailor from it. This is entirely optional — the toolkit works fully from
local `data/jd_<job-id>.txt` files with no Google setup.

## Project structure

```
resume-loop/
├── build.py                    # Render YAML + template → PDF via pdflatex
├── tailor.py                   # Build a job's resume + cover letter PDFs
├── match.py                    # Score resume vs JD (cosine + keyword gaps, --digest)
├── lint.py                     # Deterministic pre-submission ship-gate checks
├── answers.py                  # Look up reusable application answers
├── app_profile.py / autofill.py# Human-in-the-loop form autofiller
├── data/
│   ├── resume.example.yaml             # Example persona — copy to resume.yaml
│   ├── application_bank.example.yaml    # Example answers — copy to application_bank.yaml
│   ├── skill_aliases.yaml              # Synonym map for match.py (editable)
│   ├── resume.yaml                     # YOUR resume (git-ignored)
│   ├── resume_<job>.yaml / jd_<job>.txt# Per-job files (git-ignored)
│   └── cover_letter_<job>.yaml         # Per-job cover letter (git-ignored)
├── templates/
│   ├── resume_template.tex             # Jinja2 resume template
│   └── cover_letter_template.tex       # Jinja2 cover letter template
└── output/                             # Generated PDFs (git-ignored)
    └── <job>/<Name>_Resume.pdf  +  <Name>_CoverLetter.pdf
```

Templates use `<< var >>` and `<% block %>` delimiters (instead of `{{ }}` /
`{% %}`) so they don't clash with LaTeX braces.

## Credits

- Resume LaTeX template adapted from [Jake Gutierrez's resume](https://github.com/jakegut/resume) (MIT), itself based on [sb2nov/resume](https://github.com/sb2nov/resume).
- Built as an example of an **AI-in-the-loop** workflow with Claude Code.

## License

[MIT](LICENSE) — free to use, modify, and share. Contributions and forks welcome;
the hope is this helps people land jobs.
