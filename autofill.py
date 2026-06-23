#!/usr/bin/env python3
"""Human-in-the-loop job application autofiller.

Opens an application URL in a REAL (non-headless) browser. You handle login and
any CAPTCHA; then the script fills the fields it recognizes from your profile
(resume + answer bank + per-job answers) and attaches your resume / cover letter.
You review the highlighted fields and click Submit yourself — the script never
submits anything.

Works page-by-page: it fills the current page, then waits while you review and
advance. On multi-page forms (Workday, multi-step Greenhouse) just move to the
next page and autofill it too — repeat until you finish. Continue/finish either
from the terminal (Enter / 'q') or, when there's no terminal, from a button the
script injects at the top of the page (click to continue, close window to finish).

    python autofill.py <url> --job-id deloitte_ds

One-time setup:
    pip install -r requirements.txt
    playwright install chromium
"""

import argparse
import re
import sys
import time
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
    "country":            ["country"],
    "location":           ["location (city", "current location", "where are you located",
                           "city", "metro", "location"],
    "state":              ["state", "province", "region"],
    "zip":                ["zip", "zip code", "postal code", "postcode"],
    "website":            ["website", "portfolio", "personal website", "personal site",
                           "web address"],
    "current_company":    ["current employer", "current company", "present employer"],
    "current_title":      ["current title", "current job title", "current role",
                           "current position"],
    "age_18":             ["18 or older", "at least 18", "over 18", "years of age",
                           "are you 18", "18 years"],
    "school":             ["school", "university", "college", "institution", "where did you study"],
    "degree":             ["degree", "level of education", "highest level of education",
                           "education level"],
    "gender":             ["gender", "sex"],
    "hispanic_ethnicity": ["hispanic", "latino", "latinx"],
    "race":               ["race", "racial", "ethnic background", "identify your race"],
    "veteran_status":     ["veteran", "protected veteran", "military service"],
    "disability_status":  ["disability", "disabled"],
}

# keys typically answered as yes/no on dropdowns or selects
YES_NO = {"work_authorization": "yes", "sponsorship": "no", "relocation": "no",
          "travel_willingness": "yes", "age_18": "yes"}

# Only these keys get auto-selected on radios / custom dropdowns. Everything else
# (unknown choices, free-form selects) is still left highlighted for the user.
# The actual answers are NOT hard-coded here — they come from the user's profile
# (data/application_bank.yaml, git-ignored); this list only says which *kinds* of
# field may be auto-selected.
CHOICE_ALLOWLIST = {
    "work_authorization", "sponsorship", "relocation", "travel_willingness",
    "age_18", "country", "gender", "hispanic_ethnicity", "race", "veteran_status",
    "disability_status",
}

# JS: best-effort question label for a grouped control (radio set / custom listbox)
# by walking up to a fieldset legend or an aria-labelled container.
GROUP_LABEL_JS = r"""
(el) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  let n = el;
  for (let i = 0; i < 6 && n; i++) {
    n = n.parentElement;
    if (!n) break;
    if (n.tagName === 'FIELDSET') {
      const lg = n.querySelector('legend');
      if (lg && norm(lg.innerText)) return norm(lg.innerText);
    }
    const al = n.getAttribute && n.getAttribute('aria-label');
    if (al) return norm(al);
    const lb = n.getAttribute && n.getAttribute('aria-labelledby');
    if (lb) { const r = document.getElementById(lb); if (r) return norm(r.innerText); }
  }
  return '';
}
"""

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


# Floating banner + button injected into the page so the user can drive the
# script from the browser when there's no interactive terminal (e.g. launched
# from an editor/agent where stdin is not a TTY). The button stashes a token in
# window.name, which survives same-tab navigations across login -> form.
_SIGNAL_TOKEN = "AUTOFILL_CONTINUE"
_INJECT_BAR_JS = r"""
(args) => {
  const [barText, label, token] = args;
  if (!document.body || document.getElementById('__autofill_bar__')) return;
  const bar = document.createElement('div');
  bar.id = '__autofill_bar__';
  bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;' +
    'background:#111;color:#fff;font:14px/1.4 sans-serif;padding:10px 14px;' +
    'display:flex;align-items:center;gap:12px;box-shadow:0 2px 8px rgba(0,0,0,.4)';
  const span = document.createElement('span');
  span.textContent = barText;
  span.style.flex = '1';
  const btn = document.createElement('button');
  btn.textContent = label;
  btn.style.cssText = 'background:#16a34a;color:#fff;border:0;border-radius:6px;' +
    'padding:8px 16px;font:bold 14px sans-serif;cursor:pointer;white-space:nowrap';
  btn.onclick = () => { window.name = token; bar.remove(); };
  bar.appendChild(span); bar.appendChild(btn);
  document.body.appendChild(bar);
}
"""


