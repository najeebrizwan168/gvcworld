import re
import time
import random
from datetime import datetime, timedelta

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    WebDriverException,
    InvalidSessionIdException,
    NoSuchWindowException,
)
from webdriver_manager.chrome import ChromeDriverManager

# ============================================================================
# CONFIGURATION
# ============================================================================
TARGET_URL = "https://pk-gr-services.gvcworld.eu/"
APPOINTMENT_URL = "https://pk-gr-services.gvcworld.eu/appointments/add"
USER_EMAIL = "najeeb21"
USER_PASS = "980Aa0330"

# Applicant details
APPLICANT_FIRST_NAME = ""
APPLICANT_SURNAME = ""
TARGET_CITY = "islamabad"
APPLICANT_DOB = "04/07/2006"                # dd/mm/yyyy
APPLICANT_PASSPORT = "646446656"
APPLICANT_PASSPORT_EXPIRY = "04/07/2036"    # dd/mm/yyyy
APPLICANT_GENDER_VALUE = "2"                # 1=FEMALE, 2=MALE, 3=OTHER
APPLICANT_NATIONALITY_TEXT = "PAKISTAN"

# Appointment type cycle order (value → label for debug)
APPOINTMENT_TYPES_ISLAMABAD = [
    ("0", "Submission Schengen Visa (Short term – Type C)"),
    ("2", "National visa (Long term - type D)"),
    ("6", "Prime Time (optional service at an additional charge)"),
    ("26", "Long-Term Type D (Seasonal/Dependent Employment)"),
]

APPOINTMENT_TYPES_LAHORE = [
    ("Premium Lounge", "Premium Lounge (optional service at an additional charge)"),
    ("2", "National visa (Long term - type D)"),
    ("6", "Prime Time (optional service at an additional charge)"),
    ("26", "Long-Term Type D (Seasonal/Dependent Employment)"),
]

APPOINTMENT_TYPES = APPOINTMENT_TYPES_ISLAMABAD.copy()

def get_appointment_types(city: str):
    return APPOINTMENT_TYPES_LAHORE if city.strip().lower() == "lahore" else APPOINTMENT_TYPES_ISLAMABAD

SCAN_START_DATE_STR = ""  # format: dd/mm/yyyy
SCAN_END_DATE_STR = ""    # format: dd/mm/yyyy


# How long to wait for a search to return before judging the result. The wait
# polls and exits as soon as the result lands, so this is a ceiling, not a cost.
SEARCH_RESULT_WAIT_SECONDS = 3

# Latched off the first time this portal refuses a native click (it refuses all
# of them), so later clicks skip straight to JS. Reset on each browser launch.
_NATIVE_CLICK_WORKS = True


class SessionLostError(Exception):
    """Raised when the portal has bounced us back to the login page."""


# ============================================================================
# HUMAN-LIKE HELPERS
# ============================================================================
def debug(msg: str):
    """Prints a timestamped debug line."""
    ts = datetime.now().strftime("%H:%M:%S")
    try:
        print(f"  [{ts}] {msg}")
    except UnicodeEncodeError:
        # Legacy console codepage (cp1252) can't render the status glyphs —
        # degrade the line rather than killing the scan over a log message
        print(f"  [{ts}] {msg.encode('ascii', 'replace').decode('ascii')}")


def _reraise_if_dead(err: Exception):
    """Re-raises errors that mean the browser/session is gone, so the outer
    auto-recovery loop restarts instead of us silently retrying forever."""
    if isinstance(err, (InvalidSessionIdException, NoSuchWindowException)):
        raise err


def safe_click(driver, target, description="element") -> bool:
    """
    Clicks an element the most reliable way available.

    A native WebDriver click enforces interactability — the element must have a
    non-zero box, must not be `display:none`/disabled, and must not be covered
    by anything. Any of those raises and, in the old code, killed the whole run:
    that is exactly how the profile Save button died with
    ElementNotInteractableException and the reCAPTCHA checkbox died with
    ElementClickInterceptedException (a near-transparent full-viewport backdrop
    was over it).

    The native click is tried first because it keeps the human-like input
    events, but this portal rejects it on every control. Rather than pay that
    failed round-trip on every single click, the first refusal latches
    _NATIVE_CLICK_WORKS off and the rest of the session goes straight to JS.
    The latch resets on each browser launch, so a fresh session re-probes once.
    """
    global _NATIVE_CLICK_WORKS

    element = driver.find_element(By.CSS_SELECTOR, target) if isinstance(target, str) else target

    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", element)
    except WebDriverException as err:
        _reraise_if_dead(err)

    if _NATIVE_CLICK_WORKS:
        random_pause(0.2, 0.5)
        try:
            element.click()
            return True
        except WebDriverException as err:
            _reraise_if_dead(err)
            _NATIVE_CLICK_WORKS = False
            debug(f"Native click on {description} failed ({type(err).__name__}) — "
                  f"switching to JS clicks for the rest of this session.")

    try:
        driver.execute_script("arguments[0].click();", element)
        return True
    except WebDriverException as err:
        _reraise_if_dead(err)

    try:
        driver.execute_script("""
            var el = arguments[0];
            ['mousedown', 'mouseup', 'click'].forEach(function (type) {
                el.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
            });
        """, element)
        debug(f"Synthetic MouseEvent click on {description} dispatched.")
        return True
    except WebDriverException as err:
        _reraise_if_dead(err)
        debug(f"⚠ All click strategies failed for {description}: {err}")
        return False


def js_set_value(driver, element, text: str):
    """Sets an input's value straight through the DOM and fires the events the
    page listens on. Fallback for when keyboard input can't be dispatched."""
    driver.execute_script("""
        var el = arguments[0], value = arguments[1];
        el.value = value;
        if (window.jQuery) {
            window.jQuery(el).val(value).trigger('change');
        }
        el.dispatchEvent(new Event('input',  {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
    """, element, text)


