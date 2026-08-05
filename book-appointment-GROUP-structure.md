# GVCW — Book Appointment, GROUP (Family/Traveler) mode

**URL:** `https://pk-gr-services.gvcworld.eu/appointments/add`
**Captured:** 2026-08-05 · account `najeeb21` · **VAC was set to `140` Verification Office**
Companion to `book-appointment-structure.md` (individual mode). Only the deltas are here.

---

## 0. Read this first — two things that will break a naive scraper

### ★★ Cloned rows reuse the SAME ids

When you switch to group mode the app clones the hidden template row `#secondTr` once per extra
member. The clones **do not get unique ids**. Only the two datepicker fields are suffixed:

| Field | Template row (`#secondTr`) | Clone 2 (`#cloned2`) | Clone 3 (`#cloned3`) |
|---|---|---|---|
| surname | `ex_surname` | `ex_surname` ⚠ | `ex_surname` ⚠ |
| firstname | `ex_firstname` | `ex_firstname` ⚠ | `ex_firstname` ⚠ |
| **dateofbirth** | `ex_dateofbirth` | `ex_dateofbirth2` ✅ | `ex_dateofbirth3` ✅ |
| passportnumber | `ex_passportnumber` | `ex_passportnumber` ⚠ | `ex_passportnumber` ⚠ |
| **traveldocumentvaliduntil** | `ex_traveldocumentvaliduntil` | `ex_traveldocumentvaliduntil2` ✅ | `ex_traveldocumentvaliduntil3` ✅ |
| gender | `ex_gender` | `ex_gender` ⚠ | `ex_gender` ⚠ |
| nationality | `ex_nationality` | `ex_nationality` ⚠ | `ex_nationality` ⚠ |
| periodslotid | `ex_periodslotid` | `ex_periodslotid` ⚠ | `ex_periodslotid` ⚠ |

**Consequence:** `document.getElementById('ex_surname')` returns the input inside the
**hidden template row**, never the visible clone. Writing to it puts data nowhere the user can
see, and the template row is `display:none` so it may not even submit.

**Never address group fields by id. Always scope to the row.**

```js
// WRONG — hits the hidden #secondTr template
document.getElementById('ex_surname').value = 'KHAN';

// RIGHT — scope to the row, select by name
const row = document.querySelector('#groupBody tr#cloned2');
row.querySelector('[name="applicants[][surname]"]').value = 'KHAN';
```

### ★★ `#type` options depend on the VAC

With VAC `137` (Islamabad) `#type` offered 4 options. With VAC `140` (Verification Office) it
offers exactly one:

```
value="24" -> Document Verification
```

The list is rendered server-side per VAC. **Do not hardcode type ids across centres.** Read the
options at runtime, or key them per VAC after confirming each one.

Islamabad (`137`) values seen previously: `0` Submission Schengen Visa (Short term – Type C),
`2` National visa (Long term - type D), `6` Prime Time, `26` Long-Term Type D (Seasonal/Dependent).
Lahore (`138`) not yet captured.

> Heads-up: the account's VAC is currently `140` (**Verification Office**), not a booking centre.
> Sidebar reads `VAC:[Verification Office]`. Set it to `137` or `138` before real booking.

---

## 1. Switching into group mode

```js
window.jQuery('#bookingfor').val('1').trigger('change');   // 0 = Individual, 1 = Group
window.jQuery('#members').val('4').trigger('change');      // 2..5
```

`.trigger('change')` is mandatory — these are select2 widgets and the row-cloning runs off the
change handler. Allow ~1s for the clones to render, then poll for the row count.

### What changes in the DOM

| Element | Individual | Group |
|---|---|---|
| `#membersDiv` | `form-item hidden` | `form-item required` |
| `#appointmentmethodDiv` | `form-item hidden` | `form-item required` |
| `#groupBody` rows | 2 (`gp_` row + hidden `#secondTr`) | 2 + (members−1) clones |
| `#appointmentmethodDetails1` | `form-item hidden` | `form-item` (visible; matches selected method) |

`#members` options: `2`, `3`, `4`, `5` (max group size 5).

---

## 2. Row layout after switching

For `#members = 4`, `#groupBody` contains **5** `<tr>`:

```
row0  <tr>                          visible   ← primary applicant, gp_ prefix, prefilled
row1  <tr id="secondTr" class="hidden">       ← TEMPLATE, never write to it, never read from it
row2  <tr id="cloned2" class="form-item">     visible   ← member 2
row3  <tr id="cloned3" class="form-item">     visible   ← member 3
row4  <tr id="cloned4" class="form-item">     visible   ← member 4
```

Pattern: clones are `#cloned2` … `#cloned<members>`. Row0 has **no id**.

### Correct row enumeration

```js
const rows = [...document.querySelectorAll('#groupBody tr')]
               .filter(tr => !tr.classList.contains('hidden'));
// rows[0] = primary applicant, rows[1..] = members 2..N
console.assert(rows.length === Number(document.getElementById('members').value));
```

Filtering on `.hidden` is more robust than `#secondTr` by id, in case the app adds other
hidden rows later.

---

## 3. Field names — identical on every row

All rows, including the primary, submit under the same array names. **Row order is what maps a
value to a person** — there are no indices in the names.

| Column | `name` |
|---|---|
| Surname | `applicants[][surname]` |
| Name | `applicants[][firstname]` |
| Date of Birth | `applicants[][dateofbirth]` |
| Passport | `applicants[][passportnumber]` |
| Passport's Expiration Date | `applicants[][traveldocumentvaliduntil]` |
| Gender | `applicants[][gender[id]]` |
| Nationality | `applicants[][nationality[id]]` |
| (slot, hidden) | `applicants[][periodslotid]` |

