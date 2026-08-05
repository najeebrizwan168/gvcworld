import os
import re
import sys
import json
import time
import random
import hashlib
import threading
from pathlib import Path
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

# Every option the profile page's #vac select offers (profile-page-structure.md
# §2 "Select option values"). Keys are what the GUI stores in TARGET_CITY.
VAC_IDS = {
    "islamabad": "137",
    "lahore": "138",
    "verification": "140",
}

# Display names, used for log lines and for confirming the select2 widget
# actually repainted after a change. TARGET_CITY.capitalize() was fine while
# every centre was one word; "Verification Office" is not.
VAC_LABELS = {
    "islamabad": "Islamabad",
    "lahore": "Lahore",
    "verification": "Verification Office",
}

# What a user (or an older config file) might have typed for the same centre.
VAC_ALIASES = {
    "verification office": "verification",
    "verification-office": "verification",
    "verificationoffice": "verification",
}


def vac_key(city: str) -> str:
    """Normalises a city string to a VAC_IDS key. Raises on anything unknown —
    silently defaulting would scan the wrong centre."""
    name = (city or "").strip().lower()
    name = VAC_ALIASES.get(name, name)
    if name not in VAC_IDS:
        raise ValueError(f"Unknown target city: {city!r}")
    return name


def vac_id_for(city: str) -> str:
    return VAC_IDS[vac_key(city)]


def vac_label(city: str) -> str:
    return VAC_LABELS[vac_key(city)]
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

# The Verification Office renders exactly one type — the option list is built
# server-side per VAC (book-appointment-GROUP-structure.md §0), so Islamabad's
# ids are not valid here.
APPOINTMENT_TYPES_VERIFICATION = [
    ("24", "Document Verification"),
]

APPOINTMENT_TYPES = APPOINTMENT_TYPES_ISLAMABAD.copy()

APPOINTMENT_TYPES_BY_VAC = {
    "islamabad": APPOINTMENT_TYPES_ISLAMABAD,
    "lahore": APPOINTMENT_TYPES_LAHORE,
    "verification": APPOINTMENT_TYPES_VERIFICATION,
}

def get_appointment_types(city: str):
    """Each centre renders its own #type list. Islamabad's doubles as the
    fallback for a city string we do not recognise."""
    try:
        return APPOINTMENT_TYPES_BY_VAC[vac_key(city)]
    except ValueError:
        return APPOINTMENT_TYPES_ISLAMABAD


# ── "Booking as" — #bookingfor (book-appointment-structure.md §4) ────────────
# Required on the appointment form. Selecting Group reveals #membersDiv and
# #appointmentmethodDiv, both of which arrive empty and have to be filled or
# #btn-search fails its client-side validation and never hits the network.
BOOKING_FOR_INDIVIDUAL = "0"
BOOKING_FOR_GROUP = "1"
BOOKING_FOR_LABELS = {
    BOOKING_FOR_INDIVIDUAL: "Individual",
    BOOKING_FOR_GROUP: "Group (Family/Traveler)",
}
DEFAULT_BOOKING_FOR = BOOKING_FOR_INDIVIDUAL

# Per-appointment-type override, keyed by type value — the GUI fills this in.
# A type that is absent books as DEFAULT_BOOKING_FOR.
BOOKING_FOR_BY_TYPE = {}


def booking_for_for_type(type_value: str) -> str:
    """The #bookingfor value this appointment type should be searched under."""
    value = str(BOOKING_FOR_BY_TYPE.get(type_value, DEFAULT_BOOKING_FOR))
    return value if value in BOOKING_FOR_LABELS else DEFAULT_BOOKING_FOR


# ── Group booking (book-appointment-GROUP-structure.md) ──────────────────────
# Only read when Booking as = Group. #members offers 2–5; setting it is what
# clones the applicant rows, and #appointmentmethod decides how their slots
# relate. Both live behind `.hidden` until #bookingfor = 1, and #btn-search
# validates both — plus every visible applicant row — before it will put
# anything on the wire.
GROUP_MEMBER_COUNT = "2"                    # 2–5, total people including the primary
GROUP_APPOINTMENT_METHOD = "1"              # 1 = Same time

APPOINTMENT_METHOD_LABELS = {
    "1": "Same time",
    "2": "Consecutive time slots",
    "3": "Next available slots",
    "4": "Select one by one",
}

# Members 2..N only. Row 0 is the primary applicant and always comes from the
# APPLICANT_* settings, so this list holds at most four people. Each entry:
# {surname, firstname, dob, passport, expiry, gender, nationality}.
GROUP_MEMBERS = []

# Cloning runs off #members' change handler, so the rows appear asynchronously.
GROUP_ROW_RENDER_SECONDS = 10


def group_member_count() -> int:
    """Total people in the group, clamped to what #members actually offers."""
    try:
        count = int(str(GROUP_MEMBER_COUNT))
    except (TypeError, ValueError):
        return 2
    return max(2, min(5, count))


def group_appointment_method() -> str:
    value = str(GROUP_APPOINTMENT_METHOD)
    return value if value in APPOINTMENT_METHOD_LABELS else "1"


def group_is_configured() -> bool:
    """True when at least one appointment type will be searched as a group."""
    return any(booking_for_for_type(value) == BOOKING_FOR_GROUP
               for value, _ in APPOINTMENT_TYPES)


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

_search_interval = SEARCH_MIN_INTERVAL_SECONDS
_last_search_at = 0.0
_clean_searches = 0
_rate_limit_cooldowns = 0

# Flat settle after the manual login gate, before touching the dashboard.
DASHBOARD_STABILIZE_SECONDS = 5

# Latched off the first time this portal refuses a native click (it refuses all
# of them), so later clicks skip straight to JS. Reset on each browser launch.
_NATIVE_CLICK_WORKS = True


# ── Where persistent state lives ────────────────────────────────────────────
# Beside the .exe when frozen, beside the source otherwise — same rule the GUI
# already uses for its config, so everything a user might need to delete sits
# together in one folder.
if getattr(sys, "frozen", False):
    PERSIST_DIR = Path(sys.executable).resolve().parent
else:
    PERSIST_DIR = Path(__file__).resolve().parent

# Chrome's own profile. Keeping it means the portal's login survives a browser
# restart, which is what makes an automatic restart cheap enough to use as a
# recovery step at all — without it every restart would need a human at the
# reCAPTCHA.
CHROME_PROFILE_DIR = PERSIST_DIR / "chrome_profile"

