# TASK: Log-Driven Root Cause Analysis & Crash Loop Resolution

> ⚠️ **MANDATORY PREREQUISITE — DO NOT SKIP**
>
> Do not implement anything until Section 2 is complete. Read every log in
> `E:\Scraper\GVC_App\logs` plus `E:\Scraper\GVC_App\gvc_app.log` and produce the evidence table
> in Section 2 first.
>
> The hypothesis in Section 1 is **a hypothesis we already know is partially wrong**. It is
> included so you can test it, not adopt it. If your analysis simply agrees with it, you have not
> analysed hard enough — re-read the logs.

---

## 0. Context you need before reading the logs

**App:** Selenium/Python appointment scanner for `https://pk-gr-services.gvcworld.eu`, packaged as
`GVC_Scanner.exe`. Entry: `gvcAutomation.py` → `main()` (2834) → `launch_browser_and_login()`
(2703) → `start_chrome()` (2597) → `gvc_tkinter.py:458`.

**Persistent Chrome profile:** `E:\Scraper\GVC_App\chrome_profile`

**Known site behaviour (verified against the live DOM — treat as fact, do not re-derive):**

- The site is **client-rendered and slow**. On a cold load `document.readyState` reaches
  `complete` while `<body>` is still empty; real content appears several seconds later, after
  `GET /api/v1/translations` and the page's own XHRs resolve. **Never** wait on page load. Poll
  for the specific element.
- The login page and the dashboard **share the same URL** (`/`). Do not use URL change to detect
  login. Use presence of `#manage-account`, or `document.title` (`Visa | Login` →`Visa | Home`).
- `#manage-account` only exists when authenticated.
- `#btn-newuser` and `#btn-search` are `type="button"` with inline `onclick` handlers. There is no
  native form submit; `form.submit()` does nothing.

---

## 1. Our hypothesis — VERIFY, DO NOT ASSUME

We suspected this chain:

1. Script hits HTTP 429, closes the browser politely.
2. On restart it hits `[ERROR] Could not locate #manage-account` and crashes.
3. The unhandled exception bypasses `driver.quit()`, leaving a zombie `chrome.exe`.
4. The zombie holds `SingletonLock` on `chrome_profile`.
5. Auto-recovery then fails forever with `session not created: Chrome instance exited`.

### Known problems with this hypothesis

We already checked, and it does **not** hold universally:

- In `session_20260805_131153`, `session_20260805_131445` and `session_20260805_132041`,
  `session not created` is the **first error in the file** — no preceding 429, no preceding
  `#manage-account` error. `132041` started 13:20:41 and failed 13:21:02: **21 seconds in, on the
  first launch attempt.** Nothing in that run could have created a zombie.
- `session_20260805_123842` contains no failure at all.
- It only fits `session_20260805_114933` (11:50:23 → 11:50:39) and
  `session_20260805_133001` (13:34:43 → 13:34:58).
- When the profile directory was inspected directly, **no `SingletonLock` / `SingletonSocket` /
  `lockfile` was present**, yet `Default/` and `Local State` had been written minutes earlier —
  i.e. Chrome started, wrote to the profile, then exited.

So at least two distinct failure modes exist. Your job is to name both.

---

## 2. Investigation — produce this before writing code

### 2a. Evidence table (required output)

For **every** log in `E:\Scraper\GVC_App\logs`, one row:

| Log file | First error (timestamp + line no.) | Exception type | What immediately preceded it | Zombie plausible? |
|---|---|---|---|---|

"Zombie plausible?" = was there an earlier crash *in this same run*, or in a run that ended less
than ~2 min earlier, that could have orphaned a process? Answer yes/no with the timestamp you are
relying on.

### 2b. Classify every `session not created` occurrence into exactly one bucket

- **Bucket A — post-crash:** preceded by an unhandled exception in the same or an immediately
  prior run. Zombie/lock is a credible cause.
