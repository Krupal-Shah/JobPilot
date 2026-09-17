#!/usr/bin/env python3
"""
Job application autofill assistant.

Ported from the job-apply-bot project's apply.py, adapted to this app's
own multi-user SQLite data model (see load_profile/load_jobs/save_jobs/
resume_for below) instead of a single static profile.json/jobs.csv. Every
field-filling function past this adapter layer is unchanged.

Launches via SeleniumBase UC Mode (its stealth patches are worth keeping),
then attaches Playwright to that same Chrome process over CDP and drives
every actual page interaction through Playwright's locator API instead of
raw Selenium calls. Playwright locators auto-wait and always re-resolve
against the live DOM at the moment of action, rather than trusting a
selector/focus state captured earlier -- that mismatch (stale data-af-id
tags after a React re-render, ambient "active element" not being the field
we just typed into, Keys.* special codes not translating under UC mode's
CDP layer) was the root cause of most of the reliability problems this
tool had when it was pure Selenium/SeleniumBase.

Fills the boilerplate (name, contact, school, resume, expected graduation
date, common Yes/No screening questions) using this app's per-user profile
+ resume. When everything is filled, or --review-all is set, it pauses for
you to verify and submit by hand before continuing. If a single job stalls
for more than JOB_TIMEOUT seconds, it's abandoned and the batch moves on to
the next one rather than hanging indefinitely.

Usage:
    ./venv/bin/python apply.py --user-id 1 --batch 20
    ./venv/bin/python apply.py --user-id 1 --batch 20 --review-all   # always pause, never auto-submit
"""
import argparse
import random
import re
import select
import signal
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from seleniumbase import SB
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from services.applications import SqliteApplicationService
from services.dto import ApplicationStage
from services.profiles import SqliteProfileService
from services.resumes import get_master_pdf
from services.resume_tailoring import TailoringError

sys.stdout.reconfigure(line_buffering=True)

ROOT = Path(__file__).parent
SCREENSHOT_DIR = ROOT / "screenshots"
SUMMARY_PATH = ROOT / "summary.md"
CHROME_PROFILE_DIR = ROOT / "chrome_user_profile"  # persistent -- so a one-time login
# (e.g. Greenhouse's native "Autofill my application") survives across separate runs
# instead of starting from a fresh, logged-out browser profile every time.

USER_ID = None  # set from --user-id in __main__ before run_batch() is called

LABEL_TIMEOUT = 700
FIELD_PROMPT_TIMEOUT = 45  # absorbs relay round-trip lag (prompt -> queue.txt
# write) when answers are fed through the pipe instead of typed live. Was 90s specifically to
# also cover slow character-by-character typing eating into the same window -- no longer
# needed now that typing is instant (delay=0), so this only has to cover reaction time.
JOB_TIMEOUT = 120  # sec -- abandon a stalled job and move to the next one rather than hang
CHECKPOINT_TIMEOUT = 300  # sec -- for the bot-wall pause and final review checkpoint: long enough
# for a present human to actually act, short enough that an unattended overnight run doesn't
# stall the whole batch on job 1. Never auto-defaults to "applied" on timeout -- see NeedsReviewTimeout.

AMBIGUOUS_MENTION_RE = re.compile(r"\bTN\b|\bOPT\b", re.I)
BOT_WALL_RE = re.compile(
    r"unusual activity from your device or network|automated \(bot\) activity|"
    r"performing security verification|verify you are human|checking your browser|"
    r"access is temporarily restricted|attention required.*cloudflare",
    re.I,
)

class JobStalledError(BaseException):
    """
    Raised by the JOB_TIMEOUT alarm. Subclasses BaseException (not
    Exception) on purpose, like KeyboardInterrupt/SystemExit -- the codebase
    has many broad `except Exception:` blocks (deliberately, for per-field
    fault tolerance) that would otherwise silently swallow this and prevent
    the whole-job abort it's meant to trigger.
    """
    pass


def _raise_job_stalled(signum, frame):
    raise JobStalledError(f"No progress in {JOB_TIMEOUT}s")


class NeedsReviewTimeout(BaseException):
    """
    Raised when the bot-wall pause or the final review checkpoint gets no
    response within CHECKPOINT_TIMEOUT -- e.g. you're asleep. Subclasses
    BaseException for the same reason as JobStalledError: it must not be
    swallowed by a broad `except Exception:`. Distinct from JobStalledError
    because this isn't a stall -- automation finished its part fine, it's
    just waiting on a human decision that never came in time. Never causes
    an auto "applied" write; the job is left as "needs_review" so the batch
    keeps moving and you can finish it manually later.
    """
    pass


def lower_xpath(text: str) -> str:
    """
    Helper to generate case-insensitive string matching expressions in XPath.
    Uses "." (string-value, includes descendant text) rather than "text()" (direct
    text children only) -- otherwise this misses any button/label whose visible
    text is wrapped in a nested <span>/<strong>/etc, which most styled UI components do.
    """
    clean_text = text.replace("'", "")
    return f"contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{clean_text.lower()}')"


def lower_xpath_own_text(text: str) -> str:
    """
    Same idea as lower_xpath, but for broad "//*[...]" whole-document scans
    where lower_xpath's "." (full descendant string-value) is dangerous:
    it bubbles all the way up, so //*[contains(., 'first name')] matches
    <html>/<body> themselves whenever that phrase appears ANYWHERE on the
    page -- even inert JSON inside a <script> tag (confirmed on Rippling's
    Next.js page: an i18n translation blob buried "first name" as a label
    string, making a "has the form rendered" check report true before the
    form ever existed, silently skipping the "Apply now" click entirely).
    text() only matches an element's own direct text-node children, so
    <html>/<body> (which hold zero direct text, only more elements) can
    never match this way -- only real, specific elements can.
    """
    clean_text = text.replace("'", "")
    return (
        f"text()[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
        f"'abcdefghijklmnopqrstuvwxyz'), '{clean_text.lower()}')]"
    )


def clickable_xpath(pattern: str) -> str:
    """
    Builds an XPath matching any clickable-looking element whose visible
    text OR value/alt attribute contains the given pattern, case-insensitively.
    Grows as new "looks like a button but isn't a <button>" shapes get
    confirmed on real ATS pages -- covered so far:
      - <button>, <a>                          -- text via "."
      - <input type="button"|"submit"|"image">  -- text via @value/@alt, not
        text content (confirmed on BlackRock's tal.net gate page: its
        "Upload Resume" control is a plain <input type="button" value="...">,
        which a button/a-only search silently never matches)
      - *[role="button"]                        -- custom div/span widgets
        (React/MUI-style components), same reasoning as role="option" for
        dropdowns elsewhere in this file
    """
    text_match = lower_xpath(pattern)
    value_match = text_match.replace("translate(.,", "translate(@value,")
    alt_match = text_match.replace("translate(.,", "translate(@alt,")
    return (
        f"//button[{text_match}] | "
        f"//a[{text_match}] | "
        f"//*[@role='button'][{text_match}] | "
        f"//input[(@type='button' or @type='submit') and {value_match}] | "
        f"//input[@type='image' and {alt_match}]"
    )


def get_safe_xpath_snippet(text: str) -> str:
    """Removes special characters to safely query partial label matches."""
    clean = "".join(c for c in text if c.isalnum() or c.isspace()).strip()
    return clean[:15].lower()


def xpath_literal(text: str) -> str:
    """Quotes a string for XPath even if it contains both ' and " characters."""
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text:
        return f'"{text}"'
    parts = text.split("'")
    return "concat('" + "', \"'\", '".join(parts) + "')"


def slugify_filename(text: str) -> str:
    """Sanitizes free text (company/role names) into a safe filename fragment."""
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")[:60]


def human_delay(min_sec=0.1, max_sec=0.3):
    time.sleep(random.uniform(min_sec, max_sec))


def type_like_human(locator, text):
    """
    Still types character-by-character (press_sequentially, not .fill()) --
    some comboboxes only filter their option list on real per-character
    keystroke events, which .fill() doesn't fire. delay=0 for speed now
    that everything goes through a manual review checkpoint before
    submitting anyway, so there's no longer a reason to pace it out.
    """
    locator.press_sequentially(text, delay=0)


def timed_input(prompt, timeout=FIELD_PROMPT_TIMEOUT, flag_timeout=False):
    """
    Like input(), but gives up after `timeout` seconds so an unattended run
    keeps moving instead of stalling forever on one field.

    flag_timeout=False (default, used by every per-field prompt): returns ""
    on timeout, identical to the user just hitting Enter -- fine when "" means
    "skip this one field".

    flag_timeout=True (used only by the bot-wall pause and the final review
    checkpoint): returns (text, timed_out) instead, so the caller can tell a
    real Enter-press apart from a timeout -- essential there, since those two
    cases must NOT be treated the same (an unattended timeout must never be
    read as "confirmed applied").
    """
    print(prompt, end="", flush=True)
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        print(f"\n    [⏱] No response in {timeout}s -- skipping, moving on.")
        return ("", True) if flag_timeout else ""
    line = sys.stdin.readline().strip()
    return (line, False) if flag_timeout else line


