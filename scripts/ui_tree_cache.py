# © 2026 BOBLIANG. All rights reserved.
"""UI Tree Cache with TTL, local invalidation, and fingerprint index mapping."""
import threading
import time
from typing import Any, Optional
from scripts.element_fingerprint import get_elements, fingerprint_index_map

class UiTreeCache:
    """Caches the UI tree with per-action-type TTL and local invalidation."""
    def __init__(self, ttl_ms: int = 2000):
        self._lock = threading.Lock()
        self._tree: Optional[dict] = None
        self._timestamp: float = 0.0
        self._ttl: float = ttl_ms / 1000.0
        self._fingerprints: dict[str, int] = {}
        self._elements: list[dict] = []

    @property
    def stale(self) -> bool:
        with self._lock:
            return self._tree is None or (time.monotonic() - self._timestamp) > self._ttl

    def get(self, force_refresh: bool = False) -> Optional[dict]:
        with self._lock:
            if force_refresh or self.stale:
                return None
            return self._tree

    def set(self, tree: dict):
        with self._lock:
            self._tree = tree
            self._elements = get_elements(tree)
            self._fingerprints = fingerprint_index_map(self._elements)
            self._timestamp = time.monotonic()

    def get_element_index(self, fingerprint: str,
                          old_element: dict = None) -> Optional[int]:
        """Find element by fingerprint. Falls back to resilient matching on miss.

        Args:
            fingerprint: SHA-256 fingerprint string (exact match).
            old_element: Optional full element dict for resilient fallback
                         matching when exact SHA-256 fails.
        """
        with self._lock:
            # 1) Exact SHA-256 match (fast path)
            idx = self._fingerprints.get(fingerprint)
            if idx is not None:
                return idx

        # 2) Resilient weighted matching (fallback)
        if old_element is not None:
            try:
                from scripts.resilient_matcher import find_best_match, extract_fingerprint
                with self._lock:
                    candidates = list(self._elements)
                if not candidates:
                    return None
                query_fp = extract_fingerprint(old_element)
                result_idx, score = find_best_match(query_fp, candidates)
                if result_idx is not None and score >= 0.6:
                    with self._lock:
                        self._fingerprints[fingerprint] = result_idx
                    return result_idx
            except ImportError:
                pass
        return None

    def get_element_by_fingerprint(self, fingerprint: str) -> Optional[dict]:
        with self._lock:
            idx = self._fingerprints.get(fingerprint)
            if idx is not None and idx < len(self._elements):
                return self._elements[idx]
            return None

    def invalidate(self, action_type: str = "", element_ref: Any = None):
        with self._lock:
            if action_type in ("click", "type", "paste", "insert_text"):
                self._timestamp = 0.0
            elif action_type in ("scroll", "hover", "focus"):
                pass
            else:
                self._timestamp = 0.0

    def touch(self):
        with self._lock:
            self._timestamp = time.monotonic()

    def clear(self):
        with self._lock:
            self._tree = None
            self._fingerprints.clear()
            self._timestamp = 0.0

    @property
    def element_count(self) -> int:
        with self._lock:
            return len(self._elements)
