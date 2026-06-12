#!/usr/bin/env python3
"""Build resume and cover letter PDFs for a given job ID.

Tailored YAML files must already exist (written by Claude Code) at:
  data/resume_{job_id}.yaml
  data/cover_letter_{job_id}.yaml

To generate them, ask Claude Code: "tailor resume for <job_id>"
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

BASE = Path(__file__).parent

# doc key -> (template, recruiter-facing label used in the output filename)
DOCS = [
    ("resume",       "resume_template.tex",       "Resume"),
    ("cover_letter", "cover_letter_template.tex", "CoverLetter"),
]


def recruiter_filename(data_path: Path, label: str) -> str:
    """Build a send-ready filename like 'AlexRivera_Resume' from the YAML name."""
    data = yaml.safe_load(data_path.read_text()) or {}
    name = str(data.get("name", "")).strip()
    # Keep letters/digits only, collapsing each whitespace run out: "Alex Rivera" -> "AlexRivera".
    clean = re.sub(r"[^A-Za-z0-9]", "", name)
    return f"{clean}_{label}" if clean else label.lower()


def build_all(job_id: str) -> dict[str, Path]:
    outputs = {}
    for doc, template, label in DOCS:
        data_path = BASE / "data" / f"{doc}_{job_id}.yaml"
        if not data_path.exists():
            sys.exit(
                f"Missing {data_path}.\n"
                f"Ask Claude Code to tailor the {doc} for job {job_id} first."
            )
        # Organize on disk by job id, but give the file a recruiter-facing name
        # derived from the candidate's name, e.g.:
        #   output/<job_id>/AlexRivera_Resume.pdf
        out_subdir = f"output/{job_id}"
        output_name = recruiter_filename(data_path, label)
        result = subprocess.run(
            [
                sys.executable,
                str(BASE / "build.py"),
                "--data", str(data_path),
                "--template", template,
                "--output", output_name,
                "--output-dir", out_subdir,
            ],
            capture_output=True,
            text=True,
            cwd=BASE,
        )
        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr, file=sys.stderr)
            sys.exit(f"build.py failed for {doc} ({job_id}) — see output above")
        print(result.stdout.strip())
        outputs[doc] = BASE / out_subdir / f"{output_name}.pdf"
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build resume + cover letter PDFs for a job ID. "
                    "Tailored YAML files must already exist in data/."
    )
    parser.add_argument("--job-id", required=True, help="Job ID (e.g. 352997)")
    args = parser.parse_args()

    outputs = build_all(args.job_id)
    for doc, path in outputs.items():
        print(f"{doc:14}: {path}")


if __name__ == "__main__":
    main()
