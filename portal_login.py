import asyncio
import random
from patchright.async_api import async_playwright, Page

# Configuration constants
TARGET_URL = "https://pk-gr-services.gvcworld.eu/"
APPOINTMENT_URL = "https://pk-gr-services.gvcworld.eu/appointments/add"
USER_EMAIL = "najeeb21"
USER_PASS = "980Aa0330"

async def human_type(page_element, text: str):
    """Types text with random human-like delays and dispatches DOM validation events."""
    await page_element.click()
    await page_element.press("Control+A")
    await page_element.press("Backspace")
    
    for char in text:
        await page_element.type(char)
        await asyncio.sleep(random.uniform(0.05, 0.20))
        
    # Dispatch native JS events so frontend validation scripts (jQuery/React/Vue) recognize the input
    await page_element.evaluate("""el => {
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.blur();
    }""")

async def handle_recaptcha(page: Page):
    """Attempts to auto-click the reCAPTCHA checkbox inside its iframe."""
    print("Locating and clicking reCAPTCHA checkbox...")
    try:
        recaptcha_frame = page.frame_locator("iframe[title='reCAPTCHA'], iframe[src*='recaptcha']")
        recaptcha_checkbox = recaptcha_frame.locator("#recaptcha-anchor, .recaptcha-checkbox-border")
        await recaptcha_checkbox.wait_for(state="visible", timeout=10000)
        await recaptcha_checkbox.click()
        print("reCAPTCHA checkbox clicked automatically!")
    except Exception as err:
        print(f"Notice: reCAPTCHA auto-click skipped or requires manual check ({err}).")

async def navigate_to_appointments(page: Page):
    """Navigates to the Book Appointment section using menu click or direct URL."""
    print("\nWaiting 15 seconds after login...")
    await asyncio.sleep(15)
    
    print("Navigating to 'Book Appointment' section...")
    try:
        # Locators from DevTools: <li id="menu-appointments-add"><a href="/appointments/add">
        book_app_link = page.locator("#menu-appointments-add a, a[href*='/appointments/add']")
        await book_app_link.wait_for(state="visible", timeout=10000)
        await book_app_link.click()
        print("Successfully clicked 'Book Appointment' sidebar link!")
    except Exception as nav_err:
        print(f"Sidebar click failed ({nav_err}). Navigating directly to URL...")
        await page.goto(APPOINTMENT_URL, timeout=30000)
        
    print("Successfully arrived at Book Appointment section!")

async def main():
    # Launch Patchright async engine (undetectable Chromium runtime)
    async with async_playwright() as p:
        print("Launching Patchright Undetectable Chromium Browser...")
        
        browser = await p.chromium.launch(
            headless=False,
            slow_mo=random.randint(50, 120),
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--start-maximized"
            ]
        )
        
        # Create a stealth browser context with standard desktop fingerprint
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="Asia/Karachi"
        )
        
        page = await context.new_page()
        
        try:
            print(f"Navigating to Visa Portal: {TARGET_URL}")
            await page.goto(TARGET_URL, timeout=60000, wait_until="domcontentloaded")
            
            # 1. Fill Username/Email
            print("Locating username field...")
            username_field = page.locator("#username")
            await username_field.wait_for(state="visible", timeout=30000)

            print("Entering username...")
            await human_type(username_field, USER_EMAIL)
            
            await asyncio.sleep(random.uniform(1.0, 2.0))
            
            # 2. Fill Password
            print("Locating password field...")
            password_field = page.locator("#password")
            await password_field.wait_for(state="visible", timeout=30000)
            print("Entering password...")
            await human_type(password_field, USER_PASS)
            
            await asyncio.sleep(random.uniform(1.0, 2.0))
            
            # 3. Auto-click reCAPTCHA
            await handle_recaptcha(page)
            
            # 4. Manual Gate for CAPTCHA image puzzle & Sign In
            print("\n" + "="*60)
            print("[ACTION REQUIRED] MANUAL INTERVENTION GATE:")
            print("1. Solve any reCAPTCHA image puzzles if presented.")
            print("2. Click 'Sign In' / Login button if not auto-submitted.")
            print("="*60 + "\n")
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, input, "Press ENTER in terminal ONLY AFTER successful login...")
            print("Terminal gate passed successfully!")
            
            # 5. Navigate to Book Appointment section
            await navigate_to_appointments(page)

        except Exception as e:
            print(f"\n[ERROR] Execution encountered an error: {e}")
            input("\n[PAUSE] Press ENTER to safely close the browser...")
            
        finally:
            print("Closing Patchright browser session cleanly.")
            await context.close()
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())