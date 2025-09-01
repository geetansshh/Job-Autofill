from typing import Any, Dict, List, Optional
from .config import FIELD_SYNONYMS

def label_for(frame, el):
    try:
        lid = el.get_attribute("id")
        if lid:
            lbl = frame.query_selector(f'label[for="{lid}"]')
            if lbl:
                t = (lbl.inner_text() or "").strip()
                if t: return t
    except: pass
    try:
        return frame.evaluate("""(e)=>{
            let p = e.parentElement;
            while(p){
                if(p.tagName && p.tagName.toLowerCase()==='label'){ return p.innerText || ''; }
                p = p.parentElement;
            }
            return '';
        }""", el).strip()
    except: return ""

def guess_field_key(label_text: str, name_attr: str, aria_label: str, placeholder: str) -> Optional[str]:
    hay = " ".join(x for x in [
        (label_text or ""), (name_attr or ""), (aria_label or ""), (placeholder or "")
    ] if x).strip().lower()
    if not hay: return None
    for key, syns in FIELD_SYNONYMS.items():
        for s in syns + [key.replace("_"," ")]:
            if s in hay:
                return key
    return None

def _all_frames(page):
    frs = [page.main_frame]
    seen = {id(page.main_frame)}
    for fr in page.frames:
        if id(fr) not in seen:
            frs.append(fr)
    return frs

def collect_form_fields(page) -> List[Dict[str, Any]]:
    """
    Discover native inputs/selects/textareas, radios/checkboxes, and ARIA comboboxes.
    Emits:
      frame, el, widget ('input'|'textarea'|'select'|'combobox'|'checkbox'|'radio'),
      type, label, placeholder, name, aria, key, required, options, multiple, group_name
    """
    fields = []
    for frame in _all_frames(page):
        # native controls
        for el in frame.query_selector_all("input, select, textarea"):
            try:
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                typ = (el.get_attribute("type") or tag or "").lower()
                lbl = label_for(frame, el) or ""
                ph  = el.get_attribute("placeholder") or ""
                name = el.get_attribute("name") or ""
                aria = el.get_attribute("aria-label") or ""

                key = guess_field_key(lbl, name, aria, ph)
                required = False
                try: required = bool(el.get_attribute("required"))
                except: pass
                try:
                    if not required:
                        required = (el.get_attribute("aria-required") == "true")
                except: pass
                if not required and "*" in (lbl or ""):
                    required = True

                widget = "input" if tag == "input" else tag
                options, multiple, group_name = [], False, None

                if tag == "select":
                    widget = "select"
                    try: multiple = bool(el.get_attribute("multiple"))
                    except: pass
                    try:
                        for o in el.query_selector_all("option"):
                            v = o.get_attribute("value") or ""
                            lab = (o.inner_text() or "").strip() or v
                            options.append((v, lab))
                    except: pass

                if tag == "input" and el.get_attribute("list"):
                    dl_id = el.get_attribute("list")
                    dl = frame.query_selector(f"#{dl_id}")
                    if dl:
                        try:
                            for o in dl.query_selector_all("option"):
                                v = o.get_attribute("value") or ""
                                lab = (o.inner_text() or v).strip()
                                options.append((v, lab))
                            widget = "select"
                        except: pass

                if typ in ("checkbox", "radio"):
                    group_name = name
                    widget = typ

                fields.append({
                    "frame": frame, "el": el, "widget": widget, "type": typ,
                    "label": lbl, "placeholder": ph, "name": name, "aria": aria,
                    "key": key, "required": required, "options": options,
                    "multiple": multiple, "group_name": group_name
                })
            except Exception:
                continue

        # ARIA comboboxes (React-Select/Select2/custom)
        for el in frame.query_selector_all('[role="combobox"], [aria-haspopup="listbox"]'):
            try:
                if el.evaluate("e => ['input','textarea','select'].includes(e.tagName.toLowerCase())"):
                    continue
            except Exception:
                pass
            try:
                lbl = label_for(frame, el) or (el.get_attribute("aria-label") or "")
                name = el.get_attribute("name") or ""
                ph   = el.get_attribute("placeholder") or ""
                aria = el.get_attribute("aria-label") or ""
                key  = guess_field_key(lbl, name, aria, ph)
                required = "*" in (lbl or "")
                fields.append({
                    "frame": frame, "el": el, "widget": "combobox", "type": "text",
                    "label": lbl, "placeholder": ph, "name": name, "aria": aria,
                    "key": key, "required": required, "options": [], "multiple": False,
                    "group_name": None
                })
            except Exception:
                continue
    return fields

def read_current_value(f: Dict[str, Any]) -> str:
    el, widget = f["el"], f["widget"]
    try:
        if widget == "select":
            return el.evaluate("e => e.value || ''") or ""
        return el.input_value()
    except Exception:
        try: return el.evaluate("e => e.value || ''") or ""
        except Exception: return ""
