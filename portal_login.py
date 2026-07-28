import asyncio
import random
from datetime import datetime, timedelta
from patchright.async_api import async_playwright, Page

# ============================================================================
# CONFIGURATION
# ============================================================================
TARGET_URL = "https://pk-gr-services.gvcworld.eu/"
APPOINTMENT_URL = "https://pk-gr-services.gvcworld.eu/appointments/add"
USER_EMAIL = "najeeb21"
USER_PASS = "980Aa0330"

# Applicant details
APPLICANT_DOB = "04/07/2006"           # dd/mm/yyyy
APPLICANT_PASSPORT = "646446656"
APPLICANT_PASSPORT_EXPIRY = "04/07/2036"  # dd/mm/yyyy (10 years from DOB)
APPLICANT_GENDER_VALUE = "2"            # 1=FEMALE, 2=MALE, 3=OTHER
APPLICANT_NATIONALITY_TEXT = "PAKISTAN"

# Appointment type cycle order (value → label for debug)
APPOINTMENT_TYPES = [
    ("0", "Submission Schengen Visa (Short term – Type C)"),
    ("2", "National visa (Long term - type D)"),
    ("6", "Prime Time (optional service at an additional charge)"),
    ("26", "Long-Term Type D (Seasonal/Dependent Employment)"),
]

DAYS_TO_SCAN = 4


