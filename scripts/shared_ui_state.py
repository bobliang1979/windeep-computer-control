# © 2026 BOBLIANG. All rights reserved.
"""
shared_ui_state.py — Shared UI state between Hermes and Codex++.

P0 from Codex++ review: eliminates planning-execution wait by sharing
a single JSON state file that both agents read/write.

Usage:
    from shared_ui_state import SharedUIState
    
    state = SharedUIState()
    state.update_fingerprints(ui_tree)
    state.record_settle("Chrome:click", 780)
    state.set_last_action({"action": "click", "success": True})
    
    # Codex++ reads:
    fingerprints = state.get_fingerprints()
    settle_ms = state.get_adaptive_settle("Chrome:click")
"""
"""Shared UI state between Hermes and Codex++."""
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("shared_ui_state")

STATE_PATH = Path(__file__).parent / "shared_ui_state.json"


class SharedUIState:
    """Shared UI state file. Both Hermes and Codex++ read/write.

    Structure:
    {
      "fingerprints": { "a3f8c2": {"name": "Send", "index": 5, "role": "Button"} },
      "settle_history": { "Chrome:click": [780, 810, 750], "adaptive_ms": 780 },
      "last_action": { "action": "click", "success": True, "at": "ISO8601" },
      "window_states": [ {"hwnd": 0x1234, "title": "Chrome", "pid": 12345} ],
      "cache": { "tree_ts": 1234567890, "element_count": 42 },
      "_meta": { "version": 1, "last_writer": "hermes", "updated_at": "ISO8601" }
    }
    """

    def __init__(self, path: str = None):
        self._path = Path(path) if path else STATE_PATH
        self._data = self._load()

    def _load(self) -> dict:
        try:
            if self._path.exists():
                with open(self._path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
        return {
            "fingerprints": {},
            "settle_history": {},
            "last_action": None,
            "window_states": [],
            "cache": {},
            "_meta": {"version": 1, "last_writer": "unknown", "updated_at": ""},
        }

    def _save(self, writer: str = "hermes"):
        self._data["_meta"]["last_writer"] = writer
        self._data["_meta"]["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.warning("Failed to save shared_ui_state: %s", e)

    # ── Fingerprints ──

    def update_fingerprints(self, elements: list, writer: str = "hermes"):
        """Store element fingerprints from a UI tree."""
        fps = {}
        for elem in elements:
            ei = elem.get("element_index")
            fp = elem.get("_fingerprint")
            if ei is not None and fp:
                fps[fp] = {
                    "index": ei,
                    "name": elem.get("name", "") or elem.get("label", ""),
                    "role": elem.get("role", ""),
                }
        self._data["fingerprints"] = fps
        self._save(writer)

    def get_fingerprints(self) -> dict:
        return self._data.get("fingerprints", {})

    def get_element_by_fingerprint(self, fp: str) -> Optional[dict]:
        return self._data.get("fingerprints", {}).get(fp)

    # ── Settle history ──

    def record_settle(self, key: str, ms: float, writer: str = "hermes"):
        history = self._data.setdefault("settle_history", {}).setdefault(key, [])
        history.append(ms)
        if len(history) > 20:
            history[:] = history[-20:]
        # Recompute adaptive settle
        median = sorted(history)[len(history) // 2]
        self._data["settle_history"]["_adaptive_" + key] = max(200, min(int(median * 1.5), 2000))
        self._save(writer)

    def get_adaptive_settle(self, key: str, default: int = 750) -> int:
        return self._data.get("settle_history", {}).get(
            "_adaptive_" + key, default
        )

    # ── Last action ──

    def set_last_action(self, action: dict, writer: str = "hermes"):
        action["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._data["last_action"] = action
        self._save(writer)

    def get_last_action(self) -> Optional[dict]:
        return self._data.get("last_action")

    # ── Window states ──

    def update_windows(self, windows: list, writer: str = "hermes"):
        self._data["window_states"] = windows[:20]  # Keep top 20
        self._save(writer)

    def get_windows(self) -> list:
        return self._data.get("window_states", [])

    # ── Cache ──

    def update_cache(self, key: str, value: Any, writer: str = "hermes"):
        self._data.setdefault("cache", {})[key] = value
        self._save(writer)

    def get_cache(self, key: str, default: Any = None) -> Any:
        return self._data.get("cache", {}).get(key, default)

    # ── Raw access ──

    def reload(self):
        """Re-read from disk. Call before reading when expecting updates from Codex++."""
        self._data = self._load()

    @property
    def last_writer(self) -> str:
        return self._data.get("_meta", {}).get("last_writer", "unknown")

    @property
    def updated_at(self) -> str:
        return self._data.get("_meta", {}).get("updated_at", "")


# ── Singleton for convenience ──
_state = None


def get_state() -> SharedUIState:
    global _state
    if _state is None:
        _state = SharedUIState()
    return _state