def dismiss_modal_if_any(driver, timeout=8.0) -> bool:
    """
    Dismisses a bootstrap/bootbox modal if one is on screen (e.g. the profile
    save confirmation, or a form-validation error). A modal left up blocks
    every later click on the page. Returns True if one was dismissed.

    Guard: `.btn.red` is excluded from every selector here — on the profile
    page that is the destructive `unsubscribe()` control.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            dismissed = driver.execute_script("""
                var modals = document.querySelectorAll('.modal.in, .modal.show, .bootbox.modal');
                for (var i = 0; i < modals.length; i++) {
                    var m = modals[i];
                    if (getComputedStyle(m).display === 'none') continue;
                    var btn = m.querySelector('.btn-primary:not(.red), .btn:not(.red), button:not(.red)');
                    if (btn) { btn.click(); return true; }
                }
                return false;
            """)
            if dismissed:
                random_pause(0.5, 1.0)
                return True
        except WebDriverException as err:
            _reraise_if_dead(err)
        time.sleep(0.5)
    return False


def random_pause(min_s=0.3, max_s=1.0):
    """Random human-like pause between actions."""
    time.sleep(random.uniform(min_s, max_s))


def human_mouse_move(driver):
    """Moves the mouse to a random spot on the page to mimic idle human behavior."""
    try:
        body = driver.find_element(By.TAG_NAME, "body")
        x_offset = random.randint(-200, 200)
        y_offset = random.randint(-150, 150)
        ActionChains(driver).move_to_element_with_offset(body, x_offset, y_offset).perform()
        random_pause(0.1, 0.4)
    except Exception:
        pass


def human_type(driver, element, text: str):
    """Types text character-by-character with random delays like a real human.
    Falls back to a direct DOM write if the driver refuses keyboard input
    (readonly/hidden/zero-size fields all reject send_keys)."""
    try:
        element.click()
    except WebDriverException as err:
        _reraise_if_dead(err)
        driver.execute_script("arguments[0].focus();", element)
    random_pause(0.2, 0.5)

    try:
        element.send_keys(Keys.CONTROL + "a")
        random_pause(0.05, 0.15)
        element.send_keys(Keys.BACKSPACE)
        random_pause(0.2, 0.4)

        for char in text:
            element.send_keys(char)
            if random.random() < 0.08:
                time.sleep(random.uniform(0.3, 0.7))
            else:
                time.sleep(random.uniform(0.05, 0.18))

        random_pause(0.2, 0.5)
        element.send_keys(Keys.TAB)
    except WebDriverException as err:
        _reraise_if_dead(err)
        debug(f"Keyboard input rejected ({type(err).__name__}) — writing value via DOM instead.")
        js_set_value(driver, element, text)

    random_pause(0.3, 0.6)


def human_type_date(driver, selector: str, date_str: str):
    """
    Sets a datepicker field's value straight through the DOM.

    No native click, send_keys or ActionChains here by design: every native
    interaction with this form's datepickers is rejected with
    ElementNotInteractableException, so attempting one only burns a WebDriver
    round-trip before the fallback runs anyway. We write the value, fire the
    events jQuery UI and the app's handlers listen on, then close any calendar
    panel that opened.
    """
    field = driver.find_element(By.CSS_SELECTOR, selector)
    js_set_value(driver, field, date_str)

    try:
        driver.execute_script(
            "if (window.jQuery && jQuery.datepicker) { jQuery.datepicker._hideDatepicker(); }"
        )
    except WebDriverException as err:
        _reraise_if_dead(err)

    # Confirm it landed — searching on the wrong date is worse than not searching
    actual = driver.execute_script("return arguments[0].value;", field)
    if (actual or "").strip() != date_str:
        # One retry with a plain write, in case a change handler reformatted it
        driver.execute_script("arguments[0].value = arguments[1];", field, date_str)
        actual = driver.execute_script("return arguments[0].value;", field)
        if (actual or "").strip() != date_str:
            raise Exception(f"Could not set {selector} to {date_str} — field reads {actual!r}")


def human_select_dropdown(driver, selector: str, option_text: str):
    """Selects a dropdown option by text label, handling both standard and Select2 dropdowns."""
    select_el = driver.find_element(By.CSS_SELECTOR, selector)
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", select_el)
    random_pause(0.2, 0.5)

    # Try native select first
    try:
        sel = Select(select_el)
        for opt in sel.options:
            if option_text.upper() in opt.text.strip().upper():
                sel.select_by_visible_text(opt.text.strip())
                break
    except Exception:
        pass

    # Trigger change event for Select2 UI compatibility
    driver.execute_script(f"""
        var el = document.querySelector('{selector}');
        if (el) {{
            var options = el.options;
            for (var i = 0; i < options.length; i++) {{
                if (options[i].text.trim().toUpperCase().indexOf('{option_text.upper()}') !== -1) {{
                    el.value = options[i].value;
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    if (window.jQuery && jQuery(el).data('select2')) {{
                        jQuery(el).trigger('change');
                    }}
                    break;
                }}
            }}
        }}
    """)
    random_pause(0.3, 0.6)


def human_select_dropdown_by_value(driver, selector: str, value: str):
    """Selects a dropdown option by value, handling both standard and Select2 dropdowns."""
    select_el = driver.find_element(By.CSS_SELECTOR, selector)
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", select_el)
    random_pause(0.2, 0.5)

    # Try native select first
    try:
        sel = Select(select_el)
        sel.select_by_value(value)
    except Exception:
        pass

    # Trigger change event for Select2 UI compatibility
    driver.execute_script(f"""
        var el = document.querySelector('{selector}');
        if (el) {{
            el.value = '{value}';
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            if (window.jQuery && jQuery(el).data('select2')) {{
                jQuery(el).trigger('change');
            }}
        }}
    """)
    random_pause(0.3, 0.6)


def handle_recaptcha(driver):
    """Attempts to auto-click the reCAPTCHA checkbox inside its iframe."""
    debug("Attempting reCAPTCHA checkbox auto-click...")
    try:
        driver.switch_to.default_content()

        # Find the reCAPTCHA iframe
        wait = WebDriverWait(driver, 10)
        recaptcha_iframe = wait.until(
            EC.presence_of_element_located((
                By.CSS_SELECTOR,
                "iframe[title='reCAPTCHA'], iframe[src*='recaptcha/api2/anchor']"
            ))
        )
        driver.switch_to.frame(recaptcha_iframe)
        random_pause(0.5, 1.5)

        # Click the reCAPTCHA checkbox. A leftover challenge backdrop (a fixed,
        # full-viewport, near-transparent div at z-index 2000000000) can swallow
        # a native click, so safe_click's JS fallback does the work there.
        checkbox = wait.until(
            EC.presence_of_element_located((
                By.CSS_SELECTOR,
                "#recaptcha-anchor, .recaptcha-checkbox-border"
            ))
        )
        if safe_click(driver, checkbox, "reCAPTCHA checkbox"):
            debug("reCAPTCHA checkbox clicked automatically!")
        else:
            debug("reCAPTCHA checkbox could not be clicked — solve it manually in Chrome.")

        # Switch back to main content
        driver.switch_to.default_content()
    except Exception as err:
        try:
            driver.switch_to.default_content()
        except WebDriverException:
            pass
        debug(f"reCAPTCHA auto-click skipped — manual check may be needed ({err}).")


# ============================================================================
# APPOINTMENT FORM FILLING
# ============================================================================
def fill_applicant_fields(driver):
    """Fills the required Client Information fields using human-like interactions."""
    print("\n" + "=" * 60)
    print("[STEP] FILLING CLIENT INFORMATION FIELDS")
    print("=" * 60)

    human_mouse_move(driver)

    # presence, not element_to_be_clickable — the latter adds visibility and
    # enabled checks that turn a slow render into a hard 30s failure, and the
    # typing helpers below already fall back to a DOM write if input is refused
    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "#gp_passportnumber"))
    )

    if APPLICANT_FIRST_NAME:
        debug(f"Filling First Name: {APPLICANT_FIRST_NAME}")
        first_name_field = driver.find_element(By.CSS_SELECTOR, "#gp_firstname")
        try:
            first_name_field.clear()
        except WebDriverException as err:
            _reraise_if_dead(err)
        human_type(driver, first_name_field, APPLICANT_FIRST_NAME)
        human_mouse_move(driver)

    if APPLICANT_SURNAME:
        debug(f"Filling Surname: {APPLICANT_SURNAME}")
        surname_field = driver.find_element(By.CSS_SELECTOR, "#gp_surname")
        try:
            surname_field.clear()
        except WebDriverException as err:
            _reraise_if_dead(err)
        human_type(driver, surname_field, APPLICANT_SURNAME)
        human_mouse_move(driver)

    debug(f"Filling Date of Birth: {APPLICANT_DOB}")
    human_type_date(driver, "#gp_dateofbirth", APPLICANT_DOB)
    human_mouse_move(driver)

    debug(f"Filling Passport Number: {APPLICANT_PASSPORT}")
    passport_field = driver.find_element(By.CSS_SELECTOR, "#gp_passportnumber")
    human_type(driver, passport_field, APPLICANT_PASSPORT)
    human_mouse_move(driver)

    debug(f"Filling Passport Expiry: {APPLICANT_PASSPORT_EXPIRY}")
    human_type_date(driver, "#gp_traveldocumentvaliduntil", APPLICANT_PASSPORT_EXPIRY)
    human_mouse_move(driver)

    debug(f"Setting Gender to MALE (value={APPLICANT_GENDER_VALUE})")
    human_select_dropdown_by_value(driver, "#gp_gender", APPLICANT_GENDER_VALUE)
    human_mouse_move(driver)

    debug("Setting Nationality to PAKISTAN...")
    human_select_dropdown(driver, "#gp_nationality", APPLICANT_NATIONALITY_TEXT)
    human_mouse_move(driver)

    debug("All client information fields filled successfully!")
    random_pause(0.5, 1.0)


# ============================================================================
# SLOT SCANNER
# ============================================================================
def reset_search_result_state(driver):
    """
    Wipes the previous search's output before firing a new one.

    Without this, a short wait is dangerous: if the new search hasn't returned
    yet, every check below would read the *previous* date's result and report
    it against the new date. Clearing first means "still empty" is
    unambiguously "not back yet" rather than "no slots".
    """
    try:
        driver.execute_script("""
            var msg = document.querySelector('#resultMessage');
            if (msg) msg.classList.add('hidden');
            var box = document.querySelector('#appointment_box');
            if (box) box.classList.add('hidden');
            var rd = document.querySelector('#resultDiv');
            if (rd) rd.innerHTML = '';
        """)
    except WebDriverException as err:
        _reraise_if_dead(err)


def wait_for_search_result(driver, timeout=None) -> bool:
    """
    Polls until the search produces output, returning the moment it does.

    Replaces a flat sleep: most searches answer in well under a second, so this
    is both faster than the old fixed wait and safer than simply shortening it.
    """
    if timeout is None:
        timeout = SEARCH_RESULT_WAIT_SECONDS
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if driver.execute_script("""
                var msg = document.querySelector('#resultMessage');
                if (msg && !msg.classList.contains('hidden')) return true;
                if (document.querySelectorAll('#resultDiv .appointment_slot').length > 0) return true;
                var box = document.querySelector('#appointment_box');
                if (box && !box.classList.contains('hidden')) return true;
                var modals = document.querySelectorAll('.modal.in, .modal.show, .bootbox.modal');
                for (var i = 0; i < modals.length; i++) {
                    if (getComputedStyle(modals[i]).display !== 'none') return true;
                }
                return false;
            """):
                return True
        except WebDriverException as err:
            _reraise_if_dead(err)
        time.sleep(0.15)
    return False


def check_slots_available(driver) -> bool:
    """Returns True if free appointment slots (.appointment_slot_enabled) exist."""
    slot_count = driver.execute_script(
        "return document.querySelectorAll('#resultDiv .appointment_slot_enabled').length;"
    )
    return slot_count > 0


def is_no_appointment_message(driver) -> bool:
    """Returns True if the 'no appointment' error message is visible (not hidden)."""
    is_visible = driver.execute_script("""
        var el = document.querySelector('#resultMessage');
        if (!el) return false;
        return !el.classList.contains('hidden');
    """)
    return is_visible


def read_visible_modal_text(driver) -> str:
    """Returns the text of any modal currently on screen ('' if none)."""
    try:
        return driver.execute_script("""
            var modals = document.querySelectorAll('.modal.in, .modal.show, .bootbox.modal');
            for (var i = 0; i < modals.length; i++) {
                if (getComputedStyle(modals[i]).display === 'none') continue;
                return (modals[i].innerText || '').replace(/\\s+/g, ' ').trim();
            }
            return '';
        """) or ""
    except WebDriverException as err:
        _reraise_if_dead(err)
        return ""


def verify_otp_requested(driver, timeout=20) -> bool:
    """
    Confirms the portal actually accepted the OTP request.

    The old code announced "OTP request sent!" the moment the click landed,
    which is not the same thing: the site can refuse with a validation modal
    (terms unchecked, reCAPTCHA unsolved, slot expired) and no SMS goes out.
    The user then waits for a code that never arrives.

    Evidence of acceptance = the OTP entry field becoming available.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if driver.execute_script("""
                var input = document.querySelector('#onetimepassword');
                if (!input) return false;
                var wrap = document.querySelector('#onetimepassword-wrap') || input.parentElement;
                if (wrap && wrap.classList.contains('hidden')) return false;
                return input.offsetParent !== null || getComputedStyle(input).display !== 'none';
            """):
                return True
        except WebDriverException as err:
            _reraise_if_dead(err)

        modal_text = read_visible_modal_text(driver)
        if modal_text:
            debug(f"⚠ Portal refused the OTP request: {modal_text[:200]}")
            dismiss_modal_if_any(driver, timeout=3)
            return False

        time.sleep(0.4)

    debug("⚠ OTP entry field never appeared within 20s.")
    return False


