#!/usr/bin/env python3
"""Look up reusable answers from the application question bank.

The bank lives in data/application_bank.yaml. Use this to pull clean,
copy-paste-ready answer text while filling out a job application.

Examples:
  python answers.py                 # list every question id + prompt
  python answers.py location        # show answers matching "location"
  python answers.py --id sponsorship
  python answers.py --todo          # show only answers still needing input
"""

import argparse
import sys
import textwrap
from pathlib import Path

import yaml

DATA = Path(__file__).parent / "data"
BANK = DATA / "application_bank.yaml"


def _read(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text()) or {}
    return data.get("questions", [])


def load_questions(job_id: str | None = None) -> list[dict]:
    """Load the generic bank, optionally overlaying per-job answers.

    A per-job file at data/application_<job_id>.yaml overrides any entry with a
    matching `id` and appends ids that only exist for that job.
    """
    if not BANK.exists():
        sys.exit(f"Question bank not found: {BANK}")
    questions = _read(BANK)

    if not job_id:
        return questions

    job_file = DATA / f"application_{job_id}.yaml"
    if not job_file.exists():
        print(f"(no per-job file {job_file.name}; showing generic answers)\n", file=sys.stderr)
        return questions

    by_id = {q.get("id"): q for q in questions}
    order = [q.get("id") for q in questions]
    for entry in _read(job_file):
        qid = entry.get("id")
        if qid in by_id:
            by_id[qid] = {**by_id[qid], **entry}  # per-job fields win
        else:
            by_id[qid] = entry
            order.append(qid)
    return [by_id[qid] for qid in order]


def matches(q: dict, term: str) -> bool:
    term = term.lower()
    haystack = " ".join(
        [q.get("id", ""), q.get("question", ""), " ".join(q.get("tags", []))]
    ).lower()
    return term in haystack


def is_todo(q: dict) -> bool:
    return str(q.get("answer", "")).strip().startswith("TODO")


def print_full(q: dict) -> None:
    flags = []
    if q.get("tailor"):
        flags.append("TAILOR PER POSTING")
    if is_todo(q):
        flags.append("NEEDS YOUR INPUT")
    tag = f"  [{', '.join(flags)}]" if flags else ""
    print(f"\n### {q.get('id', '?')}{tag}")
    print(f"Q: {q.get('question', '').strip()}")
    answer = " ".join(str(q.get("answer", "")).split())
    print("A: " + "\n   ".join(textwrap.wrap(answer, width=88)))
    if q.get("notes"):
        print(f"   (note: {q['notes'].strip()})")


def print_list(questions: list[dict]) -> None:
    print(f"{len(questions)} question(s) in the bank:\n")
    for q in questions:
        marks = "".join(
            ["*" if q.get("tailor") else " ", "!" if is_todo(q) else " "]
        )
        print(f"  {marks}  {q.get('id', '?'):22}  {q.get('question', '').strip()}")
    print("\n  legend: * = tailor per posting,  ! = needs your input")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("search", nargs="?", help="Term to match against id, question, or tags")
    parser.add_argument("--id", help="Show a single answer by exact id")
    parser.add_argument("--job-id", help="Overlay per-job answers from data/application_<job_id>.yaml")
    parser.add_argument("--todo", action="store_true", help="Show only answers still needing input")
    args = parser.parse_args()

    questions = load_questions(args.job_id)

    if args.id:
        hit = next((q for q in questions if q.get("id") == args.id), None)
        if not hit:
            sys.exit(f"No question with id '{args.id}'. Run with no args to list them.")
        print_full(hit)
        return

    if args.todo:
        todos = [q for q in questions if is_todo(q)]
        if not todos:
            print("Nothing left to fill in — every answer is set.")
            return
        for q in todos:
            print_full(q)
        return

    if args.search:
        hits = [q for q in questions if matches(q, args.search)]
        if not hits:
            sys.exit(f"No questions match '{args.search}'. Run with no args to list them.")
        for q in hits:
            print_full(q)
        return

    print_list(questions)


if __name__ == "__main__":
    main()