def detect_bot_wall(page) -> bool:
    try:
        text = page.locator("body").inner_text(timeout=2000)
        return bool(BOT_WALL_RE.search(text or ""))
    except Exception:
        return False


def _parse_grad_month_year(expected_graduation: str) -> tuple[str, str]:
    """Best-effort split of the free-text `expected_graduation` profile
    field (e.g. "August 2027") into month/year strings. fill_grad_date's
    label-only attempt (try_fill_label with the full string) is tried
    first and covers most real ATS date pickers on its own; month/year are
    only a fallback for the ATSes that split graduation into two separate
    dropdowns, so an unparseable string degrading to "" here just means
    that fallback also comes up empty and the field surfaces in
    `unmapped` for manual review -- never a crash."""
    for fmt in ("%B %Y", "%b %Y"):
        try:
            dt = datetime.strptime(expected_graduation.strip(), fmt)
            return dt.strftime("%B"), str(dt.year)
        except (ValueError, AttributeError):
            continue
    return "", ""


def load_profile():
    """Builds the same shape apply.py's fill_* functions expect, from this
    app's per-user CandidateProfile (SQLite). Compiles the user's master resume to a PDF once here
    (not per-job in resume_for) since there's only one resume/profile for
    the whole run, and stashes it under the private "_resume_info" key.
    """
    cp = SqliteProfileService().get_profile(USER_ID)

    try:
        pdf_bytes = get_master_pdf(USER_ID)
    except TailoringError as exc:
        print(f"[❌] Could not compile your master resume: {exc}")
        print("    Fix it in the resume editor, then re-run.")
        sys.exit(1)

    resume_file = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    resume_file.write(pdf_bytes)
    resume_file.close()

    grad_month, grad_year = _parse_grad_month_year(cp.expected_graduation)

    field_map = [
        {
            "field_name": rule.field_name,
            "profile_key": rule.profile_key,
            "patterns": rule.patterns,
            "select_dropdown": rule.select_dropdown,
        }
        for rule in cp.field_map
    ]
    screening_answers = [
        {"pattern": rule.pattern, "answer": rule.answer} for rule in cp.screening_answers
    ]

    return {
        "first_name": cp.first_name,
        "last_name": cp.last_name,
        "full_name": cp.full_name or f"{cp.first_name} {cp.last_name}".strip(),
        "email": cp.email,
        "phone": cp.phone,
        "school": cp.school,
        "linkedin": cp.linkedin,
        "github": cp.github,
        "address_line_1": cp.address_line_1,
        "city": cp.city,
        "province": cp.province,
        "postal_code": cp.postal_code,
        "degree_program": cp.degree_program,
        "expected_graduation": cp.expected_graduation,
        "field_map": field_map,
        "screening_answers": screening_answers,
        # This app deliberately never auto-answers EEO/demographic
        # questions server-side (see services/automation.py's
        # SENSITIVE_CATEGORY_PATTERN) -- left empty so
        # fill_voluntary_disclosure() matches nothing and those fields
        # fall through to `unmapped` for manual review, same policy the
        # extension's AI fallback already follows.
        "voluntary_disclosure_rules": [],
        "languages": [],
        "_resume_info": {
            "path": resume_file.name,
            "grad_date_label": cp.expected_graduation,
            "grad_month": grad_month,
            "grad_year": grad_year,
        },
    }


def load_jobs():
    """This app's `applications` table (stage='bookmarked', this user
    only) is the single source of truth -- unlike job-apply-bot's
    jobs.csv, there's no separate Postgres history table to cross-check,
    so a row being in this list already means "not yet applied." Each
    job dict carries the source row's id (_application_id) so save_jobs
    can write status/notes back to the right row."""
    apps = SqliteApplicationService().list_applications(USER_ID, stage=ApplicationStage.BOOKMARKED)
    return [
        {
            "_application_id": app.id,
            "company": app.job.company,
            "role": app.job.title,
            "url": app.job.url,
            "application_type": "",
            "ats": "",
            "status": "",
            "notes": app.notes,
        }
        for app in apps
    ]


def save_jobs(jobs):
    """Writes each job's in-memory status back to its SQLite row.

    apply.py's status vocabulary (needs_review/failed/unfinished/skipped)
    is wider than this app's `stage` CHECK constraint
    (bookmarked/applied/interview/offer/rejected) -- only "applied" is a
    real stage transition here. Everything else leaves the row in
    "bookmarked" (so it stays visible on the board instead of vanishing
    or hitting the CHECK constraint) and records the reason in `notes`
    instead, since that column is free text.
    """
    svc = SqliteApplicationService()
    for job in jobs:
        app_id = job.get("_application_id")
        if app_id is None:
            continue
        status = job.get("status", "")
        try:
            if status == "applied":
                svc.update_stage(app_id, USER_ID, ApplicationStage.APPLIED)
            elif status in ("needs_review", "failed", "unfinished", "skipped"):
                svc.update_application(app_id, USER_ID, notes=job.get("notes") or status)
        except Exception as e:
            print(f"    [warn] Could not persist status for application id={app_id}: {e}")


def detect_ats(url: str) -> str:
    if "greenhouse.io" in url:
        return "greenhouse"
    if "lever.co" in url:
        return "lever"
    if "myworkdayjobs.com" in url or "workday" in url:
        return "workday"
    return "generic"


def resume_for(job: dict, profile: dict) -> dict:
    # One resume/profile for the whole run (no separate internship/newgrad
    # tracks in this app) -- compiled once in load_profile(), not per job.
    return profile["_resume_info"]


def _tag_name(locator):
    return locator.evaluate("el => el.tagName.toLowerCase()")


def _field_has_value(loc):
    """Ground truth check: is there actually a real value in this field right now?"""
    try:
        val = loc.input_value()
        return bool((val or "").strip())
    except Exception:
        pass
    try:
        text = (loc.inner_text() or "").strip()
        return bool(text) and not text.lower().startswith("select")
    except Exception:
        return False


def fill_value(page, loc, value, dropdown_aware=False):
    """
    One consistent strategy for text inputs, native <select>, and custom
    dropdown/combobox fields, instead of guessing the field type upfront
    and branching differently per guess (which kept guessing wrong tonight).

    dropdown_aware=True: click the field open first, then check whether
    options are ALREADY rendered -- a small fixed-choice dropdown (Yes/No,
    a season picker, a year list) needs no typing at all, just click the
    matching option, the same way a human would answer it. Only fall back
    to typing if nothing renders immediately, for search-filtered
    autocompletes (School, Current Location) that only populate options
    once you start typing -- then poll briefly for a freshly-filtered
    option and click that. Native <select> is tried as a pure-DOM fallback
    for the rare case where real options never render as an overlay list.

    dropdown_aware=False (default): just click and type -- for fields
    already known to be plain (email, phone, names), skipping the option
    checks entirely so every ordinary field doesn't pay for polling that
    only ever matters for dropdown-shaped ones.

    Either way, if nothing matches anything, the typed text is left in the
    field rather than lost -- a legitimate outcome for plain text fields.
    """
    primary_term = value.split(",")[0].strip() or value

    def find_and_click_option(val):
        opt_xpath = (
            f"//div[@role='option'][{lower_xpath(val)}] | "
            f"//li[@role='option'][{lower_xpath(val)}] | "
            f"//*[@role='listbox']//*[{lower_xpath(val)}]"
        )
        try:
            opt = page.locator(opt_xpath).first
            if opt.is_visible():
                opt.click()
                return True
        except Exception:
            pass
        return False

    # Don't overwrite a field the ATS's own native autofill (or an earlier
    # pass) already populated -- our scan runs once up front and a field
    # can legitimately still get filled by something else (autofill's
    # async population) between that scan and this actual fill attempt,
    # confirmed by hand: a native-autofilled value got typed over because
    # nothing re-checked the field's current state right before writing.
    if _field_has_value(loc):
        return True

    try:
        loc.click()
    except Exception:
        pass

    if not dropdown_aware:
        try:
            type_like_human(loc, value)
        except Exception:
            pass
        return _field_has_value(loc)

    # 1. Already-open static list -- no typing needed, just click the match.
    # Check immediately, no upfront wait -- most static lists (Yes/No, a
    # season picker) render synchronously the instant the field is clicked.
    if find_and_click_option(value):
        return True

    # 2. Type it (plain text, or to filter a search-based autocomplete).
    try:
        type_like_human(loc, value)
    except Exception:
        pass

    # 3. Poll briefly for a freshly-filtered/rendered option, click it --
    # check-then-wait so a fast-rendering option returns almost instantly
    # instead of always paying a fixed delay first. Kept short (under 1s
    # worst case) since this now runs on every unknown interactive field,
    # most of which are plain text and were never going to render an
    # option at all -- a long poll there is pure wasted time, not caution.
    for _ in range(6):
        if find_and_click_option(primary_term):
            return True
        page.wait_for_timeout(150)

    # 4. Native <select> pure-DOM fallback -- for the rare case where real
    # options never render as an overlay list at all.
    try:
        if _tag_name(loc) == "select":
            loc.select_option(label=value)
            return _field_has_value(loc)
    except Exception:
        pass

    return _field_has_value(loc)


