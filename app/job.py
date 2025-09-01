from typing import Dict

def scrape_job_text(page) -> Dict[str, str]:
    data = {"title":"", "company":"", "location":"", "body":""}
    try:
        data["title"] = (page.locator("h1, h2").first.inner_text() or "").strip()
    except Exception: pass
    for sel in ["[data-company]", ".company", ".organization", "header [itemprop='hiringOrganization']", "meta[name='og:site_name']"]:
        try:
            t = page.locator(sel).first.inner_text()
            if t and t.strip():
                data["company"] = t.strip(); break
        except Exception:
            continue
    if not data["company"]:
        try: data["company"] = (page.title() or "").split("|")[0].strip()
        except Exception: pass
    try:
        data["location"] = (page.locator(":text('Location') + *").first.inner_text() or "").strip()
    except Exception: pass
    try:
        data["body"] = (page.locator("main").inner_text() or "").strip()
    except Exception:
        try: data["body"] = (page.locator("body").inner_text() or "").strip()
        except Exception: data["body"] = ""
    return data