# Cookies exported from a good session, as a belt-and-braces backup: a Chrome
# profile can be invalidated by a crash or a version upgrade, and re-injecting
# the cookies restores the session without one.
SESSION_FILE = PERSIST_DIR / "gvc_session.json"

# Where the scan is up to. Rewritten before every individual date search so an
# interrupted run resumes on the exact date it was interrupted on.
SCAN_STATE_FILE = PERSIST_DIR / "gvc_scan_state.json"

# One plain-text transcript per run, kept beside everything else. The GUI
# replaces this module's print() with one that only reaches the Tk widget, so
# without an explicit sink the on-screen log is the only copy and it dies with
# the window — which is exactly the log you want after an overnight run.
LOG_DIR = PERSIST_DIR / "logs"
LOG_RETENTION = 20          # session files kept; older ones are pruned on start

# Cookie names that indicate a live portal session. Substring match, lowercased
# — the portal is ASP.NET today but the check shouldn't be brittle about it.
SESSION_COOKIE_HINTS = ("session", "sessid", "asp.net", "auth", "token",
                        "phpsess", "laravel")

# How long to let the driver and its profile lock clear after quitting Chrome
# before starting it again. Too short and Chrome refuses with "user data
# directory is already in use".
DRIVER_CLEANUP_SECONDS = 12

# Ceiling on the wait for a human to finish signing in. Polled, so a fast login
# costs nothing — this only bounds how long an abandoned run waits.
LOGIN_DETECT_TIMEOUT_SECONDS = 900

# What to do when the portal rate-limits us, indexed by how many times it has
# happened without a clean search in between. The first entry is just the
# driver-cleanup pause: restart immediately, resume from the checkpoint, and see
# whether a fresh browser session is enough. If it isn't, escalate to going
# properly quiet — silence is the only thing measured to clear this portal's
# limit, and a restart on its own does not (the limit follows the IP, not the
# browser). With Chrome closed the quiet is real: no background polling either.
RATE_LIMIT_RESTART_WAITS = (DRIVER_CLEANUP_SECONDS, 300, 600, 900)

# Optional callbacks installed by the GUI; both stay None under the terminal
# runner and are only ever called if they are callable.
ON_SESSION_READY = None    # called when a session comes up with no human needed
LOGIN_OVERRIDE = None      # returns True if the operator says "I am signed in"


class SessionLostError(Exception):
    """Raised when the portal has bounced us back to the login page."""


class RateLimitRestart(Exception):
    """
    Raised when the portal answers a search with HTTP 429.

    Carries no state of its own — the scan position is already on disk in
    SCAN_STATE_FILE by the time this is raised, so the handler in main() can
    tear the browser down and rebuild it without losing the place.
    """

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


# ============================================================================
# SESSION LOG — live transcript on disk
# ============================================================================
# Opened once per run and appended to line by line, flushed on every write so
# the file is complete even if the process is killed. Writes come from the scan
# thread, the Tk main thread and the stream tee, hence the lock.
_session_log = None
_session_log_path = None
_log_lock = threading.Lock()
_captured_streams = None        # (stdout, stderr) as they were before the tee


def session_log_path():
    """Path of the transcript for this run, or None if logging never started."""
    return _session_log_path


def _prune_old_logs():
    """Keeps the newest LOG_RETENTION transcripts. A scanner that runs for weeks
    should not quietly fill a disk with its own diary."""
    try:
        files = sorted(LOG_DIR.glob("session_*.log"),
                       key=lambda p: p.name, reverse=True)
        for stale in files[LOG_RETENTION:]:
            try:
                stale.unlink()
            except OSError:
                pass
    except OSError:
        pass


def start_session_log(capture_streams=False):
    """
    Opens this run's transcript and returns its path (None if it can't be
    written — logging must never be the thing that stops a scan).

    `capture_streams` also mirrors stdout/stderr into it, which is what catches
    tracebacks and anything printed by a library. The GUI passes True; it feeds
    its own lines in through log_to_file() because its patched print() never
    reaches stdout at all.
    """
    global _session_log, _session_log_path

    with _log_lock:
        if _session_log is not None:
            return _session_log_path
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = LOG_DIR / f"session_{stamp}.log"
            handle = open(path, "a", encoding="utf-8", buffering=1)
        except OSError:
            return None
        _session_log = handle
        _session_log_path = path

    _prune_old_logs()
    log_to_file("=" * 60)
    log_to_file(f"GVCW scanner session log — started "
                f"{datetime.now().isoformat(timespec='seconds')}")
    log_to_file("=" * 60)

    if capture_streams:
        global _captured_streams
        _captured_streams = (sys.stdout, sys.stderr)
        sys.stdout = _StreamTee(sys.stdout)
        sys.stderr = _StreamTee(sys.stderr)
    return _session_log_path


def log_to_file(text, raw=False):
    """Appends a line to the transcript. `raw` writes the text exactly as given
    (used by the stream tee, whose chunks carry their own newlines)."""
    if _session_log is None or text is None:
        return
    with _log_lock:
        handle = _session_log
        if handle is None:
            return
        try:
            handle.write(text if raw else f"{text}\n")
            handle.flush()
        except (OSError, ValueError):
            # Disk full, or the handle was closed from another thread mid-write.
            pass


def close_session_log(reason="closed"):
    """Closes the transcript and unhooks the streams. Safe to call twice."""
    global _session_log, _session_log_path, _captured_streams

    log_to_file(f"--- session log {reason} "
                f"{datetime.now().isoformat(timespec='seconds')} ---")
    with _log_lock:
        handle, _session_log = _session_log, None
        _session_log_path = None

    if _captured_streams is not None:
        # Only put back what we replaced — anything installed on top of our tee
        # since then belongs to someone else and is left alone.
        original_out, original_err = _captured_streams
        if isinstance(sys.stdout, _StreamTee):
            sys.stdout = original_out
        if isinstance(sys.stderr, _StreamTee):
            sys.stderr = original_err
        _captured_streams = None

    if handle is not None:
        try:
            handle.close()
        except OSError:
            pass


