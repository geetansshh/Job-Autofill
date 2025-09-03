from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import os, re, difflib, math, time
from pathlib import Path

# --- CONFIG ---
FORM_URL     = "https://job-boards.greenhouse.io/capco/jobs/7188625"  # your job apply URL                            
RESUME_PDF   = "./data/resume.pdf"                                    # otherwise we’ll read PDF text
HEADLESS     = False
SLOW_MO_MS   = 200

# ---------- load .env for GEMINI_API_KEY ----------
try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(), override=False)
except Exception:
    pass



# ---------- heuristics (when no LLM or LLM says UNKNOWN) ----------
YES = {"yes","y","true","ok","okay","sure"}
NO  = {"no","n","false"}

def parse_months_free_text(s: str) -> float | None:
    s = s.lower().strip()
    if not s:
        return None
    if any(x in s for x in ("fresher","no exp","0 exp","zero")):
        return 0.0
    nums = re.findall(r"(\d+(?:\.\d+)?)", s)
    if not nums:
        return None
    n = float(nums[0])
    if "year" in s or "yr" in s or "yrs" in s:
        return n * 12.0
    return n  # assume months

def months_range_from_label(label: str):
    t = label.lower().strip()
    m = re.search(r"less\s+than\s+(\d+(?:\.\d+)?)\s*(month|year)", t)
    if m:
        n = float(m.group(1)); unit = m.group(2)
        hi = n * (12 if "year" in unit else 1)
        return (0.0, hi - 0.001)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(month|year).{0,10}to.{0,10}(\d+(?:\.\d+)?)\s*(month|year)", t)
    if m:
        a, ua, b, ub = float(m.group(1)), m.group(2), float(m.group(3)), m.group(4)
        lo = a * (12 if "year" in ua else 1)
        hi = b * (12 if "year" in ub else 1)
        if hi < lo: lo, hi = hi, lo
        return (lo, hi)
    m = re.search(r"(over|more than|≥|>=)\s*(\d+(?:\.\d+)?)\s*(month|year)", t)
    if m:
        n = float(m.group(2)); unit = m.group(3)
        lo = n * (12 if "year" in unit else 1)
        return (lo + 0.001, math.inf)
    if "fresher" in t:
        return (0.0, 0.0)
    return None

STOP = {"how","many","do","you","have","good","any","in","of","the","are","to","work","with","is","your","what","current","preferred","can","join","soon","when","we","year","did","complete","completed","graduation","knowledge","experience"}

# ---------- UI helpers ----------
def open_menu_and_get_options(page, combo):
    combo.scroll_into_view_if_needed()
    combo.click()
    listbox = page.get_by_role("listbox").last
    listbox.wait_for(state="visible", timeout=6000)
    # collect options
    options = []
    # prefer role=option
    items = listbox.get_by_role("option")
    for i in range(items.count()):
        it = items.nth(i)
        if it.get_attribute("aria-disabled") == "true":
            continue
        lab = (it.inner_text() or "").strip()
        if not lab or lab.lower().startswith("select"):
            continue
        val = it.get_attribute("data-value") or it.get_attribute("value") or lab
        options.append((val, lab))
    # fallback generic nodes if empty
    if not options:
        cand = listbox.locator("[data-value], li, div, span")
        for i in range(min(cand.count(), 200)):
            it = cand.nth(i)
            if not it.is_visible(): continue
            lab = (it.inner_text() or "").strip()
            if not lab or lab.lower().startswith("select"): continue
            val = it.get_attribute("data-value") or it.get_attribute("value") or lab
            options.append((val, lab))
    # de-dupe
    seen, uniq = set(), []
    for v,l in options:
        k = (str(v).strip(), str(l).strip())
        if k not in seen:
            seen.add(k); uniq.append(k)
    return uniq, listbox

def click_option_in_open_menu(listbox, value_or_label: str) -> bool:
    try:
        esc = re.escape(str(value_or_label))
        cand = listbox.get_by_role("option", name=re.compile(esc, re.I))
        if cand.count():
            cand.first.click()
            return True
    except: pass
    try:
        items = listbox.locator("[role='option'], [data-value], li, div")
        for i in range(min(items.count(), 200)):
            it = items.nth(i)
            if not it.is_visible(): continue
            lab = (it.inner_text() or "").strip()
            val = it.get_attribute("data-value") or it.get_attribute("value") or lab
            if str(value_or_label).lower() == str(val).lower() or str(value_or_label).lower() in lab.lower():
                it.click(); return True
    except: pass
    return False

