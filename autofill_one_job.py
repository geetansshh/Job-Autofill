#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
General ATS auto-filler with LLM cover letter.
Key fix: dropdown/combobox selection is STRICTLY from visible options (no free typing).
"""

import json, os, uuid, re, time
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple, Set

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ========= EDIT THESE =========
JOB_URL = "https://job-boards.greenhouse.io/capco/jobs/7188625"  # set your link
PARSED_JSON = "./parsed_resume.json"
RESUME_PDF = "./data/resume.pdf"
RESUME_TXT = "./data/resume.txt"                     # optional text resume (preferred for LLM)
HEADLESS = True
SCREENSHOT_DIR = "./screenshots"
# ==============================

os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# --- load .env early ---
try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(), override=False)
except Exception:
    pass

# ---------- profile helpers ----------

def load_profile() -> Dict[str, Any]:
    if not os.path.exists(PARSED_JSON):
        raise FileNotFoundError(f"parsed resume JSON not found: {PARSED_JSON}")
    with open(PARSED_JSON, "r", encoding="utf-8") as f:
        return json.load(f)

def _first_str(val):
    if val is None:
        return None
    if isinstance(val, str):
        return val.strip() or None
    if isinstance(val, list):
        for x in val:
            if isinstance(x, str) and x.strip():
                return x.strip()
    return None

def extract_contact(profile: Dict[str, Any]) -> Dict[str, Optional[str]]:
    name = _first_str(profile.get("name") or profile.get("full_name"))
    first_name, last_name = None, None
    if name:
        parts = [p for p in name.strip().split() if p]
        if parts:
            first_name = parts[0]
            if len(parts) > 1:
                last_name = parts[-1]
    email = _first_str(profile.get("email") or _first_str(profile.get("emails")))
    phone = _first_str(profile.get("phone") or _first_str(profile.get("phones")))

    links_block = profile.get("links") or {}
    candidates = []
    if isinstance(links_block, dict):
        for k, v in links_block.items():
            if isinstance(v, str) and v.strip():
                candidates.append((k.lower(), v.strip()))
    for k, v in profile.items():
        if isinstance(v, str) and v.strip() and (k.endswith("_url") or k.endswith("_link")):
            candidates.append((k.lower(), v.strip()))
    for k, v in profile.items():
        if isinstance(v, str) and ("http://" in v or "https://" in v):
            candidates.append((k.lower(), v.strip()))
    linkedin = github = portfolio = website = None
    for k, url in candidates:
        low = f"{k} {url}".lower()
        if "linkedin.com" in low and not linkedin:
            linkedin = url
        elif "github.com" in low and not github:
            github = url
        elif any(s in low for s in ["portfolio", "personal site", "personal website"]) and not portfolio:
            portfolio = url
        elif not website and ("http://" in url or "https://" in url):
            website = url
    location = _first_str(profile.get("location"))

    return {
        "full_name": name,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "phone": phone,
        "linkedin": linkedin,
        "github": github,
        "portfolio": portfolio or website,
        "location": location,
    }

# ---------- field mapping ----------

FIELD_SYNONYMS = {
    "first_name":   ["first name", "given name", "forename"],
    "last_name":    ["last name", "surname", "family name"],
    "full_name":    ["name", "full name", "your name"],
    "email":        ["email", "e-mail", "email address"],
    "phone":        ["phone", "mobile", "cell", "telephone"],
    "linkedin":     ["linkedin", "linkedin url", "linkedin profile"],
    "github":       ["github", "github url", "github profile"],
    "portfolio":    ["portfolio", "website", "personal site", "url"],
    "location":     ["city", "current location", "address", "location"],
    "cover_letter": ["cover letter", "covering letter", "motivation", "statement"],
    "resume":       ["resume", "cv", "upload resume", "upload cv"],
}
KNOWN_KEYS: Set[str] = {k for k in FIELD_SYNONYMS.keys() if k not in ("resume",)}

def label_for(frame, el):
    try:
        lid = el.get_attribute("id")
        if lid:
            lbl = frame.query_selector(f'label[for="{lid}"]')
            if lbl:
                t = (lbl.inner_text() or "").strip()
                if t: return t
    except: pass
    try:
        return frame.evaluate("""(e)=>{
            let p = e.parentElement;
            while(p){
                if(p.tagName && p.tagName.toLowerCase()==='label'){ return p.innerText || ''; }
                p = p.parentElement;
            }
            return '';
        }""", el).strip()
    except: return ""

def guess_field_key(label_text: str, name_attr: str, aria_label: str, placeholder: str) -> Optional[str]:
    hay = " ".join(x for x in [
        (label_text or ""), (name_attr or ""), (aria_label or ""), (placeholder or "")
    ] if x).strip().lower()
    if not hay: return None
    for key, syns in FIELD_SYNONYMS.items():
        for s in syns + [key.replace("_"," ")]:
            if s in hay:
                return key
    return None

# ---------- resume text & Gemini ----------

def read_resume_text() -> str:
    if os.path.exists(RESUME_TXT):
        try: return Path(RESUME_TXT).read_text(encoding="utf-8", errors="ignore")
        except Exception: pass
    if os.path.exists(RESUME_PDF):
        try:
            from pypdf import PdfReader
            reader = PdfReader(RESUME_PDF)
            return "\n".join([(p.extract_text() or "") for p in reader.pages]).strip()
        except Exception: return ""
    return ""

def gemini_text(prompt: str) -> Optional[str]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key: return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        resp = model.generate_content(prompt)
        return (getattr(resp, "text", None) or "").strip()
    except Exception:
        return None

def infer_with_llm(field_key: str, resume_text: str) -> Optional[str]:
    if not resume_text.strip(): return None
    prompt = (
        f"You extract a single resume field.\n"
        f"Field: {field_key}\n"
        f"Resume text:\n-----\n{resume_text[:25000]}\n-----\n"
        f"Reply with ONLY the value. If unknown, reply: UNKNOWN"
    )
    out = gemini_text(prompt)
    if not out or out.upper() == "UNKNOWN": return None
    return out

# ---------- prompts ----------

def prompt_user_for(label: str, hint: str = "") -> Optional[str]:
    try:
        msg = f"[input needed] {label}"
        if hint: msg += f" ({hint})"
        msg += ": "
        val = input(msg).strip()
        return val or None
    except KeyboardInterrupt:
        return None

def prompt_user_choice(label: str, options: List[Tuple[str, str]]) -> Optional[str]:
    """Return chosen value (must match a listed option: by number or text)."""
    options = [(v or l, l) for v, l in options if (v or l)]
    if not options:
        print(f"[warn] No options found for: {label}")
        return None
    print(f"[choose] {label}")
    for i, (val, lab) in enumerate(options, 1):
        print(f"  {i}. {lab} [{val}]")
    while True:
        ans = input("Enter number or type an option (blank to skip): ").strip()
        if not ans: return None
        if ans.isdigit():
            idx = int(ans)
            if 1 <= idx <= len(options):
                return options[idx-1][0]
        low = ans.lower()
        # exact label/value first
        for v, lab in options:
            if lab.lower() == low or str(v).lower() == low:
                return v
        # contains
        matches = [v for v, lab in options if low in lab.lower() or low in str(v).lower()]
        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            print("Multiple matches; please be more specific.")
        else:
            print("No match; try again.")

def prompt_user_multi(label: str, options: List[Tuple[str, str]]) -> List[str]:
    options = [(v or l, l) for v, l in options if (v or l)]
    if not options:
        print(f"[warn] No options found for: {label}")
        return []
    print(f"[choose multiple] {label}")
    for i, (val, lab) in enumerate(options, 1):
        print(f"  {i}. {lab} [{val}]")
    print("Enter numbers (e.g., 1,3,5) or labels separated by commas. Blank to skip.")
    while True:
        ans = input("> ").strip()
        if not ans: return []
        picks = [x.strip() for x in re.split(r"[,\s]+", ans) if x.strip()]
        chosen_vals: List[str] = []
        ok = True
        for p in picks:
            if p.isdigit():
                idx = int(p)
                if 1 <= idx <= len(options):
                    chosen_vals.append(options[idx-1][0]); continue
                ok = False; break
            low = p.lower()
            exact = [v for v,l in options if l.lower()==low or str(v).lower()==low]
            if exact:
                chosen_vals.append(exact[0]); continue
            contains = [v for v,l in options if low in l.lower() or low in str(v).lower()]
            if len(contains)==1:
                chosen_vals.append(contains[0]); continue
            ok = False; break
        if ok:
            out = []
            for v in chosen_vals:
                if v not in out: out.append(v)
            return out
        print("Some items didn’t match uniquely; try again.")

def yes_no(prompt_text: str, default: bool=False) -> bool:
    default_str = "Y/n" if default else "y/N"
    ans = input(f"{prompt_text} [{default_str}] ").strip().lower()
    if not ans: return default
    return ans in ("y","yes")

# ---------- DOM / frames / widgets ----------

def _all_frames(page):
    frs = [page.main_frame]
    seen = {id(page.main_frame)}
    for fr in page.frames:
        if id(fr) not in seen:
            frs.append(fr)
    return frs

def collect_form_fields(page) -> List[Dict[str, Any]]:
    """
    Discover native inputs/selects/textareas, radios/checkboxes, and ARIA comboboxes (custom dropdowns).
    Emits:
      frame, el, widget ('input'|'textarea'|'select'|'combobox'|'checkbox'|'radio'),
      type, label, placeholder, name, aria, key, required, options, multiple, group_name
    """
    fields = []
    for frame in _all_frames(page):
        # Native
        for el in frame.query_selector_all("input, select, textarea"):
            try:
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                typ = (el.get_attribute("type") or tag or "").lower()
                lbl = label_for(frame, el) or ""
                ph  = el.get_attribute("placeholder") or ""
                name = el.get_attribute("name") or ""
                aria = el.get_attribute("aria-label") or ""
                key = guess_field_key(lbl, name, aria, ph)

                required = False
                try: required = bool(el.get_attribute("required"))
                except: pass
                try:
                    if not required:
                        required = (el.get_attribute("aria-required") == "true")
                except: pass
                if not required and "*" in (lbl or ""):
                    required = True

                widget = "input" if tag == "input" else tag
                options, multiple = [], False
                group_name = None

                if tag == "select":
                    widget = "select"
                    try: multiple = bool(el.get_attribute("multiple"))
                    except: pass
                    try:
                        for o in el.query_selector_all("option"):
                            v = o.get_attribute("value") or ""
                            lab = (o.inner_text() or "").strip() or v
                            options.append((v, lab))
                    except: pass

                # datalist
                if tag == "input" and el.get_attribute("list"):
                    dl_id = el.get_attribute("list")
                    dl = frame.query_selector(f"#{dl_id}")
                    if dl:
                        try:
                            for o in dl.query_selector_all("option"):
                                v = o.get_attribute("value") or ""
                                lab = (o.inner_text() or v).strip()
                                options.append((v, lab))
                            widget = "select"
                        except: pass

                if typ in ("checkbox", "radio"):
                    group_name = name
                    widget = typ

                fields.append({
                    "frame": frame, "el": el, "widget": widget, "type": typ,
                    "label": lbl, "placeholder": ph, "name": name, "aria": aria,
                    "key": key, "required": required, "options": options,
                    "multiple": multiple, "group_name": group_name
                })
            except Exception:
                continue

        # ARIA comboboxes (React-Select/Select2/custom)
        for el in frame.query_selector_all('[role="combobox"], [aria-haspopup="listbox"]'):
            try:
                if el.evaluate("e => ['input','textarea','select'].includes(e.tagName.toLowerCase())"):
                    continue  # already handled
            except Exception:
                pass
            try:
                lbl = label_for(frame, el) or (el.get_attribute("aria-label") or "")
                name = el.get_attribute("name") or ""
                ph   = el.get_attribute("placeholder") or ""
                aria = el.get_attribute("aria-label") or ""
                key  = guess_field_key(lbl, name, aria, ph)
                required = "*" in (lbl or "")
                fields.append({
                    "frame": frame, "el": el, "widget": "combobox", "type": "text",
                    "label": lbl, "placeholder": ph, "name": name, "aria": aria,
                    "key": key, "required": required, "options": [], "multiple": False,
                    "group_name": None
                })
            except Exception:
                continue

    return fields

# ---------- combobox helpers (open/read/select) ----------

def _scroll_and_focus(frame, el):
    try: el.scroll_into_view_if_needed(timeout=1500)
    except Exception: pass
    try: el.focus()
    except Exception:
        try: frame.evaluate("(e)=>e.focus&&e.focus()", el)
        except Exception: pass

def _open_menu(page, frame, el):
    _scroll_and_focus(frame, el)
    try: el.click(timeout=1500)
    except Exception:
        try: frame.evaluate("(e)=>{ (e.parentElement||e).click?.() }", el)
        except Exception: pass
    # some widgets open on key
    for key in ("Enter","ArrowDown"," "):
        try: el.press(key)
        except Exception: pass
    time.sleep(0.15)

def _visible_option_nodes(page):
    selectors = [
        '[role="listbox"] [role="option"]',
        '[role="option"]',
        'ul[role="listbox"] li',
        '.Select-menu-outer .Select-option',
        '[class*="menu"] [class*="option"]',
        '[data-value]'
    ]
    nodes = []
    for sel in selectors:
        try:
            loc = page.locator(sel)
            count = loc.count()
            for i in range(min(count, 200)):
                it = loc.nth(i)
                try:
                    if it.is_visible():
                        nodes.append(it)
                except Exception:
                    continue
        except Exception:
            continue
    return nodes

def open_combobox_and_get_options(page, frame, el) -> List[Tuple[str,str]]:
    _open_menu(page, frame, el)
    time.sleep(0.1)
    items = _visible_option_nodes(page)
    options = []
    for it in items:
        try:
            lab = it.inner_text().strip()
            if not lab: continue
            val = it.get_attribute("data-value") or it.get_attribute("value") or lab
            options.append((val, lab))
        except Exception:
            continue
    # de-dupe
    seen = set(); uniq=[]
    for v,l in options:
        k=(v,l)
        if k in seen: continue
        seen.add(k); uniq.append((v,l))
    return uniq

def select_in_combobox(page, frame, el, picks):
    """Strict selection by clicking a listed option. No free typing fallback."""
    if not isinstance(picks, list):
        picks = [picks]
    for pick in picks:
        _open_menu(page, frame, el)
        picked = False
        # try role=option name exact / contains
        escaped = re.escape(str(pick))
        try:
            loc = page.get_by_role("option", name=re.compile(escaped, re.I))
            if loc.count() > 0:
                loc.first.click()
                picked = True
        except Exception:
            pass
        if not picked:
            for it in _visible_option_nodes(page):
                try:
                    lab = it.inner_text().strip()
                    if not lab: continue
                    if str(pick).lower() == lab.lower() or str(pick).lower() in lab.lower():
                        it.click()
                        picked = True
                        break
                except Exception:
                    continue
        if not picked:
            print(f"[warn] Could not find option '{pick}' in dropdown; please try again interactively.")
        time.sleep(0.1)

# ---------- other helpers ----------

def upload_resume_if_possible(fields: List[Dict[str, Any]]):
    for f in fields:
        el, widget = f["el"], f["widget"]
        hay = " ".join([f.get("label",""), f.get("placeholder",""), f.get("name",""), f.get("aria","")]).lower()
        looks_like_resume = (widget == "input" and (f["type"] == "file")) or any(s in hay for s in ["resume", "cv"])
        if looks_like_resume and os.path.exists(RESUME_PDF):
            try: el.set_input_files(RESUME_PDF)
            except Exception: pass

def read_current_value(f: Dict[str, Any]) -> str:
    el, widget = f["el"], f["widget"]
    try:
        if widget == "select":
            return el.evaluate("e => e.value || ''") or ""
        return el.input_value()
    except Exception:
        try: return el.evaluate("e => e.value || ''") or ""
        except Exception: return ""

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
            el.fill("")
            el.type(str(val))
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

# ---------- job scraping + cover letter ----------

def scrape_job_text(page) -> Dict[str, str]:
    data = {"title":"", "company":"", "location":"", "body":""}
    try:
        data["title"] = (page.locator("h1, h2").first.inner_text() or "").strip()
    except Exception: pass
    for sel in ["[data-company]", ".company", ".organization", "header [itemprop='hiringOrganization']", "meta[name='og:site_name']"]:
        try:
            t = page.locator(sel).first.inner_text()
            if t and t.strip():
                data["company"] = t.strip(); break
        except Exception:
            continue
    if not data["company"]:
        try: data["company"] = (page.title() or "").split("|")[0].strip()
        except Exception: pass
    try:
        data["location"] = (page.locator(":text('Location') + *").first.inner_text() or "").strip()
    except Exception: pass
    try:
        data["body"] = (page.locator("main").inner_text() or "").strip()
    except Exception:
        try: data["body"] = (page.locator("body").inner_text() or "").strip()
        except Exception: data["body"] = ""
    return data

def draft_summary_and_letter(job: Dict[str,str], resume_text: str, contact: Dict[str,Any]) -> Tuple[str,str]:
    if not os.getenv("GEMINI_API_KEY") or not (job.get("body") and resume_text):
        summary = f"Role: {job.get('title','(unknown)')} | Company: {job.get('company','(unknown)')} | Location: {job.get('location','')}".strip()
        letter = f"Dear Hiring Team,\n\nI’m interested in the {job.get('title','')} role at {job.get('company','')}. My background aligns with the required skills and I’d love to contribute.\n\nBest,\n{contact.get('full_name') or (contact.get('first_name') or '') + ' ' + (contact.get('last_name') or '')}"
        return summary, letter
    prompt = (
        "You are assisting with a job application.\n"
        "First, write a concise 4–6 bullet summary of the role and company based only on the job page text.\n"
        "Second, write a short (120–160 words) tailored cover letter aligning the candidate's skills to the role.\n"
        "Use a confident but warm tone. No placeholders. No markdown.\n"
        "=== JOB PAGE TEXT ===\n"
        f"{job['body'][:25000]}\n"
        "=== CANDIDATE RESUME TEXT ===\n"
        f"{resume_text[:25000]}\n"
        "=== OUTPUT FORMAT ===\n"
        "SUMMARY:\n"
        "(bullets)\n"
        "COVER LETTER:\n"
        "(paragraphs)"
    )
    out = gemini_text(prompt) or ""
    summary, letter = "", ""
    if "COVER LETTER:" in out:
        parts = out.split("COVER LETTER:", 1)
        summary = parts[0].replace("SUMMARY:", "").strip()
        letter = parts[1].strip()
    else:
        summary = out.strip()
    if not letter:
        letter = f"Dear Hiring Team,\n\nI’m excited about the {job.get('title','')} role at {job.get('company','')}. I bring relevant skills and a strong interest in your mission.\n\nBest regards,\n{contact.get('full_name') or ''}"
    return summary, letter

# ---------- main ----------

def main():
    if not JOB_URL.startswith("http"):
        print("Please set JOB_URL to a real application link."); return
    if not os.path.exists(PARSED_JSON):
        print("parsed_resume.json not found."); return
    if not os.path.exists(RESUME_PDF):
        print(f"Resume PDF not found at {RESUME_PDF}."); return

    profile = load_profile()
    contact = extract_contact(profile)
    resume_text = read_resume_text()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        ctx = browser.new_context()
        page = ctx.new_page()

        print(f"[open] {JOB_URL}")
        try:
            page.goto(JOB_URL, timeout=60000)
        except PWTimeout:
            print("[error] page load timed out."); browser.close(); return

        page.wait_for_timeout(1200)

        # 1) Job summary + tailored cover letter
        job = scrape_job_text(page)
        summary, cover_letter = draft_summary_and_letter(job, resume_text, contact)

        print("\n=== JOB / COMPANY SUMMARY ===")
        print(summary)
        print("\n=== PROPOSED COVER LETTER ===")
        print(cover_letter)

        if not yes_no("\nApprove this cover letter and proceed to filling the form?", default=False):
            print("[abort] User declined to proceed."); browser.close(); return

        # 2) Parse form & collect fields; upload resume if possible
        fields = collect_form_fields(page)
        upload_resume_if_possible(fields)

        # 3) Plan values for known keys from JSON -> Gemini -> prompt
        planned_by_key: Dict[str, Any] = {}

        if any(f["key"] == "cover_letter" for f in fields):
            planned_by_key["cover_letter"] = cover_letter

        for f in fields:
            k = f["key"]
            if k in KNOWN_KEYS:
                v = contact.get(k)
                if k == "first_name" and not v and contact.get("full_name"):
                    v = (contact["full_name"].split() or [""])[0]
                if k == "last_name" and not v and contact.get("full_name"):
                    parts = contact["full_name"].split(); v = parts[-1] if len(parts)>1 else None
                if v: planned_by_key[k] = v

        missing_known = sorted([k for k in KNOWN_KEYS if (k not in planned_by_key)])
        if missing_known and resume_text:
            for k in missing_known:
                guess = infer_with_llm(k, resume_text)
                if guess: planned_by_key[k] = guess

        missing_known = sorted([k for k in KNOWN_KEYS if (k not in planned_by_key)])
        if missing_known:
            print("\n[manual] Provide missing contact fields:")
            for k in missing_known:
                hint = ""
                for f in fields:
                    if f["key"] == k:
                        hint = f["label"] or f["placeholder"] or f["name"] or f["aria"]; break
                v = prompt_user_for(k, hint)
                if v: planned_by_key[k] = v

        # 4) Ask for unknown questions: selects/combobox/radios/checkboxes shown; text only if required
        planned_by_field: Dict[Any, Any] = {}

        # Group radios/checkboxes
        groups: Dict[Tuple[Any,str], List[Dict[str,Any]]] = {}
        for f in fields:
            if f["widget"] in ("radio","checkbox") and f["group_name"]:
                groups.setdefault((f["frame"], f["group_name"]), []).append(f)

        def ask_for_field(f: Dict[str,Any]):
            widget = f["widget"]
            if widget == "select":
                label = f["label"] or f["placeholder"] or f["name"] or f["aria"] or "Select"
                opts = f["options"]
                if f["multiple"]:
                    picks = prompt_user_multi(label, opts)
                    if picks: planned_by_field[f["el"]] = picks
                else:
                    choice = prompt_user_choice(label, opts)
                    if choice: planned_by_field[f["el"]] = choice

            elif widget == "combobox":
                opts = open_combobox_and_get_options(page, f["frame"], f["el"])
                label = f["label"] or f["placeholder"] or f["name"] or f["aria"] or "Select"
                if opts:
                    choice = prompt_user_choice(label, opts)
                    if choice: planned_by_field[f["el"]] = choice
                else:
                    print(f"[warn] Could not read options for '{label}'. Try clicking it manually if this persists.")

            elif widget in ("radio","checkbox"):
                pass
            else:
                if f["required"]:
                    label = f["label"] or f["placeholder"] or f["name"] or f["aria"] or "Answer"
                    v = prompt_user_for(label)
                    if v: planned_by_field[f["el"]] = v

        # Required unknowns first + all selects/comboboxes
        for f in fields:
            if f["key"] in KNOWN_KEYS:
                continue
            if read_current_value(f):
                continue
            if f["required"] or f["widget"] in ("select","combobox") or f["widget"] in ("radio","checkbox"):
                ask_for_field(f)

        # Radio/checkbox groups
        for (frame_obj, grp_name), g in groups.items():
            if any(el in planned_by_field for el in [x["el"] for x in g]):
                continue
            label = g[0]["label"] or g[0]["aria"] or g[0]["name"] or "Choose"
            opt_pairs = []
            for item in g:
                val = item["el"].get_attribute("value") or ""
                lab = item["label"] or item["aria"] or val or "(option)"
                opt_pairs.append((val, lab))
            if g[0]["widget"] == "checkbox":
                chosen = prompt_user_multi(label, opt_pairs)
                for item in g:
                    v = item["el"].get_attribute("value") or ""
                    if v in chosen:
                        planned_by_field[item["el"]] = True
            else:
                val = prompt_user_choice(label, opt_pairs)
                if val:
                    for item in g:
                        v = item["el"].get_attribute("value") or ""
                        if v == val:
                            planned_by_field[item["el"]] = True

        # ----- Review before fill -----
        print("\n[review] Planned known fields:")
        for k in sorted(planned_by_key.keys()):
            shown = str(planned_by_key[k])
            if k == "cover_letter":
                shown = (shown[:120] + "…") if len(shown) > 120 else shown
            print(f"  - {k}: {shown}")
        if planned_by_field:
            print("\n[review] Planned answers to additional questions:")
            for f in fields:
                if f["el"] in planned_by_field:
                    label = f["label"] or f["placeholder"] or f["name"] or f["aria"] or "(unlabeled)"
                    print(f"  - {label}: {planned_by_field[f['el']]}")

        if not yes_no("\nProceed to fill the form now?", default=True):
            print("[abort] Stopped before filling."); browser.close(); return

        # ----- Fill everything -----
        for f in fields:
            k = f["key"]
            if k in KNOWN_KEYS and k in planned_by_key:
                fill_value_into_field(f, planned_by_key[k], page=page)
            elif f["el"] in planned_by_field:
                fill_value_into_field(f, planned_by_field[f["el"]], page=page)

        # 5) Re-check required fields; prompt again if needed
        remaining = []
        for f in fields:
            if not f["required"]: continue
            if read_current_value(f):
                continue
            if f["el"] in planned_by_field or (f["key"] in KNOWN_KEYS and f["key"] in planned_by_key):
                if not read_current_value(f):
                    remaining.append(f)
            else:
                remaining.append(f)

        if remaining:
            print("\n[recheck] Some required fields are still blank; please provide:")
            for f in remaining:
                if f["widget"] == "combobox":
                    opts = open_combobox_and_get_options(page, f["frame"], f["el"])
                    label = f["label"] or f["placeholder"] or f["name"] or f["aria"] or "Select"
                    if opts:
                        choice = prompt_user_choice(label, opts)
                        if choice: planned_by_field[f["el"]] = choice
                    else:
                        print(f"[warn] Still can't read options for '{label}'.")
                else:
                    ask_for_field(f)
            for f in remaining:
                if f["key"] in KNOWN_KEYS and f["key"] in planned_by_key:
                    fill_value_into_field(f, planned_by_key[f["key"]], page=page)
                elif f["el"] in planned_by_field:
                    fill_value_into_field(f, planned_by_field[f["el"]], page=page)

        # Final confirmation
        if not yes_no("\nReady to submit the application?", default=True):
            print("[abort] Submission cancelled."); browser.close(); return

        submitted = click_submit(page)
        if not submitted:
            print("[warn] Could not find a Submit/Apply button — saving screenshot anyway.")

        page.wait_for_timeout(2500)

        shot = os.path.join(SCREENSHOT_DIR, f"{uuid.uuid4()}.png")
        try:
            page.screenshot(path=shot, full_page=True)
            print(f"[ok] screenshot saved: {shot}")
        except Exception as e:
            print(f"[warn] failed to save screenshot: {e}")

        if success_indicator(page):
            print("[ok] looks like submission succeeded.")
        else:
            print("[info] could not confirm success. Check the screenshot.")

        browser.close()

if __name__ == "__main__":
    main()
