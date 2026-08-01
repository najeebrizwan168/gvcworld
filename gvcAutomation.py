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
APPLICANT_DOB = "04/07/2006"                # dd/mm/yyyy
APPLICANT_PASSPORT = "646446656"
APPLICANT_PASSPORT_EXPIRY = "04/07/2036"    # dd/mm/yyyy
APPLICANT_GENDER_VALUE = "2"                # 1=FEMALE, 2=MALE, 3=OTHER
APPLICANT_NATIONALITY_TEXT = "PAKISTAN"

# Appointment type cycle order (value → label for debug)
APPOINTMENT_TYPES = [
    ("0", "Submission Schengen Visa (Short term – Type C)"),
    ("2", "National visa (Long term - type D)"),
    ("6", "Prime Time (optional service at an additional charge)"),
    ("26", "Long-Term Type D (Seasonal/Dependent Employment)"),
]
SCAN_START_DATE_STR = ""  # format: dd/mm/yyyy
SCAN_END_DATE_STR = ""    # format: dd/mm/yyyy


# ============================================================================
# HUMAN-LIKE HELPERS
# ============================================================================
def debug(msg: str):
    """Prints a timestamped debug line."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"  [{ts}] {msg}")


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
    """Types text character-by-character with random delays like a real human."""
    element.click()
    random_pause(0.2, 0.5)
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
    random_pause(0.3, 0.6)


def human_type_date(driver, selector: str, date_str: str):
    """
    Types a date into a datepicker field like a human:
    Click field → triple-click to select all → type date → press Escape → Tab away.
    """
    field = driver.find_element(By.CSS_SELECTOR, selector)
    field.click()
    random_pause(0.3, 0.6)

    # Triple-click to select all text in the field
    ActionChains(driver).double_click(field).click(field).perform()
    random_pause(0.1, 0.3)

    for char in date_str:
        field.send_keys(char)
        time.sleep(random.uniform(0.05, 0.15))

    random_pause(0.3, 0.5)
    field.send_keys(Keys.ESCAPE)
    random_pause(0.2, 0.4)
    field.send_keys(Keys.TAB)
    random_pause(0.3, 0.6)


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

        # Click the reCAPTCHA checkbox
        checkbox = wait.until(
            EC.element_to_be_clickable((
                By.CSS_SELECTOR,
                "#recaptcha-anchor, .recaptcha-checkbox-border"
            ))
        )
        checkbox.click()
        debug("reCAPTCHA checkbox clicked automatically!")

        # Switch back to main content
        driver.switch_to.default_content()
    except Exception as err:
        driver.switch_to.default_content()
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

    if APPLICANT_FIRST_NAME:
        debug(f"Filling First Name: {APPLICANT_FIRST_NAME}")
        first_name_field = driver.find_element(By.CSS_SELECTOR, "#gp_firstname")
        first_name_field.clear()
        human_type(driver, first_name_field, APPLICANT_FIRST_NAME)
        human_mouse_move(driver)
        
    if APPLICANT_SURNAME:
        debug(f"Filling Surname: {APPLICANT_SURNAME}")
        surname_field = driver.find_element(By.CSS_SELECTOR, "#gp_surname")
        surname_field.clear()
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


def select_slot_and_request_otp(driver):
    """
    Auto-selects the first available slot, checks Terms of Use,
    and clicks 'Request OTP code (via Mobile) for Appointment'.
    """
    # Step 1: Click the first available (enabled) slot
    debug("Auto-selecting the first available slot...")
    try:
        first_slot = driver.find_element(By.CSS_SELECTOR, "#resultDiv .appointment_slot_enabled")
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", first_slot)
        random_pause(0.5, 1.0)
        first_slot.click()
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
        return

    human_mouse_move(driver)
    random_pause(0.5, 1.0)

    # Step 2: Check the Terms of Use checkbox (#submitinfo)
    debug("Checking 'Terms of Use' checkbox (#submitinfo)...")
    try:
        checkbox = driver.find_element(By.CSS_SELECTOR, "#submitinfo")
        is_checked = checkbox.is_selected()
        if not is_checked:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", checkbox)
            random_pause(0.3, 0.6)
            # Use JS click since the checkbox may be styled/hidden behind a label
            driver.execute_script("arguments[0].click();", checkbox)
            random_pause(0.3, 0.6)
            debug("☑ Terms of Use checkbox checked.")
        else:
            debug("☑ Terms of Use already checked.")
    except Exception as e:
        debug(f"⚠ Could not check Terms checkbox: {e}")

    human_mouse_move(driver)
    random_pause(0.5, 1.0)

    # Step 3: Click 'Request OTP code (via Mobile) for Appointment'
    debug("Clicking 'Request OTP' button (#btn-onetimepassword)...")
    try:
        otp_btn = driver.find_element(By.CSS_SELECTOR, "#btn-onetimepassword")
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", otp_btn)
        random_pause(0.5, 1.0)
        otp_btn.click()
        random_pause(1.0, 2.0)
        debug("📲 OTP request sent! Check your mobile for the SMS code.")
    except Exception as e:
        debug(f"⚠ Could not click OTP button: {e}")

    human_mouse_move(driver)


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
        random_pause(0.5, 1.0)

        debug("Clicking 'Search' button (#btn-search)...")
        search_btn = driver.find_element(By.CSS_SELECTOR, "#btn-search")
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", search_btn)
        random_pause(0.2, 0.5)
        search_btn.click()
        human_mouse_move(driver)

        debug("Waiting 10 seconds for search results...")
        time.sleep(10)

        # Check if a validation modal popped up
        modal_visible = False
        try:
            modals = driver.find_elements(
                By.CSS_SELECTOR,
                ".modal.in, .modal.show, .bootbox.modal"
            )
            for modal in modals:
                if modal.is_displayed():
                    modal_visible = True
                    break
        except Exception:
            pass

        if modal_visible:
            debug("⚠ Validation modal detected! Dismissing it...")
            try:
                dismiss_btn = driver.find_element(
                    By.CSS_SELECTOR,
                    ".modal .btn, .bootbox .btn-primary, .modal button"
                )
                dismiss_btn.click()
                random_pause(0.5, 1.0)
            except Exception:
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                random_pause(0.3, 0.5)
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

        # Check if result box is visible
        box_visible = False
        try:
            appointment_box = driver.find_element(By.CSS_SELECTOR, "#appointment_box")
            box_visible = appointment_box.is_displayed()
        except Exception:
            pass

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
# BROWSER LAUNCH + LOGIN (reusable for auto-recovery)
# ============================================================================
def launch_browser_and_login():
    """
    Launches a fresh Chrome browser, navigates to the portal, fills login
    credentials, waits for user to solve CAPTCHA & login, then navigates
    to the appointment form and fills applicant fields.
    Returns (driver, wait) on success, or raises on failure.
    """
    debug("Launching Chrome Browser via Selenium...")
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    # Anti-detection flags
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

    debug("Navigating to 'Book Appointment' section...")
    try:
        book_app_link = wait.until(
            EC.visibility_of_element_located((
                By.CSS_SELECTOR,
                "#menu-appointments-add a, a[href*='/appointments/add']"
            ))
        )
        random_pause(0.5, 1.0)
        book_app_link.click()
        debug("Clicked 'Book Appointment' sidebar link successfully!")
    except Exception:
        debug("Sidebar click failed. Navigating directly to appointment URL...")
        driver.get(APPOINTMENT_URL)

    debug("Waiting for appointment form to load (up to 120s)...")
    WebDriverWait(driver, 120).until(
        EC.visibility_of_element_located((By.CSS_SELECTOR, "#appointment"))
    )
    debug("Appointment form (#appointment) is visible and ready!")

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
                for type_value, type_label in APPOINTMENT_TYPES:
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
                    driver.get(APPOINTMENT_URL)
                    random_pause(3.0, 5.0)
                    try:
                        WebDriverWait(driver, 120).until(
                            EC.visibility_of_element_located((By.CSS_SELECTOR, "#appointment"))
                        )
                        fill_applicant_fields(driver)
                    except Exception:
                        debug("Could not reload appointment form — will restart browser.")
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
