# Agent prompt — VAC sync step (paste this to your coding agent)

---

## TASK

Add a **VAC sync** step that runs *before* the existing Book Appointment logic.

The user picks a city in our frontend (`Islamabad` or `Lahore`). The GVCW account has a VAC
configured in its profile, and the Book Appointment page **always** queries whichever VAC the
profile is set to — there is no way to override it on the appointment form itself.

So before booking, the script must:

1. Read the profile's **current** VAC.
2. If it already matches the user's chosen city → change nothing, proceed.
3. If it differs → navigate to the profile page, switch the `VAC` dropdown, save, confirm the
   save took effect.
4. Then hand off to the existing Book Appointment logic.

This must be **idempotent** — never save when the value is already correct. Every write is a
round-trip the site can rate-limit, and the site's own banner warns that excess use gets slots
cancelled.

---

## SITE FACTS (verified against the live DOM — do not guess these)

**Base URL:** `https://pk-gr-services.gvcworld.eu`
**Auth:** session cookie; every page 401s when logged out.

### VAC option values

| value | city |
|---|---|
| `137` | Islamabad Visa Application Center for Greece |
| `138` | Lahore Visa Application Center for Greece |
| `140` | Verification Office (**not a booking centre — never select**) |
| `""` | "Select an option" placeholder |

Hardcode this map:

```js
const VAC = { islamabad: '137', lahore: '138' };
```

### Reading the current VAC — three options, cheapest first

**A. Sidebar text — present on every authenticated page, no navigation needed:**

```
===najeeb21===
VAC:[Lahore]
```

Parse with `/VAC:\[([^\]]+)\]/`. Cheapest, but it's a display string — if the site ever
localises it, the regex breaks. Use as a fast pre-check only.

**B. Hidden input on `/appointments/add` — authoritative id, no extra page load if you're
already heading there:**

```js
document.getElementById('vac').value   // "138"
```

**C. The profile select itself — authoritative, but costs a page load:**

```js
document.getElementById('vac').value
```

**Recommended:** use A or B to decide *whether* a change is needed. Only load the profile page
when a change is actually required.

### Navigating to the profile page

The profile URL embeds the user id: `/user/934868`. **Do not hardcode `934868`** — it differs per
account. Read it from the header link:

```js
const profileUrl = document.querySelector('#manage-account').getAttribute('href'); // "/user/934868"
```

`#manage-account` is a plain `<a>`, so just navigate to that href. Do **not** simulate the
dropdown unless your driver requires a visible click — the panel starts collapsed
(`<div id="account" class="box tr">`) and a strict driver may reject the element. If you must
click:

```js
document.querySelector('i.fa-user-circle.click').click();  // opens the panel
document.querySelector('#manage-account').click();
```

Cache the profile URL on first login so later runs skip the lookup.

### Profile page form

`<form id="user" class="classic">` at `/user/<id>`.

> **Gotcha 1:** `document.forms.user` does **not** resolve — a hidden `<input id="id">` shadows
> the form's `id` property. Use `document.querySelector('form.classic')` or
> `document.getElementById('user')`.

Relevant fields:

| Selector | `name` | Notes |
|---|---|---|
| `#vac` | `vac[id]` | **the one we change** |
| `#id` | `id` | hidden, `934868` |
| `#username` | `username` | `disabled` — will not submit, leave alone |
| `#email` | `email` | `readonly` |
| `#phonenumber` | `phonenumber` | `readonly` |
| `#newpassword` / `#verifypassword` | | **must stay empty** — anything here changes the password |
| `#btn-newuser` | | Save button |

### ★★ RULE: change `#vac` ONLY

`#vac` is the **single** field this flow is permitted to modify. Every other field on
`/user/<id>` must be left exactly as loaded — do not set it, clear it, re-select it, normalise
it, or "fix" it.

This applies especially to `#country`, `#language` and `#timezone`. They carry `required` and an
agent may be tempted to re-assert them "to be safe". Don't. They are already populated by the
server; re-setting them risks writing a different value than the one on file, and any select2
`.trigger('change')` on them may fire app handlers with side effects.

Enforce it in code, not just by intent — snapshot before, diff after:

```js
const SNAPSHOT_FIELDS = ['username','firstname','lastname','email','country',
                         'newpassword','verifypassword','language','timezone',
                         'phonenumberprefix','phonenumber','id'];

const snap = () => Object.fromEntries(
  SNAPSHOT_FIELDS.map(id => [id, document.getElementById(id)?.value ?? null]));

const before = snap();
// ... change #vac only ...
const after = snap();
const drift = SNAPSHOT_FIELDS.filter(k => before[k] !== after[k]);
if (drift.length) throw new Error('Unexpected field drift: ' + drift.join(', '));
```

Abort before clicking Save if the diff is non-empty. Assert the password fields are empty strings
in both snapshots.

The Save button posts the whole form, so anything the script disturbs gets written to the
account. Treat every field except `#vac` as read-only.

### ★ Gotcha 3 — `#vac` is a select2 widget, not a bare `<select>`

The real `<select>` is visually hidden (`.select2-hidden-accessible`, clipped to 1×1px) and a
`<span class="select2 …>` is drawn in its place. Consequences:

- A native driver `selectOption()` / `Select.select_by_value()` may fail — the element isn't
  visible.
- Setting `.value` directly updates the DOM but **does not** repaint the widget and **does not**
  fire the change handlers the app listens on.

**Correct way — set the value and let select2 broadcast the change (jQuery is already on the
page):**

