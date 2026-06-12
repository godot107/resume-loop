#!/usr/bin/env python3
"""Human-in-the-loop job application autofiller.

Opens an application URL in a REAL (non-headless) browser. You handle login and
any CAPTCHA; then the script fills the fields it recognizes from your profile
(resume + answer bank + per-job answers) and attaches your resume / cover letter.
You review the highlighted fields and click Submit yourself — the script never
submits anything.

    python autofill.py <url> --job-id deloitte_ds

One-time setup:
    pip install -r requirements.txt
    playwright install chromium
"""

import argparse
import sys
from pathlib import Path

from app_profile import build_profile

try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:
    sys.exit(
        "Playwright is not installed.\n"
        "  pip install -r requirements.txt\n"
        "  playwright install chromium"
    )

GREEN = "2px solid #16a34a"
ORANGE = "2px solid #f59e0b"

# semantic profile key -> keywords that may appear in a field's label / name / placeholder
ALIASES: dict[str, list[str]] = {
    "first_name":         ["first name", "given name", "legal first name", "fname"],
    "last_name":          ["last name", "surname", "family name", "legal last name", "lname"],
    "full_name":          ["full name", "your name", "legal name", "candidate name"],
    "email":              ["email", "e-mail"],
    "phone":              ["phone", "mobile", "telephone", "cell"],
    "linkedin":           ["linkedin"],
    "github":             ["github"],
    "location_comments":  ["rationale for your location", "location preference", "location comment"],
    "relocation":         ["willing to relocate", "relocate", "relocation"],
    "work_authorization": ["authorized to work", "work authorization", "legally authorized"],
    "sponsorship":        ["require sponsorship", "visa sponsorship", "sponsorship"],
    "salary_base":        ["base salary", "base pay", "annual base", "desired base"],
    "salary_total":       ["total compensation", "total comp", "total target compensation",
                           "ote", "on-target earnings"],
    "salary_expectations":["salary expectation", "desired salary", "expected salary",
                           "compensation expectation", "salary"],
    "availability":       ["notice period", "when can you start", "start date", "availability"],
    "how_heard":          ["how did you hear", "referral source"],
    "why_company":        ["why are you interested", "why do you want to work", "why this",
                           "cover letter", "additional information"],
    "travel_willingness": ["willing to travel", "travel requirement"],
}

# keys typically answered as yes/no on dropdowns or selects
YES_NO = {"work_authorization": "yes", "sponsorship": "no", "relocation": "no", "travel_willingness": "yes"}

# JS: best-effort human-readable label for a form element
LABEL_JS = r"""
(el) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  let txt = '';
  if (el.id) {
    const esc = (window.CSS && CSS.escape) ? CSS.escape(el.id) : el.id;
    const l = document.querySelector('label[for="' + esc + '"]');
    if (l) txt = l.innerText;
  }
  if (!txt && el.closest('label')) txt = el.closest('label').innerText;
  if (!txt) txt = el.getAttribute('aria-label') || '';
  if (!txt && el.getAttribute('aria-labelledby')) {
    const r = document.getElementById(el.getAttribute('aria-labelledby'));
    if (r) txt = r.innerText;
  }
  if (!txt) txt = el.getAttribute('placeholder') || '';
  if (!txt) txt = el.getAttribute('name') || '';
  return norm(txt);
}
"""


def match_key(label: str) -> str | None:
    """Return the profile key whose longest alias is contained in the label."""
    label = label.lower()
    best_key, best_len = None, 0
    for key, aliases in ALIASES.items():
        for alias in aliases:
            if alias in label and len(alias) > best_len:
                best_key, best_len = key, len(alias)
    return best_key


def highlight(el, color: str) -> None:
    try:
        el.evaluate("(e, c) => { e.style.outline = c; e.scrollIntoView({block: 'center'}); }", color)
    except Exception:
        pass


def fill_select(el, key: str, value: str) -> bool:
    options = el.evaluate("(e) => Array.from(e.options).map(o => ({v: o.value, t: o.innerText}))")
    want = YES_NO.get(key)

    def pick(pred) -> str | None:
        for o in options:
            if pred((o["t"] or "").strip().lower(), (o["v"] or "").strip().lower()):
                return o["v"]
        return None

    chosen = pick(lambda t, v: t.startswith(want) or v == want or t == want) if want else None
    if not chosen and value:
        first_word = value.split()[0].lower()
        chosen = pick(lambda t, v: first_word and first_word in t)
    if chosen is not None:
        el.select_option(chosen)
        return True
    return False


