#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
fill_form.py — Autofill + AUTO resume upload (independent of answers) + slow combos + screenshots

RESTORED WORKING VERSION - File uploads are ACTIVE and working!

What's working:
- Auto resume upload is ENABLED globally, not tied to answers:
  * scans for <input type="file"> on page & iframes
  * listens for file chooser (native dialog) and supplies the resume
  * retries opportunistically during the fill and right before submit
- Keeps: slow typing for dropdowns, before/after screenshots with approval
- Video recording disabled

Run:
  python a7_fill_form_resume.py                    # Normal mode with approval
  python a7_fill_form_resume.py --no-approval      # Skip approval (for Telegram bot)
  python a7_fill_form_resume.py --no-submit        # Fill form but don't submit
"""

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import json
import os
import re
import time
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from output_config import OutputPaths, RESUME_PATH
from utils import ci_match_label, normalize

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

# ========================= CONFIG =========================
# Get JOB_URL from environment variable (set by telegram_bot.py)
JOB_URL = os.getenv("JOB_URL", "")
if not JOB_URL:
    logging.error("❌ JOB_URL environment variable not set!")
    sys.exit(1)

# SIMPLIFIED: Only use filled_answers.json (includes Gemini auto-fill + your Telegram inputs)
ANSWERS_PATH = OutputPaths.FILLED_ANSWERS
ANSWERS_FALLBACKS = [
    str(OutputPaths.FILLED_ANSWERS),
    "filled_answers.json",
    "answers.json"
]

COVER_LETTER_PATH = OutputPaths.COVER_LETTER

HEADLESS = True
SUBMIT_AT_END = True

# Check command line arguments
REQUIRE_APPROVAL = True
if "--no-approval" in sys.argv:
    REQUIRE_APPROVAL = False
    logging.info("🔓 Approval requirement disabled (--no-approval flag)")

# Check if we should skip submission entirely
SKIP_SUBMIT = "--no-submit" in sys.argv
if SKIP_SUBMIT:
    SUBMIT_AT_END = False
    logging.info("⏸️  Submit disabled (--no-submit flag)")

# Timing
SLOW_MO_MS = 300
SCREENSHOT_DIR = OutputPaths.SCREENSHOTS_DIR
COMBO_OPEN_PAUSE_MS = 200
COMBO_TYPE_DELAY_MS = 200
COMBO_POST_TYPE_WAIT_MS = 800
UPLOAD_SETTLE_MS = 800

# Regex patterns
UPLOAD_TEXT_RE = re.compile(r"(upload|browse|choose file|attach|select file|resume|cv)", re.I)

def click_apply_like_things(page):
    """Click Apply/Continue buttons to reveal forms"""
    candidates = [
        "button:has-text('Apply')",
        "button:has-text('Continue')",
        "a:has-text('Apply')",
        "a:has-text('Continue')",
        "button:has-text('Apply Now')",
        "a:has-text('Apply Now')",
        "[role='button']:has-text('Apply')",
        ".btn:has-text('Apply')",
        ".button:has-text('Apply')",
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel)
            if loc.first.is_visible():
                logging.info(f"🖱️  Clicking '{sel}' to navigate to form...")
                loc.first.click(timeout=3000)
                return True
        except Exception:
            pass
    return False

YES_WORDS = {"true": "Yes", "yes": "Yes", "y": "Yes", "1": "Yes"}
NO_WORDS = {"false": "No", "no": "No", "n": "No", "0": "No"}

def load_answers(primary: str, fallbacks: List[str]) -> Dict[str, Dict[str, Any]]:
    """Load answers from primary path or fallbacks"""
    paths = [primary] + fallbacks
    for p in paths:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError(f"{p} must be an object mapping id -> {{question, answer}}")
            logging.info(f"🗂 Using answers file: {p}")
            return data
    logging.info(f"No answers file found. Proceeding without JSON fields.")
    return {}

def to_display_answer(answer: Any) -> Any:
    """Convert answer to display format"""
    if isinstance(answer, bool): return "Yes" if answer else "No"
    if isinstance(answer, (int, float)): return str(answer)
    if isinstance(answer, str):
        v = answer.strip().lower()
        if v in YES_WORDS: return "Yes"
        if v in NO_WORDS: return "No"
    return answer

def find_field_control(page, field_id: str, question_text: str):
    """Find field control on page"""
    selectors = []
    if field_id:
        selectors.extend([f"[id='{field_id}']", f"[name='{field_id}']"])
        if not field_id[0].isdigit():
            selectors.insert(0, f"#{field_id}")
    
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                tag = loc.first.evaluate("e => e.tagName.toLowerCase()")
                if tag == "select":
                    return {"select": loc.first}
                elif tag == "input":
                    input_type = (loc.first.get_attribute("type") or "").lower()
                    if input_type == "file":
                        return {"file": loc.first}
                    role = (loc.first.get_attribute("role") or "").lower()
                    if role == "combobox":
                        return {"combo": loc.first}
                    return {"input": loc.first}
                elif tag == "textarea":
                    return {"textarea": loc.first}
        except Exception:
            continue

    # Try by label
    try:
        lbl = page.get_by_label(question_text, exact=True)
        if lbl.count() > 0:
            el = lbl.first
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            if tag == "select": return {"select": el}
            if tag == "textarea": return {"textarea": el}
            itype = (el.get_attribute("type") or "").lower()
            if itype == "file": return {"file": el}
            role = (el.get_attribute("role") or "").lower()
            if role == "combobox": return {"combo": el}
            return {"input": el}
    except Exception:
        pass

    return {}

def safe_fill_text(locator, value: str):
    """Fill text controls"""
    try:
        locator.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass

    input_type = None
    try:
        input_type = (locator.get_attribute("type") or "").lower()
    except Exception:
        pass

    if input_type in ("radio", "checkbox"):
        locator.check(timeout=6000)
        return

    try:
        locator.click(timeout=4000)
    except Exception:
        pass
    locator.fill(str(value), timeout=6000)

def select_native_select(select_locator, value: str) -> bool:
    """Select option in native select"""
    try:
        select_locator.select_option(label=value)
        return True
    except Exception:
        pass
    try:
        select_locator.select_option(value=value)
        return True
    except Exception:
        pass
    return False

def combo_first_visible_option(page):
    """Get first visible combobox option"""
    try:
        opts = page.get_by_role("option")
        if opts.count() == 0:
            opts = page.locator("[role='option'], .select__option, [id*='option-'], li[role='option']")
        opts = opts.filter(":visible")
        if opts.count() > 0:
            return opts.first
    except Exception:
        pass
    return None

def open_combo_type_slow_pick_first(page, combo_like, value: str) -> bool:
    """Open combobox, type slowly, and pick first option"""
    value = str(value)
    try:
        combo_like.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass
    try:
        combo_like.click(timeout=4000)
    except Exception:
        pass
    
    time.sleep(COMBO_OPEN_PAUSE_MS / 1000.0)
    
    try:
        input_like = combo_like.locator("input, [contenteditable='true']")
        target = input_like.first if input_like.count() > 0 else combo_like
        try:
            target.fill("")
        except Exception:
            pass
        target.type(value, delay=COMBO_TYPE_DELAY_MS)
    except Exception:
        try:
            page.keyboard.type(value, delay=COMBO_TYPE_DELAY_MS)
        except Exception:
            pass
    
    time.sleep(COMBO_POST_TYPE_WAIT_MS / 1000.0)
    option = combo_first_visible_option(page)
    try:
        if option and option.count() > 0:
            option.scroll_into_view_if_needed(timeout=2000)
            option.click(timeout=4000)
            return True
        page.keyboard.press("Enter")
        return True
    except Exception:
        return False

# ============= FILE UPLOAD SYSTEM (RESTORED & WORKING) =============

def _resume_path_abs() -> Optional[str]:
    """Get absolute resume path"""
    if RESUME_PATH.exists():
        return str(RESUME_PATH.resolve())
    return None

def _try_set_input(input_loc, file_path: str) -> bool:
    """Try to set file input"""
    try:
        input_loc.set_input_files(file_path)
        time.sleep(UPLOAD_SETTLE_MS / 1000.0)
        return True
    except Exception:
        return False

def _use_any_input_on_scope(scope, file_path: str) -> bool:
    """Try to use any file input in scope"""
    try:
        finput = scope.locator("input[type='file']")
        if finput.count() > 0:
            vis = finput.filter(":visible")
            if vis.count() > 0 and _try_set_input(vis.first, file_path):
                return True
            if _try_set_input(finput.first, file_path):
                return True
    except Exception:
        pass
    return False

def _click_and_use_file_chooser(page, scope, file_path: str) -> bool:
    """Click upload buttons and use file chooser"""
    groups = []
    try:
        btns = scope.get_by_role("button", name=UPLOAD_TEXT_RE)
        if btns.count() > 0:
            groups.append(btns)
    except Exception:
        pass
    try:
        more = scope.locator("button, input[type='button'], a, [role='button'], label").filter(has_text=UPLOAD_TEXT_RE)
        if more.count() > 0:
            groups.append(more)
    except Exception:
        pass

    for g in groups:
        n = min(g.count(), 10)
        for i in range(n):
            try:
                with page.expect_file_chooser(timeout=3000) as fc_info:
                    g.nth(i).click(timeout=3000)
                chooser = fc_info.value
                chooser.set_files(file_path)
                time.sleep(UPLOAD_SETTLE_MS / 1000.0)
                return True
            except Exception:
                continue
    return False

def _set_in_all_frames(page, file_path: str) -> bool:
    """Set file in all frame file inputs"""
    ok = False
    for frame in page.frames:
        try:
            finputs = frame.locator("input[type='file']")
            count = min(finputs.count(), 10)
            for i in range(count):
                try:
                    finputs.nth(i).set_input_files(file_path)
                    ok = True
                except Exception:
                    continue
        except Exception:
            continue
    if ok:
        time.sleep(UPLOAD_SETTLE_MS / 1000.0)
    return ok

def auto_resume_tick(page) -> bool:
    """
    ACTIVE FUNCTION - Opportunistically try to upload resume wherever possible.
    This is the KEY function that was disabled!
    """
    file_path = _resume_path_abs()
    if not file_path:
        logging.warning(f"❌ Resume file not found at {RESUME_PATH}")
        return False

    # 1) Try any file input on main page
    if _use_any_input_on_scope(page, file_path):
        logging.info("✅ Resume uploaded via input[type=file] on main page")
        return True

    # 2) Try any file input in iframes
    if _set_in_all_frames(page, file_path):
        logging.info("✅ Resume uploaded via input[type=file] inside an iframe")
        return True

    # 3) Try clicking upload-ish controls to open chooser
    if _click_and_use_file_chooser(page, page, file_path):
        logging.info("✅ Resume uploaded via file chooser on main page")
        return True
    
    for frame in page.frames:
        try:
            if _click_and_use_file_chooser(page, frame, file_path):
                logging.info("✅ Resume uploaded via file chooser inside an iframe")
                return True
        except Exception:
            continue

    return False

def enable_auto_resume_upload(page):
    """Attach global file chooser handler"""
    file_path = _resume_path_abs()
    if not file_path:
        logging.warning(f"❌ Resume not found at {RESUME_PATH}")
        return
    
    logging.info(f"📄 Resume available: {Path(file_path).name}")

    def _on_fc(fc):
        try:
            fc.set_files(file_path)
            logging.info(f"  ✅ File uploaded via chooser: {Path(file_path).name}")
        except Exception as e:
            logging.error(f"  ❌ File upload failed: {e}")

    page.on("filechooser", _on_fc)

# ===================== FIELD FILLING =====================

def fill_one_field(page, field_id: str, question_text: str, answer: Any):
    """Fill a single field"""
    ctrl = find_field_control(page, field_id, question_text)
    answer = to_display_answer(answer)

    if "select" in ctrl:
        ok = select_native_select(ctrl["select"], str(answer))
        logging.info(f"  {'✅' if ok else '⚠️'} Selected for: {question_text} -> {answer}")
        return

    if "combo" in ctrl:
        if open_combo_type_slow_pick_first(page, ctrl["combo"], str(answer)):
            logging.info(f"  ✅ Chosen (combo) for: {question_text} -> {answer}")
        else:
            logging.warning(f"  ⚠️ Could not choose (combo) '{answer}' for: {question_text}")
        return

    if "input" in ctrl or "textarea" in ctrl:
        target = ctrl.get("input") or ctrl.get("textarea")
        safe_fill_text(target, str(answer))
        logging.info(f"  ✅ Filled text for: {question_text} -> {answer}")
        return

    logging.warning(f"  ❓ Could not locate control for: {question_text} (id={field_id})")

# ===================== SUBMIT & SCREENSHOTS =====================

def maybe_click_submit(page) -> bool:
    """Try to click submit button"""
    candidates = [
        "button:has-text('Submit')",
        "button:has-text('Submit Application')",
        "button:has-text('Apply Now')",
        "button:has-text('Apply')",
        "button:has-text('Send Application')",
        "button:has-text('Next')",
        "input[type='submit']",
        "[role='button']:has-text('Submit')",
        "[role='button']:has-text('Apply')",
    ]
    for sel in candidates:
        try:
            el = page.locator(sel)
            if el.count() > 0:
                el.first.scroll_into_view_if_needed(timeout=2000)
                el.first.click(timeout=4000)
                logging.info("🔘 Clicked submit button")
                return True
        except Exception:
            pass
    logging.info("ℹ️ No submit button found")
    return False

def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def snap(page, prefix: str) -> str:
    """Take screenshot"""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, f"{prefix}_{timestamp()}.png")
    try:
        page.screenshot(path=path, full_page=True)
        logging.info(f"📸 Saved screenshot: {path}")
    except Exception:
        page.screenshot(path=path)
        logging.info(f"📸 Saved screenshot (viewport): {path}")
    return path

def approved() -> bool:
    """Check if submission is approved"""
    env = os.getenv("AUTO_APPROVE_SUBMIT", "").strip().lower()
    if env in ("1", "true", "yes", "y"):
        return True
    if not REQUIRE_APPROVAL:
        return True
    try:
        ans = input("Approve submit? [y/N]: ").strip().lower()
        return ans in ("y", "yes")
    except EOFError:
        return False

# ===================== MAIN =====================

def main():
    """Main execution"""
    answers = load_answers(ANSWERS_PATH, ANSWERS_FALLBACKS)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=HEADLESS, slow_mo=SLOW_MO_MS)
        # Video recording disabled
        context = browser.new_context(
            accept_downloads=True,
        )
        page = context.new_page()

        logging.info(f"🧭 Navigating to: {JOB_URL}")
        try:
            page.goto(JOB_URL, wait_until="load", timeout=120_000)
            try:
                page.wait_for_load_state("networkidle", timeout=60_000)
            except Exception:
                pass
        except PWTimeout:
            logging.warning("⚠️ Page load timed out; proceeding...")

        # Try to navigate to form
        logging.info("🖱️  Attempting to click Apply/Continue buttons...")
        if click_apply_like_things(page):
            logging.info("✅ Successfully clicked Apply/Continue button")
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass
            page.wait_for_timeout(2000)
        else:
            logging.info("⚠️  No Apply/Continue buttons found")

        logging.info(f"📍 Current URL: {page.url}")

        # Enable GLOBAL file chooser handler
        enable_auto_resume_upload(page)

        # *** CRITICAL: First attempt to upload resume ***
        logging.info("🔄 Attempting initial resume upload...")
        auto_resume_tick(page)

        # Fill all fields
        for field_id, bundle in answers.items():
            # Handle both flat format {id: value} and wrapped format {id: {question, answer}}
            if isinstance(bundle, dict) and "answer" in bundle:
                # Wrapped format
                question = (bundle.get("question") or "").strip()
                answer = bundle.get("answer")
            else:
                # Flat format
                question = field_id
                answer = bundle
            
            if answer is None:
                continue

            # *** CRITICAL: Try uploading between fields ***
            auto_resume_tick(page)

            fill_one_field(page, field_id, question, answer)

        # *** CRITICAL: One more try before submit ***
        logging.info("🔄 Final resume upload attempt before submit...")
        auto_resume_tick(page)

        # Screenshot before submit
        before_path = snap(page, "before_submit")

        # *** CRITICAL: Last chance right before submit ***
        auto_resume_tick(page)

        # Submit
        did_submit = False
        if SUBMIT_AT_END:
            if approved():
                logging.info("✅ User approved submission. Attempting to submit...")
                did_submit = maybe_click_submit(page)
                time.sleep(2)
            else:
                logging.info("❎ Submission not approved")

        # Screenshot after submit
        after_path = snap(page, "after_submit" if did_submit else "after_skip")

        logging.info("✅ Finished.")
        logging.info(f"➡️  Before: {before_path}")
        logging.info(f"➡️  After : {after_path}")

        context.close()
        browser.close()

if __name__ == "__main__":
    main()