def try_fill_label(page, label_patterns, value, filled, unmapped, field_name, name_attrs=None, select_dropdown=False):
    if not value:
        unmapped.append(field_name)
        return False

    try:
        labels = page.locator("label").all()
        for label_el in labels:
            raw_text = label_el.inner_text()
            if not raw_text:
                continue

            # Strip colons and asterisks so exact regex bounds like "^degree$" don't fail against "Degree: *"
            clean_label = re.sub(r'[*:\n]', '', raw_text).strip()

            for pattern in label_patterns:
                if re.search(pattern, clean_label, re.I):
                    for_attr = label_el.get_attribute("for")
                    target_xpath = None

                    if for_attr:
                        target_xpath = f"//*[@id='{for_attr}']"
                    else:
                        snippet = get_safe_xpath_snippet(raw_text)
                        if snippet:
                            target_xpath = (
                                f"//*[{lower_xpath(snippet)}]//input | "
                                f"//*[{lower_xpath(snippet)}]//select | "
                                f"//*[{lower_xpath(snippet)}]//textarea | "
                                f"//*[{lower_xpath(snippet)}]/following::input[1]"
                            )

                    if target_xpath:
                        loc = page.locator(target_xpath).first
                        if loc.is_visible() and fill_value(page, loc, value, dropdown_aware=select_dropdown):
                            filled.append(field_name)
                            human_delay()
                            return True
    except Exception:
        pass

    # Fallback for forms that label inputs via aria-label directly on the
    # element instead of a wrapping/associated <label> tag
    try:
        aria_elements = page.locator("input[aria-label], select[aria-label], textarea[aria-label]").all()
        for el in aria_elements:
            raw_text = el.get_attribute("aria-label") or ""
            if not raw_text:
                continue

            clean_label = re.sub(r'[*:\n]', '', raw_text).strip()

            for pattern in label_patterns:
                if re.search(pattern, clean_label, re.I):
                    target_xpath = f"//*[@aria-label={xpath_literal(raw_text)}]"
                    loc = page.locator(target_xpath).first

                    if loc.is_visible() and fill_value(page, loc, value, dropdown_aware=select_dropdown):
                        filled.append(field_name)
                        human_delay()
                        return True
    except Exception:
        pass

    for attr in name_attrs or []:
        try:
            selector = f'input[name="{attr}"], textarea[name="{attr}"]'
            loc = page.locator(selector).first
            if loc.is_visible():
                loc.click()
                type_like_human(loc, value)
                filled.append(field_name)
                human_delay()
                return True
        except Exception:
            continue

    unmapped.append(field_name)
    return False


def try_answer_yesno(page, pattern, answer, filled, unmapped, field_name):
    try:
        elements = page.locator("label, legend, div").all()
        target_text = None

        for el in elements:
            raw_text = el.inner_text()
            if not raw_text:
                continue

            clean_text = re.sub(r'[*:\n]', '', raw_text).strip()

            if re.search(pattern, clean_text, re.I):
                if AMBIGUOUS_MENTION_RE.search(clean_text):
                    unmapped.append(field_name)
                    return False
                target_text = raw_text
                break

        if not target_text:
            return False

        snippet = get_safe_xpath_snippet(target_text)
        if not snippet:
            unmapped.append(field_name)
            return False

        base_xpath = f"//*[{lower_xpath(snippet)}]"

        radio_xpath = (
            f"{base_xpath}//label[{lower_xpath(answer)}]//input[@type='radio'] | "
            f"//input[@type='radio'][{lower_xpath(answer)}] | "
            f"//label[{lower_xpath(answer)}]/preceding-sibling::input[@type='radio']"
        )
        radio_loc = page.locator(radio_xpath).first
        if radio_loc.is_visible():
            radio_loc.click()
            filled.append(field_name)
            human_delay()
            return True

        select_xpath = f"{base_xpath}//select | {base_xpath}/following::select[1]"
        select_loc = page.locator(select_xpath).first
        if select_loc.is_visible() and fill_value(page, select_loc, answer, dropdown_aware=True):
            filled.append(field_name)
            human_delay()
            return True

    except Exception:
        pass

    unmapped.append(field_name)
    return False


def fill_voluntary_disclosure(page, profile, filled, unmapped):
    """
    Gender/race/veteran-status/disability questions are almost always
    rendered as a radio or checkbox GROUP on real ATS forms ("( ) Male
    ( ) Female ( ) Decline to self-identify"), not a <select> or a plain
    <input> -- confirmed by hand: these four fields were unmapped on every
    single job processed all night, because this function only ever knew
    how to target a dropdown/text input via fill_value(), never an actual
    radio/checkbox OPTION matched by its own label text (the exact thing
    try_answer_yesno/click_checkbox already do correctly elsewhere in this
    file for screening questions). Try that shape first now, since it's the
    dominant real-world one; keep the old select/input path as a fallback
    for the ATSes that genuinely do render this as a dropdown.
    """
    for rule in profile["voluntary_disclosure_rules"]:
        pattern = rule["pattern"]
        candidates = rule["candidates"]
        field_name = f"voluntary:{pattern[:30]}"
        resolved = False

        question_snippet = None
        target_xpath = None
        try:
            elements = page.locator("label, legend").all()

            for el in elements:
                raw_text = el.inner_text()
                if not raw_text:
                    continue

                clean_text = re.sub(r'[*:\n]', '', raw_text).strip()
                if re.search(pattern, clean_text, re.I):
                    question_snippet = get_safe_xpath_snippet(raw_text)
                    for_attr = el.get_attribute("for")
                    if for_attr:
                        target_xpath = f"//*[@id='{for_attr}']"
                    else:
                        target_xpath = (
                            f"//*[{lower_xpath(question_snippet)}]//select | "
                            f"//*[{lower_xpath(question_snippet)}]/following::select[1] | "
                            f"//*[{lower_xpath(question_snippet)}]/following::input[1]"
                        )
                    break
        except Exception:
            pass

        # Attempt 1: radio/checkbox option -- a <label> matching a decline
        # phrase that appears after this question's own label/legend in
        # document order. "following::" scoped from the question's own
        # position (not a global search) so it lands on THIS question's
        # decline option, not an identically-worded one belonging to a
        # different EEO question further down the same page.
        if question_snippet:
            for candidate in candidates:
                try:
                    candidate_xpath = (
                        f"//*[{lower_xpath(question_snippet)}]"
                        f"/following::label[{lower_xpath(candidate)}][1]"
                    )
                    loc = page.locator(candidate_xpath).first
                    if loc.is_visible():
                        loc.click()
                        filled.append(field_name)
                        human_delay()
                        resolved = True
                        break
                except Exception:
                    continue

        # Attempt 2: genuine <select>/text-input fallback for ATSes that
        # really do render this as a dropdown instead of a radio group.
        if not resolved and target_xpath:
            try:
                loc = page.locator(target_xpath).first
                if loc.is_visible():
                    for candidate in candidates:
                        try:
                            if not fill_value(page, loc, candidate, dropdown_aware=True):
                                continue
                            filled.append(field_name)
                            human_delay()
                            resolved = True
                            break
                        except Exception:
                            continue
            except Exception:
                pass

        if not resolved:
            unmapped.append(field_name)

    try:
        consent_xpath = f"//*[{lower_xpath_own_text('consent')} or {lower_xpath_own_text('agree')}]"
        loc = page.locator(consent_xpath).first
        if loc.is_visible():
            loc.click()
            filled.append("voluntary_consent")
            human_delay()
    except Exception:
        pass


def has_identity_fields(page) -> bool:
    """
    True once the real candidate form (not just a landing/gate page) has
    rendered -- checked via a "first name" field, since every ATS form has
    one and a bare landing page (e.g. BlackRock's "Upload Resume" gate)
    never does. Shared between ensure_form_visible's own progress check and
    run_batch's post-fill "did we ever actually get to a real form" signal,
    so both use one definition of "the form is here" instead of drifting.

    Deliberately checks actual form-control attributes (name/id/placeholder/
    aria-label), not page text -- an earlier version scanned for any element
    whose text mentioned "first name" and matched <html>/<body> themselves
    on Rippling's landing page (their string-value bubbled up a "first name"
    i18n label sitting inert inside a <script> tag's JSON, and html/body are
    always "visible" by sheer page size), which made this report true before
    the form had even rendered and silently skipped clicking "Apply now".

    Checks every frame on the page, not just the top document -- some ATS
    embeds (confirmed: a Greenhouse board embedded via <iframe> on a
    company's own careers page) render the entire candidate form inside a
    child frame, which none of these selectors would ever see if only the
    top-level document were checked. `page.frames` always includes the main
    frame itself, so a normal single-document page is checked exactly as
    before; a cross-origin or otherwise inaccessible frame just can't be
    queried and is skipped, not treated as an error.
    """
    input_selectors = [
        'input[name="first_name"]',
        'input[name*="first" i][name*="name" i]',
        'input[id*="first" i][id*="name" i]',
        'input[placeholder*="first name" i]',
        'input[aria-label*="first name" i]',
    ]
    for frame in page.frames:
        for selector in input_selectors:
            try:
                if frame.locator(selector).first.is_visible():
                    return True
            except Exception:
                continue
        try:
            label_xpath = f"//label[{lower_xpath_own_text('first name')}]"
            if frame.locator(label_xpath).first.is_visible():
                return True
        except Exception:
            continue
    return False


