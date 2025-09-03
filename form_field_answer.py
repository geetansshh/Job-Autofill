# i have these form fileds and i want to answer the questions in this form using my parsed resume file there can be three type of questions fitrst , the question that can be answered directly from resume like first name ,last name etc another can be questions that can be indirectly answered from resume for example do you know selenium? so if by reading the rseume you feel that no experince of selenium another tyoe can be perosnal questiosn that can be answered directly or indirectly using resume for example are you ok with wfh, are you ok wiht 2 year bond , what is your current ctc, what is your expected ctc stc now these can questions can subtype into 2 diffrent categories the first the one without options then the llm can answer freely according to my resume and teh questiosn type , the other type can be the one which has options so the llm has to choose the closes option according to its knowledge of my resume the llm has to skip perosnal questions orany questiosn that coudnt be answered so give me a simple script for that i am using a gemini api key
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generalized resume→form filler using Gemini (no argparse, hard-coded paths).
- Works with any form schema (normalizes common field shapes).
- Accepts resume as JSON (any structure) or plain text.
- Strong system prompt enforces: fill only from resume, choose only provided options,
  answer skill questions Yes/No (not Skip), and skip personal/preference fields.
- Local post-check also skips personal/preference questions for extra safety.
- Writes: filled_answers.json, skipped_fields.json

Setup:
  pip install google-generativeai
  export GEMINI_API_KEY=YOUR_KEY   # or GOOGLE_API_KEY

Run:
  python fill_form_from_resume_general.py
"""

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# ========================= HARD-CODED CONFIG =========================
FORM_PATH = "form_fields.json"          # <-- change this if needed
RESUME_PATH = "parsed_resume.json"      # or a .txt resume
MODEL_NAME = "gemini-2.5-flash-lite"         # or "gemini-1.5-pro"
ANSWERS_OUT = "filled_answers.json"
SKIPPED_OUT = "skipped_fields.json"
DRY_RUN = False                         # True -> preview prompt payload only, no API call
# ====================================================================

# ---------------------------- Prompt (POWERFUL & GENERAL) ----------------------------

SYSTEM_INSTRUCTION = """
You are an expert ATS agent filling job application forms from a single source of truth: the candidate’s resume.

CORE MISSION
Fill each field ONLY with information grounded in the resume. If a field is personal/preference (e.g., CTC, expected salary, current salary, package, negotiable, notice period, last working day, joining time, buyout, WFH/WFO/Hybrid, shifts, relocation/preferred location, travel willingness, gender, age, DOB, marital status, bonds/service agreements, NDA/non-compete, visa status unless stated), SKIP it. If the information is not present in the resume, SKIP it.

IMPORTANT RULES
1) Evidence policy:
   - “YES” for a skill/tech/tool only when the resume explicitly shows experience, proficiency, coursework, projects, or achievements with that item (or clear synonyms).
   - “NO” for a skill/tech/tool when the resume contains no evidence at all. Do NOT answer “Skip” in that case unless the question is ambiguous or not about skills/experience.
   - Never invent dates, salaries, IDs, or options.
   - If the resume has partial/conflicting info, SKIP unless the field’s options clearly contain a best-fit label.

2) Options policy:
   - If a field has options, choose ONLY from the provided option labels (verbatim).
   - For Yes/No options, follow the evidence policy above.
   - For ranges/buckets (e.g., years of experience), pick the closest matching label based on the resume’s content. If unclear, SKIP.
   - For multi-select fields: return a list with zero or more labels (labels MUST come from the provided options).

3) Personal/Preference questions (ALWAYS SKIP unless explicitly stated in resume):
   - Compensation (CTC, current/expected salary, package, negotiable)
   - Notice period, last working day, joining time, buyout
   - Work mode (WFH, WFO, Hybrid), shifts, relocation preference, preferred location, travel willingness
   - Gender, age, DOB, marital status
   - Bonds/service agreements/clauses, NDA/non-compete
   - Visa/immigration status unless clearly present in resume
   - Any other preference not clearly stated in the resume