class _StreamTee:
    """Mirrors a stream into the session log without swallowing it."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, data):
        if self._stream is not None:
            try:
                self._stream.write(data)
            except Exception:
                pass
        log_to_file(data, raw=True)
        return len(data)

    def flush(self):
        if self._stream is not None:
            try:
                self._stream.flush()
            except Exception:
                pass

    def isatty(self):
        return False


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


# ============================================================================
# SESSION PERSISTENCE — profile, cookies, login detection
# ============================================================================
def _write_json_atomically(path: Path, payload: dict) -> bool:
    """Writes via a temp file and one rename, so a crash mid-write can never
    leave a half-written state file that fails to parse on the next run."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError as err:
        debug(f"⚠ Could not write {path.name} ({err}).")
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def _is_session_cookie(cookie: dict) -> bool:
    name = str(cookie.get("name", "")).lower()
    return any(hint in name for hint in SESSION_COOKIE_HINTS)


def portal_session_cookies(driver) -> list:
    """The session cookies the browser is currently holding for the portal."""
    try:
        cookies = driver.get_cookies() or []
    except WebDriverException as err:
        _reraise_if_dead(err)
        return []
    return [c for c in cookies if _is_session_cookie(c)]


def save_session_cookies(driver) -> bool:
    """
    Exports the authenticated cookies so a later launch can skip the login gate
    even if the Chrome profile itself is unusable.

    Cookie *values* are secrets — they are a bearer token for this account — so
    they are written to disk but never logged.
    """
    try:
        cookies = driver.get_cookies() or []
    except WebDriverException as err:
        _reraise_if_dead(err)
        return False

    if not any(_is_session_cookie(c) for c in cookies):
        debug("No portal session cookie to save yet.")
        return False

    ok = _write_json_atomically(SESSION_FILE, {
        "version": 1,
        "account": USER_EMAIL,
        "origin": TARGET_URL,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "cookies": cookies,
    })
    if ok:
        debug(f"🔐 Saved {len(cookies)} cookie(s) — next launch can skip the login gate.")
    return ok


def load_session_cookies() -> list:
    """Saved cookies for *this* account, or [] if there are none to use."""
    try:
        payload = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []

    account = payload.get("account")
    if account and account != USER_EMAIL:
        debug("The saved session belongs to a different account — ignoring it.")
        return []
    return [c for c in payload.get("cookies", [])
            if isinstance(c, dict) and c.get("name")]


def clear_session_cookies():
    try:
        SESSION_FILE.unlink()
    except OSError:
        pass


def inject_session_cookies(driver, cookies) -> int:
    """
    Adds saved cookies to the live browser. Must be called while already on the
    portal's origin — Chrome rejects a cookie whose domain doesn't match the
    current page. Returns how many were accepted.
    """
    added = 0
    now = time.time()
    for cookie in cookies:
        entry = {k: v for k, v in cookie.items()
                 if k in ("name", "value", "path", "domain", "secure",
                          "httpOnly", "expiry", "sameSite")}
        expiry = entry.get("expiry")
        if isinstance(expiry, (int, float)) and expiry <= now:
            continue                       # already dead; adding it just fails
        if entry.get("sameSite") not in ("Strict", "Lax", "None"):
            entry.pop("sameSite", None)    # Chrome rejects anything else
        try:
            driver.add_cookie(entry)
            added += 1
        except WebDriverException as err:
            _reraise_if_dead(err)
            # A leading-dot domain Chrome won't take for this exact origin —
            # retry letting it infer the domain from the current page.
            entry.pop("domain", None)
            try:
                driver.add_cookie(entry)
                added += 1
            except WebDriverException:
                pass
    return added


def is_logged_in(driver) -> bool:
    """
    True when the browser is holding a usable portal session.

    Deliberately assertion-based rather than time-based: the login page's own
    fields disappearing plus either an app-only element or a session cookie is
    what actually distinguishes "signed in" from "still on the form".
    """
    try:
        state = driver.execute_script("""
            return {
                login: !!(document.querySelector('#username')
                          && document.querySelector('#password')),
                app:   !!(document.querySelector('#appointment')
                          || document.querySelector('a[href*="/appointments"]')
                          || document.querySelector('a[href*="logout"]')
                          || document.querySelector('a[href*="/user/"]'))
            };
        """)
    except WebDriverException as err:
        _reraise_if_dead(err)
        return False

    if not state or state.get("login"):
        return False
    return bool(state.get("app")) or bool(portal_session_cookies(driver))


def wait_for_login(driver, timeout=LOGIN_DETECT_TIMEOUT_SECONDS) -> bool:
    """
    Blocks until the portal session is live, detected rather than timed.

    The operator solves the reCAPTCHA and clicks Sign In; this notices when that
    worked. The GUI's Confirm button stays wired as a manual override for the
    case where the portal changes and the detection stops recognising it.
    """
    debug("Watching for the login to complete (no fixed delay — this polls)...")
    deadline = time.monotonic() + timeout
    next_note = time.monotonic()

    while time.monotonic() < deadline:
        if callable(LOGIN_OVERRIDE):
            try:
                if LOGIN_OVERRIDE():
                    debug("Login confirmed manually — continuing.")
                    return True
            except Exception:
                pass

        if is_logged_in(driver):
            debug("✓ Login detected — the portal session is live.")
            return True

        if time.monotonic() >= next_note:
            # Also the heartbeat the GUI's Stop button lands on.
            print("  [GATE] Waiting for you to finish signing in in Chrome...")
            next_note = time.monotonic() + 10
        time.sleep(0.5)

    return False


def restore_session(driver) -> bool:
    """
    Phase B: try to reach an authenticated state with no human involved.

    Two independent sources, tried together — the Chrome profile carries the
    session on its own, and the exported cookies cover the case where the
    profile has been reset. Returns False if neither works, which is the signal
    to fall back to the manual login gate.
    """
    debug("Checking for a saved portal session...")
    driver.get(TARGET_URL)
    install_network_probe(driver)

    if is_logged_in(driver):
        debug("✓ The saved Chrome profile is still signed in — skipping the login gate.")
        return True

    cookies = load_session_cookies()
    if not cookies:
        debug("No saved session found — a one-time manual login is needed.")
        return False

    added = inject_session_cookies(driver, cookies)
    debug(f"Injected {added} saved cookie(s); reloading to let the portal see them...")
    driver.get(TARGET_URL)
    install_network_probe(driver)

    if is_logged_in(driver):
        debug("✓ Saved cookies restored the session — skipping the login gate.")
        return True

    debug("The saved session has expired — a manual login is needed.")
    clear_session_cookies()
    return False


