#!/usr/bin/env python3
"""Render a human-readable index over the opaque output/<job_id> folders.

The folders in output/ are named by ATS job id (load-bearing — every tool
resolves output/<job_id>/ by exact match, so they can't be renamed). This script
joins each folder to data/jobs.yaml (id -> company/role/status), reports what's
built in each, and writes a browsable table to output/INDEX.md.

    python catalog.py                 # print the table (all jobs)
    python catalog.py pwc             # filter by id / company / role substring
    python catalog.py --status applied
    python catalog.py --no-write      # print only; don't (re)write output/INDEX.md

By default it's a read-only view + an index file — it never renames folders. The
one exception is explicit decluttering: `--archive` moves closed applications
(status rejected/withdrawn) into output/_archive/ so the top level shows only live
jobs; `--unarchive <id>` moves one back. Archived jobs stay in the index (a
separate section), so nothing is lost.
"""

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

BASE = Path(__file__).parent
DATA = BASE / "data"
OUTPUT = BASE / "output"
ARCHIVE = OUTPUT / "_archive"

STATUS_ORDER = ["offer", "interview", "applied", "built", "rejected", "withdrawn"]
CLOSED_STATUSES = {"rejected", "withdrawn"}


def _load_manifest() -> dict:
    path = DATA / "jobs.yaml"
    if not path.exists():
        raise SystemExit(
            "data/jobs.yaml not found — copy data/jobs.example.yaml to it and fill "
            "in your jobs (see the header in that file)."
        )
    return (yaml.safe_load(path.read_text()) or {}).get("jobs", {}) or {}


def _folder_docs(folder: Path) -> tuple[bool, bool]:
    """(has_resume, has_cover) from the PDFs present in a job folder."""
    has_resume = has_cover = False
    if folder.exists():
        for pdf in folder.glob("*.pdf"):
            low = pdf.name.lower()
            if "cover" in low:
                has_cover = True
            elif "resume" in low or "cv" in low:
                has_resume = True
    return has_resume, has_cover


def _folder_date(folder: Path) -> str:
    if not folder.exists():
        return ""
    mtime = max((p.stat().st_mtime for p in folder.iterdir()), default=folder.stat().st_mtime)
    return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d")


def _scan_dir(d: Path) -> set[str]:
    """Job-id folders directly under d (skips the _archive dir and dotfiles)."""
    if not d.exists():
        return set()
    return {p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith("_")}


def collect() -> list[dict]:
    """Join manifest entries with output/ folders. Includes rows for folders with
    no manifest entry (flagged) and manifest entries with no folder (flagged).
    Each row is tagged location: 'active' (output/<id>), 'archived'
    (output/_archive/<id>), or '' (no folder on disk)."""
    manifest = _load_manifest()
    # str() the keys — YAML turns bare-numeric ids like 10460360 into ints.
    manifest = {str(k): (v or {}) for k, v in manifest.items()}
    active_ids = _scan_dir(OUTPUT)
    archived_ids = _scan_dir(ARCHIVE) - active_ids  # active wins if a stray dup exists
    folder_ids = active_ids | archived_ids

    rows = []
    for job_id in sorted(set(manifest) | folder_ids):
        entry = manifest.get(job_id, {})
        location = "active" if job_id in active_ids else "archived" if job_id in archived_ids else ""
        folder = (ARCHIVE if location == "archived" else OUTPUT) / job_id
        has_resume, has_cover = _folder_docs(folder)
        rows.append({
            "id": job_id,
            "company": str(entry.get("company", "")).strip(),
            "role": str(entry.get("role", "")).strip(),
            "status": str(entry.get("status", "")).strip() or ("built" if folder_ids and job_id in folder_ids else ""),
            "date": _folder_date(folder),
            "resume": has_resume,
            "cover": has_cover,
            "in_manifest": job_id in manifest,
            "in_output": job_id in folder_ids,
            "location": location,
            "archived": location == "archived",
        })
    return rows


def archive_closed() -> list[str]:
    """Move active output/<id> folders whose manifest status is closed
    (rejected/withdrawn) into output/_archive/<id>. Returns the ids moved."""
    active_ids = _scan_dir(OUTPUT)
    manifest = {str(k): (v or {}) for k, v in _load_manifest().items()}
    moved = []
    for job_id in sorted(active_ids):
        status = str(manifest.get(job_id, {}).get("status", "")).strip().lower()
        if status not in CLOSED_STATUSES:
            continue
        dest = ARCHIVE / job_id
        if dest.exists():
            print(f"  skip {job_id}: {dest.relative_to(BASE)} already exists")
            continue
        ARCHIVE.mkdir(exist_ok=True)
        shutil.move(str(OUTPUT / job_id), str(dest))
        moved.append(job_id)
        print(f"  archived {job_id} ({status}) → {dest.relative_to(BASE)}")
    return moved


def unarchive(job_id: str) -> bool:
    """Move output/_archive/<id> back to output/<id>. Returns True if moved."""
    src = ARCHIVE / job_id
    dest = OUTPUT / job_id
    if not src.exists():
        print(f"  {job_id} is not in {ARCHIVE.relative_to(BASE)}")
        return False
    if dest.exists():
        print(f"  {dest.relative_to(BASE)} already exists — not overwriting")
        return False
    shutil.move(str(src), str(dest))
    print(f"  restored {job_id} → {dest.relative_to(BASE)}")
    return True


def _sort_key(r: dict):
    try:
        s = STATUS_ORDER.index(r["status"])
    except ValueError:
        s = len(STATUS_ORDER)
    return (s, _neg_date(r["date"]))


def _neg_date(d: str) -> str:
    # Sort dates newest-first within a status group.
    return "".join(str(9 - int(c)) if c.isdigit() else c for c in d)