4) Output format:
   - Return ONLY valid JSON with this exact structure:
     {
       "answers": { "<field_id>": <string | number | boolean | list-of-strings> },
       "skipped": [ { "id": "<field_id>", "question": "<original question>", "reason": "<short reason>" } ]
     }
   - No extra keys, no commentary, no markdown.

5) Safety/grounding:
   - If a value can be derived but requires standard formatting (e.g., first/last name, city from “City, State”), provide a clean, concise value.
   - If a field is a free-text question, answer concisely using resume facts only.
   - Never include your reasoning in the output. Output JSON only.

INTERPRETATION NOTES
- Treat synonyms/near matches sensibly (e.g., “JS”→“JavaScript”, “Py”→“Python”). “Web automation” does NOT imply “Selenium” unless Selenium is explicitly present.
- If a field asks “Do you know <X>?” and <X> is not in the resume, answer “No” (not “Skip”).
- If a field asks for graduation year and the resume clearly lists it, return the year (or pick the correct option).
- If nothing in the resume supports a definite answer and the question is not a knowledge/skill presence check, SKIP.

Return JSON only.
"""

# ---------------------------- Helpers ----------------------------

PERSONAL_PATTERNS = [
    r'\bctc\b', r'\bsalary\b', r'\bcompensation\b', r'\bpackage\b', r'\bexpected\b', r'\bcurrent\b',
    r'\bnotice\b', r'\blast\s*working\s*day\b', r'\blwd\b', r'\bjoining\b', r'\bbuyout\b',
    r'\bwork\s*from\s*home\b', r'\bwfh\b', r'\bwfo\b', r'\bhybrid\b', r'\bshift(s)?\b',
    r'\bpreferred\s*location\b', r'\brelocat(e|ion)\b', r'\btravel\b',
    r'\bgender\b', r'\bage\b', r'\bdob\b', r'\bdate\s*of\s*birth\b', r'\bmarital\b',
    r'\bbond\b', r'\bservice\s*agreement\b', r'\bnon-?compete\b', r'\bnda\b',
    r'\bvisa\b', r'\bimmigration\b'
]

@dataclass
class NormalizedOption:
    label: str

@dataclass
class NormalizedField:
    id: str
    question: str
    type: str  # text|textarea|select|multiselect|checkbox|radio|unknown
    options: List[NormalizedOption]
    allows_multiple: bool

def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def read_resume_any(path: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Returns (resume_text, resume_json_if_any)
    - If JSON: flatten into readable text + return dict.
    - If not JSON: read as raw text, return (text, None).
    """
    try:
        data = load_json(path)
        text = flatten_resume_json(data)
        return text, data
    except Exception:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        return text, None

def flatten_resume_json(d: Dict[str, Any]) -> str:
    """Make a readable text dump from a parsed resume JSON of arbitrary shape."""
    chunks: List[str] = []
    def _walk(prefix: str, obj: Any):
        if obj is None:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                _walk(f"{prefix}{k}.", v)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _walk(f"{prefix}{i}.", v)
        else:
            val = str(obj).strip()
            if val:
                key = prefix[:-1] if prefix.endswith(".") else prefix
                chunks.append(f"{key}: {val}")
    _walk("", d)
    return "\n".join(chunks)

