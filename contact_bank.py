#!/usr/bin/env python3
"""
Central contact bank — cross-job memory of who you've reached out to.

Per-job contacts live in data/contacts_<job_id>.yaml (one island each). This
bank (data/contacts.yaml, git-ignored PII) aggregates them keyed by email, so
outreach.py can tell when you've *already* contacted a recruiter for another
role at the same company — and draft a warm follow-up instead of a redundant
cold intro. Same human-in-the-loop rule as the rest of the toolkit: it records
intent, you still send.

Record shape (keyed by email, or "name|company" until an email is known):

    lillian.burke@pwc.com:
      name: Lillian Burke
      title: Talent Acquisition Manager
      company: PwC
      linkedin: https://www.linkedin.com/in/...
      history:
        - {job_id: 737212WD, role: Finance Data and AI Manager,
           date: 2026-07-03, channel: cold-intro, status: sent}

History is keyed by (job_id, channel): re-running outreach just refreshes the
date/status of that entry, it never duplicates.

CLI:
    python contact_bank.py                 # list everyone + touch history
    python contact_bank.py pwc             # filter by name / email / company
    python contact_bank.py --mark lillian.burke@pwc.com --job 737212WD --status sent
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

import yaml

from app_profile import DATA, _load_yaml

BANK_PATH = DATA / "contacts.yaml"


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
def _key(email, name, company) -> str:
    """A stable id for a person: their email, else name|company (lowercased)."""
    if email:
        return email.strip().lower()
    return f"{(name or '').strip().lower()}|{(company or '').strip().lower()}"


def load_bank() -> dict:
    if not BANK_PATH.exists():
        return {}
    return _load_yaml(BANK_PATH) or {}


def save_bank(bank: dict) -> None:
    BANK_PATH.write_text(yaml.safe_dump(bank, sort_keys=False, allow_unicode=True))


def today() -> str:
    return datetime.date.today().isoformat()


def days_since(date_str) -> int | None:
    if not date_str:
        return None
    try:
        d = datetime.date.fromisoformat(str(date_str)[:10])
    except ValueError:
        return None
    return (datetime.date.today() - d).days


# --------------------------------------------------------------------------- #
# Read / write helpers
# --------------------------------------------------------------------------- #
def lookup(bank=None, *, email=None, name=None, company=None) -> dict | None:
    """Find a person by email (primary) or name (+company). Reads disk if no
    bank is passed."""
    bank = load_bank() if bank is None else bank
    k = _key(email, name, company)
    if k in bank:
        return bank[k]
    if email:  # a record may be stored under a name-key but carry this email
        el = email.strip().lower()
        for rec in bank.values():
            if (rec.get("email") or "").strip().lower() == el:
                return rec
    if name:
        nl = name.strip().lower()
        for rec in bank.values():
            if (rec.get("name") or "").strip().lower() == nl and (
                not company or (rec.get("company") or "").lower() == company.lower()
            ):
                return rec
    return None


def prior_touches(record, exclude_job_id=None) -> list:
    """History entries for this person, minus the current job."""
    if not record:
        return []
    return [h for h in record.get("history", []) if h.get("job_id") != exclude_job_id]


def record_touch(contact: dict, *, company, job_id, role, channel="cold-intro",
                 status=None, date=None, bank=None) -> dict:
    """Upsert a person and append/update their history entry for this job.

    Returns the bank. If no bank is passed, loads and saves it here; otherwise
    the caller owns saving (so a batch of contacts writes disk once).
    """
    own_bank = bank is None
    bank = load_bank() if own_bank else bank

    email = contact.get("email")
    name = contact.get("name")
    k = _key(email, name, company)

    rec = bank.get(k)
    if rec is None:
        # Reuse an existing record if this person is already known under another
        # key (e.g. was name-keyed before we resolved their email); re-key it.
        existing = lookup(bank, email=email, name=name, company=company)
        if existing is not None:
            old_key = next((kk for kk, vv in bank.items() if vv is existing), None)
            if old_key is not None and old_key != k:
                del bank[old_key]
            rec = existing
        else:
            rec = {}
        bank[k] = rec

    for field, val in (("name", name), ("title", contact.get("title")),
                       ("company", company), ("email", email),
                       ("linkedin", contact.get("linkedin"))):
        if val:
            rec[field] = val

    hist = rec.setdefault("history", [])
    entry = next((h for h in hist
                  if h.get("job_id") == job_id and h.get("channel") == channel), None)
    if entry is None:
        entry = {"job_id": job_id, "channel": channel}
        hist.append(entry)
    entry["role"] = role
    entry["date"] = date or entry.get("date") or today()
    entry["status"] = status or entry.get("status") or "draft"

    if own_bank:
        save_bank(bank)
    return bank


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _fmt_record(rec: dict) -> str:
    head = f"{rec.get('name', '?')} — {rec.get('title', '')}".rstrip(" —")
    email = rec.get("email") or "(no email)"
    comp = f"  @ {rec.get('company')}" if rec.get("company") else ""
    lines = [f"{head}  <{email}>{comp}"]
    for h in rec.get("history", []):
        ds = days_since(h.get("date"))
        ago = f"  ({ds}d ago)" if ds is not None else ""
        lines.append(
            f"    - {h.get('date', '')}  [{h.get('status', '?')}]  "
            f"{h.get('channel', '')} · {h.get('role', '')} ({h.get('job_id', '')}){ago}"
        )
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("query", nargs="?", help="Filter by name / email / company substring")
    p.add_argument("--mark", metavar="EMAIL", help="Update a contact's status for a job")
    p.add_argument("--job", help="Job id for --mark")
    p.add_argument("--status", help="New status for --mark (draft/sent/replied/no-reply)")
    args = p.parse_args()

    bank = load_bank()

    if args.mark:
        if not (args.job and args.status):
            sys.exit("--mark needs --job and --status.")
        rec = lookup(bank, email=args.mark)
        if not rec:
            sys.exit(f"No contact with email {args.mark} in the bank.")
        entry = next((h for h in rec.get("history", []) if h.get("job_id") == args.job), None)
        if not entry:
            sys.exit(f"No history entry for job {args.job} on {args.mark}.")
        entry["status"] = args.status
        save_bank(bank)
        print(f"{args.mark} · {args.job} → {args.status}")
        return

    if not bank:
        print("Contact bank is empty (data/contacts.yaml). It fills in as you run "
              "outreach.py for a job.")
        return

    q = (args.query or "").lower()
    shown = 0
    for rec in bank.values():
        hay = " ".join(str(rec.get(f, "")) for f in ("name", "email", "company", "title")).lower()
        if q and q not in hay:
            continue
        print(_fmt_record(rec))
        shown += 1
    if q and not shown:
        print(f"No contacts match '{args.query}'.")


if __name__ == "__main__":
    main()
