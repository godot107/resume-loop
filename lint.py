#!/usr/bin/env python3
"""Deterministic pre-submission checks for a job's resume + cover letter.

This is the ground-truth half of the ship gate. Every check here is a fixed
rule (string equality, regex, counting) — same inputs always produce the same
findings, no model, no judgment. The LLM proofread step runs this, treats its
output as fact, and layers on the judgment-only checks (truthfulness, tone)
before deciding whether the package is ready to submit. See the "Ship gate"
section in CLAUDE.md.

It checks, for a job id:
  - both YAML files and the built PDFs exist
  - contact fields match between resume and cover letter (and flags drift from
    the base resume)
  - no unescaped LaTeX specials (% silently eats a line; & # $ break the build)
  - no leftover TODO: placeholders, and required fields are present
  - the resume / cover letter render to a single page
  - every money/percent figure in the cover letter also appears in the resume
    (a fabricated metric in the letter has nothing to trace back to)

Usage:
  python lint.py --job-id 350452            # human-readable report + exit code
  python lint.py --job-id 350452 --json     # machine-readable findings
  python lint.py --data data/resume_x.yaml --cover data/cover_letter_x.yaml

Exit code = number of ERROR findings (0 = clean), so it can gate a script.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

BASE = Path(__file__).parent
DATA = BASE / "data"
OUTPUT = BASE / "output"

CONTACT_FIELDS = ("name", "phone", "email", "linkedin", "linkedin_url", "github", "github_url")
REQUIRED_RESUME = ("name", "email", "phone", "experience")
# LaTeX specials that must be backslash-escaped in body text. '%' is the
# dangerous one: it silently comments out the rest of the line rather than
# erroring, so a typo can drop content without failing the build.
LATEX_ERROR_SPECIALS = ("%", "&", "#", "$")
MONEY_RE = re.compile(r"\$\s?\d[\d,\.]*\s*(?:billion|million|thousand|[kmb])?", re.IGNORECASE)
PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s?%")


class Findings:
    """Collects (severity, check, message) and renders to text or JSON."""

    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, severity: str, check: str, message: str) -> None:
        self.items.append({"severity": severity, "check": check, "message": message})

    def error(self, check: str, msg: str) -> None:
        self.add("error", check, msg)

    def warn(self, check: str, msg: str) -> None:
        self.add("warn", check, msg)

    def ok(self, check: str, msg: str) -> None:
        self.add("ok", check, msg)

    def count(self, severity: str) -> int:
        return sum(1 for i in self.items if i["severity"] == severity)


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text()) or {}


def _string_leaves(node, skip_url: bool = True):
    """Yield every string value, skipping URL-ish fields (keys ending _url/url)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if skip_url and (str(k) == "url" or str(k).endswith("_url")):
                continue
            yield from _string_leaves(v, skip_url)
    elif isinstance(node, list):
        for v in node:
            yield from _string_leaves(v, skip_url)
    elif isinstance(node, str):
        if not node.startswith("http"):
            yield node


def _find_unescaped(text: str, char: str) -> bool:
    # A special is OK only if immediately preceded by a backslash.
    return bool(re.search(r"(?<!\\)" + re.escape(char), text))


def _figures(node) -> set[str]:
    """Normalized set of money/percent figures across all body text."""
    figs: set[str] = set()
    for s in _string_leaves(node):
        raw = s.replace("\\", "")
        for m in MONEY_RE.findall(raw) + PERCENT_RE.findall(raw):
            figs.add(re.sub(r"\s+", "", m).lower())
    return figs


def _page_count(pdf: Path) -> int | None:
    if not pdf.exists():
        return None
    try:
        out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    if out.returncode != 0:
        return None
    for line in out.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    return None


