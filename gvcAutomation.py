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

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Per-type weekday restriction, keyed by appointment-type value. Values are sets
# of ints using datetime.weekday() numbering (Monday=0 … Sunday=6). A type that
# is absent from this dict has no restriction and is scanned on every date in
# the range — which is what every caller that never sets it gets.
SCAN_WEEKDAYS = {}


# How long to wait for a search to return before judging the result. The wait
# polls and exits as soon as the result lands, so this is a ceiling, not a cost.
SEARCH_RESULT_WAIT_SECONDS = 3

# Once the results panel is open but shows no free slots, how long to keep
# looking while it finishes rendering. Polled, so this is a ceiling too.
PANEL_STABILIZE_SECONDS = 5

# Reload the appointment form after this many complete scan rounds. The page can
# lose its connection to the portal and keep answering searches from a stale
# client-side state — indistinguishable from "no appointments available" — so it
# is refreshed on a schedule rather than trusted indefinitely.
REFRESH_EVERY_N_ROUNDS = 3

# Upper bound on the wait when the page still has a request in flight. The 3s
# ceiling above is for deciding "nothing is coming"; if the network probe says
# the portal simply hasn't answered yet, cutting it off at 3s would manufacture
# the false "no slots" this is meant to prevent.
SEARCH_RESULT_EXTENDED_SECONDS = 15

# How long to confirm that clicking Search actually put a request on the wire.
CLICK_DISPATCH_CONFIRM_SECONDS = 0.6

# Consecutive searches with no server response before the page is treated as
# stalled and reloaded in place.
STALL_TIMEOUT_THRESHOLD = 3

# Wait before each successive in-place recovery. The first is immediate; if the
# stall survives a reload the portal is likely throttling us, and reloading
# harder would only raise our profile — so back off instead. Seconds.
STALL_BACKOFF_SECONDS = (0, 60, 180, 300)

# Consecutive no-response searches, and how many recoveries we have done without
# a genuine server response in between. Both reset the moment a search answers.
_consecutive_timeouts = 0
_stall_recoveries = 0

# The portal (Cloudflare in front of it) answers with this when we have asked
# too often. It is a refusal to answer, NOT an answer of "no appointments".
RATE_LIMITED_STATUS = 429

# Minimum gap between two searches. Self-tuning: it widens when the portal
# pushes back and drifts down again once it stops, so we find the fastest pace
# the portal will actually tolerate instead of guessing one. Seconds.
SEARCH_MIN_INTERVAL_SECONDS = 4.0
SEARCH_MAX_INTERVAL_SECONDS = 20.0
SEARCH_INTERVAL_GROWTH = 1.5
SEARCH_INTERVAL_DECAY = 0.9
CLEAN_SEARCHES_BEFORE_SPEEDUP = 10

# How long to send nothing at all once rate-limited. Measured, not guessed: in
# the 04/08 trace pausing 60s and 180s did not clear the limit, 300s did.
RATE_LIMIT_COOLDOWN_SECONDS = (300, 600, 900)

_search_interval = SEARCH_MIN_INTERVAL_SECONDS
_last_search_at = 0.0
_clean_searches = 0
_rate_limit_cooldowns = 0

# Flat settle after the manual login gate, before touching the dashboard.
DASHBOARD_STABILIZE_SECONDS = 5

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


"""
Wraps XMLHttpRequest and fetch so we can tell three failures apart that
otherwise look identical: the click never fired, the request fired and the
server never answered, or it answered slower than our ceiling. Idempotent —
re-running it on an already-instrumented page is a no-op — but a navigation
wipes it, so it is reinstalled after every page load.
"""
NETWORK_PROBE_JS = """
(function () {
    if (window.__gvcNet) { return; }
    var net = {inflight: 0, started: 0, finished: 0,
               lastStatus: null, lastUrl: '', lastMs: null};
    window.__gvcNet = net;

    var open = XMLHttpRequest.prototype.open;
    var send = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function (method, url) {
        this.__gvcUrl = url;
        return open.apply(this, arguments);
    };

    XMLHttpRequest.prototype.send = function () {
        var xhr = this, t0 = Date.now(), done = false;
        net.started++;
        net.inflight++;
        net.lastUrl = xhr.__gvcUrl || '';
        var finish = function () {
            if (done) { return; }
            done = true;
            net.inflight = Math.max(0, net.inflight - 1);
            net.finished++;
            net.lastMs = Date.now() - t0;
            try { net.lastStatus = xhr.status; } catch (e) { net.lastStatus = -1; }
            // Cloudflare/nginx say how long to wait here when they throttle us
            try { net.lastRetryAfter = xhr.getResponseHeader('Retry-After'); }
            catch (e) { net.lastRetryAfter = null; }
        };
        ['loadend', 'error', 'abort', 'timeout'].forEach(function (evt) {
            xhr.addEventListener(evt, finish);
        });
        return send.apply(this, arguments);
    };

    if (window.fetch) {
        var realFetch = window.fetch;
        window.fetch = function () {
            var t0 = Date.now();
            net.started++;
            net.inflight++;
            var settle = function (status) {
                net.inflight = Math.max(0, net.inflight - 1);
                net.finished++;
                net.lastMs = Date.now() - t0;
                net.lastStatus = status;
            };
            return realFetch.apply(this, arguments).then(
                function (res) { settle(res.status); return res; },
                function (err) { settle(-1); throw err; });
        };
    }
})();
"""


