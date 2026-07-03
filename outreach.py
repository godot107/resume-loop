#!/usr/bin/env python3
"""Cold-outreach helper: introduce yourself directly to a recruiter / hiring
manager after you've applied.

Same hybrid split as the rest of the toolkit: this script owns the
*deterministic* half — it loads the contact, pulls your real facts from the
resume (via app_profile), optionally finds/verifies the email with Hunter.io,
and assembles a truthful skeleton email. **Claude owns the judgment half** —
writing the one specific hook sentence and creating the Gmail draft. Nothing
here ever sends: you review the draft and hit send yourself.

Contacts for a job live in data/contacts_<job_id>.yaml (git-ignored, PII):

    company: Deloitte
    role: Data Scientist
    job_url: https://...
    contacts:
      - name: Jane Smith
        title: Data Science Recruiter
        email: jane.smith@deloitte.com   # or null until found
        domain: deloitte.com
        linkedin: https://linkedin.com/in/janesmith
        source: hunter                    # hunter | linkedin | manual
        status: draft                     # draft | sent | replied (you update)

Examples:
  python outreach.py --search-people "recruiter data" --job-id deloitte_ds   # find who
  python outreach.py --find-email --name "Jane Smith" --job-id deloitte_ds --save
  python outreach.py --job-id deloitte_ds                 # draft package(s) for review
  python outreach.py --job-id deloitte_ds --json          # machine-readable, for Claude
  python outreach.py --domain-search deloitte.com --dept it
  python outreach.py --verify jane.smith@deloitte.com

Keys are read from environment variables (auto-loaded from a git-ignored .env;
see .env.example):
  HUNTER_API_KEY   Hunter.io email lookup — https://hunter.io (free ~50/mo)
  SERPER_API_KEY   Serper.dev web search — https://serper.dev (free credits)
--search-people uses Serper (Google results as JSON); it never touches LinkedIn's
API or your account.
"""

import argparse
import json
import os
import sys
import textwrap
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

from app_profile import build_profile, find_attachments, _load_yaml, DATA

HUNTER_BASE = "https://api.hunter.io/v2"


# --------------------------------------------------------------------------- #
# Contacts file
# --------------------------------------------------------------------------- #
def contacts_path(job_id: str) -> Path:
    return DATA / f"contacts_{job_id}.yaml"


def load_contacts(job_id: str) -> dict:
    path = contacts_path(job_id)
    if not path.exists():
        sys.exit(
            f"No contacts file: {path.name}\n"
            f"Create it (see data/contacts.example.yaml) or run --find-email "
            f"with --save to start one."
        )
    return _load_yaml(path)