def select_slot_and_request_otp(driver):
    """
    Auto-selects the first available slot, checks Terms of Use, and requests
    the SMS OTP. Returns True only if the portal confirmed the OTP request.
    """
    # Step 1: Click the first available (enabled) slot
    debug("Auto-selecting the first available slot...")
    try:
        first_slot = driver.find_element(By.CSS_SELECTOR, "#resultDiv .appointment_slot_enabled")
        safe_click(driver, first_slot, "first available slot")
        random_pause(1.0, 2.0)

        # Verify slot was selected
        selected_count = driver.execute_script(
            "return document.querySelectorAll('#resultDiv .appointment_slot_selected').length;"
        )
        if selected_count > 0:
            # Read the selected time from the page
            selected_time = driver.execute_script(
                "var el = document.querySelector('#selectedTimeMsg'); return el ? el.textContent.trim() : 'unknown';"
            )
            selected_date = driver.execute_script(
                "var el = document.querySelector('#selectedDateMsg'); return el ? el.textContent.trim() : 'unknown';"
            )
            debug(f"✅ Slot selected: {selected_date} at {selected_time}")
        else:
            debug("⚠ Slot click registered but no .appointment_slot_selected found — continuing anyway...")
    except Exception as e:
        debug(f"⚠ Could not auto-select slot: {e}")
        return False

    human_mouse_move(driver)
    random_pause(0.5, 1.0)

    # Step 2: Check the Terms of Use checkbox (#submitinfo)
    debug("Checking 'Terms of Use' checkbox (#submitinfo)...")
    try:
        checkbox = driver.find_element(By.CSS_SELECTOR, "#submitinfo")
        if not checkbox.is_selected():
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", checkbox)
            random_pause(0.3, 0.6)
            # Use JS click since the checkbox may be styled/hidden behind a label
            driver.execute_script("arguments[0].click();", checkbox)
            random_pause(0.3, 0.6)

        # Confirm it actually took — the OTP request is refused without it
        if driver.execute_script("return document.querySelector('#submitinfo').checked;"):
            debug("☑ Terms of Use checkbox checked.")
        else:
            debug("⚠ Terms of Use checkbox did NOT take — the OTP request will likely be refused.")
    except Exception as e:
        debug(f"⚠ Could not check Terms checkbox: {e}")

    human_mouse_move(driver)
    random_pause(0.5, 1.0)

    # Step 3: Click 'Request OTP code (via Mobile) for Appointment'
    debug("Clicking 'Request OTP' button (#btn-onetimepassword)...")
    try:
        clicked = safe_click(driver, "#btn-onetimepassword", "#btn-onetimepassword (Request OTP)")
    except Exception as e:
        debug(f"⚠ Could not click OTP button: {e}")
        return False

    if not clicked:
        debug("⚠ OTP button could not be clicked — NO OTP was requested.")
        return False

    # Verify the site actually accepted the request. Never claim an SMS was
    # sent on the strength of a click landing — the user acts on this message.
    if verify_otp_requested(driver):
        print("\n" + "📲" * 30)
        print("  ✅ OTP REQUESTED — check your mobile for the SMS code.")
        print("  Enter it in the Chrome window to finish the booking.")
        print("📲" * 30 + "\n")
        human_mouse_move(driver)
        return True

    debug("⚠ OTP request did not confirm — no SMS may have been sent. Check the Chrome window.")
    human_mouse_move(driver)
    return False


