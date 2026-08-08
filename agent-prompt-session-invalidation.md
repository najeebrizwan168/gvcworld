# TASK: Fix the session-invalidation recovery loop

> ⚠️ **INVESTIGATE FIRST.** Section 1 is a hypothesis that is *partially* confirmed. Section 2
> lists what it fails to explain. Read `E:\Scraper\GVC_App\logs\session_20260805_160244.log`
> (17:38:44 onward) yourself and answer Section 3 **before** writing code. If you only implement
> Section 1, the loop will come back.

---

## 1. Confirmed: the session check returns a false positive

The scanner reports the session is alive, then crashes 30 seconds later looking for
`#manage-account` on what is plainly the login page.

```
[17:40:10] ✓ The saved Chrome profile is still signed in — skipping the login gate.
[17:40:40] [ERROR] Could not locate #manage-account after 30s
           title='Visa | Login' | present=['#username', '#password']
           body='Sign in to GVCW (Enter Username or E-mail & Password) …'
```

And again after cookie injection:

```
[17:41:18] Injected 6 saved cookie(s); reloading to let the portal see them...
[17:41:28] ✓ Saved cookies restored the session — skipping the login gate.
[17:41:58] [ERROR] … title='Visa | Login' | present=['#username', '#password']
```

The session is dead. The check says it is alive. This part of the hypothesis is correct.

---

## 2. What the hypothesis does NOT explain — the deeper bug

### 2a. The very first failure was NOT a logged-out page

```
[17:39:13] ✓ The saved Chrome profile is still signed in — skipping the login gate.
[17:39:43] [ERROR] … title='GVCW' | present=[] | body=''
```

`present=[]` and `body=''`. That is **not** the login page — it is a page that had not rendered
at all. Yet the session check passed on it too.

**This reveals the real defect.** The check almost certainly asks *"is the login form absent?"*
and concludes *"then we must be signed in."* On a blank, unrendered page the login form is also
absent — so an empty page reads as a healthy session. Adding a `#username` check, as the
hypothesis proposes, **does not fix this case**: on a blank page `#username` is absent too, and
the check still returns "signed in."

The fix is not "also look for the login form." It is:

1. **Wait for the page to actually render before judging anything.** This site is client-rendered
   and slow — `document.readyState` reaches `complete` while `<body>` is still empty, and content
   appears seconds later after `GET /api/v1/translations` resolves. A verdict taken before render
   is meaningless.
2. **Assert a positive logged-in marker**, never the absence of a logged-out one. `#manage-account`
   exists only when authenticated. Its presence is proof of a live session; its absence proves
   nothing on its own.

### 2b. Three states are being collapsed into two

The code currently distinguishes "signed in" from "not signed in." There are three:

| State | Evidence | Correct action |
|---|---|---|
| **Not yet rendered** | `body === ''`, no markers present | Keep waiting, up to the render timeout |
| **Logged out** | `#username` + `#password` present, title `Visa \| Login` | Session dead → re-login flow |
| **Logged in** | `#manage-account` present, title `Visa \| Home` | Proceed |

Resolve these as a **poll returning one of three verdicts**, not a boolean. A blank page must
never be classified as either signed-in or signed-out.

### 2c. Every failed attempt burns 30 seconds it did not need to

Each recovery waits the full 30s for `#manage-account` before failing — even at 17:40:10, where
`#username` was already on screen and the answer was knowable in about two seconds. The poll must
**exit early** the moment it sees the login form. Six attempts × 30s of dead waiting is most of
the loop's runtime.

### 2d. Why did the session die in the first place?

This is the question the hypothesis never asks, and it is the most important one.

Timeline: the run at 17:07:09 escalated to a 300-second rate-limit cooldown after repeated
HTTP 429s. The next launch, at 17:38:44, found the session dead.

**Investigate whether the portal invalidates the session as part of rate-limit enforcement.** If
it does, this is not a cookie-expiry problem and no amount of re-login logic will stop it
recurring — the correct fix is to stop hitting the limit. Report what you find.

