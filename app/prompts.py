import re
from typing import List, Tuple, Optional

def prompt_user_for(label: str, hint: str = "") -> Optional[str]:
    try:
        msg = f"[input needed] {label}"
        if hint: msg += f" ({hint})"
        msg += ": "
        val = input(msg).strip()
        return val or None
    except KeyboardInterrupt:
        return None

def prompt_user_choice(label: str, options: List[Tuple[str, str]]) -> Optional[str]:
    options = [(v or l, l) for v, l in options if (v or l)]
    if not options:
        print(f"[warn] No options found for: {label}")
        return None
    print(f"[choose] {label}")
    for i, (val, lab) in enumerate(options, 1):
        print(f"  {i}. {lab} [{val}]")
    while True:
        ans = input("Enter number or type an option (blank to skip): ").strip()
        if not ans: return None
        if ans.isdigit():
            idx = int(ans)
            if 1 <= idx <= len(options):
                return options[idx-1][0]
        low = ans.lower()
        for v, lab in options:
            if lab.lower() == low or str(v).lower() == low:
                return v
        matches = [v for v, lab in options if low in lab.lower() or low in str(v).lower()]
        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            print("Multiple matches; please be more specific.")
        else:
            print("No match; try again.")

def prompt_user_multi(label: str, options: List[Tuple[str, str]]) -> List[str]:
    options = [(v or l, l) for v, l in options if (v or l)]
    if not options:
        print(f"[warn] No options found for: {label}")
        return []
    print(f"[choose multiple] {label}")
    for i, (val, lab) in enumerate(options, 1):
        print(f"  {i}. {lab} [{val}]")
    print("Enter numbers (e.g., 1,3,5) or labels separated by commas. Blank to skip.")
    while True:
        ans = input("> ").strip()
        if not ans: return []
        picks = [x.strip() for x in re.split(r"[,\s]+", ans) if x.strip()]
        chosen_vals, ok = [], True
        for p in picks:
            if p.isdigit():
                idx = int(p)
                if 1 <= idx <= len(options):
                    chosen_vals.append(options[idx-1][0]); continue
                ok = False; break
            low = p.lower()
            exact = [v for v,l in options if l.lower()==low or str(v).lower()==low]
            if exact:
                chosen_vals.append(exact[0]); continue
            contains = [v for v,l in options if low in l.lower() or low in str(v).lower()]
            if len(contains)==1:
                chosen_vals.append(contains[0]); continue
            ok = False; break
        if ok:
            out = []
            for v in chosen_vals:
                if v not in out: out.append(v)
            return out
        print("Some items didn’t match uniquely; try again.")

def yes_no(prompt_text: str, default: bool=False) -> bool:
    default_str = "Y/n" if default else "y/N"
    ans = input(f"{prompt_text} [{default_str}] ").strip().lower()
    if not ans: return default
    return ans in ("y", "yes")
