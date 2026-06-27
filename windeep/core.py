"""
windeep.core — Pure ctypes Win32 API bindings for desktop deep control.

Provides window management, keystroke injection, registry access,
and process control without any external dependencies.
"""

import ctypes
import ctypes.wintypes
import struct
import sys
import time
from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple


# ── Win32 constant definitions ─────────────────────────────────────────────

_HWND_BROADCAST = 0xFFFF
_HWND_TOP = 0
_HWND_BOTTOM = 1
_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2

_SW_HIDE = 0
_SW_SHOWNORMAL = 1
_SW_SHOWMINIMIZED = 2
_SW_SHOWMAXIMIZED = 3
_SW_SHOWNOACTIVATE = 4
_SW_SHOW = 5
_SW_MINIMIZE = 6
_SW_SHOWMINNOACTIVE = 7
_SW_SHOWNA = 8
_SW_RESTORE = 9
_SW_SHOWDEFAULT = 10
_SW_FORCEMINIMIZE = 11

_WM_CLOSE = 0x0010
_WM_SETTEXT = 0x000C
_WM_KEYDOWN = 0x0100
_WM_KEYUP = 0x0101
_WM_CHAR = 0x0102
_WM_LBUTTONDOWN = 0x0201
_WM_LBUTTONUP = 0x0202
_WM_RBUTTONDOWN = 0x0204
_WM_RBUTTONUP = 0x0205

_WS_CAPTION = 0x00C00000
_WS_THICKFRAME = 0x00040000

_GWL_STYLE = -16
_GWL_EXSTYLE = -20

_SW_MAX = 11

_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_SCANCODE = 0x0008

_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1

_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_ABSOLUTE = 0x8000

_WM_SYSCOMMAND = 0x0112
_SC_MAXIMIZE = 0xF030
_SC_MINIMIZE = 0xF020
_SC_RESTORE = 0xF120
_SC_CLOSE = 0xF060

# Registry constants
_HKEY_CLASSES_ROOT = 0x80000000
_HKEY_CURRENT_USER = 0x80000001
_HKEY_LOCAL_MACHINE = 0x80000002
_HKEY_USERS = 0x80000003
_HKEY_CURRENT_CONFIG = 0x80000005

_KEY_READ = 0x20019
_REG_SZ = 1
_REG_DWORD = 4
_REG_BINARY = 3

# Process
_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_VM_READ = 0x0010
_PROCESS_TERMINATE = 0x0001

# Constants for SendMessage timeout
_SMTO_NORMAL = 0x0000
_SMTO_BLOCK = 0x0001
_SMTO_ABORTIFHUNG = 0x0002
_SMTO_NOTIMEOUTIFNOTHUNG = 0x0008


# ── Type definitions ───────────────────────────────────────────────────────

@dataclass
class WindowInfo:
    """Information about a top-level window."""
    hwnd: int
    title: str
    class_name: str
    pid: int
    visible: bool
    rect: Tuple[int, int, int, int]  # left, top, right, bottom
    minimized: bool
    maximized: bool

    def to_dict(self) -> dict:
        return asdict(self)


# ── Win32 API loading (lazy, one-time) ─────────────────────────────────────