# ============================================================================
# SCAN CHECKPOINT — resume from the exact type + date after a restart
# ============================================================================
def scan_signature() -> str:
    """
    Fingerprints the scan configuration.

    A checkpoint is only meaningful against the settings it was taken under: if
    the date range, the type list, the weekday filter or the city has changed,
    the stored date *index* points at a different date and resuming from it
    would skip real dates. Mismatched checkpoints are discarded, not adapted.
    """
    parts = [
        USER_EMAIL, TARGET_CITY, SCAN_START_DATE_STR, SCAN_END_DATE_STR,
        "|".join(v for v, _ in APPOINTMENT_TYPES),
        "|".join(f"{k}:{','.join(str(d) for d in sorted(v))}"
                 for k, v in sorted(SCAN_WEEKDAYS.items())),
        # Individual and Group are different queries against the same dates, so
        # a checkpoint taken under one must not resume under the other.
        "|".join(f"{v}={booking_for_for_type(v)}" for v, _ in APPOINTMENT_TYPES),
        # Likewise a group of three and a group of four are different searches,
        # and so are two allocation methods over the same three people.
        (f"{group_member_count()}/{group_appointment_method()}"
         if group_is_configured() else "-"),
    ]
    return hashlib.sha1("~".join(parts).encode("utf-8")).hexdigest()[:16]


def save_checkpoint(round_number, type_value, type_label, date_index, date_str,
                    completed_dates, completed_types):
    """
    Records the date that is about to be searched — not the one just finished.

    That direction matters: whatever kills the run kills it *during* a search,
    and pointing the checkpoint at the in-flight date means the restart redoes
    it rather than assuming it came back empty.
    """
    _write_json_atomically(SCAN_STATE_FILE, {
        "version": 1,
        "signature": scan_signature(),
        "round": round_number,
        "last_appointment_type": type_value,
        "last_appointment_label": type_label,
        "last_date_index": date_index,
        "last_date_searched": date_str,
        "completed_dates_list": list(completed_dates),
        "completed_types": list(completed_types),
        "search_interval": round(_search_interval, 2),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    })