def find_best_frame(page):
    """
    Some ATS embeds (confirmed: a Greenhouse board embedded via <iframe> on
    a company's own careers page) render the entire candidate form inside a
    child frame -- Playwright locators never look inside an iframe unless
    explicitly scoped to that frame's own Locator/evaluate calls. Scans
    every frame on the page, counts how many incomplete fields
    find_incomplete_fields() reports in each one, and returns whichever
    frame found the most -- the same "most fields wins" heuristic this
    project's own Chrome extension already uses for the identical problem
    (see extension/background.js's runFillOnTab). Falls back to the
    top-level page itself when every frame (including it) finds nothing, so
    an ordinary single-document ATS page behaves exactly as before this
    existed.

    A Playwright Frame supports the same .locator()/.evaluate()/
    .wait_for_timeout() calls every fill_* function below already uses on
    "page" -- only tab-level operations (goto/close/screenshot/
    bring_to_front), which stay on the real page object in run_batch, don't
    exist on Frame. That's why every fill_* function can be handed either
    one without any of them needing to change.
    """
    best_target = page
    best_count = len(find_incomplete_fields(page))
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        count = len(find_incomplete_fields(frame))
        if count > best_count:
            best_count = count
            best_target = frame
    return best_target


def click_advance_button(page):
    """
    Clicks a "Next"/"Continue"/"Save and Continue"/"Parse CV"-style button to
    move to the next page of a multi-page application wizard -- confirmed on
    BlackRock's tal.net flow: Upload CV -> [Parse CV] -> Position Preference
    -> ... -- each page only reveals the next page's fields after its own
    advance button is clicked, so a single fill-once pass always stopped on
    whatever page happened to load first and left everything after it
    unmapped. Deliberately excludes "submit"/"apply" wording so this can
    never be the final-submission click -- the review-checkpoint/--review-all
    flag is still the real safety net, but this keeps the wizard-hop's own
    action scoped to what it's actually named for.
    """
    patterns = ["next", "continue", "save and continue", "save & continue", "proceed", "parse cv", "parse resume"]
    for pattern in patterns:
        try:
            loc = page.locator(clickable_xpath(pattern)).first
            loc.click(timeout=1200)
            print(f"    [i] Multi-page wizard: clicked '{pattern}' to advance.")
            return True
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue
    return False


def ensure_form_visible(page):
    # Dismiss cookie banner if present so it doesn't intercept clicks
    try:
        cookie_accept = f"//button[{lower_xpath('accept')}]"
        loc = page.locator(cookie_accept).first
        if loc.is_visible():
            loc.click()
            page.wait_for_timeout(1000)
    except Exception:
        pass

    if has_identity_fields(page):
        return True

    patterns = ["apply", "i'm interested", "im interested", "apply now", "apply for this job", "apply for this position", "apply to this position", "start application", "fill in application form manually", "fill in manually", "enter manually"]
    for pattern in patterns:
        try:
            loc = page.locator(clickable_xpath(pattern)).first
            # click(timeout=...) uses Playwright's own actionability
            # auto-waiting instead of one instant is_visible() snapshot --
            # networkidle already gives the page a shot to settle before
            # this runs, but this modest per-pattern wait is a second,
            # cheap layer of defense against stragglers, without ballooning
            # worst-case latency across all 8 patterns when a job's page
            # genuinely has none of them.
            loc.click(timeout=1000)
            print(f"    [i] Form not visible yet -- clicked '{pattern}' button.")

            for _ in range(10):
                page.wait_for_timeout(500)
                if has_identity_fields(page):
                    return True

            print("    [!] Clicked apply button but form never appeared within 5s.")
            return True
        except PlaywrightTimeoutError:
            # Routine -- this pattern just isn't on the page, not worth a
            # debug line for every one of the 8 patterns tried per job.
            continue
        except Exception as e:
            print(f"    [debug] pattern '{pattern}' raised: {type(e).__name__}: {e}")
            continue
    return False


def try_native_autofill(page):
    """
    Some ATS forms (Greenhouse in particular) offer a native "Autofill my
    application" button that pre-populates fields from a saved
    profile/resume -- when present, clicking it first means our own fill
    passes only have to handle whatever's still genuinely missing
    afterward. find_incomplete_fields already skips anything non-empty, so
    this integrates for free: nothing extra needed to "skip over" fields
    the ATS already filled. Just reports what showed up already-filled
    right after, so it's visible for the review checkpoint to sanity-check
    rather than trusting it silently.
    """
    patterns = ["autofill my application", "autofill", "auto-fill", "auto fill"]
    for pattern in patterns:
        try:
            xpath = clickable_xpath(pattern)
            loc = page.locator(xpath).first
            loc.click(timeout=3000)
            print(f"    [i] Clicked native '{pattern}' button -- letting the ATS pre-fill what it can.")
            page.wait_for_timeout(1000)
            return True
        except Exception:
            continue
    return False


def try_upload_resume_gate(page, resume_path):
    """
    Some custom (non-Greenhouse) careers pages gate the real form behind an
    "Upload Resume" / "Fill in application form manually" landing screen --
    confirmed by hand: a job never got past this page at all, since neither
    the "apply"-style nor "autofill"-style button patterns matched its text.
    Click "Upload Resume" and feed it the resume file directly (same
    CDP-based set_file_input used everywhere else, no native OS dialog
    risk) so the site's own resume-parse autofill gets a chance to run
    before our own field-by-field fill takes over on whatever's still
    empty afterward.
    """
    patterns = ["upload resume", "upload your resume", "upload cv"]
    for pattern in patterns:
        try:
            xpath = clickable_xpath(pattern)
            loc = page.locator(xpath).first
            # click(timeout=...) lets Playwright's own actionability
            # auto-waiting retry internally until the element exists and is
            # clickable, instead of one instant is_visible() snapshot right
            # after navigation -- slower client-rendered SPA pages (confirmed
            # on BlackRock's tal.net gate page) don't have this button in the
            # DOM yet at that exact moment, so a snapshot check just silently
            # missed it every time even though it reliably appears within a
            # few seconds.
            loc.click(timeout=4000)
            print(f"    [i] Clicked '{pattern}' -- letting the site parse the resume into the form.")
            page.wait_for_timeout(500)
            try:
                set_file_input(page, 'input[type="file"]', str(resume_path))
                page.wait_for_timeout(1500)
            except Exception as e:
                print(f"    [debug] Resume upload on gate page failed: {type(e).__name__}: {e}")
            return True
        except Exception:
            continue
    return False


def progress_multi_step_wizard(page, max_hops=5):
    """
    Some ATS forms gate the real candidate-info page behind a short wizard --
    Upload Resume -> review parsed info -> Next -> Personal Info -> Next ->
    ... -- a distinct case from a single-button gate (BlackRock's landing
    page, handled by try_upload_resume_gate/ensure_form_visible) or a dead
    posting (LiveRamp's 404): those handlers get past the FIRST screen, but
    a following "Next"/"Continue" step can still stand between that and the
    actual first_name/last_name fields. Keep clicking through Next-style
    buttons -- bounded by max_hops so a genuinely stuck page doesn't loop
    forever -- until has_identity_fields() confirms the real form is here,
    or nothing more matches.
    """
    if has_identity_fields(page):
        return True

    patterns = ["next", "continue", "save and continue", "save & continue", "proceed"]
    for _ in range(max_hops):
        if has_identity_fields(page):
            return True
        clicked_this_hop = False
        for pattern in patterns:
            try:
                loc = page.locator(clickable_xpath(pattern)).first
                loc.click(timeout=1500)
                print(f"    [i] Multi-step wizard: clicked '{pattern}' -- checking for the real form.")
                page.wait_for_timeout(800)
                clicked_this_hop = True
                break
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue
        if not clicked_this_hop:
            break
    return has_identity_fields(page)


def set_file_input(page, selector, file_path):
    """
    Playwright's set_input_files sets the file value directly, natively --
    no OS-level file dialog is ever involved, unlike SeleniumBase's
    choose_file (which fell through to a real native Finder window under UC
    mode, confirmed by hand: it popped up, stole OS focus, and broke every
    fill after it for the rest of the job).

    timeout=5000 -- Playwright's 30s default wait made a genuinely-missing
    file input (some ATSes have none) stall the whole job for 30 seconds,
    confirmed by hand on more than one job tonight. 5s is still generous
    for a field that's actually there.
    """
    page.locator(selector).first.set_input_files(str(Path(file_path).resolve()), timeout=5000)