- **Bucket B — cold start:** first launch of a run, no prior crash within ~2 min. **A zombie
  cannot explain these.** Candidate causes: Chrome/ChromeDriver major-version mismatch, corrupted
  profile, bad launch flags (e.g. `--headless` combined with a persistent `--user-data-dir`,
  or `--no-sandbox`/`--disable-gpu` combinations), profile locked by an unrelated Chrome.
- **Bucket C — neither.** Explain.

State the count in each bucket. **If Bucket B is non-empty, Scenario A alone cannot fix the
problem and you must implement Scenario B as well.**

### 2c. Enable real diagnostics before concluding

The current logs cannot explain Bucket B, because ChromeDriver's reason is never captured. Add
this **first**, run once, and read the result:

```python
service = Service(executable_path=...,
                  log_output="chromedriver.log",
                  service_args=["--verbose"])
```

Also assert at startup that Chrome's major version equals ChromeDriver's, and print both.

### 2d. Questions to answer explicitly

1. Why does `#manage-account` lookup fail? Check whether the code uses a bare
   `driver.find_element` with no wait (see `gvc_app.log:119`). Given the site's slow client-side
   render, **is this a missing `WebDriverWait` rather than a symptom of anything else?**
2. Are Bucket B failures explained by version drift, launch flags, or profile corruption? The
   verbose log will say.
3. Is the app ever launching Chrome against the **user's default profile** instead of
   `chrome_profile`? That would collide with the user's own browser and fail instantly.
4. Does `driver.quit()` actually complete, or does the app assume it is synchronous? It is not,
   when the renderer has already crashed.

---

## 3. Implementation

Implement whichever of A / B / C your evidence supports. These are **not** mutually exclusive —
if Buckets A and B are both non-empty, do both.

### Scenario A — if Bucket A is non-empty (stale process / lock)

1. **Scoped process teardown.** Track the PIDs your app spawns (`driver.service.process.pid` and
   its children) and kill only those.
   > 🚫 **NEVER run `taskkill /f /im chrome.exe`.** It kills the user's personal Chrome, any
   > browser extension sessions, and unrelated automation. A blanket image kill is not acceptable
   > in this codebase. Kill by owned PID only.
2. Run teardown immediately before every `webdriver.Chrome()` instantiation, and poll until the
   owned PIDs are gone (hard-kill after ~15s) — do not assume `quit()` is synchronous.
3. Delete `SingletonLock`, `SingletonSocket`, `SingletonCookie`, `lockfile` from
   `chrome_profile` — **guarded** so it can never run while a live owned instance holds the
   profile.

### Scenario B — if Bucket B is non-empty (cold-start failure)

Fix what the verbose log actually names. Likely candidates:

1. Version drift — refuse to start with a clear message on major mismatch; prefer Selenium Manager
   (Selenium ≥ 4.6) over a pinned binary.
2. Profile corruption — detect and rebuild `chrome_profile` (back up the old one; the user will
   have to log in again).
3. Launch flags — audit the options built in `start_chrome()` (2597).
4. Fail fast with a readable message if `chrome_profile` is locked, missing or read-only, instead
   of surfacing a 20-line ChromeDriver stack trace.

### Scenario C — always, regardless of A/B

1. **Explicit waits everywhere.** Replace bare `find_element` calls on this site with
   `WebDriverWait(...).until(...)` on the specific element (`#manage-account`, `#appointment`,
   `#vac`, …). Budget ~10s typical, 30s timeout. This is the direct fix for
   `Could not locate #manage-account`.
2. **Sane auto-recovery.** Replace the fixed 10s retry with exponential backoff
   (10s → 20s → 40s, cap ~5min) and **stop after N consecutive failures** instead of looping
   forever. Surface the failure in the UI. The current loop retried into the same failure 38
   times across the logs.
3. **Persist rate-limit state to disk.** The portal returns HTTP 429 with `Retry-After`. The
   backoff currently resets on restart, so recovery walks straight back into the limit. Apply the
   interval as a global floor across restarts, plus a daily request cap.

---

## 4. ALSO FIX — the bug that makes every scan result meaningless

This is independent of the crash loop and is **higher priority than all of the above.**

