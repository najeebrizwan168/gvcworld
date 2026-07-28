# GVCW Book Appointment — Page Structure

**URL:** `https://pk-gr-services.gvcworld.eu/appointments/add`
**Requires:** authenticated session (unauthenticated GET returns HTTP 401)
**Captured:** 2026-07-28 · account `najeeb21` · VAC Islamabad (`vac=137`)

---

## 1. Stack / rendering notes

| Item | Detail |
|---|---|
| Assets | `/dist/css/vendor.css`, `/dist/css/app.css`, `/dist/js/vendor.js`, `/dist/js/app.js` |
| Widgets | jQuery + **select2** (every `<select>` is `select2-hidden-accessible`, mirrored by a `<span class="select2 …>` widget) |
| Date pickers | jQuery UI — `.datepicker.hasDatepicker`, modifiers `.lock-before-today`, `.enable-today` |
| Global JS namespace | `ubi` (e.g. sidebar links use `javascript:ubi.home()`) |
| Anti-bot | Google reCAPTCHA v2 checkbox, sitekey `6LcnlCoUAAAAAJLjWXXaByTFyuOLf4K0gGu5r3d2` |
| Second factor | SMS OTP required before booking (`#btn-onetimepassword` → `#onetimepassword`) |
| Other API seen | `GET /api/v1/translations`, `POST /anonymous/home` |

> **Important for automation:** `#btn-search` runs **client-side validation first**. With required
> applicant fields empty it shows a modal "Please check the form fields again" and fires **no**
> network request. All required fields must be populated before the availability call happens.

---

## 2. Form root

```html
<form id="appointment" class="classic">
```

Everything below lives inside this form.

---

## 3. Hidden / session fields

| Selector | Name | Notes |
|---|---|---|
| `#no-results` | — | value `"No results found"` (i18n string) |
| `#otpuser` | `otpuser` | **serialised User object incl. username + password hash — treat as secret** |
| `#vpm` | `vpm` | empty on load |
| `#vac` | `vac` | `137` = Islamabad VAC for Greece |
| `#groupid` | `groupid` | empty for individual bookings |
| `#selectedtime` | `selectedtime` | set after picking a slot |
| `#init_selecteddate` | `init_selecteddate` | |
| `#init_selectedtime` | `init_selectedtime` | |
| `#init_periodslotid` | `init_periodslotid` | |
| `#submissionMsgCheck` | `submissionMsgCheck` | i18n validation string |

---

## 4. Top block — appointment type

Layout is a `<table>` of `<td class="form-item required">` cells.

### `#type` — "Appointment for" *(required)*

| value | label |
|---|---|
| `0` | Submission Schengen Visa (Short term – Type C) |
| `2` | National visa (Long term - type D) |
| `6` | Prime Time (optional service at an additional charge) |
| `26` | Long-Term Type D (Seasonal/Dependent Employment) |

### `#travelpurposes` — "Travel Purpose"
Inside `#travelpurposesDiv.form-item.required.hidden`. Empty on load; populated by JS depending
on `#type`.

### `#bookingfor` — "Booking as" *(required)*

| value | label |
|---|---|
| `0` | Individual |
| `1` | Group (Family/Traveler) |

### `#members` — "# in Group"
Inside `#membersDiv.form-item.hidden`. Options `2`–`5`. Revealed when `#bookingfor = 1`.

### Contact (both `readonly`, prefilled from profile)

```html
<input id="ind_email"       class="form-control noupper" name="email"       readonly>
<select id="phonenumberprefix" name="phonenumberprefix[id]">  <!-- 197 = PAKISTAN +92 -->
<input id="ind_phonenumber" class="form-control"          name="phonenumber" readonly>
```

---

## 5. Client Information — `#groupTable`

```html
<h2 class="appointment_subtitle"><span>Client Information</span></h2>
<div id="groupInfoDiv" class="form-item">
  <table id="groupTable">
    <thead> Surname | Name | Date of Birth | Passport | Passport's Expiration Date | Gender | Nationality | (time) </thead>
    <tbody id="groupBody"> … </tbody>
```

### Row 1 — primary applicant, prefix `gp_`