Related signal to explain: the saved-cookie count drifts **8 → 6 → 11** across these attempts
(17:38 saved 6, having saved 8 an hour earlier; 17:41 saved 11). Cookie counts should not
fluctuate like that on a stable session. Work out which cookie is the session cookie, log it by
name, and record its presence/absence and expiry at every check. Right now the logs only say
"saved N cookie(s)", which is not diagnostic.

### 2e. Re-login is NOT automatable — design for that

The login form carries **reCAPTCHA v2** (sitekey `6LcnlCoUAAAAAJLjWXXaByTFyuOLf4K0gGu5r3d2`).
A human must solve it. "Gracefully route back to the login gate" therefore means:

- In **continuous unattended mode**, a dead session halts progress until a person returns. The
  scanner must **surface this loudly** — UI state, notification, distinct log banner — not sit in
  a silent retry loop.
- Do **not** count login-gate waiting against the 6-attempt auto-recovery budget. Waiting for a
  human is not a failure and must not exhaust the retry counter and stop the app.
- The existing login gate already polls for the user to finish (`[GATE] Waiting for you to finish
  signing in in Chrome...` at 12:12:48). Reuse that path; do not write a second one.

---

## 3. Questions to answer before coding

1. Show the current session-check implementation. Is it a negative check (absence of login form)?
   Confirm or refute 2a with the actual code.
2. Does it wait for render before judging? If not, that is the root cause of the 17:39:13 failure.
3. Does the 429 cooldown correlate with session death across all logs, or was 17:38 a one-off?
4. Which cookie carries the session, and what is its lifetime? Does it survive a browser restart
   normally?
5. Does cookie injection actually work here, or is `✓ Saved cookies restored the session` a second
   instance of the same false positive? Evidence suggests the latter — it was immediately followed
   by the login page.

---

## 4. Implementation

### A. Three-state session resolver

Replace the boolean check with a poll that returns `RENDERING` / `LOGGED_OUT` / `LOGGED_IN`:

```python
def session_state(driver, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if driver.find_elements(By.CSS_SELECTOR, "#manage-account"):
            return "LOGGED_IN"                       # positive proof
        if driver.find_elements(By.CSS_SELECTOR, "#username, #password"):
            return "LOGGED_OUT"                      # early exit, no 30s wait
        time.sleep(0.5)                              # still blank → keep polling
    return "RENDER_TIMEOUT"
```

Call this **everywhere** a session is assumed: after profile launch, after cookie injection, after
every auto-recovery relaunch, and before `ensure_vac()`. Never proceed on anything but
`LOGGED_IN`.

### B. Verify cookie injection instead of trusting it

After injecting cookies and reloading, run `session_state()` again. Only log
`✓ Saved cookies restored the session` when it returns `LOGGED_IN`. If it returns `LOGGED_OUT`,
discard the stored cookies — they are stale and re-injecting them next cycle repeats the loop.

### C. Route to the login gate, don't crash

On `LOGGED_OUT`: clear stored cookies, log a distinct banner (`SESSION EXPIRED — sign-in
required`), enter the existing login gate, wait for the human, save fresh cookies, resume from the
saved progress point. Progress is already preserved correctly — keep that behaviour.

### D. Separate the failure counters

`RENDER_TIMEOUT` is a transient fault → counts toward auto-recovery. `LOGGED_OUT` is not a fault
→ must not count, and must not trip the 6-attempt limit.

---

## 5. Acceptance criteria

1. A blank/unrendered page is **never** classified as signed-in. Reproduce by evaluating the check
   against a page mid-render.
2. `#username` present → verdict in under ~3 seconds, not 30.
3. `✓ Saved cookies restored the session` only ever printed after `#manage-account` was seen.
4. Session expiry produces a clear, visible "sign-in required" state and never a
   `Could not locate #manage-account` crash.
5. Waiting for human login does not consume auto-recovery attempts.
6. Stale cookies are discarded, not re-injected on the next cycle.
7. The session cookie is logged by name with its expiry at every check.
8. A written answer to Section 3 Q3 — whether rate limiting is invalidating the session.
