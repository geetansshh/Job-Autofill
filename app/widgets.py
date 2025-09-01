import re, time
from typing import List, Tuple

def _scroll_and_focus(frame, el):
    try: el.scroll_into_view_if_needed(timeout=1500)
    except Exception: pass
    try: el.focus()
    except Exception:
        try: frame.evaluate("(e)=>e.focus&&e.focus()", el)
        except Exception: pass

def _open_menu(page, frame, el):
    _scroll_and_focus(frame, el)
    try: el.click(timeout=1500)
    except Exception:
        try: frame.evaluate("(e)=>{ (e.parentElement||e).click?.() }", el)
        except Exception: pass
    for key in ("Enter","ArrowDown"," "):
        try: el.press(key)
        except Exception: pass
    time.sleep(0.15)

def _visible_option_nodes(page):
    selectors = [
        '[role="listbox"] [role="option"]',
        '[role="option"]',
        'ul[role="listbox"] li',
        '.Select-menu-outer .Select-option',
        '[class*="menu"] [class*="option"]',
        '[data-value]'
    ]
    nodes = []
    for sel in selectors:
        try:
            loc = page.locator(sel)
            count = loc.count()
            for i in range(min(count, 200)):
                it = loc.nth(i)
                try:
                    if it.is_visible():
                        nodes.append(it)
                except Exception:
                    continue
        except Exception:
            continue
    return nodes

def open_combobox_and_get_options(page, frame, el) -> List[Tuple[str,str]]:
    _open_menu(page, frame, el)
    time.sleep(0.1)
    items = _visible_option_nodes(page)
    options = []
    for it in items:
        try:
            lab = it.inner_text().strip()
            if not lab: continue
            val = it.get_attribute("data-value") or it.get_attribute("value") or lab
            options.append((val, lab))
        except Exception:
            continue
    seen = set(); uniq=[]
    for v,l in options:
        k=(v,l)
        if k in seen: continue
        seen.add(k); uniq.append((v,l))
    return uniq

def select_in_combobox(page, frame, el, picks):
    """Strict selection by clicking listed options (no free typing)."""
    if not isinstance(picks, list):
        picks = [picks]
    for pick in picks:
        _open_menu(page, frame, el)
        picked = False
        escaped = re.escape(str(pick))
        try:
            loc = page.get_by_role("option", name=re.compile(escaped, re.I))
            if loc.count() > 0:
                loc.first.click(); picked = True
        except Exception:
            pass
        if not picked:
            for it in _visible_option_nodes(page):
                try:
                    lab = it.inner_text().strip()
                    if not lab: continue
                    if str(pick).lower() == lab.lower() or str(pick).lower() in lab.lower():
                        it.click(); picked = True; break
                except Exception:
                    continue
        if not picked:
            print(f"[warn] Could not find option '{pick}' in dropdown.")
        time.sleep(0.1)