def scan_dates_for_type(driver, type_value: str, type_label: str) -> bool:
    """
    For a given appointment type, scans today + next DAYS_TO_SCAN days for available slots.
    Returns True if slots were found (and stops), False to continue to next type.
    """
    print("\n" + "=" * 60)
    print(f"[SCANNING] Appointment Type: {type_label}")
    print(f"[SCANNING] Type value: {type_value}")
    print("=" * 60)

    debug(f"Selecting appointment type: {type_label}...")
    if type_value == "Premium Lounge":
        human_select_dropdown(driver, "#type", type_label)
    else:
        human_select_dropdown_by_value(driver, "#type", type_value)
    random_pause(1.5, 2.5)
    human_mouse_move(driver)

    # Check if #travelpurposesDiv became visible and handle it
    try:
        travel_div_hidden = driver.execute_script(
            "return document.querySelectorAll('#travelpurposesDiv.hidden').length;"
        )
        if travel_div_hidden == 0:
            travel_select = driver.find_element(By.CSS_SELECTOR, "#travelpurposes")
            options = travel_select.find_elements(By.TAG_NAME, "option")
            if len(options) > 1:
                debug("Travel Purpose dropdown appeared — selecting first available option...")
                second_option_text = options[1].text.strip()
                if second_option_text:
                    human_select_dropdown(driver, "#travelpurposes", second_option_text)
                random_pause(0.5, 1.0)
    except Exception:
        pass

    # Calculate scan range
    try:
        start_date = datetime.strptime(SCAN_START_DATE_STR, "%d/%m/%Y")
        end_date = datetime.strptime(SCAN_END_DATE_STR, "%d/%m/%Y")
    except ValueError:
        debug("⚠ Invalid Start or End Date format. Please use dd/mm/yyyy.")
        return False
        
    delta = (end_date - start_date).days
    if delta < 0:
        debug("⚠ End Date cannot be before Start Date.")
        return False
        
    days_to_scan = delta + 1

    # Scan each day
    for day_offset in range(days_to_scan):
        target_date = start_date + timedelta(days=day_offset)
        date_str = target_date.strftime("%d/%m/%Y")

        print(f"\n  --- Day {day_offset + 1}/{days_to_scan}: {date_str} ---")

        debug(f"Setting Appointment Date to: {date_str}")
        human_type_date(driver, "#datefrom", date_str)

        debug("Clicking 'Search' button (#btn-search)...")
        reset_search_result_state(driver)
        safe_click(driver, "#btn-search", "#btn-search (Search)")

        if not wait_for_search_result(driver):
            debug(f"No response within {SEARCH_RESULT_WAIT_SECONDS}s for {date_str} — treating as no availability.")

        # Check if a validation modal popped up. Asking the DOM directly is
        # cheaper and steadier than N round-trips of is_displayed().
        modal_visible = False
        try:
            modal_visible = driver.execute_script("""
                var modals = document.querySelectorAll('.modal.in, .modal.show, .bootbox.modal');
                for (var i = 0; i < modals.length; i++) {
                    if (getComputedStyle(modals[i]).display !== 'none') return true;
                }
                return false;
            """)
        except WebDriverException as err:
            _reraise_if_dead(err)

        if modal_visible:
            debug("⚠ Validation modal detected! Dismissing it...")
            if not dismiss_modal_if_any(driver, timeout=5):
                try:
                    ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                    random_pause(0.3, 0.5)
                except WebDriverException:
                    pass
            debug("Validation error — some required fields may be missing. Continuing...")
            continue

        # Check for "no appointment" message
        no_appointment = is_no_appointment_message(driver)
        if no_appointment:
            debug(f"✗ No appointments available on {date_str}. Moving to next date...")
            continue

        # Check for available slots
        slots_found = check_slots_available(driver)
        if slots_found:
            slot_count = driver.execute_script(
                "return document.querySelectorAll('#resultDiv .appointment_slot_enabled').length;"
            )
            print("\n" + "🟢" * 30)
            print(f"  ✅ SLOTS AVAILABLE!")
            print(f"  📅 Date: {date_str}")
            print(f"  📋 Appointment Type: {type_label}")
            print(f"  🔢 Available Slots: {slot_count}")
            print("🟢" * 30 + "\n")
            select_slot_and_request_otp(driver)
            return True

        # Check if result box is visible ('.hidden' is the app's own show/hide
        # toggle, so it is the authoritative state signal here)
        box_visible = False
        try:
            box_visible = driver.execute_script("""
                var box = document.querySelector('#appointment_box');
                if (!box) return false;
                return !box.classList.contains('hidden') && getComputedStyle(box).display !== 'none';
            """)
        except WebDriverException as err:
            _reraise_if_dead(err)

        if box_visible:
            debug(f"Results panel visible on {date_str} but no free slots detected. Checking again...")
            random_pause(1.5, 2.5)
            slots_found = check_slots_available(driver)
            if slots_found:
                slot_count = driver.execute_script(
                    "return document.querySelectorAll('#resultDiv .appointment_slot_enabled').length;"
                )
                print("\n" + "🟢" * 30)
                print(f"  ✅ SLOTS AVAILABLE (on recheck)!")
                print(f"  📅 Date: {date_str}")
                print(f"  📋 Appointment Type: {type_label}")
                print(f"  🔢 Available Slots: {slot_count}")
                print("🟢" * 30 + "\n")
                select_slot_and_request_otp(driver)
                return True
            else:
                debug(f"✗ Results panel open but all slots taken/disabled on {date_str}.")
                continue

        debug(f"✗ No results or slots for {date_str}. Moving to next date...")

    debug(f"✗ No slots found across {days_to_scan} days for type: {type_label}")
    return False