def fill_tagged_field(page, selector, kind, value, select_dropdown=False):
    """
    Fills one field already tagged with data-af-id from a page scan, given
    a known value -- no re-querying the page for labels, no prompting.
    Shared by the field_map auto-fill pass and the interactive sweep.

    select_dropdown controls whether fill_value checks for rendered dropdown
    options before/after typing -- pass True for fields actually known to
    be dropdown-backed, since that check has real polling overhead not
    worth paying on every ordinary text field.
    """
    if not value:
        return False
    try:
        if kind == "file":
            try:
                set_file_input(page, selector, value)
            except Exception as e:
                print(f"    [debug] Resume attach failed on {selector}: {type(e).__name__}: {e}")
                raise
            return True

        loc = page.locator(selector).first
        if loc.is_visible():
            return fill_value(page, loc, value, dropdown_aware=select_dropdown)
    except Exception:
        pass
    return False


def fill_common_fields(page, profile, resume_path):
    """
    One full-page scan (find_incomplete_fields), tagging every empty field
    with a stable data-af-id, then matched against the saved profile's
    "field_map" in Python -- replaces what used to be a separate full-page
    label query per hardcoded field (~15 round trips), which is genuinely
    slow and was the likely source of repeated stalls on very large forms.
    Only fields that don't match anything known go on to the interactive
    sweep, via the "unmapped" list exactly as before.
    """
    filled, unmapped = [], []
    fields = find_incomplete_fields(page)

    got_first = got_last = False
    full_name_field = None

    for field in fields:
        clean_label = re.sub(r'[*:\n]', '', field["label"]).strip()
        af_id = field.get("id")
        selector = f'[data-af-id="{af_id}"]' if af_id is not None else None

        if field["kind"] == "file":
            # Only attach the resume to a field that actually asks for a
            # resume/CV -- confirmed by hand that blindly filling every
            # file-kind field put the resume into "Cover Letter" too.
            if not re.search(r"resume|cv\b", clean_label, re.I):
                continue
            if selector and fill_tagged_field(page, selector, "file", str(resume_path)):
                filled.append("resume")
            else:
                unmapped.append("resume")
            continue

        if not selector:
            # Group kinds (checkbox-group/radio-group/single-checkbox) aren't
            # simple one-value fields -- handled by the dedicated voluntary
            # disclosure/checkbox-group passes below, or the interactive sweep.
            continue

        if re.search(r"first name", clean_label, re.I):
            if fill_tagged_field(page, selector, field["kind"], profile.get("first_name", "")):
                got_first = True
                filled.append("first_name")
            continue

        if re.search(r"last name", clean_label, re.I):
            if fill_tagged_field(page, selector, field["kind"], profile.get("last_name", "")):
                got_last = True
                filled.append("last_name")
            continue

        if full_name_field is None and re.search(r"full name|legal name|^name$", clean_label, re.I):
            full_name_field = (selector, field["kind"])
            continue

        matched_spec = next(
            (spec for spec in profile.get("field_map", [])
             if any(re.search(p, clean_label, re.I) for p in spec["patterns"])),
            None,
        )
        if matched_spec:
            value = profile.get(matched_spec["profile_key"], "")
            if fill_tagged_field(page, selector, field["kind"], value, select_dropdown=matched_spec.get("select_dropdown", False)):
                filled.append(matched_spec["field_name"])
            else:
                unmapped.append(matched_spec["field_name"])

    # full_name is only a fallback -- only spend it if first+last didn't
    # both come through on their own.
    if not (got_first and got_last) and full_name_field:
        selector, kind = full_name_field
        if fill_tagged_field(page, selector, kind, profile.get("full_name", "")):
            filled.append("full_name")
        else:
            unmapped.append("full_name")

    if not got_first:
        unmapped.append("first_name")
    if not got_last:
        unmapped.append("last_name")

    if "resume" not in filled:
        # "resume" not in filled doesn't necessarily mean nothing is
        # attached -- a field the ATS's own native autofill already
        # populated is correctly excluded from the incomplete-fields scan,
        # so our own bookkeeping never saw it and never marked it filled.
        # Blindly grabbing the first file input in that case can attach
        # into the WRONG one (e.g. Cover Letter, confirmed by hand) since
        # this selector has no label check at all. Check whether any file
        # input already has a real file on it first, and only fall back to
        # attaching ourselves if none do.
        try:
            already_has_file = page.evaluate(
                "() => Array.from(document.querySelectorAll('input[type=\"file\"]'))"
                ".some(el => el.files && el.files.length > 0)"
            )
        except Exception:
            already_has_file = False

        if already_has_file:
            filled.append("resume")
        else:
            # Retry with a generic selector even if the tagged-field attempt
            # above already failed and logged "resume" to unmapped -- that
            # attempt targets one specific labeled input, which can fail for
            # reasons (timing, a styled decoy button covering the real input)
            # that a plain first-file-input selector doesn't hit.
            try:
                set_file_input(page, 'input[type="file"]', str(resume_path))
                filled.append("resume")
                if "resume" in unmapped:
                    unmapped.remove("resume")
            except Exception as e:
                print(f"    [debug] Resume fallback attach also failed: {type(e).__name__}: {e}")
                if "resume" not in unmapped:
                    unmapped.append("resume")

    fill_voluntary_disclosure(page, profile, filled, unmapped)
    fill_visa_type_selection(page, filled, unmapped)
    fill_checkbox_group(page, profile.get("languages", []), filled, unmapped, "languages")

    return filled, unmapped


def click_checkbox(page, label_text, input_selector=None):
    """
    Custom-styled checkbox/radio widgets routinely hide the real <input>
    (display:none/visibility:hidden) behind a styled decoy -- clicking that
    hidden input directly is exactly what Playwright's default
    actionability check refuses to do (and what was silently swallowing
    every checkbox click tonight via the broad except). Click the
    associated <label> instead: the browser's native label-forwards-to-
    input behavior reliably toggles the real input regardless of its own
    visibility. Verifies the actual checked state afterward and falls back
    to a forced direct click on the input if the label click didn't work
    (e.g. no real <label>/for association exists).
    """
    # Exact match first -- lower_xpath's contains() has no scoping and, for
    # short option text like "Yes"/"No", can grab a completely unrelated
    # label elsewhere on the page that happens to contain the same
    # substring (confirmed by hand: "No" ended up checking a "Yes" box
    # instead). Only fall back to the broader contains() search if no
    # exact-text label exists, since real field labels (not option text)
    # often have surrounding whitespace/markup that needs it.
    exact_xpath = (
        "//label[normalize-space(translate(., "
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'))="
        f"{xpath_literal(label_text.lower().strip())}]"
    )
    # Skip the contains() fallback entirely for short answer tokens (Yes/No
    # and the like) -- confirmed a real false-positive risk tonight: "No"
    # is a substring of "None", so a visa-type radio group offering
    # OPT/H1B/TN/None/Other could get "None" clicked when searching for
    # "No", silently misrepresenting the answer. A short token should
    # always resolve via the exact-match xpath if a real matching option
    # exists; if it doesn't, a loose substring fallback is more likely to
    # grab the wrong option than the right one.
    xpaths_to_try = (
        (exact_xpath,)
        if len(label_text.strip()) <= 3
        else (exact_xpath, f"//label[{lower_xpath(label_text)}]")
    )
    input_loc = page.locator(input_selector).first if input_selector else None

    def is_checked():
        if input_loc is None:
            return None
        try:
            return input_loc.is_checked()
        except Exception:
            return None

    clicked = False
    for xpath in xpaths_to_try:
        try:
            loc = page.locator(xpath).first
            if loc.is_visible():
                loc.click()
                clicked = True
                break
        except Exception:
            continue

    already_checked = is_checked()
    if already_checked:
        return True

    if input_loc is not None:
        try:
            input_loc.click(force=True)
        except Exception:
            pass
        result = is_checked()
        if result is not None:
            return result

    # No selector to verify against (e.g. only a label xpath was available)
    # -- whether we actually clicked something is the only signal we have.
    return clicked


def fill_visa_type_selection(page, filled, unmapped):
    """
    Some ATS forms follow up a sponsorship Yes/No question with a radio
    group asking WHICH visa type applies (OPT/H1B/TN/None/Other) -- often
    sharing the exact same surrounding question text as the Yes/No toggle
    itself (confirmed by hand on Ashby's Notion form), which makes
    text-pattern matching unreliable for telling the two apart. Detected
    structurally instead: any radio group that has a "TN" option at all is
    exactly the situation this handles, since TN is specific enough that a
    false-positive match elsewhere on a real job application is very
    unlikely. Direct, factual answer per explicit instruction: eligible for
    TN status under USMCA (Canadian citizen), not F1/OPT/H1B/J1.
    """
    try:
        tn_xpath = "//label[normalize-space(.)='TN']"
        loc = page.locator(tn_xpath).first
        if not loc.is_visible():
            return
        if click_checkbox(page, "TN"):
            filled.append("visa_type:TN")
            human_delay()
        else:
            unmapped.append("visa_type:TN")
    except Exception:
        pass


