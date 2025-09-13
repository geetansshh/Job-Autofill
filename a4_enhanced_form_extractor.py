#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Enhanced Form Extractor - Combines technical DOM extraction with LLM analysis.

This approach uses:
1. Technical extraction (original DOM inspection method)
2. Markdown content extraction of visible page elements
3. LLM analysis to reconcile both sources and find missing fields
4. Merged results for comprehensive field detection

Outputs: form_fields_enhanced.json with complete field list
"""

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from pathlib import Path
import json, re, time
import google.generativeai as genai
from output_config import OutputPaths
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

# Configuration
JOB_URL = "https://job-boards.greenhouse.io/gomotive/jobs/8137073002?gh_src=my.greenhouse.search"
OUT_FILE = OutputPaths.FORM_FIELDS_ENHANCED
MODEL_NAME = "gemini-2.5-flash-lite"

@dataclass
class ExtractedField:
    """Standardized field representation"""
    question: str
    question_id: str
    input_type: str
    required: bool
    options: List[Dict[str, str]]
    source: str  # "technical", "llm", "merged"
    confidence: float = 1.0

# ============================================================================
# PART 1: TECHNICAL EXTRACTION (Original Approach)
# ============================================================================

def _label_js():
    """JavaScript to extract labels from form elements"""
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
    """Click Apply/Continue buttons to reveal forms"""
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

def _append_select_options(el, rec):
    """Extract options from select elements"""
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
    """Extract radio/checkbox groups"""
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
                fields.append({"kind": typ, "group": name, "question": q, "options": options, "required": False, "source": "technical"})
                seen.add((typ, name))
            except Exception:
                continue

def _aria_comboboxes(frame, fields):
    """Extract ARIA combobox elements"""
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
            fields.append({"kind": "combobox", "question": q, "options": opts, "required": False, "source": "technical"})
        except Exception:
            continue

def extract_technical_fields(frame):
    """Technical DOM extraction (original approach)"""
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
                    "required": bool(el.get_attribute("required")),
                    "source": "technical"
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

def extract_all_technical_fields(page):
    """Extract from all frames using technical approach"""
    all_fields = []
    # main frame first
    try:
        all_fields += extract_technical_fields(page.main_frame)
    except Exception:
        pass
    # then every child frame
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        try:
            all_fields += extract_technical_fields(fr)
        except Exception:
            continue
    return all_fields

# ============================================================================
# PART 2: MARKDOWN CONTENT EXTRACTION
# ============================================================================

def extract_page_markdown(page):
    """Extract visible form-related content as markdown"""
    markdown_content = page.evaluate("""() => {
        // Helper function to check if element is visible
        const isVisible = (el) => {
            if (!el || !el.offsetParent) return false;
            const style = window.getComputedStyle(el);
            return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
        };
        
        // Helper to get clean text
        const getCleanText = (el) => {
            if (!isVisible(el)) return '';
            let text = el.innerText || el.textContent || '';
            return text.replace(/\\s+/g, ' ').trim();
        };
        
        let markdown = [];
        
        // Extract form structure indicators
        const forms = document.querySelectorAll('form, [role="form"], .form, .application-form');
        for (let form of forms) {
            if (!isVisible(form)) continue;
            markdown.push('\\n## FORM SECTION\\n');
            
            // Get all form-related elements within this form
            const formElements = form.querySelectorAll(`
                label, legend, fieldset,
                input, select, textarea,
                [role="combobox"], [role="listbox"], [role="option"],
                .field, .form-field, .input-field,
                .question, .form-question,
                h1, h2, h3, h4, h5, h6,
                p, div, span
            `);
            
            for (let el of formElements) {
                const text = getCleanText(el);
                if (!text || text.length < 2) continue;
                
                const tagName = el.tagName.toLowerCase();
                const className = el.className || '';
                const role = el.getAttribute('role') || '';
                
                // Format based on element type
                if (tagName.match(/^h[1-6]$/)) {
                    markdown.push(`\\n### ${text}\\n`);
                } else if (tagName === 'label' || el.hasAttribute('for')) {
                    markdown.push(`**Label:** ${text}`);
                } else if (tagName === 'legend') {
                    markdown.push(`**Legend:** ${text}`);
                } else if (tagName === 'input') {
                    const type = el.getAttribute('type') || 'text';
                    const placeholder = el.getAttribute('placeholder') || '';
                    const required = el.hasAttribute('required') ? ' (Required)' : '';
                    markdown.push(`**Input [${type}]:** ${placeholder || text}${required}`);
                } else if (tagName === 'select') {
                    const options = Array.from(el.querySelectorAll('option'))
                        .map(opt => opt.textContent?.trim())
                        .filter(opt => opt && opt !== 'Select...' && opt !== 'Choose...')
                        .slice(0, 10); // Limit options
                    const required = el.hasAttribute('required') ? ' (Required)' : '';
                    markdown.push(`**Select${required}:** ${text}`);
                    if (options.length > 0) {
                        markdown.push(`  Options: ${options.join(', ')}`);
                    }
                } else if (tagName === 'textarea') {
                    const placeholder = el.getAttribute('placeholder') || '';
                    const required = el.hasAttribute('required') ? ' (Required)' : '';
                    markdown.push(`**Textarea${required}:** ${placeholder || text}`);
                } else if (role === 'combobox' || className.includes('select') || className.includes('dropdown')) {
                    markdown.push(`**Dropdown:** ${text}`);
                } else if (className.includes('field') || className.includes('question') || className.includes('form')) {
                    markdown.push(`**Field:** ${text}`);
                } else if (text.length > 5 && text.includes('?')) {
                    markdown.push(`**Question:** ${text}`);
                } else if (text.match(/^[A-Z][a-z]+ [A-Z][a-z]+:?$/)) {
                    markdown.push(`**Field Label:** ${text}`);
                } else {
                    // Generic text that might be relevant
                    if (text.length < 100) {
                        markdown.push(`${text}`);
                    }
                }
            }
        }
        
        // If no forms found, scan the entire page for form-like content
        if (markdown.length === 0) {
            markdown.push('\\n## PAGE CONTENT SCAN\\n');
            const allElements = document.querySelectorAll(`
                label, input, select, textarea,
                [role="combobox"], [class*="field"], [class*="form"],
                h1, h2, h3, h4, h5, h6
            `);
            
            for (let el of allElements) {
                const text = getCleanText(el);
                if (!text || text.length < 2) continue;
                markdown.push(text);
            }
        }
        
        return markdown.join('\\n');
    }""")
    
    return markdown_content

# ============================================================================
# PART 3: LLM ANALYSIS AND RECONCILIATION
# ============================================================================

def setup_gemini():
    """Setup Gemini API"""
    api_key = "AIzaSyAJrmvM10sV7GxgzAwApFtGtR3ht6l3fY0"  # Your API key
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not found")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(MODEL_NAME)

def analyze_with_llm(technical_fields, markdown_content):
    """Use LLM to analyze both sources and find missing fields"""
    
    model = setup_gemini()
    
    prompt = f"""
