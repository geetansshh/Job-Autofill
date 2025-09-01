import os, uuid
from typing import Any, Dict, List
from .config import RESUME_PDF, SCREENSHOT_DIR
from .widgets import select_in_combobox
from .dom import read_current_value

def upload_resume_if_possible(fields: List[Dict[str, Any]]):
    for f in fields:
        el, widget = f["el"], f["widget"]
        hay = " ".join([f.get("label",""), f.get("placeholder",""), f.get("name",""), f.get("aria","")]).lower()
        looks_like_resume = (widget == "input" and (f["type"] == "file")) or any(s in hay for s in ["resume", "cv"])
        if looks_like_resume and os.path.exists(RESUME_PDF):
            try: el.set_input_files(RESUME_PDF)
            except Exception: pass

def fill_value_into_field(f: Dict[str, Any], val, page=None):
    el, widget, typ, frame = f["el"], f["widget"], (f["type"] or "").lower(), f["frame"]
    try:
        if widget == "select":
            if isinstance(val, list):
                try: el.select_option(value=[str(v) for v in val])
                except Exception:
                    try: el.select_option(label=[str(v) for v in val])
                    except Exception: pass
            else:
                try: el.select_option(value=str(val))
                except Exception:
                    try: el.select_option(label=str(val))
                    except Exception: pass
        elif widget == "combobox":
            select_in_combobox(page, frame, el, val)
        elif widget in ("checkbox","radio"):
            if widget == "checkbox":
                truthy = str(val).lower() in ("true","1","yes","y","on")
                try:
                    if truthy: el.check()
                    else: el.uncheck()
                except Exception: pass
            else:
                try: el.check()
                except Exception:
                    try: el.click()
                    except Exception: pass
        else:
            el.fill(""); el.type(str(val))
    except Exception:
        pass

def click_submit(page):
    for sel in [
        "button:has-text('Submit application')",
        "button:has-text('Apply')",
        "button:has-text('Submit')",
        "input[type='submit']",
        "button[type='submit']",
        "button:has-text('Next')",
        "button:has-text('Continue')",
    ]:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                loc.first.click()
                return True
        except Exception:
            continue
    return False

def success_indicator(page) -> bool:
    try:
        for c in [
            "Thank you for applying",
            "Application submitted",
            "We received your application",
            "Thanks for your application",
            "Your application has been received",
        ]:
            if page.locator(f":text('{c}')").count() > 0:
                return True
    except Exception: pass
    return False
