"""Decision Audit Log model + the CLOSED disposition/reason taxonomy (ported from v0).

Every disposition the engine reaches for a candidate (or a per-run summary) gets a structured
``DecisionRecord`` — the negative space (declined/blocked/ignored) is as traceable as the positive
(surfaced). Declines/blocks/ignores carry NO dollar (invariant #2). Every row carries a
``manifest_version`` stamp (§0).

The ``Disposition`` Literal is CLOSED and matches v0 exactly so positive parity holds. Quarantine
(linkage mismatch) and payment-period exclusion are modeled as ``audit_log`` EVENTS, not new
dispositions (decision #15) — they do not widen this taxonomy.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .facts import EvidenceCitation

Disposition = Literal["surfaced", "declined", "blocked", "ignored"]

DecisionReason = Literal[
    # --- v0 closed set (reproduced exactly in positive parity) ---
    "gap_surfaced",  # surfaced  — a normal finding
    "no_gap",  # declined  — run considered candidates, nothing to surface (one summary/run)
    "citation_unverified",  # declined  — the supporting quote failed str.find
    "not_active_in_lookback",  # declined  — support is history-only / outside the active window
    "conflicting_evidence",  # declined  — evidence both asserts and contradicts the same item
    "rtp_primary",  # blocked   — as-coded I0020B maps to Return to Provider
    "injected_instruction",  # ignored   — chart text tried to instruct the system; treated as inert data
    # --- v1 extensions (defined now; triggered in step 2+) ---
    "outside_lookback",  # declined  — AssessmentPeriodRule resolved the evidence date out of window
    "corpus_incomplete",  # declined  — over-capture withheld because corpus completeness is unproven
    "payment_period_out_of_v1_scope",  # declined — IPA/interrupted/missing-A2400 excluded from $ aggregate
    "stale_evidence",
    "historical_evidence",
    "negated_target",
    "discontinued_evidence",
    "low_ocr_confidence",
    "insufficient_support",
]


class DecisionRecord(BaseModel):
    """One disposition the engine reached (or a per-run summary). A companion to findings, never
    embedded in one. ``evidence``, when present, is str.find-verified just like a finding's."""

    model_config = ConfigDict(extra="ignore")

    decision_id: str
    run_id: str | None = None
    assessment_id: str
    resident_surrogate_id: str
    disposition: Disposition
    reason: DecisionReason
    target: str | None = None
    finding_id: str | None = None
    detail: dict = Field(default_factory=dict)
    evidence: list[EvidenceCitation] = Field(default_factory=list)
    manifest_version: str = ""
    created_at: str
    explanation: str = ""

    def model_post_init(self, __context: object) -> None:
        if not self.explanation:
            from ..audit.presentation import decision_reason

            self.explanation = decision_reason(self.reason)[1]