def lint(resume_path: Path, cover_path: Path, job_id: str | None, f: Findings) -> None:
    resume = _load(resume_path)
    cover = _load(cover_path)
    base = _load(DATA / "resume.yaml")

    # --- files present -------------------------------------------------------
    if resume is None:
        f.error("files", f"resume YAML not found: {resume_path}")
    if cover is None:
        f.warn("files", f"cover letter YAML not found: {cover_path}")
    if resume is None:
        return  # nothing else is checkable

    # --- required fields -----------------------------------------------------
    missing = [k for k in REQUIRED_RESUME if not resume.get(k)]
    if missing:
        f.error("required", f"resume missing required field(s): {', '.join(missing)}")
    else:
        f.ok("required", "all required resume fields present")

    # --- contact consistency: resume vs cover --------------------------------
    if cover is not None:
        mismatches = [
            k for k in CONTACT_FIELDS
            if str(resume.get(k, "")).strip() != str(cover.get(k, "")).strip()
        ]
        if mismatches:
            for k in mismatches:
                f.error("contact",
                        f"{k} differs: resume={resume.get(k)!r} cover={cover.get(k)!r}")
        else:
            f.ok("contact", "contact fields match between resume and cover letter")

    # --- email/phone drift from base resume (warn, not error) ----------------
    if base:
        for k in ("email", "phone"):
            if resume.get(k) and base.get(k) and resume[k] != base[k]:
                f.warn("base-drift",
                       f"{k} {resume[k]!r} differs from base resume {base[k]!r} — intended?")

    # --- LaTeX specials ------------------------------------------------------
    spec_hits = []
    for label, doc in (("resume", resume), ("cover", cover)):
        if doc is None:
            continue
        for s in _string_leaves(doc):
            for ch in LATEX_ERROR_SPECIALS:
                if _find_unescaped(s, ch):
                    spec_hits.append((label, ch, s.strip()[:60]))
    if spec_hits:
        for label, ch, snippet in spec_hits:
            f.error("latex", f"unescaped '{ch}' in {label}: \"{snippet}…\" (use \\{ch})")
    else:
        f.ok("latex", "no unescaped LaTeX specials (% & # $) in body text")

    # --- TODO placeholders ---------------------------------------------------
    todos = []
    for label, doc in (("resume", resume), ("cover", cover)):
        if doc is None:
            continue
        for s in _string_leaves(doc):
            if "TODO" in s:
                todos.append((label, s.strip()[:60]))
    if todos:
        for label, snippet in todos:
            f.error("todo", f"leftover TODO in {label}: \"{snippet}…\"")
    else:
        f.ok("todo", "no leftover TODO placeholders")

    # --- cover letter has company / title ------------------------------------
    if cover is not None:
        for k in ("company", "job_title", "date"):
            if not str(cover.get(k, "")).strip():
                f.warn("cover-meta", f"cover letter is missing '{k}'")

    # --- figure traceability: cover figures must appear in resume ------------
    if cover is not None:
        resume_figs = _figures(resume)
        for fig in sorted(_figures(cover)):
            if fig not in resume_figs:
                f.warn("figures",
                       f"figure {fig!r} in cover letter not found in resume — verify it is real")

    # --- page counts ---------------------------------------------------------
    if job_id:
        folder = OUTPUT / job_id
        for kind, pat in (("resume", "*Resume*.pdf"), ("cover letter", "*Cover*.pdf")):
            pdfs = list(folder.glob(pat)) if folder.exists() else []
            if not pdfs:
                f.warn("pdf", f"{kind} PDF not found in {folder} — run tailor.py")
                continue
            pages = _page_count(pdfs[0])
            if pages is None:
                f.warn("pdf", f"could not read page count for {pdfs[0].name} (pdfinfo missing?)")
            elif pages > 1:
                f.error("pdf", f"{kind} is {pages} pages ({pdfs[0].name}); trim to 1")
            else:
                f.ok("pdf", f"{kind} is 1 page")


def _render_human(f: Findings, job_id: str | None) -> None:
    mark = {"error": "✗", "warn": "⚠", "ok": "✓"}
    print(f"\nLint — {'job ' + job_id if job_id else 'resume/cover'}")
    print("=" * 60)
    for sev in ("error", "warn", "ok"):
        for i in [x for x in f.items if x["severity"] == sev]:
            print(f"  {mark[sev]} [{i['check']}] {i['message']}")
    print("=" * 60)
    e, w = f.count("error"), f.count("warn")
    verdict = "BLOCKED — fix errors before shipping" if e else (
        "PASS (with warnings to review)" if w else "PASS — deterministic checks clean")
    print(f"  {e} error(s), {w} warning(s).  {verdict}")
    if not e:
        print("  Deterministic gate clear; LLM still owns truthfulness + tone judgment.")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--job-id", help="Lint data/resume_<id>.yaml + cover_letter_<id>.yaml + output/<id> PDFs")
    parser.add_argument("--data", help="Resume YAML (overrides --job-id default)")
    parser.add_argument("--cover", help="Cover letter YAML (overrides --job-id default)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable findings")
    args = parser.parse_args()

    if args.data:
        resume_path = Path(args.data)
    elif args.job_id:
        resume_path = DATA / f"resume_{args.job_id}.yaml"
    else:
        resume_path = DATA / "resume.yaml"

    if args.cover:
        cover_path = Path(args.cover)
    elif args.job_id:
        cover_path = DATA / f"cover_letter_{args.job_id}.yaml"
    else:
        cover_path = DATA / "cover_letter.yaml"

    f = Findings()
    lint(resume_path, cover_path, args.job_id, f)

    if args.json:
        print(json.dumps({
            "job_id": args.job_id,
            "summary": {s: f.count(s) for s in ("error", "warn", "ok")},
            "ready": f.count("error") == 0,
            "findings": f.items,
        }, indent=2, ensure_ascii=False))
    else:
        _render_human(f, args.job_id)

    sys.exit(f.count("error"))


if __name__ == "__main__":
    main()
