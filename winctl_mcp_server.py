# © 2026 BOBLIANG. All rights reserved.
#!/usr/bin/env python3
"""
winctl_mcp_server.py — MCP Server wrapping windeep's ctypes Win32 API.

Exposes native Windows desktop control as MCP tools for any MCP client (Hermes, Codex++, etc.).

Usage:
    # HTTP mode (recommended, port 59322)
    python winctl_mcp_server.py

    # Register with Hermes
    hermes mcp add winctl --url http://localhost:59322

    # Test via CLI
    python winctl_mcp_server.py list-windows
    python winctl_mcp_server.py screenshot --hwnd 0x1234

Dependencies: none (uses windeep's ctypes, stdlib only)
"""

import base64
import io
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Any, Optional

# Add parent dir so scripts/ and windeep/ are importable
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _THIS_DIR)

from scripts.clipboard_guard import clipboard_paste_text, try_wm_settext
from scripts.element_fingerprint import element_fingerprint, get_elements
try:
    from scripts.assertion_verifier import verify, capture_state
    HAS_ASSERTIONS = True
except ImportError:
    HAS_ASSERTIONS = False

try:
    from windeep.core import (
        list_windows, find_windows, get_window_info, focus_window,
        send_keys, send_input_keys, window_click, exec_process,
        get_desktop_resolution, minimize_window, maximize_window,
        restore_window, close_window, move_window, WindowInfo,
    )
    HAS_WINDEEP = True
except ImportError as e:
    HAS_WINDEEP = False
    _import_error = str(e)

try:
    from PIL import ImageGrab, Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

DEFAULT_PORT = 59322
WINCTL_VERSION = "0.1.0"

# ── Tool definitions ──────────────────────────────────────────

