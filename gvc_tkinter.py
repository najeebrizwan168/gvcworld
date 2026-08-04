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


# ---------------------------------------------------------------------------
# Look and feel
# ---------------------------------------------------------------------------
# Deep navy with a red accent and a gold hairline — the palette a German
# consular service actually uses, so the tool reads as part of that world
# rather than as a generic utility.
PALETTE = {
    "page":      "#f4f5f7",   # window background
    "card":      "#ffffff",   # panel background
    "header":    "#10284b",   # title bar
    "header_dim": "#8fa6c4",  # subtitle on the title bar
    "primary":   "#1b3a6b",
    "primary_hi": "#26518f",  # hover
    "primary_lo": "#122a4e",  # pressed
    "accent":    "#c8102e",   # stop / errors only
    "accent_hi": "#fdecee",
    "accent_lo": "#f7ccd3",
    "gold":      "#d4a017",
    "gold_hi":   "#e5b52c",
    "gold_lo":   "#b3860f",
    "text":      "#1c2430",
    "muted":     "#6b7684",
    "border":    "#dfe3e9",
    "field":     "#c6ccd6",
    "sunken":    "#eef0f4",   # unselected toggle
    "hover":     "#eef2f8",
    "ok":        "#1a7f37",
    "warn":      "#b06000",
}

FONT_UI    = ("Segoe UI", 9)
FONT_SMALL = ("Segoe UI", 8)
FONT_LABEL = ("Segoe UI", 8, "bold")
FONT_CARD  = ("Segoe UI", 9, "bold")
FONT_TITLE = ("Segoe UI", 15, "bold")
FONT_BTN   = ("Segoe UI", 9, "bold")
FONT_LOG   = ("Consolas", 9)