def normalize_fields(form: Dict[str, Any]) -> List[NormalizedField]:
    """
    Accepts a wide variety of form schemas. We look for:
      - id or name (id preferred)
      - question/label/prompt/text/title
      - type/kind
      - options: list[str] or list[{label: str, ...}]
      - allows_multiple: inferred from type or flags
    """
    out: List[NormalizedField] = []

    possible_lists = []
    if isinstance(form, dict):
        for key in ["fields", "questions", "items", "schema", "formFields"]:
            if key in form and isinstance(form[key], list):
                possible_lists.append(form[key])
        if not possible_lists and isinstance(form.get("form"), list):
            possible_lists.append(form["form"])
    elif isinstance(form, list):
        possible_lists.append(form)

    if not possible_lists:
        raise ValueError("Could not find a list of fields in provided form JSON.")

    fields_list = max(possible_lists, key=len)

    for raw in fields_list:
        if not isinstance(raw, dict):
            continue

        fid = str(
            raw.get("id")
            or raw.get("field_id")
            or raw.get("name")
            or raw.get("key")
            or raw.get("slug")
            or raw.get("uid")
            or ""
        ).strip()

        question = str(
            raw.get("question")
            or raw.get("label")
            or raw.get("prompt")
            or raw.get("text")
            or raw.get("title")
            or ""
        ).strip()

        rtype = (raw.get("type") or raw.get("kind") or raw.get("component") or "unknown").lower()

        allows_multiple = False
        multi_flags = [
            raw.get("multiple"),
            raw.get("multi"),
            raw.get("is_multi"),
            raw.get("allows_multiple"),
        ]
        allows_multiple = any(bool(x) for x in multi_flags if x is not None)
        if any(t in rtype for t in ["checkbox", "multi", "chips"]):
            allows_multiple = True

        options_raw = raw.get("options") or raw.get("choices") or []
        options: List[NormalizedOption] = []
        if isinstance(options_raw, list):
            for opt in options_raw:
                if isinstance(opt, dict):
                    label = str(opt.get("label") or opt.get("name") or opt.get("text") or opt.get("value") or "").strip()
                else:
                    label = str(opt).strip()
                if label:
                    options.append(NormalizedOption(label=label))

        # coerce type
        if rtype in ["select", "dropdown", "combo", "combobox"]:
            rtype = "select"
        elif rtype in ["checkbox", "checkboxes"]:
            rtype = "multiselect"
            allows_multiple = True
        elif rtype in ["radio", "radiogroup", "choice"]:
            rtype = "radio"
        elif rtype in ["input", "shorttext", "textinput"]:
            rtype = "text"
        elif rtype in ["textarea", "longtext"]:
            rtype = "textarea"
        elif rtype not in ["text", "textarea", "select", "multiselect", "radio", "unknown"]:
            rtype = "unknown"

        if fid and question:
            out.append(NormalizedField(
                id=fid,
                question=question,
                type=rtype,
                options=options,
                allows_multiple=allows_multiple
            ))

    if not out:
        raise ValueError("No valid fields with id and question found.")
    return out

def extract_simple_facts(resume_text: str) -> Dict[str, Any]:
    """
    Lightweight extraction to help the model (model must still rely on resume_text).
    """
    email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", resume_text)
    phone = re.search(r"(\+?\d[\d\-\s()]{7,}\d)", resume_text)
    name_line = resume_text.splitlines()[0] if resume_text.splitlines() else ""
    return {
        "possible_email": email.group(0) if email else None,
        "possible_phone": phone.group(0) if phone else None,
        "first_line_maybe_name": name_line.strip()[:120] if name_line else None,
    }

def build_model_payload(fields: List[NormalizedField], resume_text: str, facts: Dict[str, Any]) -> Dict[str, Any]:
    norm_fields = []
    for f in fields:
        norm_fields.append({
            "id": f.id,
            "question": f.question,
            "type": f.type,
            "allows_multiple": f.allows_multiple,
            "options": [o.label for o in f.options],
        })
    return {
        "resume_text": resume_text,
        "pre_extracted_facts": facts,
        "fields": norm_fields,
    }

def call_gemini(prompt_payload: Dict[str, Any], model_name: str) -> Dict[str, Any]:
    import google.generativeai as genai
    api_key = "AIzaSyAJrmvM10sV7GxgzAwApFtGtR3ht6l3fY0"
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY / GOOGLE_API_KEY environment variable.")
    genai.configure(api_key=api_key)

    model = genai.GenerativeModel(
        model_name,
        system_instruction=SYSTEM_INSTRUCTION,
        generation_config={
            "temperature": 0.0,
            "top_p": 0.0,
            "response_mime_type": "application/json"
        },
    )
    user_msg = (
        "Fill the form using ONLY the resume below. Return JSON only.\n"
        + json.dumps(prompt_payload, ensure_ascii=False)
    )
    resp = model.generate_content(user_msg)
    text = (resp.text or "").strip()

    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]+\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        raise ValueError(f"Model returned non-JSON response:\n{text}")