def _active_page(context):
    """The page the user is currently on, or None if the window has been closed."""
    pages = [p for p in context.pages if not p.is_closed()]
    return pages[-1] if pages else None


def wait_for_continue(context, console_msg: str, bar_text: str, label: str) -> bool:
    """Pause between pages. Return True to autofill another page, False to stop.

    This is what makes multi-page forms (Workday, multi-step Greenhouse) work: the
    caller fills the current page, then waits here while the user reviews and either
    advances to the next page (→ fill it too) or finishes.

    TTY: read a line — Enter continues, 'q'/'done' finishes.
    No TTY: drive from the browser — click the injected button to continue, or just
    close the window to finish. Re-injects the banner each tick so it survives
    navigation, and reads back window.name (set by the button) to detect the click."""
    print(console_msg)
    if sys.stdin and sys.stdin.isatty():
        try:
            ans = input(f"   [Enter] = {label.lower()}  ·  [q] = finish: ").strip().lower()
        except EOFError:
            return False
        return ans not in ("q", "quit", "done", "n", "no", "stop")

    print(f"   (click “{label}” at the top of the page, or close the browser to finish)")
    while True:
        page = _active_page(context)
        if page is None:
            return False  # user closed the window → done
        try:
            if page.evaluate("() => window.name") == _SIGNAL_TOKEN:
                page.evaluate("() => { window.name = ''; }")
                return True
            page.evaluate(_INJECT_BAR_JS, [bar_text, label, _SIGNAL_TOKEN])
        except Exception:
            pass  # mid-navigation or page closing; retry next tick
        time.sleep(0.5)


def print_summary(page_num: int, filled: list, skipped: list) -> None:
    print(f"\n=== Autofill summary — page {page_num} ===")
    print(f"Filled ({len(filled)}):")
    for label, key in filled:
        print(f"  + {label[:52]:52} <- {key}")
    if skipped:
        print(f"\nSkipped ({len(skipped)}) — do these yourself:")
        for label, why in skipped:
            print(f"  - {label[:52]:52} ({why})")
    print("\nGreen outline = filled, orange = needs your attention.")


def match_key(label: str) -> str | None:
    """Return the profile key whose longest alias appears in the label as a whole
    word/phrase. Word-boundary matching avoids false hits like 'city' inside
    'ethnicity' or 'race' inside 'embrace'."""
    label = label.lower()
    best_key, best_len = None, 0
    for key, aliases in ALIASES.items():
        for alias in aliases:
            if len(alias) > best_len and re.search(
                r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", label
            ):
                best_key, best_len = key, len(alias)
    return best_key


def highlight(el, color: str) -> None:
    try:
        el.evaluate("(e, c) => { e.style.outline = c; e.scrollIntoView({block: 'center'}); }", color)
    except Exception:
        pass


def candidates_for(key: str, value: str | None) -> list[str]:
    """Ordered list of acceptable option texts (lowercase) for a choice field,
    derived generically from the user's profile value (no answers hard-coded).

    Yields the full answer first, then its leading clause before a comma/paren
    (e.g. "No, not Hispanic or Latino" -> "no"), then a bare yes/no default for
    the known yes/no questions. Matching (exact-then-startswith) means the full
    answer wins when it's present and the short forms only catch phrasing drift."""
    cands: list[str] = []
    if value:
        v = " ".join(value.split()).strip().lower()
        head = re.split(r"[,(/]", v)[0].strip()
        for c in (v, head):
            if c and c not in cands:
                cands.append(c)
    if key in YES_NO and YES_NO[key] not in cands:
        cands.append(YES_NO[key])
    return cands


def _pick_value(options: list[dict], candidates: list[str]) -> str | None:
    """Pick a <select> option value: exact match first, then startswith. Never a
    loose substring, so 'male' can't match 'Female' nor 'no' match 'Innovation'."""
    norm = lambda s: (s or "").strip().lower()
    for cand in candidates:
        for o in options:
            if norm(o["t"]) == cand or norm(o["v"]) == cand:
                return o["v"]
    for cand in candidates:
        for o in options:
            if norm(o["t"]).startswith(cand):
                return o["v"]
    return None


def _pick_element(els: list, candidates: list[str]):
    """Same exact-then-startswith logic, but over clickable elements (radios /
    custom listbox options), returning the element to click."""
    pairs = []
    for el in els:
        try:
            pairs.append((el, (el.inner_text() or "").strip().lower()))
        except Exception:
            continue
    for cand in candidates:
        for el, txt in pairs:
            if txt == cand:
                return el
    for cand in candidates:
        for el, txt in pairs:
            if txt.startswith(cand):
                return el
    return None


