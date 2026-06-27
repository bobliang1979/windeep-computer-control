# © 2026 BOBLIANG. All rights reserved.
﻿__version__ = "1.0.0"
"""windeep.scripts — Shared helper modules for computer control."""

from .ui_tree_cache import UiTreeCache
from .element_fingerprint import element_fingerprint, get_elements, match_by_fingerprint, fingerprint_index_map
from .ocr_finder import ocr_find_text, ocr_get_all_text, ocr_available
from .smart_matcher import smart_find, smart_click, smart_find_supported
from .assertion_verifier import verify, capture_state, assert_hash_changed, assert_element_appeared, assert_text_contains
from .shared_ui_state import SharedUIState, get_state
from .action_queue import ActionQueue
