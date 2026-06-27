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
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# ---- Modular components (Codex++ created) ----
SCRIPTS_DIR = Path(__file__).parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR.parent))  # so 'from scripts.X' works
try:
    from scripts.ui_tree_cache import UiTreeCache
    from scripts.element_fingerprint import element_fingerprint
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

def _call_mcp_http(tool: str, params: dict) -> Optional[dict]:
    try:
        import urllib.request
        body = json.dumps({"name": tool, "arguments": params}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{MCP_HTTP_PORT}/tools/call",
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


def _call_cua(action: str, **params) -> dict:
    result = _call_mcp_cli(action, **params)
    if result is not None:
        return result
    result = _call_mcp_http(action, params)
    if result is not None:
        return result
    return {"error": f"cua-driver MCP unavailable (tried CLI and HTTP :{MCP_HTTP_PORT})"}


# ══════════════════════════════════════════════════════════════
# Core Actions (backed by modular UiTreeCache)
# ══════════════════════════════════════════════════════════════

def _fetch_tree(pid: int, window_id: int, force: bool = False) -> Optional[dict]:
    """Fetch UI tree, using cache if available."""
    if _ui_cache and not force:
        cached = _ui_cache.get()
        if cached is not None:
            return cached

    tree = _call_cua("get_window_state", pid=pid, window_id=window_id,
                     capture_mode="som")
    if "error" not in tree and _ui_cache:
        _ui_cache.set(tree)
    return tree


def capture(pid: int = 0, window_id: int = 0, compress_kb: float = None,
            settle_ms: int = 0, use_cache: bool = True):
    """Enhanced capture with modular UI tree cache."""
    if settle_ms > 0:
        time.sleep(settle_ms / 1000)

    result = _fetch_tree(pid, window_id) if use_cache else \
        _call_cua("get_window_state", pid=pid, window_id=window_id,
                  capture_mode="som")

    if "error" in result:
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
    return result


def click(pid: int, element: int = None, x: int = None, y: int = None,
          button: str = "left", modifiers: list = None,
          settle_ms: int = None, compress_kb: float = None,
          retry: int = MAX_RETRIES, window_id: int = 0):
    """Click with fingerprint-based stale-index recovery (modular cache)."""
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
            break

        err = str(result.get("error", ""))
        # Fingerprint-based stale index recovery
        if "stale" in err.lower() and element is not None and _ui_cache:
            # Re-fetch tree and find element by fingerprint from cache
            old_fp = None
            if _ui_cache._elements and element < len(_ui_cache._elements):
                old_fp = _ui_cache._elements[element].get("_fingerprint")
            if old_fp:
                _fetch_tree(pid, window_id, force=True)
                new_idx = _ui_cache.get_element_index(old_fp)
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

    settle_start = time.time()
    if settle_ms > 0:
        time.sleep(settle_ms / 1000)
    _record_settle(pid, "click", (time.time() - settle_start) * 1000)

    shot = capture(pid, window_id, compress_kb=compress_kb)

    return {"action": "click", "pid": pid, "element": element,
            "coordinate": f"({x},{y})" if x is not None else None,
            "result": result, "screenshot": shot}


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


def paste_text(pid: int, text: str):
    """Clipboard paste via PowerShell Set-Clipboard (no pyperclip)."""
    try:
        safe_text = text.replace('"', "'")
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command",
             f'Set-Clipboard -Value """{safe_text}"""'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        proc.wait(timeout=5)
        time.sleep(0.2)
        if _ui_cache:
            _ui_cache.invalidate("paste")
        result = _call_cua("hotkey", pid=pid, keys=["ctrl", "v"])
        return {"action": "paste_text", "pid": pid, "length": len(text),
                "method": "clipboard", "result": result}
    except Exception:
        return type_text(pid, text, delay_ms=5)


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
        if _ui_cache._elements and element < len(_ui_cache._elements):
            old_fp = _ui_cache._elements[element].get("_fingerprint")
        if old_fp:
            _fetch_tree(pid, window_id, force=True)
            new_idx = _ui_cache.get_element_index(old_fp)
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

    elif args.command == "cache-info":
        if args.clear:
            _ui_cache.clear() if _ui_cache else None
            SETTLE_HISTORY.clear()
            print("Caches cleared.")
        elif _ui_cache:
            print(f"Stale: {_ui_cache.stale}")
            print(f"Elements: {_ui_cache.element_count}")
            print(f"Fingerprints: {len(_ui_cache._fingerprints)}")
            print(f"Settle history: {len(SETTLE_HISTORY)} entries")
        else:
            print("Modules not loaded (cache unavailable)")


if __name__ == "__main__":
    main()