| Selector | `name` | Type | Prefilled |
|---|---|---|---|
| `#gp_surname` | `applicants[][surname]` | text, required | `RIZWAN` |
| `#gp_firstname` | `applicants[][firstname]` | text, required | `NAJEB` |
| `#gp_dateofbirth` | `applicants[][dateofbirth]` | `.datepicker`, required | empty |
| `#gp_passportnumber` | `applicants[][passportnumber]` | text, required | empty |
| `#gp_traveldocumentvaliduntil` | `applicants[][traveldocumentvaliduntil]` | `.datepicker.lock-before-today` | empty |
| `#gp_gender` | `applicants[][gender[id]]` | select | empty |
| `#gp_nationality` | `applicants[][nationality[id]]` | select | empty |
| `#gp_periodslotid` | `applicants[][periodslotid]` | hidden | set on slot pick |
| `#gp_time` | — | `<span>` display only | |

### Row 2 — template row, prefix `ex_`

`<tr id="secondTr" class="hidden">` — identical field set with `ex_` ids. This is the **clone
template** JS uses to add group members. Note every row reuses the same `applicants[][…]` array
names, so **server-side field order matters** — ids differ, `name` attributes do not.

**Gender options:** `1` FEMALE · `2` MALE · `3` OTHER
**Nationality options:** 300 entries, e.g. `2` AFGHANISTAN, `7` ALBANIA, `79` ALGERIA, `11` ARGENTINA …
(Pakistan and the rest are in the same `id → NAME` list; dump the full `<option>` set if you need it.)

---

## 6. Date search block

```html
<div class="form-item dpicker required">
  <label for="datefrom">Appointment Date</label>
  <input id="datefrom" name="datefrom"
         class="form-control datepicker lock-before-today enable-today hasDatepicker"
         required>                     <!-- default = tomorrow, e.g. 29/07/2026 (dd/mm/yyyy) -->
  <i class="far fa-calendar-alt"></i>
</div>

<td id="appointmentmethodDiv" class="form-item hidden">
  <select id="appointmentmethod" name="appointmentmethod">   <!-- group bookings only -->
</td>

<button id="btn-search" class="btn blue" type="button"><span>Search</span></button>
```

### `#appointmentmethod` (group only)

| value | label | helper div |
|---|---|---|
| `1` | Same time | `#appointmentmethodDetails1` |
| `2` | Consecutive time slots | `#appointmentmethodDetails2` |
| `3` | Next available slots | `#appointmentmethodDetails3` |
| `4` | Select one by one | `#appointmentmethodDetails4` |

### Failure state

```html
<div id="resultMessage" class="appointment_alert appointment_alert-danger hidden">
  You cannot book an appointment on this date. Please choose another date.
</div>
```

**This is the element to watch when polling for availability.** `.hidden` present = slots may
exist; `.hidden` removed = no slots on that date.

---

## 7. Results panel — `#appointment_box`

Hidden until a successful search.

```html
<div id="appointment_box" class="hidden">
  <h4>To complete your reservation press BOOK APPOINTMENT from below</h4>
  <div class="appointment_box">
    <div id="split-main">
      <nav id="form-tabs">
        <ul>
          <li id="tab-monday" class="active"><span class="menu-label">Monday</span></li>
          <li id="tab-tuesday">…</li>   <!-- through tab-sunday -->
        </ul>
      </nav>
      <div id="main-area" class="tabs-monday">   <!-- class tracks the active day tab -->
        <div id="resultDiv" class="form-item"></div>   <!-- ← slot grid injected here -->
        <div class="info_area">
          Available appointments / Booked appointments
          <span id="selectedDateMsg">29/07/2026</span> at <span id="selectedTimeMsg"></span>
          <div id="alertmsg" class="appointment_alert appointment_alert-warning">
            Please select time slot to proceed with your reservation.
          </div>
        </div>
      </div>
```

**Key scraping targets**

- `#resultDiv` — container the time-slot grid is rendered into
- `#main-area` — its class (`tabs-monday` … `tabs-sunday`) tells you which day is displayed
- `#form-tabs li#tab-<day>` — day switcher; `.active` marks current
- `#selectedDateMsg` / `#selectedTimeMsg` — confirm the chosen slot
- `#alertmsg` — warning state before a slot is chosen