def get_label_for(page, el):
    try:
        aria = el.get_attribute("aria-label")
        if aria and aria.strip(): return aria.strip()
    except: pass
    try:
        cid = el.get_attribute("id")
        if cid:
            lbl = page.locator(f'label[for="{cid}"]').first
            if lbl.count(): 
                t = (lbl.inner_text() or "").strip()
                if t: return t
    except: pass
    # fallback: nearest visible label text above
    try:
        lbl = el.locator("xpath=preceding::label[1]").first
        if lbl.count():
            t = (lbl.inner_text() or "").strip()
            if t: return t
    except: pass
    return "Select"

# ---------- user prompt fallback ----------
def choose_from_user(label: str, options: list[tuple[str,str]]) -> str | None:
    print(f"\n{label}")
    for i,(_,lab) in enumerate(options,1):
        print(f"  {i}. {lab}")
    ans = input("Type number (1..N) or free-text (e.g. '1 year', 'yes', 'Bengaluru'): ").strip()
    if ans.isdigit():
        idx = int(ans)
        if 1 <= idx <= len(options):
            return options[idx-1][0]
    # free-text → try to map
    # a) yes/no
    if ans.lower() in YES or ans.lower() in NO:
        yn = "yes" if ans.lower() in YES else "no"
        for v,l in options:
            if l.lower()==yn:
                return v
    # b) numeric months/years → bucket
    months = parse_months_free_text(ans)
    if months is not None:
        scored = []
        for v,l in options:
            r = months_range_from_label(l)
            if r:
                lo,hi = r
                if lo <= months <= hi:
                    return v
                d = min(abs(months-lo), abs(months-hi)) if hi<math.inf else max(0, months-lo)
                scored.append((d,v))
        if scored:
            scored.sort(key=lambda x:x[0])
            return scored[0][1]
    # c) fuzzy label match
    labels = [l for _,l in options]
    best = difflib.get_close_matches(ans, labels, n=1, cutoff=0.45)
    if best:
        for v,l in options:
            if l == best[0]:
                return v
    # d) substring unique
    matches = [v for v,l in options if ans.lower() in l.lower()]
    if len(matches)==1:
        return matches[0]
    return None

# ---------- main ----------
def main():
    if not FORM_URL.startswith("http"):
        print("Please set FORM_URL to a real URL."); return


    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS, slow_mo=SLOW_MO_MS)
        ctx = browser.new_context(record_video_dir="videos")
        page = ctx.new_page()
        print(f"[open] {FORM_URL}")
        try:
            page.goto(FORM_URL, timeout=60000, wait_until="domcontentloaded")
        except PWTimeout:
            print("[error] page load timeout"); return
        page.wait_for_timeout(1200)

        # 1) Native selects (rare on Greenhouse)
        selects = page.locator("select")
        for i in range(selects.count()):
            sel = selects.nth(i)
            label = get_label_for(page, sel)
            # read options
            options = []
            o = sel.locator("option")
            for j in range(o.count()):
                opt = o.nth(j)
                lab = (opt.inner_text() or "").strip()
                if not lab or lab.lower().startswith("select"):
                    continue
                val = opt.get_attribute("value") or lab
                options.append((val, lab))
            if not options:
                continue
            chosen_val= choose_from_user(label, options)
            if chosen_val:
                try:
                    sel.select_option(value=chosen_val)
                except:
                    # fallback by label
                    try:
                        sel.select_option(label=chosen_val)
                    except:
                        pass
            page.wait_for_timeout(150)

        # 2) Comboboxes (Greenhouse/React-Select)
        combos = page.get_by_role("combobox")
        for i in range(combos.count()):
            combo = combos.nth(i)
            label = get_label_for(page, combo)

            try:
                options, listbox = open_menu_and_get_options(page, combo)
            except PWTimeout:
                print(f"[warn] Can't open list for: {label}")
                continue

            if not options:
                print(f"[warn] No visible options for: {label}")
                try: page.keyboard.press("Escape")
                except: pass
                continue

            chosen_val = choose_from_user(label, options)  # ask user first
            # ask user last
            if not chosen_val:
                chosen_val = choose_from_user(label, options)

            # click the chosen option
            if chosen_val:
                ok = click_option_in_open_menu(listbox, chosen_val)
                if not ok:
                    # try clicking by label text
                    for v,l in options:
                        if v == chosen_val:
                            ok = click_option_in_open_menu(listbox, l)
                            if ok: break

            # close menu if still open
            try: page.keyboard.press("Escape")
            except: pass
            page.wait_for_timeout(200)

        print("[done] dropdowns handled. You can submit if everything looks good.")
        page.wait_for_timeout(1500)
        ctx.close()
        browser.close()

if __name__ == "__main__":
    main()