def install_network_probe(driver):
    """(Re)installs the XHR/fetch probe. Safe to call on every page load."""
    try:
        driver.execute_script(NETWORK_PROBE_JS)
    except WebDriverException as err:
        _reraise_if_dead(err)


def read_network_state(driver) -> dict:
    """Probe counters, or {} if the probe isn't installed on this page."""
    try:
        state = driver.execute_script("""
            var n = window.__gvcNet;
            return n ? {inflight: n.inflight, started: n.started,
                        finished: n.finished, lastStatus: n.lastStatus,
                        lastMs: n.lastMs, lastUrl: n.lastUrl,
                        lastRetryAfter: n.lastRetryAfter} : null;
        """)
    except WebDriverException as err:
        _reraise_if_dead(err)
        return {}
    return state or {}


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
    """
    Writes text straight into the DOM. No click, no send_keys, no pause.

    Native keyboard input is bypassed entirely by design: this portal's fields
    are routinely readonly/hidden/zero-size and reject send_keys with
    ElementNotInteractableException, and typing character-by-character costs one
    WebDriver round-trip per character for no benefit. js_set_value sets .value
    and dispatches 'input' and 'change', which is what the page's own handlers
    listen on, so the field registers immediately.
    """
    js_set_value(driver, element, text)

    # Confirm it landed. One plain-write retry covers a change handler that
    # clobbered the value; a field that merely reformats what we wrote (case,
    # spacing) is the page doing its job, so warn rather than abort the run.
    actual = driver.execute_script("return arguments[0].value;", element)
    if (actual or "") != text:
        driver.execute_script("arguments[0].value = arguments[1];", element, text)
        actual = driver.execute_script("return arguments[0].value;", element)
        if (actual or "").strip().upper() != text.strip().upper():
            debug(f"⚠ Field did not accept the value as written — reads {actual!r}, expected {text!r}")


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

    # Native selection — this works on these dropdowns without error, so it stays
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


