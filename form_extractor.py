#!/usr/bin/env python3
"""
extract_form_fields_hardcoded.py

Purpose:
  Visit a hardcoded URL and extract visible form input fields along with their
  questions/labels and options (if any: <select>, radio/checkbox groups, ARIA comboboxes).
  Save the results to a JSON file.

Setup:
  pip install playwright
  playwright install

Edit the JOB_URL constant below to your job page.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# =======================
# 🔧 CONFIG — EDIT THESE
# =======================
JOB_URL: str = "https://job-boards.greenhouse.io/capco/jobs/6924131"  # <- change to your job URL
OUTPUT_FILE: str = "form_fields.json"
HEADLESS: bool = True
TIMEOUT_MS: int = 30000
WAIT_MS: int = 1500
# =======================


def extract_fields_initial(page) -> List[Dict[str, Any]]:
    """
    First pass: tag each interesting element with a unique [data-extract-id],
    then return a list of 'field' dicts with best-effort questions and options
    (options are empty for comboboxes here; we'll try to open and read them later).
    """
    fields = page.evaluate(
        """
(() => {
  const CSSesc = (s) => s.replace(/["\\\\]/g, "\\\\$&");

  const isVisible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none") return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  const labelFor = (el) => {
    if (!el) return "";
    // label[for=id]
    if (el.id) {
      const lab = document.querySelector(`label[for="${CSSesc(el.id)}"]`);
      if (lab && isVisible(lab)) return lab.innerText.trim();
    }
    // wrapping label
    const wrap = el.closest("label");
    if (wrap && isVisible(wrap)) return wrap.innerText.trim();

    // aria-labelledby
    const ll = el.getAttribute("aria-labelledby");
    if (ll) {
      const text = ll.split(/\\s+/)
        .map(id => document.getElementById(id))
        .filter(Boolean)
        .map(n => n.innerText || n.textContent || "")
        .map(s => s.trim())
        .filter(Boolean)
        .join(" ");
      if (text) return text;
    }

    // aria-label
    const aria = el.getAttribute("aria-label");
    if (aria) return aria.trim();

    // fieldset > legend
    const fs = el.closest("fieldset");
    if (fs) {
      const leg = fs.querySelector("legend");
      if (leg && isVisible(leg)) return leg.innerText.trim();
    }

    // previous label sibling (common in some markup)
    let prev = el.previousElementSibling;
    for (let i = 0; i < 3 && prev; i++) {
      if (prev.tagName.toLowerCase() === "label" && isVisible(prev)) {
        return prev.innerText.trim();
      }
      prev = prev.previousElementSibling;
    }

    // fallback: placeholder or name/id
    return (el.getAttribute("placeholder") || el.getAttribute("name") || el.id || "").trim();
  };

  const legendFor = (el) => {
    const fs = el.closest("fieldset");
    if (!fs) return "";
    const leg = fs.querySelector("legend");
    if (leg && isVisible(leg)) return leg.innerText.trim();
    return "";
  };

  const addField = (acc, obj) => {
    const def = {
      kind: "",
      name: "",
      id: "",
      question: "",
      options: [],
      required: false,
      extract_id: ""
    };
    acc.push(Object.assign(def, obj));
  };

  const elements = Array.from(document.querySelectorAll('input, select, textarea, [role="combobox"]'));

  let radioHandled = new Set();
  let checkboxHandled = new Set();
  let counter = 0;
  const fields = [];

  for (const el of elements) {
    if (!isVisible(el)) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    const role = (el.getAttribute("role") || "").toLowerCase();

    // Assign a unique marker so we can find it later from Python
    const extractId = `fx-${++counter}`;
    el.setAttribute("data-extract-id", extractId);

    // Required heuristic
    const required = el.hasAttribute("required") || el.getAttribute("aria-required") === "true";

    if (tag === "input" && type === "radio") {
      const name = el.getAttribute("name") || "";
      if (radioHandled.has(name)) continue;
      radioHandled.add(name);

      const group = Array.from(document.querySelectorAll(`input[type="radio"][name="${CSSesc(name)}"]`));
      const opts = group.filter(isVisible).map(r => {
        let lbl = "";
        if (r.id) {
          const lf = document.querySelector(`label[for="${CSSesc(r.id)}"]`);
          if (lf && isVisible(lf)) lbl = lf.innerText.trim();
        }
        if (!lbl) {
          const wrap = r.closest("label");
          if (wrap && isVisible(wrap)) lbl = wrap.innerText.trim();
        }
        if (!lbl && r.parentElement) {
          lbl = (r.parentElement.innerText || r.parentElement.textContent || "").trim();
        }
        return { value: r.value || "", label: lbl };
      });

      addField(fields, {
        kind: "radio",
        name,
        id: el.id || "",
        question: legendFor(el) || labelFor(el) || name,
        options: opts,
        required,
        extract_id: extractId
      });
      continue;
    }

    if (tag === "input" && type === "checkbox") {
      const name = el.getAttribute("name") || el.id || "";
      const group = Array.from(document.querySelectorAll(`input[type="checkbox"][name="${CSSesc(name)}"]`)).filter(isVisible);

      if (group.length > 1) {
        if (checkboxHandled.has(name)) continue;
        checkboxHandled.add(name);

        const opts = group.map(r => {
          let lbl = "";
          if (r.id) {
            const lf = document.querySelector(`label[for="${CSSesc(r.id)}"]`);
            if (lf && isVisible(lf)) lbl = lf.innerText.trim();
          }
          if (!lbl) {
            const wrap = r.closest("label");
            if (wrap && isVisible(wrap)) lbl = wrap.innerText.trim();
          }
          if (!lbl && r.parentElement) {
            lbl = (r.parentElement.innerText || r.parentElement.textContent || "").trim();
          }
          return { value: r.value || "on", label: lbl };
        });

        addField(fields, {
          kind: "checkbox-group",
          name,
          id: "",
          question: legendFor(el) || labelFor(el) || name,
          options: opts,
          required,
          extract_id: extractId
        });
      } else {
        // Single checkbox
        addField(fields, {
          kind: "checkbox",
          name,
          id: el.id || "",
          question: labelFor(el) || name,
          options: [{ value: "checked", label: "Yes" }],
          required,
          extract_id: extractId
        });
      }
      continue;
    }

    if (tag === "select") {
      const opts = Array.from(el.querySelectorAll("option"))
        .filter(o => !o.disabled)
        .map(o => ({ value: o.value, label: (o.textContent || "").trim() }));

      addField(fields, {
        kind: "select",
        name: el.getAttribute("name") || "",
        id: el.id || "",
        question: labelFor(el) || el.getAttribute("name") || el.id || "",
        options: opts,
        required,
        extract_id: extractId
      });
      continue;
    }

    if (role === "combobox") {
      addField(fields, {
        kind: "combobox",
        name: el.getAttribute("name") || "",
        id: el.id || "",
        question: labelFor(el) || el.getAttribute("aria-label") || el.getAttribute("placeholder") || "",
        options: [],
        required,
        extract_id: extractId
      });
      continue;
    }

    if (tag === "textarea") {
      addField(fields, {
        kind: "textarea",
        name: el.getAttribute("name") || "",
        id: el.id || "",
        question: labelFor(el) || el.getAttribute("placeholder") || el.getAttribute("name") || "",
        options: [],
        required,
        extract_id: extractId
      });
      continue;
    }

    if (tag === "input") {
      if (["hidden","submit","button","image","file","reset"].includes(type)) continue;
      addField(fields, {
        kind: type || "text",
        name: el.getAttribute("name") || "",
        id: el.id || "",
        question: labelFor(el) || el.getAttribute("placeholder") || el.getAttribute("name") || "",
        options: [],
        required,
        extract_id: extractId
      });
      continue;
    }
  }

  return fields;
})();
        """
    )
    return fields


def try_open_combobox_and_collect(page, extract_id: str) -> List[Dict[str, str]]:
    """
    Best-effort: click a combobox, wait for its listbox, collect options.
    """
    selector = f'[data-extract-id="{extract_id}"]'
    locator = page.locator(selector)
    if locator.count() == 0:
        return []

    # Try to open the combo
    try:
        locator.click(timeout=1500)
    except PlaywrightTimeoutError:
        return []

    # Heuristics to find options
    options = []

    # 1) If combobox has aria-controls pointing to the listbox
    try:
        aria_controls = locator.get_attribute("aria-controls")
    except Exception:
        aria_controls = None

    if aria_controls:
        try:
            listbox = page.locator(f'#{aria_controls}')
            listbox.wait_for(state="visible", timeout=1500)
            option_els = listbox.locator('[role="option"]')
            texts = option_els.all_text_contents()
            count = option_els.count()
            values = []
            for i in range(count):
                el = option_els.nth(i)
                val = el.get_attribute("data-value") or el.get_attribute("value") or ""
                values.append(val or "")
            for i in range(count):
                t = texts[i].strip() if i < len(texts) else ""
                v = values[i].strip() if i < len(values) else ""
                options.append({"value": v, "label": t})
        except PlaywrightTimeoutError:
            pass

    # 2) Generic fallback: first visible listbox
    if not options:
        try:
            listbox = page.locator('[role="listbox"]')
            listbox.wait_for(state="visible", timeout=1500)
            option_els = listbox.locator('[role="option"]')
            texts = option_els.all_text_contents()
            count = option_els.count()
            for i in range(count):
                el = option_els.nth(i)
                val = el.get_attribute("data-value") or el.get_attribute("value") or ""
                label = texts[i].strip() if i < len(texts) else (el.inner_text().strip() if el else "")
                options.append({"value": (val or "").strip(), "label": label})
        except PlaywrightTimeoutError:
            pass

    # 3) Close the combo by pressing Escape (best-effort)
    try:
        locator.press("Escape", timeout=500)
    except Exception:
        pass

    # Deduplicate options
    seen = set()
    deduped = []
    for opt in options:
        key = (opt.get("value",""), opt.get("label",""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(opt)

    return deduped


def extract_all_fields(page) -> List[Dict[str, Any]]:
    fields = extract_fields_initial(page)

    # For combobox fields, try to collect options by opening them
    for f in fields:
        if f.get("kind") == "combobox":
            opts = try_open_combobox_and_collect(page, f.get("extract_id", ""))
            if opts:
                f["options"] = opts

    # Strip internal marker before returning
    for f in fields:
        f.pop("extract_id", None)
    return fields


def main():
    out_path = Path(OUTPUT_FILE)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(JOB_URL, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
        except PlaywrightTimeoutError:
            # Proceed even if navigation times out—best effort scrape
            pass

        # Small wait for dynamic UIs
        if WAIT_MS > 0:
            page.wait_for_timeout(WAIT_MS)

        # Try to expand obvious accordions to reveal hidden inputs (best-effort)
        try:
            for sel in ["details > summary", "[aria-expanded='false']",
                        "[data-accordion-button]", "[data-testid='accordion-button']"]:
                loc = page.locator(sel)
                try:
                    count = loc.count()
                except Exception:
                    count = 0
                for i in range(count):
                    try:
                        loc.nth(i).click(timeout=250)
                    except Exception:
                        continue
        except Exception:
            pass

        fields = extract_all_fields(page)

        # Save file
        out_path.write_text(json.dumps({"url": JOB_URL, "fields": fields}, indent=2), encoding="utf-8")

        print(f"Wrote {len(fields)} field(s) to {out_path.resolve()}")
        print(json.dumps({"url": JOB_URL, "fields": fields}, indent=2))

        context.close()
        browser.close()


if __name__ == "__main__":
    main()
