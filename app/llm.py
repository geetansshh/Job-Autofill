import os
from typing import Any, Dict, Tuple, Optional
from .config import JOB_URL
from .profile import read_resume_text

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
    out = gemini_text(
        f"You extract a single resume field.\n"
        f"Field: {field_key}\n"
        f"Resume text:\n-----\n{resume_text[:25000]}\n-----\n"
        f"Reply with ONLY the value. If unknown, reply: UNKNOWN"
    )
    if not out or out.upper() == "UNKNOWN": return None
    return out

def draft_summary_and_letter(job: Dict[str,str], resume_text: str, contact: Dict[str,Any]) -> Tuple[str,str]:
    if not os.getenv("GEMINI_API_KEY") or not (job.get("body") and resume_text):
        summary = f"Role: {job.get('title','(unknown)')} | Company: {job.get('company','(unknown)')} | Location: {job.get('location','')}".strip()
        letter = f"Dear Hiring Team,\n\nI’m interested in the {job.get('title','')} role at {job.get('company','')}. My background aligns with the required skills and I’d love to contribute.\n\nBest,\n{contact.get('full_name') or ''}"
        return summary, letter
    out = gemini_text(
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
    ) or ""
    summary, letter = "", ""
    if "COVER LETTER:" in out:
        p = out.split("COVER LETTER:", 1)
        summary = p[0].replace("SUMMARY:", "").strip()
        letter = p[1].strip()
    else:
        summary = out.strip()
        letter = ""
    if not letter:
        letter = f"Dear Hiring Team,\n\nI’m excited about the {job.get('title','')} role at {job.get('company','')}. I bring relevant skills and a strong interest in your mission.\n\nBest regards,\n{contact.get('full_name') or ''}"
    return summary, letter
