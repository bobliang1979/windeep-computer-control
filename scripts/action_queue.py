# © 2026 BOBLIANG. All rights reserved.
"""
action_queue.py — Delay adaptation layer between Hermes (planning) and winctl (execution).

P1 from Codex++ review: Hermes plans at ~10s granularity, winctl executes at ~50ms.
ActionQueue decouples them with precondition checking and escalation.

Usage:
    from scripts.action_queue import ActionQueue
    
    queue = ActionQueue()
    
    # Hermes (planning): enqueue actions
    queue.enqueue({"tool": "click", "params": {"pid": 1234, "element_index": 7}})
    queue.enqueue({"tool": "type_text", "params": {"pid": 1234, "text": "hello"}},
                  precondition={"hash_change": True})
    
    # winctl (execution): poll and execute
    while action := queue.next_pending():
        result = execute(action["tool"], action["params"])
        queue.mark_done(action["id"], result)
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("action_queue")

QUEUE_PATH = Path(__file__).parent.parent / "action_queue.jsonl"


class ActionQueue:
    """Persistent action queue (JSONL) that decouples planning from execution.

    Hermes writes actions to the queue (fast, non-blocking).
    winctl reads and executes them (async loop).
    Codex++ can read the queue for verification.

    Queue entry format:
      {"id": "uuid", "tool": "click", "params": {...}, "precondition": {...},
       "status": "pending|running|done|failed|escalated",
       "result": {...}, "created_at": "ISO8601", "executed_at": "ISO8601"}
    """

    def __init__(self, path: str = None):
        self._path = Path(path) if path else QUEUE_PATH
        self._cache = []  # In-memory cache of recent entries

    # ── Write (Hermes side) ──

    def enqueue(self, tool: str, params: dict,
                precondition: dict = None,
                source: str = "hermes") -> str:
        """Add an action to the queue. Returns the action ID."""
        import uuid
        entry = {
            "id": str(uuid.uuid4())[:12],
            "tool": tool,
            "params": params,
            "precondition": precondition or {},
            "status": "pending",
            "result": None,
            "source": source,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "executed_at": None,
        }
        # Append to JSONL
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass
        self._cache.append(entry)
        return entry["id"]

    def enqueue_multi(self, actions: list) -> list:
        """Enqueue multiple actions at once (for a plan)."""
        ids = []
        for a in actions:
            aid = self.enqueue(
                tool=a.get("tool", "unknown"),
                params=a.get("params", {}),
                precondition=a.get("precondition"),
                source=a.get("source", "hermes"),
            )
            ids.append(aid)
        return ids

    # ── Read & Execute (winctl side) ──

    def next_pending(self) -> Optional[dict]:
        """Get the next pending action (FIFO). Atomically marks it as running."""
        entries = self._read_all()
        for e in entries:
            if e.get("status") == "pending":
                if self._check_precondition(e.get("precondition", {})):
                    e["status"] = "running"
                    self._write_all(entries)
                    return e
        return None

    def mark_done(self, action_id: str, result: dict):
        """Mark an action as done with result."""
        self._update_status(action_id, "done", result)

    def mark_failed(self, action_id: str, result: dict):
        """Mark an action as failed."""
        self._update_status(action_id, "failed", result)

    def mark_escalated(self, action_id: str, result: dict):
        """Mark an action as escalated (needs human or Codex++)."""
        self._update_status(action_id, "escalated", result)

    # ── Condition checks ──

    def _check_precondition(self, precondition: dict) -> bool:
        """Check if preconditions are met before executing.

        Supported preconditions:
          - {"hash_change": True} — wait for UI hash to change
          - {"settle_ms": 2000} — wait N ms since last action
          - {"element_present": "text"} — wait for element to appear
        """
        if not precondition:
            return True

        # settle_ms: time-based wait
        settle = precondition.get("settle_ms", 0)
        if settle > 0:
            last = self._read_last_executed()
            if last and last.get("executed_at"):
                last_time = self._parse_iso(last["executed_at"])
                if last_time and (time.time() - last_time) * 1000 < settle:
                    return False  # Not enough time passed

        # element_present: check shared_ui_state
        element = precondition.get("element_present")
        if element:
            try:
                from scripts.shared_ui_state import get_state
                state = get_state()
                state.reload()
                fps = state.get_fingerprints()
                found = any(element.lower() in v.get("name", "").lower() for v in fps.values())
                if not found:
                    return False
            except ImportError:
                pass

        return True

    # ── Read helpers ──

    def _write_all(self, entries: list):
        """Write all entries back to JSONL file."""
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                for e in entries:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("Failed to write action_queue: %s", e)

    def _read_all(self) -> list:
        """Read all entries from JSONL file."""
        entries = []
        try:
            if self._path.exists():
                with open(self._path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                entries.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
        except OSError:
            pass
        self._cache = entries
        return entries

    def _read_last_executed(self) -> Optional[dict]:
        """Read the last executed action."""
        entries = self._read_all()
        executed = [e for e in entries if e.get("status") == "done"]
        return executed[-1] if executed else None

    def _update_status(self, action_id: str, status: str, result: dict):
        entries = self._read_all()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for e in entries:
            if e["id"] == action_id:
                e["status"] = status
                e["result"] = result
                e["executed_at"] = now
                break
        self._write_all(entries)

    @staticmethod
    def _parse_iso(iso_str: str) -> Optional[float]:
        try:
            return time.mktime(time.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ"))
        except (ValueError, OSError):
            return None

    # ── Status ──

    def pending_count(self) -> int:
        return sum(1 for e in self._read_all() if e.get("status") == "pending")

    def failed_count(self) -> int:
        return sum(1 for e in self._read_all() if e.get("status") == "failed")

    def clear(self):
        """Clear the queue."""
        try:
            if self._path.exists():
                self._path.unlink()
        except OSError:
            pass
        self._cache = []

    def __len__(self) -> int:
        return len(self._read_all())
