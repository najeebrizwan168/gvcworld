# Work Summary — GVCW Appointment Scanner Project

Everything done, in order, in plain language. Site:
`https://pk-gr-services.gvcworld.eu` (Greece visa application centres, Pakistan).

---

## Part 1 — Setting up browser control

**Task:** Give Claude the ability to inspect the live site directly.

- Installed and connected the **Claude for Chrome** extension.
- This unlocked: reading the DOM, running JavaScript in the page, watching network
  requests, and reading console output.

**Why it mattered:** everything after this depended on reading the real page instead of guessing.

---

## Part 2 — Mapping the Book Appointment page (individual booking)

**Task:** Extract the full structure of `/appointments/add`.

**Delivered:** `book-appointment-structure.md`, `book-appointment-app.css`,
`book-appointment-vendor.css`

**What was found:**

| Finding | Detail |
|---|---|
| Page requires login | Logged out, the URL returns HTTP 401 |
| Form root | `<form id="appointment" class="classic">` |
| Tech stack | jQuery + select2 dropdowns + jQuery UI datepickers |
| Anti-bot | Google reCAPTCHA v2 on login and on booking |
| Second factor | SMS OTP required before a booking can be completed |
| Slow rendering | Page reports "loaded" while still blank; real content appears seconds later |

**The most useful discovery — slot state classes.** The time-slot grid uses five CSS classes, so
availability can be detected by class alone with no text parsing:

- `.appointment_slot_enabled` — **free, clickable**
- `.appointment_slot_selected` — currently chosen
- `.appointment_slot_notselectable` / `_reserved` — free but not pickable
- `.appointment_slot_disabled` — taken

---

## Part 3 — Mapping the profile page

**Task:** Find the profile button and document the profile page.

**Delivered:** `profile-page-structure.md`

**What was found:**

- The control is in the **top-right**, not top-left, and reads **"Manage Account"**.
- `<a href="/user/934868" id="manage-account" class="btn darkblue">` — the URL contains the
  user ID, so it must be read at runtime, not hardcoded.
- The VAC (visa centre) selector lives here: `137` = Islamabad, `138` = Lahore,
  `140` = Verification Office (not a booking centre).
- Save and Unsubscribe are JavaScript buttons with no normal form submit.

**Key insight:** the VAC set on this page controls which centre the Book Appointment page
searches. There is no way to override it on the appointment form itself.

---

## Part 4 — The VAC sync instruction set

**Task:** Write instructions so the scanner switches city automatically when the user picks a
different one.

**Delivered:** `agent-prompt-vac-sync.md`

**Traps documented:**

1. Dropdowns are select2 widgets — setting a value directly doesn't update the display or fire
   the app's handlers. Must use `jQuery('#vac').val('137').trigger('change')`.
2. No native form submit — the Save button runs `saveprofile(this)`.
3. The appointment page caches the VAC at load, so it must be reloaded after any change.
4. Only the VAC field may be modified — the Save button posts the whole form, so a snapshot/diff
   guard was specified to catch accidental changes to other fields.

---

## Part 5 — Mapping group (family/traveller) booking

**Task:** Document the structure for group bookings.

**Delivered:** `book-appointment-GROUP-structure.md`

**Two traps that would break a scraper:**

1. **Cloned rows reuse the same IDs.** Adding group members clones a hidden template row into
   `#cloned2`, `#cloned3`… but only the date fields get unique IDs. Surname, passport, gender and
   nationality all keep the same ID as the hidden template. Looking a field up by ID returns the
   invisible template row, so data written there goes nowhere. Fields must be found **by name,
   scoped to the row**.
2. **Appointment types differ per centre.** Islamabad offers four types; Verification Office
   offers one. Type IDs cannot be hardcoded across centres.

Also documented: group size limits (2–5) and the four allocation methods, which change how slots
must be selected.

---

## Part 6 — Diagnosing the crash loop

**Task:** Work out why the scanner kept failing with `session not created: Chrome instance exited`.

**Delivered:** `agent-prompt-fix-crash-loop.md`, `agent-prompt-crash-investigation.md`

**Process:** read every session log, classified each failure, and found the original theory
(a leftover Chrome process locking the profile) only explained about a third of them.

**What the development agent then found — the actual bug:** the retry logic was checking for the
wrong error text. It only retried when the message said *"user data directory is already in use"*,
but Chrome actually says *"session not created: Chrome instance exited"*. The strings never
matched, so **the retry code never ran once**. All three retry attempts were dead code.

**Also fixed:** recovery closed the browser *after* waiting instead of before; errors were being
silently swallowed; a missing wait caused `#manage-account` lookups to fail on the slow-rendering
page.

**Corrections made along the way:**