```
[12:18:10] Member 2 (row cloned2): filled.
[12:18:10] ⚠ Member 1 (row (primary)) is missing: nationality[id] (no field)
```

Repeats every cycle (12:18:10, 12:20:39, 12:26:24, 12:37:07…). `#btn-search` validates
client-side and **sends no request** when any visible row is incomplete. Therefore **every
"No results or slots" line in every log is a false negative.** No availability request has ever
reached the portal.

**Cause:** group rows are being addressed by `id`. The primary row uses the `gp_` prefix
(`gp_nationality`); clones use `ex_` and **duplicate each other's ids** — only the datepickers get
suffixed (`ex_dateofbirth2`, `ex_traveldocumentvaliduntil2`). All rows share identical `name`
attributes.

**Fix — resolve the row first, then the field by `name` scoped to that row:**

```python
FIELDS = ["surname", "firstname", "dateofbirth", "passportnumber",
          "traveldocumentvaliduntil", "gender[id]", "nationality[id]"]

def visible_rows(driver):
    return [r for r in driver.find_elements(By.CSS_SELECTOR, "#groupBody tr")
            if "hidden" not in (r.get_attribute("class") or "")]   # excludes #secondTr template

def field(row, name):
    return row.find_element(By.CSS_SELECTOR, f'[name="applicants[][{name}]"]')
```

`visible_rows()[0]` = primary applicant, `[1:]` = members 2..N. Assert
`len(visible_rows()) == int(members_value)` after cloning.

**Add a pre-search gate.** If any visible row has any empty required field, abort the cycle and
log loudly — do **not** click Search and do **not** record "no availability":

```python
missing = [(i, f) for i, r in enumerate(visible_rows(driver))
                  for f in FIELDS if not field(r, f).get_attribute("value")]
if missing:
    raise IncompleteFormError(missing)
```

**Distinguish the three no-result cases** instead of collapsing them into "No results":
(a) validation modal appeared (*"Please check the form fields again"* — dismiss via the element
whose text is `OK`), (b) `#resultMessage` lost `.hidden` (genuine no-slots), (c) HTTP error.

---

## 5. Secondary issues

- **Click strategy.** `ElementNotInteractableException` on `#btn-newuser` and `#btn-search` starts
  11 seconds after `[12:13:09] Chrome window hidden - running in background` and affects both
  buttons. Cause is the hidden window, not the elements. The existing fallback works; the problem
  is *discovering it via an exception every session*. **Decide the click strategy once at
  startup** — if the window will be hidden, use JS clicks from the start:
  ```python
  driver.execute_script("saveprofile(document.getElementById('btn-newuser'));")
  driver.execute_script("document.getElementById('btn-search').click();")
  ```
- **Do not hardcode `#type` option ids.** The list is rendered per VAC: Islamabad (`137`) exposed
  `0`/`2`/`6`/`26`; Verification Office (`140`) exposes only `24`. Read options at runtime, match
  on label, fail loudly if the configured type is absent.
- **Guard VAC `140`.** It is Verification Office, not a booking centre. Refuse to scan on it.

---

## 6. Deliverables

1. The Section 2a evidence table and 2b bucket counts, **before** any code changes.
2. A one-paragraph statement of the real root cause(s) — explicitly say which parts of our
   hypothesis were right and which were wrong.
3. `chromedriver.log` from a verbose run, with the line that actually explains a Bucket B failure.
4. The code changes, scoped to what the evidence supports.
5. Confirmation that at least one real availability request reached the portal, with its endpoint,
   payload and response shape captured to the log.

## 7. Acceptance criteria

- No blanket `taskkill /im chrome.exe` anywhere in the codebase.
- Every visible group row, **including the primary**, is fully populated before Search.
- "No availability" is logged only when `#resultMessage` actually became visible.
- Bare `find_element` replaced with explicit waits on this site's pages.
- Auto-recovery backs off exponentially and gives up after N failures.
- Chrome/ChromeDriver major versions asserted at startup.
- Rate-limit state survives a restart.
