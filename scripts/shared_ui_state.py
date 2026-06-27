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
import calendar
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
            "energy": {
                "level": 100.0,         # 0-100 ATP energy
                "zone": "high",          # high | warning | coma
                "history": [],           # last 20 action outcomes (True/False)
                "last_failure_at": None, # ISO8601 of most recent failure
            },
            "_meta": {"version": 2, "last_writer": "unknown", "updated_at": ""},
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

    # ── ATP Energy Model (from C19×AEON meta-cognition pattern) ──

    ATP_HIGH_THRESHOLD = 70.0
    ATP_WARNING_THRESHOLD = 30.0
    ATP_HISTORY_SIZE = 20
    ATP_PENALTY_WEIGHT = 100.0  # failure_rate × 100 = max penalty 100 pts
    ATP_RECOVERY_RATE = 3.0    # minutes per recovery point (60 min = 20 pts full recovery)
    ATP_MAX_TIME_BONUS = 20.0

    def compute_energy(self, writer: str = "hermes"):
        """Compute ATP energy level from recent failure rate and time since last failure.

        Formula:
            ATP = 100 - failure_rate × 80 + time_recovery
            time_recovery = min(20, minutes_since_last_failure / 3)

        failure_rate: fraction of False outcomes in recent window (0-1)
        With 0 failures: 100 (high) → 20 minutes stable: 100 (still high)
        With 50% failures: 60 (warning) → 20 minutes stable: 80 (high)
        With 100% failures fresh: 20 (coma border) → 20min stable: 40 (warning)
        """
        history = self._data.setdefault("energy", {}).setdefault("history", [])
        last_failure_at = self._data["energy"].get("last_failure_at")

        # Failure rate: from recent window
        window = self.ATP_HISTORY_SIZE
        recent = history[-window:] if len(history) >= window else history
        if recent:
            failure_rate = sum(1 for r in recent if not r) / len(recent)
        else:
            failure_rate = 0.0

        # Time recovery: minutes since last failure
        time_since_failure = 0.0
        if last_failure_at:
            try:
                last_ts = calendar.timegm(
                    time.strptime(last_failure_at, "%Y-%m-%dT%H:%M:%SZ")
                )
                time_since_failure = (time.time() - last_ts) / 60.0  # minutes
            except (ValueError, OSError):
                pass
        time_bonus = min(self.ATP_MAX_TIME_BONUS,
                         time_since_failure / self.ATP_RECOVERY_RATE)

        # Compute ATP
        level = 100.0 - (failure_rate * self.ATP_PENALTY_WEIGHT) + time_bonus
        level = max(0.0, min(100.0, level))

        # Determine zone
        if level >= self.ATP_HIGH_THRESHOLD:
            zone = "high"
        elif level >= self.ATP_WARNING_THRESHOLD:
            zone = "warning"
        else:
            zone = "coma"

        self._data["energy"]["level"] = round(level, 1)
        self._data["energy"]["zone"] = zone
        self._save(writer)
        return self._data["energy"]

    def record_action_outcome(self, success: bool, writer: str = "hermes"):
        """Record whether an action succeeded or failed. Updates ATP energy."""
        history = self._data.setdefault("energy", {}).setdefault("history", [])
        history.append(success)
        if len(history) > self.ATP_HISTORY_SIZE:
            history[:] = history[-self.ATP_HISTORY_SIZE:]
        if not success:
            self._data["energy"]["last_failure_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
        return self.compute_energy(writer)

    def get_energy(self) -> dict:
        """Return current ATP energy state: level (0-100), zone, history length."""
        eng = self._data.get("energy", {})
        return {
            "level": eng.get("level", 100.0),
            "zone": eng.get("zone", "high"),
            "history_len": len(eng.get("history", [])),
            "last_failure_at": eng.get("last_failure_at"),
        }

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
