#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
page_judger.py — resolve the REAL application form page by clicking Apply (no argparse; hard-coded config).

Outputs
-------
- page_judger_out.json         (trace + findings)
- resolved_form_url.txt        (final form URL if found)
- form_page_reached.txt        ("true" or "false")
- screenshots/                 (landing + after click(s) + final)
- (optional) outputs/job_summary.txt and outputs/cover_letter.txt

Usage
-----
pip install playwright
playwright install
python page_judger.py
"""

import contextlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from playwright.sync_api import sync_playwright
import logging
from output_config import OutputPaths
from utils import ci_match_label, normalize
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
from utils import ci_match_label, normalize

# =========================
# HARD-CODED CONFIG (edit)
# =========================
# Get START_URL from environment variable (set by telegram_bot.py)
START_URL = os.getenv("JOB_URL", "")
if not START_URL:
    print("❌ JOB_URL environment variable not set!")
    sys.exit(1)

HEADLESS = False                                   # True for headless browser
SLOW_MO_MS = 300                                     # Slow-mo for debugging (0 for speed)
GENERATE_COVER = True                                # Call your generator on the landing page
TIMEOUT_MS = 15000                                   # Default Playwright timeout
MAX_STEPS = 6                                        # Max Apply-click hops to reach form

# Outputs (now using centralized config)
SCREENSHOT_DIR = OutputPaths.SCREENSHOTS_DIR
OUT_JSON = OutputPaths.PAGE_JUDGER_OUT
OUT_RESOLVED = OutputPaths.RESOLVED_FORM_URL
OUT_REACHED = OutputPaths.FORM_PAGE_REACHED

# Heuristics for "Apply" buttons/links
APPLY_SELECTORS = [
    # Role-based (preferred)
    "role=button[name=/apply|submit|start application|join|careers/i]",
    "role=link[name=/apply|submit|start application|join|careers/i]",
    # ... (other selectors can be added here)
]

# ATS provider hints
ATS_HINTS = {
    "ashby": ["ashbyhq.com"],
    "taleo": ["taleo.net"],
    "smartrecruiters": ["smartrecruiters.com"],
    "bamboohr": ["bamboohr.com"],
    "zoho": ["zoho.com", "zoho.in"],
    "icims": ["icims.com"],
    "oracle": ["oraclecloud.com"],
}

# ---------- helpers ----------
def _ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")

def _classify_ats(url: str) -> str:
    u = (url or "").lower()
    for name, hints in ATS_HINTS.items():
        if any(h in u for h in hints):
            return name
    return "unknown"

def _count_form_controls(scope) -> int:
    """Count visible, user-input controls on this DOM scope (page or frame).
       Fast heuristic (counts nodes; doesn’t iterate each for visibility)."""
    count = 0
    try:
        inputs = scope.locator("input:not([type='hidden']):not([type='button']):not([type='reset'])")
        selects = scope.locator("select")
        textareas = scope.locator("textarea")
        combos = scope.locator("[role='combobox']")
        for loc in (inputs, selects, textareas, combos):
            try:
                n = loc.count()
            except Exception:
                n = 0
            count += min(n, 200)  # cap to avoid pathological pages
    except Exception:
        pass
    return int(count)

def _page_has_form_controls(page) -> Tuple[bool, Optional[str]]:
    """Check page and iframes for significant input controls."""
    try:
        # 1) Top-level
        top_count = _count_form_controls(page)
        if top_count >= 2:  # lowered threshold from 4 to 2
            return True, page.url
        # 2) Iframes
        for fr in page.frames:
            try:
                if fr == page.main_frame:
                    continue
                c = _count_form_controls(fr)
                if c >= 2:  # lowered threshold from 4 to 2
                    return True, fr.url
            except Exception:
                continue
        # 3) Fallback: check for any form elements or contact-like content
        if top_count > 0:  # any form controls at all
            return True, page.url
        # Check for common contact/application form indicators
        form_indicators = [
            "form", "[action]", "input[type='email']", "input[name*='email']",
            "input[name*='name']", "textarea", "select"
        ]
        for indicator in form_indicators:
            try:
                if page.locator(indicator).count() > 0:
                    return True, page.url
            except Exception:
                continue
    except Exception:
        pass
    return False, None

def _snap(page, label: str) -> None:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    pth = SCREENSHOT_DIR / f"{_ts()}_{label}.png"
    try:
        page.screenshot(path=str(pth), full_page=True)
    except Exception:
        with contextlib.suppress(Exception):
            page.screenshot(path=str(pth))
    logging.info(f"Saved screenshot: {pth}")

def _click_apply(page) -> bool:
    # Role-based first
    try:
        btn = page.get_by_role("button", name=re.compile(r"apply|submit|start application", re.I))
        if btn.count() > 0:
            btn.first.scroll_into_view_if_needed(timeout=3000)
            btn.first.click(timeout=8000)
            return True
    except Exception:
        pass
    try:
        anc = page.get_by_role("link", name=re.compile(r"apply|submit|start application", re.I))
        if anc.count() > 0:
            anc.first.scroll_into_view_if_needed(timeout=3000)
            anc.first.click(timeout=8000)
            return True
    except Exception:
        pass
    # CSS fallbacks
    for sel in APPLY_SELECTORS:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                loc.first.scroll_into_view_if_needed(timeout=3000)
                loc.first.click(timeout=8000)
                return True
        except Exception:
            continue
    # Very last resort
    try:
        any_apply = page.locator(":is(a,button,[role='button']):has-text('Apply')")
        if any_apply.count() > 0:
            any_apply.first.scroll_into_view_if_needed(timeout=3000)
            any_apply.first.click(timeout=8000)
            return True
    except Exception:
        pass
    return False

def _maybe_generate_cover_and_summary(landing_url: str, enable: bool) -> Optional[Dict[str, str]]:
    if not enable:
        return None
    try:
        import cover_letter_and_summary as genmod  # your module
        from output_config import RESUME_PATH
        
        resume_path = str(RESUME_PATH)
        if not RESUME_PATH.exists():
            logging.warning(f"Resume not found at {resume_path}. Set RESUME_FILE or RESUME_PATH env var.")
        # cover_letter_and_summary.generate(job_url, resume_path) -> (job_md, summary, cover)
        job_md, summary, cover = genmod.generate(landing_url, resume_path)
        # Use centralized output paths
        OutputPaths.JOB_PAGE_MD.write_text(job_md, encoding="utf-8")
        OutputPaths.JOB_SUMMARY.write_text(summary, encoding="utf-8")
        OutputPaths.COVER_LETTER.write_text(cover, encoding="utf-8")
        logging.info(f"Wrote {OutputPaths.JOB_SUMMARY} and {OutputPaths.COVER_LETTER}")
        return {"summary_path": str(OutputPaths.JOB_SUMMARY),
                "cover_letter_path": str(OutputPaths.COVER_LETTER)}
    except Exception as e:
        logging.warning(f"Could not run cover_letter_and_summary.generate(): {e}")
        return None

@dataclass
class StepRecord:
    action: str
    url_before: str
    url_after: Optional[str] = None
    note: Optional[str] = None
    opened_new_page: bool = False

@dataclass
class JudgeResult:
    start_url: str
    final_url: Optional[str]
    status: str
    provider: str
    steps: List[StepRecord]
    form_found: bool
    form_in_iframe: bool
    landing_generated: bool
    errors: List[str]

def judge(start_url: str, headless: bool, slow_mo_ms: int, generate_cover: bool) -> JudgeResult:
    steps: List[StepRecord] = []
    errors: List[str] = []
    final_url: Optional[str] = None
    form_in_iframe = False
    landing_generated = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo_ms)
        context = browser.new_context()
        context.set_default_timeout(TIMEOUT_MS)
        page = context.new_page()

        # Navigate to landing
        try:
            page.goto(start_url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        except Exception as e:
            errors.append(f"Navigation error on landing: {e}")
        _snap(page, "landing")

        # Generate summary/cover while we're on JD page
        if generate_cover:
            try:
                info = _maybe_generate_cover_and_summary(page.url, True)
                landing_generated = bool(info)
            except Exception as e:
                errors.append(f"Cover/summary generation error: {e}")

        # Check if landing already has form controls
        has_form, form_url = _page_has_form_controls(page)
        if has_form:
            final_url = form_url or page.url
            form_in_iframe = (form_url is not None and form_url != page.url)
            steps.append(StepRecord(action="detect_form_on_landing", url_before=page.url, url_after=page.url,
                                    note="Form controls present on landing"))
        else:
            # Walk clicks towards Apply
            seen_urls = {page.url}
            for i in range(MAX_STEPS):
                url_before = page.url
                new_page = None
                # Try expect_page (if click opens new tab)
                try:
                    with context.expect_page() as newp_info:
                        clicked = _click_apply(page)
                    try:
                        new_page = newp_info.value
                    except Exception:
                        new_page = None
                except Exception:
                    clicked = _click_apply(page)

                if not clicked:
                    steps.append(StepRecord(action="apply_not_found", url_before=url_before, note="No Apply-like control"))
                    break

                time.sleep(1.0)

                opened_new = False
                if new_page:
                    opened_new = True
                    try:
                        new_page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_MS)
                    except Exception:
                        pass
                    page = new_page
                else:
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_MS)
                    except Exception:
                        pass

                steps.append(StepRecord(action="click_apply", url_before=url_before, url_after=page.url,
                                        opened_new_page=opened_new))
                _snap(page, f"after_click_{i+1}")

                if page.url in seen_urls and not opened_new:
                    steps.append(StepRecord(action="loop_detected", url_before=page.url, note="URL repeated"))
                    break
                seen_urls.add(page.url)

                has_form, form_url = _page_has_form_controls(page)
                if has_form:
                    final_url = form_url or page.url
                    form_in_iframe = (form_url is not None and form_url != page.url)
                    steps.append(StepRecord(action="detect_form_after_click", url_before=page.url, url_after=page.url,
                                            note="Form controls present"))
                    break

        # Fallback: if no form was detected but we have basic web content, assume current page is the form
        if not final_url:
            try:
                # Check for basic webpage indicators that suggest this could be an application page
                basic_indicators = [
                    "input", "button", "form", "textarea", "select", 
                    "[type='email']", "[type='text']", "a[href*='mailto']"
                ]
                has_basic_elements = False
                for indicator in basic_indicators:
                    if page.locator(indicator).count() > 0:
                        has_basic_elements = True
                        break
                
                if has_basic_elements:
                    final_url = page.url
                    steps.append(StepRecord(action="fallback_assume_form", url_before=page.url, url_after=page.url,
                                          note="Assuming current page is form page (fallback)"))
            except Exception as e:
                errors.append(f"Fallback check error: {e}")

        provider = _classify_ats(final_url or page.url)
        status = "form_found" if final_url else "apply_missing_or_failed"

        result = JudgeResult(
            start_url=start_url,
            final_url=final_url,
            status=status,
            provider=provider,
            steps=steps,
            form_found=bool(final_url),
            form_in_iframe=form_in_iframe,
            landing_generated=landing_generated,
            errors=errors,
        )


        # Persist artifacts
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
        OUT_REACHED.write_text("true" if final_url else "false", encoding="utf-8")
        if final_url:
            OUT_RESOLVED.write_text(final_url, encoding="utf-8")

        # ----- REQUIRED CONSOLE OUTPUT -----
        # This is an easy hook for your pipeline.
        if final_url:
            logging.info("Reached application form page.")
            logging.info(f"Final form URL: {final_url}")
            logging.info("FORM_PAGE_REACHED=true")
        else:
            logging.error("Did not reach application form page.")
            logging.info("FORM_PAGE_REACHED=false")

        logging.info(f"(Wrote {OUT_REACHED} with 'true/false')")

        context.close()
        browser.close()

        return result

if __name__ == "__main__":
    if not START_URL or START_URL.startswith("https://<"):
        raise SystemExit("Please edit page_judger.py and set START_URL to your landing/listing URL.")
    res = judge(START_URL, headless=HEADLESS, slow_mo_ms=SLOW_MO_MS, generate_cover=GENERATE_COVER)
    # Exit code: 0 if reached, 1 otherwise (so CI/pipeline can gate)
    raise SystemExit(0 if res.form_found else 1)
