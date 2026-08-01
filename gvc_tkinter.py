import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import json
from pathlib import Path
import traceback
from datetime import datetime, timedelta
import re

import gvcAutomation as bot
import win_hide

FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    PERSIST_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
    PERSIST_DIR = BASE_DIR

CONFIG_FILE = PERSIST_DIR / "gvc_ui_config.json"
LOG_FILE = PERSIST_DIR / "gvc_app.log"
MAX_LOG_LINES = 3000

# Setup basic tee logging if frozen
class _Tee:
    def __init__(self, *streams):
        self._streams = [s for s in streams if s is not None]

    def write(self, data: str):
        for s in self._streams:
            try:
                s.write(data)
            except:
                pass
        return len(data)

    def flush(self):
        for s in self._streams:
            try:
                s.flush()
            except:
                pass

    def isatty(self):
        return False

if FROZEN:
    try:
        log = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    except OSError:
        class _NullWriter:
            def write(self, *a, **k): return 0
            def flush(self): pass
        log = _NullWriter()
    sys.stdout = _Tee(sys.stdout, log)
    sys.stderr = _Tee(sys.stderr, log)
    print(f"\n{'=' * 60}\n[{datetime.now().isoformat(timespec='seconds')}] App started")


# APPOINTMENT_TYPE_CHOICES is now generated dynamically.

GENDER_CHOICES = [
    {"value": "1", "label": "Female"},
    {"value": "2", "label": "Male"},
    {"value": "3", "label": "Other"},
]

def normalize_date(raw: str, field: str) -> str:
    value = (raw or "").strip()
    iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
    if iso:
        year, month, day = iso.groups()
        return f"{int(day):02d}/{int(month):02d}/{year}"
    dmy = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", value)
    if dmy:
        day, month, year = dmy.groups()
        return f"{int(day):02d}/{int(month):02d}/{year}"
    raise ValueError(f"{field} must look like dd/mm/yyyy - got {value!r}")