- An early assumption that missing lock files proved there was no lock was **wrong** — those files
  are Linux/Mac only and never exist on Windows.
- A claim that no search had ever reached the portal was **wrong** — the logs showed real results
  and real rate limits. The warning that prompted it was a reporting bug, not a blocked search.

---

## Part 7 — Independent verification of the scanner's results

**Task:** Confirm the scanner's output is accurate, for the client.

**Delivered:** `GVCW_Verification_Report.pdf` (2 pages, written for a non-technical reader)

**Method:** ran a completely separate set of searches by hand across the same dates, with a
network probe recording the portal's own HTTP responses directly.

**Result: 16 of 16 dates verified — 100% match.**

- Every weekday returned exactly 10 slots, all taken, none free.
- Weekends returned "no appointments available" — the centre is closed.
- **14 August also returned closed — Pakistan's Independence Day.** The scanner detected this
  without being told it was a holiday, which is strong evidence the data is genuine.

**Later cross-check:** a further scanner run an hour later reproduced all 15 re-checked dates
identically, and its results for 26–31 August fit the same weekday/weekend pattern.

---

## Part 8 — Understanding the rate limit (HTTP 429)

**Task:** Explain the 429 errors and how many searches are possible.

**What was measured:**

| Run | Pacing | Searches before being blocked |
|---|---|---|
| Manual verification | ~4s | 17 |
| Scanner run 1 | 4.0s | 19 |
| Scanner run 2 | 9.0s | 21 |

**Findings:**

- The practical ceiling is roughly **17–21 searches** per session.
- Slower pacing helps only slightly — the limit counts total volume more than speed.
- The portal claims a 10-second cooldown. **The real cooldown is minutes** — over three minutes in
  testing. Honouring the stated 10 seconds walks straight back into the block.
- A blocked date is correctly saved and retried, never recorded as "no availability".

**On rotating proxies:** advised against. The evidence points to the limit tracking the **account**
rather than the network address — it followed the session across full browser restarts. Tests to
confirm this were provided (read the 429 response headers; check whether other pages on the site
are also blocked; try the same account from a phone). Separately, the portal's own notice states
that excess use results in cancellation of booked slots.

---

## Part 9 — Session expiry loop

**Task:** Fix the scanner getting stuck in an endless recovery loop.

**Delivered:** `agent-prompt-session-invalidation.md`

**The problem:** the scanner reported "still signed in", then crashed 30 seconds later on what was
clearly the login page.

**The underlying cause:** the session check asks *"is the login form missing?"* and concludes
*"then we must be signed in."* On a blank, still-loading page the login form is also missing — so
an empty page was being read as a healthy session.

**The fix specified:** wait for the page to actually finish rendering, then check for a
**positive** sign of being logged in (`#manage-account`), and treat "still loading", "logged out"
and "logged in" as three separate states rather than two.

**Also flagged:** re-logging in requires solving a reCAPTCHA, so a person must be present. In
unattended mode the scanner must clearly announce that it needs a human, and waiting for one must
not count as a failure.

---

## Outstanding issues

| Issue | Status |
|---|---|
| Gender set to value `3` = **OTHER**, not MALE (MALE is `2`) | **Not yet fixed** — wrong data would be submitted on a real booking |
| A failed request with status `0` is recorded as "no availability" | **Not yet fixed** — creates a genuine false negative |
| Cause of cold-start Chrome failures | Still unidentified; diagnostic logging now in place |
| Whether rate limiting kills the session | Open question, flagged for investigation |

---

## Files produced

| File | Purpose |
|---|---|
| `book-appointment-structure.md` | Individual booking page reference |
| `book-appointment-GROUP-structure.md` | Group booking page reference |
| `book-appointment-app.css` | Site-specific styling rules |
| `book-appointment-vendor.css` | Third-party styling rules |
| `profile-page-structure.md` | Profile page and menu reference |
| `agent-prompt-vac-sync.md` | Instructions for automatic city switching |
| `agent-prompt-fix-crash-loop.md` | Crash loop fix brief |
| `agent-prompt-crash-investigation.md` | Investigation brief for the crash loop |
| `agent-prompt-session-invalidation.md` | Session expiry fix brief |
| `GVCW_Verification_Report.pdf` | Client-facing accuracy report |
| `WORK-SUMMARY.md` | This document |

---

## The bottom line

The scanner works and its results are accurate — independently verified at 100% across every date
that could be checked. The reason no appointment has been found is straightforward: **there are
none.** Every working day showed exactly 10 slots with all 10 already taken, and the centre is
closed at weekends and on public holidays.

The remaining engineering work is about staying running reliably — surviving rate limits, session
expiry and browser crashes — rather than about finding slots that the portal is not offering.
