# © 2026 BOBLIANG. All rights reserved.
#!/usr/bin/env python3
"""
computer_control_enhanced.py — Enhanced computer control with bytebot-inspired patterns.

P0 features (Codex++ plan, modular):
  UiTreeCache      → from scripts.ui_tree_cache import UiTreeCache
  Element Fingerprint → from scripts.element_fingerprint import element_fingerprint
  Type-text merge  → set_value > paste > type_text fallback

Usage:
    python computer_control_enhanced.py click --pid 1234 --element 7
    python computer_control_enhanced.py type-text --pid 1234 "hello"
    python computer_control_enhanced.py paste --pid 1234 "long text..."
    python computer_control_enhanced.py type-keys --pid 1234 ctrl c
    python computer_control_enhanced.py cache-info --clear
"""

import argparse
import base64
import io
import json
import os
import queue
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

# SendInput (real keystrokes for XAML/UWP/SPA where PostMessage fails)
try:
    from scripts.sendinput import send_click as _si_click, send_type_text as _si_type
    HAS_SENDINPUT = True
except ImportError:
    HAS_SENDINPUT = False

# ---- Modular components (Codex++ created) ----
SCRIPTS_DIR = Path(__file__).parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR.parent))  # so 'from scripts.X' works
try:
    from scripts.ui_tree_cache import UiTreeCache
    from scripts.element_fingerprint import element_fingerprint
    from scripts.layout_knowledge import get_layout_knowledge
    from scripts.clipboard_guard import (
        clipboard_guard as shared_clipboard_guard,
        clipboard_paste_text as shared_clipboard_paste,
        try_wm_settext,
        backup_clipboard,
        restore_clipboard,
    )
    HAS_MODULES = True
except ImportError:
    HAS_MODULES = False

# ---- Compression ----
try:
    from compress_image import compress_to_target
    HAS_COMPRESS = True
except ImportError:
    HAS_COMPRESS = False

DEFAULT_SETTLE_MS = 750
DEFAULT_COMPRESS_KB = 512
MAX_RETRIES = 2
MCP_HTTP_PORT = 59321
_MCP_UIA_PORT = 59323  # cua-driver-uia.exe MCP endpoint (for High IL windows)
SETTLE_HISTORY: dict[str, list[float]] = {}

# ---- Global cache instance ----
_ui_cache = UiTreeCache(ttl_ms=2000) if HAS_MODULES else None


# ══════════════════════════════════════════════════════════════
# Adaptive settle delay
# ══════════════════════════════════════════════════════════════