def uncheck_if_checked(page, label_text):
    """
    Toggles a checkbox/radio off if it's currently checked. Used to clear
    stale state on exclusive-choice fields (Yes/No) implemented with plain,
    non-exclusive <input type="checkbox"> instead of real radios -- clicking
    "No" there doesn't auto-uncheck "Yes" the way a native radio would, and
    a check from an EARLIER attempt tonight can survive across runs via the
    ATS's own draft-save now that we're logged into a persistent profile.
    Confirmed by hand: both "Yes" and "No" ended up checked simultaneously.
    """
    try:
        label_loc = page.locator(f"//label[{lower_xpath(label_text)}]").first
        for_id = label_loc.get_attribute("for")
        input_loc = page.locator(f"#{for_id}").first if for_id else None
        if input_loc is not None and input_loc.is_checked():
            label_loc.click()
    except Exception:
        pass


def fill_checkbox_group(page, values, filled, unmapped, field_name):
    checked_any = False
    for value in values:
        try:
            xpath = f"//label[{lower_xpath(value)}]"
            loc = page.locator(xpath).first
            if loc.is_visible():
                for_id = loc.get_attribute("for")
                input_selector = f"#{for_id}" if for_id else None
                if click_checkbox(page, value, input_selector=input_selector):
                    human_delay()
                    checked_any = True
        except Exception:
            continue
    if checked_any:
        filled.append(field_name)
    else:
        unmapped.append(field_name)


def fill_grad_date(page, resume_info, filled, unmapped):
    label = resume_info["grad_date_label"]
    scratch_unmapped = []

    ok = try_fill_label(page, ["expected graduation", "graduation date", "anticipated graduation"], label, filled, scratch_unmapped, "grad_date", select_dropdown=True)
    if ok:
        return

    got_month = try_fill_label(page, ["graduation.*month", "end date month"], resume_info["grad_month"], filled, scratch_unmapped, "grad_month", select_dropdown=True)
    got_year = try_fill_label(page, ["graduation.*year", "end date year"], resume_info["grad_year"], filled, scratch_unmapped, "grad_year", select_dropdown=True)

    if not (got_month and got_year):
        unmapped.append("grad_date")

    ideal_start_date = resume_info.get("ideal_start_date")
    if ideal_start_date:
        start_date_patterns = [
            "ideal start date", "anticipated start date", "^start date$",
            "available to start", "available start date", "when can you start",
            "earliest start date", "start availability", "preferred start date",
        ]
        # scratch_unmapped was a dead end before -- try_fill_label's failure
        # never reached the real `unmapped` list, so a start-date field that
        # genuinely couldn't be filled was silently dropped instead of
        # surfacing for review like every other unmapped field does.
        if not try_fill_label(page, start_date_patterns, ideal_start_date, filled, scratch_unmapped, "ideal_start_date", select_dropdown=True):
            unmapped.append("ideal_start_date")


INCOMPLETE_FIELDS_JS = """
const out = [];
const seenGroups = new Set();
let afCounter = 0;

// Every scan restarts numbering from 0, but a field tagged and then filled
// by an EARLIER scan keeps its old data-af-id forever (nothing here ever
// removes it) -- so a later scan can hand that same number to a totally
// different field, and `[data-af-id="N"]` then matches two elements at
// once (first-in-DOM wins, silently targeting the wrong one). Clear every
// stale tag before handing out fresh numbers so no scan can ever collide.
document.querySelectorAll('[data-af-id]').forEach((el) => el.removeAttribute('data-af-id'));

const allInputs = Array.from(document.querySelectorAll('input, select, textarea'));

function labelFor(el) {
    if (el.labels && el.labels.length) return el.labels[0].innerText.trim();
    if (el.closest('label')) return el.closest('label').innerText.trim();
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();
    if (el.placeholder) return el.placeholder.trim();
    if (el.id) {
        const lab = document.querySelector(`label[for="${el.id}"]`);
        if (lab) return lab.innerText.trim();
    }
    return '';
}

function isGenericLabel(text) {
    const t = text.trim().toLowerCase();
    if (t.length <= 3) return true;
    return /^(attach|browse|upload|choose file|select file|select|add|click here|file)$/.test(t);
}

function groupQuestionLabel(el) {
    const fieldset = el.closest('fieldset');
    if (fieldset) {
        const legend = fieldset.querySelector('legend');
        if (legend && legend.innerText.trim()) return legend.innerText.trim();
    }

    let node = el.closest('div, section, form, tr') || el.parentElement;
    for (let hops = 0; node && hops < 8; hops++) {
        let sib = node.previousElementSibling;
        while (sib) {
            const text = (sib.innerText || '').trim();
            if (text && text.length > 2 && text.length < 400) return text;
            sib = sib.previousElementSibling;
        }
        node = node.parentElement;
    }
    return '';
}

allInputs.forEach(el => {
    if (el.type === 'hidden') return;

    // Custom-styled checkbox/radio widgets routinely hide the real input
    // (display:none/visibility:hidden) behind a styled decoy -- exempt them
    // from the visibility filter same as SELECT/file, instead of missing
    // them entirely.
    const style = window.getComputedStyle(el);
    const isCustomWidget = el.type === 'checkbox' || el.type === 'radio';
    const exempt = el.tagName === 'SELECT' || el.type === 'file' || isCustomWidget;
    if (style.display === 'none' && !exempt) return;
    if (style.visibility === 'hidden' && !exempt) return;

    if (el.type === 'checkbox' || el.type === 'radio') {
        if (el.name) {
            const groupKey = el.type + ":" + el.name;
            if (seenGroups.has(groupKey)) return;
            seenGroups.add(groupKey);

            const group = allInputs.filter(
                (other) => other.type === el.type && other.name === el.name
            );

            if (el.type === 'radio' && group.some((g) => g.checked)) return;
            if (el.type === 'checkbox' && group.every((g) => g.checked)) return;

            const options = group.map((g) => labelFor(g)).filter((t) => t);
            if (!options.length) return;

            out.push({
                kind: el.type === 'radio' ? 'radio-group' : 'checkbox-group',
                label: (groupQuestionLabel(el) || options[0]).slice(0, 200),
                tag: 'GROUP',
                required: !!(el.required || el.getAttribute('aria-required') === 'true'),
                options: options,
            });
            return;
        }

        if (el.checked) return;
        let label = labelFor(el);
        if (!label || isGenericLabel(label)) label = groupQuestionLabel(el) || label;
        if (!label) return;

        const afId = afCounter++;
        el.setAttribute('data-af-id', String(afId));

        out.push({
            id: afId,
            kind: 'single-checkbox',
            label: label.slice(0, 200),
            tag: el.tagName,
            required: !!(el.required || el.getAttribute('aria-required') === 'true'),
            options: null,
        });
        return;
    }

    if (el.tagName === 'SELECT') {
        if (el.value && el.value !== '') return;
    } else if (el.value && el.value.trim() !== '') {
        return;
    }

    let label = labelFor(el);
    if (!label || isGenericLabel(label)) label = groupQuestionLabel(el) || label;
    if (!label) return;

    let options = null;
    if (el.tagName === 'SELECT') {
        options = Array.from(el.options)
            .map((o) => o.text.trim())
            .filter((t) => t && !/^select/i.test(t));
    }

    let kind = 'text';
    if (el.tagName === 'SELECT') kind = 'select';
    else if (el.type === 'file') kind = 'file';

    const afId = afCounter++;
    el.setAttribute('data-af-id', String(afId));

    out.push({
        id: afId,
        kind: kind,
        label: label.slice(0, 200),
        tag: el.tagName,
        required: !!(el.required || el.getAttribute('aria-required') === 'true'),
        options: options,
    });
});

return out;
"""


def find_incomplete_fields(page):
    try:
        # Playwright's evaluate() treats a plain string as an expression, not
        # a function body -- wrap in an arrow function so the script's own
        # top-level `return` is valid (Selenium's execute_script used to do
        # this wrapping automatically; Playwright doesn't).
        return page.evaluate("() => {\n" + INCOMPLETE_FIELDS_JS + "\n}")
    except Exception:
        return []


def discover_dropdown_options(page, loc):
    """
    The scan only captures an options list for a genuine native <select> --
    a custom-styled combobox (the majority of what these forms actually use)
    never exposes its choices ahead of time, so a field like that reaches
    the interactive prompt with zero visibility into what it can even be
    answered with. Click it open and read back whatever role="option"
    elements actually render, purely from the live DOM -- no hardcoded
    option lists -- so real choices can be shown before asking, the same
    way a native <select>'s options already are. Closes the dropdown again
    (Escape) afterward so this discovery peek doesn't commit anything.

    The role="option" query is document-wide, not scoped to this field's
    own popup -- confirmed on Epic's Avature form: an already-answered
    multi-select "Discipline" field left its selected "×Computer Science"
    chip in the DOM with role="option" even after closing, and since the
    query has no way to tell that chip apart from a freshly-opened
    dropdown's real options, it leaked into every later field's peek --
    including plain text inputs like Email and Phone Number, which then
    got silently overwritten via the single-option auto-select path. Taking
    a before/after snapshot and keeping only options that are genuinely new
    since this click filters out anything that was already sitting on the
    page from an earlier field.
    """
    query_js = (
        "() => Array.from(document.querySelectorAll("
        "'[role=\"option\"], [role=\"listbox\"] li, [role=\"listbox\"] div'"
        ")).map(el => el.innerText.trim()).filter(t => t && t.length > 0 && t.length < 200)"
    )
    try:
        before = set(page.evaluate(query_js))
    except Exception:
        before = set()

    try:
        loc.click()
    except Exception:
        return []
    page.wait_for_timeout(150)
    try:
        after = page.evaluate(query_js)
    except Exception:
        after = []
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass

    seen = set()
    deduped = []
    for o in after:
        if o not in before and o not in seen:
            seen.add(o)
            deduped.append(o)
    return deduped