# ============================================================================
# VAC SYNC
# ============================================================================
def ensure_vac(driver):
    """Ensures the profile's VAC matches the target city before booking."""
    print("\n" + "=" * 60)
    print("[STEP] SYNCHRONIZING VAC CITY")
    print("=" * 60)
    
    vac_map = {"islamabad": "137", "lahore": "138"}
    target_city = TARGET_CITY.lower()
    if target_city not in vac_map:
        raise ValueError(f"Unknown target city: {target_city}")
    
    target_id = vac_map[target_city]
    
    # 1. Fast pre-check: Read current VAC from the sidebar display string
    #    ("===najeeb21===  VAC:[Lahore]") — present on every authenticated page.
    try:
        sidebar_text = driver.find_element(By.TAG_NAME, "body").text
        match = re.search(r"VAC:\s*\[([^\]]+)\]", sidebar_text)
        if match and match.group(1).strip().lower().startswith(target_city):
            debug(f"VAC already set to {target_city.capitalize()} (sidebar text match). Skipping sync.")
            return False
    except Exception:
        pass # Fallback to profile page

    debug(f"Checking VAC from profile page to ensure it's {target_city.capitalize()}...")
    
    # 2. Get Profile URL
    try:
        profile_url = driver.find_element(By.CSS_SELECTOR, "#manage-account").get_attribute("href")
    except Exception:
        raise Exception("Could not locate #manage-account to find profile URL.")
        
    driver.get(profile_url)
    
    # Wait for #vac
    wait = WebDriverWait(driver, 30)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#vac")))
    
    # Snapshot script
    snapshot_script = """
    const SNAPSHOT_FIELDS = ['username','firstname','lastname','email','country',
                             'newpassword','verifypassword','language','timezone',
                             'phonenumberprefix','phonenumber','id'];
    return Object.fromEntries(
        SNAPSHOT_FIELDS.map(id => [id, document.getElementById(id)?.value ?? null])
    );
    """
    
    # Wait for values to populate
    def check_populated(d):
        val = d.execute_script("return document.getElementById('vac').value;")
        return val is not None and val != ""
    wait.until(check_populated)
    
    current_vac = driver.execute_script("return document.getElementById('vac').value;")
    if current_vac == target_id:
        debug(f"VAC already set to {target_city.capitalize()} (value={target_id}). Skipping sync.")
        return False
        
    debug(f"VAC drift detected: current={current_vac}, target={target_id}. Updating profile...")
    
    before_snap = driver.execute_script(snapshot_script)
    if before_snap.get("newpassword") != "" or before_snap.get("verifypassword") != "":
        raise Exception("Password fields are not empty; aborting VAC sync.")
        
    # Change ONLY #vac
    driver.execute_script(f"""
        if (window.jQuery) {{
            window.jQuery('#vac').val('{target_id}').trigger('change');
        }} else {{
            const s = document.getElementById('vac');
            s.value = '{target_id}';
            s.dispatchEvent(new Event('change', {{ bubbles: true }}));
        }}
    """)
    
    # Verify widget updated
    time.sleep(1)
    native_val = driver.execute_script("return document.getElementById('vac').value;")
    widget_text = driver.execute_script("return document.querySelector('#vac-wrap .select2-selection__rendered').textContent;")
    
    if native_val != target_id or target_city.capitalize() not in widget_text:
        raise Exception(f"VAC change failed to apply to UI widget. native: {native_val}, widget: {widget_text}")
        
    after_snap = driver.execute_script(snapshot_script)
    drift = [k for k in before_snap.keys() if before_snap[k] != after_snap[k]]
    if drift:
        raise Exception(f"Unexpected field drift in profile: {', '.join(drift)}")
        
    debug("VAC updated in form. Clicking Save...")
    save_btn = driver.find_element(By.CSS_SELECTOR, "#btn-newuser")
    if not safe_click(driver, save_btn, "#btn-newuser (Save profile)"):
        # Last resort: the button is `type="button" onclick="saveprofile(this)"`,
        # there is no native form submit — call the page's own handler.
        debug("Save button unclickable — invoking saveprofile() directly...")
        driver.execute_script("""
            var btn = document.getElementById('btn-newuser');
            if (typeof window.saveprofile !== 'function') {
                throw new Error('saveprofile() is not available on this page');
            }
            window.saveprofile(btn);
        """)

    # Wait for save result — the site may raise a confirmation/error modal
    debug("Waiting for the profile save to complete...")
    if dismiss_modal_if_any(driver, timeout=10):
        debug("Dismissed the post-save modal.")
    time.sleep(5)

    # Verify from a fresh load, not from in-page state (one retry — the save
    # is asynchronous and can land a moment after the click)
    new_vac = None
    for attempt in (1, 2):
        driver.get(profile_url)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#vac")))
        wait.until(check_populated)
        new_vac = driver.execute_script("return document.getElementById('vac').value;")
        if new_vac == target_id:
            break
        if attempt == 1:
            debug(f"VAC still reads {new_vac} — re-checking in 5s...")
            time.sleep(5)

    if new_vac != target_id:
        raise Exception(f"VAC save failed! Expected {target_id}, got {new_vac}")

    debug(f"VAC successfully synced to {target_city.capitalize()}.")
    return True



