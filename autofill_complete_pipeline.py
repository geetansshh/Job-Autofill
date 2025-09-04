#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pipeline runner (no changes to your existing files).

Order:
  1) resume_parser_gemini.py      -> parsed_resume.json
  2) cover-letter + summary       -> (runs only if a generator script exists)
  3) approval gate (continue?)
  4) form_extractor.py            -> form_fields.json
  5) form_answer_gemini.py        -> filled_answers.json + skipped_fields.json
  6) complete_skipped_fields.py   -> user_completed_answers.json (interactive)
  7) fill_form_resume.py          -> Playwright autofill (uses user_completed_answers.json)

Notes:
- Respects your current filenames/outputs. No modifications to those files.
- Nice prints, timing, file existence checks, and counts for easy debugging.
"""

import json
import os
import sys
import time
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

# -------- paths (relative to where you run this) --------
PY = sys.executable

S_RESUME_PARSER = "resume_parser_gemini.py"     # -> parsed_resume.json
S_FORM_EXTRACT  = "form_extractor.py"           # -> form_fields.json
S_FORM_ANSWER   = "form_answer_gemini.py"       # -> filled_answers.json, skipped_fields.json
S_SKIPPED_FIX   = "complete_skipped_fields.py"  # -> user_completed_answers.json
S_FORM_FILL     = "fill_form_resume.py"         # -> playwright-driven fill

# Cover letter / summary generators (first that exists will be run; all that exist will be run in order)
COVER_SUMMARY_CANDIDATES: List[List[str]] = [
    [PY, "cover_letter_summary.py"],
    [PY, "cover_letter_and_summary.py"],
    [PY, "cover_letter.py"],
    [PY, "summary.py"],
]

# -------- expected artifacts by step --------
F_PARSED_RESUME = Path("parsed_resume.json")
F_FORM_FIELDS   = Path("form_fields.json")
F_FILLED        = Path("filled_answers.json")
F_SKIPPED       = Path("skipped_fields.json")
F_USER_ANS      = Path("user_completed_answers.json")
F_COVER_TXT     = Path("cover_letter.txt")      # optional
F_SUMMARY_TXT   = Path("summary.txt")           # optional, name may vary in your local script

# -------- utility printing --------
def hr():
    print("\n" + "─" * 78 + "\n")

def h1(title: str):
    hr()
    print(f"🛠️  {title}")
    hr()

def ok(msg: str):
    print(f"✅ {msg}")

def warn(msg: str):
    print(f"⚠️  {msg}")

def info(msg: str):
    print(f"ℹ️  {msg}")

def err(msg: str):
    print(f"❌ {msg}")

# -------- shell runner --------
def run_step(cmd: List[str], title: str, must_exist: Optional[List[Path]] = None, env: Optional[dict] = None) -> bool:
    """
    Run a command in the foreground so interactive steps still work.
    Optionally verify certain files exist after completion.
    """
    h1(title)
    print("• Command:", " ".join(cmd))
    start = time.time()
    try:
        proc = subprocess.run(cmd, env=env, check=False)
    except FileNotFoundError:
        err(f"Command not found: {cmd[0]}")
        return False

    elapsed = time.time() - start
    print(f"\n⏱  Step finished in {elapsed:.1f}s with return code {proc.returncode}")

    if proc.returncode != 0:
        warn("Non-zero return code. Continuing only if outputs are present.")

    if must_exist:
        all_ok = True
        for p in must_exist:
            if p.exists():
                ok(f"Found output: {p}")
            else:
                err(f"Missing expected output: {p}")
                all_ok = False
        return all_ok
    return True

# -------- helpers for small validations --------
def count_json_items(path: Path) -> Optional[int]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            return len(data)
    except Exception:
        return None
    return None

def yesno(prompt: str, default_no: bool = True) -> bool:
    try:
        ans = input(f"{prompt} [{'Y/n' if default_no is False else 'y/N'}]: ").strip().lower()
    except EOFError:
        return not default_no  # default to "no" if no input and default_no=True
    if not ans:
        return not default_no
    return ans in ("y", "yes")

def check_env_key(var_names: List[str]) -> None:
    for v in var_names:
        if os.getenv(v):
            ok(f"Environment variable set: {v}")
            return
    warn(f"No API key found in {var_names}. Your scripts may load from .env or have hard-coded keys.")

# ============================ MAIN PIPELINE ============================

def main():
    print("\n🚀 Resume→Cover Letter→Form Extract→Answer→Complete Skips→Auto-Fill")
    print("   (interactive and debug-friendly)\n")

    # quick pre-flight: python + playwright check
    info(f"Python: {sys.version.split()[0]}  |  Executable: {PY}")
    for mod in ("google.generativeai", "pdfplumber", "playwright"):
        try:
            __import__(mod)
            ok(f"Import OK: {mod}")
        except Exception:
            warn(f"Import failed: {mod} (the step using it may error)")

    # API keys env check (resume parser + answerer may need it)
    check_env_key(["GEMINI_API_KEY", "GOOGLE_API_KEY"])

    # ------------------------------------------------------------------ #
    # 1) Parse resume -> parsed_resume.json
    #    (Matches your script’s OUT_PATH default.)                       #
    # ------------------------------------------------------------------ #
    # resume_parser_gemini.py writes ./parsed_resume.json by default. :contentReference[oaicite:5]{index=5}
    if not run_step([PY, S_RESUME_PARSER], "Step 1/7: Parse resume (Gemini)", [F_PARSED_RESUME]):
        err("Stopping: resume parse did not produce parsed_resume.json")
        return
    cnt = count_json_items(F_PARSED_RESUME)
    info(f"parsed_resume.json keys: {cnt if cnt is not None else 'n/a'}")

    # ------------------------------------------------------------------ #
    # 2) Cover letter + summary (optional)                               #
    # ------------------------------------------------------------------ #
    h1("Step 2/7: Generate cover letter & summary (if generator exists)")
    found_any = False
    for cmd in COVER_SUMMARY_CANDIDATES:
        if Path(cmd[1]).exists():
            found_any = True
            if not run_step(cmd, f"Cover/Summary via {cmd[1]}"):
                warn(f"{cmd[1]} finished with issues.")
    if not found_any:
        info("No cover-letter/summary generator scripts found. Skipping.")
    else:
        if F_COVER_TXT.exists():
            ok(f"Cover letter ready: {F_COVER_TXT}")
        else:
            warn("cover_letter.txt not found (upload step will just skip cover letter).")
        if F_SUMMARY_TXT.exists():
            ok(f"Summary ready: {F_SUMMARY_TXT}")
        else:
            info("summary.txt not found (that’s OK unless your generator names it this).")

    # ------------------------------------------------------------------ #
    # 3) Approval gate                                                   #
    # ------------------------------------------------------------------ #
    h1("Step 3/7: Approval to proceed to form extraction & filling")
    if not yesno("Proceed with form extraction and answering?", default_no=False):
        warn("User aborted after review. Exiting.")
        return
    ok("Continuing...")

    # ------------------------------------------------------------------ #
    # 4) Extract fields -> form_fields.json                              #
    # ------------------------------------------------------------------ #
    # form_extractor.py writes form_fields.json by default. :contentReference[oaicite:6]{index=6}
    if not run_step([PY, S_FORM_EXTRACT], "Step 4/7: Extract form fields (Playwright)", [F_FORM_FIELDS]):
        err("Stopping: form extraction didn’t produce form_fields.json")
        return
    fields_count = None
    try:
        with F_FORM_FIELDS.open("r", encoding="utf-8") as f:
            data = json.load(f)
        fields = data.get("fields") if isinstance(data, dict) else None
        fields_count = len(fields) if isinstance(fields, list) else None
    except Exception:
        pass
    info(f"Detected fields: {fields_count if fields_count is not None else 'n/a'}")

    # ------------------------------------------------------------------ #
    # 5) Answer fields -> filled_answers.json + skipped_fields.json      #
    # ------------------------------------------------------------------ #
    # form_answer_gemini.py writes the pair your other tools consume. 
    if not run_step([PY, S_FORM_ANSWER], "Step 5/7: Answer fields (Gemini)", [F_FILLED, F_SKIPPED]):
        warn("Proceeding even though answerer returned non-zero. Checking files...")
    ans_cnt = count_json_items(F_FILLED)
    skip_cnt = 0
    try:
        with F_SKIPPED.open("r", encoding="utf-8") as f:
            skipped_list = json.load(f)
        skip_cnt = len(skipped_list) if isinstance(skipped_list, list) else 0
    except Exception:
        pass
    info(f"Filled answers: {ans_cnt if ans_cnt is not None else 'n/a'} | Skipped: {skip_cnt}")

    # ------------------------------------------------------------------ #
    # 6) Complete skipped fields (interactive)                           #
    # ------------------------------------------------------------------ #
    # complete_skipped_fields.py consumes skipped_fields.json + filled_answers.json,
    # and writes user_completed_answers.json for the final Playwright fill. :contentReference[oaicite:8]{index=8}
    if skip_cnt > 0:
        ok("Launching interactive completion for skipped fields...")
        if not run_step([PY, S_SKIPPED_FIX], "Step 6/7: Complete skipped fields (interactive)", [F_USER_ANS]):
            warn("Interactive completion may have been skipped or failed. Checking for user_completed_answers.json...")
    else:
        info("No skipped fields. Creating passthrough user_completed_answers.json from filled_answers.json.")
        # Convert {id: value} -> {id: {question, answer}} is the complete_skipped_fields.py format,
        # but if there are truly zero skipped, fill_form_resume.py also tolerates empty/absent file.
        # We’ll create a minimal passthrough so you see a consistent file.
        try:
            with F_FILLED.open("r", encoding="utf-8") as f:
                filled = json.load(f)
            # wrap as {id: {question, answer}} with question="(from form)" if unknown
            wrapped = {fid: {"question": "(from form)", "answer": val} for fid, val in filled.items()}
            with F_USER_ANS.open("w", encoding="utf-8") as f:
                json.dump(wrapped, f, indent=2, ensure_ascii=False)
            ok(f"Created {F_USER_ANS} from filled_answers.json")
        except Exception as e:
            warn(f"Could not generate {F_USER_ANS} automatically: {e}")

    # ------------------------------------------------------------------ #
    # 7) Fill the form (Playwright, interactive submit approval inside)  #
    # ------------------------------------------------------------------ #
    # fill_form_resume.py consumes user_completed_answers.json (and attempts resume upload independently). :contentReference[oaicite:9]{index=9}
    if not run_step([PY, S_FORM_FILL], "Step 7/7: Auto-fill application form (Playwright)"):
        warn("The Playwright fill ended with a non-zero code. Check the above logs and screenshots (if any).")

    hr()
    ok("Pipeline complete.")
    print("Artifacts you’ll likely care about:")
    print(f"  • {F_PARSED_RESUME.resolve() if F_PARSED_RESUME.exists() else '(missing)'}")
    print(f"  • {F_FORM_FIELDS.resolve() if F_FORM_FIELDS.exists() else '(missing)'}")
    print(f"  • {F_FILLED.resolve() if F_FILLED.exists() else '(missing)'}")
    print(f"  • {F_SKIPPED.resolve() if F_SKIPPED.exists() else '(missing)'}")
    print(f"  • {F_USER_ANS.resolve() if F_USER_ANS.exists() else '(missing)'}")
    print(f"  • {F_COVER_TXT.resolve() if F_COVER_TXT.exists() else '(optional)'}")
    print(f"  • {F_SUMMARY_TXT.resolve() if F_SUMMARY_TXT.exists() else '(optional)'}\n")

if __name__ == "__main__":
    main()
