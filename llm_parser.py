# llm_parser.py
# ------------------------------------------------------------
# Maps a single free-text reply to a dict of { field_id: answer }
# using Google Gemini (google-generativeai) if available, else
# a simple numbered/positional fallback parser.
#
# Env:
#   GEMINI_API_KEY=<your key>
#   GEMINI_MODEL (optional, default: "gemini-1.5-flash")
#
# Install:
#   pip install google-generativeai
# ------------------------------------------------------------

import os
import json
import re
from typing import List, Dict, Any, Optional

def map_answers_to_ids(
    skipped_items: List[Dict[str, Any]],
    user_reply: str,
    model_hint: Optional[str] = None
) -> Dict[str, Any]:
    """
    Returns dict like { field_id: answer } using Gemini if available, else naive fallback.
    `skipped_items` entries look like: { "id": "...", "question": "...", "reason": "..." }
    """
    if _has_gemini_key():
        try:
            return _gemini_parse(skipped_items, user_reply, model_hint)
        except Exception:
            # fallback if LLM fails
            return _fallback_parse(skipped_items, user_reply)
    else:
        return _fallback_parse(skipped_items, user_reply)

# ------------------- internals -------------------

def _has_gemini_key() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))

def _prompt(skipped: List[Dict[str, Any]], reply: str) -> str:
    numbered = []
    for i, s in enumerate(skipped, 1):
        q = s.get("question") or "(no question text)"
        fid = s.get("id") or "(no id)"
        numbered.append(f"{i}. [id={fid}] {q}")
    numbered_text = "\n".join(numbered)
    schema = {
        "type": "object",
        "additionalProperties": False,
        "patternProperties": {
            ".*": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]}
        }
    }
    return (
        "You are a form-filling assistant.\n"
        "Given a set of numbered questions (with internal field ids) and the user's single free-text reply,\n"
        "extract answers and output a STRICT JSON object that maps field_id -> answer.\n"
        "Rules:\n"
        "• Use the field_id from [id=...] strictly as the keys.\n"
        "• If the field is multi-select, return a JSON array of strings (not a single comma string).\n"
        "• If not enough info, omit that key.\n"
        "• Do NOT include any explanations, only valid JSON.\n\n"
        "Questions:\n"
        f"{numbered_text}\n\n"
        "User reply:\n"
        f"{reply}\n\n"
        "Output JSON schema (informal):\n"
        f"{json.dumps(schema, indent=2)}\n\n"
        "Return ONLY the JSON object."
    )

def _gemini_parse(skipped: List[Dict[str, Any]], reply: str, model_hint: Optional[str]) -> Dict[str, Any]:
    import google.generativeai as genai
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    model_name = model_hint or os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    g = genai.GenerativeModel(model_name)
    prompt = _prompt(skipped, reply)
    res = g.generate_content(prompt)
    txt = (res.text or "").strip()

    json_str = _extract_json(txt)
    data = json.loads(json_str) if json_str else {}
    allowed = {s["id"] for s in skipped if s.get("id")}
    return {k: data[k] for k in data.keys() if k in allowed}

def _extract_json(s: str) -> Optional[str]:
    # Try code fences first
    fence = re.findall(r"```(?:json)?\s*([\s\S]*?)```", s)
    if fence:
        return fence[0].strip()
    # Otherwise try to locate first { ... } block
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start:end+1]
    return None

def _fallback_parse(skipped: List[Dict[str, Any]], reply: str) -> Dict[str, Any]:
    """
    Very simple heuristic fallback:
    - If reply has clearly numbered lines like '1) foo', '2. bar', map by number.
    - Else, split by newlines and map by order.
    - For commas, return a list (basic multi-select support).
    """
    lines = [l.strip() for l in reply.strip().splitlines() if l.strip()]
    # numbered lines: 1) answer, 2. answer, 3 - answer:
    numbered_pairs = []
    for line in lines:
        m = re.match(r"^(\d+)[\).\-\:]\s*(.+)$", line)
        if m:
            try:
                idx = int(m.group(1))
                numbered_pairs.append((idx, m.group(2).strip()))
            except:
                pass

    def maybe_multi(s: str):
        if "," in s:
            return [p.strip() for p in s.split(",") if p.strip()]
        return s

    out: Dict[str, Any] = {}
    if numbered_pairs:
        for idx, ans in numbered_pairs:
            if 1 <= idx <= len(skipped):
                fid = skipped[idx - 1].get("id")
                if fid:
                    out[fid] = maybe_multi(ans)
        return out
    else:
        for i, ans in enumerate(lines):
            if i < len(skipped):
                fid = skipped[i].get("id")
                if fid:
                    out[fid] = maybe_multi(ans)
        return out