def fill_select(el, key: str, value: str) -> bool:
    options = el.evaluate("(e) => Array.from(e.options).map(o => ({v: o.value, t: o.innerText}))")
    chosen = _pick_value(options, candidates_for(key, value))
    if chosen is not None:
        el.select_option(chosen)
        return True
    return False


def process_radios(target, fields, filled, skipped) -> None:
    """Select the right radio in each group whose question matches an allowlisted key."""
    groups: dict[str, list] = {}
    order: list[str] = []
    for el in target.query_selector_all("input[type=radio]"):
        try:
            if not el.is_visible() or not el.is_enabled():
                continue
        except Exception:
            continue
        name = el.get_attribute("name") or el.evaluate(GROUP_LABEL_JS) or repr(el)
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(el)

    for name in order:
        els = groups[name]
        qlabel = els[0].evaluate(GROUP_LABEL_JS)
        key = match_key(qlabel) if qlabel else None
        if key not in CHOICE_ALLOWLIST:
            continue
        cands = candidates_for(key, fields.get(key))
        chosen = None
        for cand in cands:  # exact option-label / value match first
            for el in els:
                opt = (el.evaluate(LABEL_JS) or "").strip().lower()
                val = (el.get_attribute("value") or "").strip().lower()
                if opt == cand or val == cand:
                    chosen = el
                    break
            if chosen:
                break
        if not chosen:
            for cand in cands:  # then startswith
                for el in els:
                    if (el.evaluate(LABEL_JS) or "").strip().lower().startswith(cand):
                        chosen = el
                        break
                if chosen:
                    break
        if chosen is not None:
            try:
                chosen.check()
                highlight(chosen, GREEN)
                filled.append((qlabel, key))
            except Exception as e:
                highlight(chosen, ORANGE)
                skipped.append((qlabel, str(e)[:50]))
        else:
            skipped.append((qlabel or str(name), f"pick manually for '{key}'"))


def process_comboboxes(target, fields, filled, skipped) -> None:
    """Best-effort fill of custom (React) dropdowns for allowlisted choice keys:
    click to open, click the matching option. Anything unmatched is left for you."""
    triggers = target.query_selector_all(
        "[role=combobox], [aria-haspopup=listbox], div[class*=select__control]"
    )
    for trig in triggers:
        try:
            if not trig.is_visible() or not trig.is_enabled():
                continue
        except Exception:
            continue
        label = trig.evaluate(LABEL_JS) or trig.evaluate(GROUP_LABEL_JS)
        key = match_key(label) if label else None
        if key not in CHOICE_ALLOWLIST:
            continue
        cands = candidates_for(key, fields.get(key))
        if not cands:
            continue
        try:
            trig.click()
            target.wait_for_timeout(350)
        except Exception:
            continue
        chosen = _pick_element(target.query_selector_all("[role=option]"), cands)
        if chosen is not None:
            try:
                chosen.click()
                highlight(trig, GREEN)
                filled.append((label, key))
                continue
            except Exception as e:
                highlight(trig, ORANGE)
                skipped.append((label, str(e)[:50]))
        try:
            trig.press("Escape")
        except Exception:
            pass
        highlight(trig, ORANGE)
        skipped.append((label, f"open and pick manually for '{key}'"))


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
        context.new_page().goto(args.url)

        # First page: wait for the user to log in and reach the form.
        proceed = wait_for_continue(
            context,
            "\n>> Log in, dismiss cookie/consent banners, solve any CAPTCHA, and open the\n"
            "   application FORM (first page).",
            "Logged in and on the form? Autofill this page →",
            "Autofill this page",
        )

        # Fill one page per pass. Multi-page forms (Workday, multi-step Greenhouse):
        # review each page, advance to the next, and autofill it too — repeat until
        # you finish & submit (then close the window, or press 'q' in a terminal).
        page_num = 0
        while proceed:
            page = _active_page(context)
            if page is None:
                break
            page_num += 1
            filled, skipped = [], []
            for frame in page.frames:  # ATS forms (Greenhouse, Lever) often live in an iframe
                try:
                    process(frame, fields, attachments, filled, skipped)
                    process_radios(frame, fields, filled, skipped)
                    process_comboboxes(frame, fields, filled, skipped)
                except Exception:
                    continue
            print_summary(page_num, filled, skipped)

            proceed = wait_for_continue(
                context,
                "\n>> Review THIS page (especially eligibility/EEO) and fix anything. Then either\n"
                "   advance to the NEXT page and autofill it, or finish & submit yourself.",
                "On the next page? Autofill it →",
                "Autofill next page",
            )

        try:
            browser.close()
        except Exception:
            pass  # user may have already closed the window


if __name__ == "__main__":
    main()
