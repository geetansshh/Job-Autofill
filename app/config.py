import os
from pathlib import Path

# load .env
try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(), override=False)
except Exception:
    pass

# overridable via env vars if you like
JOB_URL = os.getenv("JOB_URL", "https://example.com/your-job-apply-page")
PARSED_JSON = os.getenv("PARSED_JSON", "./parsed_resume.json")
RESUME_PDF = os.getenv("RESUME_PDF", "./data/resume.pdf")
RESUME_TXT = os.getenv("RESUME_TXT", "./data/resume.txt")
HEADLESS = os.getenv("HEADLESS", "true").lower() in ("1", "true", "yes", "y")
SCREENSHOT_DIR = os.getenv("SCREENSHOT_DIR", "./screenshots")
Path(SCREENSHOT_DIR).mkdir(parents=True, exist_ok=True)

# shared field synonyms
FIELD_SYNONYMS = {
    "first_name":   ["first name", "given name", "forename"],
    "last_name":    ["last name", "surname", "family name"],
    "full_name":    ["name", "full name", "your name"],
    "email":        ["email", "e-mail", "email address"],
    "phone":        ["phone", "mobile", "cell", "telephone"],
    "linkedin":     ["linkedin", "linkedin url", "linkedin profile"],
    "github":       ["github", "github url", "github profile"],
    "portfolio":    ["portfolio", "website", "personal site", "url"],
    "location":     ["city", "current location", "address", "location"],
    "cover_letter": ["cover letter", "covering letter", "why do you want to work here", "motivation", "statement"],
    "resume":       ["resume", "cv", "upload resume", "upload cv"],
}
KNOWN_KEYS = {k for k in FIELD_SYNONYMS.keys() if k not in ("resume",)}
