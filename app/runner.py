import os, uuid
from typing import Any, Dict, List, Tuple
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from .config import JOB_URL, HEADLESS, SCREENSHOT_DIR, KNOWN_KEYS
from .profile import load_profile, extract_contact, read_resume_text
from .llm import infer_with_llm, draft_summary_and_letter
from .dom import collect_form_fields, read_current_value
from .fill import upload_resume_if_possible, fill_value_into_field, click_submit, success_indicator
from .job import scrape_job_text
from .prompts import prompt_user_for, prompt_user_choice, prompt_user_multi, yes_no
from .widgets import open_combobox_and_get_options

def run_once():
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
            print("[error] page load timed out.")
            browser.close(); return

        page.wait_for_timeout(1200)

        # 1) Summary + tailored cover letter
        job = scrape_job_text(page)
        summary, cover_letter = draft_summary_and_letter(job, resume_text, contact)

        print("\n=== JOB / COMPANY SUMMARY ===")
        print(summary)
        print("\n=== PROPOSED COVER LETTER ===")
        print(cover_letter)

        if not yes_no("\nApprove this cover letter and proceed to filling the form?", default=False):
            print("[abort] User declined to proceed."); browser.close(); return

        # 2) Parse & upload
        fields = collect_form_fields(page)
        upload_resume_if_possible(fields)

        # 3) Plan known keys (JSON -> LLM -> prompt)
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

        # 4) Unknown questions (show selects/comboboxes; text only if required; radios/checkboxes grouped)
        planned_by_field: Dict[Any, Any] = {}

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
                    print(f"[warn] Could not read options for '{label}'. Try clicking it manually if needed.")

            elif widget in ("radio","checkbox"):
                pass
            else:
                if f["required"]:
                    label = f["label"] or f["placeholder"] or f["name"] or f["aria"] or "Answer"
                    v = prompt_user_for(label)
                    if v: planned_by_field[f["el"]] = v

        # required unknowns first + all selects/comboboxes + radios/checkboxes
        for f in fields:
            if f["key"] in KNOWN_KEYS:
                continue
            if read_current_value(f):
                continue
            if f["required"] or f["widget"] in ("select","combobox") or f["widget"] in ("radio","checkbox"):
                ask_for_field(f)

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

        # review
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

        # fill
        for f in fields:
            k = f["key"]
            if k in KNOWN_KEYS and k in planned_by_key:
                fill_value_into_field(f, planned_by_key[k], page=page)
            elif f["el"] in planned_by_field:
                fill_value_into_field(f, planned_by_field[f["el"]], page=page)

        # re-check required
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
                ask_for_field(f)
            for f in remaining:
                if f["key"] in KNOWN_KEYS and f["key"] in planned_by_key:
                    fill_value_into_field(f, planned_by_key[f["key"]], page=page)
                elif f["el"] in planned_by_field:
                    fill_value_into_field(f, planned_by_field[f["el"]], page=page)

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

def main():
    # sanity checks here if you want
    run_once()
