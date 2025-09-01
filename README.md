here’s a drop-in “architecture README” you can paste into your repo so any fresh chat (or teammate) instantly groks the codebase.

---

# Job Autofill — project map & how it works

**What it does**

1. opens a job page (any ATS),
2. scrapes the job text → generates a concise summary + a tailored cover letter (via Gemini using `GEMINI_API_KEY` from `.env`),
3. shows you both for approval,
4. uploads your resume,
5. fills contact fields from `parsed_resume.json` (falls back to Gemini, then asks you),
6. for dropdowns/comboboxes/radios/checkboxes: shows **actual options** and only selects from those (no free typing),
7. re-checks required fields, asks again if needed,
8. submits and saves a full-page screenshot.

**Run**

```bash
pip install -r requirements.txt
playwright install chromium
python -m app.apply
```

`.env` (example):

```
GEMINI_API_KEY=YOUR_KEY
JOB_URL=https://example.com/job
HEADLESS=true
```

---

## Files & responsibilities

### `app/apply.py` — entrypoint

* Tiny bootstrap that calls `runner.main()`.
* Run with: `python -m app.apply`.

---

### `app/runner.py` — the orchestrator (main flow)

* Owns the **application flow** end-to-end:

  1. Load profile & resume text (`profile.load_profile`, `profile.extract_contact`, `profile.read_resume_text`).
  2. Launch Playwright, open `JOB_URL`.
  3. Scrape job page (`job.scrape_job_text`).
  4. Draft summary + cover letter (`llm.draft_summary_and_letter`) and ask you to approve (`prompts.yes_no`).
  5. Discover form fields (`dom.collect_form_fields`) and upload resume (`fill.upload_resume_if_possible`).
  6. Plan contact values: JSON → LLM fallback (`llm.infer_with_llm`) → ask you (`prompts.prompt_user_for`).
  7. Handle **unknown questions**:

     * For selects/comboboxes: read live options (`widgets.open_combobox_and_get_options`) and let you choose (`prompts.prompt_user_choice` / `prompt_user_multi`).
     * For radios/checkboxes: group by name, show choices once, then record picks.
     * For text fields: only ask if required.
  8. **Review planned values** in console; ask to proceed.
  9. Fill everything (`fill.fill_value_into_field`).

10. Re-scan required empties and ask again if needed (`dom.read_current_value`).
11. Confirm & submit (`fill.click_submit`), take full-page screenshot, and heuristically detect success (`fill.success_indicator`).

Think of `runner.py` as the script’s “director”.

---

### `app/config.py` — configuration & shared constants

* Loads `.env` so `GEMINI_API_KEY`, `JOB_URL`, etc. are available.
* Exposes overridable constants:

  * `JOB_URL`, `PARSED_JSON`, `RESUME_PDF`, `RESUME_TXT`, `HEADLESS`, `SCREENSHOT_DIR`.
* Defines `FIELD_SYNONYMS` (phrase → canonical key) and `KNOWN_KEYS` (contact fields we try to auto-fill).
* You can add synonyms here to improve field detection across ATS.

---

### `app/profile.py` — candidate data

* `load_profile()`: reads `parsed_resume.json` (produced by your resume parser).
* `extract_contact(profile)`: normalizes contact info:

  * name → first/last, email, phone, links (LinkedIn/GitHub/portfolio), location.
* `read_resume_text()`: pulls plain-text resume (prefers `data/resume.txt`, falls back to PDF extraction).

---

### `app/llm.py` — Gemini helpers

* `gemini_text(prompt)`: thin wrapper that uses `GEMINI_API_KEY` from `.env`.
* `infer_with_llm(field_key, resume_text)`: tries to extract a single contact field from resume text (returns `None` if unknown).
* `draft_summary_and_letter(job, resume_text, contact)`:

  * Produces a **4–6 bullet summary** and a **120–160 word cover letter** tailored to the job.
  * Pure fallback (non-LLM) is used if the key or texts are missing.

---

### `app/prompts.py` — command-line UI

* `prompt_user_for(...)`: ask for a free-text value (used only when required or for missing contact fields).
* `prompt_user_choice(...)`: single-choice picker; you can answer by number or by typing option text.
* `prompt_user_multi(...)`: multi-choice version, supports comma-separated numbers or option texts.
* `yes_no(...)`: y/n confirmation prompts used for review and submit.

---

### `app/dom.py` — DOM discovery & required detection

* Walks **all frames**; finds:

  * native `input`, `select`, `textarea`
  * `role="combobox"` / `aria-haspopup="listbox"` custom widgets (React-Select, Select2, etc.)