def human_select_dropdown_by_value(driver, selector: str, value: str):
    """Selects a dropdown option by value, handling both standard and Select2 dropdowns."""
    select_el = driver.find_element(By.CSS_SELECTOR, selector)
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", select_el)

    # Native selection — this works on these dropdowns without error, so it stays
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

    # No pause and no mouse jitter between fields: every write below is a direct
    # DOM assignment that takes effect the instant it returns, so there is
    # nothing to wait for. The idle-behaviour move happens once, above.
    if APPLICANT_FIRST_NAME:
        debug(f"Filling First Name: {APPLICANT_FIRST_NAME}")
        first_name_field = driver.find_element(By.CSS_SELECTOR, "#gp_firstname")
        human_type(driver, first_name_field, APPLICANT_FIRST_NAME)

    if APPLICANT_SURNAME:
        debug(f"Filling Surname: {APPLICANT_SURNAME}")
        surname_field = driver.find_element(By.CSS_SELECTOR, "#gp_surname")
        human_type(driver, surname_field, APPLICANT_SURNAME)

    debug(f"Filling Date of Birth: {APPLICANT_DOB}")
    human_type_date(driver, "#gp_dateofbirth", APPLICANT_DOB)

    debug(f"Filling Passport Number: {APPLICANT_PASSPORT}")
    passport_field = driver.find_element(By.CSS_SELECTOR, "#gp_passportnumber")
    human_type(driver, passport_field, APPLICANT_PASSPORT)

    debug(f"Filling Passport Expiry: {APPLICANT_PASSPORT_EXPIRY}")
    human_type_date(driver, "#gp_traveldocumentvaliduntil", APPLICANT_PASSPORT_EXPIRY)

    # Dropdowns keep native selection — Select() works on these without error,
    # so there is no reason to inject values into them by hand.
    debug(f"Setting Gender to MALE (value={APPLICANT_GENDER_VALUE})")
    human_select_dropdown_by_value(driver, "#gp_gender", APPLICANT_GENDER_VALUE)

    debug("Setting Nationality to PAKISTAN...")
    human_select_dropdown(driver, "#gp_nationality", APPLICANT_NATIONALITY_TEXT)

    debug("All client information fields filled successfully!")


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
    started = time.monotonic()
    deadline = started + timeout
    hard_deadline = started + max(timeout, SEARCH_RESULT_EXTENDED_SECONDS)
    extended = False

    while True:
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

        now = time.monotonic()
        if now >= deadline:
            # Past the normal ceiling. Keep waiting only while the page still
            # has a request outstanding — a slow portal is not the same thing
            # as no appointments, and calling it early is how false negatives
            # get logged. With no probe, behave exactly as before.
            if now >= hard_deadline or read_network_state(driver).get("inflight", 0) <= 0:
                return False
            if not extended:
                debug(f"Portal has not answered in {timeout}s but a request is still "
                      f"in flight — waiting up to {SEARCH_RESULT_EXTENDED_SECONDS}s.")
                extended = True
            deadline = min(hard_deadline, now + 1.0)
        time.sleep(0.15)


def interruptible_sleep(total_seconds: float, label: str):
    """
    Long pause that still lets the GUI stop the scan.

    The GUI delivers Stop by raising KeyboardInterrupt out of its patched
    print(), so a single blocking time.sleep(300) would leave the Stop button
    dead for five minutes. Sleeping in slices and logging progress gives the
    interrupt somewhere to land.
    """
    deadline = time.monotonic() + total_seconds
    next_note = time.monotonic() + 15
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(1.0, left))
        if time.monotonic() >= next_note:
            left = deadline - time.monotonic()
            if left > 3:
                debug(f"   {label} — {int(left)}s remaining...")
            next_note = time.monotonic() + 15


def pace_search():
    """Holds each search back so we never exceed the current sustainable rate."""
    global _last_search_at
    if _last_search_at:
        wait = _search_interval - (time.monotonic() - _last_search_at)
        if wait > 0:
            time.sleep(wait)
    _last_search_at = time.monotonic()


def note_search_outcome(status):
    """
    Tunes the search interval from what the portal actually returns.

    Guessing a safe fixed rate is not possible from outside — the limit is the
    portal's and it is not published. Widening on refusal and easing back on a
    clean run converges on the fastest pace it will tolerate.
    """
    global _search_interval, _clean_searches, _rate_limit_cooldowns

    if status == RATE_LIMITED_STATUS:
        _clean_searches = 0
        previous = _search_interval
        _search_interval = min(SEARCH_MAX_INTERVAL_SECONDS,
                               _search_interval * SEARCH_INTERVAL_GROWTH)
        if _search_interval > previous:
            debug(f"Rate limited — spacing searches {previous:.1f}s → {_search_interval:.1f}s apart.")
        return

    if status is not None and 200 <= status < 400:
        _rate_limit_cooldowns = 0
        _clean_searches += 1
        if (_clean_searches >= CLEAN_SEARCHES_BEFORE_SPEEDUP
                and _search_interval > SEARCH_MIN_INTERVAL_SECONDS):
            _clean_searches = 0
            previous = _search_interval
            _search_interval = max(SEARCH_MIN_INTERVAL_SECONDS,
                                   _search_interval * SEARCH_INTERVAL_DECAY)
            debug(f"{CLEAN_SEARCHES_BEFORE_SPEEDUP} clean searches — easing pace "
                  f"{previous:.1f}s → {_search_interval:.1f}s.")