You are an expert at analyzing job application forms. I have two sources of information about a form:

1. TECHNICAL EXTRACTION (from DOM inspection):
{json.dumps(technical_fields, indent=2)}

2. VISIBLE PAGE CONTENT (markdown format):
```
{markdown_content}
```

Your task:
1. Identify ALL form fields mentioned in the visible content
2. Compare with the technical extraction to find missing fields
3. For each field (both existing and missing), determine:
   - Question text (what the user sees)
   - Input type (text, email, phone, select, textarea, radio, checkbox, file, etc.)
   - Whether it appears required (look for asterisks *, "required", "mandatory")
   - Possible options if it's a selection field
   - A unique ID/name for the field

Return ONLY a JSON array with this structure:
[
  {{
    "question": "First Name",
    "question_id": "first_name",
    "input_type": "text", 
    "required": true,
    "options": [],
    "source": "technical|llm|merged",
    "confidence": 0.95
  }}
]

Rules:
- Include ALL fields from technical extraction (mark as "technical")
- Add any missing fields found in markdown (mark as "llm") 
- If a field exists in both but with better info from one source, merge them (mark as "merged")
- Confidence: 1.0 for technical fields, 0.7-0.9 for LLM-inferred fields
- Be conservative - only add fields you're confident about
- Common field types: first_name, last_name, email, phone, resume_upload, cover_letter, experience_years, etc.
"""

    try:
        response = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.1,
                "response_mime_type": "application/json"
            }
        )
        
        result = json.loads(response.text)
        return result if isinstance(result, list) else []
        
    except Exception as e:
        print(f"LLM analysis failed: {e}")
        # Fallback: return technical fields in correct format
        fallback = []
        for field in technical_fields:
            fallback.append({
                "question": field.get("question", ""),
                "question_id": field.get("group") or field.get("id") or field.get("name") or "",
                "input_type": field.get("kind", "text"),
                "required": field.get("required", False),
                "options": field.get("options", []),
                "source": "technical",
                "confidence": 1.0
            })
        return fallback

# ============================================================================
# PART 4: ENHANCED EXTRACTION ORCHESTRATOR
# ============================================================================

def extract_enhanced_form_fields(page):
    """Main function combining technical + LLM extraction"""
    print("🔍 Starting enhanced form field extraction...")
    
    # Step 1: Technical extraction
    print("📋 Performing technical DOM extraction...")
    technical_fields = extract_all_technical_fields(page)
    print(f"   Found {len(technical_fields)} fields via technical extraction")
    
    # Step 2: Extract page content as markdown
    print("📄 Extracting visible page content...")
    markdown_content = extract_page_markdown(page)
    print(f"   Extracted {len(markdown_content)} characters of content")
    
    # Step 3: LLM analysis
    print("🤖 Analyzing with LLM...")
    enhanced_fields = analyze_with_llm(technical_fields, markdown_content)
    print(f"   LLM found {len(enhanced_fields)} total fields")
    
    # Step 4: Clean and standardize
    cleaned_fields = []
    for field in enhanced_fields:
        cleaned_field = {
            "question": (field.get("question") or "").strip() or field.get("question_id", "Unknown"),
            "question_id": field.get("question_id", "").strip(),
            "input_type": field.get("input_type", "text"),
            "required": bool(field.get("required", False)),
            "options": field.get("options", []),
            "source": field.get("source", "unknown"),
            "confidence": float(field.get("confidence", 0.5))
        }
        
        if cleaned_field["question_id"]:  # Only keep fields with IDs
            cleaned_fields.append(cleaned_field)
    
    print(f"✅ Final result: {len(cleaned_fields)} cleaned fields")
    return cleaned_fields, markdown_content

def save_enhanced_results(fields, markdown_content, url):
    """Save comprehensive results including debug info"""
    
    # Main output
    payload = {
        "url": url,
        "extraction_method": "enhanced_technical_plus_llm",
        "total_fields": len(fields),
        "fields": fields,
        "metadata": {
            "technical_fields": len([f for f in fields if f["source"] == "technical"]),
            "llm_fields": len([f for f in fields if f["source"] == "llm"]),
            "merged_fields": len([f for f in fields if f["source"] == "merged"]),
            "avg_confidence": sum(f["confidence"] for f in fields) / len(fields) if fields else 0
        }
    }
    
    OUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"💾 Saved enhanced extraction to: {OUT_FILE}")
    
    # Debug output with markdown content
    debug_file = OUT_FILE.parent / "form_extraction_debug.json"
    debug_payload = {
        "url": url,
        "extracted_markdown": markdown_content,
        "field_breakdown": {
            "technical": [f for f in fields if f["source"] == "technical"],
            "llm": [f for f in fields if f["source"] == "llm"],
            "merged": [f for f in fields if f["source"] == "merged"]
        }
    }
    debug_file.write_text(json.dumps(debug_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"🐛 Saved debug info to: {debug_file}")

# ============================================================================
# MAIN EXECUTION
# ============================================================================

def run_enhanced_extraction():
    """Main function to run the enhanced extraction process"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        print(f"🌐 Navigating to: {JOB_URL}")
        page.goto(JOB_URL, wait_until="domcontentloaded")

        # Try to reveal the form
        print("🖱️  Attempting to click Apply/Continue buttons...")
        click_apply_like_things(page)
        page.wait_for_timeout(2000)

        # Enhanced extraction
        fields, markdown_content = extract_enhanced_form_fields(page)
        
        # Save results
        save_enhanced_results(fields, markdown_content, JOB_URL)

        # Quick preview
        print("\n📊 EXTRACTION SUMMARY:")
        print(f"   Total fields: {len(fields)}")
        for source in ["technical", "llm", "merged"]:
            count = len([f for f in fields if f["source"] == source])
            if count > 0:
                print(f"   {source.title()} fields: {count}")
        
        print(f"\n🔍 First 3 fields preview:")
        for i, field in enumerate(fields[:3]):
            print(f"   {i+1}. {field['question']} [{field['input_type']}] ({'required' if field['required'] else 'optional'}) - {field['source']}")

        browser.close()

if __name__ == "__main__":
    run_enhanced_extraction()