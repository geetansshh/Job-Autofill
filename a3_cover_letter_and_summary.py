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

# Suppress Google API/GRPC warnings
os.environ['GRPC_VERBOSITY'] = 'ERROR'
os.environ['GRPC_TRACE'] = ''
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['GOOGLE_CLOUD_DISABLE_GRPC_FOR_REST'] = 'true'

from output_config import OutputPaths

# ===================== EDIT THESE =====================
JOB_URL        = "https://job-boards.greenhouse.io/hackerrank/jobs/7211528?gh_jid=7211528&gh_src=1836e8621us"     # <-- your job URL
RESUME_PATH    = "./data/Geetansh_resume.pdf"               # prefers .txt; falls back to .pdf
CANDIDATE_NAME = "Geetansh"                        # <-- your name (or None)
EXTRAS         = None                             # <-- extra instructions for cover letter (or None)
SUMMARY_ROLE_BULLETS  = 5   # bullets under ROLE SUMMARY
SUMMARY_ABOUT_BULLETS = 3   # bullets under ABOUT THE COMPANY
WORD_TARGET    = 160        # cover letter ~140–180 words
REQUIRE_USER_APPROVAL = False   # Set to False to skip review and auto-save
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
        print("❌ No GEMINI_API_KEY found in environment")
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=api)
        model = genai.GenerativeModel("gemini-2.5-flash-lite")
        resp = model.generate_content(prompt)
        print("✅ Successfully used Gemini AI")
        return (resp.text or "").strip()
    except Exception as e:
        print(f"❌ Gemini API error: {str(e)[:100]}...")
        return None


# ---------- Markdown cleaning ----------
def clean_job_markdown(md: str) -> str:
    """Clean markdown to remove navigation, links, and noise before AI processing"""
    lines = md.split('\n')
    cleaned_lines = []
    
    for line in lines:
        line = line.strip()
        
        # Skip empty lines
        if not line:
            continue
            
        # Skip image/link markdown
        if line.startswith('[![') or line.startswith('[]('):
            continue
            
        # Skip "Apply Now" and navigation
        if line in ['Apply Now', 'View All Jobs', 'Share on Linkedin', 'Share on Facebook', 'Share on Twitter', 'Share on Whatsapp']:
            continue
            
        # Clean up markdown formatting but keep important text
        # Remove image markdown but keep link text
        line = re.sub(r'\[!\[.*?\]\(.*?\)\]\(.*?\)', '', line)
        line = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', line)
        
        cleaned_lines.append(line)
    
    return '\n'.join(cleaned_lines)