def is_personal(question: str) -> bool:
    q = (question or "").lower()
    return any(re.search(p, q) for p in PERSONAL_PATTERNS)

def ci_match_label(val: str, labels: List[str]) -> Optional[str]:
    """Case-insensitive exact match to one of the labels; returns the canonical label if found."""
    v = val.strip().casefold()
    for lab in labels:
        if v == lab.casefold():
            return lab
    return None

def validate_and_clip(fields: List[NormalizedField], model_out: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Enforce:
      - answers: { field_id: scalar | list[str] }
      - For optioned fields, ensure values are within labels (case-insensitive allowed, mapped back).
      - Merge model skipped with our own added clips.
      - Also locally skip personal/preference questions for safety.
    """
    answers: Dict[str, Any] = {}
    skipped: List[Dict[str, Any]] = []
    field_map = {f.id: f for f in fields}

    out_answers = model_out.get("answers") or {}
    out_skipped = model_out.get("skipped") or []

    # carry over model's skipped list first
    for s in out_skipped:
        if isinstance(s, dict) and s.get("id") and s.get("reason"):
            skipped.append(s)

    for f in fields:
        if is_personal(f.question):
            skipped.append({"id": f.id, "question": f.question, "reason": "personal/preference"})
            continue

        if f.id not in out_answers:
            # if model didn't answer, keep skipped (unless already present)
            if not any(s.get("id") == f.id for s in skipped):
                skipped.append({"id": f.id, "question": f.question, "reason": "not in resume / ambiguous"})
            continue

        val = out_answers.get(f.id)

        if f.options:
            labels = [o.label for o in f.options]
            if f.allows_multiple:
                chosen: List[str] = []
                if isinstance(val, list):
                    for v in val:
                        if isinstance(v, str):
                            lab = ci_match_label(v, labels)
                            if lab:
                                chosen.append(lab)
                elif isinstance(val, str):
                    lab = ci_match_label(val, labels)
                    if lab:
                        chosen = [lab]
                if chosen:
                    answers[f.id] = chosen
                else:
                    skipped.append({"id": f.id, "question": f.question, "reason": "no valid option chosen"})
            else:
                if isinstance(val, str):
                    lab = ci_match_label(val, labels)
                    if lab:
                        answers[f.id] = lab
                    else:
                        skipped.append({"id": f.id, "question": f.question, "reason": "no valid option chosen"})
                else:
                    skipped.append({"id": f.id, "question": f.question, "reason": "invalid value type"})
        else:
            # free text / scalar — keep simple types only
            if isinstance(val, (str, int, float, bool)) or (isinstance(val, list) and all(isinstance(x, str) for x in val)):
                answers[f.id] = val
            else:
                skipped.append({"id": f.id, "question": f.question, "reason": "invalid value type"})

    return answers, skipped

# ---------------------------- Main (no argparse) ----------------------------

def main():
    # Load inputs
    form = load_json(FORM_PATH)
    fields = normalize_fields(form)
    resume_text, _resume_json = read_resume_any(RESUME_PATH)
    facts = extract_simple_facts(resume_text)
    payload = build_model_payload(fields, resume_text, facts)

    if DRY_RUN:
        print("=== SYSTEM INSTRUCTION ===")
        print(SYSTEM_INSTRUCTION.strip())
        print("\n=== PROMPT PAYLOAD (to model) ===")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    # Call Gemini
    model_out = call_gemini(payload, MODEL_NAME)

    # Validate & clip to schema/options + local personal safety
    answers, skipped = validate_and_clip(fields, model_out)

    # Write outputs
    with open(ANSWERS_OUT, "w", encoding="utf-8") as f:
        json.dump(answers, f, ensure_ascii=False, indent=2)
    with open(SKIPPED_OUT, "w", encoding="utf-8") as f:
        json.dump(skipped, f, ensure_ascii=False, indent=2)

    print(f"Wrote: {ANSWERS_OUT}")
    print(f"Wrote: {SKIPPED_OUT}")

if __name__ == "__main__":
    main()
