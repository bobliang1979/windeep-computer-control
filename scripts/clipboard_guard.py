"""clipboard_guard.py — Shared multi-format clipboard guard module.

Provides safe clipboard backup/restore with:
- Multi-format support (text, bitmaps, file references, custom formats)
- OpenClipboard timeout (prevents infinite blocking)
- WM_SETTEXT shortcut for edit controls (zero clipboard side-effects)
- Shared across winctl_mcp_server.py and computer_control_enhanced.py

Usage:
    from scripts.clipboard_guard import clipboard_guard, clipboard_paste_text

    # Safe paste with auto backup/restore
    with clipboard_guard():
        set_clipboard_text("hello")
        send_ctrl_v(hwnd)

    # Or use the combined helper
    clipboard_paste_text(hwnd, "hello", pid=1234)
"""

import ctypes
import time
from contextlib import contextmanager
from typing import Dict, Optional

# ── Constants ───────────────────────────────────────────────────

CF_UNICODETEXT = 13
CF_HDROP = 15
CF_DIB = 8
CF_BITMAP = 2

# Standard format names (for debugging)
FORMAT_NAMES = {
    1: "CF_TEXT", 2: "CF_BITMAP", 3: "CF_METAFILEPICT",
    4: "CF_SYLK", 5: "CF_DIF", 6: "CF_TIFF",
    7: "CF_OEMTEXT", 8: "CF_DIB", 9: "CF_PALETTE",
    10: "CF_PENDATA", 11: "CF_RIFF", 12: "CF_WAVE",
    13: "CF_UNICODETEXT", 14: "CF_ENHMETAFILE",
    15: "CF_HDROP", 16: "CF_LOCALE", 17: "CF_DIBV5",
}

# Class names for WM_SETTEXT shortcut
EDIT_CONTROL_CLASSES = [
    "Edit", "RichEdit", "RICHEDIT", "RICHEDIT50W",
    "_WEDIT", "TextBox", "Scintilla", "ScintillaNet",
    "CEdit", "TEdit", "QLineEdit", "TMemo",
    "Windows.UI.Xaml.Controls.TextBox",
    "System.Windows.Controls.TextBox",
]

CLIPBOARD_TIMEOUT_MS = 2000
CLIPBOARD_RETRY_INTERVAL_MS = 10


# ── Internal helpers ────────────────────────────────────────────

def _open_clipboard_with_timeout(timeout_ms: int = CLIPBOARD_TIMEOUT_MS) -> bool:
    """Open clipboard with retry loop instead of infinite block.

    If another process holds the clipboard lock, this avoids hanging
    forever by retrying every 10ms up to timeout_ms.
    """
    user32 = ctypes.windll.user32
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        if user32.OpenClipboard(None):
            return True
        time.sleep(CLIPBOARD_RETRY_INTERVAL_MS / 1000.0)
    return False


def _copy_global_mem(h_mem: int) -> Optional[bytes]:
    """Copy raw bytes from a global memory handle."""
    if not h_mem:
        return None
    kernel32 = ctypes.windll.kernel32
    size = kernel32.GlobalSize(h_mem)
    if not size:
        return None
    p = kernel32.GlobalLock(h_mem)
    if not p:
        return None
    try:
        buf = ctypes.create_string_buffer(size)
        ctypes.memmove(buf, p, size)
        return bytes(buf)
    finally:
        kernel32.GlobalUnlock(h_mem)


def _set_global_mem(data: bytes, flags: int = 0x2002) -> Optional[int]:
    """Allocate movable global memory and copy data into it.

    Returns handle suitable for SetClipboardData.
    The caller/Clipboard owns the handle after SetClipboardData.
    """
    if not data:
        return None
    kernel32 = ctypes.windll.kernel32
    h_mem = kernel32.GlobalAlloc(flags, len(data))
    if h_mem:
        p = kernel32.GlobalLock(h_mem)
        if p:
            ctypes.memmove(p, data, len(data))
            kernel32.GlobalUnlock(h_mem)
    return h_mem


def _enum_formats() -> list:
    """Enumerate all formats currently on the clipboard."""
    formats = []
    fmt = 0
    user32 = ctypes.windll.user32
    while True:
        fmt = user32.EnumClipboardFormats(fmt)
        if fmt == 0:
            break
        formats.append(fmt)
    return formats


# ── Core backup / restore ───────────────────────────────────────

def backup_clipboard(timeout_ms: int = CLIPBOARD_TIMEOUT_MS) -> Dict[int, bytes]:
    """Backup ALL clipboard formats to raw bytes.

    Handles: text (CF_UNICODETEXT), file references (CF_HDROP),
    device-independent bitmaps (CF_DIB), and any custom format
    whose data can be copied as raw bytes.

    Returns dict of {format_id: raw_bytes}. Empty dict if clipboard
    is locked by another process (caller should handle).
    """
    backup: Dict[int, bytes] = {}
    if not _open_clipboard_with_timeout(timeout_ms):
        return backup  # clipboard locked by another process

    try:
        formats = _enum_formats()
        for fmt in formats:
            h_data = ctypes.windll.user32.GetClipboardData(fmt)
            if h_data:
                raw = _copy_global_mem(h_data)
                if raw:
                    backup[fmt] = raw
    finally:
        ctypes.windll.user32.CloseClipboard()

    return backup