class _Win32:
    """Lazy-loaded Win32 API bindings. Imported once on first use."""
    _initialized = False

    @classmethod
    def _init(cls):
        if cls._initialized:
            return
        cls._initialized = True

        cls.user32 = ctypes.windll.user32
        cls.kernel32 = ctypes.windll.kernel32
        cls.advapi32 = ctypes.windll.advapi32

        # ── Window enumeration ──
        cls._enum_windows_proc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
        )
        cls.user32.EnumWindows.argtypes = [
            cls._enum_windows_proc, ctypes.wintypes.LPARAM
        ]
        cls.user32.EnumWindows.restype = ctypes.c_bool

        cls.user32.GetWindowTextW.argtypes = [
            ctypes.wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int
        ]
        cls.user32.GetWindowTextW.restype = ctypes.c_int

        cls.user32.GetClassNameW.argtypes = [
            ctypes.wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int
        ]
        cls.user32.GetClassNameW.restype = ctypes.c_int

        cls.user32.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
        cls.user32.IsWindowVisible.restype = ctypes.c_bool

        cls.user32.GetWindowRect.argtypes = [
            ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)
        ]
        cls.user32.GetWindowRect.restype = ctypes.c_bool

        cls.user32.GetWindowLongW.argtypes = [
            ctypes.wintypes.HWND, ctypes.c_int
        ]
        cls.user32.GetWindowLongW.restype = ctypes.c_long

        cls.user32.IsIconic.argtypes = [ctypes.wintypes.HWND]
        cls.user32.IsIconic.restype = ctypes.c_bool
        cls.user32.IsZoomed.argtypes = [ctypes.wintypes.HWND]
        cls.user32.IsZoomed.restype = ctypes.c_bool

        cls.user32.IsWindow.argtypes = [ctypes.wintypes.HWND]
        cls.user32.IsWindow.restype = ctypes.c_bool
        cls.user32.GetWindowTextLengthW.argtypes = [ctypes.wintypes.HWND]
        cls.user32.GetWindowTextLengthW.restype = ctypes.c_int
        cls.user32.SetForegroundWindow.argtypes = [ctypes.wintypes.HWND]
        cls.user32.SetForegroundWindow.restype = ctypes.c_bool

        cls.user32.ShowWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
        cls.user32.ShowWindow.restype = ctypes.c_bool

        cls.user32.BringWindowToTop.argtypes = [ctypes.wintypes.HWND]
        cls.user32.BringWindowToTop.restype = ctypes.c_bool

        cls.user32.MoveWindow.argtypes = [
            ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_bool
        ]
        cls.user32.MoveWindow.restype = ctypes.c_bool

        cls.user32.SetWindowPos.argtypes = [
            ctypes.wintypes.HWND, ctypes.wintypes.HWND,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint
        ]
        cls.user32.SetWindowPos.restype = ctypes.c_bool

        cls.user32.CloseWindow.argtypes = [ctypes.wintypes.HWND]
        cls.user32.CloseWindow.restype = ctypes.c_bool

        # ── Message sending ──
        cls.user32.SendMessageW.argtypes = [
            ctypes.wintypes.HWND, ctypes.wintypes.UINT,
            ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
        ]
        cls.user32.SendMessageW.restype = ctypes.wintypes.LPARAM

        cls.user32.PostMessageW.argtypes = [
            ctypes.wintypes.HWND, ctypes.wintypes.UINT,
            ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
        ]
        cls.user32.PostMessageW.restype = ctypes.c_bool

        # ── Process ──
        cls.kernel32.OpenProcess.argtypes = [
            ctypes.wintypes.DWORD, ctypes.c_bool, ctypes.wintypes.DWORD
        ]
        cls.kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE

        cls.kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        cls.kernel32.CloseHandle.restype = ctypes.c_bool

        cls.psapi = ctypes.windll.psapi
        cls.psapi.GetModuleBaseNameW.argtypes = [
            ctypes.wintypes.HANDLE, ctypes.wintypes.HANDLE,
            ctypes.c_wchar_p, ctypes.wintypes.DWORD
        ]
        cls.psapi.GetModuleBaseNameW.restype = ctypes.wintypes.DWORD

        cls.kernel32.TerminateProcess.argtypes = [
            ctypes.wintypes.HANDLE, ctypes.wintypes.UINT
        ]
        cls.kernel32.TerminateProcess.restype = ctypes.c_bool

        # ── ShellExecute ──
        cls.user32.ShellExecuteW = ctypes.windll.shell32.ShellExecuteW
        cls.user32.ShellExecuteW.argtypes = [
            ctypes.wintypes.HWND, ctypes.c_wchar_p, ctypes.c_wchar_p,
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int
        ]
        cls.user32.ShellExecuteW.restype = ctypes.c_void_p

        # ── Registry ──
        cls.advapi32.RegOpenKeyExW.argtypes = [
            ctypes.wintypes.HANDLE, ctypes.c_wchar_p, ctypes.wintypes.DWORD,
            ctypes.c_uint, ctypes.POINTER(ctypes.wintypes.HANDLE)
        ]
        cls.advapi32.RegOpenKeyExW.restype = ctypes.wintypes.LONG

        cls.advapi32.RegQueryValueExW.argtypes = [
            ctypes.wintypes.HANDLE, ctypes.c_wchar_p, ctypes.wintypes.LPDWORD,
            ctypes.POINTER(ctypes.wintypes.DWORD),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.wintypes.DWORD)
        ]
        cls.advapi32.RegQueryValueExW.restype = ctypes.wintypes.LONG

        cls.advapi32.RegCloseKey.argtypes = [ctypes.wintypes.HANDLE]
        cls.advapi32.RegCloseKey.restype = ctypes.wintypes.LONG

        # ── Input simulation ──
        cls.user32.SendInput.argtypes = [
            ctypes.wintypes.UINT,
            ctypes.c_void_p,  # INPUT array
            ctypes.c_int
        ]
        cls.user32.SendInput.restype = ctypes.wintypes.UINT

        # ── GDI for PID ──
        cls.user32.GetWindowThreadProcessId.argtypes = [
            ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.DWORD)
        ]
        cls.user32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD

        # ── GetDesktopWindow ──
        cls.user32.GetDesktopWindow.argtypes = []
        cls.user32.GetDesktopWindow.restype = ctypes.wintypes.HWND


