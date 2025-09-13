# generate_job_summary_and_cover_crawl4ai.py
# ------------------------------------------------------------
# pip install crawl4ai google-generativeai python-dotenv pypdf
# (optional) create .env with GEMINI_API_KEY=your_key
#
# Run:
#   python generate_job_summary_and_cover_crawl4ai.py
#
# Outputs: job_page.md, job_summary.txt, cover_letter.txt
# ------------------------------------------------------------

from __future__ import annotations
import asyncio, os, re, textwrap
from pathlib import Path
from output_config import OutputPaths

# ===================== EDIT THESE =====================
JOB_URL        = "https://job-boards.greenhouse.io/gomotive/jobs/8137073002?gh_src=my.greenhouse.search"     # <-- your job URL
RESUME_PATH    = "./data/resume.pdf"               # prefers .txt; falls back to .pdf
CANDIDATE_NAME = "Geetansh"                        # <-- your name (or None)
EXTRAS         = None                             # <-- extra instructions for cover letter (or None)
SUMMARY_ROLE_BULLETS  = 5   # bullets under ROLE SUMMARY
SUMMARY_ABOUT_BULLETS = 3   # bullets under ABOUT THE COMPANY
WORD_TARGET    = 160        # cover letter ~140–180 words
# OUTDIR removed - now using centralized output paths
# ======================================================

# (optional) load .env for GEMINI_API_KEY
try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(), override=False)
except Exception:
    pass


# ---------- Resume reading ----------
def read_resume_text(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Resume not found: {path}")

    if p.suffix.lower() in {".txt", ".md"}:
        return p.read_text(encoding="utf-8", errors="ignore")

    if p.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except Exception as e:
            raise RuntimeError("Install pypdf: pip install pypdf") from e
        reader = PdfReader(str(p))
        return "\n".join((page.extract_text() or "") for page in reader.pages)

    # last resort
    return p.read_text(encoding="utf-8", errors="ignore")


# ---------- Crawl4AI: fetch markdown (generic) ----------
async def _crawl_markdown_async(url: str) -> str:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    # light, generic config; prune low-signal blocks if available
    try:
        from crawl4ai.content_filter_strategy import PruningContentFilter
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
        md_gen = DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.4, threshold_type="fixed")
        )
        run_conf = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, markdown_generator=md_gen)
    except Exception:
        run_conf = CrawlerRunConfig(cache_mode=CacheMode.BYPASS)

    async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
        result = await crawler.arun(url=url, config=run_conf)

    # Defensive across versions
    md = getattr(result, "markdown", None)
    if isinstance(md, str):
        return md
    if md is not None:
        return getattr(md, "fit_markdown", None) or getattr(md, "raw_markdown", "") or ""
    md2 = getattr(result, "markdown_v2", None)
    if md2 and getattr(md2, "markdown_with_citations", None):
        return md2.markdown_with_citations or md2.raw_markdown or ""
    return ""


def crawl_markdown(url: str) -> str:
    return asyncio.run(_crawl_markdown_async(url))


# ---------- LLM (Gemini) ----------
def gen_with_gemini(prompt: str) -> str | None:
    api = os.getenv("GEMINI_API_KEY")
    if not api:
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=api)
        model = genai.GenerativeModel("gemini-1.5-flash")
        resp = model.generate_content(prompt)
        return (resp.text or "").strip()
    except Exception:
        return None


# ---------- Light title/company detection (optional) ----------
def guess_title_company_from_markdown(md: str) -> tuple[str, str]:
    lines = [l.strip() for l in md.splitlines() if l.strip()]
    title = ""
    company = ""

    # First heading-ish line as title
    for l in lines[:20]:
        if l.startswith("#"):
            title = l.lstrip("#").strip()
            break
    if not title and lines:
        title = lines[0][:120]

    # Try split patterns: "Role – Company" / "Role | Company" / "Role at Company"
    m = re.split(r"\s[-–|•]\s", title)
    if len(m) >= 2 and 2 <= len(m[-1]) <= 80:
        company = m[-1].strip()
        title = " - ".join(m[:-1]).strip()
    else:
        m2 = re.search(r"\b(.+?)\s+at\s+(.+)$", title, re.I)
        if m2:
            title = m2.group(1).strip()
            company = m2.group(2).strip()

    if not company:
        for l in lines[:30]:
            if l.startswith(("**","__")) and l.endswith(("**","__")) and 2 < len(l) < 80:
                company = l.strip("*_")
                break
    return title, company


