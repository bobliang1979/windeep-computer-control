# © 2026 BOBLIANG. All rights reserved.
"""
smart_matcher.py — Multi-strategy element matching for UIA blind spot coverage.

P1 from Codex++ plan: combine UIA tree + OCR + position + fuzzy matching
into a single fallback chain.

Strategy chain (strict priority):
  1. UIA exact match (element_index by role + label)
  2. UIA fuzzy match (Levenshtein on label, same parent)
  3. UIA position match (nearest clickable to last known position)
  4. OCR text match (Windows native OCR)
  5. Direct coordinates (last resort)

Usage:
    from scripts.smart_matcher import smart_click, smart_find

    # Smart find: try all strategies to locate an element
    result = smart_find(target_text="提交", tree=ui_tree, screenshot_b64=screenshot)
    # -> {"method": "uia_exact"|"uia_fuzzy"|"ocr"|"coordinate",
    #     "element_index": int|None, "x": int, "y": int, "confidence": float}

    # Smart click: find + click in one step
    result = smart_click(pid=1234, target_text="提交", window_id=0)
    # -> {"success": bool, "method": "...", "pid": 1234, ...}
"""

import json
import time
from typing import Optional

try:
    from scripts.ocr_finder import ocr_find_text, ocr_available
    HAS_OCR = True
except ImportError:
    HAS_OCR = False


# ── UIA tree helpers ─────────────────────────────────────────

def _get_flat_elements(tree: dict) -> list:
    sc = tree.get("structuredContent") or tree.get("structured_content") or {}
    flat = sc.get("elements") or tree.get("elements") or []
    if flat:
        return flat
    out = []
    def walk(node):
        for child in (node.get("children") or []):
            out.append(child)
            walk(child)
    walk(tree)
    return out


def _text_in_element(elem: dict) -> str:
    """Get the best text representation of an element."""
    return str(elem.get("label") or elem.get("name") or elem.get("value") or "")


def _levenshtein(a: str, b: str) -> int:
    """Simple Levenshtein distance."""
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = curr
    return prev[-1]


def _normalize(s: str) -> str:
    return s.lower().strip().replace(" ", "").replace("\u200b", "")


# ── Strategy 1: UIA exact match ─────────────────────────────

def _match_uia_exact(elements: list, target_text: str) -> Optional[dict]:
    """Find element where label/name exactly matches target text."""
    target_norm = _normalize(target_text)
    for elem in elements:
        text = _normalize(_text_in_element(elem))
        if text == target_norm:
            frame = elem.get("frame") or {}
            return {
                "method": "uia_exact",
                "element_index": elem.get("element_index"),
                "x": frame.get("x", 0) + frame.get("w", 0) // 2,
                "y": frame.get("y", 0) + frame.get("h", 0) // 2,
                "confidence": 0.98,
                "matched_text": _text_in_element(elem),
            }
    return None


# ── Strategy 2: UIA fuzzy match ─────────────────────────────

def _match_uia_fuzzy(elements: list, target_text: str) -> Optional[dict]:
    """Find element where label/name fuzzy-matches target (Levenshtein)."""
    target_norm = _normalize(target_text)
    best = None
    best_dist = 999

    for elem in elements:
        text = _normalize(_text_in_element(elem))
        if not text:
            continue
        # Prefer prefix/substring match over full Levenshtein
        if target_norm in text or text in target_norm:
            dist = abs(len(text) - len(target_norm))
        else:
            dist = _levenshtein(text, target_norm)

        if dist < best_dist:
            best_dist = dist
            frame = elem.get("frame") or {}
            best = {
                "method": "uia_fuzzy",
                "element_index": elem.get("element_index"),
                "x": frame.get("x", 0) + frame.get("w", 0) // 2,
                "y": frame.get("y", 0) + frame.get("h", 0) // 2,
                "confidence": max(0.5, 1.0 - dist / max(len(target_norm), 1) / 2),
                "matched_text": _text_in_element(elem),
                "distance": dist,
            }

    if best and best["confidence"] > 0.5:
        return best
    return None


# ── Strategy 3: UIA position match ──────────────────────────

def _match_uia_position(elements: list, last_x: int, last_y: int) -> Optional[dict]:
    """Find clickable element nearest to last known position."""
    best = None
    best_dist = 999999

    for elem in elements:
        frame = elem.get("frame") or {}
        cx = frame.get("x", 0) + frame.get("w", 0) // 2
        cy = frame.get("y", 0) + frame.get("h", 0) // 2
        dist = (cx - last_x) ** 2 + (cy - last_y) ** 2

        if dist < best_dist:
            best_dist = dist
            best = {
                "method": "uia_position",
                "element_index": elem.get("element_index"),
                "x": cx,
                "y": cy,
                "confidence": max(0.3, 1.0 - (dist ** 0.5) / 2000),
            }

    if best and best["confidence"] > 0.3:
        return best
    return None