def cool_down_after_rate_limit(retry_after=None):
    """
    Stops sending anything until the portal's limit resets.

    Deliberately does NOT reload the page. A reload costs several more HTTP
    requests against the same exhausted budget, which is why the 04/08 run kept
    getting 429s straight after each recovery — the limit is per IP/session and
    survives a fresh page. The only thing that clears it is silence.
    """
    global _rate_limit_cooldowns

    index = min(_rate_limit_cooldowns, len(RATE_LIMIT_COOLDOWN_SECONDS) - 1)
    seconds = RATE_LIMIT_COOLDOWN_SECONDS[index]

    if retry_after:
        try:
            seconds = max(seconds, float(retry_after))
            debug(f"Portal sent Retry-After: {retry_after}s.")
        except (TypeError, ValueError):
            pass

    _rate_limit_cooldowns += 1
    print("\n" + "⛔" * 30)
    print(f"  RATE LIMITED (HTTP {RATE_LIMITED_STATUS}) — the portal is refusing to answer.")
    print(f"  Going quiet for {int(seconds)}s. No searches, no reloads.")
    print(f"  Dates hit by this are NOT recorded as 'no availability'.")
    print("⛔" * 30 + "\n")
    interruptible_sleep(seconds, "Rate-limit cooldown")
    debug("Cooldown finished — resuming at the slower pace.")


def fire_search(driver):
    """
    Clicks Search and confirms the click actually reached the page.

    A click that lands on a dead handler produces no request and no DOM change,
    which is indistinguishable downstream from "the portal said no slots". Here
    we watch the probe's request counter: if nothing goes out, the click is
    retried once before we accept the result.
    """
    for attempt in (1, 2):
        before = read_network_state(driver)
        reset_search_result_state(driver)
        safe_click(driver, "#btn-search", "#btn-search (Search)")

        if before.get("started") is None:
            return          # no probe on this page — nothing to verify against

        deadline = time.monotonic() + CLICK_DISPATCH_CONFIRM_SECONDS
        while time.monotonic() < deadline:
            after = read_network_state(driver)
            if after.get("started", 0) > before.get("started", 0):
                return
            if after.get("inflight", 0) > 0:
                return
            time.sleep(0.05)

        if attempt == 1:
            debug("Search click put no request on the wire — clicking once more...")

    debug("⚠ Search click still produced no request after a retry.")


def wait_for_slots_to_render(driver, timeout=None) -> bool:
    """
    Waits for free slots to appear in an already-open results panel.

    Replaces a blind sleep-then-look-once: the panel can open a moment before
    its slot buttons are painted, so a single immediate check can miss them.
    Polling returns the instant a slot shows up, so the ceiling below is the
    worst case for a genuinely empty panel, not the cost of every check.
    """
    if timeout is None:
        timeout = PANEL_STABILIZE_SECONDS
    deadline = time.monotonic() + timeout
    while True:
        try:
            if check_slots_available(driver):
                return True
        except WebDriverException as err:
            _reraise_if_dead(err)
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.15)


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


def dates_for_type(type_value: str, start_date: datetime, end_date: datetime) -> list:
    """
    The dates in [start_date, end_date] this appointment type should be searched on.

    Types with no entry in SCAN_WEEKDAYS get the whole range, so the default
    behaviour is unchanged for any caller that never touches the filter.
    """
    every_day = [start_date + timedelta(days=offset)
                 for offset in range((end_date - start_date).days + 1)]

    allowed = SCAN_WEEKDAYS.get(type_value)
    if not allowed:
        return every_day
    return [d for d in every_day if d.weekday() in allowed]


def describe_weekday_filter(type_value: str) -> str:
    """'Tue, Thu' for a restricted type, empty string when it scans every day."""
    allowed = SCAN_WEEKDAYS.get(type_value)
    if not allowed or len(allowed) >= 7:
        return ""
    return ", ".join(WEEKDAY_NAMES[d] for d in sorted(allowed))


def count_dates_to_scan() -> int:
    """Total searches one full round will make across every enabled type."""
    try:
        start_date = datetime.strptime(SCAN_START_DATE_STR, "%d/%m/%Y")
        end_date = datetime.strptime(SCAN_END_DATE_STR, "%d/%m/%Y")
    except ValueError:
        return 0
    if end_date < start_date:
        return 0
    return sum(len(dates_for_type(v, start_date, end_date)) for v, _ in APPOINTMENT_TYPES)


def select_appointment_type(driver, type_value: str, type_label: str):
    """
    Picks the appointment type and handles the Travel Purpose dropdown it can
    reveal. Split out of the scan so an in-place reload can restore it — a
    reload resets #type back to its default, and scanning on would silently
    query the wrong category.
    """
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


