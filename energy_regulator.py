# energy_regulator.py — cognitive-kit to winctl MCP bridge
#
# Wraps the EnergyEngine with execution strategy regulation.
# Use this module to make any action executor energy-aware.
#
# Copyright 2026 BOBLIANG. All rights reserved.

import os, sys

# Find cognitive-kit
_KIT_CANDIDATES = [
    os.path.expanduser("~/Desktop/_Projects/认知架构/cognitive-kit"),
    os.path.expanduser("~/Desktop/cognitive-kit"),
    os.path.join(os.path.dirname(__file__), "..", "..", "认知架构", "cognitive-kit"),
]
for p in _KIT_CANDIDATES:
    resolved = os.path.abspath(p)
    if os.path.exists(os.path.join(resolved, "cognitive_kit")):
        sys.path.insert(0, resolved)
        break

from cognitive_kit.energy import EnergyEngine

__all__ = ["EnergyRegulator", "get_regulator"]

# Default state file path (alongside this module)
_DEFAULT_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "energy_state.json")


class EnergyRegulator:
    """Energy-aware execution regulator.

    Bridges cognitive-kit's EnergyEngine into any action executor.
    Provides:
      - should_proceed(): gate before each action
      - record_and_regulate(success): record outcome, return regulation advice
      - get_strategy(): current execution strategy with behavioral guidance
    """

    def __init__(self, state_path: str = None):
        self._engine = EnergyEngine(state_path or _DEFAULT_STATE)

    # ── Regulation ──

    def should_proceed(self) -> tuple:
        """Check if execution should proceed based on energy zone.

        Returns:
            (proceed: bool, reason: str, advice: dict)
        """
        status = self._engine.get_status()
        zone = status["zone"]

        if zone == "coma":
            return (False,
                    "energy zone is COMA — stop execution",
                    {"action": "enter_comath",
                     "detail": "Run enter_comath() before next action"})

        if zone == "warning":
            return (True,
                    "energy zone is WARNING — streamline execution",
                    {"action": "streamline",
                     "skip_screenshot": True,
                     "skip_mcts": True,
                     "reduce_verify": True})

        return (True,
                "energy zone is HIGH — full execution",
                {"action": "normal",
                 "skip_screenshot": False,
                 "skip_mcts": False,
                 "reduce_verify": False})

    def record_and_regulate(self, success: bool) -> dict:
        """Record an action outcome and return regulation advice.

        Args:
            success: Whether the action succeeded.

        Returns:
            dict with current energy status and regulation advice.
        """
        status = self._engine.record_action(success)
        zone = status["zone"]
        advice = {
            "high": {"level": "normal", "note": "Full execution"},
            "warning": {"level": "streamlined",
                        "note": "Skip screenshot, hash-only verify"},
            "coma": {"level": "halted",
                     "note": "Stop all actions. Enter COMATH cycle."},
        }
        return {
            **status,
            "advice": advice.get(zone, advice["high"]),
        }

    def get_strategy(self) -> dict:
        """Return current execution strategy with behavioral guidance.

        Returns:
            dict describing what to do at this energy level.
        """
        status = self._engine.get_status()
        zone = status["zone"]
        strategies = {
            "high": {
                "mode": "full",
                "settle": "adaptive (median*1.5)",
                "verify": "hash + screenshot comparison",
                "mcts_proposals": True,
                "introspection": "full",
                "meta": "Normal operation. All checks active.",
            },
            "warning": {
                "mode": "streamlined",
                "settle": "adaptive (median*1.5, capped at 1000ms)",
                "verify": "hash-only, screenshot skipped",
                "mcts_proposals": False,
                "introspection": "reduced",
                "meta": "Failure rate elevated. Conserve compute.",
            },
            "coma": {
                "mode": "halted",
                "settle": "N/A — not executing",
                "verify": "N/A — not executing",
                "mcts_proposals": False,
                "introspection": "comath_only",
                "meta": "STOP. Run enter_comath() to recover.",
            },
        }
        return {
            "zone": zone,
            "level": status["level"],
            "strategy": strategies.get(zone, strategies["high"]),
            "comath_cycles": status["comath_cycles"],
        }

    def enter_comath(self) -> dict:
        """Enter COMATH recovery cycle. Returns dream clusters and insight."""
        return self._engine.enter_comath()

    def get_status(self) -> dict:
        """Get raw energy status."""
        return self._engine.get_status()

    def reset(self):
        """Reset energy to full (after COMATH fix applied)."""
        self._engine.reset()


_regulator = None


def get_regulator(state_path: str = None) -> EnergyRegulator:
    global _regulator
    if _regulator is None:
        _regulator = EnergyRegulator(state_path)
    return _regulator