TOOLS = [
    {
        "name": "list_windows",
        "description": "List all visible top-level windows with HWND, PID, title, class, and bounds.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title_filter": {
                    "type": "string",
                    "description": "Optional case-insensitive title substring filter.",
                }
            },
        },
    },
    {
        "name": "get_window_info",
        "description": "Get detailed info about a specific window by HWND (decimal or hex).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hwnd": {
                    "type": "integer",
                    "description": "Window handle (decimal, or use 0x prefix for hex).",
                }
            },
            "required": ["hwnd"],
        },
    },
    {
        "name": "focus_window",
        "description": "Bring a window to the foreground by HWND. NOTE: this takes focus from the user.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hwnd": {
                    "type": "integer",
                    "description": "Window handle to focus.",
                }
            },
            "required": ["hwnd"],
        },
    },
    {
        "name": "click",
        "description": "Send a mouse click to a window at specified coordinates via PostMessage. Background-safe, no focus steal.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hwnd": {
                    "type": "integer",
                    "description": "Target window handle.",
                },
                "x": {
                    "type": "integer",
                    "description": "X coordinate relative to window top-left.",
                },
                "y": {
                    "type": "integer",
                    "description": "Y coordinate relative to window top-left.",
                },
                "button": {
                    "type": "string",
                    "enum": ["left", "right", "middle"],
                    "description": "Mouse button (default: left).",
                },
            },
            "required": ["hwnd", "x", "y"],
        },
    },
    {
        "name": "type_text",
        "description": "Send text to a window via WM_CHAR messages. Background-safe, no focus steal. Best for short text (<100 chars). For long text, prefer paste_text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hwnd": {
                    "type": "integer",
                    "description": "Target window handle.",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type.",
                },
            },
            "required": ["hwnd", "text"],
        },
    },
    {
        "name": "paste_text",
        "description": "Paste text via clipboard (SetClipboardData + WM_PASTE or Ctrl+V). Use for long text where WM_CHAR would be too slow.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hwnd": {
                    "type": "integer",
                    "description": "Target window handle.",
                },
                "text": {
                    "type": "string",
                    "description": "Text to paste.",
                },
            },
            "required": ["hwnd", "text"],
        },
    },
    {
        "name": "send_keys",
        "description": "Send a key combination (e.g. Ctrl+C, Alt+Tab) to a window. Keys are sequential press-release pairs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hwnd": {
                    "type": "integer",
                    "description": "Target window handle.",
                },
                "keys": {
                    "type": "string",
                    "description": "Key combination, e.g. 'ctrl+c', 'alt+tab', 'escape'.",
                },
            },
            "required": ["hwnd", "keys"],
        },
    },
    {
        "name": "move_window",
        "description": "Move and/or resize a window by HWND.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window handle."},
                "x": {"type": "integer", "description": "New X position (or -1 to keep)."},
                "y": {"type": "integer", "description": "New Y position (or -1 to keep)."},
                "w": {"type": "integer", "description": "New width (or -1 to keep)."},
                "h": {"type": "integer", "description": "New height (or -1 to keep)."},
            },
            "required": ["hwnd"],
        },
    },
    {
        "name": "close_window",
        "description": "Send WM_CLOSE to a window. Prefer this over kill_app for graceful close.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window handle to close."},
            },
            "required": ["hwnd"],
        },
    },
    {
        "name": "minimize_window",
        "description": "Minimize a window.",
        "inputSchema": {"type": "object", "properties": {"hwnd": {"type": "integer"}}, "required": ["hwnd"]},
    },
    {
        "name": "maximize_window",
        "description": "Maximize a window.",
        "inputSchema": {"type": "object", "properties": {"hwnd": {"type": "integer"}}, "required": ["hwnd"]},
    },
    {
        "name": "restore_window",
        "description": "Restore a minimized/maximized window to normal.",
        "inputSchema": {"type": "object", "properties": {"hwnd": {"type": "integer"}}, "required": ["hwnd"]},
    },
    {
        "name": "launch",
        "description": "Launch an executable or open a file/URL via ShellExecute.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Executable path, file path, or URL."},
                "args": {"type": "string", "description": "Optional command-line arguments."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "screenshot",
        "description": "Take a screenshot of a specific window (by HWND) or the full screen. Returns base64 PNG.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hwnd": {
                    "type": "integer",
                    "description": "Optional window handle. Omit for full-screen screenshot.",
                },
            },
        },
    },
    {
        "name": "desktop_info",
        "description": "Get desktop resolution and basic system info.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "find_windows",
        "description": "Find windows by title and/or class name pattern.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title_pattern": {"type": "string", "description": "Case-insensitive title substring."},
                "class_pattern": {"type": "string", "description": "Case-insensitive class name substring."},
            },
        },
    },
    {
        "name": "capture_state",
        "description": "Capture current UI state (tree hash + element count) for assertion verification. Use before and after actions, then call verify().",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Window handle to capture state for."},
            },
        },
    },
    {
        "name": "verify",
        "description": "Run structured assertions against before/after states. Supports: hash_change (tree changed), element_appeared (text), element_disappeared (text), text_contains (text).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "before": {"type": "object", "description": "State dict from capture_state (before action)."},
                "after": {"type": "object", "description": "State dict from capture_state (after action)."},
                "assertions": {
                    "type": "array",
                    "description": "List of assertion objects: [{\"assertion\": \"hash_change\"}, {\"assertion\": \"element_appeared\", \"text\": \"OK\"}]",
                    "items": {"type": "object"},
                },
            },
            "required": ["before", "after", "assertions"],
        },
    },
    {
        "name": "ocr_find",
        "description": "Find text in a screenshot using Windows native OCR. Covers UIA blind spots (Electron, WebView, custom canvas). Returns center coordinates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "screenshot_b64": {"type": "string", "description": "Base64 PNG screenshot."},
                "text": {"type": "string", "description": "Text to search for (case-insensitive)."},
            },
            "required": ["screenshot_b64", "text"],
        },
    },
    {
        "name": "ocr_available",
        "description": "Check if Windows native OCR engine is available (Windows 10+).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "smart_find",
        "description": "Multi-strategy element location: UIA exact → UIA fuzzy → OCR → position. Returns element_index and/or coordinates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_text": {"type": "string", "description": "Text to find."},
                "tree_json": {"type": "string", "description": "Optional: UI tree as JSON string (from get_window_state)."},
                "screenshot_b64": {"type": "string", "description": "Optional: base64 screenshot for OCR."},
                "last_x": {"type": "integer", "description": "Optional: last known X for position matching."},
                "last_y": {"type": "integer", "description": "Optional: last known Y for position matching."},
            },
            "required": ["target_text"],
        },
    },
    {
        "name": "smart_click",
        "description": "Find element by text and click it. Uses multi-strategy matching (UIA → OCR → fuzzy). Single command for 'find the X button and click it'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pid": {"type": "integer", "description": "Target process ID."},
                "target_text": {"type": "string", "description": "Text of the element to find and click."},
                "window_id": {"type": "integer", "description": "Optional window handle."},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "Mouse button."},
            },
            "required": ["pid", "target_text"],
        },
    },
    {
        "name": "get_energy",
        "description": "Get the current ATP energy level of the control stack. Returns level (0-100), zone (high/warning/coma), action history length, and time since last failure. Energy is derived from recent failure rate via the ATP metabolic model.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


# ── Tool handlers ─────────────────────────────────────────────

def _hwnd_to_int(hwnd):
    """Convert HWND to int (handle both decimal and hex input)."""
    if isinstance(hwnd, str):
        return int(hwnd, 16) if hwnd.startswith(("0x", "0X")) else int(hwnd)
    return hwnd


def _window_info_to_dict(win: WindowInfo) -> dict:
    # rect is a tuple (left, top, right, bottom) from windeep
    r = win.rect
    return {
        "hwnd": win.hwnd,
        "title": win.title,
        "class_name": win.class_name,
        "pid": win.pid,
        "rect": {"x": r[0], "y": r[1], "w": r[2] - r[0], "h": r[3] - r[1]},
        "visible": win.visible,
    }


def handle_list_windows(args: dict) -> dict:
    title_filter = args.get("title_filter", "")
    wins = list_windows()
    if title_filter:
        title_filter = title_filter.lower()
        wins = [w for w in wins if title_filter in w.title.lower()]
    return {"windows": [_window_info_to_dict(w) for w in wins], "count": len(wins)}


def handle_get_window_info(args: dict) -> dict:
    hwnd = _hwnd_to_int(args["hwnd"])
    info = get_window_info(hwnd)
    if info:
        return {"window": _window_info_to_dict(info)}
    return {"error": f"Window 0x{hwnd:x} not found"}


def handle_focus_window(args: dict) -> dict:
    hwnd = _hwnd_to_int(args["hwnd"])
    ok = focus_window(hwnd)
    return {"success": ok, "hwnd": hwnd}


def handle_click(args: dict) -> dict:
    hwnd = _hwnd_to_int(args["hwnd"])
    button = args.get("button", "left")
    ok = window_click(hwnd, args["x"], args["y"], button)
    return {"success": ok, "hwnd": hwnd, "x": args["x"], "y": args["y"], "button": button}


def handle_type_text(args: dict) -> dict:
    hwnd = _hwnd_to_int(args["hwnd"])
    text = args["text"]
    ok = send_keys(hwnd, text)
    return {"success": ok, "hwnd": hwnd, "length": len(text)}


def handle_paste_text(args: dict) -> dict:
    """Paste via clipboard. Uses shared clipboard_guard for safe multi-format handling.

    Strategy: WM_SETTEXT (zero clipboard) -> clipboard_guard() + Ctrl+V.
    """
    hwnd = _hwnd_to_int(args["hwnd"])
    text = args["text"]
    return clipboard_paste_text(hwnd, text)


def handle_send_keys(args: dict) -> dict:
    hwnd = _hwnd_to_int(args["hwnd"])
    keys = args["keys"].lower()

    # Map common key combos to virtual key codes
    key_map = {
        "ctrl": 0x11, "shift": 0x10, "alt": 0x12,
        "enter": 0x0D, "return": 0x0D, "tab": 0x09,
        "escape": 0x1B, "esc": 0x1B, "space": 0x20,
        "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
        "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
        "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
        "a": 0x41, "b": 0x42, "c": 0x43, "d": 0x44, "e": 0x45,
        "f": 0x46, "g": 0x47, "h": 0x48, "i": 0x49, "j": 0x4A,
        "k": 0x4B, "l": 0x4C, "m": 0x4D, "n": 0x4E, "o": 0x4F,
        "p": 0x50, "q": 0x51, "r": 0x52, "s": 0x53, "t": 0x54,
        "u": 0x55, "v": 0x56, "w": 0x57, "x": 0x58, "y": 0x59, "z": 0x5A,
        "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
        "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
        "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
        "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
        "f11": 0x7A, "f12": 0x7B, "win": 0x5B, "apps": 0x5D,
    }

    import ctypes
    user32 = ctypes.windll.user32

    parts = keys.split("+")
    # Press modifiers in order, then the final key
    for i, part in enumerate(parts):
        vk = key_map.get(part.strip())
        if vk is None:
            return {"success": False, "error": f"Unknown key: {part}"}
        is_last = (i == len(parts) - 1)
        # For the last key in a combo, press and release
        # For modifiers (all but last), press only
        user32.PostMessageW(hwnd, 0x0100, vk, 0)  # WM_KEYDOWN
        if is_last:
            user32.PostMessageW(hwnd, 0x0101, vk, 0)  # WM_KEYUP

    # Release modifiers in reverse order (skip last which was already released)
    for part in reversed(parts[:-1]):
        vk = key_map.get(part.strip())
        if vk:
            user32.PostMessageW(hwnd, 0x0101, vk, 0)  # WM_KEYUP

    return {"success": True, "keys": keys, "hwnd": hwnd}


def handle_move_window(args: dict) -> dict:
    hwnd = _hwnd_to_int(args["hwnd"])
    x, y = args.get("x", -1), args.get("y", -1)
    w, h = args.get("w", -1), args.get("h", -1)
    ok = move_window(hwnd, x, y, w, h)
    return {"success": ok, "hwnd": hwnd}


def handle_close_window(args: dict) -> dict:
    hwnd = _hwnd_to_int(args["hwnd"])
    ok = close_window(hwnd)
    return {"success": ok, "hwnd": hwnd}


def handle_minimize(args: dict) -> dict:
    hwnd = _hwnd_to_int(args["hwnd"])
    return {"success": minimize_window(hwnd), "hwnd": hwnd}


def handle_maximize(args: dict) -> dict:
    hwnd = _hwnd_to_int(args["hwnd"])
    return {"success": maximize_window(hwnd), "hwnd": hwnd}


def handle_restore(args: dict) -> dict:
    hwnd = _hwnd_to_int(args["hwnd"])
    return {"success": restore_window(hwnd), "hwnd": hwnd}


def handle_launch(args: dict) -> dict:
    path = args["path"]
    args_str = args.get("args", "")
    ok = exec_process(path, args_str)
    return {"success": ok, "path": path}


def handle_screenshot(args: dict) -> dict:
    import ctypes
    hwnd = args.get("hwnd", 0)

    if hwnd:
        hwnd = _hwnd_to_int(hwnd)
        # Capture specific window using PrintWindow
        gdi_allocated = False
        dc = mem_dc = bmp = old = None
        try:
            info = get_window_info(hwnd)
            if not info:
                return {"error": f"Window 0x{hwnd:x} not found"}
            r = info.rect
            w, h = r.width, r.height
            if w <= 0 or h <= 0:
                return {"error": "Window has zero dimensions"}

            # Use PrintWindow API
            user32 = ctypes.windll.user32
            dc = user32.GetDC(hwnd)
            if not dc:
                return handle_screenshot({})  # full-screen fallback

            # Create compatible DC and bitmap
            gdi32 = ctypes.windll.gdi32
            mem_dc = gdi32.CreateCompatibleDC(dc)
            bmp = gdi32.CreateCompatibleBitmap(dc, w, h)
            old = gdi32.SelectObject(mem_dc, bmp)
            gdi_allocated = True
            user32.PrintWindow(hwnd, mem_dc, 0)

            # Convert to PIL Image
            from PIL import Image
            bmp_info = ctypes.create_string_buffer(64)
            gdi32.GetObjectW(bmp, 64, bmp_info)
            # Read pixel data
            bits = ctypes.create_string_buffer(w * h * 4)
            gdi32.GetBitmapBits(bmp, w * h * 4, bits)
            img = Image.frombuffer("BGRA", (w, h), bits, "raw", "BGRA", 0, 1)

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            return {"base64": b64, "format": "png", "width": w, "height": h,
                    "kb": round(len(buf.getvalue()) / 1024, 1)}
        except Exception as e:
            return {"error": f"Window screenshot failed: {e}"}
        finally:
            # GDI cleanup — Codex++ fix: prevents handle leak on exception
            if gdi_allocated:
                try:
                    if old: gdi32.SelectObject(mem_dc, old)
                    if bmp: gdi32.DeleteObject(bmp)
                    if mem_dc: gdi32.DeleteDC(mem_dc)
                except Exception:
                    pass
            if dc:
                try: user32.ReleaseDC(hwnd, dc)
                except Exception: pass

    # Full-screen capture
    if HAS_PIL:
        img = ImageGrab.grab()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return {"base64": b64, "format": "png", "width": img.width, "height": img.height,
                "kb": round(len(buf.getvalue()) / 1024, 1)}
    return {"error": "PIL not available for screenshot"}


def handle_desktop_info(args: dict) -> dict:
    w, h = get_desktop_resolution()
    return {
        "resolution": f"{w}x{h}",
        "width": w, "height": h,
        "version": WINCTL_VERSION,
        "has_windeep": HAS_WINDEEP,
        "has_pil": HAS_PIL,
        "tools_count": len(TOOLS),
        "has_pil": HAS_PIL,
        "tools_count": len(TOOLS),
    }


def handle_find_windows(args: dict) -> dict:
    title = args.get("title_pattern", "")
    cls = args.get("class_pattern", "")
    wins = find_windows(title_pattern=title, class_pattern=cls)
    return {"windows": [_window_info_to_dict(w) for w in wins], "count": len(wins)}


# ── P2 Assertion handlers ────────────────────────────────────

def handle_capture_state(args: dict) -> dict:
    hwnd = args.get("hwnd", 0)
    try:
        info = get_window_info(hwnd) if hwnd else None
        state = capture_state(pid=info.pid if info else 0,
                              window_id=hwnd)
        return {"state": state}
    except Exception as e:
        return {"error": str(e)}


def handle_verify_assertions(args: dict) -> dict:
    before = args.get("before", {})
    after = args.get("after", {})
    assertions_raw = args.get("assertions", [])
    # Parse assertions: each is {"assertion": "...", "text": "..."}
    parsed = []
    for a in assertions_raw:
        if isinstance(a, dict):
            name = a.get("assertion", "")
            params = {k: v for k, v in a.items() if k != "assertion"}
            parsed.append((name, params))
        else:
            parsed.append((str(a), {}))
    result = verify(before, after, parsed)
    return result


# ── OCR & Smart matching handlers ────────────────────────────

def handle_ocr_find(args: dict) -> dict:
    from scripts.ocr_finder import ocr_find_text
    return ocr_find_text(args.get("text", ""), args.get("screenshot_b64", ""))


def handle_ocr_available(args: dict) -> dict:
    from scripts.ocr_finder import ocr_available
    return {"available": ocr_available()}


def handle_smart_find(args: dict) -> dict:
    from scripts.smart_matcher import smart_find
    tree = None
    tree_json = args.get("tree_json", "")
    if tree_json:
        tree = json.loads(tree_json)
    return smart_find(
        target_text=args.get("target_text", ""),
        tree=tree,
        screenshot_b64=args.get("screenshot_b64"),
        last_x=args.get("last_x"),
        last_y=args.get("last_y"),
    )
def handle_smart_click(args: dict) -> dict:
    """Find element by text and click it."""
    from scripts.smart_matcher import smart_click
    return smart_click(
        pid=args["pid"],
        target_text=args["target_text"],
        window_id=args.get("window_id", 0),
        button=args.get("button", "left"),
    )


def handle_get_energy(args: dict) -> dict:
    """Get ATP energy level of the control stack."""
    try:
        from scripts.shared_ui_state import get_state
        state = get_state()
        energy = state.get_energy()
        return {
            "energy_level": energy["level"],
            "zone": energy["zone"],
            "history_len": energy["history_len"],
            "last_failure_at": energy["last_failure_at"],
            "interpretation": (
                "high energy: full execution with verification"
                if energy["zone"] == "high"
                else "warning: streamlined verify, skip screenshot diff"
                if energy["zone"] == "warning"
                else "coma: replay analysis of accumulated failures"
            ),
        }
    except (ImportError, Exception) as e:
        return {"error": f"ATP energy unavailable: {e}"}


HANDLERS = {
    "list_windows": handle_list_windows,
    "get_window_info": handle_get_window_info,
    "focus_window": handle_focus_window,
    "click": handle_click,
    "type_text": handle_type_text,
    "paste_text": handle_paste_text,
    "send_keys": handle_send_keys,
    "move_window": handle_move_window,
    "close_window": handle_close_window,
    "minimize_window": handle_minimize,
    "maximize_window": handle_maximize,
    "restore_window": handle_restore,
    "launch": handle_launch,
    "screenshot": handle_screenshot,
    "desktop_info": handle_desktop_info,
    "find_windows": handle_find_windows,
    "capture_state": handle_capture_state,
    "verify": handle_verify_assertions,
    "ocr_find": handle_ocr_find,
    "ocr_available": handle_ocr_available,
    "smart_find": handle_smart_find,
    "smart_click": handle_smart_click,
    "get_energy": handle_get_energy,
}


# ── MCP HTTP Server ───────────────────────────────────────────

class MCPRequestHandler(BaseHTTPRequestHandler):

    def _send_json(self, data: Any, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length > 0:
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))
        return {}

    def do_GET(self):
        if self.path == "/tools" or self.path == "/":
            self._send_json({"tools": TOOLS})
        elif self.path == "/health":
            self._send_json({
                "status": "ok",
                "version": WINCTL_VERSION,
                "has_windeep": HAS_WINDEEP,
                "has_pil": HAS_PIL,
            })
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        if self.path == "/tools/call":
            body = self._read_body()
            name = body.get("name", "")
            arguments = body.get("arguments", {})

            handler = HANDLERS.get(name)
            if not handler:
                self._send_json({"error": f"Unknown tool: {name}"}, 404)
                return

            try:
                result = handler(arguments)
                # Wrap in MCP content format
                self._send_json({
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
                })
            except Exception as e:
                self._send_json({
                    "content": [{"type": "text", "text": json.dumps({
                        "error": str(e), "tool": name, "arguments": arguments
                    })}]
                })
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        """Quiet logging — only log errors."""
        if args and len(args) > 0 and "400" in str(args[0]):
            super().log_message(format, *args)