def recover_stalled_page(driver, type_value: str, type_label: str):
    """
    In-place recovery from a stalled page: reload the form in the SAME window,
    re-arm native clicks, and re-select the appointment type so scanning
    resumes on the right category. Never touches the browser process, the
    session, or the date list.
    """
    global _stall_recoveries, _consecutive_timeouts

    backoff = STALL_BACKOFF_SECONDS[min(_stall_recoveries, len(STALL_BACKOFF_SECONDS) - 1)]
    if backoff:
        debug(f"⏳ Still stalling after {_stall_recoveries} recovery attempt(s) — waiting "
              f"{backoff}s before trying again. Reloading harder would only look like "
              f"more automated traffic to the portal.")
        interruptible_sleep(backoff, "Stall backoff")

    _stall_recoveries += 1
    reopen_appointment_form(
        driver,
        f"stalled — {STALL_TIMEOUT_THRESHOLD} consecutive searches got no server response")
    select_appointment_type(driver, type_value, type_label)
    _consecutive_timeouts = 0


def scan_dates_for_type(driver, type_value: str, type_label: str) -> bool:
    """
    For a given appointment type, scans today + next DAYS_TO_SCAN days for available slots.
    Returns True if slots were found (and stops), False to continue to next type.
    """
    print("\n" + "=" * 60)
    print(f"[SCANNING] Appointment Type: {type_label}")
    print(f"[SCANNING] Type value: {type_value}")
    print("=" * 60)

    select_appointment_type(driver, type_value, type_label)

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

    dates = dates_for_type(type_value, start_date, end_date)
    days_to_scan = len(dates)

    weekday_filter = describe_weekday_filter(type_value)
    if weekday_filter:
        debug(f"Weekdays: {weekday_filter} — {days_to_scan} of {delta + 1} dates in range")
    if not dates:
        debug(f"⚠ No dates in range match the selected weekdays ({weekday_filter}). Skipping this type.")
        return False

    # Scan each day. Index-based rather than `for ... in`: a date interrupted by
    # a stall recovery is retried on the same index, so no configured date is
    # ever dropped from the sweep.
    global _consecutive_timeouts, _stall_recoveries
    day_index = 0
    retried_after_recovery = False
    unchecked = []          # dates the portal refused to answer for

    while day_index < len(dates):
        target_date = dates[day_index]
        date_str = target_date.strftime("%d/%m/%Y")

        print(f"\n  --- Day {day_index + 1}/{days_to_scan}: "
              f"{WEEKDAY_NAMES[target_date.weekday()]} {date_str} ---")

        debug(f"Setting Appointment Date to: {date_str}")
        human_type_date(driver, "#datefrom", date_str)

        debug("Clicking 'Search' button (#btn-search)...")
        pace_search()
        fire_search(driver)

        if wait_for_search_result(driver):
            _consecutive_timeouts = 0
            _stall_recoveries = 0
            retried_after_recovery = False
            note_search_outcome(read_network_state(driver).get("lastStatus"))
        else:
            net = read_network_state(driver)
            status = net.get("lastStatus")
            note_search_outcome(status)

            # A 429 is a refusal to answer, not an answer. Recording it as "no
            # availability" would hide a real slot, and reloading the page to
            # "recover" only spends more of the same exhausted budget.
            if status == RATE_LIMITED_STATUS:
                debug(f"⛔ Portal refused to answer for {date_str} (HTTP {status}) — "
                      f"this date has NOT been checked.")
                if _rate_limit_cooldowns < len(RATE_LIMIT_COOLDOWN_SECONDS):
                    cool_down_after_rate_limit(net.get("lastRetryAfter"))
                    continue      # same date — it was never actually queried
                unchecked.append(date_str)
                debug(f"⚠ Still rate-limited after {_rate_limit_cooldowns} cooldowns. "
                      f"{date_str} stays UNCHECKED and will be retried next round.")
                day_index += 1
                continue

            _consecutive_timeouts += 1
            debug(f"No response within {SEARCH_RESULT_WAIT_SECONDS}s for {date_str} "
                  f"(consecutive: {_consecutive_timeouts}) — "
                  f"requests sent={net.get('started', '?')}, in flight={net.get('inflight', '?')}, "
                  f"last HTTP status={status}, last took={net.get('lastMs', '?')}ms")

            if _consecutive_timeouts >= STALL_TIMEOUT_THRESHOLD and not retried_after_recovery:
                recover_stalled_page(driver, type_value, type_label)
                retried_after_recovery = True
                continue          # same date, freshly loaded page — nothing skipped

            debug(f"Treating {date_str} as no availability.")

        # Past the point of no return for this date: every branch below moves on
        # by exactly one, so advance here and let the existing `continue`s work.
        day_index += 1

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
            slots_found = wait_for_slots_to_render(driver)
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

    if unchecked:
        print("\n" + "!" * 60)
        print(f"  ⚠ {len(unchecked)} of {days_to_scan} dates were NOT checked for this type")
        print(f"     {type_label}")
        print(f"     The portal rate-limited us on: {', '.join(unchecked)}")
        print(f"     These are NOT 'no availability' — they are unknown, and will")
        print(f"     be retried on the next round.")
        print("!" * 60)
        debug(f"✗ No slots found across {days_to_scan - len(unchecked)} verified days "
              f"for type: {type_label} ({len(unchecked)} unchecked)")
    else:
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
            install_network_probe(driver)   # a navigation wipes the previous one
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