# ---------- Prompts ----------
def build_summary_prompt(job_markdown: str,
                         detected_title: str,
                         detected_company: str,
                         about_bullets: int,
                         role_bullets: int) -> str:
    title_line = f"Detected Title: {detected_title}\n" if detected_title else ""
    company_line = f"Detected Company: {detected_company}\n" if detected_company else ""
    return textwrap.dedent(f"""
        You are given a JOB DESCRIPTION in Markdown. Produce a concise, job-focused summary.
        IMPORTANT: Base the summary ONLY on the job Markdown. Do NOT use candidate information.

        {title_line}{company_line}
        JOB DESCRIPTION (Markdown):
        \"\"\"{job_markdown[:25000]}\"\"\"

        OUTPUT FORMAT (exact):
        SUMMARY:
        ABOUT THE COMPANY:
        - (max {about_bullets} bullets)

        ROLE SUMMARY:
        - (max {role_bullets} bullets)
    """).strip()


def build_cover_prompt(job_markdown: str,
                       resume_text: str,
                       name: str | None,
                       extras: str | None,
                       word_target: int,
                       detected_company: str) -> str:
    name_line = f"Candidate: {name}\n" if name else ""
    extra_line = f"Additional instructions: {extras}\n" if extras else ""
    company_hint = f"Company: {detected_company}\n" if detected_company else ""
    return textwrap.dedent(f"""
        You are given a JOB DESCRIPTION in Markdown and a candidate RESUME in plain text.

        {name_line}{extra_line}{company_hint}
        JOB DESCRIPTION (Markdown):
        \"\"\"{job_markdown[:25000]}\"\"\"

        RESUME (plain text):
        \"\"\"{resume_text[:25000]}\"\"\"

        TASK:
        Write a COVER LETTER of ~{word_target} words (±20), confident and warm, specific to this role.
        - Use 2–4 concrete skills/achievements from the resume that match the JD.
        - Address the company if provided; otherwise keep greeting generic.
        - Avoid placeholders and generic fluff.
        - Close with a short call to action.

        OUTPUT FORMAT (exact):
        COVER LETTER:
        Paragraphs here
    """).strip()


# ---------- Fallbacks (no Gemini) ----------
def fallback_job_summary(job_md: str,
                         about_bullets: int,
                         role_bullets: int) -> str:
    lines = [l.strip() for l in job_md.splitlines() if l.strip()]
    about = []
    role  = []

    # Simple heuristic: pick lines mentioning "About", "Who we are" for company;
    # otherwise, early intro lines. For role, prefer bullet-looking or long, task-like lines.
    for ln in lines[:80]:
        if len(about) >= about_bullets:
            break
        if re.search(r"\babout\b|\bwho (we|i) are\b", ln, re.I):
            about.append(ln.lstrip("-*• ").strip())
    if not about:
        about = [l.lstrip("-*• ").strip() for l in lines[:about_bullets]]

    for ln in lines:
        if len(role) >= role_bullets:
            break
        score = (2 if ln.lstrip().startswith(("-", "*", "•")) else 0) + (1 if len(ln) > 50 else 0)
        if re.search(r"(responsib|require|experience|skills|you will|role|job description|what you)", ln, re.I):
            score += 1
        if score >= 1:
            role.append(ln.lstrip("-*• ").strip())
    if not role:
        role = [l.lstrip("-*• ").strip() for l in lines[:role_bullets]]

    out = "SUMMARY:\n"
    out += "ABOUT THE COMPANY:\n" + "\n".join(f"- {x}" for x in about[:about_bullets]) + "\n\n"
    out += "ROLE SUMMARY:\n" + "\n".join(f"- {x}" for x in role[:role_bullets])
    return out


