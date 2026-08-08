# Agent prompt — fix the crash loop, the silent search failure, and the click strategy

Evidence base: `GVC_App/logs/session_20260805_120750.log`, `GVC_App/gvc_app.log`.
Entry point: `gvcAutomation.py` → `main()` (2834) → `launch_browser_and_login()` (2703) →
`start_chrome()` (2597) → `gvc_tkinter.py:458`.

Fix these in the order given. **P0 first — it is the reason no appointment has ever been found.**

---

## P0 — THE REAL BUG: primary applicant's nationality is never filled

### Evidence

```
[12:18:10] Member 2 (row cloned2): filled.
[12:18:10] ⚠ Member 1 (row (primary)) is missing: nationality[id] (no field)
[12:18:10] Search validates every visible row before it sends anything, so it will refuse until those are filled in.
```

This repeats on **every** scan cycle (12:18:10, 12:20:39, 12:26:24, 12:37:07…). The scan then
reports "No results or slots" for all 27 dates — which is meaningless, because `#btn-search`
never passed client-side validation and **no availability request was ever sent**. Every "no
availability" line in the log so far is a false negative.

### Cause

The primary row is addressed with the wrong field key. The primary row is `<tr>` with **no id**
and its inputs use the `gp_` prefix; the clones use `ex_`. But **all rows share the same `name`
attributes**, and the clones additionally **duplicate each other's ids**.

| Column | primary row id | clone id | `name` (identical on every row) |
|---|---|---|---|
| Surname | `gp_surname` | `ex_surname` (dup) | `applicants[][surname]` |
| Name | `gp_firstname` | `ex_firstname` (dup) | `applicants[][firstname]` |
| DOB | `gp_dateofbirth` | `ex_dateofbirth2`, `…3` | `applicants[][dateofbirth]` |
| Passport | `gp_passportnumber` | `ex_passportnumber` (dup) | `applicants[][passportnumber]` |
| Expiry | `gp_traveldocumentvaliduntil` | `ex_traveldocumentvaliduntil2`, `…3` | `applicants[][traveldocumentvaliduntil]` |
| Gender | `gp_gender` | `ex_gender` (dup) | `applicants[][gender[id]]` |
| **Nationality** | **`gp_nationality`** | `ex_nationality` (dup) | `applicants[][nationality[id]]` |
| Slot | `gp_periodslotid` | `ex_periodslotid` (dup) | `applicants[][periodslotid]` |

The log message `missing: nationality[id] (no field)` shows the code is looking up the bare key
`nationality[id]` — which matches nothing as an id, and isn't a valid standalone name either.

### Required fix

Never address group fields by id. Resolve **row first, then field by `name`, scoped to that row.**

```python
FIELDS = ["surname", "firstname", "dateofbirth", "passportnumber",
          "traveldocumentvaliduntil", "gender[id]", "nationality[id]"]

def visible_rows(driver):
    rows = driver.find_elements(By.CSS_SELECTOR, "#groupBody tr")
    return [r for r in rows if "hidden" not in (r.get_attribute("class") or "")]
    # excludes the #secondTr template row

def field(row, name):
    return row.find_element(By.CSS_SELECTOR, f'[name="applicants[][{name}]"]')
```

`visible_rows()[0]` is the primary applicant, `[1:]` are members 2..N. Assert
`len(visible_rows()) == int(members_select.value)` after cloning.

### Verification gate — add this, it is not optional

Before clicking Search, assert every visible row has every required field non-empty. If not,
**abort the cycle and log loudly** rather than clicking and recording a false "no availability".

```python
missing = [(i, f) for i, r in enumerate(visible_rows(driver))
                  for f in FIELDS
                  if not field(r, f).get_attribute("value")]
if missing:
    raise IncompleteFormError(missing)   # do NOT click search, do NOT log "no availability"
```

Also: after a search that returns nothing, distinguish the three cases explicitly —
(a) validation modal appeared *("Please check the form fields again"* — dismiss via the element
whose text is `OK`), (b) `#resultMessage` lost `.hidden` (genuine no-slots), (c) HTTP error.
Right now all three collapse into "No results".

---

## P1 — `ElementNotInteractableException` is a symptom, not the bug

### Evidence

```
[12:13:09] Chrome window hidden - running in background.
[12:13:20] Native click on #btn-newuser (Save profile) failed (ElementNotInteractableException)
[12:18:10] Native click on #btn-search (Search) failed (ElementNotInteractableException)
[12:20:39] Native click on #btn-search (Search) failed (ElementNotInteractableException)
```

The first native-click failure occurs **11 seconds after the app hides the Chrome window**, and
every native click thereafter fails the same way — on `#btn-search` as well as `#btn-newuser`.

**Root cause: the hidden/minimised window, not the elements.** `#btn-newuser` and `#btn-search`
are ordinary visible `<button>`s. Selenium's native click requires the element to be rendered and
hit-testable; once the window is hidden, nothing is. This is not a select2 problem and it is not
specific to the profile page.