def save_contacts(job_id: str, data: dict) -> None:
    path = contacts_path(job_id)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    print(f"Wrote {path.name}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Hunter.io (deterministic lookup half) — stdlib only, no extra deps.
# --------------------------------------------------------------------------- #
def _load_dotenv() -> None:
    """Load KEY=value lines from a project-root .env into os.environ, without
    clobbering vars already set in the shell. Keeps HUNTER_API_KEY out of git
    (.env is ignored) while letting the script find it with no manual `source`."""
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def _hunter_key() -> str:
    _load_dotenv()
    key = os.environ.get("HUNTER_API_KEY", "").strip()
    if not key:
        sys.exit(
            "HUNTER_API_KEY is not set. Export your Hunter.io key first:\n"
            "  export HUNTER_API_KEY=xxxxxxxx\n"
            "(or skip Hunter and paste an email you found manually into the "
            "contacts file)."
        )
    return key


def _hunter_get(endpoint: str, params: dict) -> dict:
    params = {**params, "api_key": _hunter_key()}
    url = f"{HUNTER_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        sys.exit(f"Hunter.io error {e.code}: {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"Hunter.io request failed: {e.reason}")
    return payload.get("data", {})


def _split_name(name: str) -> tuple[str, str]:
    parts = name.split()
    if len(parts) < 2:
        return name, ""
    return parts[0], " ".join(parts[1:])


def find_email(name: str, domain: str) -> dict:
    """Hunter Email Finder → {email, confidence (score), ...}."""
    first, last = _split_name(name)
    data = _hunter_get(
        "email-finder",
        {"domain": domain, "first_name": first, "last_name": last},
    )
    return {
        "name": name,
        "email": data.get("email"),
        "confidence": data.get("score"),
        "title": (data.get("position") or None),
        "linkedin": (data.get("linkedin_url") or None),
        "domain": domain,
        "source": "hunter",
    }


def verify_email(email: str) -> dict:
    """Hunter Email Verifier → {status, score, ...}."""
    return _hunter_get("email-verifier", {"email": email})


def domain_search(domain: str, department: str | None) -> list[dict]:
    """Hunter Domain Search → list of people found at a company."""
    params = {"domain": domain, "limit": 25}
    if department:
        params["department"] = department
    data = _hunter_get("domain-search", params)
    people = []
    for e in data.get("emails", []):
        name = " ".join(p for p in (e.get("first_name"), e.get("last_name")) if p)
        people.append(
            {
                "name": name or "(unknown)",
                "email": e.get("value"),
                "title": e.get("position"),
                "department": e.get("department"),
                "confidence": e.get("confidence"),
                "linkedin": e.get("linkedin"),
            }
        )
    return people


# --------------------------------------------------------------------------- #
# Web search via Serper.dev (find the right person, without touching LinkedIn).
# Serper returns Google results as JSON — we query public linkedin.com/in pages,
# never LinkedIn's API or your account. (Google's own Custom Search JSON API is
# closed to new projects, hence this backend.) Free credits at serper.dev.
# stdlib only.
# --------------------------------------------------------------------------- #
SERPER_SEARCH_URL = "https://google.serper.dev/search"


def _serper_key() -> str:
    _load_dotenv()
    key = os.environ.get("SERPER_API_KEY", "").strip()
    if not key:
        sys.exit(
            "People search needs SERPER_API_KEY in .env.\n"
            "  Sign up (free credits) at https://serper.dev → copy your API key\n"
            "  → add SERPER_API_KEY=... to .env (see .env.example)."
        )
    return key


def web_search(query: str, num: int = 10) -> list[dict]:
    """Serper.dev Google search → list of organic results (title/link/snippet)."""
    body = json.dumps({"q": query, "num": min(max(num, 1), 10)}).encode()
    req = urllib.request.Request(
        SERPER_SEARCH_URL,
        data=body,
        headers={"X-API-KEY": _serper_key(), "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        sys.exit(f"Serper.dev error {e.code}: {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"Serper.dev request failed: {e.reason}")
    return payload.get("organic", [])


def _parse_linkedin_result(item: dict) -> dict:
    """A LinkedIn result title looks like 'Jane Smith - DS Recruiter - Deloitte
    | LinkedIn'. Split it into name / headline / profile URL."""
    import re

    raw = re.sub(r"\s*[|\-–—]\s*LinkedIn\s*$", "", item.get("title", "")).strip()
    parts = [p.strip() for p in re.split(r"\s+[-–—]\s+", raw) if p.strip()]
    name = parts[0] if parts else raw
    headline = " - ".join(parts[1:]) if len(parts) > 1 else item.get("snippet", "")[:80]
    return {
        "name": name,
        "title": headline,
        "linkedin": item.get("link"),
        "source": "linkedin",
    }


def search_people(terms: str, company: str | None, num: int = 10) -> tuple[str, list[dict]]:
    """Google-search public LinkedIn profiles for people matching `terms`
    (e.g. 'recruiter data') at `company`. Returns (query, people)."""
    q = ["site:linkedin.com/in"]
    if company:
        q.append(f'"{company}"')
    if terms:
        q.append(terms)
    query = " ".join(q)
    return query, [_parse_linkedin_result(i) for i in web_search(query, num)]


# --------------------------------------------------------------------------- #
# Draft assembly (deterministic skeleton; Claude fills the [HOOK]).
# --------------------------------------------------------------------------- #
HOOK_PLACEHOLDER = (
    "[HOOK: one truthful sentence tying your background to this role — "
    "Claude fills from the PREP block, or write your own]"
)


def _contact_line(fields: dict) -> str:
    bits = [
        fields.get("email"),
        fields.get("phone"),
        fields.get("linkedin_url") or fields.get("linkedin"),
        fields.get("website_url") or fields.get("website"),
    ]
    return " · ".join(b for b in bits if b)


def build_draft(job_id: str, contact: dict, meta: dict) -> dict:
    """Assemble one draft package for a single contact.

    Everything here is factual (pulled from the resume) except HOOK_PLACEHOLDER,
    which is deliberately left for Claude / you to fill — the script never
    invents a claim about your fit.
    """
    fields, _ = build_profile(job_id)
    # A cold intro attaches the resume only (the body references it); the cover
    # letter is application-specific and left off.
    all_attachments = find_attachments(job_id)
    attachments = {"resume": all_attachments["resume"]} if "resume" in all_attachments else {}

    first = _split_name(contact.get("name", ""))[0] or "there"
    company = meta.get("company", "your team")
    role = meta.get("role", "")
    full_name = fields.get("full_name", "")
    cur_title = fields.get("current_title", "")
    cur_company = fields.get("current_company", "")

    if role:
        subject = f"Application for {role} — {full_name}".rstrip(" —")
        applied_line = (
            f"I recently applied for the {role} position at {company} and "
            f"wanted to reach out directly to introduce myself."
        )
    else:
        subject = f"Introduction — {full_name}".rstrip(" —")
        applied_line = (
            f"I recently applied for a role at {company} and wanted to reach "
            f"out directly to introduce myself."
        )

    intro = ""
    if cur_title and cur_company:
        intro = f"I'm currently a {cur_title} at {cur_company}. "
    elif cur_title:
        intro = f"I'm currently a {cur_title}. "

    body = (
        f"Hi {first},\n\n"
        f"{applied_line}\n\n"
        f"{intro}{HOOK_PLACEHOLDER}\n\n"
        f"I'd welcome a brief conversation about how my background fits what "
        f"your team is looking for. My resume is attached for reference, and "
        f"I'm happy to share more.\n\n"
        f"Thanks for your time,\n"
        f"{full_name}\n"
        f"{_contact_line(fields)}"
    )

    return {
        "job_id": job_id,
        "to": contact.get("email"),
        "to_name": contact.get("name"),
        "to_title": contact.get("title"),
        "from": fields.get("email"),
        "subject": subject,
        "body": body,
        "attachments": [str(p) for p in attachments.values()],
        "contact": contact,
    }


# --------------------------------------------------------------------------- #
# PREP block — the semantic hand-off to Claude (like match.py --digest).
# --------------------------------------------------------------------------- #
def prep_block(job_id: str) -> str:
    """Facts Claude needs to write a truthful, specific hook + refine tone."""
    resume = _load_yaml(_resume_for(job_id))
    lines = ["=== PREP FOR CLAUDE — write the [HOOK] from these facts only ==="]

    exp = resume.get("experience") or []
    if exp:
        cur = exp[0]
        lines.append(f"Current role: {cur.get('title')} @ {cur.get('company')}")
        for b in (cur.get("bullets") or [])[:3]:
            lines.append(f"  - {b}")

    projects = resume.get("projects") or []
    if projects:
        lines.append("Projects on this resume:")
        for p in projects:
            lines.append(f"  - {p.get('name')}: {(p.get('bullets') or [''])[0]}")

    jd = DATA / f"jd_{job_id}.txt"
    if jd.exists():
        text = " ".join(jd.read_text().split())
        lines.append("")
        lines.append(f"JD excerpt: {textwrap.shorten(text, width=600)}")

    lines.append("")
    lines.append(
        "TASK: replace [HOOK] with ONE specific, truthful sentence connecting "
        "the strongest fact above to this role. No invented metrics, titles, or "
        "tools. Then create a Gmail draft (never send)."
    )
    return "\n".join(lines)


def _resume_for(job_id: str) -> Path:
    tailored = DATA / f"resume_{job_id}.yaml"
    return tailored if tailored.exists() else DATA / "resume.yaml"


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def print_draft(pkg: dict) -> None:
    print("=" * 72)
    to = pkg["to"] or "(no email yet — run --find-email)"
    title = f" · {pkg['to_title']}" if pkg.get("to_title") else ""
    print(f"To:      {pkg['to_name']}{title}  <{to}>")
    print(f"From:    {pkg['from']}")
    print(f"Subject: {pkg['subject']}")
    if pkg["attachments"]:
        print("Attach:  " + ", ".join(Path(a).name for a in pkg["attachments"]))
    else:
        print("Attach:  (no PDFs found — run tailor.py for this job id)")
    print("-" * 72)
    print(pkg["body"])
    print("=" * 72)


def print_people(people: list[dict]) -> None:
    if not people:
        print("No results found.")
        return
    print(f"{len(people)} result(s):\n")
    for p in people:
        conf = f" ({p['confidence']}%)" if p.get("confidence") is not None else ""
        # Hunter results carry an email; LinkedIn search results carry a URL.
        extra = p.get("email") or p.get("linkedin") or ""
        title = (p.get("title") or "")[:34]
        print(f"  {p['name']:24} {title:34} {extra}{conf}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--job-id", help="Use data/contacts_<job_id>.yaml + resume_<job_id>.yaml")
    parser.add_argument("--json", action="store_true", help="Machine-readable draft package(s) for Claude")
    parser.add_argument("--prep", action="store_true", help="Print the PREP block for Claude to write the hook")

    parser.add_argument("--search-people", metavar="TERMS", help="Google-search public LinkedIn profiles (e.g. 'recruiter data'); pair with --company or --job-id")
    parser.add_argument("--find-email", action="store_true", help="Hunter Email Finder (needs --name + --domain or --job-id)")
    parser.add_argument("--domain-search", metavar="DOMAIN", help="Hunter Domain Search — list people at a company")
    parser.add_argument("--verify", metavar="EMAIL", help="Hunter Email Verifier — check deliverability of an address")
    parser.add_argument("--name", help="Full name for --find-email")
    parser.add_argument("--company", help="Company name for --search-people (or taken from the contacts file via --job-id)")
    parser.add_argument("--domain", help="Company email domain for --find-email (e.g. deloitte.com)")
    parser.add_argument("--dept", help="Department filter for --domain-search (e.g. it, hr, executive)")
    parser.add_argument("--save", action="store_true", help="Append/update the found contact in the contacts file (needs --job-id)")
    args = parser.parse_args()

    # --- Google: find the right person -------------------------------------
    if args.search_people is not None:
        company = args.company
        if args.job_id and not company:
            company = load_contacts(args.job_id).get("company")
        query, people = search_people(args.search_people, company)
        if args.json:
            print(json.dumps(people, indent=2, ensure_ascii=False))
        else:
            print(f"query: {query}\n")
            print_people(people)
            if people:
                print("\nPick a name, then resolve the email:\n"
                      "  python outreach.py --find-email --name \"<name>\" "
                      "--job-id <id> --save")
        return

    # --- Hunter: verify -----------------------------------------------------
    if args.verify:
        result = verify_email(args.verify)
        print(json.dumps(result, indent=2) if args.json else
              f"{args.verify}: status={result.get('status')} score={result.get('score')}")
        return

    # --- Hunter: domain search ---------------------------------------------
    if args.domain_search:
        people = domain_search(args.domain_search, args.dept)
        if args.json:
            print(json.dumps(people, indent=2))
        else:
            print_people(people)
        return

    # --- Hunter: find one email --------------------------------------------
    if args.find_email:
        domain = args.domain
        name = args.name
        if args.job_id and not domain:
            domain = load_contacts(args.job_id).get("domain")
        if not (name and domain):
            sys.exit("--find-email needs --name and --domain (or --domain from the contacts file).")
        hit = find_email(name, domain)
        if args.json:
            print(json.dumps(hit, indent=2))
        else:
            print(f"{hit['name']}: {hit['email'] or '(not found)'} "
                  f"(confidence {hit['confidence']})  title={hit['title']}")
        if args.save:
            if not args.job_id:
                sys.exit("--save needs --job-id.")
            path = contacts_path(args.job_id)
            data = _load_yaml(path) if path.exists() else {"domain": domain, "contacts": []}
            data.setdefault("contacts", [])
            existing = next((c for c in data["contacts"] if c.get("name") == name), None)
            entry = {k: v for k, v in hit.items() if v is not None}
            entry.setdefault("status", "draft")
            if existing:
                existing.update(entry)
            else:
                data["contacts"].append(entry)
            save_contacts(args.job_id, data)
        return

    # --- Draft mode (default) ----------------------------------------------
    if not args.job_id:
        sys.exit("Pass --job-id (or a Hunter flag). See --help.")

    doc = load_contacts(args.job_id)
    meta = {k: doc.get(k) for k in ("company", "role", "job_url") if doc.get(k)}
    contacts = doc.get("contacts") or []
    if not contacts:
        sys.exit(f"No contacts listed in {contacts_path(args.job_id).name}.")

    packages = [build_draft(args.job_id, c, meta) for c in contacts]

    if args.json:
        print(json.dumps(packages, indent=2, ensure_ascii=False))
        return

    for pkg in packages:
        print_draft(pkg)
    if args.prep or any(HOOK_PLACEHOLDER in p["body"] for p in packages):
        print("\n" + prep_block(args.job_id))


if __name__ == "__main__":
    main()
