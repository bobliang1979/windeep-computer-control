"""resilient_matcher.py — Weighted multi-attribute element matching.

Replaces brittle SHA-256 exact fingerprint matching with weighted
similarity scoring across multiple attributes.

When AutomationId is available: short-circuit to exact match (score=1.0).
When not: combine Name, ClassName, ControlType, ParentPath via
weighted Levenshtein similarity.

Usage:
    from scripts.resilient_matcher import ResilientMatcher
    
    matcher = ResilientMatcher()
    idx, score = matcher.find(query_fingerprint, candidates)
    if score >= 0.6:
        element = candidates[idx]
"""

import json
from difflib import SequenceMatcher
from typing import List, Optional, Tuple


# ── Default weights ─────────────────────────────────────────────
# Order matters: higher weight = more reliable attribute
W_AUTOID   = 0.50  # AutomationId exact match → short-circuit
W_NAME     = 0.35  # Name: Levenshtein similarity
W_CLASS    = 0.08  # ClassName: exact match
W_TYPE     = 0.05  # ControlType: exact match  
W_PARENT   = 0.02  # ParentPath: sequence overlap

MATCH_THRESHOLD = 0.55  # Minimum score to consider a match
# When Name similarity alone is below this, reject even if combined score passes
MIN_NAME_SIMILARITY = 0.45


# ── Similarity helpers ──────────────────────────────────────────

def _levenshtein_similarity(a: str, b: str) -> float:
    """Normalized string similarity [0, 1] via SequenceMatcher."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _path_similarity(a_path: list, b_path: list) -> float:
    """Parent path overlap score [0, 1].

    Paths like ["Pane", "ToolBar", "Window"] — count shared
    suffix length (most specific shared ancestors).
    """
    if not a_path or not b_path:
        return 0.5  # Neutral when no path info
    # Count matching suffix elements (from end = most specific)
    matches = 0
    for ai, bi in zip(reversed(a_path), reversed(b_path)):
        if ai == bi:
            matches += 1
        else:
            break
    max_len = max(len(a_path), len(b_path))
    return matches / max_len if max_len > 0 else 0.0


def _extract_parent_path(elem: dict) -> list:
    """Extract parent class chain from an element dict.

    Supports both cua-driver 'elements' format (with parent_index)
    and tree format (with children/ancestors).
    """
    # Direct parent_path field
    pp = elem.get("parent_path") or elem.get("_parent_path")
    if pp:
        return pp if isinstance(pp, list) else [pp]

    # Build from parent_index chain (needs full elements array)
    parent_idx = elem.get("parent_index")
    if parent_idx is not None:
        return []  # Needs full tree context; caller should pass it

    # Flattened tree format
    ancestors = elem.get("ancestors")
    if ancestors:
        return [a.get("class_name", "") for a in ancestors if a.get("class_name")]

    return []


# ── Fingerprint extraction ──────────────────────────────────────

def extract_fingerprint(elem: dict) -> dict:
    """Extract the 5 matching attributes from an element dict.

    Returns a dict with keys: automation_id, name, class_name,
    control_type, parent_path.
    """
    return {
        "automation_id": (elem.get("automation_id") or "").strip(),
        "name": (elem.get("name") or elem.get("label") or "").strip(),
        "class_name": (elem.get("class_name") or "").strip(),
        "control_type": str(elem.get("control_type") or
                          elem.get("role") or ""),
        "parent_path": _extract_parent_path(elem),
    }


# ── Weighted matcher ────────────────────────────────────────────

class ResilientMatcher:
    """Weighted multi-attribute element matcher with configurable threshold."""

    def __init__(self, threshold: float = MATCH_THRESHOLD):
        self.threshold = threshold

    def find(self, query_fp: dict, candidates: List[dict],
             return_score: bool = False) -> Optional[int]:
        """Find best matching candidate index.

        Args:
            query_fp: Fingerprint dict from extract_fingerprint()
            candidates: List of element dicts with matching attributes.
            return_score: If True, returns tuple (index, score).

        Returns:
            Element index in candidates list, or None if below threshold.
        """
        if not query_fp or not candidates:
            return None

        best_idx = -1
        best_score = 0.0

        for i, cand in enumerate(candidates):
            score = self._score(query_fp, cand)

            # Short-circuit on perfect AutomationId match
            if score >= 1.0:
                return (i, score) if return_score else i

            if score > best_score:
                best_score = score
                best_idx = i

        if best_score >= self.threshold:
            return (best_idx, best_score) if return_score else best_idx
        return None

    def find_with_score(self, query_fp: dict,
                        candidates: List[dict]) -> Tuple[Optional[int], float]:
        """Like find() but always returns (index, score)."""
        result = self.find(query_fp, candidates, return_score=True)
        if result is not None:
            return result
        return (None, 0.0)

    def _score(self, query_fp: dict, candidate: dict) -> float:
        """Compute weighted similarity score between query and candidate."""
        cand_fp = extract_fingerprint(candidate)

        q_autoid = query_fp.get("automation_id", "")
        c_autoid = cand_fp.get("automation_id", "")

        # AutomationId: exact match = short-circuit
        if q_autoid and c_autoid:
            if q_autoid == c_autoid:
                return 1.0
            # Both have autoid but don't match → strong mismatch
            return 0.0

        # No automation_id on either side → use remaining attributes
        # Normalize weights to account for absent autoid
        remaining = W_NAME + W_CLASS + W_TYPE + W_PARENT
        if remaining <= 0:
            return 0.0

        score = 0.0
        name_sim = _levenshtein_similarity(
            query_fp.get("name", ""), cand_fp.get("name", ""))
        score += W_NAME * name_sim

        # Name similarity gate: if names are very different, reject
        if name_sim < MIN_NAME_SIMILARITY and not q_autoid:
            return 0.0

        q_cls = query_fp.get("class_name", "")
        c_cls = cand_fp.get("class_name", "")
        if q_cls and c_cls:
            score += W_CLASS * (1.0 if q_cls == c_cls else 0.0)

        q_type = query_fp.get("control_type", "")
        c_type = cand_fp.get("control_type", "")
        if q_type and c_type:
            score += W_TYPE * (1.0 if q_type == c_type else 0.0)

        q_path = query_fp.get("parent_path", [])
        c_path = cand_fp.get("parent_path", [])
        score += W_PARENT * _path_similarity(q_path, c_path)

        # Normalize to [0, 1] range
        return score / remaining


# ── Convenience function ────────────────────────────────────────

def find_best_match(query_fp: dict, candidates: List[dict],
                    threshold: float = MATCH_THRESHOLD) -> Tuple[Optional[int], float]:
    """One-shot find best matching element.

    Args:
        query_fp: Fingerprint from extract_fingerprint()
        candidates: List of element dicts
        threshold: Minimum score (default 0.6)

    Returns:
        (index, score) — index is None if below threshold.
    """
    matcher = ResilientMatcher(threshold)
    return matcher.find_with_score(query_fp, candidates)