def prompt_and_fill_field(page, field):
    """
    Asks the user directly in the terminal how to answer a field we've
    never seen before. Every target is addressed via its data-af-id tag.
    """
    label = field["label"]
    kind = field["kind"]
    tag_hint = " (dropdown)" if kind == "select" else ""
    req_hint = "" if field["required"] else " [optional]"

    print(f"\n[?] Unique question detected: {label}{tag_hint}{req_hint}")

    af_id = field.get("id")
    selector = f'[data-af-id="{af_id}"]' if af_id is not None else None

    if kind == "single-checkbox":
        answer = timed_input("    Check this box? [y/N]: ").lower()
        if answer != "y":
            return False
        try:
            return click_checkbox(page, label, input_selector=selector)
        except Exception:
            return False

    if kind in ("checkbox-group", "radio-group"):
        print("    Options: " + " | ".join(field["options"]))
        prompt = "    Which apply (comma-separated, Enter to skip): " if kind == "checkbox-group" else "    Pick one (Enter to skip): "
        answer = timed_input(prompt)
        if not answer:
            return False

        picks = [a.strip() for a in answer.split(",")] if kind == "checkbox-group" else [answer.strip()]
        matched_options = []
        checked_any = False

        for pick in picks:
            match = next((o for o in field["options"] if pick.lower() in o.lower()), None)
            if not match:
                continue
            matched_options.append(match)
            try:
                if click_checkbox(page, match):
                    checked_any = True
                    human_delay()
            except Exception:
                continue

        # A radio-group is semantically exclusive by definition. Some ATS
        # forms also render an exclusive Yes/No-style choice with plain
        # (non-exclusive) checkboxes instead of real radios, so clear any
        # non-matching option that's still checked (e.g. a stale answer from
        # an earlier attempt) rather than leaving both options checked at
        # once. Skipped for genuine multi-select checkbox-groups (more than
        # 2 options), where several legitimately-checked options is the point.
        if kind == "radio-group" or len(field["options"]) <= 2:
            for other in field["options"]:
                if other not in matched_options:
                    uncheck_if_checked(page, other)

        return checked_any

    if not field["options"] and selector:
        # This field's kind wasn't detected as a native <select>, so the
        # scan never captured an options list -- but it may still be a
        # custom-styled combobox with real choices that only render once
        # clicked open. Peek at it before assuming it's genuinely free text.
        try:
            peek_loc = page.locator(selector).first
            if peek_loc.is_visible():
                discovered = discover_dropdown_options(page, peek_loc)
                if discovered:
                    field["options"] = discovered
        except Exception:
            pass

    if field["options"]:
        print("    Options: " + " | ".join(field["options"]))

        # Only one real choice on offer (e.g. a season/cohort picker with a
        # single active option) -- no actual decision to make, so just take
        # it instead of burning a prompt round-trip on a non-choice.
        if len(field["options"]) == 1:
            print(f"    -> Only one option available ({field['options'][0]!r}) -- selecting it automatically.")
            answer = field["options"][0]
        else:
            answer = timed_input("    What do you want to answer (Enter to skip): ")
    else:
        answer = timed_input("    What do you want to answer (Enter to skip): ")

    if not answer:
        return False

    # The stall watchdog is armed once per field, before this timed_input
    # call -- covering BOTH the (up to 90s) wait for an answer AND the fill
    # itself. A long essay answer can take 30s+ to type, and if the wait
    # alone ran close to the budget, typing could push the combined time
    # over JOB_TIMEOUT and abort the whole job mid-keystroke. Re-arm now
    # that we have a real answer, so filling gets its own fresh budget.
    signal.alarm(JOB_TIMEOUT)

    try:
        if selector:
            target_xpath = selector
        else:
            snippet = get_safe_xpath_snippet(label)
            target_xpath = (
                f"//*[{lower_xpath(snippet)}]/following::input[1] | "
                f"//*[{lower_xpath(snippet)}]/following::textarea[1] | "
                f"//*[{lower_xpath(snippet)}]/following::select[1] | "
                f"//input[@placeholder='{label}']"
            )

        if kind == "file":
            set_file_input(page, target_xpath, answer)
            return True

        loc = page.locator(target_xpath).first
        if loc.is_visible():
            # dropdown_aware=True here since this is the catch-all path for
            # a field of genuinely unknown type -- it could be a typeable
            # combobox (e.g. "What year will you graduate?"), and checking
            # for rendered options is safe now (no blind Enter risk).
            return fill_value(page, loc, answer, dropdown_aware=True)
    except Exception:
        pass
    return False


def resolve_unique_questions(page, filled, unmapped):
    """
    Re-scans the page fresh before every single field instead of walking a
    field list from one scan up front. Filling one field in a repeating
    group (e.g. "First example" of a multi-bullet essay question) can
    trigger the page's own re-render of its sibling fields, which drops the
    data-af-id tags already set on later ones -- re-scanning right before
    each attempt guarantees the tag used was just set moments ago and is
    real. asked_labels prevents re-prompting the same question forever if a
    fill genuinely fails or gets skipped and the field is still empty next
    scan.
    """
    asked_labels = set()
    announced = False

    while True:
        fields = find_incomplete_fields(page)
        remaining = [f for f in fields if f["label"] not in asked_labels]
        if not remaining:
            break

        if not announced:
            required_count = sum(1 for f in fields if f["required"])
            print(f"\n[⏸] Final check: {len(fields)} field(s) still empty ({required_count} required). Going through them one by one.")
            announced = True

        field = remaining[0]
        asked_labels.add(field["label"])

        # Reset the stall watchdog on every field, not just once per job --
        # a form this size can legitimately take several minutes to get
        # through, and the watchdog should only fire on a genuine freeze on
        # ONE field, not on cumulative time across many fields each working fine.
        signal.alarm(JOB_TIMEOUT)

        field_name = f"custom:{field['label'][:40]}"
        try:
            resolved = prompt_and_fill_field(page, field)
        except Exception as e:
            print(f"    [!] Unexpected error on this field, skipping it: {type(e).__name__}: {e}")
            resolved = False

        if resolved:
            print(f"    [✓] Filled: {field['label'][:60]}")
            filled.append(field_name)
            if field_name in unmapped:
                unmapped.remove(field_name)
            human_delay()
        else:
            print(f"    [✗] Could not fill (element gone/not visible after re-scan): {field['label'][:60]}")
            if field["required"]:
                unmapped.append(field_name)