# ── Helpers ────────────────────────────────────────────────────────────────

def _hkey_from_path(path: str) -> Tuple[int, str]:
    """Parse registry path like 'HKEY_CURRENT_USER\\Software\\Microsoft' into (hkey, subkey)."""
    parts = path.split("\\", 1)
    hive_name = parts[0].upper()
    subkey = parts[1] if len(parts) > 1 else ""

    hives = {
        "HKEY_CLASSES_ROOT": _HKEY_CLASSES_ROOT,
        "HKCR": _HKEY_CLASSES_ROOT,
        "HKEY_CURRENT_USER": _HKEY_CURRENT_USER,
        "HKCU": _HKEY_CURRENT_USER,
        "HKEY_LOCAL_MACHINE": _HKEY_LOCAL_MACHINE,
        "HKLM": _HKEY_LOCAL_MACHINE,
        "HKEY_USERS": _HKEY_USERS,
        "HKU": _HKEY_USERS,
        "HKEY_CURRENT_CONFIG": _HKEY_CURRENT_CONFIG,
        "HKCC": _HKEY_CURRENT_CONFIG,
    }
    hkey = hives.get(hive_name)
    if hkey is None:
        raise ValueError(f"Unknown registry hive: {hive_name}")
    return hkey, subkey


def _input_struct():
    """Create a ctypes structure for SendInput INPUT."""
    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.wintypes.WORD),
            ("wScan", ctypes.wintypes.WORD),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("time", ctypes.wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_void_p),
        ]

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.wintypes.LONG),
            ("dy", ctypes.wintypes.LONG),
            ("mouseData", ctypes.wintypes.DWORD),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("time", ctypes.wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_void_p),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("mi", _MOUSEINPUT),
            ("ki", _KEYBDINPUT),
        ]

    class _INPUT(ctypes.Structure):
        _fields_ = [
            ("type", ctypes.wintypes.DWORD),
            ("u", _INPUT_UNION),
        ]

    return _INPUT


# ── Public API ─────────────────────────────────────────────────────────────

def list_windows() -> List[WindowInfo]:
    """Enumerate all top-level windows on the desktop."""
    _Win32._init()
    results = []

    def _enum_callback(hwnd, lparam):
        # Check if it's a real window (has a visible parent or is desktop)
        length = _Win32.user32.GetWindowTextLengthW(hwnd)
        title_buf = ctypes.create_unicode_buffer(length + 1)
        _Win32.user32.GetWindowTextW(hwnd, title_buf, length + 1)
        title = title_buf.value or ""

        class_buf = ctypes.create_unicode_buffer(256)
        _Win32.user32.GetClassNameW(hwnd, class_buf, 256)
        class_name = class_buf.value or ""

        pid = ctypes.wintypes.DWORD(0)
        _Win32.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        visible = bool(_Win32.user32.IsWindowVisible(hwnd))
        minimized = bool(_Win32.user32.IsIconic(hwnd))
        maximized = bool(_Win32.user32.IsZoomed(hwnd))

        rect = ctypes.wintypes.RECT()
        _Win32.user32.GetWindowRect(hwnd, ctypes.byref(rect))

        results.append(WindowInfo(
            hwnd=hwnd,
            title=title,
            class_name=class_name,
            pid=pid.value,
            visible=visible,
            rect=(rect.left, rect.top, rect.right, rect.bottom),
            minimized=minimized,
            maximized=maximized,
        ))
        return True

    callback = _Win32._enum_windows_proc(_enum_callback)
    _Win32.user32.EnumWindows(callback, 0)
    return results


def find_windows(title_pattern: str = "", class_pattern: str = "",
                 visible_only: bool = False) -> List[WindowInfo]:
    """Find windows matching title or class pattern (case-insensitive substring)."""
    all_wins = list_windows()
    results = []
    title_lower = title_pattern.lower()
    class_lower = class_pattern.lower()

    for w in all_wins:
        if visible_only and not w.visible:
            continue
        if title_pattern and title_lower not in w.title.lower():
            continue
        if class_pattern and class_lower not in w.class_name.lower():
            continue
        results.append(w)
    return results


def get_window_info(hwnd: int) -> Optional[WindowInfo]:
    """Get info for a specific window handle."""
    for w in list_windows():
        if w.hwnd == hwnd:
            return w
    return None