def _adaptive_settle(pid: int, action_type: str) -> int:
    key = f"{pid}:{action_type}"
    times = SETTLE_HISTORY.get(key, [])
    if not times:
        return DEFAULT_SETTLE_MS
    median = sorted(times)[len(times) // 2]
    return max(200, min(int(median * 1.5), 2000))


def _record_settle(pid: int, action_type: str, actual_ms: float):
    key = f"{pid}:{action_type}"
    if key not in SETTLE_HISTORY:
        SETTLE_HISTORY[key] = []
    SETTLE_HISTORY[key].append(actual_ms)
    if len(SETTLE_HISTORY[key]) > 20:
        SETTLE_HISTORY[key] = SETTLE_HISTORY[key][-20:]


# ══════════════════════════════════════════════════════════════
# MCP call helpers
# ══════════════════════════════════════════════════════════════

def _call_mcp_http(tool: str, params: dict, port: int = None) -> Optional[dict]:
    try:
        import urllib.request
        p = port or MCP_HTTP_PORT
        body = json.dumps({"name": tool, "arguments": params}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{p}/tools/call",
            data=body, headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _call_mcp_cli(action: str, **params) -> Optional[dict]:
    try:
        result = subprocess.run(
            ["hermes", "mcp", "call", "cua-driver", action, json.dumps(params)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return None


def _get_window_integrity(hwnd: int) -> Optional[int]:
    """Get window integrity level via ctypes (no pywin32 dependency).

    Returns:
        0x2000 = Medium (normal user)
        0x3000 = High (admin)
        0x4000 = System
        None  = unable to determine (treat as High for safety)
    """
    try:
        import ctypes, ctypes.wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32

        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        h_process = kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
        if not h_process:
            return 0x3000  # Can't query → likely high-integrity
        try:
            h_token = ctypes.c_void_p()
            if not advapi32.OpenProcessToken(h_process, 0x0008, ctypes.byref(h_token)):
                return 0x3000
            try:
                # TokenIntegrityLevel = 25
                buf = ctypes.create_string_buffer(256)
                ret_len = ctypes.wintypes.DWORD()
                if advapi32.GetTokenInformation(h_token, 25, buf, 256, ctypes.byref(ret_len)):
                    # SID structure: Revision(1) + SubAuthorityCount(1) + IdentifierAuthority(6) + SubAuthority(N)
                    # Integrity level is the last SubAuthority value
                    sub_auth_count = buf[1]
                    if sub_auth_count > 0:
                        # SubAuthorities start at offset 8, each is 4 bytes
                        offset = 8 + (sub_auth_count - 1) * 4
                        import struct
                        il = struct.unpack_from('<I', buf, offset)[0]
                        return il
            finally:
                kernel32.CloseHandle(h_token)
        finally:
            kernel32.CloseHandle(h_process)
    except Exception:
        return 0x3000
    return None


def _call_cua(action: str, **params) -> dict:
    # Route to UIA MCP for high-integrity targets
    if params.pop("_uia_route", False):
        return _call_mcp_http(action, params, port=_MCP_UIA_PORT)
    result = _call_mcp_cli(action, **params)
    if result is not None:
        return result
    result = _call_mcp_http(action, params)
    if result is not None:
        return result
    return {"error": f"cua-driver MCP unavailable (tried CLI and HTTP :{MCP_HTTP_PORT})"}


def _resolve_hwnd(pid: int):
    """Resolve a PID to a window handle."""
    try:
        result = _call_cua("list_windows", pid=pid)
        if "windows" in result and result["windows"]:
            return result["windows"][0].get("window_id") or \
                   result["windows"][0].get("hwnd")
    except Exception:
        pass
    return None


def _get_route_status() -> dict:
    """Return current route health for diagnosis/monitoring."""
    return {
        "locked_routes": sorted(_LOCKED_ROUTES) if _LOCKED_ROUTES else [],
        "last_failure": _LAST_FAILURE_DETAIL,
        "route_failure_counts": dict(_ROUTE_FAILURE_COUNT),
        "routing_integrity": _check_routing_integrity(),
        "energy_ok": _energy_check().get("proceed", True),
    }


# ══════════════════════════════════════════════════════════════
# Route function integrity (SSDT-style guard)
# ══════════════════════════════════════════════════════════════

_ROUTING_FINGERPRINTS = {}

def _register_routing_guard():
    """Snapshot bytecode fingerprints of critical routing functions.
    
    If any function is replaced at runtime (e.g. by a malicious module),
    _check_routing_integrity() will detect it.
    """
    import hashlib
    for name in ["_call_cua", "_call_mcp_cli", "_call_mcp_http"]:
        fn = globals().get(name)
        if fn:
            code = fn.__code__
            _ROUTING_FINGERPRINTS[name] = hashlib.sha256(code.co_code).hexdigest()

def _check_routing_integrity() -> dict:
    """Verify routing functions haven't been tampered with.
    
    Returns {"ok": True} or {"ok": False, "violations": [(name, ...)]}.
    """
    import hashlib
    if not _ROUTING_FINGERPRINTS:
        return {"ok": False, "error": "guards not registered"}
    violations = []
    for name, expected in _ROUTING_FINGERPRINTS.items():
        fn = globals().get(name)
        if not fn:
            violations.append((name, "missing"))
            continue
        actual = hashlib.sha256(fn.__code__.co_code).hexdigest()
        if actual != expected:
            violations.append((name, "tampered", actual[:12]))
    return {"ok": len(violations) == 0, "violations": violations}

# Register on import
_register_routing_guard()


# ══════════════════════════════════════════════════════════════
# Enhanced energy feedback: route locking + failure tracking
# ══════════════════════════════════════════════════════════════

_LOCKED_ROUTES = set()       # Routes disabled due to high failure rate
_LAST_FAILURE_DETAIL = None  # Stores last failure for diagnosis
_ROUTE_FAILURE_COUNT = {}    # {route_name: consecutive_failures}
_MAX_CONSECUTIVE_FAILURES = 3


def _record_route_failure(route: str, action: str, detail: str = ""):
    """Record a route failure. Locks the route after MAX_CONSECUTIVE_FAILURES."""
    global _LAST_FAILURE_DETAIL
    key = f"{route}:{action}"
    _ROUTE_FAILURE_COUNT[key] = _ROUTE_FAILURE_COUNT.get(key, 0) + 1
    _LAST_FAILURE_DETAIL = {
        "route": route,
        "action": action,
        "detail": detail,
        "count": _ROUTE_FAILURE_COUNT[key],
        "time": time.time(),
    }
    cnt = _ROUTE_FAILURE_COUNT[key]
    if cnt >= _MAX_CONSECUTIVE_FAILURES:
        _LOCKED_ROUTES.add(route)
        _LAST_FAILURE_DETAIL["locked_route"] = route
        _energy_record(False)

def _route_available(route: str) -> bool:
    """Check if a route is available (not locked)."""
    return route not in _LOCKED_ROUTES

def _unlock_route(route: str):
    """Unlock a previously locked route (e.g. after successful operation)."""
    _LOCKED_ROUTES.discard(route)
    # Clear failure counters for this route
    keys = [k for k in _ROUTE_FAILURE_COUNT if k.startswith(f"{route}:")]
    for k in keys:
        del _ROUTE_FAILURE_COUNT[k]


# ══════════════════════════════════════════════════════════════
# Async verification worker (Phase 3 light — Observer pattern)
# ══════════════════════════════════════════════════════════════

_VERIFY_QUEUE: queue.Queue = None
_VERIFY_WORKER: threading.Thread = None
_VERIFY_RESULTS: dict = {}        # {task_id: {"status": ..., "result": ...}}
_VERIFY_LOCK = threading.Lock()


def _start_verifier():
    """Start the background verification worker (idempotent)."""
    global _VERIFY_QUEUE, _VERIFY_WORKER
    if _VERIFY_WORKER is not None and _VERIFY_WORKER.is_alive():
        return
    _VERIFY_QUEUE = queue.Queue()
    _VERIFY_WORKER = threading.Thread(target=_verifier_loop, daemon=True)
    _VERIFY_WORKER.start()


def _verifier_loop():
    """Background loop: consume verify tasks, settle, capture, assert."""
    while True:
        try:
            task = _VERIFY_QUEUE.get()
            if task is None:  # Sentinel for shutdown
                break
            task_id = task.get("id")
            pid = task.get("pid")
            window_id = task.get("window_id")
            settle_ms = task.get("settle_ms", DEFAULT_SETTLE_MS)
            expected = task.get("expected", {})

            # Settle: wait for UI to stabilize
            if settle_ms > 0:
                time.sleep(settle_ms / 1000.0)

            # Capture after settle
            result = _call_mcp_cli("get_window_state", pid=pid,
                                   window_id=window_id, capture_mode="som")
            if result is None:
                # CLI unavailable, try HTTP with short timeout
                try:
                    import urllib.request
                    body = json.dumps({
                        "name": "get_window_state",
                        "arguments": {"pid": pid, "window_id": window_id,
                                      "capture_mode": "som"}
                    }).encode()
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{MCP_HTTP_PORT}/tools/call",
                        data=body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        result = json.loads(resp.read().decode())
                except Exception:
                    result = {"error": "MCP unavailable"}

            if "error" in result:
                # MCP unavailable — skip verification, mark as skipped
                with _VERIFY_LOCK:
                    _VERIFY_RESULTS[task_id] = {
                        "status": "skipped",
                        "reason": result.get("error", "MCP unavailable")[:60],
                    }
                continue

            # Run assertions
            passed = True
            issues = []

            if expected.get("element_index") is not None:
                elements = result.get("elements", [])
                target = expected["element_index"]
                if target >= len(elements):
                    passed = False
                    issues.append(f"element[{target}] missing")

            if expected.get("text_substring"):
                # OCR assertion via winctl or inline
                screenshot = result.get("screenshot_base64") or ""
                if expected["text_substring"] not in str(result):
                    passed = False
                    issues.append(f"text '{expected['text_substring']}' not found")

            # Hash change check
            if expected.get("hash_before"):
                hash_before = expected["hash_before"]
                hash_after = str(hash(str(result)))
                if hash_after == hash_before:
                    passed = False
                    issues.append("state unchanged (hash match)")

            status = "passed" if passed else "failed"
            with _VERIFY_LOCK:
                _VERIFY_RESULTS[task_id] = {
                    "status": status,
                    "issues": issues,
                    "settled_ms": settle_ms,
                }

            if not passed:
                _record_route_failure("verify", "async",
                                      "; ".join(issues))
        except Exception:
            pass  # Worker never crashes


def _enqueue_verify(pid: int, window_id: int = 0, settle_ms: int = None,
                    expected: dict = None, task_id: str = None):
    """Enqueue an async verification task. Returns immediately.

    Call _get_verify_result(task_id) later to check outcome.
    """
    _start_verifier()
    if task_id is None:
        task_id = f"v{int(time.time() * 1000)}_{pid}"
    if settle_ms is None:
        settle_ms = DEFAULT_SETTLE_MS

    _VERIFY_QUEUE.put({
        "id": task_id,
        "pid": pid,
        "window_id": window_id,
        "settle_ms": settle_ms,
        "expected": expected or {},
    })
    with _VERIFY_LOCK:
        _VERIFY_RESULTS[task_id] = {"status": "pending"}
    return task_id


def _get_verify_result(task_id: str, timeout: float = None) -> Optional[dict]:
    """Get result of a verification task. Returns None if still pending.

    Args:
        task_id: Returned from _enqueue_verify()
        timeout: Max seconds to wait. None = don't wait, return immediately.
    """
    deadline = None
    if timeout is not None:
        deadline = time.time() + timeout

    while True:
        with _VERIFY_LOCK:
            result = _VERIFY_RESULTS.get(task_id)
            if result and result.get("status") != "pending":
                return result
        if deadline and time.time() > deadline:
            return {"status": "timeout", "task_id": task_id}
        if timeout is not None:
            time.sleep(0.05)
        else:
            return None


# ══════════════════════════════════════════════════════════════
# Core Actions (backed by modular UiTreeCache)
# ══════════════════════════════════════════════════════════════

def _fetch_tree(pid: int, window_id: int, force: bool = False,
                process_name: str = None, window_title: str = None,
                window_class: str = None) -> Optional[dict]:
    """Fetch UI tree, using cache if available.
    Cross-session: checks layout_knowledge before MCP call to skip cold start.
    """
    # In-memory cache (fast path)
    if _ui_cache and not force:
        cached = _ui_cache.get()
        if cached is not None:
            return cached

    # Cross-session layout knowledge (cold-start bypass)
    if HAS_MODULES and process_name and window_class and not force:
        db = get_layout_knowledge()
        known = db.lookup(process_name, window_title or "", window_class)
        if known:
            # Build return dict with window identity for capture() to learn from
            return {
                "elements": known.get("elements", []),
                "source": "layout_knowledge",
                "window": {
                    "process_name": known.get("process", process_name or ""),
                    "title": known.get("title", window_title or ""),
                    "class_name": known.get("class", window_class or ""),
                },
                "layout_knowledge_hit": True,
            }

    tree = _call_cua("get_window_state", pid=pid, window_id=window_id,
                     capture_mode="som")
    if "error" not in tree and _ui_cache:
        _ui_cache.set(tree)
    return tree


def _capture_window_wgc(pid: int, window_id: int) -> dict:
    """Capture a specific window using multi-method fallback pipeline.

    Methods tried in order:
    1. DXGI Desktop Duplication (D3D11, works on Electron/DirectX)
    2. PrintWindow (works on occluded Win32 windows)
    3. BitBlt (visible window content)
    4. PIL ImageGrab (full desktop fallback)
    """
    try:
        from scripts.dxgi_capture import capture_window_fallback
        hwnd = window_id or 0
        result = capture_window_fallback(hwnd)
        if "error" not in result:
            return result
        # Fall through to original PrintWindow logic
    except ImportError:
        pass
    except Exception:
        pass

    # Original PrintWindow + BitBlt logic
    try:
        import ctypes
        from ctypes import wintypes
        from PIL import Image, ImageGrab
        import io, base64

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        # Get window bounds
        rect = wintypes.RECT()
        user32.GetWindowRect(window_id, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top

        if w <= 0 or h <= 0:
            # Window is hidden/zero-sized, can't capture
            return {"error": "zero-size window"}

        # Method 1: PrintWindow (renders window content, works occluded)
        hdc = user32.GetDC(0)
        mem_dc = gdi32.CreateCompatibleDC(hdc)
        bitmap = gdi32.CreateCompatibleBitmap(hdc, w, h)
        old = gdi32.SelectObject(mem_dc, bitmap)

        PW_CLIENTONLY = 1
        pw_result = user32.PrintWindow(window_id, mem_dc, PW_CLIENTONLY)

        # Cleanup
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(0, hdc)

        if pw_result:
            # Convert HBITMAP to PIL Image via save to file
            from PIL import Image
            import io
            
            # Get bitmap info
            bpp = 32
            stride = (w * bpp + 31) // 32 * 4
            bmp_data = ctypes.create_string_buffer(stride * h)
            
            bmi = ctypes.create_string_buffer(40)
            ctypes.memset(bmi, 0, 40)
            ctypes.cast(bmi, ctypes.POINTER(ctypes.c_uint32))[0] = 40
            ctypes.cast(bmi, ctypes.POINTER(ctypes.c_uint32))[4] = w
            ctypes.cast(bmi, ctypes.POINTER(ctypes.c_uint32))[8] = h
            ctypes.cast(bmi, ctypes.POINTER(ctypes.c_uint16))[12] = 1  # planes
            ctypes.cast(bmi, ctypes.POINTER(ctypes.c_uint16))[14] = bpp  # bpp
            
            lines = gdi32.GetDIBits(mem_dc, bitmap, 0, h, bmp_data, bmi, 0)
            gdi32.DeleteObject(bitmap)
            
            if lines and lines > 0:
                img = Image.frombuffer("RGBA", (w, h), bmp_data, "raw", "BGRA", stride)
                # Check if blank
                ext = img.getextrema() if hasattr(img, 'getextrema') else None
                if ext is None or not (ext[0][0] > 240 and ext[1][0] < 15):
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    b64 = base64.b64encode(buf.getvalue()).decode()
                    gdi32.DeleteDC(mem_dc)
                    user32.ReleaseDC(0, hdc)
                    return {"base64": b64, "format": "png",
                            "width": w, "height": h,
                            "input_kb": round(len(buf.getvalue()) / 1024, 1),
                            "method": "printwindow"}
            else:
                gdi32.DeleteObject(bitmap)

        # Method 2: PrintWindow didn't work or was blank, try direct desktop capture
        # Use mss for multi-monitor support
        try:
            import mss
            with mss.mss() as sct:
                monitor = {"left": rect.left, "top": rect.top,
                          "width": w, "height": h}
                sct_img = sct.grab(monitor)
                img = Image.frombytes("RGB", sct_img.size, sct_img.rgb)
        except ImportError:
            img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return {"base64": b64, "format": "png",
                "width": img.width, "height": img.height,
                "input_kb": round(len(buf.getvalue()) / 1024, 1),
                "method": "bitblt"}
    except Exception as e:
        return {"error": f"wgc capture failed: {e}"}


def capture(pid: int = 0, window_id: int = 0, compress_kb: float = None,
            settle_ms: int = 0, use_cache: bool = True,
            learn_layout: bool = True,
            process_name: str = None, window_title: str = None,
            window_class: str = None):
    """Enhanced capture with modular UI tree cache + cross-session layout learning.

    Args:
        learn_layout: If True, persist the captured layout to disk for
            future cold-start bypass.
        process_name/window_title/window_class: Window identity for
            layout_knowledge keying. If omitted, learned from the
            capture result.
    """
    if settle_ms > 0:
        time.sleep(settle_ms / 1000)

    result = _fetch_tree(pid, window_id,
                         process_name=process_name,
                         window_title=window_title,
                         window_class=window_class) if use_cache else \
        _call_cua("get_window_state", pid=pid, window_id=window_id,
                  capture_mode="som")

    if "error" in result:
        # Try occluded window capture before falling back to full desktop
        wgc_result = _capture_window_wgc(pid, window_id) if window_id else None
        if wgc_result and "error" not in wgc_result:
            return wgc_result
        
        # Full desktop fallback
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            if compress_kb and HAS_COMPRESS:
                # Pipeline: compress direct from PIL Image, skip base64 round-trip
                from compress_image import compress_pipeline
                comp = compress_pipeline(img, target_kb=compress_kb,
                                         fast_format='jpeg', fast_quality=85)
                return {"base64": comp.base64, "format": comp.format,
                        "width": comp.width, "height": comp.height,
                        "output_kb": round(comp.output_kb, 1)}
            import io, base64
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            return {"base64": b64, "format": "png",
                    "width": img.width, "height": img.height,
                    "input_kb": round(len(buf.getvalue()) / 1024, 1)}
        except ImportError:
            return {"error": "PIL not available and cua-driver unavailable"}

    # Cross-session layout learning: save successful captures to disk
    if learn_layout and HAS_MODULES and "error" not in result:
        try:
            elements = result.get("elements") or result.get("structuredContent", {}).get("elements")
            if elements and len(elements) > 3:  # Meaningful layout
                info = result.get("window", {})
                pn = process_name or info.get("process_name") or ""
                wt = window_title or info.get("title") or ""
                wc = window_class or info.get("class_name") or ""
                if pn and wc:
                    db = get_layout_knowledge()
                    db.learn(pn, wt, wc, elements)
        except Exception:
            pass  # Non-critical; learning failure never breaks capture

    return result


def click(pid: int, element: int = None, x: int = None, y: int = None,
          button: str = "left", modifiers: list = None,
          settle_ms: int = None, compress_kb: float = None,
          retry: int = MAX_RETRIES, window_id: int = 0):
    """Click with fingerprint-based stale-index recovery (modular cache).
    Energy-aware: checks ATP energy gate before proceeding.
    """
    energy = _energy_check("click")
    if not energy["proceed"]:
        return {"action": "click", "pid": pid, "error": "energy halted",
                "energy_reason": energy.get("reason", "")}
    skip_shot = energy.get("skip_screenshot", False)
    if settle_ms is None:
        settle_ms = _adaptive_settle(pid, "click")

    for attempt in range(retry + 1):
        params = {"pid": pid, "button": button}
        if element is not None:
            params["element_index"] = element
        elif x is not None and y is not None:
            params["x"] = x
            params["y"] = y
        if modifiers:
            params["modifiers"] = modifiers

        result = _call_cua("click", **params)

        if "error" not in result:
            _energy_record(True)
            break

        err = str(result.get("error", ""))
        # Fingerprint-based stale index recovery
        if "stale" in err.lower() and element is not None and _ui_cache:
            # Re-fetch tree and find element by fingerprint from cache
            old_fp = None
            old_element = None
            if _ui_cache._elements and element < len(_ui_cache._elements):
                cached = _ui_cache._elements[element]
                old_fp = cached.get("_fingerprint")
                old_element = cached  # Full element for resilient fallback
            if old_fp:
                new_idx = _ui_cache.get_element_index(old_fp, old_element)
                if new_idx is not None:
                    element = new_idx
                    time.sleep(0.1)
                    continue
            _ui_cache.invalidate("click")
            time.sleep(0.3)
            continue
        if "background_unavailable" in err:
            params["dispatch"] = "foreground"
            continue

    if _ui_cache:
        _ui_cache.invalidate("click")

    # Record energy for failure case (success already recorded inside the loop)
    if "error" in result:
        _energy_record(False)
        _record_route_failure("cua-driver", "click",
                              str(result.get("error", ""))[:80])

    settle_start = time.time()
    if settle_ms > 0:
        time.sleep(settle_ms / 1000)
    _record_settle(pid, "click", (time.time() - settle_start) * 1000)

    shot = capture(pid, window_id, compress_kb=compress_kb) if not skip_shot else None

    # Async verification (fire-and-forget for background state assertion)
    verify_id = None
    if not skip_shot and element is not None:
        verify_id = _enqueue_verify(pid, window_id=window_id, settle_ms=200,
                                    expected={"element_index": element})

    return {"action": "click", "pid": pid, "element": element,
            "coordinate": f"({x},{y})" if x is not None else None,
            "result": result, "screenshot": shot, "verify_task": verify_id}


def type_text(pid: int, text: str, delay_ms: int = 30,
              window_id: int = 0, element: int = None):
    """Intelligent text input: set_value -> paste -> type_text."""
    if element is not None:
        set_result = _call_cua("set_value", pid=pid, value=text,
                              element_index=element, window_id=window_id)
        if "error" not in set_result:
            return {"action": "type_text", "pid": pid, "length": len(text),
                    "method": "set_value", "result": set_result}

    if len(text) > 25:
        paste_result = paste_text(pid, text)
        return {"action": "type_text", "pid": pid, "length": len(text),
                "method": "paste", "result": paste_result}

    result = _call_cua("type_text", pid=pid, text=text, delay_ms=delay_ms)
    if _ui_cache:
        _ui_cache.invalidate("type")
    return {"action": "type_text", "pid": pid, "length": len(text),
            "method": "type_text", "result": result}


# Known Electron app executable names that need --force-renderer-accessibility
ELECTRON_APPS = {
    "code.exe": "Visual Studio Code",
    "code-insiders.exe": "VS Code Insiders",
    "slack.exe": "Slack",
    "discord.exe": "Discord",
    "electron.exe": "Generic Electron",
    "whatsapp.exe": "WhatsApp Desktop",
    "teams.exe": "Microsoft Teams (new)",
}

def paste_text(pid: int, text: str) -> dict:
    """Paste text with shared clipboard_guard (multi-format safe) + energy-aware.

    Uses target's window handle directly for WM_SETTEXT path.
    Falls back to clipboard_guard() + Ctrl+V via cua-driver hotkey.
    """
    # Get window handle for WM_SETTEXT optimization
    hwnd = _resolve_hwnd(pid)
    if hwnd and try_wm_settext(hwnd, text):
        if _ui_cache:
            _ui_cache.invalidate("paste")
        return {"action": "paste_text", "pid": pid, "length": len(text),
                "method": "wm_settext", "result": {"success": True}}

    try:
        # Use shared clipboard_guard for multi-format safe paste
        if HAS_MODULES:
            with shared_clipboard_guard():
                from scripts.clipboard_guard import set_clipboard_text
                if not set_clipboard_text(text):
                    return type_text(pid, text, delay_ms=5)
                result = _call_cua("hotkey", pid=pid, keys=["ctrl", "v"])
        else:
            # Fallback if modules not available
            result = _fallback_paste(pid, text)
    except Exception:
        return type_text(pid, text, delay_ms=5)

    if _ui_cache:
        _ui_cache.invalidate("paste")
    return {"action": "paste_text", "pid": pid, "length": len(text),
            "method": "clipboard_guarded", "result": result}


def _fallback_paste(pid: int, text: str) -> dict:
    """Last-resort paste when clipboard_guard module is unavailable."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        # Backup text only
        old = None
        if user32.OpenClipboard(None):
            h = user32.GetClipboardData(13)
            if h:
                lp = kernel32.GlobalLock(h)
                if lp:
                    old = ctypes.c_wchar_p(lp).value
                    kernel32.GlobalUnlock(h)
            user32.CloseClipboard()

        # Set new text
        if not user32.OpenClipboard(None):
            return {"error": "clipboard locked"}
        try:
            user32.EmptyClipboard()
            w_text = (text + "\0").encode("utf-16-le")
            hm = kernel32.GlobalAlloc(0x2002, len(w_text))
            if hm:
                p = kernel32.GlobalLock(hm)
                ctypes.memmove(p, w_text, len(w_text))
                kernel32.GlobalUnlock(hm)
                user32.SetClipboardData(13, hm)
        finally:
            user32.CloseClipboard()

        result = _call_cua("hotkey", pid=pid, keys=["ctrl", "v"])

        # Restore
        if old:
            time.sleep(0.05)
            if user32.OpenClipboard(None):
                try:
                    user32.EmptyClipboard()
                    w_text = (old + "\0").encode("utf-16-le")
                    hm = kernel32.GlobalAlloc(0x2002, len(w_text))
                    if hm:
                        p = kernel32.GlobalLock(hm)
                        ctypes.memmove(p, w_text, len(w_text))
                        kernel32.GlobalUnlock(hm)
                        user32.SetClipboardData(13, hm)
                finally:
                    user32.CloseClipboard()

        return result
    except Exception:
        return {"error": "fallback paste failed"}


def _resolve_hwnd(pid: int):
    """Resolve a PID to a window handle (for WM_SETTEXT shortcut)."""
    try:
        result = _call_cua("list_windows", pid=pid)
        if "windows" in result and result["windows"]:
            return result["windows"][0].get("window_id") or \
                   result["windows"][0].get("hwnd")
    except Exception:
        pass
    return None


# ── Energy-aware action wrapper ─────────────────────────────────

_HAS_ENERGY = False
_energy_regulator = None

try:
    from energy_regulator import get_regulator
    _energy_regulator = get_regulator()
    _HAS_ENERGY = True
except ImportError:
    pass


def _energy_check(action_type: str = None) -> dict:
    """Check ATP energy before executing an action.

    Returns advice dict with keys:
        proceed: bool — False means STOP
        strategy: str — 'normal' | 'streamlined' | 'halted'
        skip_screenshot: bool
        reduce_verify: bool
    """
    if not _HAS_ENERGY or _energy_regulator is None:
        return {"proceed": True, "strategy": "normal",
                "skip_screenshot": False, "reduce_verify": False}

    try:
        proceed, reason, advice = _energy_regulator.should_proceed()
        result = {
            "proceed": proceed,
            "reason": reason,
            "strategy": advice.get("action", "normal"),
            "skip_screenshot": advice.get("skip_screenshot", False),
            "reduce_verify": advice.get("reduce_verify", False),
        }
        # Auto-recover from coma: trigger enter_comath
        if not proceed and "coma" in reason.lower():
            try:
                _energy_regulator.enter_comath()
                result["auto_recovery"] = "enter_comath triggered"
            except Exception:
                pass
        return result
    except Exception:
        return {"proceed": True, "strategy": "normal",
                "skip_screenshot": False, "reduce_verify": False}


def _energy_record(success: bool):
    """Record action outcome to energy model."""
    if _HAS_ENERGY and _energy_regulator is not None:
        try:
            _energy_regulator.record_and_regulate(success)
        except Exception:
            pass


# ── Electron accessibility (WM_GETOBJECT) ───────────────────────

WM_GETOBJECT = 0x003D
OBJID_CLIENT = 0xFFFFFFFC

def activate_electron_accessibility(hwnd: int) -> bool:
    """Trigger Chromium/Electron accessibility tree via MSAA protocol.

    Chromium documents: it first calls NotifyWinEvent(EVENT_SYSTEM_ALERT, ...)
    with custom object id=1. If it subsequently receives WM_GETOBJECT with
    that same id, it enables full accessibility.
    
    ⚠️ VERIFIED: the naive WM_GETOBJECT+OBJID_CLIENT did NOT work.
    This corrected version does TWO things:
      1. NotifyWinEvent to signal assistive tech presence
      2. WM_GETOBJECT with the matching child id (1 not OBJID_CLIENT)
    
    Returns True if the message was sent.
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        WM_GETOBJECT = 0x003D
        EVENT_SYSTEM_ALERT = 0x0002
        CUSTOM_CHILD_ID = 1  # Chromium's custom object id for a11y detection

        # Step 1: NotifyWinEvent to signal assistive tech
        user32.NotifyWinEvent(EVENT_SYSTEM_ALERT, hwnd, OBJID_CLIENT, CUSTOM_CHILD_ID)

        # Step 2: WM_GETOBJECT with the matching child id
        result = user32.SendMessageW(hwnd, WM_GETOBJECT, 0, CUSTOM_CHILD_ID)
        return result != 0
    except Exception:
        return False


def _ensure_electron_accessibility(pid: int, elements: list = None) -> bool:
    """Check if window has shallow UIA tree and trigger accessibility if needed.

    Returns True if activation was triggered.
    """
    if not elements or len(elements) > 10:
        return False  # Not shallow → already accessible

    # Check if this is an Electron app
    try:
        result = _call_cua("list_windows", pid=pid)
        windows = result.get("windows", [])
        if not windows:
            return False
        win = windows[0]
        title = win.get("title", "").lower()
        class_name = str(win.get("class_name", ""))
        # Electron windows have specific class patterns
        is_electron = (
            "electron" in class_name.lower() or
            "chrome_widgetwin" in class_name.lower() or
            "cobalt" in class_name.lower()
        )
        if not is_electron:
            return False

        hwnd = win.get("window_id") or win.get("hwnd")
        if not hwnd:
            return False

        result = activate_electron_accessibility(hwnd)
        if result:
            time.sleep(1.0)  # Wait for UIA tree to build
        return result
    except Exception:
        return False


def launch_app(path_or_name: str, args: str = "",
               electron_accessibility: bool = None):
    """Launch an application with optional Electron accessibility injection.

    Detects known Electron apps and automatically appends
    --force-renderer-accessibility to enable full UIA tree depth.

    Args:
        path_or_name: Executable path or app name (resolved by cua-driver)
        args: Optional command-line arguments
        electron_accessibility: Force on/off Electron injection.
            None = auto-detect based on known app list.

    Returns:
        dict with "pid", "name", "windows" keys
    """
    # Auto-detect Electron apps
    basename = os.path.basename(path_or_name).lower()
    if electron_accessibility is None:
        electron_accessibility = basename in ELECTRON_APPS

    if electron_accessibility:
        # Inject --force-renderer-accessibility flag
        extra = ["--force-renderer-accessibility"]
        if args:
            # If args already exist, prepend extra before them
            result = _call_cua("launch_app", name=path_or_name,
                               additional_arguments=extra + [args])
        else:
            result = _call_cua("launch_app", name=path_or_name,
                               additional_arguments=extra)
    else:
        if args:
            result = _call_cua("launch_app", name=path_or_name,
                               additional_arguments=[args])
        else:
            result = _call_cua("launch_app", name=path_or_name)

    if "error" in result:
        # Fallback: try windeep's exec_process
        try:
            from windeep.core import exec_process as windeep_launch
            ok = windeep_launch(path_or_name, args)
            if ok:
                return {"method": "windeep", "path": path_or_name}
        except ImportError:
            pass

    return result


def type_keys(pid: int, keys: list, window_id: int = None):
    params = {"pid": pid, "keys": keys}
    if window_id is not None:
        params["window_id"] = window_id
    result = _call_cua("hotkey", **params)
    return {"action": "type_keys", "pid": pid,
            "keys": "+".join(keys), "result": result}


def scroll(pid: int, direction: str = "down", amount: int = 3,
           element: int = None, modifiers: list = None,
           settle_ms: int = None, window_id: int = 0):
    if settle_ms is None:
        settle_ms = _adaptive_settle(pid, "scroll")

    params = {"pid": pid, "direction": direction, "amount": amount}
    if element is not None:
        params["element_index"] = element
    if modifiers:
        params["modifiers"] = modifiers

    result = _call_cua("scroll", **params)

    if "stale" in str(result.get("error", "")).lower() and element is not None and _ui_cache:
        old_fp = None
        old_element = None
        if _ui_cache._elements and element < len(_ui_cache._elements):
            cached = _ui_cache._elements[element]
            old_fp = cached.get("_fingerprint")
            old_element = cached  # Full element for resilient fallback
        if old_fp:
            new_idx = _ui_cache.get_element_index(old_fp, old_element)
            if new_idx is not None:
                params["element_index"] = new_idx
                result = _call_cua("scroll", **params)

    if settle_ms > 0:
        time.sleep(settle_ms / 1000)
    return {"action": "scroll", "pid": pid, "direction": direction,
            "result": result}


def drag(pid: int, from_x: int, from_y: int, to_x: int, to_y: int,
         button: str = "left", modifiers: list = None,
         settle_ms: int = None, window_id: int = 0):
    if settle_ms is None:
        settle_ms = _adaptive_settle(pid, "drag")
    params = {"pid": pid, "from_x": from_x, "from_y": from_y,
              "to_x": to_x, "to_y": to_y, "button": button}
    if modifiers:
        params["modifiers"] = modifiers
    result = _call_cua("drag", **params)
    if _ui_cache:
        _ui_cache.invalidate("drag")
    if settle_ms > 0:
        time.sleep(settle_ms / 1000)
    return {"action": "drag", "pid": pid, "result": result}


def list_apps():
    result = _call_cua("list_apps")
    return result


def health_check():
    result = _call_cua("health_report")
    if _ui_cache:
        result["cache"] = {
            "alive": True,
            "stale": _ui_cache.stale,
            "element_count": _ui_cache.element_count,
            "fingerprints": len(_ui_cache._fingerprints) if hasattr(_ui_cache, '_fingerprints') else 0,
            "settle_history": len(SETTLE_HISTORY),
        }
    return result


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Enhanced computer control (bytebot-inspired, P0 modular)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("screenshot")
    p.add_argument("--compress", type=float, default=DEFAULT_COMPRESS_KB)
    p.add_argument("--pid", type=int, default=0)
    p.add_argument("--window-id", type=int, default=0)
    p.add_argument("--settle", type=int, default=0)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("click")
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("--element", type=int)
    p.add_argument("--x", type=int)
    p.add_argument("--y", type=int)
    p.add_argument("--button", default="left", choices=["left", "right", "middle"])
    p.add_argument("--modifiers", nargs="*")
    p.add_argument("--settle", type=int, default=None)
    p.add_argument("--compress", type=float, default=DEFAULT_COMPRESS_KB)
    p.add_argument("--no-shot", action="store_true")
    p.add_argument("--window-id", type=int, default=0)

    p = sub.add_parser("type-text")
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("text")
    p.add_argument("--delay", type=int, default=30)
    p.add_argument("--element", type=int)
    p.add_argument("--window-id", type=int, default=0)

    p = sub.add_parser("paste")
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("text")

    p = sub.add_parser("type-keys")
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("keys", nargs="+")

    p = sub.add_parser("scroll")
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("--direction", default="down",
                   choices=["up", "down", "left", "right"])
    p.add_argument("--amount", type=int, default=3)
    p.add_argument("--element", type=int)
    p.add_argument("--modifiers", nargs="*")
    p.add_argument("--settle", type=int, default=None)
    p.add_argument("--window-id", type=int, default=0)

    p = sub.add_parser("drag")
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("--from-x", type=int, required=True)
    p.add_argument("--from-y", type=int, required=True)
    p.add_argument("--to-x", type=int, required=True)
    p.add_argument("--to-y", type=int, required=True)
    p.add_argument("--button", default="left")
    p.add_argument("--modifiers", nargs="*")
    p.add_argument("--settle", type=int, default=None)
    p.add_argument("--window-id", type=int, default=0)

    sub.add_parser("list-apps")
    sub.add_parser("health")

    p = sub.add_parser("route-status")
    p.add_argument("--json", action="store_true", help="Output as JSON")

    p = sub.add_parser("cache-info")
    p.add_argument("--clear", action="store_true")

    args = parser.parse_args()

    if args.command == "screenshot":
        result = capture(pid=args.pid, window_id=args.window_id,
                        compress_kb=args.compress, settle_ms=args.settle)
        if args.json:
            print(json.dumps(result, indent=2))
        elif "error" in result:
            print(f"Error: {result['error']}")
        else:
            print(f"Screenshot: {result.get('input_kb', 0):.0f}KB -> "
                  f"{result.get('output_kb', result.get('input_kb', 0)):.0f}KB")
            print(f"Format: {result.get('format', '?')}")
            print(f"Size: {result.get('width', '?')}x{result.get('height', '?')}")

    elif args.command == "click":
        result = click(pid=args.pid, element=args.element,
                       x=args.x, y=args.y, button=args.button,
                       modifiers=args.modifiers,
                       settle_ms=args.settle if not args.no_shot else 0,
                       compress_kb=args.compress if not args.no_shot else None,
                       window_id=args.window_id)
        print(json.dumps({k: v for k, v in result.items()
                         if k != "screenshot" or not args.no_shot},
                        indent=2, default=str)[:3000])

    elif args.command == "type-text":
        result = type_text(pid=args.pid, text=args.text,
                          delay_ms=args.delay, element=args.element,
                          window_id=args.window_id)
        print(json.dumps(result, indent=2, default=str))

    elif args.command == "paste":
        result = paste_text(pid=args.pid, text=args.text)
        print(json.dumps(result, indent=2, default=str))

    elif args.command == "type-keys":
        result = type_keys(pid=args.pid, keys=args.keys)
        print(json.dumps(result, indent=2, default=str))

    elif args.command == "scroll":
        result = scroll(pid=args.pid, direction=args.direction,
                       amount=args.amount, element=args.element,
                       modifiers=args.modifiers, settle_ms=args.settle,
                       window_id=args.window_id)
        print(json.dumps(result, indent=2, default=str))

    elif args.command == "drag":
        result = drag(pid=args.pid, from_x=args.from_x, from_y=args.from_y,
                     to_x=args.to_x, to_y=args.to_y, button=args.button,
                     modifiers=args.modifiers, settle_ms=args.settle,
                     window_id=args.window_id)
        print(json.dumps(result, indent=2, default=str))

    elif args.command == "list-apps":
        result = list_apps()
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            print(json.dumps(result, indent=2, default=str)[:3000])

    elif args.command == "health":
        result = health_check()
        print(json.dumps(result, indent=2, default=str)[:3000])

    elif args.command == "route-status":
        result = _get_route_status()
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            ok = result.get("routing_integrity", {}).get("ok", False)
            locked = result.get("locked_routes", [])
            last_fail = result.get("last_failure")
            print(f"Routing integrity: {'✅ OK' if ok else '❌ COMPROMISED'}")
            print(f"Locked routes: {locked if locked else '✅ none'}")
            if last_fail:
                print(f"Last failure: {last_fail.get('route')}.{last_fail.get('action')} "
                      f"(x{last_fail.get('count')}) — {last_fail.get('detail', '')[:60]}")
            else:
                print(f"Last failure: ✅ none")

    elif args.command == "cache-info":
        if _ui_cache:
            if args.clear:
                _ui_cache.clear()
                SETTLE_HISTORY.clear()
                print("Caches cleared.")
            else:
                s = _ui_cache
                print("Stale: %s\nElements: %s\nFingerprints: %s\nSettle history: %s entries" % (
                    s.stale, s.element_count, len(s._fingerprints), len(SETTLE_HISTORY)))
        else:
            print("Modules not loaded (cache unavailable)")


# ══════════════════════════════════════════════════════════════
# XAML-aware click (UIA hit-test path for modern apps)
# ══════════════════════════════════════════════════════════════

# Known XAML/WinUI3/UWP process names that don't respond to PostMessage
_XAML_APPS = {
    "notepad.exe", "calculator.exe", "photos.exe", "settings.exe",
    "screensketch.exe", "snippingtool.exe", "ms-paint.exe",
    "bingsports.exe", "bingnews.exe", "bingfinance.exe",
    "bingweather.exe",
}


def _is_xaml_app(pid: int) -> bool:
    """Check if a PID belongs to a known XAML/UWP app."""
    try:
        import psutil
        p = psutil.Process(pid)
        return p.name().lower() in _XAML_APPS
    except Exception:
        return False


def _xaml_click(element_index: int = None, x: int = None, y: int = None,
                pid: int = None, window_id: int = None,
                button: str = "left") -> dict:
    """Click on XAML/WinUI3 target using UIA hit-test path.

    XAML apps ignore PostMessage. Three strategies in order:
    1. comtypes UIA COM (direct InvokePattern/ExpandCollapsePattern)
    2. Coord click with dispatch='auto' (UIA hit-test)
    3. Foreground swap + SendInput for XAML menu items
    """
    # Resolve element_index to pixel coordinates first
    if element_index is not None and (x is None or y is None):
        state = _call_cua("get_window_state", pid=pid,
                          window_id=window_id, capture_mode="som",
                          max_elements=200)
        elements = state.get("elements") or []
        if element_index < len(elements):
            elem = elements[element_index]
            frame = elem.get("frame", {})
            x = frame.get("x", 0) + frame.get("w", 10) // 2
            y = frame.get("y", 0) + frame.get("h", 10) // 2

    # Strategy 1: comtypes UIA COM (direct pattern invocation)
    try:
        from comtypes.gen import UIAutomationClient as UIA
        # Try to find by element name if we have it from UIA tree
        if element_index is not None:
            cache = state.get("elements") if isinstance(state, dict) else []
            if element_index < len(cache):
                elem_name = cache[element_index].get("label", "")
                if elem_name:
                    result = _uia_com_invoke(pid=pid, element_name=elem_name, hwnd=window_id)
                    if result.get("ok"):
                        return result
    except ImportError:
        pass
    except Exception:
        pass

    # Strategy 1: coordinate + dispatch=auto (UIA hit-test first)
    if x is not None and y is not None:
        result = _call_cua("click", pid=pid, x=x, y=y,
                         button=button, dispatch="auto",
                         window_id=window_id)
        if "error" not in result:
            return result

    # Strategy 2: foreground swap + SendInput (works on XAML menus)
    if x is not None and y is not None:
        try:
            bring = _call_cua("bring_to_front", pid=pid)
            if "error" not in bring:
                result = _call_cua("click", pid=pid, x=x, y=y,
                                 button=button, dispatch="foreground",
                                 window_id=window_id)
                if "error" not in result:
                    return result
        except Exception:
            pass

    # Strategy 3: element_index fallback
    return _call_cua("click", pid=pid, element_index=element_index,
                     button=button, window_id=window_id)


def _uia_com_invoke(pid: int, element_name: str = None,
                    automation_id: str = None, hwnd: int = None) -> dict:
    """Invoke a UIA element via comtypes COM (replaces PowerShell approach).

    Uses direct UIA COM calls via comtypes for full tree traversal including
    XAML menu bars. Overcomes PostMessage limitations on modern Windows apps.

    Args:
        pid: Target process ID
        element_name: UIA Name property to search for
        automation_id: UIA AutomationId to search for (preferred)
        hwnd: Explicit HWND (resolved from pid if omitted)
    """
    try:
        from comtypes.client import CreateObject
        from comtypes.gen import UIAutomationClient as UIA
        import comtypes
        import ctypes
        from ctypes import byref, c_ulong, c_bool, c_int

        ole32 = ctypes.windll.ole32
        ole32.CoInitializeEx(0, 2)

        # Resolve HWND if needed
        if hwnd is None:
            result = _call_cua("list_windows", pid=pid)
            windows = result.get("windows", [])
            if not windows:
                return {"error": "no windows for pid"}
            hwnd = windows[0].get("window_id")

        # Create UIA object and get root element
        uia = CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        root = uia.ElementFromHandle(hwnd)

        # Find element by AutomationId or Name
        if automation_id:
            cond = uia.CreatePropertyCondition(UIA.UIA_AutomationIdPropertyId, automation_id)
        elif element_name:
            cond = uia.CreatePropertyCondition(UIA.UIA_NamePropertyId, element_name)
        else:
            return {"error": "provide automation_id or element_name"}

        target = root.FindFirst(UIA.TreeScope_Descendants, cond)
        if not target:
            # Fallback: search for name prefix match (handles "保存(S)\tCtrl+S")
            if element_name:
                all_items = root.FindAll(UIA.TreeScope_Descendants, cond)
                for i in range(all_items.Length):
                    item = all_items.GetElement(i)
                    if item.CurrentName.startswith(element_name):
                        target = item
                        break
            if not target:
                return {"error": f"element not found ({automation_id or element_name})"}

        found_name = target.CurrentName
        found_aid = target.CurrentAutomationId

        # Try patterns in order: Invoke → ExpandCollapse → Value
        try:
            inv_p = target.GetCurrentPattern(UIA.UIA_InvokePatternId)
            from comtypes.gen.UIAutomationClient import IUIAutomationInvokePattern
            inv = inv_p.QueryInterface(IUIAutomationInvokePattern)
            inv.Invoke()
            ole32.CoUninitialize()
            return {"ok": True, "method": "uia_invoke", "name": found_name}
        except Exception:
            pass

        try:
            ecp_p = target.GetCurrentPattern(UIA.UIA_ExpandCollapsePatternId)
            from comtypes.gen.UIAutomationClient import IUIAutomationExpandCollapsePattern
            ecp = ecp_p.QueryInterface(IUIAutomationExpandCollapsePattern)
            ecp.Expand()
            ole32.CoUninitialize()
            return {"ok": True, "method": "uia_expand", "name": found_name}
        except Exception:
            pass

        try:
            vp_p = target.GetCurrentPattern(UIA.UIA_ValuePatternId)
            from comtypes.gen.UIAutomationClient import IUIAutomationValuePattern
            vp = vp_p.QueryInterface(IUIAutomationValuePattern)
            vp.SetValue(element_name or "")
            ole32.CoUninitialize()
            return {"ok": True, "method": "uia_setvalue", "name": found_name}
        except Exception:
            pass

        ole32.CoUninitialize()
        return {"ok": True, "found": found_name, "method": "uia_find"}
    except ImportError:
        return {"error": "comtypes not installed (pip install comtypes)"}
    except Exception as e:
        try: ctypes.windll.ole32.CoUninitialize()
        except: pass
        return {"error": str(e)}


def _uia_com_findall(pid: int, control_type: int = None,
                     name_startswith: str = None, hwnd: int = None) -> list:
    """Find ALL matching UIA elements, returns list of (name, aid, ctrl_type)."""
    import comtypes, ctypes
    from comtypes.client import CreateObject
    from comtypes.gen import UIAutomationClient as UIA
    from ctypes import byref, c_ulong

    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx(0, 2)
    try:
        if hwnd is None:
            result = _call_cua("list_windows", pid=pid)
            win = result.get("windows", [{}])[0]
            hwnd = win.get("window_id")

        uia = CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        root = uia.ElementFromHandle(hwnd)

        if control_type:
            cond = uia.CreatePropertyCondition(UIA.UIA_ControlTypePropertyId, control_type)
        else:
            cond = uia.CreateTrueCondition()

        all_e = root.FindAll(UIA.TreeScope_Descendants, cond)
        results = []
        for i in range(all_e.Length):
            e = all_e.GetElement(i)
            nm = e.CurrentName
            if name_startswith and not nm.startswith(name_startswith):
                continue
            results.append({
                "name": nm, "aid": e.CurrentAutomationId,
                "type": e.CurrentControlType, "index": i
            })
        return results
    finally:
        ole32.CoUninitialize()


if __name__ == "__main__":
    main()