def load_checkpoint():
    """The saved scan position, or None if there isn't a usable one."""
    try:
        payload = json.loads(SCAN_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    if not isinstance(payload, dict) or payload.get("version") != 1:
        return None
    if payload.get("signature") != scan_signature():
        debug("A checkpoint exists but the scan settings have changed since — "
              "starting a clean round instead of resuming into the wrong dates.")
        clear_checkpoint()
        return None
    if not isinstance(payload.get("last_date_index"), int):
        return None
    return payload


def clear_checkpoint():
    """Drops the checkpoint. Called when a round finishes cleanly — a stale one
    would make the next fresh run resume into the middle of a finished sweep."""
    try:
        SCAN_STATE_FILE.unlink()
    except OSError:
        pass


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


def rate_limit_restart_wait(retry_after=None) -> float:
    """
    How long to stay off the wire before the post-429 restart reconnects.

    Escalates per consecutive rate-limit event: the first is the plain
    driver-cleanup pause, so the restart-and-resume completes in seconds as
    intended. If the portal 429s us again straight after that restart, it has
    told us the limit is not tied to the browser session, and the wait steps up
    to a genuine quiet period — the only thing measured to clear it.

    _rate_limit_cooldowns is zeroed by note_search_outcome() on the first clean
    search, so a one-off 429 never leaves us permanently slow.
    """
    global _rate_limit_cooldowns

    index = min(_rate_limit_cooldowns, len(RATE_LIMIT_RESTART_WAITS) - 1)
    seconds = float(RATE_LIMIT_RESTART_WAITS[index])

    if retry_after:
        try:
            seconds = max(seconds, float(retry_after))
            debug(f"Portal sent Retry-After: {retry_after}s — honouring it.")
        except (TypeError, ValueError):
            pass

    _rate_limit_cooldowns += 1
    return seconds


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


def select_booking_for(driver, type_value: str):
    """
    Sets "Booking as" (#bookingfor) for this appointment type.

    Called after #type, never before: changing the appointment type re-renders
    this block, so setting it first would be undone. Group additionally reveals
    #membersDiv and #appointmentmethodDiv and clones one applicant row per
    person, all of which #btn-search validates before it will put anything on
    the wire — _prepare_group_booking() below sets them up.

    Returns the value that ended up selected.
    """
    wanted = booking_for_for_type(type_value)
    label = BOOKING_FOR_LABELS[wanted]

    current = driver.execute_script(
        "var el = document.getElementById('bookingfor'); return el ? el.value : null;")
    if current is None:
        debug("⚠ #bookingfor is not on this page — leaving Booking as untouched.")
        return None

    if str(current) == wanted:
        debug(f"Booking as: {label} (already set).")
    else:
        debug(f"Setting Booking as: {label} (#bookingfor={wanted})...")
        human_select_dropdown_by_value(driver, "#bookingfor", wanted)
        random_pause(0.6, 1.2)

        applied = driver.execute_script(
            "var el = document.getElementById('bookingfor'); return el ? el.value : null;")
        if str(applied) != wanted:
            raise Exception(
                f"Could not set Booking as to {label}: #bookingfor reads {applied!r}")

    if wanted == BOOKING_FOR_GROUP:
        _prepare_group_booking(driver)
    return wanted


# Counts the applicant rows the user can actually see. Filtering on `.hidden`
# rather than on #secondTr by id is what the structure doc recommends — it keeps
# working if the app ever adds another hidden row.
GROUP_ROW_COUNT_JS = """
    return Array.prototype.filter.call(
        document.querySelectorAll('#groupBody tr'),
        function (tr) { return !tr.classList.contains('hidden'); }).length;
"""


def _prepare_group_booking(driver):
    """
    Brings the form into group mode and fills every extra applicant row.

    The order is the one in book-appointment-GROUP-structure.md §7 and it is not
    interchangeable: #members has to go first because setting it is what clones
    the rows, the clones render asynchronously so they have to be waited for,
    and only then can the rows be written to.
    """
    count = group_member_count()
    method = group_appointment_method()
    debug(f"Group booking: {count} people, allocation method {method} "
          f"({APPOINTMENT_METHOD_LABELS[method]}).")

    if not _set_group_select(driver, "members", str(count)):
        debug("⚠ #members is missing or does not offer that many people — the "
              "portal may not have switched into group mode. Leaving the "
              "applicant rows alone.")
        return

    rendered = _wait_for_group_rows(driver, count)
    if rendered < count:
        debug(f"⚠ Only {rendered} of {count} applicant rows rendered within "
              f"{GROUP_ROW_RENDER_SECONDS}s. Filling the ones that are there.")

    if not _set_group_select(driver, "appointmentmethod", method):
        debug("⚠ #appointmentmethod is missing — leaving the allocation method "
              "as the portal set it.")

    fill_group_member_rows(driver, count)
    report_group_row_gaps(driver)


def _set_group_select(driver, element_id: str, value: str) -> bool:
    """
    Sets one of the two group selects and confirms it took.

    Both are select2-backed, so the change has to reach jQuery or the row
    cloning and the reveal handlers never run — which is what
    human_select_dropdown_by_value already does. Returns False when the element
    or the option is absent, so the caller can say so rather than crash.
    """
    exists = driver.execute_script("""
        var el = document.getElementById(arguments[0]);
        if (!el) { return false; }
        var wanted = arguments[1];      // hoisted: `arguments` is not the
        return Array.prototype.some.call(el.options, function (o) {
            return o.value === wanted;  // outer one inside this callback
        });
    """, element_id, str(value))
    if not exists:
        return False

    current = driver.execute_script(
        "return document.getElementById(arguments[0]).value;", element_id)
    if str(current) == str(value):
        return True

    human_select_dropdown_by_value(driver, f"#{element_id}", str(value))
    random_pause(0.3, 0.6)

    applied = driver.execute_script(
        "return document.getElementById(arguments[0]).value;", element_id)
    if str(applied) != str(value):
        raise Exception(
            f"Could not set #{element_id} to {value} — it reads {applied!r}")
    return True


def _wait_for_group_rows(driver, expected: int, timeout=None) -> int:
    """Polls until the clones have rendered. Reading the rows straight after
    setting #members finds only the original two."""
    if timeout is None:
        timeout = GROUP_ROW_RENDER_SECONDS
    deadline = time.monotonic() + timeout
    while True:
        try:
            seen = driver.execute_script(GROUP_ROW_COUNT_JS) or 0
        except WebDriverException as err:
            _reraise_if_dead(err)
            seen = 0
        if seen >= expected or time.monotonic() >= deadline:
            return seen
        time.sleep(0.25)


# Writes members 2..N into their own rows. Everything is reached through
# [name="applicants[][…]"] scoped to a row: the clones reuse the template's ids
# (#ex_surname and friends), so an id lookup lands in the hidden #secondTr and
# the data goes nowhere the portal will submit.
FILL_GROUP_ROWS_JS = r"""
var people = arguments[0];
var report = [];

var rows = Array.prototype.filter.call(
    document.querySelectorAll('#groupBody tr'),
    function (tr) { return !tr.classList.contains('hidden'); });

function field(row, name) {
    return row.querySelector('[name="applicants[][' + name + ']"]');
}

function setText(el, value) {
    if (!el) { return 'missing'; }
    el.value = value;
    if (window.jQuery) { window.jQuery(el).val(value); }
    el.dispatchEvent(new Event('input',  { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return el.value === value ? 'ok' : 'rejected';
}

function setByValue(el, value) {
    if (!el) { return 'missing'; }
    var offered = Array.prototype.some.call(el.options, function (o) {
        return o.value === value;
    });
    if (!offered) { return 'no such option'; }
    el.value = value;
    el.dispatchEvent(new Event('change', { bubbles: true }));
    if (window.jQuery && window.jQuery(el).data('select2')) {
        window.jQuery(el).trigger('change');
    }
    return el.value === value ? 'ok' : 'rejected';
}

function setByText(el, text) {
    if (!el) { return 'missing'; }
    var wanted = String(text).trim().toUpperCase();
    var match = null;
    for (var i = 0; i < el.options.length; i++) {
        var label = el.options[i].text.trim().toUpperCase();
        if (label === wanted) { match = el.options[i]; break; }
        if (!match && label.indexOf(wanted) !== -1) { match = el.options[i]; }
    }
    if (!match) { return 'no such option'; }
    return setByValue(el, match.value);
}

for (var i = 0; i < people.length; i++) {
    // rows[0] is the primary applicant — fill_applicant_fields() owns that one.
    var row = rows[i + 1];
    var entry = { member: i + 2 };
    if (!row) { entry.row = 'missing'; report.push(entry); continue; }

    var person = people[i];
    entry.row         = row.id || '(unnamed)';
    entry.surname     = setText(field(row, 'surname'), person.surname);
    entry.firstname   = setText(field(row, 'firstname'), person.firstname);
    entry.passport    = setText(field(row, 'passportnumber'), person.passport);
    entry.dob         = setText(field(row, 'dateofbirth'), person.dob);
    entry.expiry      = setText(field(row, 'traveldocumentvaliduntil'), person.expiry);
    entry.gender      = setByValue(field(row, 'gender[id]'), person.gender);
    entry.nationality = setByText(field(row, 'nationality[id]'), person.nationality);
    report.push(entry);
}

// A datepicker panel can pop open on focus and swallow the next click.
if (window.jQuery && window.jQuery.datepicker) {
    try { window.jQuery.datepicker._hideDatepicker(); } catch (e) {}
}
return report;
"""


def _group_member_payload(member: dict) -> dict:
    """Normalises a configured member into the seven fields a row needs."""
    def text(key, fallback=""):
        return str(member.get(key) or "").strip() or fallback

    return {
        "surname":     text("surname"),
        "firstname":   text("firstname"),
        "passport":    text("passport"),
        "dob":         text("dob"),
        "expiry":      text("expiry"),
        "gender":      text("gender", APPLICANT_GENDER_VALUE),
        "nationality": text("nationality", APPLICANT_NATIONALITY_TEXT),
    }


def fill_group_member_rows(driver, count: int):
    """
    Writes members 2..count into the cloned rows.

    Row 0 is the primary applicant and is left to fill_applicant_fields, so a
    group of N needs N-1 configured people here. Nothing is logged but the
    per-field outcome — passport numbers and dates of birth do not belong in a
    transcript that gets shared when something goes wrong.
    """
    wanted = max(0, count - 1)
    people = [_group_member_payload(m) for m in GROUP_MEMBERS[:wanted]]

    if len(people) < wanted:
        debug(f"⚠ Group is set to {count} people but only {len(people) + 1} have "
              f"details configured. {wanted - len(people)} row(s) will stay blank "
              "and Search will refuse to run.")
    if not people:
        return

    report = driver.execute_script(FILL_GROUP_ROWS_JS, people) or []
    for entry in report:
        member = f"Member {entry.get('member')}"
        if entry.get("row") == "missing":
            debug(f"⚠ {member}: no row on the page to fill.")
            continue
        problems = [f"{key}: {state}" for key, state in entry.items()
                    if key not in ("member", "row") and state != "ok"]
        if problems:
            debug(f"⚠ {member} (row {entry['row']}) — {', '.join(problems)}")
        else:
            debug(f"{member} (row {entry['row']}): filled.")


# Reports which visible row still has an empty required cell. Named fields only,
# and it returns the field names rather than their contents.
GROUP_ROW_GAPS_JS = r"""
var rows = Array.prototype.filter.call(
    document.querySelectorAll('#groupBody tr'),
    function (tr) { return !tr.classList.contains('hidden'); });

var names = ['surname', 'firstname', 'dateofbirth', 'passportnumber',
             'traveldocumentvaliduntil', 'gender[id]', 'nationality[id]'];
var gaps = [];

for (var i = 0; i < rows.length; i++) {
    var blank = [];
    for (var n = 0; n < names.length; n++) {
        var el = rows[i].querySelector('[name="applicants[][' + names[n] + ']"]');
        if (!el) { blank.push(names[n] + ' (no field)'); continue; }
        if (!String(el.value || '').trim()) { blank.push(names[n]); }
    }
    if (blank.length) {
        gaps.push({ member: i + 1, row: rows[i].id || '(primary)', blank: blank });
    }
}
return { rows: rows.length, gaps: gaps };
"""


def report_group_row_gaps(driver) -> bool:
    """
    Logs any visible applicant row that is still incomplete.

    #btn-search validates every visible row client-side and shows "Please check
    the form fields again" without sending a request, which is indistinguishable
    from a slow portal. Naming the row and the field turns that dead end into
    something actionable. Returns True when every row is complete.
    """
    try:
        result = driver.execute_script(GROUP_ROW_GAPS_JS) or {}
    except WebDriverException as err:
        _reraise_if_dead(err)
        return True

    gaps = result.get("gaps") or []
    if not gaps:
        debug(f"All {result.get('rows', '?')} applicant row(s) are complete.")
        return True

    for gap in gaps:
        debug(f"⚠ Member {gap.get('member')} (row {gap.get('row')}) is missing: "
              f"{', '.join(gap.get('blank', []))}")
    debug("Search validates every visible row before it sends anything, so it "
          "will refuse until those are filled in.")
    return False


def select_appointment_type(driver, type_value: str, type_label: str):
    """
    Picks the appointment type, sets "Booking as", and handles the Travel
    Purpose dropdown the type can reveal. Split out of the scan so an in-place
    reload can restore it — a reload resets #type back to its default, and
    scanning on would silently query the wrong category.
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

    # Last, and never earlier: #type re-renders this block when it changes.
    select_booking_for(driver, type_value)


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


def scan_dates_for_type(driver, type_value: str, type_label: str,
                        resume=None, round_number=0, completed_types=()) -> bool:
    """
    For a given appointment type, scans today + next DAYS_TO_SCAN days for available slots.
    Returns True if slots were found (and stops), False to continue to next type.

    `resume` is a checkpoint from an interrupted run. When it belongs to this
    appointment type the sweep restarts at the exact date index it recorded, so
    a browser restart costs nothing but the time it took — no date is re-searched
    unnecessarily and, more importantly, none is skipped.
    """
    print("\n" + "=" * 60)
    print(f"[SCANNING] Appointment Type: {type_label}")
    print(f"[SCANNING] Type value: {type_value}")
    print(f"[SCANNING] Booking as: {BOOKING_FOR_LABELS[booking_for_for_type(type_value)]}")
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
    checked = []            # dates this type has genuinely had an answer for

    # Pick the sweep back up where the last run was cut off, but only if the
    # checkpoint was taken on this same appointment type.
    if resume and resume.get("last_appointment_type") == type_value:
        resume_index = resume.get("last_date_index", 0)
        checked = [d for d in resume.get("completed_dates_list", []) if isinstance(d, str)]
        if 0 <= resume_index < len(dates):
            day_index = resume_index
            debug(f"▶ Resuming this type at day {day_index + 1}/{days_to_scan} "
                  f"({resume.get('last_date_searched')}) — "
                  f"{len(checked)} date(s) already checked before the interruption.")
        else:
            debug("▶ Checkpoint index is outside the current range — scanning this type from the start.")

    while day_index < len(dates):
        target_date = dates[day_index]
        date_str = target_date.strftime("%d/%m/%Y")

        # Written before the search, not after: whatever interrupts us
        # interrupts an in-flight search, and the restart must redo that date
        # rather than assume it came back empty.
        save_checkpoint(round_number, type_value, type_label, day_index, date_str,
                        checked, completed_types)

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
            status = read_network_state(driver).get("lastStatus")
            note_search_outcome(status)

            # A 429 that lands inside the wait window is still a refusal, not an
            # answer — the panel simply didn't change. Same handling as below.
            if status == RATE_LIMITED_STATUS:
                raise RateLimitRestart(
                    f"HTTP {RATE_LIMITED_STATUS} on {date_str} ({type_label})",
                    retry_after=read_network_state(driver).get("lastRetryAfter"))
        else:
            net = read_network_state(driver)
            status = net.get("lastStatus")
            note_search_outcome(status)

            # A 429 is a refusal to answer, not an answer of "no appointments".
            # Recording it as no-availability would hide a real slot, so the
            # date stays uncommitted, the checkpoint above still points at it,
            # and the restart handler in main() picks it back up from there.
            if status == RATE_LIMITED_STATUS:
                debug(f"⛔ Portal refused to answer for {date_str} (HTTP {status}) — "
                      f"this date has NOT been checked and will be retried after the restart.")
                raise RateLimitRestart(
                    f"HTTP {RATE_LIMITED_STATUS} on {date_str} ({type_label})",
                    retry_after=net.get("lastRetryAfter"))

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
        if date_str not in checked:
            checked.append(date_str)

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
            # In group mode this is almost always an incomplete applicant row,
            # and the modal itself does not say which one. The DOM still holds
            # the answer, so read it out before clearing the modal away.
            if booking_for_for_type(type_value) == BOOKING_FOR_GROUP:
                report_group_row_gaps(driver)
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
    
    target_city = vac_key(TARGET_CITY)
    target_id = VAC_IDS[target_city]
    target_label = VAC_LABELS[target_city]

    # 1. Fast pre-check: Read current VAC from the sidebar display string
    #    ("===najeeb21===  VAC:[Lahore]") — present on every authenticated page.
    #    Matched on the key, not the label: "Verification Office" renders in the
    #    sidebar as its full name and startswith("verification") still holds.
    try:
        sidebar_text = driver.find_element(By.TAG_NAME, "body").text
        match = re.search(r"VAC:\s*\[([^\]]+)\]", sidebar_text)
        if match and match.group(1).strip().lower().startswith(target_city):
            debug(f"VAC already set to {target_label} (sidebar text match). Skipping sync.")
            return False
    except Exception:
        pass # Fallback to profile page

    debug(f"Checking VAC from profile page to ensure it's {target_label}...")
    
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
        debug(f"VAC already set to {target_label} (value={target_id}). Skipping sync.")
        return False

    debug(f"VAC drift detected: current={current_vac}, target={target_id} "
          f"({target_label}). Updating profile...")
    
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
    
    # Case-insensitive: the option text is the centre's full name ("Islamabad
    # Visa Application Center for Greece", "Verification Office"), so the label
    # is a substring of it rather than an exact match.
    if native_val != target_id or target_label.lower() not in (widget_text or "").lower():
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

    debug(f"VAC successfully synced to {target_label} (value={target_id}).")
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
    expected = vac_id_for(TARGET_CITY)

    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#vac")))
    loaded_vac = driver.execute_script("return document.getElementById('vac').value;")
    if loaded_vac != expected:
        raise Exception(f"FATAL: Appointment form loaded with VAC {loaded_vac}, expected {expected}")
    debug(f"VAC gate passed — appointment form is querying "
          f"{vac_label(TARGET_CITY)} ({expected}).")


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
def start_chrome():
    """
    Starts Chrome against the persistent profile directory.

    The persistent profile is what makes an automatic restart viable: cookies,
    local storage and the portal's own device trust all survive, so a rebuilt
    browser lands back on an authenticated session instead of a reCAPTCHA.
    """
    debug("Launching Chrome Browser via Selenium...")
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    # Guarantee a real viewport even when the window is hidden off-screen
    options.add_argument("--window-size=1920,1080")

    # Persist the whole browser profile between runs and across restarts.
    try:
        CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        options.add_argument(f"--user-data-dir={CHROME_PROFILE_DIR}")
        options.add_argument("--profile-directory=Default")
        debug(f"Using saved Chrome profile: {CHROME_PROFILE_DIR}")
    except OSError as err:
        debug(f"⚠ Could not use a persistent Chrome profile ({err}) — "
              f"this run will need a manual login.")

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

    # A just-closed Chrome can still be holding the profile lock. That is
    # expected on a restart, not an error — wait for it rather than failing.
    last_error = None
    for attempt in (1, 2, 3):
        try:
            driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()),
                options=options
            )
            break
        except WebDriverException as err:
            last_error = err
            if "user data directory is already in use" not in str(err).lower():
                raise
            debug(f"The Chrome profile is still locked by the closing browser "
                  f"(attempt {attempt}/3) — waiting for it to let go...")
            interruptible_sleep(DRIVER_CLEANUP_SECONDS, "Waiting for the Chrome profile")
    else:
        raise last_error

    # Remove webdriver flag to reduce detection
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"}
    )
    return driver


def perform_manual_login(driver):
    """
    Phase A: the one-time gate. The operator signs in by hand; we detect when
    that has worked and export the session so it never has to happen again.
    """
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
    print("[ACTION REQUIRED] MANUAL INTERVENTION GATE (one time only):")
    print("1. Solve any reCAPTCHA image puzzles if presented.")
    print("2. Click 'Sign In' / Login button.")
    print("The scanner detects the successful login on its own — the session is")
    print("then saved, so later launches and restarts skip this gate entirely.")
    print("=" * 60 + "\n")

    if not wait_for_login(driver):
        raise Exception(
            f"No login detected within {LOGIN_DETECT_TIMEOUT_SECONDS}s. "
            f"Last page state: {describe_page(driver)}")

    debug(f"Waiting {DASHBOARD_STABILIZE_SECONDS} seconds for dashboard to stabilize...")
    time.sleep(DASHBOARD_STABILIZE_SECONDS)

    # Persist it immediately: this is the only moment we are certain the session
    # is good, and it is what every later launch will restore from.
    save_session_cookies(driver)


def launch_browser_and_login():
    """
    Brings up an authenticated browser sitting on a filled appointment form.

    Phase B first — a saved profile/cookies get us straight in with nobody
    watching, which is what lets a rate-limit restart be fully automatic.
    Phase A (the manual gate) only runs when there is no usable session.
    Returns (driver, wait), or raises on failure.
    """
    global _NATIVE_CLICK_WORKS, _consecutive_timeouts, _stall_recoveries
    _NATIVE_CLICK_WORKS = True   # fresh session — probe native clicks once more
    _consecutive_timeouts = 0
    _stall_recoveries = 0
    # Pacing state is deliberately NOT reset here: what the portal will tolerate
    # is a property of the portal, not of this browser process, and throwing the
    # learned interval away on every restart would walk straight back into 429.

    driver = start_chrome()
    unattended = restore_session(driver)

    if unattended:
        # Nobody had to touch it, so nothing showed the Chrome window to the
        # operator — tell the GUI it can go back to running hidden.
        if callable(ON_SESSION_READY):
            try:
                ON_SESSION_READY()
            except Exception:
                pass
        save_session_cookies(driver)     # refresh the copy on disk
    else:
        debug("Browser opened. Pausing like a human looking at the screen...")
        random_pause(3.0, 5.0)
        perform_manual_login(driver)

    # Sync VAC before booking
    ensure_vac(driver)

    # ALWAYS a fresh navigation — the page caches the VAC at load time
    open_appointment_page(driver)

    # Hard gate assert VAC
    assert_vac_on_appointment_page(driver)

    random_pause(1.5, 2.5)
    human_mouse_move(driver)

    fill_applicant_fields(driver)

    return driver, WebDriverWait(driver, 30)


def shutdown_browser(driver):
    """Closes Chrome. Used by the restart path, where the teardown has to happen
    before the wait rather than after it."""
    if driver is None:
        return
    try:
        driver.quit()
        debug("Chrome closed.")
    except Exception:
        pass


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
def resume_types(types_to_scan, pending):
    """
    Trims a round's type list down to what the interrupted run still owes.

    Types already finished this round are dropped, and the list is rotated so it
    starts on the type that was interrupted — so a restart never re-sweeps
    completed categories and never resumes into the wrong one.
    """
    if not pending:
        return types_to_scan

    done = set(pending.get("completed_types", []))
    remaining = [t for t in types_to_scan if t[0] not in done]

    resume_at = pending.get("last_appointment_type")
    if resume_at and any(t[0] == resume_at for t in remaining):
        while remaining[0][0] != resume_at:
            remaining.append(remaining.pop(0))
    return remaining


def main():
    global _search_interval, _last_search_at, _clean_searches, _rate_limit_cooldowns

    print("=" * 60)
    print("  GVCW VISA APPOINTMENT SLOT SCANNER")
    print("  Selenium Undetected Chrome Runtime")
    print("  ♾️  CONTINUOUS MODE — will scan forever until you stop it")
    print("=" * 60)
    if _session_log_path is not None:
        print(f"  Session log: {_session_log_path}")
    print(f"  VAC: {vac_label(TARGET_CITY)} ({vac_id_for(TARGET_CITY)})")
    for value, label in APPOINTMENT_TYPES:
        print(f"    · {label} — booking as "
              f"{BOOKING_FOR_LABELS[booking_for_for_type(value)]}")
    if group_is_configured():
        count = group_member_count()
        method = group_appointment_method()
        print(f"  Group: {count} people, {APPOINTMENT_METHOD_LABELS[method]} "
              f"(#appointmentmethod={method})")
        print(f"    · Member 1 — the primary applicant above")
        for index, member in enumerate(GROUP_MEMBERS[:count - 1], start=2):
            name = " ".join(part for part in
                            (str(member.get("firstname") or "").strip(),
                             str(member.get("surname") or "").strip()) if part)
            print(f"    · Member {index} — {name or '(no name configured)'}")
        if len(GROUP_MEMBERS) < count - 1:
            print(f"    ⚠ {count - 1 - len(GROUP_MEMBERS)} member(s) have no "
                  f"details — Search will refuse until they are filled in.")
    print("=" * 60)

    _search_interval = SEARCH_MIN_INTERVAL_SECONDS
    _last_search_at = 0.0
    _clean_searches = 0
    _rate_limit_cooldowns = 0

    round_number = 0
    # Survives browser restarts — this is what turns a teardown into a pause
    # rather than a lost round.
    pending = load_checkpoint()
    if pending:
        _search_interval = max(_search_interval,
                               float(pending.get("search_interval") or 0))
        print(f"  ▶ Resuming an interrupted scan: round {pending.get('round')}, "
              f"{pending.get('last_appointment_label')}, "
              f"{pending.get('last_date_searched')}")
        print(f"    Pacing restored to {_search_interval:.1f}s between searches.")

    # ── Outer loop: auto-recovers if the browser dies ──
    while True:
        driver = None
        try:
            driver, wait = launch_browser_and_login()

            rounds_since_refresh = 0

            # ── Inner loop: infinite scan rounds ──
            while True:
                # A resumed round keeps its original number so the log reads
                # continuously across the restart.
                round_number = (pending.get("round", round_number)
                                if pending else round_number + 1)

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

                # Everything this round has already finished, so a restart part
                # way through picks up the remainder rather than starting over.
                completed_types = list(pending.get("completed_types", [])) if pending else []
                types_to_scan = resume_types(types_to_scan, pending)

                for type_value, type_label in types_to_scan:
                    # Health-check before each type
                    if not is_browser_alive(driver):
                        raise Exception("Browser window was closed or crashed — restarting...")

                    result = scan_dates_for_type(
                        driver, type_value, type_label,
                        resume=pending, round_number=round_number,
                        completed_types=completed_types)
                    pending = None          # consumed — only the first type resumes
                    completed_types.append(type_value)
                    if result:
                        slots_found = True
                        break
                    debug("Moving to next appointment type...")
                    random_pause(1.0, 2.0)
                    human_mouse_move(driver)

                # The round is over: no half-finished position left to resume
                # into, so the checkpoint would only mislead the next start.
                pending = None
                clear_checkpoint()

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

        except RateLimitRestart as limited:
            # The checkpoint already points at the date the portal refused, so
            # the browser can go and come back without costing us that date.
            pending = load_checkpoint()
            shutdown_browser(driver)
            driver = None

            seconds = rate_limit_restart_wait(limited.retry_after)
            print("\n" + "⛔" * 30)
            print(f"  RATE LIMITED — {limited}")
            print(f"  Progress saved: round {round_number}, "
                  f"{pending.get('last_appointment_label') if pending else '?'}, "
                  f"{pending.get('last_date_searched') if pending else '?'}")
            print(f"  Chrome closed. Reconnecting in {int(seconds)}s and resuming")
            print(f"  from that exact date — nothing is recorded as 'no availability'.")
            print("⛔" * 30 + "\n")

            interruptible_sleep(seconds, "Rate-limit recovery")
            debug("Relaunching from the saved profile and picking the scan back up...")

        except Exception as e:
            print(f"\n[ERROR] {e}")
            import traceback
            traceback.print_exc()

            # A crash mid-round is worth resuming from too — same checkpoint.
            pending = load_checkpoint()
            if isinstance(e, SessionLostError):
                # The saved cookies are what just failed; keep them and the next
                # launch would silently retry a dead session.
                clear_session_cookies()
                print("  The portal session expired — you will be asked to sign in once more.")
            if pending:
                print(f"  Progress kept: round {pending.get('round')}, "
                      f"{pending.get('last_appointment_label')}, "
                      f"{pending.get('last_date_searched')}.")

            print("\n" + "=" * 60)
            print("  🔁 AUTO-RECOVERY: Will reopen the browser in 10 seconds...")
            print("=" * 60)
            interruptible_sleep(10, "Auto-recovery")

        finally:
            # Try to close the old browser if it's still around
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass


if __name__ == "__main__":
    # Terminal runs get the same transcript the GUI writes. The GUI opens its
    # own before importing anything, so this is a no-op under it.
    start_session_log(capture_streams=True)
    try:
        main()
    finally:
        close_session_log("closed on exit")