def focus_window(hwnd: int) -> bool:
    """Bring a window to foreground (activates it)."""
    _Win32._init()

    # Check if window is valid
    if not _Win32.user32.IsWindow(hwnd):
        return False

    # If minimized, restore first
    if _Win32.user32.IsIconic(hwnd):
        _Win32.user32.ShowWindow(hwnd, _SW_RESTORE)

    # Try multiple approaches
    _Win32.user32.BringWindowToTop(hwnd)
    _Win32.user32.SetForegroundWindow(hwnd)

    time.sleep(0.05)
    return True


def move_window(hwnd: int, x: int, y: int, w: int, h: int) -> bool:
    """Move and resize a window. Use 0 for any dimension to keep current size."""
    _Win32._init()

    if not _Win32.user32.IsWindow(hwnd):
        return False

    rect = ctypes.wintypes.RECT()
    _Win32.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    cw = rect.right - rect.left
    ch = rect.bottom - rect.top

    nx = x if x >= 0 else rect.left
    ny = y if y >= 0 else rect.top
    nw = w if w > 0 else cw
    nh = h if h > 0 else ch

    return bool(_Win32.user32.MoveWindow(hwnd, nx, ny, nw, nh, True))


def close_window(hwnd: int) -> bool:
    """Send close message to a window."""
    _Win32._init()
    if not _Win32.user32.IsWindow(hwnd):
        return False
    _Win32.user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
    return True


def send_keys(hwnd: int, text: str) -> bool:
    """Send text to a window via WM_SETTEXT + WM_CHAR messages."""
    _Win32._init()
    if not _Win32.user32.IsWindow(hwnd):
        return False

    # Set window text directly
    buf = ctypes.create_unicode_buffer(text)
    _Win32.user32.SendMessageW(hwnd, _WM_SETTEXT, 0, ctypes.addressof(buf))
    return True


def send_input_keys(text: str) -> int:
    """Send keystrokes via SendInput (global, no target window needed).
    
    Returns number of events sent.
    """
    _Win32._init()
    INPUT = _input_struct()
    total = 0

    for ch in text:
        vk = ord(ch.upper())
        # Key down
        ki = INPUT()
        ki.type = _INPUT_KEYBOARD
        ki.u.ki.wVk = vk
        ki.u.ki.dwFlags = 0
        ret = _Win32.user32.SendInput(1, ctypes.byref(ki), ctypes.sizeof(INPUT))
        total += ret

        # Key up
        ki.u.ki.dwFlags = _KEYEVENTF_KEYUP
        ret = _Win32.user32.SendInput(1, ctypes.byref(ki), ctypes.sizeof(INPUT))
        total += ret

    return total


def exec_process(path: str, args: str = "", show: int = 1) -> bool:
    """Execute a program via ShellExecute.

    Args:
        path: executable path
        args: command line arguments
        show: 1=normal, 3=maximized, 7=minimized
    """
    _Win32._init()
    operation = "open"
    result = _Win32.user32.ShellExecuteW(
        0, operation, path, args, None, show
    )
    # ShellExecute returns value > 32 on success
    return result > 32


def read_registry(path: str, value_name: str = "") -> dict:
    """Read a registry value.

    Args:
        path: e.g. 'HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion'
        value_name: specific value name, or '' for default value
    """
    _Win32._init()
    hkey, subkey = _hkey_from_path(path)

    hkey_handle = ctypes.wintypes.HANDLE(0)
    ret = _Win32.advapi32.RegOpenKeyExW(
        hkey, subkey, 0, _KEY_READ, ctypes.byref(hkey_handle)
    )
    if ret != 0:
        return {"error": f"RegOpenKeyEx failed: {ret}", "path": path}

    try:
        # First call to get size
        type_buf = ctypes.wintypes.DWORD(0)
        size_buf = ctypes.wintypes.DWORD(0)
        ret = _Win32.advapi32.RegQueryValueExW(
            hkey_handle, value_name, None,
            ctypes.byref(type_buf), None, ctypes.byref(size_buf)
        )
        if ret != 0:
            return {"error": f"Value not found: {ret}", "path": path, "value": value_name}

        # Second call to read data
        data = ctypes.create_string_buffer(size_buf.value)
        ret = _Win32.advapi32.RegQueryValueExW(
            hkey_handle, value_name, None,
            ctypes.byref(type_buf), data, ctypes.byref(size_buf)
        )
        if ret != 0:
            return {"error": f"RegQueryValueEx failed: {ret}"}

        if type_buf.value == _REG_SZ:
            # Null-terminated wide string
            raw = data.raw[:size_buf.value]
            try:
                value = raw.decode("utf-16-le").rstrip("\x00")
            except:
                value = repr(raw)
        elif type_buf.value == _REG_DWORD:
            value = struct.unpack("<I", data.raw[:4])[0]
        elif type_buf.value == _REG_BINARY:
            value = list(data.raw[:size_buf.value])
        else:
            value = repr(data.raw[:size_buf.value])

        return {
            "path": path,
            "value_name": value_name or "(default)",
            "type": type_buf.value,
            "value": value,
        }
    finally:
        _Win32.advapi32.RegCloseKey(hkey_handle)