---

## 8. Booking / submit block

```html
<div id="btn-onetimepassword-wrap" class="form-item nolabel">
  <button id="btn-onetimepassword" class="btn blue" type="button">
    <span><span>Request OTP code (via Mobile) for Appointment</span></span>
  </button>
</div>

<div class="form-button-wrapper">
  <div class="form-item required">
    <span id="submissiontext">I have read and accepted the <a><u>Terms of Use</u></a> and <a><u>Privacy Policy</u></a></span>
    <input id="submitinfo" class="form-control" name="submitinfo" type="checkbox">
  </div>
  <button id="btn-new-appointment" class="btn blue" type="button"><span>Book your appointment</span></button>
</div>

<div id="onetimepassword-wrap" class="form-item nolabel">
  <input id="onetimepassword" class="tc noupper" name="onetimepassword" placeholder="Enter the OTP code">
</div>

<div class="form-item resetrequired">
  <div id="recaptcha" class="g-recaptcha">
    <iframe name="a-…" role="presentation"></iframe>
    <textarea id="g-recaptcha-response" class="g-recaptcha-response" name="g-recaptcha-response"></textarea>
  </div>
</div>
```

**Booking gate = three factors:** `#submitinfo` checked + valid SMS OTP in `#onetimepassword` +
solved reCAPTCHA in `#g-recaptcha-response`.

---

## 9. Time-slot state classes (from `app.css`)

The slot grid injected into `#resultDiv` uses `.appointment_slot` plus one state modifier.
**This is the single most useful thing for automation** — it is how you tell a free slot from a
taken one without parsing text:

| Class | Meaning | Visual |
|---|---|---|
| `.appointment_slot_enabled` | **FREE — clickable** | green outline, transparent fill, `cursor: pointer` |
| `.appointment_slot_selected` | your current pick | solid green fill, white text |
| `.appointment_slot_notselectable` | free but not pickable in this mode | green outline, grey fill |
| `.appointment_slot_reserved` | held / reserved | green outline, grey fill, white text |
| `.appointment_slot_disabled` | taken / unavailable | dark red border, grey fill |

```js
// availability check
document.querySelectorAll('#resultDiv .appointment_slot_enabled').length
```

---

## 10. CSS class inventory

Layout classes used by this section (385 matching rules across `vendor.css` + `app.css`):

`classic` · `form-item` · `required` · `hidden` · `nolabel` · `resetrequired` · `dpicker` ·
`form-control` · `noupper` · `tc` · `form-button-wrapper` · `btn` · `blue` ·
`appointment_box` · `appointment_box_tip` · `appointment_tip` · `appointment_subtitle` ·
`appointment_alert` · `appointment_alert-danger` · `appointment_alert-warning` ·
`info_area` · `menu-label` · `active` · `tabs-monday`…`tabs-sunday` ·
`select2` · `select2-container` · `select2-container--default` · `select2-hidden-accessible` ·
`datepicker` · `hasDatepicker` · `lock-before-today` · `enable-today` ·
`far` · `fa-calendar-alt` · `g-recaptcha` · `g-recaptcha-response`

Semantics worth knowing:

- `.hidden` — the app's show/hide toggle; conditional fields are in the DOM from page load and
  simply carry this class. Presence/absence of `.hidden` is the reliable state signal.
- `.required` — marker on the wrapper (`td`/`span`/`div`), not on the input; the input carries
  `required="required"` separately.
- `.form-item` — the universal field wrapper.
- `.dpicker` — wrapper for a datepicker + its calendar icon.

**Full rule text is in the sibling files:**

- `book-appointment-app.css` — 283 site-specific rules (the appointment styling, slot states,
  form layout, responsive breakpoints)
- `book-appointment-vendor.css` — 109 rules of stock Font Awesome 5 + select2 4.x

Both were pulled from the live CSSOM, so values are the browser's computed/normalised form
(`rgb()` instead of hex, longhand instead of shorthand) rather than the original authored source.