def parse_modification_request(
    user_message: str,
    questions: List[Dict[str, Any]],
    current_answers: Dict[str, str]
) -> Optional[Dict[str, Any]]:
    """
    Parse natural language modification requests like:
    - "no change this to that"
    - "change question 2 to new value"
    - "modify answer 3 to xyz"
    - "update first_name to John"
    
    Returns: {
        "field_id": str,
        "question": str,
        "old_value": str,
        "new_value": str
    } or None if parsing fails
    
    NOTE: This handles SINGLE modifications only.
    Use parse_multiple_modifications() for multiple changes in one message.
    """
    msg = user_message.lower().strip()
    
    # Pattern 1: "no change X to Y" or "change X to Y"
    # Try to extract "change <something> to <new_value>"
    change_patterns = [
        r'(?:no\s+)?change\s+(.+?)\s+to\s+(.+)',
        r'modify\s+(.+?)\s+to\s+(.+)',
        r'update\s+(.+?)\s+to\s+(.+)',
        r'replace\s+(.+?)\s+(?:with|to)\s+(.+)',
    ]
    
    for pattern in change_patterns:
        match = re.search(pattern, msg, re.IGNORECASE)
        if match:
            identifier = match.group(1).strip()
            new_value = match.group(2).strip()
            
            # Try to identify which field based on identifier
            field_id, question = _identify_field(identifier, questions, current_answers)
            
            if field_id:
                old_value = current_answers.get(field_id, "")
                return {
                    "field_id": field_id,
                    "question": question,
                    "old_value": old_value,
                    "new_value": new_value
                }
    
    # Pattern 2: "question N to value" (e.g., "question 2 to xyz")
    question_num_match = re.search(r'(?:question|q)\s*(\d+)\s+to\s+(.+)', msg, re.IGNORECASE)
    if question_num_match:
        q_num = int(question_num_match.group(1))
        new_value = question_num_match.group(2).strip()
        
        if 1 <= q_num <= len(questions):
            question = questions[q_num - 1]
            field_id = question.get("id")
            old_value = current_answers.get(field_id, "")
            
            return {
                "field_id": field_id,
                "question": question.get("question", ""),
                "old_value": old_value,
                "new_value": new_value
            }
    
    return None

def parse_multiple_modifications(
    user_message: str,
    questions: List[Dict[str, Any]],
    current_answers: Dict[str, str]
) -> List[Dict[str, Any]]:
    """
    Parse multiple modification requests in a single message like:
    - "change question 2 to yes, question 3 to no"
    - "question 1 to abc, question 5 to xyz"
    - "change q2 to yes, q3 to no, q4 to maybe"
    
    Returns list of modifications: [{
        "field_id": str,
        "question": str,
        "old_value": str,
        "new_value": str
    }, ...]
    """
    modifications = []
    msg = user_message.strip()
    
    # Pattern: "question N to value" repeated with commas or "and"
    # Split by comma or " and " or ", and"
    segments = re.split(r',\s*(?:and\s+)?|\s+and\s+', msg, flags=re.IGNORECASE)
    
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        
        # Try to parse each segment as a modification
        # Pattern: "(change/modify/update)? question N to value"
        patterns = [
            r'(?:change|modify|update)?\s*(?:question|q)\s*(\d+)\s+to\s+(.+)',
            r'(?:change|modify|update)?\s*(\d+)\s+to\s+(.+)',  # Just "2 to yes"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, segment, re.IGNORECASE)
            if match:
                q_num = int(match.group(1))
                new_value = match.group(2).strip()
                
                # Remove trailing "question" or "q" if it's the start of next modification
                new_value = re.sub(r'\s*(?:question|q)\s*\d+.*$', '', new_value, flags=re.IGNORECASE).strip()
                
                if 1 <= q_num <= len(questions):
                    question = questions[q_num - 1]
                    field_id = question.get("question_id") or question.get("id", "")
                    question_text = question.get("question", "") or question.get("label", "")
                    old_value = current_answers.get(field_id, "")
                    
                    modifications.append({
                        "field_id": field_id,
                        "question": question_text,
                        "old_value": old_value,
                        "new_value": new_value
                    })
                break
    
    return modifications

def _identify_field(
    identifier: str,
    questions: List[Dict[str, Any]],
    current_answers: Dict[str, str]
) -> tuple[Optional[str], Optional[str]]:
    """
    Try to identify which field the user is referring to.
    Returns (field_id, question_text) or (None, None)
    """
    identifier = identifier.lower().strip()
    
    # Check if identifier is a question number
    if identifier.startswith("question") or identifier.startswith("q"):
        num_match = re.search(r'\d+', identifier)
        if num_match:
            q_num = int(num_match.group())
            if 1 <= q_num <= len(questions):
                q = questions[q_num - 1]
                # Handle both "id" and "question_id" fields
                field_id = q.get("question_id") or q.get("id", "")
                question_text = q.get("question", "") or q.get("label", "")
                return field_id, question_text
    
    # Check if identifier matches a field ID (try both "id" and "question_id")
    for q in questions:
        field_id = q.get("question_id", "") or q.get("id", "")
        if field_id.lower() == identifier:
            question_text = q.get("question", "") or q.get("label", "")
            return field_id, question_text
    
    # Check if identifier appears in the question text
    for q in questions:
        question_text = q.get("question", "") or q.get("label", "")
        if identifier in question_text.lower():
            field_id = q.get("question_id", "") or q.get("id", "")
            return field_id, question_text
    
    # Check if identifier matches the current answer
    for field_id, answer in current_answers.items():
        if str(answer).lower() == identifier:
            # Find the question for this field_id
            for q in questions:
                q_field_id = q.get("question_id", "") or q.get("id", "")
                if q_field_id == field_id:
                    question_text = q.get("question", "") or q.get("label", "")
                    return field_id, question_text
    
    return None, None