# ---------- Light title/company detection (improved) ----------
def guess_title_company_from_markdown(md: str) -> tuple[str, str]:
    lines = [l.strip() for l in md.splitlines() if l.strip()]
    title = ""
    company = ""
    
    # Look for company name first - check for common patterns
    company_patterns = [
        r'About Us.*?Sense is',  # "About Us" followed by "Sense is"
        r'Founded in \d+, (.*?) is',  # "Founded in YEAR, COMPANY is"
        r'(Sense|SenseHQ) is a',  # Direct mention of Sense/SenseHQ
    ]
    
    for pattern in company_patterns:
        match = re.search(pattern, md, re.IGNORECASE | re.DOTALL)
        if match and 'Sense' in match.group(0):
            company = "Sense"
            break
    
    # Look for job title - avoid generic sections like "What You'll Do", "Perks & Benefits"
    # Focus on actual job titles that appear near the top
    avoid_titles = {
        "what you'll do", "job description", "job requirement", "perks & benefits", 
        "about us", "apply now", "view all jobs", "share on", "powered by"
    }
    
    # Try to find job title in the first few meaningful lines
    for i, line in enumerate(lines[:15]):
        line_clean = line.lower().strip("*#_- ")
        
        # Skip navigation, headers, and generic sections
        if any(avoid in line_clean for avoid in avoid_titles):
            continue
        if line.startswith(('[![', '[', 'https://', 'Apply Now')):
            continue
        if len(line_clean) < 5 or len(line_clean) > 100:
            continue
            
        # Look for job title patterns
        job_keywords = ['intern', 'engineer', 'developer', 'analyst', 'manager', 'specialist', 'coordinator']
        if any(keyword in line_clean for keyword in job_keywords):
            title = line.strip("*#_- ")
            break
    
    # Fallback: if no title found, look for the first substantial line
    if not title:
        for line in lines[:10]:
            if (5 < len(line) < 100 and 
                not line.startswith(('[![', '[', 'https://')) and
                not any(avoid in line.lower() for avoid in avoid_titles)):
                title = line.strip("*#_- ")
                break
    
    # Fallback company detection
    if not company:
        for line in lines[:30]:
            if 'sense' in line.lower() and len(line) < 100:
                if 'sense is' in line.lower():
                    company = "Sense"
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
        You are analyzing a JOB DESCRIPTION to extract key information. Focus on actual job content, not website navigation or formatting.

        {title_line}{company_line}
        JOB DESCRIPTION (Markdown):
        \"\"\"{job_markdown[:25000]}\"\"\"

        INSTRUCTIONS:
        1. For ABOUT THE COMPANY: Extract meaningful facts about the company's business, mission, size, or industry
        2. For ROLE SUMMARY: Extract specific job responsibilities, requirements, or tasks mentioned in the job description
        3. IGNORE: Navigation links, social media buttons, "Apply Now" buttons, website headers/footers, image tags
        4. Focus on substantive content about the job and company

        OUTPUT FORMAT (exact):
        SUMMARY:
        ABOUT THE COMPANY:
        - (max {about_bullets} concise bullets, each under 80 characters)

        ROLE SUMMARY:
        - (max {role_bullets} brief job tasks/requirements, each under 80 characters)
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
        You are writing a professional cover letter for a job application.

        {name_line}{extra_line}{company_hint}
        JOB DESCRIPTION (Markdown):
        \"\"\"{job_markdown[:25000]}\"\"\"

        RESUME (plain text):
        \"\"\"{resume_text[:25000]}\"\"\"

        INSTRUCTIONS:
        1. Extract the actual job title, responsibilities, and requirements from the job description
        2. Ignore website navigation, headers, "Apply Now" buttons, and formatting elements
        3. Focus on technical skills mentioned: Python, SQL, APIs, LLM prompts, MySQL, etc.
        4. Match the candidate's experience with Python, web scraping, data pipelines, and software engineering
        5. Write a professional, specific cover letter addressing the real job content

        REQUIREMENTS:
        - Address the letter to "{detected_company} Hiring Team" if company provided
        - ~{word_target} words (±20)
        - Mention 2-3 specific technical skills that match the job
        - Reference relevant experience from the resume
        - Professional and confident tone

        OUTPUT FORMAT (exact):
        COVER LETTER:
        [Your cover letter content here]
    """).strip()


# ---------- Fallbacks (no Gemini) ----------
def fallback_job_summary(job_md: str,
                         about_bullets: int,
                         role_bullets: int) -> str:
    lines = [l.strip() for l in job_md.splitlines() if l.strip()]
    about = []
    role = []

    # Extract company info - look for substantial paragraphs about the company
    in_about_section = False
    for i, ln in enumerate(lines):
        if len(about) >= about_bullets:
            break
        
        # Detect about section
        if re.search(r"^about us$|^who we are$", ln, re.I):
            in_about_section = True
            continue
        
        # If in about section or line mentions company info
        if (in_about_section or 
            re.search(r"sense is|founded in|startup|employees|customers|funding", ln, re.I)):
            
            # Skip short lines and apply buttons, keep lines concise
            if 30 < len(ln) < 120 and "apply now" not in ln.lower():
                # Truncate long lines to keep bullets concise
                bullet_text = ln.lstrip("-*• ").strip()
                if len(bullet_text) > 80:
                    bullet_text = bullet_text[:75] + "..."
                about.append(bullet_text)
                in_about_section = False  # Only take next substantial line after header
    
    # Extract role info - look for job requirements and responsibilities
    in_job_section = False
    for ln in lines:
        if len(role) >= role_bullets:
            break
            
        # Detect job sections
        if re.search(r"job description|what you'll do|requirements|looking for", ln, re.I):
            in_job_section = True
            continue
            
        # Look for bullet points or substantial job-related content
        if (ln.startswith(("*", "-", "•")) or 
            re.search(r"develop|write|contribute|work with|python|sql|api|llm", ln, re.I)):
            
            if 20 < len(ln) < 150:
                # Keep role bullets concise
                bullet_text = ln.lstrip("-*• ").strip()
                if len(bullet_text) > 80:
                    bullet_text = bullet_text[:75] + "..."
                role.append(bullet_text)

    # Fill with fallback content if needed
    if not about:
        about = [l for l in lines[:10] if len(l) > 30 and "apply" not in l.lower()][:about_bullets]
    if not role:
        role = [l for l in lines if len(l) > 30 and any(kw in l.lower() for kw in ["python", "sql", "develop", "work"])][:role_bullets]

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

    # Clean markdown for AI processing
    job_md_clean = clean_job_markdown(job_md)

    # Light detection for nicer prompts (use cleaned version)
    detected_title, detected_company = guess_title_company_from_markdown(job_md_clean)

    # 1) SUMMARY (job-focused only)
    summary_prompt = build_summary_prompt(
        job_markdown=job_md_clean,
        detected_title=detected_title,
        detected_company=detected_company,
        about_bullets=about_bullets,
        role_bullets=role_bullets,
    )
    summary_ai = gen_with_gemini(summary_prompt)
    if summary_ai and summary_ai.strip().startswith("SUMMARY:"):
        summary = summary_ai.strip()
        print("✅ Using AI-generated summary")
    else:
        summary = fallback_job_summary(job_md_clean, about_bullets, role_bullets)
        print("⚠️ Using fallback summary (AI failed)")
        if summary_ai:
            print(f"AI output was: {summary_ai[:100]}...")

    # 2) COVER LETTER (job + resume)
    cover_prompt = build_cover_prompt(
        job_markdown=job_md_clean,
        resume_text=resume_text,
        name=name,
        extras=extras,
        word_target=word_target,
        detected_company=detected_company,
    )
    cover_ai = gen_with_gemini(cover_prompt)
    if cover_ai and cover_ai.strip().startswith("COVER LETTER:"):
        cover = cover_ai.strip()
        print("✅ Using AI-generated cover letter")
    elif cover_ai:
        cover = "COVER LETTER:\n" + cover_ai.strip()
        print("✅ Using AI-generated cover letter (formatted)")
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

    detected_title, detected_company = guess_title_company_from_markdown(job_md)
    
    print("\n" + "="*80)
    print(f"JOB TITLE: {detected_title or 'Not detected'}")
    print(f"COMPANY: {detected_company or 'Not detected'}")
    print("="*80)
    print("\nJOB SUMMARY:")
    print(summary)
    print("\n" + "="*80)
    print("\nCOVER LETTER (preview):")
    print(cover)
    print("="*80)

    if REQUIRE_USER_APPROVAL:
        proceed = input("\nDoes this look correct? Proceed to next step? (yes/no): ").strip().lower()
        if proceed != "yes":
            print("❌ Exiting at user request.")
            import sys
            sys.exit(0)
        print("✅ User approved. Saving files...")
    else:
        print("✅ Auto-saving files (user approval disabled)...")

    OutputPaths.JOB_SUMMARY.write_text(summary, encoding="utf-8")
    OutputPaths.COVER_LETTER.write_text(cover, encoding="utf-8")

    print("\nSaved files:")
    print(f" - {OutputPaths.JOB_PAGE_MD}       (raw job Markdown)")
    print(f" - {OutputPaths.JOB_SUMMARY}   (job-focused summary)")
    print(f" - {OutputPaths.COVER_LETTER}  (tailored cover letter)")

if __name__ == "__main__":
    main()
