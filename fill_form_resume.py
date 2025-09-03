#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
fill_form.py — Autofill + AUTO resume upload (independent of answers) + slow combos + screenshots

What’s new vs earlier:
- Auto resume upload is ENABLED globally, not tied to answers:
  * scans for <input type="file"> on page & iframes
  * listens for file chooser (native dialog) and supplies the resume
  * retries opportunistically during the fill and right before submit
- Keeps: slow typing for dropdowns, video recording, before/after screenshots with approval
- (Commented) "Locate me" helper for later

Run:
  RESUME_FILE="/absolute/path/to/resume.pdf" python fill_form.py
  # or set RESUME_PATH below
"""

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Iterable

# ========================= CONFIG =========================
JOB_URL = "https://job-boards.greenhouse.io/capco/jobs/6924131"   # <-- put the form URL here

# Answers file + fallbacks (still used for other fields; resume upload is now independent)
ANSWERS_PATH = "user_completed_answers.json"
ANSWERS_FALLBACKS = ["user_sompleted_answers.json", "answers.json"]

# Resume path (env override recommended)
RESUME_PATH = Path(os.getenv("RESUME_FILE", "")).expanduser() if os.getenv("RESUME_FILE") else (Path("data") / "resume.pdf")
COVER_LETTER_PATH = Path("cover_letter.txt")  # used only if you also want cover letter uploads

HEADLESS = False
SUBMIT_AT_END = True
REQUIRE_APPROVAL = True

# Slow-mo + video
SLOW_MO_MS = 300
RECORD_VIDEO_DIR = "videos"

# Screenshots
SCREENSHOT_DIR = "screenshots"

# Combobox "type slow" behavior
COMBO_OPEN_PAUSE_MS = 150
COMBO_TYPE_DELAY_MS = 160
COMBO_POST_TYPE_WAIT_MS = 600

# Upload helper timing
UPLOAD_SETTLE_MS = 800

# Texts that usually trigger a file chooser
UPLOAD_TEXT_RE = re.compile(r"(upload|browse|choose file|attach|select file|resume|cv)", re.I)

# (Commented) geolocation button text
LOCATE_BUTTON_RE = re.compile(r"(locate me|use my location|detect location|current location)", re.I)
# =========================================================

YES_WORDS = {"true": "Yes", "yes": "Yes", "y": "Yes", "1": "Yes"}
NO_WORDS  = {"false": "No", "no": "No", "n": "No", "0": "No"}

def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def load_answers(primary: str, fallbacks: List[str]) -> Dict[str, Dict[str, Any]]:
    paths = [primary] + fallbacks
    for p in paths:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError(f"{p} must be an object mapping id -> {{question, answer}}")
            print(f"🗂 Using answers file: {p}")
            return data
    print(f"ℹ️ No answers file found at {paths}. Proceeding without JSON fields.")
    return {}

def to_display_answer(answer: Any) -> Any:
    if isinstance(answer, bool): return "Yes" if answer else "No"
    if isinstance(answer, (int, float)): return str(answer)
    if isinstance(answer, str):
        v = answer.strip().lower()
        if v in YES_WORDS: return "Yes"
        if v in NO_WORDS:  return "No"
    return answer

# ---------- locator discovery ----------

def find_field_control(page, field_id: str, question_text: str):
    """
    Return dict keys among: input, textarea, select, file, radio_container, combo, container
    """
    # direct id/name
    for sel in [f"#{field_id}", f"[name='{field_id}']"]:
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

    # by accessible label
    try:
        lbl = page.get_by_label(question_text, exact=True)
        if lbl.count() > 0:
            el = lbl.first
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            if tag == "select":   return {"select": el}
            if tag == "textarea": return {"textarea": el}
            itype = (el.get_attribute("type") or "").lower()
            if itype == "file":   return {"file": el}
            role = (el.get_attribute("role") or "").lower()
            if role == "combobox": return {"combo": el}
            return {"input": el}
    except Exception:
        pass

    # label element -> associated control or sibling
    try:
        lab = page.locator(f"label:has-text('{question_text}')").first
        if lab.count() > 0:
            for_attr = lab.get_attribute("for")
            if for_attr:
                el = page.locator(f"#{for_attr}")
                if el.count() > 0:
                    tag = el.first.evaluate("e => e.tagName.toLowerCase()")
                    if tag == "select":   return {"select": el.first}
                    if tag == "textarea": return {"textarea": el.first}
                    itype = (el.first.get_attribute("type") or "").lower()
                    if itype == "file":   return {"file": el.first}
                    role = (el.first.get_attribute("role") or "").lower()
                    if role == "combobox": return {"combo": el.first}
                    return {"input": el.first}
            sibling = lab.locator("xpath=following::*[self::input or self::select or self::textarea][1]")
            if sibling.count() > 0:
                tag = sibling.first.evaluate("e => e.tagName.toLowerCase()")
                if tag == "select":   return {"select": sibling.first}
                if tag == "textarea": return {"textarea": sibling.first}
                itype = (sibling.first.get_attribute("type") or "").lower()
                if itype == "file":   return {"file": sibling.first}
                role = (sibling.first.get_attribute("role") or "").lower()
                if role == "combobox": return {"combo": sibling.first}
                return {"input": sibling.first}
    except Exception:
        pass

    # placeholder
    try:
        ph = page.get_by_placeholder(question_text, exact=True)
        if ph.count() > 0:
            el = ph.first
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            if tag == "textarea": return {"textarea": el}
            return {"input": el}
    except Exception:
        pass

    # Look near the label for combos/files
    container = None
    try:
        label = page.get_by_text(question_text, exact=True).first
        if label.count() > 0:
            container = label.locator("xpath=ancestor::*[self::div or self::section or self::li][1]")
            combo = container.locator("[role='combobox'], [aria-haspopup='listbox'], [aria-expanded], div[class*='select'], div[class*='combo']")
            if combo.count() > 0:
                return {"combo": combo.first, "container": container}
            finput = container.locator("input[type='file']")
            if finput.count() > 0:
                return {"file": finput.first, "container": container}
    except Exception:
        pass

    # Radio/checkbox groups near label
    try:
        if container is None:
            label = page.get_by_text(question_text, exact=True).first
            if label.count() > 0:
                container = label.locator("xpath=ancestor::*[self::div or self::fieldset][1]")
        if container:
            radios = container.get_by_role("radio")
            checks = container.get_by_role("checkbox")
            if radios.count() > 0 or checks.count() > 0:
                return {"radio_container": container, "container": container}
    except Exception:
        pass

    return {"container": container} if container else {}

# ---------- basic fill helpers ----------

def safe_fill_text(locator, value: str):
    try: locator.scroll_into_view_if_needed(timeout=2000)
    except Exception: pass
    locator.click(timeout=4000)
    locator.fill(str(value), timeout=6000)

def click_option_label_near(page, label_text: str) -> bool:
    candidates = [
        page.get_by_label(label_text, exact=True),
        page.get_by_role("radio", name=label_text),
        page.get_by_role("checkbox", name=label_text),
        page.locator(f"label:has-text('{label_text}')"),
    ]
    for c in candidates:
        try:
            if c.count() > 0:
                c.first.scroll_into_view_if_needed(timeout=2000)
                c.first.click(timeout=4000)
                return True
        except Exception:
            pass
    try:
        opts = page.get_by_role("radio").filter(has_text=label_text)
        if opts.count() > 0:
            opts.first.scroll_into_view_if_needed(timeout=2000)
            opts.first.click(timeout=4000)
            return True
    except Exception:
        pass
    return False

# ---------- selects / combos ----------

def select_native_select(select_locator, value: str) -> bool:
    try:
        select_locator.select_option(label=value); return True
    except Exception: pass
    try:
        select_locator.select_option(value=value); return True
    except Exception: pass
    try:
        options = select_locator.locator("option")
        count = options.count()
        for i in range(count):
            txt = (options.nth(i).text_content() or "").strip()
            val = (options.nth(i).get_attribute("value") or "").strip()
            if val or (txt and "select" not in txt.lower() and "choose" not in txt.lower() and "please" not in txt.lower()):
                select_locator.select_option(index=i); return True
    except Exception: pass
    return False

def combo_first_visible_option(page):
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
    value = str(value)
    try: combo_like.scroll_into_view_if_needed(timeout=2000)
    except Exception: pass
    try: combo_like.click(timeout=4000)
    except Exception:
        try:
            box = combo_like.bounding_box()
            if box: page.mouse.click(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
        except Exception: pass
    time.sleep(COMBO_OPEN_PAUSE_MS / 1000.0)
    typed = False
    try:
        input_like = combo_like.locator("input, [contenteditable='true']")
        target = input_like.first if input_like.count() > 0 else combo_like
        try: target.fill("")
        except Exception: pass
        target.type(value, delay=COMBO_TYPE_DELAY_MS)
        typed = True
    except Exception:
        try:
            page.keyboard.type(value, delay=COMBO_TYPE_DELAY_MS)
            typed = True
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
        return True if typed else False
    except Exception:
        try:
            page.keyboard.press("Enter")
            return True if typed else False
        except Exception:
            return False

# ---------- AUTO RESUME UPLOAD (INDEPENDENT OF ANSWERS) ----------

def _try_set_input(input_loc, file_path: str) -> bool:
    try:
        input_loc.set_input_files(file_path)
        time.sleep(UPLOAD_SETTLE_MS / 1000.0)
        return True
    except Exception:
        return False

def _use_any_input_on_scope(scope, file_path: str) -> bool:
    try:
        finput = scope.locator("input[type='file']")
        if finput.count() > 0:
            vis = finput.filter(":visible")
            # Prefer visible input if any
            if vis.count() > 0 and _try_set_input(vis.first, file_path):
                return True
            # else try the first one
            if _try_set_input(finput.first, file_path):
                return True
    except Exception:
        pass
    return False

def _click_and_use_file_chooser(page, scope, file_path: str) -> bool:
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
    if ok: time.sleep(UPLOAD_SETTLE_MS / 1000.0)
    return ok

def _resume_path_abs() -> Optional[str]:
    p = Path(RESUME_PATH).expanduser().resolve()
    return str(p) if p.exists() else None

def auto_resume_tick(page) -> bool:
    """
    Opportunistically try to upload the resume wherever possible (page + iframes),
    without relying on any answers/labels.
    """
    file_path = _resume_path_abs()
    if not file_path:
        print(f"❌ Resume file not found at {Path(RESUME_PATH).expanduser().resolve()}")
        return False

    # 1) Any file input on main page
    if _use_any_input_on_scope(page, file_path):
        print("✅ Resume uploaded via input[type=file] on main page")
        return True

    # 2) Any file input in iframes
    if _set_in_all_frames(page, file_path):
        print("✅ Resume uploaded via input[type=file] inside an iframe")
        return True

    # 3) Try clicking upload-ish controls to open chooser (page then frames)
    if _click_and_use_file_chooser(page, page, file_path):
        print("✅ Resume uploaded via file chooser on main page")
        return True
    for frame in page.frames:
        try:
            if _click_and_use_file_chooser(page, frame, file_path):
                print("✅ Resume uploaded via file chooser inside an iframe")
                return True
        except Exception:
            continue

    # Nothing found this tick
    return False

def enable_auto_resume_upload(page):
    """
    Attach file-chooser handler and do an initial scan.
    Call auto_resume_tick() anytime during the run to re-attempt.
    """
    file_path = _resume_path_abs()
    if not file_path:
        print(f"❌ Resume path missing. Set RESUME_FILE env var or update RESUME_PATH.")
    else:
        print(f"📄 Using resume: {file_path}")

    # 1) Hook file chooser globally
    def _on_fc(fc):
        fp = _resume_path_abs()
        if not fp:
            print("⚠️ File chooser opened but resume file not found.")
            return
        try:
            fc.set_files(fp)
            print("✅ Resume set via file chooser handler")
        except Exception as e:
            print(f"⚠️ File chooser handler failed: {e}")

    page.on("filechooser", _on_fc)

    # 2) Initial attempt right away
    try:
        auto_resume_tick(page)
    except Exception:
        pass

# ---------- orchestrator for non-file fields ----------

def fill_one_field(page, field_id: str, question_text: str, answer: Any):
    ctrl = find_field_control(page, field_id, question_text)
    answer = to_display_answer(answer)

    # Native <select>
    if "select" in ctrl:
        if isinstance(answer, list):
            ok_all = True
            for a in answer:
                ok = select_native_select(ctrl["select"], str(a)); ok_all = ok_all and ok
                time.sleep(0.2)
            print(("  ✅ Selected (multi) " if ok_all else "  ⚠️ Partial select "), f"for: {question_text} -> {answer}")
        else:
            ok = select_native_select(ctrl["select"], str(answer))
            print(("  ✅ Selected" if ok else "  ⚠️ Could not select"), f"for: {question_text} -> {answer}")
        return

    # React/custom combo
    if "combo" in ctrl:
        if isinstance(answer, list):
            for a in answer:
                if not open_combo_type_slow_pick_first(page, ctrl["combo"], str(a)):
                    print(f"  ⚠️ Could not choose (combo) '{a}' for: {question_text}")
                time.sleep(0.2)
        else:
            if open_combo_type_slow_pick_first(page, ctrl["combo"], str(answer)):
                print(f"  ✅ Chosen (combo) for: {question_text} -> {answer}")
            else:
                print(f"  ⚠️ Could not choose (combo) '{answer}' for: {question_text}")
        return

    # Radio/checkbox groups
    if "radio_container" in ctrl:
        if isinstance(answer, list):
            for a in answer:
                if not click_option_label_near(page, str(a)):
                    print(f"  ⚠️ Could not click option '{a}' for: {question_text}")
        else:
            if not click_option_label_near(page, str(answer)):
                print(f"  ⚠️ Could not click option '{answer}' for: {question_text}")
            else:
                print(f"  ✅ Clicked option for: {question_text} -> {answer}")
        return

    # Text input / textarea
    if "input" in ctrl or "textarea" in ctrl:
        target = ctrl.get("input") or ctrl.get("textarea")
        safe_fill_text(target, str(answer))
        print(f"  ✅ Filled text for: {question_text} -> {answer}")
        return

    print(f"  ❓ Could not locate control for: {question_text} (id={field_id}). Skipped.")

# ---------- submit + screenshots ----------

def maybe_click_submit(page) -> bool:
    candidates = [
        "button:has-text('Submit')",
        "button:has-text('Apply')",
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
                print("🔘 Clicked submit-like button.")
                return True
        except Exception:
            pass
    print("ℹ️ No obvious submit button clicked.")
    return False

def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def snap(page, prefix: str) -> str:
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, f"{prefix}_{timestamp()}.png")
    try:
        page.screenshot(path=path, full_page=True)
        print(f"📸 Saved screenshot: {path}")
    except Exception:
        page.screenshot(path=path)
        print(f"📸 Saved screenshot (viewport): {path}")
    return path

def approved() -> bool:
    env = os.getenv("AUTO_APPROVE_SUBMIT", "").strip().lower()
    if env in ("1", "true", "yes", "y"): return True
    if not REQUIRE_APPROVAL: return True
    try:
        ans = input("Approve submit? [y/N]: ").strip().lower()
        return ans in ("y", "yes")
    except EOFError:
        return False

# --- OPTIONAL (COMMENTED) GEO-LOCATION FLOW ---
# def try_locate_me(page, context):
#     origin = "https://job-boards.greenhouse.io"
#     context.set_geolocation({"latitude": 35.681236, "longitude": 139.767125})
#     context.grant_permissions(["geolocation"], origin=origin)
#     try:
#         btn = page.get_by_role("button", name=LOCATE_BUTTON_RE)
#         if btn.count() == 0:
#             btn = page.locator("button, a, [role='button'], label").filter(has_text=LOCATE_BUTTON_RE)
#         if btn.count() > 0:
#             btn.first.scroll_into_view_if_needed(timeout=2000)
#             btn.first.click(timeout=4000)
#             time.sleep(2)
#             print("📍 'Locate me' clicked and location granted.")
#             return True
#     except Exception as e:
#         print(f"⚠️ Locate me flow failed: {e}")
#     return False

# ---------- main ----------

def main():
    answers = load_answers(ANSWERS_PATH, ANSWERS_FALLBACKS)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=SLOW_MO_MS)
        os.makedirs(RECORD_VIDEO_DIR, exist_ok=True)
        context = browser.new_context(
            accept_downloads=True,
            record_video_dir=RECORD_VIDEO_DIR,
            record_video_size={"width": 1280, "height": 720},
        )
        page = context.new_page()

        print(f"🧭 Navigating to: {JOB_URL}")
        try:
            page.goto(JOB_URL, wait_until="load", timeout=120_000)
            try:
                page.wait_for_load_state("networkidle", timeout=60_000)
            except Exception:
                pass
        except PWTimeout:
            print("⚠️ Page load timed out; proceeding...")

        print(f"🎥 Recording enabled. Saving to: {RECORD_VIDEO_DIR}")

        # Enable GLOBAL, JSON-INDEPENDENT resume upload
        enable_auto_resume_upload(page)

        # First opportunistic attempt (in case input is already in DOM)
        auto_resume_tick(page)

        # Fill all answers (for non-file fields)
        for field_id, bundle in answers.items():
            question = (bundle.get("question") or "").strip()
            answer = bundle.get("answer")
            if not question: 
                continue

            # opportunistically try uploading resume between fields too (if widget appears late)
            auto_resume_tick(page)

            if answer is None:
                continue
            fill_one_field(page, field_id, question, answer)

        # One more try before snapshot/submit (late-rendered widgets)
        auto_resume_tick(page)

        # Screenshot BEFORE submit
        before_path = snap(page, "before_submit")

        # Last-chance try after everything is filled, right before submit
        auto_resume_tick(page)

        # Approval gate
        did_submit = False
        if SUBMIT_AT_END:
            if approved():
                try:
                    with page.expect_navigation(wait_until="load", timeout=60000):
                        did_submit = maybe_click_submit(page)
                except Exception:
                    did_submit = maybe_click_submit(page)
                time.sleep(2)
            else:
                print("❎ Submission not approved. Skipping submit.")

        # Screenshot AFTER submit (or after skip)
        after_path = snap(page, "after_submit" if did_submit else "after_skip")

        print("✅ Finished.")
        print(f"➡️  Before: {before_path}")
        print(f"➡️  After : {after_path}")

        context.close()
        browser.close()

if __name__ == "__main__":
    main()
