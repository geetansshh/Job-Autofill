#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Form extractor -> LLM-friendly JSON for Lever/Greenhouse-like forms.

Outputs: form_fields_cleaned.json with:
  - question (label text)
  - question_id (id, name, or radio/checkbox group name)
  - input_type (text, textarea, select, combobox, radio, checkbox, file)
  - required (bool)
  - options: [{label, value}] if present
"""

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from pathlib import Path
import json, re, time
from output_config import OutputPaths

JOB_URL = "https://job-boards.greenhouse.io/gomotive/jobs/8137073002?gh_src=my.greenhouse.search"
OUT_FILE = OutputPaths.FORM_FIELDS_CLEANED  # Now using centralized output path

# ------------- helpers -------------

def _label_js():
    return r"""
(e => {
  const CSSesc = s => s?.replace(/[\[\].#]/g, m => '\\'+m) || "";
  const isVisible = (x) => !!x && !!(x.offsetParent || x.getClientRects().length);
  const byFor = (x) => x.id ? document.querySelector(`label[for="${CSSesc(x.id)}"]`) : null;
  const wrap = e.closest("label");
  const lab1 = byFor(e);
  if (lab1 && isVisible(lab1)) return lab1.innerText.trim();
  if (wrap && isVisible(wrap)) return wrap.innerText.trim();
  const ll = e.getAttribute("aria-labelledby");
  if (ll) {
    const parts = ll.split(/\s+/).map(id => document.getElementById(id)).filter(Boolean);
    const txt = parts.map(n => n?.innerText?.trim()).filter(Boolean).join(" ");
    if (txt) return txt;
  }
  const la = e.getAttribute("aria-label");
  if (la) return la.trim();
  const ph = e.getAttribute("placeholder") || e.getAttribute("aria-placeholder");
  if (ph) return ph.trim();
  // Try nearest legend
  const lg = e.closest("fieldset")?.querySelector("legend");
  if (lg) return lg.innerText.trim();
  // Nearest visible label-ish element
  const cand = e.closest("div,section,li,td")?.querySelector("label,h1,h2,h3,h4,span[role='heading']");
  return cand?.innerText?.trim() || "";
})
"""

def click_apply_like_things(page):
    # Lever/GH pages often need an initial click to reveal the form
    candidates = [
        "button:has-text('Apply')",
        "button:has-text('Continue')",
        "a:has-text('Apply')",
        "a:has-text('Continue')",
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel)
            if loc.first.is_visible():
                loc.first.click(timeout=2000)
                return True
        except Exception:
            pass
    return False

# ------------- extraction -------------

def _append_select_options(el, rec):
    try:
        opts = el.locator("option")
        n = opts.count()
        items = []
        for i in range(n):
            o = opts.nth(i)
            try:
                label = o.inner_text().strip()
            except Exception:
                label = ""
            val = o.get_attribute("value") or label
            items.append({"label": label, "value": val})
        rec["options"] = items
    except Exception:
        pass

def _radio_checkbox_groups(frame, fields):
    seen = set()
    for typ in ("radio", "checkbox"):
        inputs = frame.locator(f"input[type='{typ}']")
        n = inputs.count()
        for i in range(n):
            try:
                el = inputs.nth(i)
                if not el.is_visible():
                    continue
                name = el.get_attribute("name") or ""
                if not name or (typ, name) in seen:
                    continue
                # group peers
                group = frame.locator(f"input[type='{typ}'][name='{name}']")
                m = group.count()
                # label for the group
                try:
                    q = el.evaluate(_label_js())
                    if not q:
                        # legend of nearest fieldset
                        q = el.evaluate("(e)=>e.closest('fieldset')?.querySelector('legend')?.innerText?.trim()||''")
                except Exception:
                    q = ""

                options = []
                for j in range(m):
                    peer = group.nth(j)
                    try:
                        lab = peer.evaluate(_label_js())
                        if not lab:
                            # try sibling text
                            lab = peer.evaluate("(e)=>e.closest('label')?.innerText?.trim()||e.parentElement?.innerText?.trim()||''")
                    except Exception:
                        lab = ""
                    val = peer.get_attribute("value") or lab or ""
                    options.append({"label": lab, "value": val})
                fields.append({"kind": typ, "group": name, "question": q, "options": options, "required": False})
                seen.add((typ, name))
            except Exception:
                continue

def _aria_comboboxes(frame, fields):
    # Try to open combobox and capture current option list
    cbs = frame.locator("[role='combobox']")
    n = cbs.count()
    for i in range(n):
        cb = cbs.nth(i)
        try:
            if not cb.is_visible():
                continue
            q = cb.evaluate(_label_js())
            opts = []
            try:
                cb.click(timeout=1000)
                time.sleep(0.2)
                options = frame.locator("[role='option']")
                k = min(options.count(), 50)
                for j in range(k):
                    o = options.nth(j)
                    label = o.inner_text().strip()
                    value = o.get_attribute("data-value") or label
                    opts.append({"label": label, "value": value})
            except Exception:
                pass
            # Try closing
            try:
                frame.page.keyboard.press("Escape")
            except Exception:
                pass
            fields.append({"kind": "combobox", "question": q, "options": opts, "required": False})
        except Exception:
            continue

def extract_in_frame(frame):
    fields = []

    # Native inputs/textarea/select
    for sel in ("input", "textarea", "select"):
        els = frame.locator(sel)
        n = els.count()
        for i in range(n):
            el = els.nth(i)
            try:
                if not el.is_visible():
                    continue
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                itype = ""
                if tag == "input":
                    itype = (el.get_attribute("type") or "").lower() or "text"
                q = el.evaluate(_label_js())
                rec = {
                    "kind": tag if tag != "input" else itype,
                    "id": el.get_attribute("id") or "",
                    "name": el.get_attribute("name") or "",
                    "question": q,
                    "options": [],
                    "required": bool(el.get_attribute("required"))
                }
                if tag == "select":
                    _append_select_options(el, rec)
                elif tag == "input" and itype in ("radio", "checkbox"):
                    # skip here; group handler will take care
                    continue
                fields.append(rec)
            except Exception:
                continue

    # Radios/checkboxes grouped by name
    _radio_checkbox_groups(frame, fields)

    # ARIA comboboxes
    _aria_comboboxes(frame, fields)

    return fields

def extract_all_frames(page):
    all_fields = []
    # main frame first
    try:
        all_fields += extract_in_frame(page.main_frame)
    except Exception:
        pass
    # then every child frame
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        try:
            all_fields += extract_in_frame(fr)
        except Exception:
            continue
    return all_fields

# ------------- cleaning -------------

def clean_fields_llm(fields):
    """
    Return an LLM-friendly list of fields with keys:
      - question
      - question_id
      - input_type
      - required
      - options: [{label, value}]
    """
    cleaned = []
    for f in fields or []:
        # Prefer extracted label, but do NOT drop fields if missing.
        question = (f.get("question") or "").strip()
        qid = f.get("group") or f.get("id") or f.get("name") or ""
        if not question:
            # Fallback so unlabeled inputs (common in custom widgets) are included.
            question = qid or "(no label)"
        input_type = f.get("kind") or f.get("type") or ""
        required = bool(f.get("required", False))
        opts = []
        for o in (f.get("options") or []):
            if isinstance(o, dict):
                label = o.get("label") if o.get("label") is not None else (o.get("text") or o.get("value"))
                value = o.get("value")
            else:
                label = str(o)
                value = None
            opts.append({"label": label, "value": value})
        cleaned.append({
            "question": question,
            "question_id": qid,
            "input_type": input_type,
            "required": required,
            "options": opts
        })
    return cleaned

def save_clean(out_fields, url, out_path=OUT_FILE):
    payload = {"url": url, "fields": out_fields}
    Path(out_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(out_fields)} cleaned fields to {Path(out_path).resolve()}")

# ------------- main -------------

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        print("Navigating to:", JOB_URL)
        page.goto(JOB_URL, wait_until="domcontentloaded")

        click_apply_like_things(page)
        page.wait_for_timeout(1500)

        fields = extract_all_frames(page)
        cleaned = clean_fields_llm(fields)
        save_clean(cleaned, JOB_URL, OUT_FILE)

        # optional: quick preview
        print(json.dumps({"url": JOB_URL, "fields": cleaned[:5]}, indent=2, ensure_ascii=False))

        browser.close()

if __name__ == "__main__":
    run()