def process(target, fields, attachments, filled, skipped) -> None:
    """Fill recognized fields within a page or frame."""
    for el in target.query_selector_all("input, textarea, select"):
        try:
            if not el.is_visible() or not el.is_enabled():
                continue
        except Exception:
            continue
        tag = el.evaluate("e => e.tagName.toLowerCase()")
        itype = (el.get_attribute("type") or "text").lower() if tag == "input" else tag
        if itype in ("hidden", "submit", "button", "image", "reset", "file",
                     "checkbox", "radio", "password"):
            continue
        label = el.evaluate(LABEL_JS)
        if not label:
            continue
        key = match_key(label)
        if not key:
            continue
        value = fields.get(key)
        if not value:
            highlight(el, ORANGE)
            skipped.append((label, f"no value for '{key}'"))
            continue
        try:
            if tag == "select":
                ok = fill_select(el, key, value)
            else:
                el.fill(value)
                ok = True
            if ok:
                highlight(el, GREEN)
                filled.append((label, key))
            else:
                highlight(el, ORANGE)
                skipped.append((label, f"couldn't choose option for '{key}'"))
        except Exception as e:
            highlight(el, ORANGE)
            skipped.append((label, str(e)[:60]))

    # File inputs (resume / cover letter)
    file_inputs = target.query_selector_all("input[type=file]")
    for idx, el in enumerate(file_inputs):
        label = (el.evaluate(LABEL_JS) or "").lower()
        kind = path = None
        if "cover" in label:
            kind, path = "cover_letter", attachments.get("cover_letter")
        elif "resume" in label or "cv" in label:
            kind, path = "resume", attachments.get("resume")
        elif len(file_inputs) == 1 or idx == 0:
            kind, path = "resume", attachments.get("resume")
        elif idx == 1:
            kind, path = "cover_letter", attachments.get("cover_letter")
        if path and Path(path).exists():
            try:
                el.set_input_files(str(path))
                highlight(el, GREEN)
                filled.append((label or kind, f"file:{kind}"))
            except Exception as e:
                highlight(el, ORANGE)
                skipped.append((label or kind, f"upload failed: {str(e)[:50]}"))
        else:
            highlight(el, ORANGE)
            skipped.append((label or "file upload", f"no {kind} file found"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Human-in-the-loop application autofiller.")
    parser.add_argument("url", help="Application form URL")
    parser.add_argument("--job-id", help="Use resume_<job_id>.yaml + per-job answers + that job's PDFs")
    args = parser.parse_args()

    fields, attachments = build_profile(args.job_id)
    if not fields:
        sys.exit("Empty profile — check data/resume.yaml and data/application_bank.yaml.")
    print(f"Profile: {len(fields)} fields | attachments: {', '.join(attachments) or 'none'}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.goto(args.url)

        input(
            "\n>> Log in, dismiss cookie/consent banners, solve any CAPTCHA, and open the\n"
            "   application FORM. Then press Enter here to autofill..."
        )

        page = context.pages[-1]  # in case login opened a new tab
        filled, skipped = [], []
        for frame in page.frames:  # ATS forms (Greenhouse, Lever) often live in an iframe
            try:
                process(frame, fields, attachments, filled, skipped)
            except Exception:
                continue

        print("\n=== Autofill summary ===")
        print(f"Filled ({len(filled)}):")
        for label, key in filled:
            print(f"  + {label[:52]:52} <- {key}")
        if skipped:
            print(f"\nSkipped ({len(skipped)}) — do these yourself:")
            for label, why in skipped:
                print(f"  - {label[:52]:52} ({why})")
        print("\nGreen outline = filled, orange = needs your attention.")

        input(
            "\n>> Review EVERY field (especially eligibility questions), fix anything, and\n"
            "   click Submit yourself. Press Enter to close the browser when done..."
        )
        browser.close()


if __name__ == "__main__":
    main()