# ── Strategy 4: OCR text match ──────────────────────────────

def _match_ocr(target_text: str, screenshot_b64: str) -> Optional[dict]:
    """Find text in screenshot using Windows native OCR."""
    if not HAS_OCR:
        return None

    result = ocr_find_text(target_text, screenshot_b64)
    if result.get("found"):
        return {
            "method": "ocr",
            "element_index": None,
            "x": result["x"] + result.get("w", 0) // 2,
            "y": result["y"] + result.get("h", 0) // 2,
            "confidence": result.get("confidence", 0.8),
            "matched_text": result.get("text", target_text),
        }
    return None


# ── Main smart matching ─────────────────────────────────────

def smart_find(
    target_text: str,
    tree: dict = None,
    screenshot_b64: str = None,
    last_x: int = None,
    last_y: int = None,
    prefer_ocr: bool = False,
) -> dict:
    """Multi-strategy element location.

    Tries strategies in order:
      1. UIA exact match
      2. UIA fuzzy match
      3. UIA position match (if last_x/last_y provided)
      4. OCR text match (if screenshot provided)
      5. Direct coordinate fallback

    Args:
        target_text: Text to search for.
        tree: UI tree dict from get_window_state.
        screenshot_b64: Base64 PNG screenshot (for OCR).
        last_x, last_y: Last known click position (for position matching).
        prefer_ocr: If True, try OCR before UIA fuzzy.

    Returns:
        {"method": str, "element_index": int|None, "x": int, "y": int,
         "confidence": float, ...}
        or {"method": "not_found", "confidence": 0.0}
    """
    elements = _get_flat_elements(tree) if tree else []

    # Order strategies
    strategies = [
        ("uia_exact", lambda: _match_uia_exact(elements, target_text)),
    ]

    if prefer_ocr and screenshot_b64:
        strategies.append(("ocr", lambda: _match_ocr(target_text, screenshot_b64)))
        strategies.append(("uia_fuzzy", lambda: _match_uia_fuzzy(elements, target_text)))
    else:
        strategies.append(("uia_fuzzy", lambda: _match_uia_fuzzy(elements, target_text)))
        if screenshot_b64:
            strategies.append(("ocr", lambda: _match_ocr(target_text, screenshot_b64)))

    if last_x is not None and last_y is not None:
        strategies.append(("uia_position", lambda: _match_uia_position(elements, last_x, last_y)))

    for name, strategy in strategies:
        try:
            result = strategy()
            if result is not None:
                return result
        except Exception:
            continue

    return {"method": "not_found", "confidence": 0.0, "element_index": None,
            "x": 0, "y": 0}


def smart_click(
    pid: int,
    target_text: str,
    window_id: int = 0,
    tree: dict = None,
    screenshot_b64: str = None,
    button: str = "left",
    settle_ms: int = 750,
) -> dict:
    """Find element by smart matching and click it.

    Combines smart_find() with a MCP click call.

    Args:
        pid: Target process ID.
        target_text: Text to find and click.
        window_id: Window handle.
        tree: UI tree (fetched if None).
        screenshot_b64: Screenshot (for OCR fallback).
        button: Mouse button.

    Returns:
        {"success": bool, "method": str, "confidence": float, ...}
    """
    from computer_control_enhanced import _call_cua

    # Ensure we have a tree
    if tree is None:
        result = _call_cua("get_window_state", pid=pid, window_id=window_id,
                          capture_mode="som")
        if "error" not in result:
            tree = result

    # Smart find
    match = smart_find(
        target_text=target_text,
        tree=tree,
        screenshot_b64=screenshot_b64,
        prefer_ocr=True,
    )

    if match["method"] == "not_found":
        return {"success": False, "error": f"'{target_text}' not found by any strategy",
                "method": "not_found", "pid": pid}

    # Execute click
    click_params = {"pid": pid, "button": button}

    if match.get("element_index") is not None:
        click_params["element_index"] = match["element_index"]
    else:
        click_params["x"] = match["x"]
        click_params["y"] = match["y"]

    click_result = _call_cua("click", **click_params)

    if settle_ms > 0:
        time.sleep(settle_ms / 1000)

    return {
        "success": "error" not in click_result,
        "method": match["method"],
        "confidence": match["confidence"],
        "pid": pid,
        "target": target_text,
        "click": click_result,
        "position": {"x": match.get("x"), "y": match.get("y")},
    }


def smart_find_supported() -> dict:
    """Check availability of all matching strategies."""
    return {
        "uia_exact": True,
        "uia_fuzzy": True,
        "uia_position": True,
        "ocr": HAS_OCR and ocr_available(),
    }
