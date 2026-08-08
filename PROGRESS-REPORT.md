# GVCW Visa Appointment Scanner — Progress Report

**Period:** 28 July – 5 August 2026
**Status:** Core engine complete · Verified accurate · Stability hardening in progress

---

## 1. Delivery Format

| Item | Result |
|---|---|
| **Desktop app (Tkinter)** | Moved off browser/server to a native Windows app — no hosting cost, no sandbox limits |
| **Multithreading** | Scraping runs on background threads; UI never freezes during long scans |
| **Zero-install package** | Single `--onedir` build with Python runtime + Selenium drivers bundled. Client runs one `.exe` |

---

## 2. User Interface

| Feature | What it does |
|---|---|
| **Per-type group config** | Each visa type gets its own group setup instead of one shared form |
| **Collapsible group editor** | `[⚙ Edit Group Info]` unlocks only when "Group" is selected |
| **Master toggle** | Switch between *same group info for all types* and *separate per type* |
| **Member tabs** | `ttk.Notebook` cards for 2–5 family members — no long scrolling forms |
| **Live session log** | All output written continuously to a persistent `.log` file |
| **Chrome show/hide** | Win32 API button toggles the browser window without running headless |

---

## 3. Site Documentation *(reference material produced)*

| Document | Covers |
|---|---|
| `book-appointment-structure.md` | Individual booking form — every field, ID and name |
| `book-appointment-GROUP-structure.md` | Group booking — row cloning, allocation methods |
| `profile-page-structure.md` | Profile page, VAC selector, Manage Account button |
| `book-appointment-app.css` / `vendor.css` | 392 styling rules incl. slot-state classes |

**Key discovery — slot detection by CSS class, no text parsing:**

- `.appointment_slot_enabled` → **free**
- `.appointment_slot_disabled` → taken
- `_selected` / `_reserved` / `_notselectable` → other states

---

## 4. Core Automation

| Fix | Problem solved |
|---|---|
| **Row-scoped selectors** | Portal clones group rows but reuses IDs — `#ex_surname` pointed at a hidden template. Now targets `[name="applicants[][field]"]` scoped to each row |
| **All 4 allocation methods** | Same Time · Consecutive · Next Available · One by One — mapped and integrated |
| **Verification Office (VAC 140)** | VAC sync now switches centres and loads each one's distinct appointment catalogue |
| **JS execution fallbacks** | `ElementNotInteractableException` on profile save bypassed via direct JavaScript calls |
| **VAC auto-sync** | Detects city mismatch, switches profile, reloads, verifies — only the VAC field is ever modified |

---

## 5. Stability & Crash Recovery

| Fix | Problem solved |
|---|---|
| **Retry-guard string bug** ⭐ | Retry only matched *"user data directory is already in use"*; Chrome actually says *"Chrome instance exited"*. **Retry code had never executed once.** Root cause of the entire crash loop |
| **Scoped process teardown** | `kill_process_tree` kills only PIDs the app spawned — deliberately *not* a blanket `taskkill /im chrome.exe`, which would close the user's own browser |
| **Graceful shutdown** | 5s grace before hard kill lets Chrome flush its cookie database — fixed random cookie loss |
| **Recovery reordered** | Browser now closed *before* the wait, not after |
| **Exponential backoff** | 10s → 300s, stops after 6 attempts instead of looping forever |
| **Diagnostics** | Verbose ChromeDriver log + Chrome/driver version check at startup |

---

## 6. Session Validation *(latest)*

**Problem:** scanner reported "signed in", then crashed 30s later on the login page.

**Cause:** the check asked *"is the login form missing?"* → *"then we're signed in."* A blank, still-loading page also has no login form.

**Fix — 3-state resolver, polling every 0.5s:**

| State | Detected by | Action |
|---|---|---|
| `LOGGED_IN` | `#manage-account` present | Proceed |
| `LOGGED_OUT` | `#username` present | Exit in ~2s, re-login |
| `RENDER_TIMEOUT` | Neither, page still blank | Keep waiting |

**Plus:**

- Stale cookies cleared, not re-injected
- Visible `🔑 SESSION EXPIRED — SIGN-IN REQUIRED` banner
- Routes to the manual reCAPTCHA gate for a human
- Waiting for a human no longer burns the 6-attempt recovery budget

---

## 7. Independent Accuracy Verification

**Method:** searched every date again by hand, separately, with a network probe recording the portal's own HTTP responses.

### ✅ 16 of 16 dates verified — 100% match

- Every weekday: exactly **10 slots, all taken, 0 free**
- Weekends: centre closed
- **14 August: closed — Pakistan Independence Day.** Scanner detected this without being told it was a holiday
- Re-confirmed an hour later: all 15 re-checked dates identical

**Deliverable:** `GVCW_Verification_Report.pdf` — 2-page client-facing report

---

## 8. Rate Limit (HTTP 429) Findings

| Run | Pacing | Searches before block |
|---|---|---|
| Manual check | ~4s | 17 |
| Scanner run 1 | 4.0s | 19 |
| Scanner run 2 | 9.0s | 21 |

- Practical ceiling: **17–21 searches per session**
- Slower pacing helps only marginally — total volume is what counts
- Portal says wait 10s. **Real cooldown is 3+ minutes**
- Blocked dates are saved and retried — never recorded as "no availability"
- **Proxy rotation not recommended:** evidence points to the limit tracking the *account*, not the IP — it survived full browser restarts

---

## 9. Open Items

| Issue | Impact | Priority |
|---|---|---|
| Gender set to `3` = **OTHER** (MALE is `2`) | Wrong data on a real booking | **High** |
| Status `0` response recorded as "no availability" | Genuine false negative | **High** |
| Cold-start Chrome failures | Cause unidentified; logging now in place | Medium |
| Does rate limiting kill the session? | Would explain repeat logouts | Medium |

---

## Bottom Line

- **The scanner works, and its output is accurate** — independently verified at 100%.
- **No appointments found because none exist.** Every working day: 10 slots, all taken.
- Remaining work is **reliability** — surviving rate limits, session expiry and browser crashes — not slot detection.