def mix(colour_a: str, colour_b: str, t: float) -> str:
    """Blends two #rrggbb colours. t=0 gives a, t=1 gives b."""
    a = [int(colour_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(colour_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


BUTTON_KINDS = {
    # bg, fg, border, hover, pressed
    "primary": (PALETTE["primary"], "#ffffff", PALETTE["primary"],
                PALETTE["primary_hi"], PALETTE["primary_lo"]),
    "danger":  ("#ffffff", PALETTE["accent"], PALETTE["accent"],
                PALETTE["accent_hi"], PALETTE["accent_lo"]),
    "gold":    (PALETTE["gold"], "#26200c", PALETTE["gold"],
                PALETTE["gold_hi"], PALETTE["gold_lo"]),
    "ghost":   ("#ffffff", PALETTE["primary"], PALETTE["field"],
                PALETTE["hover"], PALETTE["border"]),
}


class ActionButton(tk.Button):
    """
    A flat button that reacts to the pointer: it lifts on hover, sinks on
    press, and eases back to its resting colour over ~150ms on release.

    Plain tk.Button rather than ttk because ttk on Windows hands button
    rendering to the native theme engine, which ignores background colours —
    there is no way to paint a navy button through it.
    """

    ANIM_STEPS = 6
    ANIM_MS = 25

    def __init__(self, parent, text, command=None, kind="primary", **kw):
        bg, fg, border, hover, press = BUTTON_KINDS[kind]
        self._bg, self._fg, self._hover, self._press = bg, fg, hover, press
        self._anim = None
        self._inside = False
        super().__init__(
            parent, text=text, command=command, font=FONT_BTN,
            bg=bg, fg=fg, activebackground=press, activeforeground=fg,
            disabledforeground=PALETTE["muted"],
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=border, highlightcolor=border, cursor="hand2",
            padx=kw.pop("padx", 14), pady=kw.pop("pady", 7), **kw)

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    # keep the disabled look in step with state changes made by the caller
    def configure(self, cnf=None, **kw):
        result = super().configure(cnf, **kw)
        if (cnf and "state" in cnf) or "state" in kw:
            self._settle()
        return result

    config = configure

    def _enabled(self) -> bool:
        return str(self["state"]) != "disabled"

    def _stop_anim(self):
        if self._anim is not None:
            try:
                self.after_cancel(self._anim)
            except Exception:
                pass
            self._anim = None

    def _settle(self):
        """Repaint to whatever the resting colour should be right now."""
        self._stop_anim()
        if not self._enabled():
            self.configure(bg=mix(self._bg, PALETTE["page"], 0.65),
                           cursor="arrow")
            return
        self.configure(bg=self._hover if self._inside else self._bg,
                       cursor="hand2")

    def _on_enter(self, _event=None):
        self._inside = True
        if self._enabled():
            self._stop_anim()
            self.configure(bg=self._hover)

    def _on_leave(self, _event=None):
        self._inside = False
        if self._enabled():
            self._stop_anim()
            self.configure(bg=self._bg)

    def _on_press(self, _event=None):
        if self._enabled():
            self._stop_anim()
            self.configure(bg=self._press)

    def _on_release(self, _event=None):
        if not self._enabled():
            return
        target = self._hover if self._inside else self._bg
        self._ease(self._press, target, 0)

    def _ease(self, start, end, step):
        if step > self.ANIM_STEPS:
            self._anim = None
            return
        try:
            self.configure(bg=mix(start, end, step / self.ANIM_STEPS))
        except tk.TclError:      # widget destroyed mid-animation
            return
        self._anim = self.after(self.ANIM_MS, self._ease, start, end, step + 1)


def make_card(parent, title):
    """
    A white panel with a section title and the gold hairline under it.
    Returns the body frame to put content in.
    """
    outer = tk.Frame(parent, bg=PALETTE["card"], highlightthickness=1,
                     highlightbackground=PALETTE["border"],
                     highlightcolor=PALETTE["border"])
    outer.pack(fill=tk.X, pady=(0, 10))

    head = tk.Frame(outer, bg=PALETTE["card"])
    head.pack(fill=tk.X, padx=12, pady=(10, 0))
    tk.Label(head, text=title.upper(), bg=PALETTE["card"],
             fg=PALETTE["header"], font=FONT_CARD).pack(anchor="w")
    tk.Frame(head, bg=PALETTE["gold"], height=2, width=34).pack(anchor="w", pady=(3, 0))

    body = tk.Frame(outer, bg=PALETTE["card"])
    body.pack(fill=tk.X, padx=12, pady=(8, 12))
    return body


def field_label(parent, text):
    return tk.Label(parent, text=text.upper(), bg=PALETTE["card"],
                    fg=PALETTE["muted"], font=FONT_LABEL)


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

        self.configure(bg=PALETTE["card"])

        header = tk.Frame(self, bg=PALETTE["header"])
        header.pack(fill=tk.X)
        ActionButton(header, "‹", command=lambda: self.shift_month(-1),
                     kind="primary", padx=10, pady=3).pack(side=tk.LEFT, padx=8, pady=8)
        self.header_label = tk.Label(header, anchor="center", width=18,
                                     bg=PALETTE["header"], fg="#ffffff",
                                     font=("Segoe UI", 10, "bold"))
        self.header_label.pack(side=tk.LEFT, expand=True)
        ActionButton(header, "›", command=lambda: self.shift_month(1),
                     kind="primary", padx=10, pady=3).pack(side=tk.LEFT, padx=8, pady=8)

        self.grid_frame = tk.Frame(self, bg=PALETTE["card"], padx=8, pady=8)
        self.grid_frame.pack()

        footer = tk.Frame(self, bg=PALETTE["card"])
        footer.pack(fill=tk.X, padx=8, pady=(0, 8))
        ActionButton(footer, "Today", command=self.pick_today,
                     kind="ghost", padx=10, pady=4).pack(side=tk.LEFT)
        ActionButton(footer, "Cancel", command=self.destroy,
                     kind="ghost", padx=10, pady=4).pack(side=tk.RIGHT)

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
            tk.Label(self.grid_frame, text=name, width=4, anchor="center",
                     bg=PALETTE["card"],
                     fg=PALETTE["accent"] if col >= 5 else PALETTE["muted"],
                     font=FONT_LABEL).grid(row=0, column=col, padx=1, pady=(0, 4))

        today = datetime.now().date()
        for r, week in enumerate(calendar.Calendar(firstweekday=0).monthdayscalendar(
                self.year, self.month), start=1):
            for c, day in enumerate(week):
                if day == 0:
                    continue
                when = datetime(self.year, self.month, day)
                kind = "ghost"
                if when.date() == self.selected.date():
                    kind = "primary"
                elif when.date() == today:
                    kind = "gold"
                ActionButton(self.grid_frame, str(day), kind=kind,
                             command=lambda w=when: self.choose(w),
                             width=2, padx=4, pady=3).grid(row=r, column=c, padx=1, pady=1)


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
        self.configure(bg=PALETTE["page"])

        self._thread = None
        self._stop = threading.Event()
        self._continue = threading.Event()
        self._driver = None
        self._hwnd = None
        self._lock = threading.Lock()
        self._pulse = None

        self.apply_theme()
        self.build_ui()
        self.load_settings()

    def apply_theme(self):
        """
        Paints ttk to match the palette.

        'clam' is the only bundled theme that lets colours through — the default
        Windows theme delegates to the native renderer and silently ignores most
        of what is configured below.
        """
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure("TFrame", background=PALETTE["card"])
        style.configure("Page.TFrame", background=PALETTE["page"])
        style.configure("TPanedwindow", background=PALETTE["page"])
        style.configure("TLabel", background=PALETTE["card"],
                        foreground=PALETTE["text"], font=FONT_UI)

        style.configure("Field.TEntry", fieldbackground="#ffffff",
                        background="#ffffff", foreground=PALETTE["text"],
                        insertcolor=PALETTE["text"], padding=(6, 5),
                        bordercolor=PALETTE["field"], lightcolor=PALETTE["field"],
                        darkcolor=PALETTE["field"], borderwidth=1)
        for prop in ("bordercolor", "lightcolor", "darkcolor"):
            style.map("Field.TEntry", **{prop: [("focus", PALETTE["primary"])]})

        style.configure("Field.TCombobox", fieldbackground="#ffffff",
                        background="#ffffff", foreground=PALETTE["text"],
                        arrowcolor=PALETTE["primary"], padding=(5, 4),
                        bordercolor=PALETTE["field"], lightcolor=PALETTE["field"],
                        darkcolor=PALETTE["field"], borderwidth=1)
        style.map("Field.TCombobox",
                  fieldbackground=[("readonly", "#ffffff")],
                  selectbackground=[("readonly", "#ffffff")],
                  selectforeground=[("readonly", PALETTE["text"])],
                  bordercolor=[("focus", PALETTE["primary"])])
        # the popdown list is a classic Tk listbox, styled through the option db
        self.option_add("*TCombobox*Listbox.background", "#ffffff")
        self.option_add("*TCombobox*Listbox.foreground", PALETTE["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", PALETTE["primary"])
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.option_add("*TCombobox*Listbox.font", FONT_UI)

        style.configure("Card.TCheckbutton", background=PALETTE["card"],
                        foreground=PALETTE["text"], font=FONT_UI,
                        focuscolor=PALETTE["card"])
        style.map("Card.TCheckbutton",
                  background=[("active", PALETTE["card"])],
                  indicatorcolor=[("selected", PALETTE["primary"]),
                                  ("!selected", "#ffffff")])

        style.configure("Vertical.TScrollbar", background=PALETTE["border"],
                        troughcolor=PALETTE["page"], bordercolor=PALETTE["page"],
                        arrowcolor=PALETTE["muted"], relief="flat")
        style.map("Vertical.TScrollbar",
                  background=[("active", PALETTE["muted"])])

    # ---------------------------------------------------------------- layout
    def build_ui(self):
        self.build_header()

        body = tk.Frame(self, bg=PALETTE["page"])
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        self.paned = ttk.PanedWindow(body, orient=tk.HORIZONTAL)
        self.paned.pack(fill=tk.BOTH, expand=True)

        left_frame = self.build_settings_pane()
        self.build_activity_pane()

        self.build_account_card(left_frame)
        self.build_applicant_card(left_frame)
        self.build_search_card(left_frame)
        self.build_action_bar(left_frame)

    def build_header(self):
        """Navy title bar with the live status pill on the right."""
        bar = tk.Frame(self, bg=PALETTE["header"])
        bar.pack(fill=tk.X, side=tk.TOP)

        left = tk.Frame(bar, bg=PALETTE["header"])
        left.pack(side=tk.LEFT, padx=16, pady=10)
        tk.Label(left, text="✈  GVC Appointment Scanner", bg=PALETTE["header"],
                 fg="#ffffff", font=FONT_TITLE).pack(anchor="w")
        tk.Label(left, text="Visa appointment availability monitor",
                 bg=PALETTE["header"], fg=PALETTE["header_dim"],
                 font=FONT_SMALL).pack(anchor="w")

        right = tk.Frame(bar, bg=PALETTE["header"])
        right.pack(side=tk.RIGHT, padx=16)
        self.status_dot = tk.Canvas(right, width=12, height=12, bg=PALETTE["header"],
                                    highlightthickness=0)
        self.status_dot.pack(side=tk.LEFT, pady=2)
        self._dot = self.status_dot.create_oval(2, 2, 10, 10, fill=PALETTE["header_dim"],
                                                outline="")
        self.status_label = tk.Label(right, text="Status: Idle", bg=PALETTE["header"],
                                     fg="#ffffff", font=FONT_CARD,
                                     wraplength=360, justify="left")
        self.status_label.pack(side=tk.LEFT, padx=(8, 0))

        tk.Frame(self, bg=PALETTE["gold"], height=3).pack(fill=tk.X, side=tk.TOP)

    def build_settings_pane(self):
        """
        The scrolling column of cards on the left.

        Scrollable because the per-type weekday rows push the settings past the
        window height on a 720p screen; without it the Start button ends up
        below the bottom edge.
        """
        left_outer = tk.Frame(self.paned, bg=PALETTE["page"])
        self.paned.add(left_outer, weight=1)

        left_canvas = tk.Canvas(left_outer, highlightthickness=0, borderwidth=0,
                                width=392, bg=PALETTE["page"])
        left_scroll = ttk.Scrollbar(left_outer, orient="vertical",
                                    command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=left_scroll.set)
        left_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        left_frame = tk.Frame(left_canvas, bg=PALETTE["page"], padx=12, pady=12)
        left_window = left_canvas.create_window((0, 0), window=left_frame, anchor="nw")

        def _sync_left_scroll(_event=None):
            left_canvas.configure(scrollregion=left_canvas.bbox("all"))
            left_canvas.itemconfigure(
                left_window,
                width=max(left_canvas.winfo_width(), left_frame.winfo_reqwidth()))

        left_frame.bind("<Configure>", _sync_left_scroll)
        left_canvas.bind("<Configure>", _sync_left_scroll)

        # Wheel is bound only while the pointer is over this panel, so it does
        # not steal scrolling from the activity log on the right.
        def _on_wheel(event):
            left_canvas.yview_scroll(int(-event.delta / 120), "units")

        left_canvas.bind("<Enter>", lambda e: self.bind_all("<MouseWheel>", _on_wheel))
        left_canvas.bind("<Leave>", lambda e: self.unbind_all("<MouseWheel>"))
        return left_frame

    def build_activity_pane(self):
        right_outer = tk.Frame(self.paned, bg=PALETTE["page"], padx=12, pady=12)
        self.paned.add(right_outer, weight=2)

        card = tk.Frame(right_outer, bg=PALETTE["card"], highlightthickness=1,
                        highlightbackground=PALETTE["border"])
        card.pack(fill=tk.BOTH, expand=True)

        head = tk.Frame(card, bg=PALETTE["card"])
        head.pack(fill=tk.X, padx=12, pady=(10, 0))
        tk.Label(head, text="ACTIVITY", bg=PALETTE["card"], fg=PALETTE["header"],
                 font=FONT_CARD).pack(anchor="w")
        tk.Frame(head, bg=PALETTE["gold"], height=2, width=34).pack(anchor="w", pady=(3, 0))

        self.log_area = scrolledtext.ScrolledText(
            card, wrap=tk.WORD, state="disabled", font=FONT_LOG,
            bg="#fbfcfe", fg=PALETTE["text"], relief="flat", borderwidth=0,
            padx=10, pady=8, insertbackground=PALETTE["text"],
            selectbackground=PALETTE["primary"], selectforeground="#ffffff")
        self.log_area.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # One tag per severity, so the log is scannable at a glance instead of
        # being a wall of identical grey text.
        for tag, colour, font in (
                ("info",  PALETTE["text"],   FONT_LOG),
                ("muted", PALETTE["muted"],  FONT_LOG),
                ("head",  PALETTE["primary"], ("Consolas", 9, "bold")),
                ("ok",    PALETTE["ok"],     ("Consolas", 9, "bold")),
                ("warn",  PALETTE["warn"],   FONT_LOG),
                ("err",   PALETTE["accent"], ("Consolas", 9, "bold"))):
            self.log_area.tag_configure(tag, foreground=colour, font=font)

    def build_account_card(self, parent):
        card = make_card(parent, "Account")
        card.columnconfigure(0, weight=1, uniform="f")
        card.columnconfigure(1, weight=1, uniform="f")

        self.username_var = tk.StringVar()
        self._entry(card, 0, 0, "Username", self.username_var)
        self.password_var = tk.StringVar()
        self._entry(card, 0, 1, "Password", self.password_var, show="*")

    def build_applicant_card(self, parent):
        card = make_card(parent, "Applicant")
        card.columnconfigure(0, weight=1, uniform="f")
        card.columnconfigure(1, weight=1, uniform="f")

        self.first_name_var = tk.StringVar()
        self._entry(card, 0, 0, "First name", self.first_name_var)
        self.surname_var = tk.StringVar()
        self._entry(card, 0, 1, "Surname", self.surname_var)

        self.dob_var = tk.StringVar()
        self._entry(card, 2, 0, "Date of birth", self.dob_var)
        self.passport_var = tk.StringVar()
        self._entry(card, 2, 1, "Passport number", self.passport_var)

        self.expiry_var = tk.StringVar()
        self._entry(card, 4, 0, "Passport expiry", self.expiry_var)
        self.gender_var = tk.StringVar()
        self._combo(card, 4, 1, "Gender", self.gender_var,
                    [g["label"] for g in GENDER_CHOICES])

        self.nationality_var = tk.StringVar()
        self._entry(card, 6, 0, "Nationality", self.nationality_var)
        self.city_var = tk.StringVar(value="islamabad")
        self._combo(card, 6, 1, "City (VAC)", self.city_var, ["islamabad", "lahore"])
        self.city_var.trace_add("write", self.update_appointment_types_ui)

    def build_search_card(self, parent):
        card = make_card(parent, "Search window")
        card.columnconfigure(0, weight=1, uniform="f")
        card.columnconfigure(1, weight=1, uniform="f")

        self.start_date_var = tk.StringVar(value=datetime.now().strftime("%d/%m/%Y"))
        self._date_field(card, 0, 0, self.start_date_var, "From")
        self.end_date_var = tk.StringVar(
            value=(datetime.now() + timedelta(days=4)).strftime("%d/%m/%Y"))
        self._date_field(card, 0, 1, self.end_date_var, "To")

        self.range_label = tk.Label(card, text="", bg=PALETTE["card"],
                                    fg=PALETTE["muted"], font=FONT_SMALL)
        self.range_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        for var in (self.start_date_var, self.end_date_var):
            var.trace_add("write", self.update_range_label)
            var.trace_add("write", self.update_type_day_counts)
        self.update_range_label()

        tk.Frame(card, bg=PALETTE["border"], height=1).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=10)

        tk.Label(card, text="APPOINTMENT TYPES", bg=PALETTE["card"],
                 fg=PALETTE["muted"], font=FONT_LABEL).grid(
            row=4, column=0, columnspan=2, sticky="w")
        tk.Label(card, text="Untick a weekday to skip it for that type.",
                 bg=PALETTE["card"], fg=PALETTE["muted"], font=FONT_SMALL).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(1, 6))

        self.types_frame = tk.Frame(card, bg=PALETTE["card"])
        self.types_frame.grid(row=6, column=0, columnspan=2, sticky="ew")

        self.type_vars = {}
        self.weekday_vars = {}
        self.day_count_labels = {}
        self.current_appointment_choices = []

    def build_action_bar(self, parent):
        bar = tk.Frame(parent, bg=PALETTE["page"])
        bar.pack(fill=tk.X, pady=(2, 0))
        bar.columnconfigure(0, weight=3, uniform="b")
        bar.columnconfigure(1, weight=2, uniform="b")

        self.start_btn = ActionButton(bar, "▶  Start Scanning",
                                      command=self.start_scan, kind="primary")
        self.start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.stop_btn = ActionButton(bar, "⏹  Stop", command=self.stop_scan,
                                     kind="danger", state="disabled")
        self.stop_btn.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self.confirm_btn = ActionButton(bar, "🔓  Confirm Manual Action",
                                        command=self.confirm_gate, kind="gold",
                                        state="disabled")
        self.confirm_btn.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    # ------------------------------------------------------------ field parts
    def _entry(self, parent, row, column, label, var, **kw):
        field_label(parent, label).grid(row=row, column=column, sticky="w",
                                        padx=(0, 6) if column == 0 else (6, 0))
        entry = ttk.Entry(parent, textvariable=var, style="Field.TEntry",
                          font=FONT_UI, **kw)
        entry.grid(row=row + 1, column=column, sticky="ew", pady=(2, 8),
                   padx=(0, 6) if column == 0 else (6, 0))
        return entry

    def _combo(self, parent, row, column, label, var, values):
        field_label(parent, label).grid(row=row, column=column, sticky="w",
                                        padx=(0, 6) if column == 0 else (6, 0))
        combo = ttk.Combobox(parent, textvariable=var, values=values,
                             state="readonly", style="Field.TCombobox", font=FONT_UI)
        combo.grid(row=row + 1, column=column, sticky="ew", pady=(2, 8),
                   padx=(0, 6) if column == 0 else (6, 0))
        return combo

    def _date_field(self, parent, row, column, var, label="Date"):
        """A date entry paired with a button that opens the calendar picker."""
        field_label(parent, label).grid(row=row, column=column, sticky="w",
                                        padx=(0, 6) if column == 0 else (6, 0))
        holder = tk.Frame(parent, bg=PALETTE["card"])
        holder.grid(row=row + 1, column=column, sticky="ew", pady=(2, 0),
                    padx=(0, 6) if column == 0 else (6, 0))
        ttk.Entry(holder, textvariable=var, style="Field.TEntry",
                  font=FONT_UI, width=11).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ActionButton(holder, "📅", kind="ghost", padx=7, pady=3,
                     command=lambda: CalendarPopup(self, var)).pack(side=tk.LEFT, padx=(4, 0))

    def update_range_label(self, *args):
        """Shows how many days the chosen range covers, so an over-long scan is
        obvious before it starts rather than after."""
        try:
            start = datetime.strptime(normalize_date(self.start_date_var.get(), "start"), "%d/%m/%Y")
            end = datetime.strptime(normalize_date(self.end_date_var.get(), "end"), "%d/%m/%Y")
        except Exception:
            self.range_label.config(text="", foreground=PALETTE["muted"])
            return

        days = (end - start).days + 1
        if days < 1:
            self.range_label.config(text="⚠  End date is before start date",
                                    foreground=PALETTE["accent"])
        else:
            self.range_label.config(
                text=f"{days} day{'s' if days != 1 else ''} per appointment type",
                foreground=PALETTE["muted"] if days <= 30 else PALETTE["warn"])

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
            block = tk.Frame(self.types_frame, bg=PALETTE["card"])
            block.grid(row=r, column=0, sticky="ew", pady=(0, 8))

            var = tk.BooleanVar(value=True)
            self.type_vars[value] = var
            # tk, not ttk: the clam theme draws its checked indicator as a cross,
            # which reads as "excluded" on a list of things you are opting into.
            tk.Checkbutton(block, text=t["label"], variable=var,
                           bg=PALETTE["card"], fg=PALETTE["text"], font=FONT_UI,
                           activebackground=PALETTE["card"],
                           activeforeground=PALETTE["primary"],
                           selectcolor="#ffffff", anchor="w", bd=0,
                           highlightthickness=0, padx=0, cursor="hand2"
                           ).grid(row=0, column=0, sticky="w")

            # Plain tk.Checkbutton, not ttk: indicatoron=0 turns it into a toggle
            # button, and only the tk widget lets us repaint it per state. ttk
            # has no equivalent without defining a custom theme element.
            day_row = tk.Frame(block, bg=PALETTE["card"])
            day_row.grid(row=1, column=0, sticky="w", padx=(20, 0), pady=(4, 0))
            saved = previous.get(value, [True] * 7)
            bits = []
            for i, name in enumerate(self.DAY_TOGGLE_LABELS):
                bit = tk.BooleanVar(value=saved[i])
                bits.append(bit)
                toggle = tk.Checkbutton(
                    day_row, text=name, variable=bit, indicatoron=0, width=2,
                    font=FONT_LABEL, relief="flat", bd=0, highlightthickness=1,
                    highlightbackground=PALETTE["border"], cursor="hand2",
                    takefocus=0)
                toggle.configure(command=lambda b=bit, w=toggle: self._toggle_day(b, w))
                self._paint_day_toggle(bit, toggle)
                toggle.pack(side=tk.LEFT, padx=1)
            self.weekday_vars[value] = bits

            count = tk.Label(day_row, text="", bg=PALETTE["card"],
                             fg=PALETTE["muted"], font=FONT_SMALL)
            count.pack(side=tk.LEFT, padx=(8, 0))
            self.day_count_labels[value] = count

        self.update_type_day_counts()

    def _paint_day_toggle(self, bit, widget):
        """Selected weekdays are filled navy; skipped ones sit back in grey."""
        on = bit.get()
        widget.configure(
            bg=PALETTE["primary"] if on else PALETTE["sunken"],
            fg="#ffffff" if on else PALETTE["muted"],
            selectcolor=PALETTE["primary"] if on else PALETTE["sunken"],
            activebackground=PALETTE["primary_hi"] if on else PALETTE["hover"],
            activeforeground="#ffffff" if on else PALETTE["text"],
            highlightbackground=PALETTE["primary"] if on else PALETTE["border"])

    def _toggle_day(self, bit, widget):
        self._paint_day_toggle(bit, widget)
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
                label.config(text="", fg=PALETTE["muted"])
                continue

            picked = {i for i, bit in enumerate(self.weekday_vars[value]) if bit.get()}
            if len(picked) >= 7:
                label.config(text=f"{total} days", fg=PALETTE["muted"])
                continue

            matching = sum(1 for offset in range(total)
                           if (start + timedelta(days=offset)).weekday() in picked)
            label.config(text=f"{matching} of {total} days",
                         fg=PALETTE["accent"] if matching == 0 else PALETTE["muted"])

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

    LOG_TAGS = (
        ("ok",    ("✅", "SLOTS AVAILABLE", "OTP REQUESTED", "🟢", "📲", "successfully")),
        ("err",   ("❌", "FATAL", "Traceback", "Error:", "ERROR", "failed", "Failed")),
        ("warn",  ("⚠",)),
        ("head",  ("[STEP]", "[SCANNING]", "[GATE]", "══", "SCAN ROUND")),
        ("muted", ("✗",)),
    )

    @classmethod
    def log_tag(cls, msg: str) -> str:
        """Severity of a log line, so the activity panel is scannable."""
        for tag, markers in cls.LOG_TAGS:
            if any(marker in msg for marker in markers):
                return tag
        return "info"

    def log(self, msg):
        self.log_area.config(state="normal")
        self.log_area.insert(tk.END, msg + "\n", self.log_tag(msg))
        self.log_area.see(tk.END)
        self.log_area.config(state="disabled")

    # --------------------------------------------------------------- status
    STATUS_COLOURS = {
        "idle":    PALETTE["header_dim"],
        "busy":    PALETTE["gold"],
        "waiting": PALETTE["gold"],
        "running": "#4ade80",
        "stopped": PALETTE["header_dim"],
        "error":   PALETTE["accent"],
    }

    def set_status(self, text, kind="idle"):
        """Updates the header pill. 'running' and 'waiting' pulse the dot."""
        self.status_label.config(text=f"Status: {text}")
        colour = self.STATUS_COLOURS.get(kind, PALETTE["header_dim"])
        self.status_dot.itemconfigure(self._dot, fill=colour)

        if self._pulse is not None:
            try:
                self.after_cancel(self._pulse)
            except Exception:
                pass
            self._pulse = None
        if kind in ("running", "waiting"):
            self._pulse_dot(colour, 0)

    def _pulse_dot(self, colour, step):
        """Breathes the status dot between its colour and the bar behind it."""
        t = abs((step % 20) - 10) / 10.0
        try:
            self.status_dot.itemconfigure(
                self._dot, fill=mix(colour, PALETTE["header"], t * 0.55))
        except tk.TclError:
            return
        self._pulse = self.after(70, self._pulse_dot, colour, step + 1)

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
        self.set_status("Starting…", "busy")
        
        self._stop.clear()
        self._continue.clear()
        
        self._thread = threading.Thread(target=self._run_scan, args=(cfg,), daemon=True)
        self._thread.start()

    def _login_override(self):
        """Confirm-button escape hatch for the automatic login detection.

        The bot detects a completed login on its own; this only matters if the
        portal changes shape and the detection stops recognising it, so the
        operator can still say 'I am in' and keep the run going.
        """
        if self._continue.is_set():
            self._continue.clear()
            self.after(0, self.log, "[GATE] Continue received - resuming automation.")
            self.after(0, self._gate_closed)
            self._hide_window()
            return True
        return False

    def _gate_open(self, message):
        self.confirm_btn.config(state="normal")
        self.set_status("Waiting for you to sign in to Chrome", "waiting")
        self.log(message)

    def _gate_closed(self):
        self.confirm_btn.config(state="disabled")
        self.set_status("Running", "running")

    def _run_scan(self, cfg):
        original_print = getattr(bot, "print", None)
        original_input = getattr(bot, "input", None)
        original_webdriver = bot.webdriver
        original_ready = getattr(bot, "ON_SESSION_READY", None)
        original_override = getattr(bot, "LOGIN_OVERRIDE", None)
        gate_shown = threading.Event()

        def on_session_ready():
            """The bot got in with no human needed — put Chrome back out of sight."""
            self.after(0, self.log, "[SESSION] Signed in from the saved profile - no login needed.")
            self._hide_window()

        def patched_print(*args, **kwargs):
            if self._stop.is_set():
                raise KeyboardInterrupt("stopped from UI")
            text = kwargs.get("sep", " ").join(str(a) for a in args)

            # The login gate is no longer an input() call — the bot polls for the
            # login instead — so the Confirm button is armed off the bot's own
            # heartbeat line, the first time it appears.
            if "[GATE] Waiting for you to finish signing in" in text and not gate_shown.is_set():
                gate_shown.set()
                self._continue.clear()
                self._show_window()
                self.after(0, self._gate_open,
                           "[GATE] Sign in to Chrome - the scanner will detect it automatically.")
            elif "Login detected" in text and gate_shown.is_set():
                gate_shown.clear()          # re-arms if a later session expires
                self.after(0, self._gate_closed)
                self._hide_window()

            for line in text.split("\n"):
                if line.strip():
                    self.after(0, self.log, line.rstrip())

        def patched_input(prompt=""):
            text = str(prompt).strip()
            is_login = bool(text)
            
            def enable_confirm():
                self.confirm_btn.config(state="normal")
                self.set_status("Waiting for you to confirm login/booking in Chrome",
                                "waiting")
            
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
                self.set_status("Running", "running")
                
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
            bot.ON_SESSION_READY = on_session_ready
            bot.LOGIN_OVERRIDE = self._login_override
            bot.webdriver = WebDriverProxy(original_webdriver, self._track_driver)

            self.after(0, lambda: self.set_status("Running", "running"))
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
            bot.ON_SESSION_READY = original_ready
            bot.LOGIN_OVERRIDE = original_override
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
        self.set_status("Stopped", "stopped")

    def stop_scan(self):
        self._stop.set()
        self._continue.set()
        self.log("Stop requested... winding down.")
        self.set_status("Stopping…", "busy")

    def confirm_gate(self):
        self._continue.set()

if __name__ == "__main__":
    app = App()
    app.mainloop()
