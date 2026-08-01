# GVCW — Profile menu + User Edit page

**Captured:** 2026-08-01 · account `najeeb21` (user id `934868`)

> Two corrections to the brief: the profile control is in the **top-right**, not top-left, and the
> button reads **"Manage Account"**, not "Manage Profile".

---

## 1. The header profile control

The account icon sits in `#header-wrapper > #mini-menu-wrapper`. It is **not** a link — it's an
`<i>` with an inline `onclick` that toggles a dropdown panel.

### Trigger icon

```html
<i onclick="miniMenuToggleBox(this)" class="fas fa-user-circle click mm-icons" aria-hidden="true"></i>
```

Selector for your tool:

```css
i.fas.fa-user-circle.click.mm-icons
```

```js
document.querySelector('i.fa-user-circle.click').click();   // opens the panel
```

### Dropdown panel — `#account`

Closed state is `class="box tr"`; `miniMenuToggleBox()` adds/removes the open class.

```html
<div id="account" class="box tr">
  <div class="box-field box-close-btn">
    <i class="fas fa-times click" onclick="miniMenuToggleBox(this)"></i>
  </div>
  <div class="box-field box-title"><span>Profile Navigation</span></div>
  <div class="box-field box-image"><i class="fas fa-user-circle"></i></div>
  <div class="box-field box-name">NAJEB RIZWAN</div>
  <div class="box-field box-email"><span>najeebraprizwan@gmail.com</span></div>
  <div class="box-field box-buttons">
    <a href="/user/934868" id="manage-account" class="btn darkblue"><span>Manage Account</span></a>
    <button class="btn blue" onclick="ubi.logout(this)"><span>Sign out</span></button>
  </div>
</div>
```

### ★ The Manage Account button — exact HTML

```html
<a href="/user/934868" id="manage-account" class="btn darkblue"><span>Manage Account</span></a>
```

| | |
|---|---|
| Selector | `#manage-account` |
| `href` | `/user/934868` — **contains the user id, differs per account** |
| Visible without opening the panel? | Yes — `getBoundingClientRect()` is non-zero even while the dropdown is closed |

**Click strategies, in order of robustness:**

```js
// 1. Best — skip the UI entirely, the href is a plain link
location.href = document.querySelector('#manage-account').getAttribute('href');

// 2. Direct click; no need to open the dropdown first (element is not display:none)
document.querySelector('#manage-account').click();

// 3. Human-like — open panel, then click
document.querySelector('i.fa-user-circle.click').click();
document.querySelector('#manage-account').click();
```

For a real browser driver (Selenium/Playwright) prefer strategy 1 or 3 — strategy 2 can fail an
"element is not interactable" check if the driver enforces visibility on the collapsed panel.

### Rest of the header, for reference

```html
<header id="header-wrapper" class="column tr" role="banner">
  <div id="menu-toggle" class="classic-btn tr" onclick="mainMenuToggle(this)">
    <i class="fa fa-bars"></i>
  </div>
  <div id="search-fullbg" onclick="ubi.search.close()"></div>
  <div id="mini-menu-wrapper">
    <nav><ul>
      <li id="lang-wrapper">
        <div class="icon-wrapper">
          <i class="fas fa-globe click mm-icons" onclick="miniMenuToggleBox(this)"></i>
          <div class="icon-indicator tr"><span class="flag-icon flag-icon-gb"></span></div>
        </div>
        <div class="box dropdown tr"><ul>
          <li class="lang-selector tr active">
            <span>English</span><span class="flag-icon flag-icon-gb tr"></span>
          </li>
        </ul></div>
      </li>
      <li>
        <i class="fas fa-user-circle click mm-icons" onclick="miniMenuToggleBox(this)"></i>
        <div id="account" class="box tr"> … </div>
      </li>
      <li id="menu-toggle-small">
        <i class="fa fa-bars click mm-icons" onclick="mainMenuToggle(this)"></i>
      </li>
    </ul></nav>
  </div>
</header>
```

Global JS helpers exposed here: `mainMenuToggle()`, `miniMenuToggleBox()`, `ubi.logout()`,
`ubi.search.close()`.

---

## 2. Profile page — `/user/<id>`

**URL:** `https://pk-gr-services.gvcworld.eu/user/934868`
**Title:** `Visa | User | Edit`
**Form:** `<form id="user" class="classic">`

> Gotcha: `document.forms` won't resolve `form.id` here — a hidden `<input id="id">` shadows the
> form's own `id` property. Use `document.querySelector('form.classic')` or
> `document.getElementById('user')`.

### Full field table

| Selector | `name` | Type | State | Value |
|---|---|---|---|---|
| `#id` | `id` | hidden | — | `934868` |
| `#no-results` | — | hidden | — | `"No results found"` (i18n) |
| `#username` | `username` | text | **disabled**, required | `najeeb21` |
| `#firstname` | `firstname` | text | required | `NAJEB` |
| `#lastname` | `lastname` | text | required | `RIZWAN` |
| `#email` | `email` | email | **readonly**, required | `najeebraprizwan@gmail.com` |
| `#country` | `country[id]` | select, 1 opt | required | PAKISTAN |
| `#vac` | `vac[id]` | select, 4 opts | required | Lahore |
| `#newpassword` | `newpassword` | password | — | empty |
| `#verifypassword` | `verifypassword` | password | — | empty |
| `#language` | `language` | select, 2 opts | required | English |
| `#timezone` | `timezone` | select, 633 opts | required | `Asia/Karachi` |
| `#phonenumberprefix` | `phonenumberprefix[id]` | select, 2 opts | — | PAKISTAN +92 |
| `#phonenumber` | `phonenumber` | text, `maxlength=14` | **readonly** | `3341437718` |

