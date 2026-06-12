#!/usr/bin/env python3
"""Flatten resume + application bank (+ per-job answers) into one autofill profile.

Used by autofill.py. Run directly to inspect what would be filled:

    python app_profile.py --job-id deloitte_ds
"""

import argparse
import json
from pathlib import Path

import yaml

from answers import load_questions

BASE = Path(__file__).parent
DATA = BASE / "data"
OUTPUT = BASE / "output"


def _load_yaml(path: Path) -> dict:
    return (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}


def _resume_path(job_id: str | None) -> Path:
    if job_id:
        tailored = DATA / f"resume_{job_id}.yaml"
        if tailored.exists():
            return tailored
    return DATA / "resume.yaml"


def find_attachments(job_id: str | None) -> dict[str, Path]:
    """Locate the resume / cover letter PDFs for a job by keyword in the filename,
    so it works regardless of the exact naming convention used."""
    attachments: dict[str, Path] = {}
    if not job_id:
        return attachments
    folder = OUTPUT / job_id
    if not folder.exists():
        return attachments
    for pdf in sorted(folder.glob("*.pdf")):
        low = pdf.name.lower()
        if "cover" in low:
            attachments.setdefault("cover_letter", pdf)
        elif "resume" in low or "cv" in low:
            attachments.setdefault("resume", pdf)
    return attachments


def build_profile(job_id: str | None = None) -> tuple[dict[str, str], dict[str, Path]]:
    """Return (fields, attachments) for autofill.

    fields maps a semantic key (first_name, email, work_authorization, ...) to a
    ready-to-type string. TODO placeholders from the bank are skipped.
    """
    resume = _load_yaml(_resume_path(job_id))
    fields: dict[str, str] = {}

    name = str(resume.get("name", "")).strip()
    if name:
        parts = name.split()
        fields["first_name"] = parts[0]
        fields["last_name"] = " ".join(parts[1:]) if len(parts) > 1 else ""
        fields["full_name"] = name
    for key in ("email", "phone", "linkedin", "github", "linkedin_url", "github_url"):
        val = resume.get(key)
        if val:
            fields[key] = str(val).strip()

    # Application answers: generic bank overlaid with per-job answers; drop TODO placeholders.
    for q in load_questions(job_id):
        ans = " ".join(str(q.get("answer", "")).split())
        if ans and not ans.startswith("TODO"):
            fields[q["id"]] = ans

    return fields, find_attachments(job_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the autofill profile for inspection.")
    parser.add_argument("--job-id", help="Use resume_<job_id>.yaml + per-job answers + that job's PDFs")
    args = parser.parse_args()

    fields, attachments = build_profile(args.job_id)
    print(json.dumps(fields, indent=2, ensure_ascii=False))
    print("\nAttachments:")
    if attachments:
        for k, v in attachments.items():
            print(f"  {k}: {v}")
    else:
        print("  (none found — run tailor.py for this job id first)")


if __name__ == "__main__":
    main()