def _docs_cell(r: dict) -> str:
    if not r["in_output"]:
        return "— (no folder)"
    if not (r["resume"] or r["cover"]):
        return "empty"
    parts = []
    if r["resume"]:
        parts.append("resume")
    if r["cover"]:
        parts.append("cover")
    return "+".join(parts)


def filter_rows(rows, term: str | None, status: str | None):
    if status:
        rows = [r for r in rows if r["status"] == status.lower()]
    if term:
        t = term.lower()
        rows = [r for r in rows if t in r["id"].lower()
                or t in r["company"].lower() or t in r["role"].lower()]
    return rows


def _company_display(company: str) -> str:
    company = company or "?"
    return "⚠ (needs company)" if company.startswith("TODO") else company


def render_text(rows) -> str:
    if not rows:
        return "No matching jobs."
    lines, warns = [], []
    companies = {r["id"]: _company_display(r["company"]) for r in rows}
    w_co = max(7, *(len(c) for c in companies.values()))
    w_role = max(4, *(min(46, len(r["role"])) for r in rows))
    w_id = max(6, *(len(r["id"]) for r in rows))
    header = f"{'DATE':<10}  {'STATUS':<10}  {'COMPANY':<{w_co}}  {'ROLE':<{w_role}}  {'DOCS':<12}  {'JOB ID':<{w_id}}"
    lines.append(header)
    lines.append("-" * len(header))
    for r in rows:
        co = companies[r["id"]]
        if (r["company"] or "").startswith("TODO"):
            warns.append(r["id"])
        role = (r["role"] or "")[:46]
        lines.append(
            f"{r['date']:<10}  {r['status']:<10}  {co:<{w_co}}  {role:<{w_role}}  "
            f"{_docs_cell(r):<12}  {r['id']:<{w_id}}"
        )
    lines.append("")
    lines.append(f"{len(rows)} active job(s).")
    orphans = [r["id"] for r in rows if not r["in_manifest"]]
    if orphans:
        lines.append(f"⚠ In output/ but not in jobs.yaml: {', '.join(orphans)}")
    missing = [r["id"] for r in rows if not r["in_output"]]
    if missing:
        lines.append(f"⚠ In jobs.yaml but no output/ folder: {', '.join(missing)}")
    if warns:
        lines.append(f"⚠ Company still 'TODO' in jobs.yaml: {', '.join(sorted(set(warns)))}")
    return "\n".join(lines)


def render_archived_text(rows) -> str:
    if not rows:
        return ""
    lines = ["", f"Archived ({len(rows)}) — in output/_archive/, restore with --unarchive <id>:"]
    for r in rows:
        co = _company_display(r["company"])
        lines.append(f"  {r['date']}  {r['status']:<10}  {co} — {r['role']}  [{r['id']}]")
    return "\n".join(lines)


def _md_table(rows) -> list[str]:
    out = [
        "| Date | Status | Company | Role | Docs | Job ID |",
        "|------|--------|---------|------|------|--------|",
    ]
    for r in rows:
        co = r["company"] or "?"
        if co.startswith("TODO"):
            co = "⚠ _needs company_"
        out.append(
            f"| {r['date']} | {r['status']} | {co} | {r['role']} | "
            f"{_docs_cell(r)} | `{r['id']}` |"
        )
    return out


def render_markdown(active, archived) -> str:
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [
        "# Application index",
        "",
        f"_Generated by `catalog.py` on {now}. Edit `data/jobs.yaml`, not this file._",
        "",
        f"## Active ({len(active)})",
        "",
        *_md_table(active),
        "",
    ]
    orphans = [r["id"] for r in active if not r["in_manifest"]]
    if orphans:
        out.append(f"> ⚠ In `output/` but missing from `jobs.yaml`: {', '.join(f'`{o}`' for o in orphans)}")
        out.append("")
    if archived:
        out += [f"## Archived ({len(archived)})", "",
                "_Moved to `output/_archive/`; restore with `python catalog.py --unarchive <id>`._",
                "", *_md_table(archived), ""]
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Readable index over output/<job_id> folders.")
    ap.add_argument("term", nargs="?", help="Filter by id / company / role substring")
    ap.add_argument("--status", help="Only show jobs with this status")
    ap.add_argument("--no-write", action="store_true", help="Don't (re)write output/INDEX.md")
    ap.add_argument("--archive", action="store_true",
                    help="Move closed jobs (rejected/withdrawn) into output/_archive/")
    ap.add_argument("--unarchive", metavar="ID", help="Move output/_archive/<ID> back to output/")
    args = ap.parse_args()

    if args.archive:
        print("Archiving closed applications:")
        moved = archive_closed()
        print(f"{len(moved)} folder(s) archived.\n" if moved else "Nothing to archive.\n")
    if args.unarchive:
        unarchive(args.unarchive)
        print()

    rows = sorted(collect(), key=_sort_key)
    shown = filter_rows(rows, args.term, args.status)
    active = [r for r in shown if not r["archived"]]
    archived = [r for r in shown if r["archived"]]
    print(render_text(active))
    archived_text = render_archived_text(archived)
    if archived_text:
        print(archived_text)

    # INDEX.md always reflects the full (unfiltered) set.
    if not args.no_write and OUTPUT.exists():
        all_rows = sorted(collect(), key=_sort_key)
        index = OUTPUT / "INDEX.md"
        index.write_text(render_markdown(
            [r for r in all_rows if not r["archived"]],
            [r for r in all_rows if r["archived"]],
        ))
        print(f"\nWrote {index.relative_to(BASE)} ({len(all_rows)} jobs).")


if __name__ == "__main__":
    main()