Inputs carry `.form-control`; most also carry `.noupper`. Wrappers are `div.form-item`, with
`.required` added where the field is mandatory (that's what renders the red `*` via
`.form-item.required label::after`).

### Structure

```html
<form id="user" class="classic">
  <input id="no-results" type="hidden" value="No results found">
  <input id="id" name="id" type="hidden" value="934868">

  <div class="form-item required noupper">
    <label for="username">Username</label>
    <input id="username" class="form-control noupper" name="username" type="text"
           placeholder="Username" required disabled value="najeeb21">
  </div>

  <div class="form-item required noupper">
    <label for="firstname">First Name</label>
    <input id="firstname" class="form-control noupper" name="firstname" type="text"
           placeholder="First Name" required value="NAJEB">
  </div>

  <div class="form-item required noupper">
    <label for="lastname">Last Name</label>
    <input id="lastname" class="form-control noupper" name="lastname" type="text"
           placeholder="Last Name" required value="RIZWAN">
  </div>

  <div class="form-item required noupper">
    <label for="email">Email</label>
    <span>If this Email is not valid, you must change it. To do this, you must first cancel
          your registration (Unsubscribe) and then register again with a valid Email.</span>
    <input id="email" class="form-control noupper" name="email" type="email"
           placeholder="Email" required readonly value="najeebraprizwan@gmail.com">
  </div>

  <div id="country-wrap" class="form-item required">
    <label for="country">Country</label><br>
    <select id="country" name="country[id]"> … </select>
  </div>

  <div id="vac-wrap" class="form-item required">
    <label for="vac">VAC</label>
    <select id="vac" name="vac[id]"> … </select>
  </div>

  <div class="form-item">
    <label for="newpassword">New Password</label>
    <input id="newpassword" class="form-control" name="newpassword" type="password">
  </div>

  <div class="form-item">
    <label for="verifypassword">Retype New Password</label>
    <input id="verifypassword" class="form-control" name="verifypassword" type="password">
  </div>

  <div class="form-item required">
    <label for="language">Language of communication (e.i.: email, prompts, message, etc.)</label>
    <select id="language" name="language"> … </select>
  </div>

  <div class="form-item required">
    <label for="timezone">Timezone</label>
    <select id="timezone" name="timezone"> … 633 options … </select>
  </div>

  <div class="form-item">
    <label for="phonenumber">Phone</label>
    <span>If this mobile phone number is not valid, you must change it. …</span>
    <table><tbody><tr>
      <td><select id="phonenumberprefix" name="phonenumberprefix[id]"> … </select></td>
      <td><input id="phonenumber" class="form-control" name="phonenumber" type="text"
                 readonly value="3341437718" maxlength="14"></td>
    </tr></tbody></table>
  </div>

  <div class="form-item">
    <h3><span>Date Registered</span><strong>:</strong> <span>28/07/2026 13:08</span></h3>
  </div>

  <div class="form-button-wrapper">
    <button id="btn-newuser" class="btn big blue" type="button" onclick="saveprofile(this)">
      <span>Save</span>
    </button>
    <a class="btn red" onclick="unsubscribe(this, 'post');"><span>Unsubscribe</span></a>
  </div>
</form>
```

### Select option values

```
#country  [name="country[id]"]
    value="19"   -> PAKISTAN                                    (only option)

#vac  [name="vac[id]"]                              ← THE IMPORTANT ONE
    value=""     -> Select an option
    value="137"  -> Islamabad Visa Application Center for Greece
    value="138"  -> Lahore Visa Application Center for Greece   ← currently selected
    value="140"  -> Verification Office

#language  [name="language"]
    value=""     -> Select an option
    value="en"   -> English

#phonenumberprefix  [name="phonenumberprefix[id]"]
    value=""     -> (blank)
    value="197"  -> PAKISTAN +92

#timezone  [name="timezone"]
    633 IANA zone strings (Africa/Abidjan … ), currently "Asia/Karachi"
```

### Buttons

| Selector | Action |
|---|---|
| `#btn-newuser` `.btn.big.blue` | `saveprofile(this)` — submits the profile |
| `a.btn.red` | `unsubscribe(this, 'post')` — **destructive**, deletes the registration |

Note both are `type="button"` / `<a>` with inline JS — there is no native form submit, so a
scraper cannot just call `form.submit()`; it must invoke `saveprofile()` or click the button.

---

## 3. Cross-page finding: the VAC selector

`#vac` on this page is what controls which centre the Book Appointment page queries. It changed
from Islamabad (`137`) to Lahore (`138`) between the two capture sessions, and
`/appointments/add` picked that up automatically — its own `#vac` hidden input now reads `138`
and the header shows `VAC:[Lahore]`.

So for a monitor covering both cities you either flip `#vac` here between polls, or hold two
authenticated sessions.