# ============================================================================
# HUMAN-LIKE HELPERS
# ============================================================================
def debug(msg: str):
    """Prints a timestamped debug line."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"  [{ts}] {msg}")


async def random_pause(min_s=0.3, max_s=1.0):
    """Random human-like pause between actions."""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def human_mouse_move(page: Page):
    """Moves the mouse to a random spot on the page to mimic idle human behavior."""
    x = random.randint(100, 1200)
    y = random.randint(100, 600)
    await page.mouse.move(x, y, steps=random.randint(5, 15))
    await random_pause(0.1, 0.4)


async def human_type(page_element, text: str):
    """Types text character-by-character with random delays like a real human."""
    await page_element.click()
    await random_pause(0.2, 0.5)
    await page_element.press("Control+A")
    await random_pause(0.05, 0.15)
    await page_element.press("Backspace")
    await random_pause(0.2, 0.4)

    for i, char in enumerate(text):
        await page_element.type(char)
        if random.random() < 0.08:
            await asyncio.sleep(random.uniform(0.3, 0.7))
        else:
            await asyncio.sleep(random.uniform(0.05, 0.18))

    await random_pause(0.2, 0.5)
    await page_element.press("Tab")
    await random_pause(0.3, 0.6)


async def human_type_date(page: Page, selector: str, date_str: str):
    """
    Types a date into a datepicker field like a human:
    Click field → triple-click to select all → type date → press Escape → Tab away.
    """
    field = page.locator(selector)
    await field.click()
    await random_pause(0.3, 0.6)

    await field.click(click_count=3)
    await random_pause(0.1, 0.3)

    for char in date_str:
        await field.type(char)
        await asyncio.sleep(random.uniform(0.05, 0.15))

    await random_pause(0.3, 0.5)
    await page.keyboard.press("Escape")
    await random_pause(0.2, 0.4)
    await page.keyboard.press("Tab")
    await random_pause(0.3, 0.6)


async def human_select_dropdown(page: Page, selector: str, option_text: str):
    """Selects a dropdown option by text label, handling both standard and Select2 dropdowns."""
    select_el = page.locator(selector)
    await select_el.scroll_into_view_if_needed()
    await random_pause(0.2, 0.5)

    try:
        await select_el.select_option(label=option_text, force=True)
    except Exception:
        pass

    # Trigger change event for Select2 UI compatibility
    await page.evaluate(f"""(text) => {{
        const el = document.querySelector('{selector}');
        if (el) {{
            for (const opt of el.options) {{
                if (opt.text.trim().toUpperCase().includes(text.toUpperCase())) {{
                    el.value = opt.value;
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    if (window.jQuery && jQuery(el).data('select2')) {{
                        jQuery(el).trigger('change');
                    }}
                    break;
                }}
            }}
        }}
    }}""", option_text)
    await random_pause(0.3, 0.6)


async def human_select_dropdown_by_value(page: Page, selector: str, value: str):
    """Selects a dropdown option by value, handling both standard and Select2 dropdowns."""
    select_el = page.locator(selector)
    await select_el.scroll_into_view_if_needed()
    await random_pause(0.2, 0.5)

    try:
        await select_el.select_option(value=value, force=True)
    except Exception:
        pass

    # Trigger change event for Select2 UI compatibility
    await page.evaluate(f"""() => {{
        const el = document.querySelector('{selector}');
        if (el) {{
            el.value = '{value}';
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            if (window.jQuery && jQuery(el).data('select2')) {{
                jQuery(el).trigger('change');
            }}
        }}
    }}""")
    await random_pause(0.3, 0.6)


async def handle_recaptcha(page: Page):
    """Attempts to auto-click the reCAPTCHA checkbox inside its iframe."""
    debug("Attempting reCAPTCHA checkbox auto-click...")
    try:
        recaptcha_frame = page.frame_locator("iframe[title='reCAPTCHA'], iframe[src*='recaptcha/api2/anchor']").first
        recaptcha_checkbox = recaptcha_frame.locator("#recaptcha-anchor, .recaptcha-checkbox-border")
        await recaptcha_checkbox.wait_for(state="visible", timeout=10000)
        await random_pause(0.5, 1.5)
        await recaptcha_checkbox.click()
        debug("reCAPTCHA checkbox clicked automatically!")
    except Exception as err:
        debug(f"reCAPTCHA auto-click skipped — manual check may be needed ({err}).")


# ============================================================================
# APPOINTMENT FORM FILLING
# ============================================================================
async def fill_applicant_fields(page: Page):
    """Fills the required Client Information fields using human-like interactions."""
    print("\n" + "="*60)
    print("[STEP] FILLING CLIENT INFORMATION FIELDS")
    print("="*60)

    await human_mouse_move(page)

    debug(f"Filling Date of Birth: {APPLICANT_DOB}")
    await human_type_date(page, "#gp_dateofbirth", APPLICANT_DOB)
    await human_mouse_move(page)

    debug(f"Filling Passport Number: {APPLICANT_PASSPORT}")
    passport_field = page.locator("#gp_passportnumber")
    await human_type(passport_field, APPLICANT_PASSPORT)
    await human_mouse_move(page)

    debug(f"Filling Passport Expiry: {APPLICANT_PASSPORT_EXPIRY}")
    await human_type_date(page, "#gp_traveldocumentvaliduntil", APPLICANT_PASSPORT_EXPIRY)
    await human_mouse_move(page)

    debug(f"Setting Gender to MALE (value={APPLICANT_GENDER_VALUE})")
    await human_select_dropdown_by_value(page, "#gp_gender", APPLICANT_GENDER_VALUE)
    await human_mouse_move(page)

    debug("Setting Nationality to PAKISTAN...")
    await human_select_dropdown(page, "#gp_nationality", APPLICANT_NATIONALITY_TEXT)
    await human_mouse_move(page)

    debug("All client information fields filled successfully!")
    await random_pause(0.5, 1.0)


# ============================================================================
# SLOT SCANNER
# ============================================================================
async def check_slots_available(page: Page) -> bool:
    """Returns True if free appointment slots (.appointment_slot_enabled) exist."""
    slot_count = await page.evaluate("""() => {
        return document.querySelectorAll('#resultDiv .appointment_slot_enabled').length;
    }""")
    return slot_count > 0


async def is_no_appointment_message(page: Page) -> bool:
    """Returns True if the 'no appointment' error message is visible (not hidden)."""
    is_visible = await page.evaluate("""() => {
        const el = document.querySelector('#resultMessage');
        if (!el) return false;
        return !el.classList.contains('hidden');
    }""")
    return is_visible


async def scan_dates_for_type(page: Page, type_value: str, type_label: str) -> bool:
    """
    For a given appointment type, scans today + next 7 days for available slots.
    Returns True if slots were found (and stops), False to continue to next type.
    """
    print("\n" + "="*60)
    print(f"[SCANNING] Appointment Type: {type_label}")
    print(f"[SCANNING] Type value: {type_value}")
    print("="*60)

    debug(f"Selecting appointment type: {type_label}...")
    await human_select_dropdown_by_value(page, "#type", type_value)
    await random_pause(1.5, 2.5)
    await human_mouse_move(page)

    # Check if #travelpurposesDiv became visible and handle it
    try:
        travel_div_hidden = await page.locator("#travelpurposesDiv.hidden").count()
        if travel_div_hidden == 0:
            travel_select = page.locator("#travelpurposes")
            has_options = await travel_select.locator("option").count()
            if has_options > 1:
                debug("Travel Purpose dropdown appeared — selecting first available option...")
                second_option_text = await travel_select.locator("option").nth(1).text_content()
                if second_option_text:
                    await human_select_dropdown(page, "#travelpurposes", second_option_text.strip())
                await random_pause(0.5, 1.0)
    except Exception:
        pass

    # Scan each day
    today = datetime.now()
    for day_offset in range(DAYS_TO_SCAN):
        target_date = today + timedelta(days=day_offset)
        date_str = target_date.strftime("%d/%m/%Y")

        print(f"\n  --- Day {day_offset + 1}/{DAYS_TO_SCAN}: {date_str} ---")

        debug(f"Setting Appointment Date to: {date_str}")
        await human_type_date(page, "#datefrom", date_str)
        await random_pause(0.5, 1.0)

        debug("Clicking 'Search' button (#btn-search)...")
        search_btn = page.locator("#btn-search")
        await search_btn.scroll_into_view_if_needed()
        await random_pause(0.2, 0.5)
        await search_btn.click()
        await human_mouse_move(page)

        debug("Waiting 10 seconds for search results...")
        await asyncio.sleep(10)

        # Check if a validation modal popped up
        try:
            modal_el = page.locator(".modal.in, .modal.show, .bootbox.modal").first
            modal_visible = await modal_el.is_visible()
        except Exception:
            modal_visible = False

        if modal_visible:
            debug("⚠ Validation modal detected! Dismissing it...")
            try:
                dismiss_btn = page.locator(".modal .btn, .bootbox .btn-primary, .modal button").first
                await dismiss_btn.click()
                await random_pause(0.5, 1.0)
            except Exception:
                await page.keyboard.press("Escape")
                await random_pause(0.3, 0.5)
            debug("Validation error — some required fields may be missing. Continuing...")
            continue

        # Check for "no appointment" message
        no_appointment = await is_no_appointment_message(page)
        if no_appointment:
            debug(f"✗ No appointments available on {date_str}. Moving to next date...")
            continue

        # Check for available slots
        slots_found = await check_slots_available(page)
        if slots_found:
            slot_count = await page.evaluate(
                "() => document.querySelectorAll('#resultDiv .appointment_slot_enabled').length"
            )
            print("\n" + "🟢"*30)
            print(f"  ✅ SLOTS AVAILABLE!")
            print(f"  📅 Date: {date_str}")
            print(f"  📋 Appointment Type: {type_label}")
            print(f"  🔢 Available Slots: {slot_count}")
            print("🟢"*30 + "\n")
            debug("STOPPING — Slot selection menu is open. Browser window will remain open.")
            return True

        # Check if result box is visible
        try:
            box_visible = await page.locator("#appointment_box:not(.hidden)").is_visible()
        except Exception:
            box_visible = False

        if box_visible:
            debug(f"Results panel visible on {date_str} but no free slots detected. Checking again...")
            await random_pause(1.5, 2.5)
            slots_found = await check_slots_available(page)
            if slots_found:
                slot_count = await page.evaluate(
                    "() => document.querySelectorAll('#resultDiv .appointment_slot_enabled').length"
                )
                print("\n" + "🟢"*30)
                print(f"  ✅ SLOTS AVAILABLE (on recheck)!")
                print(f"  📅 Date: {date_str}")
                print(f"  📋 Appointment Type: {type_label}")
                print(f"  🔢 Available Slots: {slot_count}")
                print("🟢"*30 + "\n")
                debug("STOPPING — Slot selection menu is open. Browser window will remain open.")
                return True
            else:
                debug(f"✗ Results panel open but all slots taken/disabled on {date_str}.")
                continue

        debug(f"✗ No results or slots for {date_str}. Moving to next date...")

    debug(f"✗ No slots found across {DAYS_TO_SCAN} days for type: {type_label}")
    return False


# ============================================================================
# MAIN
# ============================================================================
async def main():
    async with async_playwright() as p:
        print("="*60)
        print("  GVCW VISA APPOINTMENT SLOT SCANNER")
        print("  Patchright Undetectable Chromium Runtime")
        print("="*60)

        debug("Launching Patchright Undetectable Chromium Browser...")
        browser = await p.chromium.launch(
            headless=False,
            slow_mo=random.randint(30, 70),
            args=[
                "--start-maximized"
            ]
        )

        w = random.randint(1350, 1400)
        h = random.randint(750, 790)
        context = await browser.new_context(
            viewport={"width": w, "height": h},
            locale="en-US",
            timezone_id="Asia/Karachi"
        )

        page = await context.new_page()

        try:
            debug("Browser opened. Pausing like a human looking at the screen...")
            await random_pause(3.0, 5.0)

            debug(f"Navigating to Visa Portal: {TARGET_URL}")
            await page.goto(TARGET_URL, timeout=60000, wait_until="domcontentloaded")

            debug("Page loaded. Looking around before interacting...")
            await human_mouse_move(page)
            await random_pause(2.0, 3.5)
            await human_mouse_move(page)
            await random_pause(1.0, 2.0)

            debug("Locating username field (#username)...")
            username_field = page.locator("#username")
            await username_field.wait_for(state="visible", timeout=30000)
            debug("Username field found. Moving mouse toward it...")

            await username_field.scroll_into_view_if_needed()
            await random_pause(0.5, 1.0)

            debug("Entering username...")
            await human_type(username_field, USER_EMAIL)

            await random_pause(1.5, 2.5)
            await human_mouse_move(page)

            debug("Locating password field (#password)...")
            password_field = page.locator("#password")
            await password_field.wait_for(state="visible", timeout=30000)
            await password_field.scroll_into_view_if_needed()
            await random_pause(0.5, 1.0)
            debug("Entering password...")
            await human_type(password_field, USER_PASS)

            await random_pause(1.5, 3.0)
            await human_mouse_move(page)
            await random_pause(0.5, 1.5)

            await handle_recaptcha(page)

            print("\n" + "="*60)
            print("[ACTION REQUIRED] MANUAL INTERVENTION GATE:")
            print("1. Solve any reCAPTCHA image puzzles if presented.")
            print("2. Click 'Sign In' / Login button.")
            print("="*60 + "\n")

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, input, "Press ENTER in terminal ONLY AFTER successful login...")
            debug("Terminal gate passed — login confirmed by user.")

            debug("Waiting 15 seconds for dashboard to stabilize...")
            await asyncio.sleep(15)

            debug("Navigating to 'Book Appointment' section...")
            try:
                book_app_link = page.locator("#menu-appointments-add a, a[href*='/appointments/add']")
                await book_app_link.wait_for(state="visible", timeout=10000)
                await random_pause(0.5, 1.0)
                await book_app_link.click()
                debug("Clicked 'Book Appointment' sidebar link successfully!")
            except Exception:
                debug("Sidebar click failed. Navigating directly to appointment URL...")
                await page.goto(APPOINTMENT_URL, timeout=30000, wait_until="domcontentloaded")

            debug("Waiting for appointment form to load...")
            await page.locator("#appointment").wait_for(state="visible", timeout=20000)
            debug("Appointment form (#appointment) is visible and ready!")

            await random_pause(1.5, 2.5)
            await human_mouse_move(page)

            await fill_applicant_fields(page)

            print("\n" + "="*60)
            print("[SCANNING] STARTING APPOINTMENT SLOT SCAN")
            print(f"[SCANNING] Will check {DAYS_TO_SCAN} days × {len(APPOINTMENT_TYPES)} types")
            print("="*60)

            slots_found = False
            for type_value, type_label in APPOINTMENT_TYPES:
                result = await scan_dates_for_type(page, type_value, type_label)
                if result:
                    slots_found = True
                    break
                debug(f"Moving to next appointment type...")
                await random_pause(1.0, 2.0)
                await human_mouse_move(page)

            if not slots_found:
                print("\n" + "="*60)
                print("  ❌ NO SLOTS FOUND across all types and dates.")
                print(f"  Scanned: {len(APPOINTMENT_TYPES)} types × {DAYS_TO_SCAN} days")
                print("  Browser window will remain open for manual inspection.")
                print("="*60 + "\n")

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, input,
                "\n[DONE] Scan complete. Press ENTER to close the browser..."
            )

        except Exception as e:
            print(f"\n[ERROR] Execution encountered an error: {e}")
            import traceback
            traceback.print_exc()
            input("\n[PAUSE] Press ENTER to safely close the browser...")

        finally:
            debug("Closing Patchright browser session cleanly.")
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())