def fallback_cover_letter(job_md: str, resume_text: str, name: str | None,
                          word_target: int, company_hint: str) -> str:
    jd = job_md.lower()
    cv = resume_text.lower()
    techs = ["selenium","appium","python","java","cypress","playwright","pytest",
             "jenkins","aws","gcp","azure","sql","rest","api","microservices","linux","kubernetes"]
    overlap = [t for t in techs if t in jd and t in cv]
    highlight = ", ".join(overlap[:4]) if overlap else "relevant tools and practices"

    greeting = f"Dear {company_hint} Hiring Team," if company_hint else "Dear Hiring Team,"
    who = name or "I"
    body = (
        f"I’m excited to apply for this role. My background includes hands-on experience with {highlight}, "
        f"which has helped improve automation reliability, speed up feedback cycles, and strengthen release quality.\n\n"
        "In prior projects I built and maintained automated test suites, collaborated closely with engineering and product "
        "to clarify acceptance criteria, and used CI/CD telemetry to focus testing where it mattered most. "
        "I enjoy translating requirements into robust, maintainable tests and thrive in agile, cross-functional teams.\n\n"
        "I’d welcome the chance to discuss how I can contribute to your roadmap. Thank you for your time and consideration."
    )
    return f"COVER LETTER:\n{greeting}\n\n{body}\n\nBest regards,\n{who}"


# ---------- Orchestration ----------
def generate(job_url: str,
             resume_path: str,
             *,
             name: str | None = None,
             extras: str | None = None,
             about_bullets: int = SUMMARY_ABOUT_BULLETS,
             role_bullets: int = SUMMARY_ROLE_BULLETS,
             word_target: int = WORD_TARGET) -> tuple[str, str, str]:
    # Get inputs
    resume_text = read_resume_text(resume_path)
    job_md = crawl_markdown(job_url)

    # Save the raw job page markdown
    OutputPaths.JOB_PAGE_MD.write_text(job_md, encoding="utf-8")

    # Light detection for nicer prompts
    detected_title, detected_company = guess_title_company_from_markdown(job_md)

    # 1) SUMMARY (job-focused only)
    summary_prompt = build_summary_prompt(
        job_markdown=job_md,
        detected_title=detected_title,
        detected_company=detected_company,
        about_bullets=about_bullets,
        role_bullets=role_bullets,
    )
    summary_ai = gen_with_gemini(summary_prompt)
    if summary_ai and summary_ai.strip().startswith("SUMMARY:"):
        summary = summary_ai.strip()
    else:
        summary = fallback_job_summary(job_md, about_bullets, role_bullets)

    # 2) COVER LETTER (job + resume)
    cover_prompt = build_cover_prompt(
        job_markdown=job_md,
        resume_text=resume_text,
        name=name,
        extras=extras,
        word_target=word_target,
        detected_company=detected_company,
    )
    cover_ai = gen_with_gemini(cover_prompt)
    if cover_ai and cover_ai.strip().startswith("COVER LETTER:"):
        cover = cover_ai.strip()
    elif cover_ai:
        cover = "COVER LETTER:\n" + cover_ai.strip()
    else:
        cover = fallback_cover_letter(job_md, resume_text, name, word_target, detected_company)

    return job_md, summary, cover


# ---------- Run (no argparse) ----------
def main():
    job_md, summary, cover = generate(
        JOB_URL,
        RESUME_PATH,
        name=CANDIDATE_NAME,
        extras=EXTRAS,
        about_bullets=SUMMARY_ABOUT_BULLETS,
        role_bullets=SUMMARY_ROLE_BULLETS,
        word_target=WORD_TARGET,
    )

    OutputPaths.JOB_SUMMARY.write_text(summary, encoding="utf-8")
    OutputPaths.COVER_LETTER.write_text(cover, encoding="utf-8")

    print("\nSaved files:")
    print(f" - {OutputPaths.JOB_PAGE_MD}       (raw job Markdown)")
    print(f" - {OutputPaths.JOB_SUMMARY}   (job-focused summary)")
    print(f" - {OutputPaths.COVER_LETTER}  (tailored cover letter)")

    print("\n=== SUMMARY (job-focused) ===\n")
    print(summary)
    print("\n=== COVER LETTER ===\n")
    print(cover)

if __name__ == "__main__":
    main()
