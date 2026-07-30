"""
Local web frontend for gvcAutomation.py

Runs a small FastAPI server on localhost that collects the applicant/login
details, starts the Selenium scanner in a background thread, and streams the
scanner's output into the browser.

The automation script itself is NOT modified. Instead this module:
  * writes the form values onto gvcAutomation's module-level config globals,
  * shadows `print` inside that module so every log line is captured,
  * shadows `input` inside that module so the two manual gates
    ("solve the CAPTCHA + sign in", "slots found, press ENTER to resume")
    become buttons in the web UI instead of terminal prompts.

The CAPTCHA is still solved by hand in the Chrome window that Selenium opens.

Usage:
    python gvc_web.py                 # http://127.0.0.1:8000
    python gvc_web.py --port 8080     # custom port
    python gvc_web.py --no-browser    # don't auto-open the UI
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import gvcAutomation as bot

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
CONFIG_FILE = BASE_DIR / "gvc_ui_config.json"

MAX_LOG_LINES = 3000

# Snapshot the appointment types the script ships with — the UI offers these as
# checkboxes so the user can pick which ones to cycle through.
APPOINTMENT_TYPE_CHOICES = [
    {"value": value, "label": label} for value, label in bot.APPOINTMENT_TYPES
]

GENDER_CHOICES = [
    {"value": "1", "label": "Female"},
    {"value": "2", "label": "Male"},
    {"value": "3", "label": "Other"},
]

# States: idle | starting | running | awaiting_login | awaiting_resume | stopping | stopped | error
LIVE_STATES = {"starting", "running", "awaiting_login", "awaiting_resume", "stopping"}


# ============================================================================
# HELPERS
# ============================================================================
def normalize_date(raw: str, field: str) -> str:
    """Accepts yyyy-mm-dd (native date input) or dd/mm/yyyy, returns dd/mm/yyyy."""
    value = (raw or "").strip()
    iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
    if iso:
        year, month, day = iso.groups()
        return f"{int(day):02d}/{int(month):02d}/{year}"

    dmy = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", value)
    if dmy:
        day, month, year = dmy.groups()
        return f"{int(day):02d}/{int(month):02d}/{year}"

    raise ValueError(f"{field} must look like dd/mm/yyyy — got {value!r}")


class WebDriverProxy:
    """
    Stands in for gvcAutomation's `webdriver` module so we learn about the
    Chrome instance the moment it is created.

    Needed because gvcAutomation.main() only sees the driver once
    launch_browser_and_login() *returns*. If the run is stopped while the login
    gate is still waiting, main()'s `driver` local is still None, so its
    `finally: driver.quit()` never fires and Chrome would be orphaned.
    """

    def __init__(self, real_module: Any, on_create: Any) -> None:
        self._real = real_module
        self._on_create = on_create

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def Chrome(self, *args: Any, **kwargs: Any) -> Any:  # noqa: N802 - mirrors selenium's API
        driver = self._real.Chrome(*args, **kwargs)
        self._on_create(driver)
        return driver


def classify(line: str) -> str:
    """Tags a log line so the UI can colour it."""
    if "SLOTS AVAILABLE" in line or "✅" in line or "🟢" in line or "📲" in line:
        return "good"
    if "[ERROR]" in line or "🛑" in line or "Traceback" in line:
        return "error"
    if "⚠" in line or "✗" in line or "❌" in line or "🔁" in line:
        return "warn"
    if "[ACTION REQUIRED]" in line or "[GATE]" in line:
        return "gate"
    if line.startswith("  ══") or "SCAN ROUND" in line or "[SCANNING]" in line or "[STEP]" in line:
        return "head"
    return "info"


# ============================================================================
# RUNNER
# ============================================================================
class Runner:
    """Owns the background scanner thread and all shared state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._logs: list[dict[str, Any]] = []
        self._seq = 0
        self._state = "idle"
        self._detail = ""
        self._gate_prompt = ""
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._continue = threading.Event()
        self._slot_hits = 0
        self._started_at: float | None = None
        self._driver: Any = None

    # ---- logging -------------------------------------------------------
    def log(self, text: str, level: str | None = None) -> None:
        entry = {
            "i": 0,
            "t": datetime.now().strftime("%H:%M:%S"),
            "text": text,
            "level": level or classify(text),
        }
        with self._lock:
            self._seq += 1
            entry["i"] = self._seq
            self._logs.append(entry)
            if len(self._logs) > MAX_LOG_LINES:
                del self._logs[: len(self._logs) - MAX_LOG_LINES]
            if entry["level"] == "good" and "SLOTS AVAILABLE" in text:
                self._slot_hits += 1

    def _set_state(self, state: str, detail: str = "") -> None:
        with self._lock:
            self._state = state
            self._detail = detail

    # ---- public API ----------------------------------------------------
    def is_live(self) -> bool:
        with self._lock:
            return self._state in LIVE_STATES

    def status(self, since: int = 0) -> dict[str, Any]:
        with self._lock:
            lines = [e for e in self._logs if e["i"] > since]
            return {
                "state": self._state,
                "detail": self._detail,
                "gate_prompt": self._gate_prompt,
                "slot_hits": self._slot_hits,
                "uptime": (time.time() - self._started_at) if self._started_at else 0,
                "seq": self._seq,
                "lines": lines,
            }

    def start(self, cfg: "RunConfig") -> dict[str, Any]:
        with self._lock:
            if self._state in LIVE_STATES:
                return {"ok": False, "error": "A run is already in progress."}
            self._logs.clear()
            self._seq = 0
            self._slot_hits = 0
            self._gate_prompt = ""
            self._state = "starting"
            self._detail = ""
            self._started_at = time.time()

        self._stop.clear()
        self._continue.clear()

        self._thread = threading.Thread(target=self._run, args=(cfg,), daemon=True, name="gvc-scanner")
        self._thread.start()
        return {"ok": True}

    def proceed(self) -> dict[str, Any]:
        with self._lock:
            waiting = self._state in {"awaiting_login", "awaiting_resume"}
        if not waiting:
            return {"ok": False, "error": "The scanner is not waiting for you right now."}
        self._continue.set()
        return {"ok": True}

    def stop(self) -> dict[str, Any]:
        if not self.is_live():
            return {"ok": False, "error": "Nothing is running."}
        self.log("[GATE] Stop requested from the web UI — winding down…", level="warn")
        self._set_state("stopping", "Stop requested — closing the browser.")
        self._stop.set()
        self._continue.set()  # release the gate if it is blocking
        return {"ok": True}

    # ---- browser bookkeeping -------------------------------------------
    def _track_driver(self, driver: Any) -> None:
        with self._lock:
            self._driver = driver

    def _close_driver(self) -> None:
        """Belt-and-braces cleanup — safe to call even if main() already quit."""
        with self._lock:
            driver, self._driver = self._driver, None
        if driver is None:
            return
        try:
            driver.quit()
            self.log("Chrome window closed.", level="warn")
        except Exception:
            pass  # already gone

    # ---- injected into gvcAutomation -----------------------------------
    def _make_print(self):
        """Replacement for gvcAutomation's `print`: mirrors to stdout + captures."""

        def patched(*args: Any, sep: str = " ", end: str = "\n", file: Any = None, flush: bool = False) -> None:
            # Every log line is a chance to notice a stop request. Raising
            # KeyboardInterrupt unwinds into main()'s handler, which closes the
            # browser cleanly — the script's own Ctrl+C path.
            if self._stop.is_set():
                raise KeyboardInterrupt("stopped from web UI")

            text = sep.join(str(a) for a in args)
            try:
                sys.stdout.write(text + end)
                sys.stdout.flush()
            except Exception:
                pass
            for line in text.split("\n"):
                if line.strip():
                    self.log(line.rstrip())

        return patched

    def _make_input(self):
        """Replacement for gvcAutomation's `input`: blocks until the UI says go."""

        def patched(prompt: str = "") -> str:
            text = str(prompt).strip()
            is_login = "login" in text.lower() or not text
            state = "awaiting_login" if is_login else "awaiting_resume"
            message = text or "Solve the CAPTCHA and sign in, then click Continue."

            with self._lock:
                self._gate_prompt = message
                self._state = state
                self._detail = message
            self.log(f"[GATE] Waiting for you: {message}", level="gate")

            while True:
                if self._stop.is_set():
                    raise KeyboardInterrupt("stopped from web UI")
                if self._continue.wait(timeout=0.25):
                    self._continue.clear()
                    break

            if self._stop.is_set():
                raise KeyboardInterrupt("stopped from web UI")

            with self._lock:
                self._gate_prompt = ""
                self._state = "running"
                self._detail = ""
            self.log("[GATE] Continue received — resuming automation.", level="gate")
            return ""

        return patched

    def _apply_config(self, cfg: "RunConfig") -> None:
        bot.USER_EMAIL = cfg.username
        bot.USER_PASS = cfg.password
        bot.APPLICANT_DOB = cfg.dob
        bot.APPLICANT_PASSPORT = cfg.passport_number
        bot.APPLICANT_PASSPORT_EXPIRY = cfg.passport_expiry
        bot.APPLICANT_GENDER_VALUE = cfg.gender
        bot.APPLICANT_NATIONALITY_TEXT = cfg.nationality
        bot.APPOINTMENT_TYPES = [(t["value"], t["label"]) for t in cfg.appointment_types]
        bot.DAYS_TO_SCAN = cfg.days_to_scan

    # ---- thread body ---------------------------------------------------
    def _run(self, cfg: "RunConfig") -> None:
        original_print = getattr(bot, "print", None)
        original_input = getattr(bot, "input", None)
        original_webdriver = bot.webdriver

        try:
            self._apply_config(cfg)
            bot.print = self._make_print()
            bot.input = self._make_input()
            bot.webdriver = WebDriverProxy(original_webdriver, self._track_driver)

            self.log("Launching Chrome via Selenium — a separate browser window will open.", level="head")
            self.log(f"Scanning {len(cfg.appointment_types)} appointment type(s) × {cfg.days_to_scan} day(s) per round.")
            self._set_state("running")

            bot.main()  # loops until KeyboardInterrupt (i.e. our stop request)

            self._set_state("stopped", "Scanner finished.")
            self.log("Scanner stopped.", level="warn")

        except KeyboardInterrupt:
            self._set_state("stopped", "Stopped by user.")
            self.log("Scanner stopped by user.", level="warn")
        except BaseException as exc:  # noqa: BLE001 - surface anything to the UI
            self._set_state("error", f"{type(exc).__name__}: {exc}")
            self.log(f"[ERROR] {type(exc).__name__}: {exc}", level="error")
        finally:
            # main() closes the browser on its own way out, but not when the run
            # was stopped before launch_browser_and_login() returned.
            self._close_driver()

            # Restore the module namespace so a later run re-patches cleanly.
            bot.webdriver = original_webdriver
            if original_print is None:
                bot.__dict__.pop("print", None)
            else:
                bot.print = original_print
            if original_input is None:
                bot.__dict__.pop("input", None)
            else:
                bot.input = original_input

            self._stop.clear()
            self._continue.clear()
            with self._lock:
                self._gate_prompt = ""
                if self._state in LIVE_STATES:
                    self._state = "stopped"


