import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import json
import calendar
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


class CalendarPopup(tk.Toplevel):
    """
    A month-grid date picker for the scan range fields.

    Deliberately pure tkinter rather than tkcalendar: the app ships as a frozen
    PyInstaller bundle, and this avoids adding a third-party dependency that
    would have to be collected into it.
    """

    DAY_HEADS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]

    def __init__(self, parent, date_var):
        super().__init__(parent)
        self.date_var = date_var
        self.title("Pick a date")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        try:
            selected = datetime.strptime(normalize_date(date_var.get(), "date"), "%d/%m/%Y")
        except Exception:
            selected = datetime.now()
        self.year, self.month = selected.year, selected.month
        self.selected = selected

        header = ttk.Frame(self, padding=(6, 6, 6, 0))
        header.pack(fill=tk.X)
        ttk.Button(header, text="‹", width=3,
                   command=lambda: self.shift_month(-1)).pack(side=tk.LEFT)
        self.header_label = ttk.Label(header, anchor="center",
                                      font=("Arial", 10, "bold"), width=18)
        self.header_label.pack(side=tk.LEFT, expand=True)
        ttk.Button(header, text="›", width=3,
                   command=lambda: self.shift_month(1)).pack(side=tk.LEFT)

        self.grid_frame = ttk.Frame(self, padding=6)
        self.grid_frame.pack()

        footer = ttk.Frame(self, padding=(6, 0, 6, 6))
        footer.pack(fill=tk.X)
        ttk.Button(footer, text="Today", command=self.pick_today).pack(side=tk.LEFT)
        ttk.Button(footer, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)

        self.draw()
        self.update_idletasks()
        self.geometry(f"+{parent.winfo_rootx() + 60}+{parent.winfo_rooty() + 120}")
        self.bind("<Escape>", lambda _e: self.destroy())

    def shift_month(self, delta):
        month = self.month + delta
        year = self.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        self.year, self.month = year, month
        self.draw()

    def pick_today(self):
        self.choose(datetime.now())

    def choose(self, when: datetime):
        self.date_var.set(when.strftime("%d/%m/%Y"))
        self.destroy()

    def draw(self):
        for widget in self.grid_frame.winfo_children():
            widget.destroy()

        self.header_label.config(text=f"{calendar.month_name[self.month]} {self.year}")
        for col, name in enumerate(self.DAY_HEADS):
            ttk.Label(self.grid_frame, text=name, width=4, anchor="center",
                      font=("Arial", 8, "bold")).grid(row=0, column=col, padx=1, pady=(0, 2))

        today = datetime.now().date()
        for r, week in enumerate(calendar.Calendar(firstweekday=0).monthdayscalendar(
                self.year, self.month), start=1):
            for c, day in enumerate(week):
                if day == 0:
                    continue
                when = datetime(self.year, self.month, day)
                style = "TButton"
                if when.date() == today:
                    style = "Today.TButton"
                if when.date() == self.selected.date():
                    style = "Selected.TButton"
                ttk.Button(self.grid_frame, text=str(day), width=4, style=style,
                           command=lambda w=when: self.choose(w)).grid(
                    row=r, column=c, padx=1, pady=1)


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
    # Monday-first, matching datetime.weekday() so the index IS the weekday number
    DAY_TOGGLE_LABELS = ["M", "T", "W", "T", "F", "S", "S"]

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
        
        style = ttk.Style(self)
        style.configure("Today.TButton", foreground="#0a58ca", font=("Arial", 9, "bold"))
        style.configure("Selected.TButton", foreground="#ffffff", background="#0a58ca")
        style.map("Selected.TButton", background=[("active", "#0a58ca")])

        self.build_ui()
        self.load_settings()

    def build_ui(self):
        # Top Header
        header = ttk.Label(self, text="GVC Appointment Scanner", font=("Arial", 16, "bold"))
        header.pack(side=tk.TOP, pady=(0, 10))

        # Main PanedWindow for resizable split
        self.paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        self.paned.pack(fill=tk.BOTH, expand=True)

        # Left Panel (Settings). Scrollable: the per-type weekday rows push the
        # settings past the window height on a 720p screen, and without this the
        # Start button ends up below the bottom edge.
        left_outer = ttk.Frame(self.paned, relief="solid", borderwidth=1)
        self.paned.add(left_outer, weight=1)

        left_canvas = tk.Canvas(left_outer, highlightthickness=0, borderwidth=0, width=360)
        left_scroll = ttk.Scrollbar(left_outer, orient="vertical", command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=left_scroll.set)
        left_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        left_frame = ttk.Frame(left_canvas, padding=10)
        left_window = left_canvas.create_window((0, 0), window=left_frame, anchor="nw")

        def _sync_left_scroll(_event=None):
            left_canvas.configure(scrollregion=left_canvas.bbox("all"))
            left_canvas.itemconfigure(
                left_window,
                width=max(left_canvas.winfo_width(), left_frame.winfo_reqwidth()))

        left_frame.bind("<Configure>", _sync_left_scroll)
        left_canvas.bind("<Configure>", _sync_left_scroll)

        # Wheel is bound only while the pointer is over this panel, so it does
        # not steal scrolling from the console output on the right.
        def _on_wheel(event):
            left_canvas.yview_scroll(int(-event.delta / 120), "units")

        left_canvas.bind("<Enter>", lambda e: self.bind_all("<MouseWheel>", _on_wheel))
        left_canvas.bind("<Leave>", lambda e: self.unbind_all("<MouseWheel>"))


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
        self._date_field(left_frame, row, 0, self.start_date_var)
        self.end_date_var = tk.StringVar(value=(datetime.now() + timedelta(days=4)).strftime("%d/%m/%Y"))
        self._date_field(left_frame, row, 1, self.end_date_var)
        row += 1

        self.range_label = ttk.Label(left_frame, text="", foreground="gray", font=("Arial", 8))
        self.range_label.grid(row=row, column=0, columnspan=2, sticky="w", padx=2)
        self.start_date_var.trace_add("write", self.update_range_label)
        self.end_date_var.trace_add("write", self.update_range_label)
        self.start_date_var.trace_add("write", self.update_type_day_counts)
        self.end_date_var.trace_add("write", self.update_type_day_counts)
        self.update_range_label()
        row += 1
        
        # Appointment Types (span across)
        ttk.Label(left_frame, text="Appointment Types:", font=("Arial", 9, "bold")).grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 2), padx=2); row+=1
        
        self.types_frame = ttk.Frame(left_frame)
        self.types_frame.grid(row=row, column=0, columnspan=2, sticky="w")
        row+=1
        
        self.type_vars = {}
        self.weekday_vars = {}
        self.day_count_labels = {}
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

    def _date_field(self, parent, row, column, var):
        """A date entry paired with a button that opens the calendar picker."""
        holder = ttk.Frame(parent)
        holder.grid(row=row, column=column, pady=2, padx=2, sticky="w")
        ttk.Entry(holder, textvariable=var, width=16).pack(side=tk.LEFT)
        ttk.Button(holder, text="📅", width=3,
                   command=lambda: CalendarPopup(self, var)).pack(side=tk.LEFT, padx=(2, 0))

    def update_range_label(self, *args):
        """Shows how many days the chosen range covers, so an over-long scan is
        obvious before it starts rather than after."""
        try:
            start = datetime.strptime(normalize_date(self.start_date_var.get(), "start"), "%d/%m/%Y")
            end = datetime.strptime(normalize_date(self.end_date_var.get(), "end"), "%d/%m/%Y")
        except Exception:
            self.range_label.config(text="", foreground="gray")
            return

        days = (end - start).days + 1
        if days < 1:
            self.range_label.config(text="End date is before start date", foreground="red")
        else:
            self.range_label.config(
                text=f"{days} day{'s' if days != 1 else ''} per appointment type",
                foreground="gray" if days <= 30 else "#b06000")

    def update_appointment_types_ui(self, *args):
        # This runs again on every city switch and destroys the old widgets, so
        # carry the weekday picks over — types 2/6/26 exist in both cities and
        # the user would otherwise lose their choices just by looking at Lahore.
        previous = {value: [bit.get() for bit in bits]
                    for value, bits in self.weekday_vars.items()}

        for widget in self.types_frame.winfo_children():
            widget.destroy()

        city = self.city_var.get()
        types = bot.get_appointment_types(city)
        self.current_appointment_choices = [{"value": v, "label": l} for v, l in types]

        self.type_vars = {}
        self.weekday_vars = {}
        self.day_count_labels = {}
        for r, t in enumerate(self.current_appointment_choices):
            value = t["value"]
            block = ttk.Frame(self.types_frame)
            block.grid(row=r, column=0, sticky="w", pady=(0, 5))

            var = tk.BooleanVar(value=True)
            self.type_vars[value] = var
            ttk.Checkbutton(block, text=t["label"], variable=var).grid(
                row=0, column=0, sticky="w", padx=10)

            # Plain tk.Checkbutton, not ttk: indicatoron=0 turns it into a toggle
            # that visibly stays pressed and honours selectcolor. ttk has no
            # equivalent without defining a custom theme element.
            day_row = ttk.Frame(block)
            day_row.grid(row=1, column=0, sticky="w", padx=(28, 0))
            saved = previous.get(value, [True] * 7)
            bits = []
            for i, name in enumerate(self.DAY_TOGGLE_LABELS):
                bit = tk.BooleanVar(value=saved[i])
                bits.append(bit)
                tk.Checkbutton(day_row, text=name, variable=bit, indicatoron=0,
                               width=2, font=("Arial", 8), selectcolor="#9ec5fe",
                               command=self.update_type_day_counts).pack(side=tk.LEFT, padx=1)
            self.weekday_vars[value] = bits

            count = ttk.Label(day_row, text="", foreground="gray", font=("Arial", 8))
            count.pack(side=tk.LEFT, padx=(6, 0))
            self.day_count_labels[value] = count

        self.update_type_day_counts()

    def update_type_day_counts(self, *args):
        """How many dates in the range each type will actually be searched on."""
        if not self.day_count_labels:
            return

        try:
            start = datetime.strptime(normalize_date(self.start_date_var.get(), "start"), "%d/%m/%Y")
            end = datetime.strptime(normalize_date(self.end_date_var.get(), "end"), "%d/%m/%Y")
            total = (end - start).days + 1
        except Exception:
            total = 0

        for value, label in self.day_count_labels.items():
            if total < 1:
                label.config(text="", foreground="gray")
                continue

            picked = {i for i, bit in enumerate(self.weekday_vars[value]) if bit.get()}
            if len(picked) >= 7:
                label.config(text=f"{total} days", foreground="gray")
                continue

            matching = sum(1 for offset in range(total)
                           if (start + timedelta(days=offset)).weekday() in picked)
            label.config(text=f"{matching} of {total} days",
                         foreground="red" if matching == 0 else "gray")

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
            # Older configs stored bare value strings instead of {value,label}
            saved_values = {t["value"] if isinstance(t, dict) else str(t) for t in saved_types}
            if saved_values:
                for t in self.current_appointment_choices:
                    self.type_vars[t["value"]].set(t["value"] in saved_values)

            # Weekdays arrived after the rest of this config. Entries without them
            # — including the oldest bare-string format — mean "every day".
            for saved in saved_types:
                if not isinstance(saved, dict):
                    continue
                bits = self.weekday_vars.get(saved.get("value"))
                weekdays = saved.get("weekdays")
                if bits is None or not isinstance(weekdays, list) or not weekdays:
                    continue
                for i, bit in enumerate(bits):
                    bit.set(i in weekdays)
            self.update_type_day_counts()
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
                if not self.type_vars[t["value"]].get():
                    continue
                weekdays = [i for i, bit in enumerate(self.weekday_vars[t["value"]]) if bit.get()]
                if not weekdays:
                    raise ValueError(f"{t['label']}: pick at least one weekday.")
                chosen_types.append({"value": t["value"], "label": t["label"], "weekdays": weekdays})

            if not chosen_types:
                raise ValueError("Pick at least one appointment type.")

            # A type whose weekdays never occur in the range would search nothing
            # and look like a hang, so catch it here rather than mid-scan.
            scan_start = datetime.strptime(start_date_str, "%d/%m/%Y")
            scan_end = datetime.strptime(end_date_str, "%d/%m/%Y")
            if scan_end < scan_start:
                raise ValueError("Scan end date is before the scan start date.")
            range_days = (scan_end - scan_start).days + 1
            for t in chosen_types:
                picked = set(t["weekdays"])
                if len(picked) >= 7:
                    continue
                if not any((scan_start + timedelta(days=o)).weekday() in picked
                           for o in range(range_days)):
                    raise ValueError(
                        f"{t['label']}: none of the selected weekdays fall between "
                        f"{start_date_str} and {end_date_str}.")


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
            # Only restricted types go in — an all-seven type is left out so the
            # dict stays empty for anyone who never uses the weekday filter.
            bot.SCAN_WEEKDAYS = {t["value"]: set(t.get("weekdays", []))
                                 for t in cfg["appointment_types"]
                                 if 0 < len(t.get("weekdays", [])) < 7}

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