def restore_clipboard(backup: Dict[int, bytes], delay_ms: int = 50):
    """Restore clipboard from backup dict.

    Args:
        backup: Dict from backup_clipboard().
        delay_ms: Milliseconds to wait for the target app to
            consume the Ctrl+V before restoring. 50ms is generally
            sufficient for UI thread message processing.
    """
    if not backup:
        return

    # Let target app consume WM_PASTE / Ctrl+V
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)

    if not _open_clipboard_with_timeout():
        return

    try:
        ctypes.windll.user32.EmptyClipboard()
        for fmt, raw_data in backup.items():
            h_mem = _set_global_mem(raw_data)
            if h_mem:
                ctypes.windll.user32.SetClipboardData(fmt, h_mem)
                # System owns h_mem after SetClipboardData
    finally:
        ctypes.windll.user32.CloseClipboard()


# ── WM_SETTEXT shortcut ─────────────────────────────────────────

def try_wm_settext(hwnd: int, text: str) -> bool:
    """Try to set text on an edit control via WM_SETTEXT.

    This is the preferred path: zero clipboard side-effects,
    fastest possible write. Only works on edit/rich-edit controls
    and similar Win32 widgets.

    Returns True if WM_SETTEXT was sent and class was recognized.
    Does NOT guarantee the control processed it correctly — callers
    should fall back to clipboard_guard() + Ctrl+V.
    """
    import ctypes
    user32 = ctypes.windll.user32
    try:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        class_name = buf.value or ""

        if not any(ec in class_name for ec in EDIT_CONTROL_CLASSES):
            return False

        w_text = (text + "\0").encode("utf-16-le")
        # wParam=1 means "repaint after setting text"
        result = user32.SendMessageW(hwnd, 0x000C, 1, w_text)
        return bool(result)
    except Exception:
        return False


# ── Context manager ─────────────────────────────────────────────

@contextmanager
def clipboard_guard():
    """Context manager: backup ALL clipboard formats, yield, restore.

    On enter:  opens clipboard with timeout, enumerates ALL formats
               (text, images, file refs, custom), copies raw bytes.
    On yield:  caller can modify clipboard freely.
    On exit:   waits 50ms for target to consume data, then restores
               ALL original formats atomically.

    Example:
        with clipboard_guard():
            set_clipboard_text("my data")
            send_ctrl_v(hwnd)
        # clipboard is now restored to original state
    """
    backup = backup_clipboard()
    try:
        yield backup
    finally:
        restore_clipboard(backup, delay_ms=50)


# ── Set clipboard text (low-level helper) ───────────────────────

def set_clipboard_text(text: str) -> bool:
    """Write text to clipboard (CF_UNICODETEXT only).

    Call inside clipboard_guard() context for safe multi-format use.
    Returns True on success.
    """
    if not _open_clipboard_with_timeout():
        return False
    try:
        ctypes.windll.user32.EmptyClipboard()
        w_text = (text + "\0").encode("utf-16-le")
        h_mem = _set_global_mem(w_text)
        if h_mem:
            ctypes.windll.user32.SetClipboardData(CF_UNICODETEXT, h_mem)
            return True
        return False
    finally:
        ctypes.windll.user32.CloseClipboard()


# ── Combined paste helper ───────────────────────────────────────

def clipboard_paste_text(hwnd: int, text: str,
                         send_ctrl_v_via=None) -> dict:
    """Paste text with full clipboard guard and optimal strategy.

    Strategy:
        1. Try WM_SETTEXT (zero clipboard side-effects)
        2. clipboard_guard() + write clipboard + Ctrl+V

    Args:
        hwnd: Target window handle.
        text: Text to paste.
        send_ctrl_v_via: Optional callable (params) for sending
            Ctrl+V. Receives hwnd and text. If None, uses
            PostMessage for Ctrl+V.

    Returns:
        dict with success, method, length
    """
    # Strategy 1: WM_SETTEXT (zero clipboard)
    if try_wm_settext(hwnd, text):
        return {"success": True, "hwnd": hwnd,
                "length": len(text), "method": "wm_settext"}

    # Strategy 2: clipboard_guard() + Ctrl+V
    try:
        import ctypes
        with clipboard_guard():
            if not set_clipboard_text(text):
                return {"success": False, "error": "clipboard locked",
                        "fallback": "type_text"}

            # Send Ctrl+V
            user32 = ctypes.windll.user32
            user32.PostMessageW(hwnd, 0x0100, 0x11, 0)  # WM_KEYDOWN Ctrl
            user32.PostMessageW(hwnd, 0x0100, 0x56, 0)  # WM_KEYDOWN V
            user32.PostMessageW(hwnd, 0x0101, 0x56, 0)  # WM_KEYUP V
            user32.PostMessageW(hwnd, 0x0101, 0x11, 0)  # WM_KEYUP Ctrl

        return {"success": True, "hwnd": hwnd, "length": len(text),
                "method": "clipboard_guarded"}
    except Exception as e:
        return {"success": False, "error": str(e), "fallback": "type_text"}