def window_click(hwnd: int, x: int, y: int, button: str = "left") -> bool:
    """Send a mouse click to a window at relative coordinates.

    Uses PostMessage to send WM_LBUTTONDOWN/UP to a specific window.
    The window doesn't need to be in focus.

    Args:
        hwnd: window handle
        x, y: coordinates relative to window client area
        button: 'left' or 'right'
    """
    _Win32._init()
    if not _Win32.user32.IsWindow(hwnd):
        return False

    # Pack coordinates into LPARAM (lParam = MAKELPARAM(x, y))
    lparam = (y << 16) | (x & 0xFFFF)

    if button == "left":
        _Win32.user32.PostMessageW(hwnd, _WM_LBUTTONDOWN, 0x0001, lparam)
        time.sleep(0.01)
        _Win32.user32.PostMessageW(hwnd, _WM_LBUTTONUP, 0x0000, lparam)
    else:
        _Win32.user32.PostMessageW(hwnd, _WM_RBUTTONDOWN, 0x0001, lparam)
        time.sleep(0.01)
        _Win32.user32.PostMessageW(hwnd, _WM_RBUTTONUP, 0x0000, lparam)

    return True


def minimize_window(hwnd: int) -> bool:
    """Minimize a window."""
    _Win32._init()
    return bool(_Win32.user32.ShowWindow(hwnd, _SW_MINIMIZE))


def maximize_window(hwnd: int) -> bool:
    """Maximize a window."""
    _Win32._init()
    return bool(_Win32.user32.ShowWindow(hwnd, _SW_SHOWMAXIMIZED))


def restore_window(hwnd: int) -> bool:
    """Restore a minimized/maximized window."""
    _Win32._init()
    return bool(_Win32.user32.ShowWindow(hwnd, _SW_RESTORE))


def hide_window(hwnd: int) -> bool:
    """Hide a window."""
    _Win32._init()
    return bool(_Win32.user32.ShowWindow(hwnd, _SW_HIDE))


def show_window(hwnd: int) -> bool:
    """Show a hidden window."""
    _Win32._init()
    return bool(_Win32.user32.ShowWindow(hwnd, _SW_SHOW))


def get_desktop_resolution() -> Tuple[int, int]:
    """Get the current desktop resolution."""
    _Win32._init()
    w = _Win32.user32.GetSystemMetrics(0)
    h = _Win32.user32.GetSystemMetrics(1)
    return (w, h)


class WinDeep:
    """High-level Windows deep control interface."""
    
    @staticmethod
    def windows(title_filter: str = "") -> List[WindowInfo]:
        """List windows, optionally filtered by title."""
        all_wins = list_windows()
        if title_filter:
            lower = title_filter.lower()
            return [w for w in all_wins if lower in w.title.lower()]
        return [w for w in all_wins if w.visible]

    @staticmethod
    def focus(title: str) -> Optional[int]:
        """Focus a window by title substring. Returns hwnd or None."""
        matches = find_windows(title_pattern=title, visible_only=True)
        if not matches:
            return None
        hwnd = matches[0].hwnd
        focus_window(hwnd)
        return hwnd

    @staticmethod
    def send(title: str, text: str) -> bool:
        """Send text to a window by title."""
        matches = find_windows(title_pattern=title, visible_only=True)
        if not matches:
            return False
        return send_keys(matches[0].hwnd, text)

    @staticmethod
    def click(title: str, x: int, y: int) -> bool:
        """Click at coordinates in a window by title."""
        matches = find_windows(title_pattern=title, visible_only=True)
        if not matches:
            return False
        return window_click(matches[0].hwnd, x, y)

    @staticmethod
    def close(title: str) -> bool:
        """Close a window by title."""
        matches = find_windows(title_pattern=title, visible_only=True)
        if not matches:
            return False
        return close_window(matches[0].hwnd)

    @staticmethod
    def exec(path: str) -> bool:
        """Execute a program."""
        return exec_process(path)
