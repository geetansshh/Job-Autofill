import json, os
from pathlib import Path
from typing import Any, Dict, Optional
from .config import PARSED_JSON, RESUME_PDF, RESUME_TXT

def load_profile() -> Dict[str, Any]:
    if not os.path.exists(PARSED_JSON):
        raise FileNotFoundError(f"parsed resume JSON not found: {PARSED_JSON}")
    return json.loads(Path(PARSED_JSON).read_text(encoding="utf-8"))

def _first_str(val):
    if val is None: return None
    if isinstance(val, str): return val.strip() or None
    if isinstance(val, list):
        for x in val:
            if isinstance(x, str) and x.strip():
                return x.strip()
    return None

def extract_contact(profile: Dict[str, Any]) -> Dict[str, Optional[str]]:
    name = _first_str(profile.get("name") or profile.get("full_name"))
    first_name, last_name = None, None
    if name:
        parts = [p for p in name.strip().split() if p]
        if parts:
            first_name = parts[0]
            if len(parts) > 1: last_name = parts[-1]
    email = _first_str(profile.get("email") or _first_str(profile.get("emails")))
    phone = _first_str(profile.get("phone") or _first_str(profile.get("phones")))

    links_block = profile.get("links") or {}
    candidates = []
    if isinstance(links_block, dict):
        for k, v in links_block.items():
            if isinstance(v, str) and v.strip():
                candidates.append((k.lower(), v.strip()))
    for k, v in profile.items():
        if isinstance(v, str) and v.strip() and (k.endswith("_url") or k.endswith("_link")):
            candidates.append((k.lower(), v.strip()))
    for k, v in profile.items():
        if isinstance(v, str) and ("http://" in v or "https://" in v):
            candidates.append((k.lower(), v.strip()))
    linkedin = github = portfolio = website = None
    for k, url in candidates:
        low = f"{k} {url}".lower()
        if "linkedin.com" in low and not linkedin: linkedin = url
        elif "github.com" in low and not github: github = url
        elif any(s in low for s in ["portfolio","personal site","personal website"]) and not portfolio:
            portfolio = url
        elif not website and ("http://" in url or "https://" in url):
            website = url
    location = _first_str(profile.get("location"))
    return {
        "full_name": name, "first_name": first_name, "last_name": last_name,
        "email": email, "phone": phone, "linkedin": linkedin, "github": github,
        "portfolio": portfolio or website, "location": location,
    }

def read_resume_text() -> str:
    if os.path.exists(RESUME_TXT):
        try:
            return Path(RESUME_TXT).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass
    if os.path.exists(RESUME_PDF):
        try:
            from pypdf import PdfReader
            reader = PdfReader(RESUME_PDF)
            return "\n".join([(p.extract_text() or "") for p in reader.pages]).strip()
        except Exception:
            return ""
    return ""