def reopen_appointment_form(driver, reason: str):
    """
    Reloads the Book Appointment form in the SAME window and re-fills it.

    This is a fresh navigation, not a browser restart — the Chrome window, the
    session and the login all survive. Used both after a manual booking and on
    the periodic refresh, because the page can quietly lose its connection to
    the portal and then answer every search from a dead client-side state,
    which looks exactly like "no appointments available".

    Raises on failure so the caller's recovery path can restart the browser.
    """
    global _NATIVE_CLICK_WORKS, _consecutive_timeouts

    debug(f"↻ Reloading the appointment form ({reason})...")
    try:
        open_appointment_page(driver)
        assert_vac_on_appointment_page(driver)

        # Fresh DOM: re-arm the native-click probe and reinstall the network
        # probe, both of which a navigation wipes out.
        _NATIVE_CLICK_WORKS = True
        _consecutive_timeouts = 0
        debug("↻ Native-click probe re-armed (USE_JS_CLICK reset to False) for the new DOM.")

        fill_applicant_fields(driver)
    except SessionLostError:
        raise
    except Exception as reload_err:
        debug(f"Could not reload appointment form ({reload_err}) — will restart browser.")
        raise Exception("Appointment form reload failed — restarting...")
    debug("↻ Appointment form reloaded and re-filled — resuming the scan.")


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
    global _NATIVE_CLICK_WORKS, _consecutive_timeouts, _stall_recoveries
    global _search_interval, _last_search_at, _clean_searches, _rate_limit_cooldowns
    _NATIVE_CLICK_WORKS = True   # fresh session — probe native clicks once more
    _consecutive_timeouts = 0
    _stall_recoveries = 0
    _search_interval = SEARCH_MIN_INTERVAL_SECONDS
    _last_search_at = 0.0
    _clean_searches = 0
    _rate_limit_cooldowns = 0

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

    debug(f"Waiting {DASHBOARD_STABILIZE_SECONDS} seconds for dashboard to stabilize...")
    time.sleep(DASHBOARD_STABILIZE_SECONDS)
    
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
            rounds_since_refresh = 0

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
                # Weekday filters make "types × days" a lie, so report the real
                # number of searches this round will actually make.
                searches = count_dates_to_scan()
                if searches:
                    print(f"  Checking {len(APPOINTMENT_TYPES)} types over {days_to_scan} days "
                          f"— {searches} searches")
                else:
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
                    reopen_appointment_form(driver, "returning from a manual booking")
                    rounds_since_refresh = 0
                else:
                    print("\n" + "-" * 60)
                    print(f"  ❌ Round {round_number} complete — no slots found.")
                    pause_secs = random.uniform(5, 15)
                    debug(f"Pausing {pause_secs:.1f}s before next round...")
                    print("-" * 60)
                    time.sleep(pause_secs)

                    # Scheduled reload: same window, fresh page, form re-filled.
                    rounds_since_refresh += 1
                    if rounds_since_refresh >= REFRESH_EVERY_N_ROUNDS:
                        if not is_browser_alive(driver):
                            raise Exception("Browser window was closed — restarting...")
                        reopen_appointment_form(
                            driver,
                            f"scheduled — {rounds_since_refresh} rounds since the last reload")
                        rounds_since_refresh = 0

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