class WebDriverProxy:
    def __init__(self, real_module, on_create):
        self._real = real_module
        self._on_create = on_create

    def __getattr__(self, name):
        return getattr(self._real, name)

    def Chrome(self, *args, **kwargs):
        driver = self._real.Chrome(*args, **kwargs)
        self._on_create(driver)
        return driver


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GVC Appointment Scanner")
        self.geometry("1000x750")
        self.configure(padx=10, pady=10)
        
        self._thread = None
        self._stop = threading.Event()
        self._continue = threading.Event()
        self._driver = None
        self._hwnd = None
        self._lock = threading.Lock()
        
        self.build_ui()
        self.load_settings()

    def build_ui(self):
        # Top Header
        header = ttk.Label(self, text="GVC Appointment Scanner", font=("Arial", 16, "bold"))
        header.pack(side=tk.TOP, pady=(0, 10))

        # Main PanedWindow for resizable split
        self.paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        self.paned.pack(fill=tk.BOTH, expand=True)

        # Left Panel (Settings)
        left_frame = ttk.Frame(self.paned, padding=10, relief="solid", borderwidth=1)
        self.paned.add(left_frame, weight=1)
        
        # Right Panel (Logs)
        right_frame = ttk.Frame(self.paned, padding=10, relief="solid", borderwidth=1)
        self.paned.add(right_frame, weight=3)
        
        row = 0
        
        # Row 0
        ttk.Label(left_frame, text="Username:", font=("Arial", 9, "bold")).grid(row=row, column=0, sticky="w", pady=(5, 2), padx=2)
        ttk.Label(left_frame, text="Password:", font=("Arial", 9, "bold")).grid(row=row, column=1, sticky="w", pady=(5, 2), padx=2)
        row += 1
        self.username_var = tk.StringVar()
        ttk.Entry(left_frame, textvariable=self.username_var, width=22).grid(row=row, column=0, pady=2, padx=2, sticky="w")
        self.password_var = tk.StringVar()
        ttk.Entry(left_frame, textvariable=self.password_var, show="*", width=22).grid(row=row, column=1, pady=2, padx=2, sticky="w")
        row += 1
        # Row 1
        ttk.Label(left_frame, text="First Name:", font=("Arial", 9, "bold")).grid(row=row, column=0, sticky="w", pady=(5, 2), padx=2)
        ttk.Label(left_frame, text="Surname:", font=("Arial", 9, "bold")).grid(row=row, column=1, sticky="w", pady=(5, 2), padx=2)
        row += 1
        self.first_name_var = tk.StringVar()
        ttk.Entry(left_frame, textvariable=self.first_name_var, width=22).grid(row=row, column=0, pady=2, padx=2, sticky="w")
        self.surname_var = tk.StringVar()
        ttk.Entry(left_frame, textvariable=self.surname_var, width=22).grid(row=row, column=1, pady=2, padx=2, sticky="w")
        row += 1
        
        # Row 2
        ttk.Label(left_frame, text="DOB (dd/mm/yyyy):", font=("Arial", 9, "bold")).grid(row=row, column=0, sticky="w", pady=(5, 2), padx=2)
        ttk.Label(left_frame, text="Passport Number:", font=("Arial", 9, "bold")).grid(row=row, column=1, sticky="w", pady=(5, 2), padx=2)
        row += 1
        self.dob_var = tk.StringVar()
        ttk.Entry(left_frame, textvariable=self.dob_var, width=22).grid(row=row, column=0, pady=2, padx=2, sticky="w")
        self.passport_var = tk.StringVar()
        ttk.Entry(left_frame, textvariable=self.passport_var, width=22).grid(row=row, column=1, pady=2, padx=2, sticky="w")
        row += 1
        
        # Row 2
        ttk.Label(left_frame, text="Passport Expiry:", font=("Arial", 9, "bold")).grid(row=row, column=0, sticky="w", pady=(5, 2), padx=2)
        ttk.Label(left_frame, text="Gender:", font=("Arial", 9, "bold")).grid(row=row, column=1, sticky="w", pady=(5, 2), padx=2)
        row += 1
        self.expiry_var = tk.StringVar()
        ttk.Entry(left_frame, textvariable=self.expiry_var, width=22).grid(row=row, column=0, pady=2, padx=2, sticky="w")
        self.gender_var = tk.StringVar()
        gender_cb = ttk.Combobox(left_frame, textvariable=self.gender_var, values=[g["label"] for g in GENDER_CHOICES], state="readonly", width=19)
        gender_cb.grid(row=row, column=1, pady=2, padx=2, sticky="w")
        row += 1
        
        # Row 3
        ttk.Label(left_frame, text="Nationality:", font=("Arial", 9, "bold")).grid(row=row, column=0, sticky="w", pady=(5, 2), padx=2)
        ttk.Label(left_frame, text="City (VAC):", font=("Arial", 9, "bold")).grid(row=row, column=1, sticky="w", pady=(5, 2), padx=2)
        row += 1
        self.nationality_var = tk.StringVar()
        ttk.Entry(left_frame, textvariable=self.nationality_var, width=22).grid(row=row, column=0, pady=2, padx=2, sticky="w")
        self.city_var = tk.StringVar(value="islamabad")
        city_cb = ttk.Combobox(left_frame, textvariable=self.city_var, values=["islamabad", "lahore"], state="readonly", width=19)
        city_cb.grid(row=row, column=1, pady=2, padx=2, sticky="w")
        self.city_var.trace_add("write", self.update_appointment_types_ui)
        row += 1
        
        # Row 4
        ttk.Label(left_frame, text="Scan Start Date:", font=("Arial", 9, "bold")).grid(row=row, column=0, sticky="w", pady=(5, 2), padx=2)
        ttk.Label(left_frame, text="Scan End Date:", font=("Arial", 9, "bold")).grid(row=row, column=1, sticky="w", pady=(5, 2), padx=2)
        row += 1
        self.start_date_var = tk.StringVar(value=datetime.now().strftime("%d/%m/%Y"))
        ttk.Entry(left_frame, textvariable=self.start_date_var, width=22).grid(row=row, column=0, pady=2, padx=2, sticky="w")
        self.end_date_var = tk.StringVar(value=(datetime.now() + timedelta(days=4)).strftime("%d/%m/%Y"))
        ttk.Entry(left_frame, textvariable=self.end_date_var, width=22).grid(row=row, column=1, pady=2, padx=2, sticky="w")
        row += 1
        
        # Appointment Types (span across)
        ttk.Label(left_frame, text="Appointment Types:", font=("Arial", 9, "bold")).grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 2), padx=2); row+=1
        
        self.types_frame = ttk.Frame(left_frame)
        self.types_frame.grid(row=row, column=0, columnspan=2, sticky="w")
        row+=1
        
        self.type_vars = {}
        self.current_appointment_choices = []
        
        ttk.Separator(left_frame, orient='horizontal').grid(row=row, column=0, columnspan=2, sticky="ew", pady=15); row+=1
        
        # Buttons
        buttons_frame = ttk.Frame(left_frame)
        buttons_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=2)
        buttons_frame.columnconfigure(0, weight=1)
        buttons_frame.columnconfigure(1, weight=1)
        
        self.start_btn = ttk.Button(buttons_frame, text="▶ Start Scanning", command=self.start_scan)
        self.start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 2))
        
        self.stop_btn = ttk.Button(buttons_frame, text="⏹ Stop", command=self.stop_scan, state="disabled")
        self.stop_btn.grid(row=0, column=1, sticky="ew", padx=(2, 0))
        
        row += 1
        
        ttk.Separator(left_frame, orient='horizontal').grid(row=row, column=0, columnspan=2, sticky="ew", pady=15); row+=1
        
        self.confirm_btn = ttk.Button(left_frame, text="🔓 Confirm Manual Action", command=self.confirm_gate, state="disabled")
        self.confirm_btn.grid(row=row, column=0, columnspan=2, sticky="ew", pady=2, padx=2, ipady=5); row+=1
        
        self.status_label = ttk.Label(left_frame, text="Status: Idle", foreground="blue", wraplength=280, font=("Arial", 10, "bold"))
        self.status_label.grid(row=row, column=0, columnspan=2, sticky="w", pady=15, padx=2); row+=1

        ttk.Label(right_frame, text="Console Output", font=("Arial", 11, "bold")).pack(anchor="w", pady=(0, 5))
        self.log_area = scrolledtext.ScrolledText(right_frame, wrap=tk.WORD, state="disabled", font=("Consolas", 10), bg="#1e1e1e", fg="#cccccc")
        self.log_area.pack(fill=tk.BOTH, expand=True)

    def update_appointment_types_ui(self, *args):
        for widget in self.types_frame.winfo_children():
            widget.destroy()
            
        city = self.city_var.get()
        types = bot.get_appointment_types(city)
        self.current_appointment_choices = [{"value": v, "label": l} for v, l in types]
        
        self.type_vars = {}
        for r, t in enumerate(self.current_appointment_choices):
            var = tk.BooleanVar(value=True)
            self.type_vars[t["value"]] = var
            cb = ttk.Checkbutton(self.types_frame, text=t["label"], variable=var)
            cb.grid(row=r, column=0, sticky="w", padx=10)

    def load_settings(self):
        if not CONFIG_FILE.exists():
            self.username_var.set(bot.USER_EMAIL)
            self.first_name_var.set(bot.APPLICANT_FIRST_NAME)
            self.surname_var.set(bot.APPLICANT_SURNAME)
            self.dob_var.set(bot.APPLICANT_DOB)
            self.passport_var.set(bot.APPLICANT_PASSPORT)
            self.expiry_var.set(bot.APPLICANT_PASSPORT_EXPIRY)
            self.gender_var.set([g["label"] for g in GENDER_CHOICES if g["value"] == bot.APPLICANT_GENDER_VALUE][0] if any(g["value"] == bot.APPLICANT_GENDER_VALUE for g in GENDER_CHOICES) else "Male")
            self.nationality_var.set(bot.APPLICANT_NATIONALITY_TEXT)
            self.city_var.set(bot.TARGET_CITY if hasattr(bot, "TARGET_CITY") else "islamabad")
            self.start_date_var.set(bot.SCAN_START_DATE_STR or datetime.now().strftime("%d/%m/%Y"))
            self.end_date_var.set(bot.SCAN_END_DATE_STR or (datetime.now() + timedelta(days=4)).strftime("%d/%m/%Y"))
            self.update_appointment_types_ui()
            return
            
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            self.username_var.set(data.get("username", bot.USER_EMAIL))
            self.first_name_var.set(data.get("first_name", bot.APPLICANT_FIRST_NAME))
            self.surname_var.set(data.get("surname", bot.APPLICANT_SURNAME))
            self.dob_var.set(data.get("dob", bot.APPLICANT_DOB))
            self.passport_var.set(data.get("passport_number", bot.APPLICANT_PASSPORT))
            self.expiry_var.set(data.get("passport_expiry", bot.APPLICANT_PASSPORT_EXPIRY))
            
            self.gender_var.set([g["label"] for g in GENDER_CHOICES if g["value"] == data.get("gender", bot.APPLICANT_GENDER_VALUE)][0] if any(g["value"] == data.get("gender", bot.APPLICANT_GENDER_VALUE) for g in GENDER_CHOICES) else "Male")
            self.nationality_var.set(data.get("nationality", bot.APPLICANT_NATIONALITY_TEXT))
            self.city_var.set(data.get("target_city", "islamabad"))
            self.start_date_var.set(data.get("scan_start_date", bot.SCAN_START_DATE_STR or datetime.now().strftime("%d/%m/%Y")))
            self.end_date_var.set(data.get("scan_end_date", bot.SCAN_END_DATE_STR or (datetime.now() + timedelta(days=4)).strftime("%d/%m/%Y")))
            
            self.update_appointment_types_ui()
            
            saved_types = data.get("appointment_types", [])
            saved_values = {t["value"] for t in saved_types}
            if saved_values:
                for t in self.current_appointment_choices:
                    self.type_vars[t["value"]].set(t["value"] in saved_values)
        except Exception as e:
            print("Failed to load config:", e)
            self.update_appointment_types_ui()

    def save_settings(self, cfg):
        try:
            payload = {
                "username": cfg["username"],
                "first_name": cfg["first_name"],
                "surname": cfg["surname"],
                "dob": cfg["dob"],
                "passport_number": cfg["passport_number"],
                "passport_expiry": cfg["passport_expiry"],
                "gender": cfg["gender"],
                "nationality": cfg["nationality"],
                "target_city": cfg["target_city"],
                "scan_start_date": cfg["scan_start_date"],
                "scan_end_date": cfg["scan_end_date"],
                "appointment_types": cfg["appointment_types"]
            }
            CONFIG_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    def log(self, msg):
        self.log_area.config(state="normal")
        self.log_area.insert(tk.END, msg + "\n")
        self.log_area.see(tk.END)
        self.log_area.config(state="disabled")

    def _track_driver(self, driver):
        with self._lock:
            self._driver = driver
            self._hwnd = None

        try:
            driver_pid = driver.service.process.pid
        except Exception:
            return
            
        hwnd = win_hide.find_chrome_hwnd(driver_pid, timeout=10.0)
        with self._lock:
            self._hwnd = hwnd
        if hwnd is None:
            self.after(0, self.log, "Could not locate the Chrome window handle - hide/show will be unavailable.")

    def _hide_window(self):
        with self._lock:
            hwnd = self._hwnd
        if hwnd is None: return
        try:
            win_hide.hide_window(hwnd)
            self.after(0, self.log, "Chrome window hidden - running in background.")
        except Exception as exc:
            self.after(0, self.log, f"Could not hide Chrome window: {exc}")

    def _show_window(self):
        with self._lock:
            hwnd = self._hwnd
        if hwnd is None: return
        try:
            win_hide.show_window(hwnd)
            self.after(0, self.log, "Chrome window restored to the foreground.")
        except Exception as exc:
            self.after(0, self.log, f"Could not restore Chrome window: {exc}")

    def start_scan(self):
        try:
            username = self.username_var.get().strip()
            password = self.password_var.get()
            if not username or not password:
                raise ValueError("Username and password are required.")
                
            first_name = self.first_name_var.get().strip()
            surname = self.surname_var.get().strip()
                
            passport = self.passport_var.get().strip()
            if not passport:
                raise ValueError("Passport number is required.")
                
            nationality = self.nationality_var.get().strip()
            if not nationality:
                raise ValueError("Nationality is required.")
                
            target_city = self.city_var.get().strip().lower()
            if not target_city:
                target_city = "islamabad"
                
            dob = normalize_date(self.dob_var.get(), "Date of birth")
            expiry = normalize_date(self.expiry_var.get(), "Passport expiry")
            
            gender_label = self.gender_var.get()
            gender_val = "2"
            for g in GENDER_CHOICES:
                if g["label"] == gender_label:
                    gender_val = g["value"]
                    break
                    
            start_date_str = normalize_date(self.start_date_var.get(), "Scan start date")
            end_date_str = normalize_date(self.end_date_var.get(), "Scan end date")
                
            chosen_types = []
            for t in self.current_appointment_choices:
                if self.type_vars[t["value"]].get():
                    chosen_types.append({"value": t["value"], "label": t["label"]})
                    
            if not chosen_types:
                raise ValueError("Pick at least one appointment type.")
                
            cfg = {
                "username": username,
                "password": password,
                "first_name": first_name,
                "surname": surname,
                "dob": dob,
                "passport_number": passport,
                "passport_expiry": expiry,
                "gender": gender_val,
                "nationality": nationality,
                "target_city": target_city,
                "scan_start_date": start_date_str,
                "scan_end_date": end_date_str,
                "appointment_types": chosen_types
            }
        except Exception as exc:
            messagebox.showerror("Validation Error", str(exc))
            return

        self.save_settings(cfg)
        self.log_area.config(state="normal")
        self.log_area.delete('1.0', tk.END)
        self.log_area.config(state="disabled")
        
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_label.config(text="Status: Starting...", foreground="blue")
        
        self._stop.clear()
        self._continue.clear()
        
        self._thread = threading.Thread(target=self._run_scan, args=(cfg,), daemon=True)
        self._thread.start()

    def _run_scan(self, cfg):
        original_print = getattr(bot, "print", None)
        original_input = getattr(bot, "input", None)
        original_webdriver = bot.webdriver

        def patched_print(*args, **kwargs):
            if self._stop.is_set():
                raise KeyboardInterrupt("stopped from UI")
            text = kwargs.get("sep", " ").join(str(a) for a in args)
            for line in text.split("\n"):
                if line.strip():
                    self.after(0, self.log, line.rstrip())

        def patched_input(prompt=""):
            text = str(prompt).strip()
            is_login = bool(text)
            
            def enable_confirm():
                self.confirm_btn.config(state="normal")
                self.status_label.config(
                    text="Status: Waiting for you to confirm login/booking in Chrome",
                    foreground="orange"
                )
            
            self.after(0, enable_confirm)
            
            if is_login:
                self.after(0, self.log, f"[GATE] Waiting for you: {text or 'Please confirm in Chrome'}")
            else:
                self.after(0, self.log, "[GATE] Slots found! Restoring Chrome window.")
                self._show_window()

            while True:
                if self._stop.is_set():
                    raise KeyboardInterrupt("stopped from UI")
                if self._continue.wait(timeout=0.25):
                    self._continue.clear()
                    break

            if self._stop.is_set():
                raise KeyboardInterrupt("stopped from UI")

            def disable_confirm():
                self.confirm_btn.config(state="disabled")
                self.status_label.config(text="Status: Running", foreground="green")
                
            self.after(0, disable_confirm)
            self.after(0, self.log, "[GATE] Continue received - resuming automation.")
            self._hide_window()
            return ""

        try:
            bot.USER_EMAIL = cfg["username"]
            bot.USER_PASS = cfg["password"]
            bot.APPLICANT_FIRST_NAME = cfg["first_name"]
            bot.APPLICANT_SURNAME = cfg["surname"]
            bot.APPLICANT_DOB = cfg["dob"]
            bot.APPLICANT_PASSPORT = cfg["passport_number"]
            bot.APPLICANT_PASSPORT_EXPIRY = cfg["passport_expiry"]
            bot.APPLICANT_GENDER_VALUE = cfg["gender"]
            bot.APPLICANT_NATIONALITY_TEXT = cfg["nationality"]
            bot.TARGET_CITY = cfg["target_city"]
            bot.SCAN_START_DATE_STR = cfg["scan_start_date"]
            bot.SCAN_END_DATE_STR = cfg["scan_end_date"]
            bot.APPOINTMENT_TYPES = [(t["value"], t["label"]) for t in cfg["appointment_types"]]

            bot.print = patched_print
            bot.input = patched_input
            bot.webdriver = WebDriverProxy(original_webdriver, self._track_driver)

            self.after(0, lambda: self.status_label.config(text="Status: Running", foreground="green"))
            bot.main()
            
        except KeyboardInterrupt:
            self.after(0, self.log, "Scanner stopped by user.")
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            self.after(0, self.log, f"[ERROR] {err}")
            traceback.print_exc()
        finally:
            with self._lock:
                driver, self._driver = self._driver, None
                self._hwnd = None
            if driver is not None:
                try:
                    driver.quit()
                    self.after(0, self.log, "Chrome window closed.")
                except:
                    pass

            bot.webdriver = original_webdriver
            if original_print is None:
                bot.__dict__.pop("print", None)
            else:
                bot.print = original_print
            if original_input is None:
                bot.__dict__.pop("input", None)
            else:
                bot.input = original_input

            self.after(0, self._on_scan_stopped)

    def _on_scan_stopped(self):
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.confirm_btn.config(state="disabled")
        self.status_label.config(text="Status: Stopped", foreground="black")

    def stop_scan(self):
        self._stop.set()
        self._continue.set()
        self.log("Stop requested... winding down.")
        self.status_label.config(text="Status: Stopping...", foreground="red")

    def confirm_gate(self):
        self._continue.set()

if __name__ == "__main__":
    app = App()
    app.mainloop()