Note the existing fallback **already works** — it logs "switching to JS clicks for the rest of
this session" and the scan continues. So this exception is **not** what crashes the driver.

### Required fix

Stop treating this as an error path. Pick one:

- **Preferred:** if the window is going to be hidden, use JS clicks from the start for all
  app-controlled buttons. Detect the state once at startup and set the click strategy, instead of
  discovering it via an exception on every session.
- **Or:** don't hide the window; use a proper headless mode, where CDP-level clicks work
  consistently.

For the profile Save specifically, calling the handler directly is correct and matches how the
page works — `#btn-newuser` is `type="button"` with `onclick="saveprofile(this)"`, so there is no
native form submit and `form.submit()` would do nothing:

```python
driver.execute_script("saveprofile(document.getElementById('btn-newuser'));")
```

Same for `#btn-search`:

```python
driver.execute_script("document.getElementById('btn-search').click();")
```

Do **not** switch click strategy mid-flight. Decide once, at session start.

---

## P2 — the `session not created` restart loop

### Evidence

`session not created: Chrome instance exited` appears **38 times** in `gvc_app.log`. Auto-recovery
retries after 10s into the same failure. Profile: `E:\Scraper\GVC_App\chrome_profile`.

### Required fixes

**a. Make teardown deterministic.** Before every relaunch: `driver.quit()`, then wait for the
Chrome PIDs spawned by this app to actually exit (poll, with a hard kill after ~15s). Do not
assume `quit()` is synchronous — it isn't when the renderer has crashed.

**b. Clear stale locks.** Before launch, if no owned Chrome PID is alive, delete from
`chrome_profile/`: `SingletonLock`, `SingletonSocket`, `SingletonCookie`, `lockfile`. Guard this
so it can never run while a live instance owns the profile.

**c. Back off properly.** 10s fixed retry is too aggressive. Use exponential backoff
(10s → 20s → 40s, cap ~5min) and **stop after N consecutive failures** instead of looping
forever. Surface the failure in the UI.

**d. Turn on the verbose driver log** so the next failure names its own cause:

```python
service = Service(executable_path=..., log_output="chromedriver.log",
                  service_args=["--verbose"])
```

**e. Rule out version drift.** Compare Chrome's major version against ChromeDriver's at startup
and refuse to run with a clear message on mismatch. Better: delete the pinned binary and let
Selenium Manager (4.6+) resolve it.

**f. Guard the profile directory.** Fail fast with a readable message if `chrome_profile` is
locked, missing, or read-only — rather than surfacing a 20-line ChromeDriver stack trace.

---

## P3 — you are being rate limited, and the restarts make it worse

### Evidence

```
[12:20:42] Rate limited — spacing searches 6.0s → 9.0s apart.
[12:20:42] ⛔ Portal refused to answer for 24/08/2026 (HTTP 429)
[12:20:45] Portal sent Retry-After: 10s — honouring it.
```

The portal is returning **HTTP 429**. Its own banner states the platform is for individual
applicants, that use is monitored, and that excess use results in **cancellation of reserved
slots**.

### Required fixes

- Honour `Retry-After` exactly (already partly done) and apply it as a **global** floor across
  restarts, not per-session — a crash-restart currently resets the spacing and immediately
  re-triggers 429.
- Persist the backoff state to disk so auto-recovery resumes at the throttled interval.
- Add a hard floor between searches and a daily request cap.
- **Fixing P0 alone will cut request volume substantially** — right now every one of the 27 daily
  searches is a wasted round-trip that could never have succeeded.

---

## P4 — do not hardcode `#type` ids

Log shows `Type value: Premium Lounge`. The `#type` option list is rendered **per VAC**:
Islamabad (`137`) exposed 4 options (`0`, `2`, `6`, `26`); Verification Office (`140`) exposes
exactly one (`24` Document Verification). Read the options at runtime and match on label; fail
loudly if the configured type isn't present for the current VAC.

Related: VAC `140` is **Verification Office**, not a booking centre. The VAC sync at 12:13:19
correctly detected `current=140, target=138`. Add a guard that refuses to scan while VAC is `140`.

---

## Acceptance criteria

1. Every visible group row — **including the primary** — is fully populated before Search; a
   run with any missing field aborts loudly instead of logging "no availability".
2. At least one real availability request reaches the portal, with its endpoint, payload and
   response shape captured to the log.
3. "No availability" is only logged when `#resultMessage` actually became visible.
4. Click strategy chosen once at startup; no mid-session strategy switching.
5. Auto-recovery backs off exponentially and gives up after N failures.
6. `chromedriver.log` written with `--verbose`.
7. Chrome/ChromeDriver major versions asserted equal at startup.
8. Rate-limit state survives a restart.
