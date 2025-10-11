#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Interactive completion for skipped form fields (dedup + ask-once + carry over previous answers).

Reads:
  - FORM_PATH               form schema (used to show options / question text)
  - SKIPPED_PATH            skipped fields (may contain duplicate IDs)
  - EXISTING_FILLED         autofill answers: {id: value} (optional)
  - PREVIOUS_COMPLETED      prior interactive output: {id:{question,answer}} (optional)

Writes:
  - OUTPUT_ANSWERS          merged interactive output: {id:{question,answer}}
  - STILL_SKIPPED           any you left blank in this run
"""

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from output_config import OutputPaths

# ========================= HARD-CODED PATHS (now centralized) =========================
FORM_PATH = OutputPaths.FORM_FIELDS_ENHANCED        # Note: using cleaned form fields
SKIPPED_PATH = OutputPaths.SKIPPED_FIELDS
EXISTING_FILLED = OutputPaths.FILLED_ANSWERS         # optional
PREVIOUS_COMPLETED = OutputPaths.USER_COMPLETED_ANSWERS   # optional; reused/updated
OUTPUT_ANSWERS = OutputPaths.USER_COMPLETED_ANSWERS
STILL_SKIPPED = OutputPaths.STILL_SKIPPED

# Configuration
SKIP_INTERACTIVE_REVIEW = False   # Set to True to skip the final review/modification mode
# ====================================================================

# ----------------------- Schema Normalization -----------------------

@dataclass
class NormalizedOption:
    label: str

@dataclass
class NormalizedField:
    id: str
    question: str
    type: str               # text|textarea|select|multiselect|checkbox|radio|unknown
    options: List[NormalizedOption]
    allows_multiple: bool

def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_previously_answered():
    """Load previously answered questions to avoid duplicates"""
    cache_path = os.path.join(OutputPaths.DATA_DIR, "answered_questions_cache.json")
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_previously_answered(answers_dict):
    """Save answered questions to avoid asking again"""
    cache_path = os.path.join(OutputPaths.DATA_DIR, "answered_questions_cache.json")
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(answers_dict, f, indent=2)

def normalize_fields(form: Dict[str, Any]) -> List[NormalizedField]:
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
            raw.get("question_id")  # Primary field for our form structure
            or raw.get("id")
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

        rtype = (raw.get("input_type") or raw.get("type") or raw.get("kind") or raw.get("component") or "unknown").lower()

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
        elif rtype in ["file", "upload", "file_upload", "resume_upload"]:
            rtype = "file"
        elif rtype not in ["text", "textarea", "select", "multiselect", "radio", "file", "unknown"]:
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

# ----------------------- Interactive Helpers -----------------------

def ci_match_label(val: str, labels: List[str]) -> Optional[str]:
    v = val.strip().casefold()
    for lab in labels:
        if v == lab.casefold():
            return lab
    return None

def parse_selection(input_str: str, labels: List[str]) -> List[str]:
    chosen: List[str] = []
    toks = [t.strip() for t in input_str.split(",") if t.strip()]
    for t in toks:
        if t.isdigit():
            idx = int(t) - 1
            if 0 <= idx < len(labels):
                if labels[idx] not in chosen:
                    chosen.append(labels[idx])
        else:
            lab = ci_match_label(t, labels)
            if lab and lab not in chosen:
                chosen.append(lab)
    return chosen

def ask_for_field(field: NormalizedField) -> Tuple[bool, Any]:
    print("\n" + "="*70)
    print(f"Field: {field.question}")
    print(f"ID: {field.id}")

    # Special handling for file uploads
    if field.type == "file":
        # Check if this is a resume/CV field and if the resume file exists
        if "resume" in field.id.lower() or "cv" in field.id.lower() or "resume" in field.question.lower() or "cv" in field.question.lower():
            resume_paths = ["./data/Geetansh_resume.pdf", "./data/resume.pdf", "./data/resume.txt", "data/Geetansh_resume.pdf", "data/resume.pdf", "data/resume.txt"]
            existing_resume = None
            for path in resume_paths:
                if os.path.exists(path):
                    existing_resume = path
                    break
            
            if existing_resume:
                print(f"📄 Auto-detected resume file: {existing_resume}")
                print("✅ Using existing resume file for upload")
                return (True, existing_resume)
            else:
                print("📄 This appears to be a resume/CV upload field.")
                print("   Resume file not found in ./data/ directory.")
                print("   The form filling script will handle the upload automatically.")
                print("   Marking as completed with placeholder.")
                return (True, "RESUME_FILE_UPLOAD")
        else:
            print("📁 This is a file upload field.")
            print("   Please specify the file path, or press Enter to skip.")
            raw = input("> ").strip()
            if not raw:
                return (False, None)
            return (True, raw)

    if field.options:
        labels = [o.label for o in field.options]
        print("\nOptions:")
        for i, lab in enumerate(labels, start=1):
            print(f"  {i}. {lab}")

        if field.allows_multiple:
            print("\nSelect one or more options (comma-separated indices or labels).")
            print("Press Enter to skip.")
            raw = input("> ").strip()
            if not raw:
                return (False, None)
            chosen = parse_selection(raw, labels)
            if chosen:
                return (True, chosen)
            else:
                print("No valid options recognized. Skipping this field.")
                return (False, None)
        else:
            print("\nSelect ONE option (enter index or label). Press Enter to skip.")
            raw = input("> ").strip()
            if not raw:
                return (False, None)
            chosen = parse_selection(raw, labels)
            if len(chosen) == 1:
                return (True, chosen[0])
            elif len(chosen) > 1:
                print("Multiple choices detected; expecting only one. Skipping this field.")
                return (False, None)
            else:
                print("No valid option recognized. Skipping this field.")
                return (False, None)
    else:
        print("\nType your answer (free text). Press Enter to skip.")
        raw = input("> ").strip()
        if not raw:
            return (False, None)
        return (True, raw)

# ----------------------- Dedup / Merge Helpers -----------------------

def dedup_skipped_by_id(skipped_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Keep one item per field id. If both 'personal/preference' and some other reason exist,
    keep the 'personal/preference' entry. Otherwise, keep the first seen.
    """
    chosen: Dict[str, Dict[str, Any]] = {}
    for item in skipped_list:
        fid = item.get("id")
        if not fid:
            continue
        if fid not in chosen:
            chosen[fid] = item
        else:
            # prefer personal/preference reason
            curr = chosen[fid]
            if (item.get("reason") == "personal/preference" and curr.get("reason") != "personal/preference"):
                chosen[fid] = item
    # preserve original order of first appearance
    seen = set()
    out: List[Dict[str, Any]] = []
    for item in skipped_list:
        fid = item.get("id")
        if not fid or fid in seen:
            continue
        if fid in chosen:
            out.append(chosen[fid])
            seen.add(fid)
    return out

