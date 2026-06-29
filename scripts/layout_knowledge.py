"""layout_knowledge.py — Cross-session UI layout knowledge persistence.

Learns element layouts per (process_name, window_class) key and persists
them to disk. Subsequent sessions skip cold-discovery by matching against
known layouts via window-title similarity.

Usage:
    from scripts.layout_knowledge import get_layout_knowledge

    db = get_layout_knowledge()

    # Before capture: check if we already know this window
    known = db.lookup("Code.exe", "settings.json - VS Code", "Chrome_WidgetWin_1")
    if known:
        # Use cached element positions directly
        elements = known

    # After successful capture: learn the layout
    db.learn("Code.exe", title, class_name, elements, fingerprint_map)

Storage: ~/.layout_knowledge/ui_layouts.json (JSON, human-readable).
"""

import json
import os
import threading
import time
from pathlib import Path
from difflib import SequenceMatcher
from typing import Optional, Dict, Any, List

# ── Storage ─────────────────────────────────────────────────────

_SCRIPTS_DIR = Path(__file__).parent.resolve()
STORAGE_DIR = _SCRIPTS_DIR / ".layout_knowledge"
STORAGE_FILE = STORAGE_DIR / "ui_layouts.json"

# Window title similarity threshold (0.0-1.0) for fuzzy key matching.
# 0.5 means "main.py - VS Code" matches "settings.json - VS Code" via
# shared token overlap {"-", "VS", "Code"} / union = 0.6.
TITLE_SIMILARITY_THRESHOLD = 0.5

# Max layouts to keep per process (LRU eviction)
MAX_PER_PROCESS = 5


# ── Core class ──────────────────────────────────────────────────

class LayoutKnowledge:
    """Persistent cross-session UI layout knowledge base.

    Keys are (process_name, window_class) pairs — stable across
    window title changes. Matching also checks title similarity
    so a layout learned on "main.py - VS Code" can be matched
    against "settings.json - VS Code".
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._layouts: Dict[str, dict] = {}
        self._dirty = False
        self._load()

    # ── Persistence ──

    def _load(self):
        """Load layouts from disk."""
        with self._lock:
            try:
                if STORAGE_FILE.exists():
                    raw = STORAGE_FILE.read_text(encoding="utf-8")
                    self._layouts = json.loads(raw) if raw.strip() else {}
            except (json.JSONDecodeError, OSError):
                self._layouts = {}

    def _save(self):
        """Save layouts to disk atomically."""
        with self._lock:
            try:
                STORAGE_DIR.mkdir(parents=True, exist_ok=True)
                tmp = STORAGE_FILE.with_suffix(".tmp")
                tmp.write_text(
                    json.dumps(self._layouts, indent=2, ensure_ascii=False,
                               default=str),
                    encoding="utf-8"
                )
                tmp.replace(STORAGE_FILE)
                self._dirty = False
            except OSError:
                pass  # Non-critical; will retry on next learn()

    # ── Key management ──

    @staticmethod
    def _make_key(process_name: str, window_class: str) -> str:
        """Stable key: process|window_class."""
        return f"{process_name}|{window_class}"

    @staticmethod
    def _title_similarity(a: str, b: str) -> float:
        """Levenshtein-derived similarity for window titles."""
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    # ── Public API ──

    def lookup(self, process_name: str, window_title: str,
               window_class: str) -> Optional[dict]:
        """Look up a known layout.

        Returns the full stored entry dict (with 'elements', 'title',
        'class', 'process', 'learned_at') if a sufficiently similar
        layout exists, None otherwise.

        Match priority:
          1. Exact (process, class) match + title similarity ≥ threshold
          2. Fuzzy (process, class) match + best title similarity
        """
        key = self._make_key(process_name, window_class)

        # Strategy 1: exact key match
        entry = self._layouts.get(key)
        if entry:
            stored_title = entry.get("title", "")
            if self._title_similarity(window_title, stored_title) >= TITLE_SIMILARITY_THRESHOLD:
                return entry

        # Strategy 2: fuzzy key match (different title, same process+class)
        best_score = 0.0
        best_entry = None
        for sk, se in self._layouts.items():
            if sk.startswith(process_name + "|") and window_class in sk:
                stored_title = se.get("title", "")
                score = self._title_similarity(window_title, stored_title)
                if score > best_score:
                    best_score = score
                    best_entry = se

        if best_score >= TITLE_SIMILARITY_THRESHOLD:
            return best_entry

        return None

    def learn(self, process_name: str, window_title: str,
              window_class: str, elements: list,
              fingerprint_map: dict = None):
        """Learn a layout from captured elements.

        Args:
            process_name: e.g. "Code.exe"
            window_title: e.g. "main.py - VS Code"
            window_class: UIA class name, e.g. "Chrome_WidgetWin_1"
            elements: List of element dicts from get_window_state
            fingerprint_map: Dict of {element_index: fingerprint}
        """
        key = self._make_key(process_name, window_class)
        now = time.time()

        with self._lock:
            self._layouts[key] = {
                "title": window_title,
                "class": window_class,
                "process": process_name,
                "learned_at": now,
                "element_count": len(elements) if elements else 0,
                "elements": elements,
                "fingerprints": fingerprint_map or {},
            }
            self._dirty = True

            # LRU eviction: keep at most MAX_PER_PROCESS layouts per process
            proc_entries = [
                (k, v) for k, v in self._layouts.items()
                if k.startswith(process_name + "|")
            ]
            if len(proc_entries) > MAX_PER_PROCESS:
                proc_entries.sort(key=lambda x: x[1].get("learned_at", 0))
                for old_key, _ in proc_entries[:len(proc_entries) - MAX_PER_PROCESS]:
                    del self._layouts[old_key]

        self._save()

    def forget(self, process_name: str, window_class: str = None):
        """Remove cached layouts for a process.

        Args:
            process_name: e.g. "Code.exe"
            window_class: Optional — if set, only forget layouts for
                          this specific class
        """
        prefix = process_name + "|"
        to_delete = [
            k for k in self._layouts
            if k.startswith(prefix) and
               (window_class is None or window_class in k)
        ]
        for k in to_delete:
            del self._layouts[k]
        if to_delete:
            self._dirty = True
            self._save()

    def stats(self) -> dict:
        """Return usage statistics."""
        return {
            "total_layouts": len(self._layouts),
            "processes": list(set(
                k.split("|")[0] for k in self._layouts
            )),
            "elements_cached": sum(
                v.get("element_count", 0) for v in self._layouts.values()
            ),
            "storage_file": str(STORAGE_FILE),
            "file_exists": STORAGE_FILE.exists(),
        }

    def save(self):
        """Force save if dirty."""
        if self._dirty:
            self._save()


# ── Singleton accessor ──────────────────────────────────────────

_layout_db: Optional[LayoutKnowledge] = None


def get_layout_knowledge() -> LayoutKnowledge:
    """Return the singleton LayoutKnowledge instance."""
    global _layout_db
    if _layout_db is None:
        _layout_db = LayoutKnowledge()
    return _layout_db