Classes on a clone row (`#cloned2`):

```html
<input  name="applicants[][surname]"                    class="form-control">
<input  name="applicants[][firstname]"                  class="form-control">
<input  name="applicants[][dateofbirth]"                class="form-control datepicker hasDatepicker">
<input  name="applicants[][passportnumber]"             class="form-control">
<input  name="applicants[][traveldocumentvaliduntil]"   class="form-control datepicker lock-before-today hasDatepicker">
<select name="applicants[][gender[id]]"                 class="form-control">
<select name="applicants[][nationality[id]]"            class="form-control allowclear">
<input  name="applicants[][periodslotid]"               type="hidden">
```

Note `.allowclear` on nationality — it renders an `×` clear button in the select2 widget.

### Filling one member

```js
function fillMember(row, m) {
  const q = n => row.querySelector(`[name="applicants[][${n}]"]`);
  q('surname').value        = m.surname;
  q('firstname').value      = m.firstname;
  q('passportnumber').value = m.passport;

  // datepickers: jQuery UI is bound; set value then fire change so validation clears
  setDate(q('dateofbirth'), m.dob);                    // dd/mm/yyyy
  setDate(q('traveldocumentvaliduntil'), m.expiry);    // dd/mm/yyyy

  // select2-backed selects — .val().trigger('change') or the widget won't repaint
  window.jQuery(q('gender[id]')).val(m.genderId).trigger('change');
  window.jQuery(q('nationality[id]')).val(m.nationalityId).trigger('change');
}
```

Reference values: gender `1` FEMALE, `2` MALE, `3` OTHER. Nationality is the same 300-entry
id→country list as individual mode.

**Date format is `dd/mm/yyyy`** (`#datefrom` was observed holding `05/08/2026`). The
`.lock-before-today` class on the expiry picker means jQuery UI blocks past dates — typing a past
value directly may be silently reverted, so verify the field after writing.

---

## 4. Allocation method — `#appointmentmethod`

Group-only, `name="appointmentmethod"`. Revealed alongside `#members`.

| value | label | helper div | meaning |
|---|---|---|---|
| `1` | Same time | `#appointmentmethodDetails1` | all members at one identical slot |
| `2` | Consecutive time slots | `#appointmentmethodDetails2` | back-to-back slots |
| `3` | Next available slots | `#appointmentmethodDetails3` | earliest N slots, not necessarily together |
| `4` | Select one by one | `#appointmentmethodDetails4` | pick a slot per member individually |

Only the div matching the current selection lacks `.hidden`. Default is `1`.

**This changes how slots come back.** With `4` you must set `applicants[][periodslotid]` on each
row separately — one slot pick per member. With `1`/`2`/`3` the app resolves all rows from a
single selection. Branch your slot-picking logic on this value.

If your users always want the family together, use `1` and fall back to `3` when the search
returns nothing — the site's own helper text says exactly that.

---

## 5. Search block (unchanged from individual mode)

```html
<input  id="datefrom" name="datefrom" class="form-control datepicker lock-before-today enable-today hasDatepicker" required>
<select id="appointmentmethod" name="appointmentmethod">        <!-- now visible -->
<button id="btn-search" class="btn blue" type="button"><span>Search</span></button>
```

`#btn-search` validates client-side first. With any required cell blank across **any** visible
row it opens a modal reading *"Please check the form fields again"* and fires **no** network
request. Every visible row must be complete before the availability call happens.

Modal dismiss: click the element whose text is `OK`.

Result targets, same as individual mode:

- `#resultMessage.appointment_alert-danger` — loses `.hidden` when the date has no slots
- `#appointment_box` — loses `.hidden` on a successful search
- `#resultDiv` — slot grid injected here
- `#main-area` class `tabs-monday` … `tabs-sunday`; `#form-tabs li#tab-<day>.active`
- Slot states: `.appointment_slot_enabled` (free), `_selected`, `_notselectable`, `_reserved`,
  `_disabled` — see `book-appointment-app.css`

---

## 6. Submit gate (unchanged)

`#submitinfo` checkbox + SMS OTP via `#btn-onetimepassword` → `#onetimepassword` + reCAPTCHA v2
(`#g-recaptcha-response`, sitekey `6LcnlCoUAAAAAJLjWXXaByTFyuOLf4K0gGu5r3d2`), then
`#btn-new-appointment`.

---

## 7. Group-mode checklist

```
1.  ensure VAC is 137 or 138 (NOT 140)          → see agent-prompt-vac-sync.md
2.  reload /appointments/add                     → picks up VAC, repopulates #type
3.  read #type options at runtime, pick by label not by hardcoded id
4.  jQuery('#bookingfor').val('1').trigger('change')
5.  jQuery('#members').val(N).trigger('change')
6.  poll until visible #groupBody rows === N     (~1s)
7.  jQuery('#appointmentmethod').val(M).trigger('change')
8.  rows = visible #groupBody tr   (EXCLUDE #secondTr)
9.  fill each row via [name=...] scoped to the row — NEVER by id
10. set #datefrom (dd/mm/yyyy)
11. click #btn-search; if the "check the form fields" modal appears, a row is incomplete
12. watch #resultMessage / #appointment_box / #resultDiv
13. slot pick: method 4 → one periodslotid per row; methods 1-3 → single selection
```

---

## 8. Still unverified

- The availability API — `#btn-search` has never passed validation in my sessions, so the
  endpoint, payload and response shape are still unknown. Needs one complete row set.
- A real slot element's markup (`#resultDiv` is empty until a search succeeds). Only the state
  classes are known, from CSS.
- Lahore (`138`) `#type` option list.
- Whether row order in the POST reliably maps to member order (the `applicants[][…]` names carry
  no index — worth confirming against a real submission).