def unwrap_previous_completed(prev_completed: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert {id:{question,answer}} -> {id: answer} so we can skip re-asking.
    """
    out: Dict[str, Any] = {}
    for fid, bundle in prev_completed.items():
        if isinstance(bundle, dict) and "answer" in bundle:
            out[fid] = bundle["answer"]
    return out

# ---------------------------- Main ----------------------------

def interactive_review_all_answers(wrapped_answers: Dict[str, Dict[str, Any]], 
                                  field_map: Dict[str, NormalizedField]) -> Dict[str, Dict[str, Any]]:
    """
    Show all answers and allow user to modify any they don't like.
    Returns updated wrapped_answers dictionary.
    """
    print("\n📝 Here are all your answers. You can modify any you don't like:")
    print("   - Press Enter to keep the current answer")
    print("   - Type a new answer to change it")
    print("   - Type 'skip' or 'review' to see all answers first, then modify\n")
    
    # First, ask if they want to review all answers
    try:
        review_mode = input("Would you like to review all answers first? [y/N]: ").strip().lower()
        show_all_first = review_mode in ('y', 'yes', 'review')
    except EOFError:
        show_all_first = False
    
    # Show all answers first if requested
    if show_all_first:
        print("\n📋 ALL CURRENT ANSWERS:")
        print("-" * 60)
        for i, (field_id, bundle) in enumerate(wrapped_answers.items(), 1):
            question = bundle.get("question", "")
            answer = bundle.get("answer", "")
            print(f"{i:2d}. {question}")
            print(f"    → {answer}")
            print()
        print("-" * 60)
    else:
        print("✅ Skipping review as requested.")
    
    # Now allow modifications
    modified_answers = dict(wrapped_answers)  # Copy to modify
    
    print(f"\n🔄 MODIFICATION MODE - {len(wrapped_answers)} questions to review:")
    print("=" * 60)
    
    for i, (field_id, bundle) in enumerate(wrapped_answers.items(), 1):
        question = bundle.get("question", "")
        current_answer = bundle.get("answer", "")
        
        print(f"\n[{i}/{len(wrapped_answers)}] {question}")
        print(f"Current answer: {current_answer}")
        
        # Get field info for validation
        field_info = field_map.get(field_id)
        
        try:
            if field_info and field_info.options:
                # Show options for select/radio fields
                print("Available options:")
                for j, opt in enumerate(field_info.options, 1):
                    marker = "→" if opt.label == current_answer else " "
                    print(f"  {marker} {j}. {opt.label}")
                
                user_input = input("Keep current (Enter) or choose number/type new answer: ").strip()
                
                if user_input == "":
                    # Keep current answer
                    continue
                elif user_input.replace(" ", "").replace(",", "").isdigit() or " " in user_input or "," in user_input:
                    # Handle single number or multiple numbers (e.g., "2", "2 3", "1,2,3", "1 4 5")
                    try:
                        # Split by both space and comma
                        parts = user_input.replace(",", " ").split()
                        choice_nums = [int(x.strip()) for x in parts if x.strip().isdigit()]
                        selected_options = []
                        
                        for choice_num in choice_nums:
                            if 1 <= choice_num <= len(field_info.options):
                                selected_options.append(field_info.options[choice_num - 1].label)
                            else:
                                print(f"  ⚠️ Invalid choice {choice_num} ignored.")
                        
                        if selected_options:
                            # For multi-select, keep as list; for single select, use first item
                            if field_info.allows_multiple or len(selected_options) > 1:
                                new_answer = selected_options
                            else:
                                new_answer = selected_options[0]
                            
                            modified_answers[field_id]["answer"] = new_answer
                            print(f"  ✅ Changed to: {new_answer}")
                        else:
                            print(f"  ❌ No valid choices found. Keeping current answer.")
                    except ValueError:
                        # Not valid numbers, treat as custom text
                        modified_answers[field_id]["answer"] = user_input
                        print(f"  ✅ Changed to: {user_input}")
                else:
                    # User typed custom answer
                    modified_answers[field_id]["answer"] = user_input
                    print(f"  ✅ Changed to: {user_input}")
            else:
                # Text field - just ask for new value
                user_input = input("Keep current (Enter) or type new answer: ").strip()
                
                if user_input != "":
                    modified_answers[field_id]["answer"] = user_input
                    print(f"  ✅ Changed to: {user_input}")
        
        except EOFError:
            print("  ⏭️ Skipping remaining questions...")
            break
        except KeyboardInterrupt:
            print("\n  ⏭️ Review interrupted. Keeping current answers.")
            break
    
    # Count changes
    changes = 0
    for field_id in wrapped_answers:
        if str(wrapped_answers[field_id]["answer"]) != str(modified_answers[field_id]["answer"]):
            changes += 1
    
    if changes > 0:
        print(f"\n✅ Review complete! {changes} answer(s) modified.")
    else:
        print(f"\n✅ Review complete! No changes made.")
    
    return modified_answers

def main():
    # Load form + normalize fields
    form = load_json(FORM_PATH)
    fields = normalize_fields(form)
    field_map = {f.id: f for f in fields}

    # Load skipped list & DEDUP by id
    skipped_list = load_json(SKIPPED_PATH)  # may contain duplicates
    if not isinstance(skipped_list, list):
        raise ValueError("skipped_fields.json must be a list.")
    skipped_list = dedup_skipped_by_id(skipped_list)

    # Load existing autofill answers (optional)
    try:
        existing_filled = load_json(EXISTING_FILLED)  # { id: value }
        if not isinstance(existing_filled, dict):
            existing_filled = {}
    except Exception:
        existing_filled = {}

    # Load previous interactive output (optional)
    try:
        prev_completed_wrapped = load_json(PREVIOUS_COMPLETED)  # { id: {question, answer} }
        if not isinstance(prev_completed_wrapped, dict):
            prev_completed_wrapped = {}
    except Exception:
        prev_completed_wrapped = {}
    previous_values = unwrap_previous_completed(prev_completed_wrapped)

    # Known answers so we DON'T ASK again
    known_answers: Dict[str, Any] = dict(existing_filled)
    known_answers.update(previous_values)

    new_values: Dict[str, Any] = {}
    still_skipped: List[Dict[str, Any]] = []
    asked_ids = set()

    print("\n== Interactive completion for skipped fields (ask-once) ==")
    print("Tip: Press Enter on any prompt to skip that field.\n")

    for s in skipped_list:
        fid = s.get("id")
        if not fid:
            continue

        # If already have an answer, skip prompting
        if fid in known_answers:
            continue

        # Guard against duplicates within the same run
        if fid in asked_ids:
            continue
        asked_ids.add(fid)

        f = field_map.get(fid)
        if not f:
            print(f"\n[WARN] Field metadata not found for id={fid!r}. Skipping.")
            still_skipped.append({"id": fid, "question": s.get("question") or "", "reason": "field metadata not found"})
            continue

        answered, val = ask_for_field(f)
        if answered:
            new_values[fid] = val
        else:
            still_skipped.append({"id": fid, "question": f.question, "reason": s.get("reason") or "user skipped"})

    # Merge and wrap output
    merged_values: Dict[str, Any] = dict(known_answers)
    merged_values.update(new_values)

    wrapped_output: Dict[str, Dict[str, Any]] = {}
    for fid, val in merged_values.items():
        f = field_map.get(fid)
        q = f.question if f else "(question text not found in form)"
        wrapped_output[fid] = {"question": q, "answer": val}

    # Interactive review of ALL answers (optional)
    if SKIP_INTERACTIVE_REVIEW:
        print("\n✅ Skipping interactive review (auto-mode enabled)")
        final_answers = wrapped_output
    else:
        print("\n" + "="*60)
        print("📋 FINAL REVIEW - Check all your answers before form filling")
        print("="*60)
        final_answers = interactive_review_all_answers(wrapped_output, field_map)
    
    with open(OUTPUT_ANSWERS, "w", encoding="utf-8") as f:
        json.dump(final_answers, f, ensure_ascii=False, indent=2)
    with open(STILL_SKIPPED, "w", encoding="utf-8") as f:
        json.dump(still_skipped, f, ensure_ascii=False, indent=2)

    print("\nDone.")
    print(f"Saved merged answers → {OUTPUT_ANSWERS}")
    print(f"Remaining skipped   → {STILL_SKIPPED}")

if __name__ == "__main__":
    main()
