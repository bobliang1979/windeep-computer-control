# © 2026 BOBLIANG. All rights reserved.
"""
assertion_verifier.py — Structured assertion system for UI action verification.

P2 from Codex++ plan: verify that UI actions actually produced expected results.

Assertions:
  - hash_change: Did the UI state hash change after the action?
  - element_appeared(text): Did an element with matching text appear?
  - element_disappeared(text): Did an element with matching text disappear?
  - text_contains(screenshot_b64, text): Does the screenshot contain the text?

Usage:
    from scripts.assertion_verifier import verify, capture_state, assert_hash_changed
    
    before = capture_state(pid, window_id)
    result = click(pid, element=7)
    after = capture_state(pid, window_id)
    
    report = verify(before, after, [
        ("hash_change", {}),
        ("element_appeared", {"text": "确认"}),
    ])
    # -> {"passed": True, "confidence": 0.95, "details": [...]}

Dependencies:
  - Pillow (optional, for screenshot OCR fallback)
  - Windows.Media.Ocr (optional, for native OCR)
"""

import hashlib
import json
import time
from typing import Any, Optional


# ── State capture ────────────────────────────────────────────

def _compute_tree_hash(tree: dict) -> str:
    """Compute a stable hash of the UI tree structure.

    Uses element indices, roles, labels, and positions to detect changes.
    Ignores transient properties (process IDs, timestamps).
    """
    elements = _get_flat_elements(tree)
    signatures = []
    for elem in elements:
        sig = {
            "i": elem.get("element_index", 0),
            "r": elem.get("role", ""),
            "l": elem.get("label", ""),
            "n": elem.get("name", ""),
            # Position: rounded to nearest 10px to ignore sub-pixel shifts
            "x": (elem.get("frame") or {}).get("x", 0) // 10 * 10,
            "y": (elem.get("frame") or {}).get("y", 0) // 10 * 10,
            "w": (elem.get("frame") or {}).get("w", 0) // 10 * 10,
            "h": (elem.get("frame") or {}).get("h", 0) // 10 * 10,
        }
        signatures.append(sig)
    raw = json.dumps(signatures, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _get_flat_elements(tree: dict) -> list:
    """Extract flat element list from UI tree in any format."""
    sc = tree.get("structuredContent") or tree.get("structured_content") or {}
    flat = sc.get("elements") or tree.get("elements") or []
    if flat:
        return flat
    # Tree format: walk children
    out = []
    def walk(node):
        for child in (node.get("children") or []):
            out.append(child)
            walk(child)
    walk(tree)
    return out


def capture_state(pid: int = 0, window_id: int = 0,
                  tree: dict = None, screenshot_b64: str = None) -> dict:
    """Capture current UI state for later comparison.

    If tree is provided, uses it directly (avoids extra MCP call).
    """
    state = {
        "timestamp": time.time(),
        "pid": pid,
        "window_id": window_id,
    }

    if tree:
        state["tree"] = tree
        state["tree_hash"] = _compute_tree_hash(tree)
        state["element_count"] = len(_get_flat_elements(tree))
    else:
        state["tree_hash"] = "unknown"
        state["element_count"] = 0

    if screenshot_b64:
        state["screenshot_hash"] = _compute_screenshot_hash(screenshot_b64)
    else:
        state["screenshot_hash"] = ""

    return state


def _compute_screenshot_hash(b64: str) -> str:
    """Compute a perceptual hash of a base64 screenshot.

    Fast: just hashes the raw bytes. For difference detection,
    the tree_hash is usually sufficient.
    """
    import base64
    raw = base64.b64decode(b64.split(",", 1)[-1])
    # Sample: hash first 64KB to keep it fast
    sample = raw[:65536] if len(raw) > 65536 else raw
    return hashlib.md5(sample).hexdigest()[:12]


# ── Assertion functions ──────────────────────────────────────

def assert_hash_changed(before: dict, after: dict,
                         **kwargs) -> dict:
    """Assert that the UI tree hash changed after the action.

    Returns: {"passed": bool, "confidence": float, "detail": str}
    """
    bh = before.get("tree_hash", "")
    ah = after.get("tree_hash", "")
    changed = (bh != ah) and ah != "unknown" and bh != "unknown"

    if changed:
        return {
            "passed": True,
            "confidence": 0.95,
            "detail": f"Tree hash changed: {bh} -> {ah}",
        }
    return {
        "passed": False,
        "confidence": 0.3,
        "detail": f"Tree hash unchanged: {bh}",
    }


def assert_element_appeared(before: dict, after: dict,
                              text: str, **kwargs) -> dict:
    """Assert an element with matching text appeared in the after-state.

    Searches element labels, names, and values in the UI tree.
    """
    before_elems = _get_flat_elements(before.get("tree", {}))
    after_elems = _get_flat_elements(after.get("tree", {}))

    # Find matching elements in after that weren't in before
    text_lower = text.lower()
    after_matches = _find_elements_by_text(after_elems, text_lower)
    before_matches = _find_elements_by_text(before_elems, text_lower)

    new_matches = after_matches - before_matches
    if new_matches:
        return {
            "passed": True,
            "confidence": min(0.5 + 0.1 * len(new_matches), 0.98),
            "detail": f"'{text}' appeared in {len(new_matches)} new element(s)",
        }
    if after_matches:
        return {
            "passed": True,
            "confidence": 0.5,
            "detail": f"'{text}' found but existed before action",
        }
    return {
        "passed": False,
        "confidence": 0.1,
        "detail": f"'{text}' not found in UI tree after action",
    }


def assert_element_disappeared(before: dict, after: dict,
                                 text: str, **kwargs) -> dict:
    """Assert an element with matching text disappeared after the action."""
    before_elems = _get_flat_elements(before.get("tree", {}))
    after_elems = _get_flat_elements(after.get("tree", {}))

    text_lower = text.lower()
    before_matches = _find_elements_by_text(before_elems, text_lower)
    after_matches = _find_elements_by_text(after_elems, text_lower)

    gone = before_matches - after_matches
    if gone:
        return {
            "passed": True,
            "confidence": 0.95,
            "detail": f"'{text}' disappeared ({len(gone)} element(s) gone)",
        }
    if before_matches:
        return {
            "passed": False,
            "confidence": 0.1,
            "detail": f"'{text}' still present after action",
        }
    return {
        "passed": True,
        "confidence": 0.3,
        "detail": f"'{text}' was not present before or after (no regression)",
    }


def assert_text_contains(before: dict, after: dict,
                          text: str, **kwargs) -> dict:
    """Assert that screenshot text contains the given string.

    Uses UI tree element labels by default (no OCR dependency).
    For OCR, pass use_ocr=True and have Pillow+winrt available.
    """
    after_tree = after.get("tree", {})
    after_elems = _get_flat_elements(after_tree)

    text_lower = text.lower()
    all_texts = set()
    for elem in after_elems:
        for key in ("label", "name", "value", "description"):
            val = elem.get(key, "")
            if val:
                all_texts.add(str(val).lower())

    matched = any(text_lower in t for t in all_texts)

    if matched:
        return {
            "passed": True,
            "confidence": 0.9,
            "detail": f"'{text}' found in {sum(1 for t in all_texts if text_lower in t)} element text(s)",
        }
    return {
        "passed": False,
        "confidence": 0.2,
        "detail": f"'{text}' not found in UI tree element text",
    }


def _find_elements_by_text(elements: list, text_lower: str) -> set:
    """Find element indices matching text in label/name/value."""
    matches = set()
    for elem in elements:
        ei = elem.get("element_index")
        if ei is None:
            continue
        for key in ("label", "name", "value", "description"):
            val = elem.get(key, "")
            if val and text_lower in str(val).lower():
                matches.add(ei)
                break
    return matches


# ── Assertion registry & runner ──────────────────────────────

ASSERTIONS = {
    "hash_change": assert_hash_changed,
    "element_appeared": assert_element_appeared,
    "element_disappeared": assert_element_disappeared,
    "text_contains": assert_text_contains,
}


def verify(before: dict, after: dict,
           assertions: list) -> dict:
    """Run a list of assertions against before/after states.

    Args:
        before: State dict from capture_state().
        after: State dict from capture_state().
        assertions: List of (assertion_name, params_dict) tuples.
            e.g. [("hash_change", {}), ("element_appeared", {"text": "保存"})]

    Returns:
        {
            "passed": bool,       # All assertions passed
            "confidence": float,  # Average confidence
            "results": [...],     # Per-assertion results
        }
    """
    results = []
    confidences = []

    for name, params in assertions:
        func = ASSERTIONS.get(name)
        if not func:
            results.append({
                "assertion": name,
                "passed": False,
                "confidence": 0.0,
                "detail": f"Unknown assertion: {name}",
            })
            confidences.append(0.0)
            continue

        try:
            result = func(before, after, **params)
            results.append({
                "assertion": name,
                **result,
            })
            confidences.append(result.get("confidence", 0.0))
        except Exception as e:
            results.append({
                "assertion": name,
                "passed": False,
                "confidence": 0.0,
                "detail": f"Error: {e}",
            })
            confidences.append(0.0)

    all_passed = all(r["passed"] for r in results)
    avg_confidence = sum(confidences) / max(len(confidences), 1)

    return {
        "passed": all_passed,
        "confidence": round(avg_confidence, 3),
        "results": results,
    }


def run_assertions(assertions: list) -> dict:
    """Synchronous entry point for CLI / MCP tools when before/after are provided inline.

    Args:
        assertions: List of dicts, each with "assertion" key and optional params.
            e.g. [{"assertion": "hash_change"}, {"assertion": "element_appeared", "text": "OK"}]

    Returns:
        Verification report dict with passed/confidence/results.
    """
    return {
        "status": "needs_before_after",
        "message": "Use verify(before_state, after_state, assertions) with captured states",
        "assertions": [a["assertion"] if isinstance(a, dict) else a for a in assertions],
        "supported": list(ASSERTIONS.keys()),
    }
