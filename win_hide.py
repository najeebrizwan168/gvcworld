"""
Windows-only helpers to locate Chrome's real top-level window from the
chromedriver process, and to hide/show it via the Win32 API.

Uses only ctypes (stdlib) — no pywin32/psutil dependency. Selenium exposes
chromedriver's PID (`driver.service.process.pid`), not the browser window's
handle, and chromedriver spawns Chrome as a child process, so the window has
to be found by walking the process tree and matching visible top-level
windows owned by one of Chrome's descendant PIDs.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32

TH32CS_SNAPPROCESS = 0x00000002
SW_HIDE = 0
SW_SHOWNORMAL = 1
GW_OWNER = 4


class _ProcessEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def _descendant_processes(root_pid: int) -> list[tuple[int, str]]:
    """Returns (pid, exe_name) for every process descended from root_pid."""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return []

    entries: list[tuple[int, int, str]] = []
    try:
        pe = _ProcessEntry32()
        pe.dwSize = ctypes.sizeof(_ProcessEntry32)
        if kernel32.Process32First(snap, ctypes.byref(pe)):
            while True:
                entries.append((pe.th32ProcessID, pe.th32ParentProcessID, pe.szExeFile.decode(errors="ignore")))
                if not kernel32.Process32Next(snap, ctypes.byref(pe)):
                    break
    finally:
        kernel32.CloseHandle(snap)

    by_parent: dict[int, list[tuple[int, str]]] = {}
    for pid, ppid, name in entries:
        by_parent.setdefault(ppid, []).append((pid, name))

    out: list[tuple[int, str]] = []
    frontier = [root_pid]
    while frontier:
        current = frontier.pop()
        for pid, name in by_parent.get(current, []):
            out.append((pid, name))
            frontier.append(pid)
    return out


def _find_main_window(candidate_pids: set[int]) -> int | None:
    """Returns the HWND of the first visible, unowned top-level window
    belonging to one of the given PIDs (i.e. Chrome's actual browser window,
    not a renderer/GPU/utility helper process)."""
    found: list[int] = []
    enum_proc_t = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd: int, _lparam: int) -> bool:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in candidate_pids:
            return True
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
            return True
        if user32.GetWindowTextLengthW(hwnd) > 0:
            found.append(hwnd)
        return True

    user32.EnumWindows(enum_proc_t(callback), 0)
    return found[0] if found else None


def find_chrome_hwnd(driver_pid: int, timeout: float = 10.0) -> int | None:
    """Polls for up to `timeout` seconds for Chrome's main window to appear
    under the given chromedriver PID's process tree."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chrome_pids = {
            pid for pid, name in _descendant_processes(driver_pid)
            if name.lower() == "chrome.exe"
        }
        if chrome_pids:
            hwnd = _find_main_window(chrome_pids)
            if hwnd:
                return hwnd
        time.sleep(0.25)
    return None


def file_version(path) -> str:
    """
    Reads an executable's version straight out of its version resource.

    Deliberately not `chrome.exe --version`: on this platform that call can
    block for minutes instead of printing and exiting, which would hang the
    scanner before it ever opened a browser. Reading the resource touches no
    process at all.
    """
    path = str(path)
    version_dll = ctypes.windll.version
    size = version_dll.GetFileVersionInfoSizeW(path, None)
    if not size:
        return ""
    buffer = ctypes.create_string_buffer(size)
    if not version_dll.GetFileVersionInfoW(path, 0, size, buffer):
        return ""

    block = ctypes.c_void_p()
    length = wintypes.UINT()
    if not version_dll.VerQueryValueW(buffer, "\\",
                                      ctypes.byref(block), ctypes.byref(length)):
        return ""

    class _FixedFileInfo(ctypes.Structure):
        _fields_ = [("dwSignature", wintypes.DWORD),
                    ("dwStrucVersion", wintypes.DWORD),
                    ("dwFileVersionMS", wintypes.DWORD),
                    ("dwFileVersionLS", wintypes.DWORD)]

    info = ctypes.cast(block, ctypes.POINTER(_FixedFileInfo)).contents
    return "{}.{}.{}.{}".format(info.dwFileVersionMS >> 16,
                                info.dwFileVersionMS & 0xFFFF,
                                info.dwFileVersionLS >> 16,
                                info.dwFileVersionLS & 0xFFFF)


PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259


def process_is_alive(pid: int) -> bool:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def terminate_process(pid: int) -> bool:
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


def kill_process_tree(root_pid: int, timeout: float = 15.0) -> dict:
    """
    Kills root_pid and everything descended from it, and does not return until
    they are actually gone (or the timeout expires).

    Scoped on purpose: the PIDs come from walking the process tree of a
    chromedriver *we* started, so this can never touch the operator's own Chrome
    or an unrelated automation run. A blanket `taskkill /im chrome.exe` would.

    Returns {"targets": n, "killed": n, "survivors": [pid, ...]}.
    """
    if not root_pid:
        return {"targets": 0, "killed": 0, "survivors": []}

    # Snapshot the tree before killing anything: once the root dies its children
    # are reparented and the walk can no longer find them.
    targets = [root_pid] + [pid for pid, _ in _descendant_processes(root_pid)]
    # Children first, so a parent cannot respawn one on its way out.
    for pid in reversed(targets):
        if process_is_alive(pid):
            terminate_process(pid)

    deadline = time.monotonic() + timeout
    survivors = [pid for pid in targets if process_is_alive(pid)]
    while survivors and time.monotonic() < deadline:
        time.sleep(0.2)
        for pid in survivors:
            terminate_process(pid)
        survivors = [pid for pid in targets if process_is_alive(pid)]

    return {"targets": len(targets),
            "killed": len(targets) - len(survivors),
            "survivors": survivors}


def hide_window(hwnd: int) -> bool:
    return bool(user32.ShowWindow(hwnd, SW_HIDE))


def show_window(hwnd: int) -> bool:
    ok = bool(user32.ShowWindow(hwnd, SW_SHOWNORMAL))
    user32.SetForegroundWindow(hwnd)
    return ok


def is_visible(hwnd: int) -> bool:
    return bool(user32.IsWindowVisible(hwnd))
