"""Element fingerprinting for stable UI element identification."""
import hashlib
import json
from typing import Any, Optional

def element_fingerprint(elem: dict) -> str:
    props = {
        "name": elem.get("name", "") or "",
        "control_type": elem.get("control_type", "") or "",
        "class_name": elem.get("class_name", "") or "",
        "automation_id": elem.get("automation_id", "") or "",
    }
    raw = json.dumps(props, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]

def get_elements(ui_tree: dict) -> list[dict]:
    # Handle flat elements array (cua-driver get_window_state format)
    sc = ui_tree.get("structuredContent") or ui_tree.get("structured_content") or {}
    flat = sc.get("elements") or ui_tree.get("elements") or []
    if flat:
        out = []
        for elem in flat:
            fp = element_fingerprint(elem)
            elem["_fingerprint"] = fp
            elem["_parent_fingerprint"] = ""
            out.append(elem)
        return out
    # Handle tree/children structure (Codex++ format)
    elements = []
    def walk(node, parent_fp=""):
        for child in (node.get("children") or []):
            fp = element_fingerprint(child)
            child["_fingerprint"] = fp
            child["_parent_fingerprint"] = parent_fp
            elements.append(child)
            walk(child, fp)
    walk(ui_tree)
    return elements

def match_by_fingerprint(target_fp, elements, parent_fp=None):
    for idx, elem in enumerate(elements):
        if elem.get("_fingerprint") == target_fp:
            if parent_fp is None or elem.get("_parent_fingerprint") == parent_fp:
                return idx
    return None

def fingerprint_index_map(elements):
    return {e["_fingerprint"]: i for i, e in enumerate(elements) if "_fingerprint" in e}