# ── Threaded HTTP Server (Codex++ fix: concurrent request handling) ──

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Threaded MCP server — handles concurrent tool calls without blocking."""
    allow_reuse_address = True
    daemon_threads = True


# ── CLI mode (one-shot commands) ──────────────────────────────

def _cli_execute(tool_name: str, **kwargs) -> dict:
    handler = HANDLERS.get(tool_name)
    if not handler:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        return handler(kwargs)
    except Exception as e:
        return {"error": str(e)}


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="winctl MCP Server — Win32 desktop control as MCP tools",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"HTTP server port (default: {DEFAULT_PORT})")
    parser.add_argument("--daemon", action="store_true",
                        help="Run as HTTP server (default if no subcommand)")
    parser.add_argument("--json", action="store_true",
                        help="Pretty-print JSON output")

    # Subcommands for one-shot CLI mode
    sub = parser.add_subparsers(dest="command")

    for tool_def in TOOLS:
        name = tool_def["name"]
        p = sub.add_parser(name, help=tool_def["description"])
        schema = tool_def["inputSchema"]
        for prop_name, prop_schema in schema.get("properties", {}).items():
            flag = f"--{prop_name.replace('_', '-')}"
            if prop_schema.get("type") == "integer":
                p.add_argument(flag, type=int, required=prop_name in schema.get("required", []))
            elif prop_schema.get("type") == "array":
                p.add_argument(flag, nargs="+")
            else:
                p.add_argument(flag, type=str, required=prop_name in schema.get("required", []))

    args = parser.parse_args()

    if args.command:
        # CLI one-shot mode
        kwargs = {}
        for tool_def in TOOLS:
            if tool_def["name"] == args.command:
                for prop_name in tool_def["inputSchema"].get("properties", {}):
                    val = getattr(args, prop_name.replace("-", "_"), None)
                    if val is not None:
                        kwargs[prop_name] = val
                break
        result = _cli_execute(args.command, **kwargs)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(result, ensure_ascii=False))
        return

    # HTTP server mode
    if not HAS_WINDEEP:
        print(f"ERROR: windeep not available: {_import_error}", file=sys.stderr)
        print("Make sure windeep/core.py is accessible.", file=sys.stderr)
        sys.exit(1)

    server = ThreadedHTTPServer(("127.0.0.1", args.port), MCPRequestHandler)
    print(f"winctl MCP Server v{WINCTL_VERSION} on http://127.0.0.1:{args.port}", file=sys.stderr)
    print(f"Tools: {len(TOOLS)} registered", file=sys.stderr)
    print(f"windeep: {'OK' if HAS_WINDEEP else 'MISSING'}, PIL: {'OK' if HAS_PIL else 'MISSING'}", file=sys.stderr)
    print(f"Register with: hermes mcp add winctl --url http://127.0.0.1:{args.port}", file=sys.stderr)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)
        server.server_close()


if __name__ == "__main__":
    main()
