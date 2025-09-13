#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Ultra-minimal: pass resume PDF text to Gemini, get back JSON, save to parsed_resume.json.
- No argparse / no schema / no Pydantic.
- Edit RESUME_PDF if needed.
- Reads GEMINI_API_KEY from .env.
"""

import json
import os
import pdfplumber
from dotenv import load_dotenv
from output_config import OutputPaths

# ---- edit this to your actual file ----
RESUME_PDF = "./data/resume.pdf"
OUT_PATH   = OutputPaths.PARSED_RESUME  # Now using centralized output path
MODEL_NAME = "gemini-2.5-flash-lite"
# ---------------------------------------

def read_pdf_text(path: str) -> str:
    if not os.path.exists(path):
        raise FileNotFoundError(f"PDF not found: {path}")
    with pdfplumber.open(path) as pdf:
        parts = [(p.extract_text() or "") for p in pdf.pages]
    return "\n".join(parts).strip()

def build_prompt(resume_text: str) -> str:
    # Keep it simple but very explicit. We ask for JSON only and link-type detection.
    return (
        "You are an expert résumé parser.\n"
        "Return ONLY a single JSON object (no markdown, no commentary). "
        "Do not invent data; omit keys you cannot find. "
        "Use snake_case keys. Dates should prefer ISO formats (YYYY or YYYY-MM). "
        "All URLs MUST include the 'https://' scheme.\n\n"
        "Detect and classify profile links into specific keys, e.g.:\n"
        "  linkedin_url, github_url, portfolio_url, personal_website_url,\n"
        "  leetcode_url, kaggle_url, twitter_url, medium_url,\n"
        "  stackoverflow_url, behance_url, dribbble_url, google_scholar_url, researchgate_url.\n"
        "If multiple of the same type exist, make them arrays (e.g., github_urls: []).\n\n"
        "Recommended—but optional—top-level keys (include only if present in the text):\n"
        "  name, emails, phones, location, summary,\n"
        "  links: { ...typed link keys as above... },\n"
        "  skills, certifications, languages,\n"
        "  experience: [ { company, title, start_date, end_date, location, highlights } ],\n"
        "  projects: [ { name, link, description, technologies } ],\n"
        "  education: [ { institution, degree, field_of_study, start_date, end_date, grade } ],\n"
        "  awards, publications, volunteering,\n"
        "  preferences: { work_authorization, clearance, relocation, remote, salary_expectation }.\n\n"
        "Resume text:\n"
        "```\n" + resume_text + "\n```"
    )

def main():
    # 1) load key
    load_dotenv()
    api_key ="AIzaSyAJrmvM10sV7GxgzAwApFtGtR3ht6l3fY0"
    if not api_key:
        print("[error] GEMINI_API_KEY missing in environment/.env")
        return

    # 2) read pdf
    try:
        text = read_pdf_text(RESUME_PDF)
        if not text:
            print("[error] No text extracted from PDF (is it scanned?).")
            return
    except Exception as e:
        print(f"[error] reading PDF: {e}")
        return

    # 3) call Gemini with JSON response enforced
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(MODEL_NAME)
        prompt = build_prompt(text)
        resp = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        raw = resp.text if hasattr(resp, "text") else str(resp)
        # try to parse; if model ever wraps text, attempt a basic brace-slice
        try:
            data = json.loads(raw)
        except Exception:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                data = json.loads(raw[start:end+1])
            else:
                raise
    except Exception as e:
        print(f"[error] Gemini call/JSON parse failed: {e}")
        return

    # 4) write JSON to disk
    try:
        with open(OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"[ok] wrote {OUT_PATH}")
    except Exception as e:
        print(f"[error] writing {OUT_PATH}: {e}")

if __name__ == "__main__":
    main()
