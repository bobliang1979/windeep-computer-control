# Shared Vocabulary (Frozen at 2026-06-27)

> This file defines the precise meaning of shared terms between Hermes and Codex++.
> Any term not listed here is [OOV] — must be defined before use.
> OOV Protocol: When either party uses a term not in this file, the other party auto-tags it `[OOV: term]` and negotiates a definition in the same turn.

---

## confidence

| Source | Definition | Scale | Notes |
|--------|-----------|-------|-------|
| Hermes | assertion_verifier's numerical output (based on before/after hash comparison, element presence, text match) | 0.0–1.0 | Computed; no human judgment |
| Codex++ | Subjective reliability estimate of an audit judgment (based on experience & pattern match) | 0.0–1.0 | Subjective; backed by calibration history |

**Convention**: When Codex++ says `confidence: 0.95`, it maps to assertion_verifier's `confidence > 0.8` threshold. When Codex++ says `confidence: 0.50`, it maps to `0.3 < confidence < 0.8` (mid zone requiring evidence chain). When Codex++ says `confidence: < 0.3`, escalation.

---

## escalate

| Source | Definition | Trigger |
|--------|-----------|---------|
| Hermes | action_queue status: operation failed and cannot auto-recover | 3 consecutive failures of the same action |
| Codex++ | Problem exceeds my knowledge boundary, needs Hermes or human | No clear pattern match in index |

**Convention**: Unified as "current layer cannot handle, pass upward". Escalation always includes: (1) what was tried, (2) evidence, (3) what the next layer needs to decide.

---

## settle

| Source | Definition | Formula |
|--------|-----------|---------|
| Hermes | Adaptive delay after an action before verification | `min(2000, max(200, median(history) × 1.5))` |
| Codex++ | Same definition | Same formula |

**Convention**: Unified. Never use fixed 750ms. History window: last 20 entries per action key.

---

## calibration

| Source | Definition | When computed |
|--------|-----------|--------------|
| Hermes | `quality_calibration()` — correlation between predicted confidence and actual outcome | After every L1 closure, batch compute after 10+ records |
| Codex++ | Same: tracks whether my confidence estimates are well-calibrated | Same schedule |

**Convention**: `corr > 0.3` = calibrated (trust confidence). `corr ≤ 0.3` = uncalibrated (treat confidence as noise, always escalate mid-zone).

---

## ATP / energy

| Source | Definition | Zones |
|--------|-----------|-------|
| Hermes | Operational energy level of the control stack, derived from recent failure rate | high > 70%, warning 20–70%, coma < 20% |
| Codex++ | Same: how much processing budget the system should spend per action | Same zones |

**Convention**: ATP is computed by `compute_energy(failure_rate_last_10, time_since_last_failure)`. High = full MCTS + screenshot comparison. Warning = streamlined verify only, skip screenshot diff. Coma = enter COMATH replay analysis of accumulated failures.

---

## success / failure

| Source | Hermes definition | Codex++ definition |
|--------|------------------|-------------------|
| Hermes | Action completed with `result.success == True` and hash changed / element appeared | — |
| Codex++ | Audit finding matched actual behavior in next execution | — |

**Convention**: For Hermes, success is empirical (hash/state change). For Codex++, success is predictive accuracy (calibration). These are different metrics — do not conflate.

---

## fingerprint

| Source | Definition |
|--------|-----------|
| Hermes | SHA256 truncation (first 6 hex chars) of an element's role + name + bounds, used to identify UI elements across reloads |
| Codex++ | Same concept — unique stable identifier for an entity |

**Convention**: Fingerprints are for UI elements only. They are not used for cognitive state or lesson identity.

---

## precondition

| Source | Definition | Supported types |
|--------|-----------|----------------|
| Hermes | `action_queue` check that must pass before executing an action | `hash_change`, `settle_ms`, `element_present` |
| Codex++ | Same concept — condition that gates execution | Same types |

**Convention**: Preconditions are evaluated client-side (in action_queue). Failures block the action, don't escalate.

---

## OOV (Out Of Vocabulary)

Any term not listed in this file. When encountered:
1. Auto-tag: `[OOV: term]`
2. Propose a definition in the same turn
3. If the other party accepts, add to this file
4. If rejected, renegotiate

**Exception**: Technical terms from Python stdlib (`uuid`, `json`, `pathlib`) and well-known CS terms (`MCTS`, `CQRS`, `FIFO`, `L1/L2/L3`) do not need definition unless their meaning is specific to this collaboration.

**ATP note**: In this collaboration, `ATP` always means "operational energy level of the control stack", not the biochemical molecule. Context disambiguates.

---

## OOV (mechanism)

Auto-tagging protocol for terms not in this vocabulary. When either party encounters an undefined term, it is tagged `[OOV: term]` and a definition is negotiated in the same turn before proceeding.

## OOV (status)

A term that is not in this vocabulary. When tagged, it means "this term's meaning is undefined — negotiate before use."

---

## trust

| Aspect | Definition | Formula |
|--------|-----------|---------|
| Hermes trust in Codex++ | Whether Codex++'s confidence estimates are reliable | `trust = True if calibration.corr > 0.3 else False` |
| Codex++ trust in Hermes | Whether Hermes assertion results are accurate | `trust = True if assertion_verifier false-positive rate < 10% else False` |

**Convention**: `trust` is always a boolean. When `trust = False`, mid-zone confidence (0.3–0.8) auto-escalates to human. `trust` is not a gradient — it is a binary gate derived from `calibration`.

---

## settle_during

The waiting period between executing an action and verifying its result. This is not the `settle` value itself — it is the temporal window during which `settle` is being spent.

**Convention**: During `settle_during`, S0 (Explorer) monitors for unexpected popups or async window changes. Do NOT confuse `settle_during` (the time window) with `settle` (the duration value).

---

## Action items after vocabulary freeze

1. All future Hermes→Codex++ messages MUST use terms according to this vocabulary
2. Codex++ audit responses MUST match the `confidence` convention when stating confidence
3. JSONL calibration entries MUST use the `calibration` definition above
4. OOV terms trigger immediate definition — never glide past undefined terms