```js
window.jQuery('#vac').val('137').trigger('change');
```

**Fallback if jQuery isn't reachable in your context:**

```js
const s = document.getElementById('vac');
s.value = '137';
s.dispatchEvent(new Event('change', { bubbles: true }));
```

Run this via your driver's script-execution call (`page.evaluate` / `execute_script` /
`Runtime.evaluate`). After setting, assert **both**:

```js
document.getElementById('vac').value === '137'                                 // native state
document.querySelector('#vac-wrap .select2-selection__rendered').textContent   // widget repainted
  .includes('Islamabad')
```

If the widget text didn't update, the change event didn't land — fail loudly rather than
proceeding, or you'll book at the wrong city.

### ★ Gotcha 4 — there is no native form submit

Both buttons are inline-JS handlers, not submit inputs:

```html
<button id="btn-newuser" class="btn big blue" type="button" onclick="saveprofile(this)">
  <span>Save</span>
</button>
<a class="btn red" onclick="unsubscribe(this, 'post');"><span>Unsubscribe</span></a>
```

- `form.submit()` will **not** work. Click `#btn-newuser` or call `saveprofile(...)`.
- **Never** touch the red `.btn.red` / `unsubscribe()` control — it deletes the registration.
  Add an explicit guard so no selector in this flow can ever match it.

### Gotcha 5 — the appointment page caches the VAC

`/appointments/add` reads the VAC at page load into a hidden `#vac` input and renders
`VAC: <name> Visa Application Center for Greece`. After changing the profile you **must
re-navigate to (or hard-reload) `/appointments/add`** — an already-open tab keeps the stale
value and will silently search the wrong city.

---

## REQUIRED IMPLEMENTATION

```
ensureVac(targetCity):                      // "islamabad" | "lahore"
    targetId = VAC[targetCity]              // throw on unknown city
    current  = readCurrentVac()             // strategy A or B
    if current == targetId:
        log "VAC already <city>, skipping"
        return false                        // nothing changed
    profileUrl = cachedProfileUrl or read #manage-account href
    navigate(profileUrl)
    waitFor('#vac')                         // page is JS-rendered, see timing note
    before = snapshotAllFieldsExceptVac()
    assert '#newpassword'.value == '' and '#verifypassword'.value == ''
    setSelect2('#vac', targetId)            // ← the ONLY field touched
    assert native value == targetId AND widget text contains city name
    assert snapshotAllFieldsExceptVac() == before      // no drift, else ABORT
    click('#btn-newuser')
    waitForSaveResult()
    reload(profileUrl); assert '#vac'.value == targetId    // verify from a fresh load
    return true                             // changed
```

Then, in the existing booking flow:

```
changed = ensureVac(userSelectedCity)
navigate('/appointments/add')               // ALWAYS re-navigate, changed or not
assert document.getElementById('vac').value == VAC[userSelectedCity]   // hard gate
... existing booking logic ...
```

That last assertion is the safety net. If it ever fails, **abort** — never fall through into
booking with a mismatched VAC.

### Timing

The app is client-rendered and slow. On a cold load `document.readyState` reaches `complete`
while `<body>` is still empty; content appears a few seconds later after
`GET /api/v1/translations` and the page's own XHRs resolve. **Do not** wait on `load` or a fixed
`sleep`. Poll for the actual element:

```
waitFor(() => document.querySelector('#vac')?.value !== undefined, timeout=30s, interval=250ms)
```

Budget ~10s typical, 30s timeout.

---

## THINGS I COULD NOT VERIFY — probe these first, then code

I did not click Save on a live account, so the post-save behaviour is unconfirmed. Before writing
the final flow, run these once manually and report what happens:

1. **What does `saveprofile()` do on success?** Full page reload, redirect, inline toast, or a
   modal? This determines what `waitForSaveResult()` waits on.
2. **Is there a confirmation modal?** The site uses modals elsewhere — the appointment page shows
   an `Error` modal dismissed by a button whose text is `OK`. If Save raises one, the flow needs a
   dismiss/confirm step.
3. **Does saving require any other field?** `#country`, `#language`, `#timezone` are all marked
   `required`. They're prefilled, but confirm the POST doesn't reject on a field the disabled
   `#username` would otherwise have supplied.
4. **What request does Save fire?** Capture method, URL and payload. If it's a clean JSON/form
   POST, consider calling it directly with the session cookie and skipping the UI entirely — far
   faster and less brittle than driving select2.
5. **Rate limiting / CAPTCHA on save?** The login form and the booking step both use reCAPTCHA v2
   (sitekey `6LcnlCoUAAAAAJLjWXXaByTFyuOLf4K0gGu5r3d2`). I saw none on the profile form, but
   confirm — a captcha here would make automated VAC switching impractical.

Report findings before implementing, so the retry/verify logic matches reality instead of
assumptions.

---

## CONSTRAINTS

- **`#vac` is the only field you may modify.** Everything else on the profile page is read-only.
  Snapshot before, diff after, abort on any drift. The Save button posts the entire form.
- **Idempotent.** No save when the value already matches.
- **Never** select VAC `140` (Verification Office).
- **Never** trigger `unsubscribe()`.
- **Never** write to `#newpassword` / `#verifypassword`.
- Verify after saving by reloading, not by trusting the in-page state.
- Abort loudly on any assertion failure. Booking at the wrong city is worse than not booking.
- Keep VAC switches to the minimum — the platform explicitly monitors excess automated use.