# ============================================================================
# APPOINTMENT PAGE LOADING
# ============================================================================
def wait_for_appointment_form(driver, timeout=90) -> bool:
    """
    Polls for the appointment form's *real* readiness.

    This replaces `EC.visibility_of_element_located('#appointment')`, which had
    two problems: it waits on the <form> wrapper rather than the controls the
    scanner actually drives, and on timeout it raises a TimeoutException with an
    empty message — telling you nothing about why the page didn't come up.

    Here we poll for the specific controls, and distinguish the real failure
    modes: a session bounce back to the login page raises SessionLostError so
    the outer loop re-logs-in, anything else returns False so the caller can
    reload and retry.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if driver.execute_script("""
                return !!(document.querySelector('#appointment')
                          && document.querySelector('#type')
                          && document.querySelector('#datefrom')
                          && document.querySelector('#gp_passportnumber'));
            """):
                return True

            if driver.execute_script(
                "return !!(document.querySelector('#password') && document.querySelector('#username'));"
            ):
                raise SessionLostError("Portal bounced back to the login page — session expired.")
        except SessionLostError:
            raise
        except WebDriverException as err:
            _reraise_if_dead(err)
        time.sleep(0.5)
    return False


def describe_page(driver) -> str:
    """A one-line summary of whatever is actually on screen. Used when a wait
    fails, so the log says what went wrong instead of raising a bare
    TimeoutException with an empty message."""
    try:
        info = driver.execute_script("""
            var body = document.body ? (document.body.innerText || '').trim() : '';
            return {
                url:   location.href,
                title: document.title,
                has:   ['#appointment', '#type', '#datefrom', '#username', '#password']
                         .filter(function (s) { return !!document.querySelector(s); }),
                text:  body.replace(/\\s+/g, ' ').slice(0, 220)
            };
        """)
        return (f"url={info['url']} | title={info['title']!r} | "
                f"present={info['has']} | body={info['text']!r}")
    except WebDriverException as err:
        return f"(could not inspect page: {type(err).__name__})"


def open_appointment_page(driver, attempts=3):
    """Navigates to the Book Appointment form, retrying with a reload if the
    client-side render doesn't complete. Always a fresh navigation — the page
    caches the VAC at load time (see profile/VAC notes)."""
    for attempt in range(1, attempts + 1):
        debug(f"Navigating to 'Book Appointment' section (attempt {attempt}/{attempts}): {APPOINTMENT_URL}...")
        driver.get(APPOINTMENT_URL)

        debug("Waiting for appointment form to render (up to 90s)...")
        if wait_for_appointment_form(driver, timeout=90):
            debug("Appointment form (#appointment) is loaded and ready!")
            return True

        debug(f"⚠ Appointment form did not render within 90s (attempt {attempt}/{attempts}).")
        debug(f"   Page state: {describe_page(driver)}")
        if dismiss_modal_if_any(driver, timeout=3):
            debug("   Dismissed a modal that was blocking the page.")
        random_pause(3.0, 6.0)

    raise Exception(
        f"Appointment form (#appointment) never rendered after {attempts} attempts. "
        f"Last page state: {describe_page(driver)}"
    )


def assert_vac_on_appointment_page(driver):
    """Hard gate: never fall through into booking with a mismatched VAC."""
    debug("Asserting VAC is correctly set on /appointments/add...")
    vac_map = {"islamabad": "137", "lahore": "138"}
    expected = vac_map[TARGET_CITY.lower()]

    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#vac")))
    loaded_vac = driver.execute_script("return document.getElementById('vac').value;")
    if loaded_vac != expected:
        raise Exception(f"FATAL: Appointment form loaded with VAC {loaded_vac}, expected {expected}")
    debug(f"VAC gate passed — appointment form is querying {TARGET_CITY.capitalize()} ({expected}).")


# ============================================================================
# BROWSER LAUNCH + LOGIN (reusable for auto-recovery)
# ============================================================================
def launch_browser_and_login():
    """
    Launches a fresh Chrome browser, navigates to the portal, fills login
    credentials, waits for user to solve CAPTCHA & login, then navigates
    to the appointment form and fills applicant fields.
    Returns (driver, wait) on success, or raises on failure.
    """
    global _NATIVE_CLICK_WORKS
    _NATIVE_CLICK_WORKS = True   # fresh session — probe native clicks once more

    debug("Launching Chrome Browser via Selenium...")
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    # Guarantee a real viewport even when the window is hidden off-screen
    options.add_argument("--window-size=1920,1080")

    # ── Keep the renderer at full speed while the window is hidden ──
    # win_hide puts the window through SW_HIDE, so Chrome backgrounds the
    # renderer and throttles JS timers to ~1/sec. This page is client-rendered
    # and polls over XHR, so throttling makes it render far slower than it does
    # on screen. These flags keep a hidden tab running at foreground priority.
    options.add_argument("--disable-features=CalculateNativeWinOcclusion")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-ipc-flooding-protection")

    # Anti-detection flags
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )

    # Remove webdriver flag to reduce detection
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"}
    )

    debug("Browser opened. Pausing like a human looking at the screen...")
    random_pause(3.0, 5.0)

    debug(f"Navigating to Visa Portal: {TARGET_URL}")
    driver.get(TARGET_URL)

    debug("Page loaded. Looking around before interacting...")
    human_mouse_move(driver)
    random_pause(2.0, 3.5)
    human_mouse_move(driver)
    random_pause(1.0, 2.0)

    debug("Locating username field (#username)...")
    wait = WebDriverWait(driver, 30)
    username_field = wait.until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, "#username"))
    )
    debug("Username field found. Moving mouse toward it...")

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", username_field)
    random_pause(0.5, 1.0)

    debug("Entering username...")
    human_type(driver, username_field, USER_EMAIL)

    random_pause(1.5, 2.5)
    human_mouse_move(driver)

    debug("Locating password field (#password)...")
    password_field = wait.until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, "#password"))
    )
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", password_field)
    random_pause(0.5, 1.0)
    debug("Entering password...")
    human_type(driver, password_field, USER_PASS)

    random_pause(1.5, 3.0)
    human_mouse_move(driver)
    random_pause(0.5, 1.5)

    handle_recaptcha(driver)

    print("\n" + "=" * 60)
    print("[ACTION REQUIRED] MANUAL INTERVENTION GATE:")
    print("1. Solve any reCAPTCHA image puzzles if presented.")
    print("2. Click 'Sign In' / Login button.")
    print("=" * 60 + "\n")

    input("Press ENTER in terminal ONLY AFTER successful login...")
    debug("Terminal gate passed — login confirmed by user.")

    debug("Waiting 15 seconds for dashboard to stabilize...")
    time.sleep(15)
    
    # Sync VAC before booking
    ensure_vac(driver)

    # ALWAYS a fresh navigation — the page caches the VAC at load time
    open_appointment_page(driver)

    # Hard gate assert VAC
    assert_vac_on_appointment_page(driver)

    random_pause(1.5, 2.5)
    human_mouse_move(driver)

    fill_applicant_fields(driver)

    return driver, wait


def is_browser_alive(driver) -> bool:
    """Quick health-check: returns False if the browser window has been closed or crashed."""
    try:
        _ = driver.title
        return True
    except Exception:
        return False


# ============================================================================
# MAIN — INFINITE SCAN LOOP WITH AUTO-RECOVERY
# ============================================================================
def main():
    print("=" * 60)
    print("  GVCW VISA APPOINTMENT SLOT SCANNER")
    print("  Selenium Undetected Chrome Runtime")
    print("  ♾️  CONTINUOUS MODE — will scan forever until you stop it")
    print("=" * 60)

    # ── Outer loop: auto-recovers if the browser dies ──
    while True:
        driver = None
        try:
            driver, wait = launch_browser_and_login()

            round_number = 0

            # ── Inner loop: infinite scan rounds ──
            while True:
                round_number += 1

                # Health-check before each round
                if not is_browser_alive(driver):
                    raise Exception("Browser window was closed or crashed — restarting...")

                print("\n" + "🔄" * 30)
                print(f"  ══════  SCAN ROUND {round_number}  ══════")
                days_to_scan = 1
                try:
                    start_date = datetime.strptime(SCAN_START_DATE_STR, "%d/%m/%Y")
                    end_date = datetime.strptime(SCAN_END_DATE_STR, "%d/%m/%Y")
                    days_to_scan = max(1, (end_date - start_date).days + 1)
                except ValueError:
                    pass
                print(f"  Checking {len(APPOINTMENT_TYPES)} types × {days_to_scan} days")
                print("🔄" * 30)

                slots_found = False
                
                # Fetch dynamically based on global TARGET_CITY
                current_types = get_appointment_types(TARGET_CITY)
                
                # Filter to only run types that the user checked in the UI
                # APPOINTMENT_TYPES acts as the "enabled" list coming from the GUI
                enabled_values = {v for v, l in APPOINTMENT_TYPES}
                types_to_scan = [t for t in current_types if t[0] in enabled_values]
                
                for type_value, type_label in types_to_scan:
                    # Health-check before each type
                    if not is_browser_alive(driver):
                        raise Exception("Browser window was closed or crashed — restarting...")

                    result = scan_dates_for_type(driver, type_value, type_label)
                    if result:
                        slots_found = True
                        break
                    debug("Moving to next appointment type...")
                    random_pause(1.0, 2.0)
                    human_mouse_move(driver)

                if slots_found:
                    print("\n" + "=" * 60)
                    print("  ✅ SLOTS FOUND! Browser is open for you to book.")
                    print("  Press ENTER when you're done to RESUME scanning...")
                    print("=" * 60)
                    input()
                    debug("Resuming continuous scanning after slot notification...")

                    # After user interaction, re-navigate to the appointment page
                    # in case the form state changed
                    if not is_browser_alive(driver):
                        raise Exception("Browser window was closed — restarting...")
                    debug("Re-navigating to appointment form for next round...")
                    try:
                        open_appointment_page(driver)
                        assert_vac_on_appointment_page(driver)
                        fill_applicant_fields(driver)
                    except Exception as reload_err:
                        debug(f"Could not reload appointment form ({reload_err}) — will restart browser.")
                        raise Exception("Appointment form reload failed — restarting...")
                else:
                    print("\n" + "-" * 60)
                    print(f"  ❌ Round {round_number} complete — no slots found.")
                    pause_secs = random.uniform(5, 15)
                    debug(f"Pausing {pause_secs:.1f}s before next round...")
                    print("-" * 60)
                    time.sleep(pause_secs)

        except KeyboardInterrupt:
            print("\n\n" + "=" * 60)
            print("  🛑 STOPPED BY USER (Ctrl+C)")
            print("=" * 60)
            break

        except Exception as e:
            print(f"\n[ERROR] {e}")
            import traceback
            traceback.print_exc()
            print("\n" + "=" * 60)
            print("  🔁 AUTO-RECOVERY: Will reopen browser from login page in 10 seconds...")
            print("=" * 60)
            time.sleep(10)

        finally:
            # Try to close the old browser if it's still around
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