def run_batch(args):
    profile = load_profile()
    jobs = load_jobs()

    signal.signal(signal.SIGALRM, _raise_job_stalled)

    if args.headless:
        print("[⚠️] WARNING: Running UC mode in --headless mode significantly increases anti-bot detection.")
        print("    If Cloudflare/Turnstile triggers frequently, run without --headless.")

    print("[*] Launching Stealth Engine Context (SeleniumBase UC Mode)...")

    CHROME_PROFILE_DIR.mkdir(exist_ok=True)
    with SB(uc=True, headless=args.headless, user_data_dir=str(CHROME_PROFILE_DIR)) as sb:
        SCREENSHOT_DIR.mkdir(exist_ok=True)
        processed_count = 0

        # SeleniumBase UC mode launches and stealth-patches the real Chrome
        # process -- keep it purely for that. Playwright then attaches to
        # the SAME browser over CDP for all page interaction from here on.
        debugger_address = sb.driver.capabilities["goog:chromeOptions"]["debuggerAddress"]
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://{debugger_address}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()

            for index, job in enumerate(jobs):
                if processed_count >= args.batch:
                    print(f"[!] Target batch size of {args.batch} reached. Stopping.")
                    break

                url = job.get("url", "").strip()
                if not url or job.get("status") in ["applied", "skipped"]:
                    continue

                # No separate has_applied() cross-check needed here (unlike
                # job-apply-bot's Postgres history table): load_jobs()
                # already only loaded this user's stage='bookmarked' rows,
                # which by construction means "not yet applied."

                print(f"\n[➔] Launching Pipeline Matrix targeting: {job['company']} - {job['role']}")
                job["ats"] = detect_ats(url)

                # One tab per job, not one reused tab -- a job that needs
                # manual review stays open exactly as filled instead of
                # getting overwritten by the next job's navigation, so
                # multiple applications can sit ready for review at once
                # instead of the batch blocking on each one in turn.
                page = context.new_page()
                # new_page() doesn't bring the tab to the foreground when
                # driving an existing browser over CDP -- confirmed by hand
                # that the tab silently navigated in the background while a
                # different (stale) tab stayed visible. Bring it forward so
                # progress is actually visible while watching.
                page.bring_to_front()

                signal.alarm(JOB_TIMEOUT)
                try:
                    page.goto(url)
                    # A fixed 1200ms wait isn't enough for slower client-rendered
                    # SPA career pages (confirmed on BlackRock's tal.net gate page:
                    # inspecting it live seconds after this job finished showed
                    # "Upload Resume"/"Fill in application form manually" fully
                    # rendered and visible -- they just weren't there yet at the
                    # 1200ms mark, so try_upload_resume_gate/ensure_form_visible
                    # scanned an empty/half-hydrated page and found nothing real
                    # to click). networkidle waits for the page's own network
                    # activity to actually settle instead of guessing a fixed
                    # delay; bounded and swallowed since some pages never go
                    # fully idle (analytics beacons, polling) and shouldn't hang
                    # the job over it.
                    try:
                        page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass
                    page.wait_for_timeout(1200)

                    if detect_bot_wall(page):
                        # No waiting for a human to solve it right now -- leave
                        # this tab open exactly as it is and move straight to
                        # the next application instead of blocking the whole
                        # batch on one CAPTCHA nobody may be present to solve.
                        raise NeedsReviewTimeout("Bot-wall/CAPTCHA encountered -- left open for manual follow-up.")

                    resume_info = resume_for(job, profile)
                    resume_path = ROOT / resume_info["path"]

                    # Some custom (non-Greenhouse) careers pages gate the
                    # real form behind an "Upload Resume" landing screen --
                    # try that gate first, since it can reveal or pre-fill
                    # the real form outright (a job never got past a page
                    # like this at all until this was added).
                    try_upload_resume_gate(page, resume_path)
                    ensure_form_visible(page)
                    # find_best_frame: the native-autofill button lives
                    # wherever the real form does -- including inside a
                    # Greenhouse board embedded via <iframe> on the
                    # company's own careers page, not necessarily the top
                    # document.
                    try_native_autofill(find_best_frame(page))
                    # Handles the case where the above got past the FIRST
                    # gate screen but a short wizard (Next/Continue) still
                    # stands between here and the real candidate-info page.
                    progress_multi_step_wizard(page)

                    # Some ATS forms are a genuine multi-PAGE wizard, not just
                    # a single form behind a gate -- confirmed on BlackRock:
                    # Upload CV -> Parse CV -> Position Preference -> ... A
                    # single fill-once pass only ever saw whatever page
                    # happened to be loaded first and left everything past it
                    # unmapped. Loop: fill the current page fully, and only
                    # advance to the next one if nothing required is still
                    # empty on THIS page -- never advance past something that
                    # still needs a real answer. Bounded by max_wizard_pages
                    # so a genuinely stuck page can't loop forever.
                    filled, unmapped = [], []
                    still_empty = []
                    max_wizard_pages = 6
                    for wizard_hop in range(max_wizard_pages):
                        # Re-resolved every hop, not just once before the loop:
                        # a wizard step can reveal a different iframe (or move
                        # the form out of one) than the previous step had.
                        target = find_best_frame(page)

                        page_filled, page_unmapped = fill_common_fields(target, profile, str(resume_path))
                        filled.extend(page_filled)
                        unmapped.extend(page_unmapped)
                        signal.alarm(JOB_TIMEOUT)
                        fill_grad_date(target, resume_info, filled, unmapped)
                        signal.alarm(JOB_TIMEOUT)

                        for idx, entry in enumerate(profile["screening_answers"]):
                            try_answer_yesno(target, entry["pattern"], entry["answer"], filled, unmapped, f"screening_{idx}")
                        signal.alarm(JOB_TIMEOUT)

                        resolve_unique_questions(target, filled, unmapped)

                        # Ground-truth check: re-scan the live page instead of
                        # trusting our own filled/unmapped bookkeeping.
                        still_empty = [f for f in find_incomplete_fields(target) if f["required"]]
                        for f in still_empty:
                            name = f"verify:{f['label'][:40]}"
                            if name not in unmapped:
                                unmapped.append(name)

                        if still_empty:
                            print(f"    -> Verification pass: {len(still_empty)} required field(s) still actually empty on this page.")
                            break

                        if not click_advance_button(target):
                            break
                        # Navigation/settle timing stays page-level even when
                        # the form itself lives in a frame -- clicking Next
                        # can still trigger a real top-level navigation.
                        page.wait_for_timeout(1000)
                        try:
                            page.wait_for_load_state("networkidle", timeout=4000)
                        except Exception:
                            pass
                        signal.alarm(JOB_TIMEOUT)

                    print(f"    -> Filled parameters: {filled}")
                    if unmapped:
                        print(f"    -> Unmapped constraints: {unmapped}")

                    # Signal, not just a symptom: if nothing got filled AND the
                    # real candidate form never rendered (still no "first name"
                    # field anywhere), we're almost certainly stranded on a
                    # landing/gate page like BlackRock's -- a code-fix problem
                    # (add the new button/element shape to clickable_xpath,
                    # same growing-matcher pattern used to fix BlackRock), not
                    # a "just needs a human to finish the form" problem. Flag
                    # it loudly and distinctly so it's never confused with a
                    # normal review case at a glance.
                    stuck_on_landing_page = not filled and not has_identity_fields(page)
                    if stuck_on_landing_page:
                        print(f"    [⚠] Looks stuck on a landing/gate page -- no fields filled and no real form ever appeared. This needs a code fix (new button shape for clickable_xpath), not just manual review.")

                    # Never auto-submit a real application, regardless of how
                    # cleanly the form filled -- every job ends here, tab left
                    # open exactly as filled, for a human to look over and
                    # click the real site's own submit button themselves. Only
                    # the notes distinguish "fully filled, just needs your
                    # submit click" from "some fields still need your input."
                    print(f"[⏳] Ready for review -- leaving this tab open ({job['company']} - {job['role']}) and moving to the next application.")
                    job["status"] = "needs_review"
                    base_notes = f"Filled and ready -- flagged: {unmapped}" if unmapped else "Filled and ready for manual submit."
                    job["notes"] = f"[STUCK ON LANDING/GATE PAGE -- needs a code fix] {base_notes}" if stuck_on_landing_page else base_notes

                    # Screenshot every job, success or not, so results are
                    # visually verifiable at a glance instead of trusting the
                    # filled/unmapped bookkeeping alone -- exactly how the
                    # BlackRock landing-page bug got caught in the first place.
                    try:
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        shot_name = f"{slugify_filename(job['company'])}_{slugify_filename(job['role'])}_{job['status']}_{ts}.png"
                        page.screenshot(path=str(SCREENSHOT_DIR / shot_name))
                    except Exception:
                        pass

                except JobStalledError:
                    print(f"[⏱] No progress in {JOB_TIMEOUT}s -- abandoning this one and moving to the next application.")
                    job["status"] = "unfinished"
                    job["notes"] = f"Stalled for more than {JOB_TIMEOUT}s, abandoned automatically."
                    try:
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        shot_name = f"{slugify_filename(job['company'])}_{slugify_filename(job['role'])}_stalled_{ts}.png"
                        page.screenshot(path=str(SCREENSHOT_DIR / shot_name))
                    except Exception:
                        pass

                except NeedsReviewTimeout as err:
                    print(f"[⏳] {err} Moving to next application -- you can finish this one manually.")
                    job["status"] = "needs_review"
                    job["notes"] = str(err)
                    try:
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        shot_name = f"{slugify_filename(job['company'])}_{slugify_filename(job['role'])}_botwall_{ts}.png"
                        page.screenshot(path=str(SCREENSHOT_DIR / shot_name))
                    except Exception:
                        pass

                except Exception as err:
                    print(f"[❌] Fatal error parsing job pipeline: {err}")
                    job["status"] = "failed"
                    job["notes"] = f"Runtime tracking anomaly: {str(err)}"

                    try:
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        shot_name = f"{slugify_filename(job['company'])}_{slugify_filename(job['role'])}_error_{ts}.png"
                        page.screenshot(path=str(SCREENSHOT_DIR / shot_name))
                    except Exception:
                        pass

                finally:
                    signal.alarm(0)
                    # Only close tabs that don't need a look -- a needs_review
                    # tab stays open exactly as filled for morning review
                    # instead of getting silently closed here.
                    if job.get("status") != "needs_review":
                        try:
                            page.close()
                        except Exception:
                            pass

                save_jobs(jobs)
                processed_count += 1
                human_delay(0.5, 1.0)

            review_count = sum(1 for j in jobs if j.get("status") == "needs_review")
            if review_count:
                print(f"\n[*] {review_count} application(s) left open for manual review -- browser stays open. Stop this process manually once you're done reviewing.")
                while True:
                    time.sleep(300)
            else:
                print("\n[*] Shutting down environment sessions safely.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job application autofill assistant")
    parser.add_argument("--user-id", type=int, required=True, help="Apply using this account's bookmarked jobs, profile, and resume.")
    parser.add_argument("--batch", type=int, default=10, help="Maximum number of pending jobs to process this run.")
    parser.add_argument("--review-all", action="store_true", help="Force manual review/submit on every application.")
    parser.add_argument("--headless", action="store_true", help="Run without a visible browser window.")
    parsed_args = parser.parse_args()

    USER_ID = parsed_args.user_id
    run_batch(parsed_args)