* For each element returns a field object:

  ```
  {
    frame, el, widget, type, label, placeholder, name, aria,
    key, required, options, multiple, group_name
  }
  ```

  * `widget` ∈ {`input`, `textarea`, `select`, `combobox`, `checkbox`, `radio`}
  * `key` is the canonical field (`first_name`, `email`, `cover_letter`, etc.) matched via `FIELD_SYNONYMS`.
  * `required` is true if:

    * element has `required`/`aria-required="true"`, or
    * label includes an asterisk `*` (many ATS rely on this).
  * `options` filled for native `<select>` or `<input list="datalist">`.
  * `group_name` set for radios/checkboxes to group choices.
* `read_current_value(f)`: returns current field value (used when re-checking required fields).

---

### `app/widgets.py` — custom dropdowns (comboboxes)

* Deals with modern dropdowns rendered outside the form (portals).
* `open_combobox_and_get_options(page, frame, el)`:

  * scrolls/focuses, opens the menu (click + key hints),
  * reads **visible** options anywhere on the page,
  * returns a **de-duplicated** list of `(value, label)`.
* `select_in_combobox(page, frame, el, picks)`:

  * **strictly clicks a listed option** (no free typing), matching by exact or partial label/value.
  * Handles single or multiple sequential picks.

This is why the script works on Greenhouse, Lever, Workday, Ashby, Taleo, SmartRecruiters, and most custom sites.

---

### `app/fill.py` — filling & submission

* `upload_resume_if_possible(fields)`: finds a resume/CV file control (native or labeled) and sets `RESUME_PDF`.
* `fill_value_into_field(f, val, page=None)`:

  * smart `select` (value→label fallback),
  * `combobox` (click option via `widgets.select_in_combobox`),
  * checkbox/radio selection,
  * text fields (clear + type).
* `click_submit(page)`: tries several common submit/apply selectors.
* `success_indicator(page)`: heuristics to detect “Thanks/Received/Submitted” messages.

---

### `app/job.py` — job page scraping

* `scrape_job_text(page)`: extracts:

  * `title` (first `h1/h2`),
  * `company` (common selectors or page title fallback),
  * `location` (basic heuristic),
  * `body` (main content text).
* This text feeds the LLM to produce the summary & cover letter.

---

## Data flow (high level)

```
config      profile      job         llm            dom/widgets         fill
  |           |           |           |                  |                |
  v           v           v           v                  v                v
.ENV -> constants  JSON->contact  page->job  -> summary+cover-letter  discover fields -> upload resume
                                 (approve)                          -> plan contact -> ask for unknowns
                                                                     -> review -> fill -> recheck -> submit
```

---

## How to customize

* **Point to a new job link**
  Set `JOB_URL` in `.env` (preferred) or edit `app/config.py`.

* **Add/adjust field detection**
  Add phrases to `FIELD_SYNONYMS` in `app/config.py` (e.g., map “mobile number” → `phone`).

* **Pre-wire default answers**
  If a site always asks specific questions, you can intercept in `runner.py` before prompting (e.g., detect by label text and set a default choice).

* **Debug dropdowns**
  Set `HEADLESS=false` in `.env` to watch the clicks. If a menu still won’t show options, log the HTML around the control and add a CSS selector to `widgets._visible_option_nodes`.

---

## Environment & safety notes

* The script loads `.env` automatically; **never commit** `.env`. Keep `screenshots/` ignored too.
* `.env.example` can document variables without secrets.
* The LLM is used only for:

  * job summary + cover letter,
  * guessing missing **contact** fields,
  * it never “hallucinates” dropdown answers — those must be chosen from real options.

---

## Common pitfalls & fixes

* **“Dropdown shows but no options in console”**
  Some portals render menus at the end of `<body>`. We scan page-wide; if still empty, the widget might lazy-load—click the combobox once manually to load the list, then re-run the prompt step.

* **Site requires CAPTCHA/MFA**
  The script can’t solve those; set `HEADLESS=false`, complete the step, then continue.

* **Submit button not found**
  Add a site-specific selector in `fill.click_submit`.

* **PDF text extraction is poor**
  Put a plain-text copy of your resume at `data/resume.txt`—it’s preferred by the LLM helper.

---

## Quick runbook

1. Put your resume at `data/resume.pdf` (and optionally `data/resume.txt`).
2. Put your parsed resume JSON at `parsed_resume.json`.
3. Create `.env` with `GEMINI_API_KEY` and `JOB_URL`.
4. `pip install -r requirements.txt && playwright install chromium`
5. `python -m app.apply`
6. Approve cover letter → answer dropdown prompts → review → submit → check screenshot path printed.

---

Save this page as `README.md` in your repo and you’ll have a perfect handoff for any new chat or collaborator.