runner = Runner()


# ============================================================================
# REQUEST MODELS
# ============================================================================
class RunConfig(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    dob: str
    passport_number: str = Field(min_length=1)
    passport_expiry: str
    gender: str = "2"
    nationality: str = "PAKISTAN"
    appointment_types: list[dict[str, str]] = Field(default_factory=list)
    days_to_scan: int = 4


class StartRequest(BaseModel):
    username: str = ""
    password: str = ""
    dob: str = ""
    passport_number: str = ""
    passport_expiry: str = ""
    gender: str = "2"
    nationality: str = "PAKISTAN"
    appointment_types: list[str] = Field(default_factory=list)
    days_to_scan: int = 4
    remember: bool = True

    def to_config(self) -> RunConfig:
        errors: list[str] = []

        if not self.username.strip():
            errors.append("Portal username is required.")
        if not self.password:
            errors.append("Portal password is required.")
        if not self.passport_number.strip():
            errors.append("Passport number is required.")
        if not self.nationality.strip():
            errors.append("Nationality is required.")

        dob = expiry = ""
        try:
            dob = normalize_date(self.dob, "Date of birth")
        except ValueError as exc:
            errors.append(str(exc))
        try:
            expiry = normalize_date(self.passport_expiry, "Passport expiry")
        except ValueError as exc:
            errors.append(str(exc))

        if self.gender not in {g["value"] for g in GENDER_CHOICES}:
            errors.append("Gender selection is invalid.")

        if not 1 <= self.days_to_scan <= 30:
            errors.append("Days to scan must be between 1 and 30.")

        known = {t["value"]: t["label"] for t in APPOINTMENT_TYPE_CHOICES}
        chosen = [v for v in self.appointment_types if v in known]
        if not chosen:
            errors.append("Pick at least one appointment type.")

        if errors:
            raise ValueError(" ".join(errors))

        # Preserve the script's original cycle order rather than click order.
        ordered = [
            {"value": t["value"], "label": t["label"]}
            for t in APPOINTMENT_TYPE_CHOICES
            if t["value"] in set(chosen)
        ]

        return RunConfig(
            username=self.username.strip(),
            password=self.password,
            dob=dob,
            passport_number=self.passport_number.strip(),
            passport_expiry=expiry,
            gender=self.gender,
            nationality=self.nationality.strip().upper(),
            appointment_types=ordered,
            days_to_scan=self.days_to_scan,
        )


# ============================================================================
# SAVED SETTINGS (password is deliberately never written to disk)
# ============================================================================
def load_saved() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(req: StartRequest) -> None:
    payload = req.model_dump(exclude={"password", "remember"})
    try:
        CONFIG_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


# ============================================================================
# APP
# ============================================================================
app = FastAPI(title="GVC Appointment Scanner", docs_url=None, redoc_url=None)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/bootstrap")
def bootstrap() -> JSONResponse:
    return JSONResponse(
        {
            "appointment_types": APPOINTMENT_TYPE_CHOICES,
            "genders": GENDER_CHOICES,
            "target_url": bot.TARGET_URL,
            "defaults": {
                "username": bot.USER_EMAIL,
                "dob": bot.APPLICANT_DOB,
                "passport_number": bot.APPLICANT_PASSPORT,
                "passport_expiry": bot.APPLICANT_PASSPORT_EXPIRY,
                "gender": bot.APPLICANT_GENDER_VALUE,
                "nationality": bot.APPLICANT_NATIONALITY_TEXT,
                "days_to_scan": bot.DAYS_TO_SCAN,
                "appointment_types": [t["value"] for t in APPOINTMENT_TYPE_CHOICES],
            },
            "saved": load_saved(),
        }
    )


@app.get("/api/status")
def status(since: int = 0) -> JSONResponse:
    return JSONResponse(runner.status(since))


@app.post("/api/start")
def start(req: StartRequest) -> JSONResponse:
    try:
        cfg = req.to_config()
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    result = runner.start(cfg)
    if not result.get("ok"):
        return JSONResponse(result, status_code=409)

    if req.remember:
        save_settings(req)
    return JSONResponse({"ok": True})


@app.post("/api/continue")
def proceed() -> JSONResponse:
    result = runner.proceed()
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


@app.post("/api/stop")
def stop() -> JSONResponse:
    result = runner.stop()
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local web UI for the GVC appointment scanner.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open the UI.")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    url = f"http://{args.host}:{args.port}"
    print("=" * 60)
    print("  GVC APPOINTMENT SCANNER — LOCAL WEB UI")
    print(f"  Open: {url}")
    print("  The Chrome window for the CAPTCHA opens separately when you")
    print("  press Start. Keep this terminal open while it runs.")
    print("=" * 60)

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
