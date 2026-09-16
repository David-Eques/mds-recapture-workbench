"""Human-readable, version-independent decision copy for every public workflow.

The engine stores a closed reason code for audit stability. Public payloads add a
friendly title and explanation from this module so OCR and compatibility paths
cannot drift into different descriptions of the same safety decision.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..models.decisions import DecisionReason, DecisionRecord

DECISION_REASON_COPY: dict[DecisionReason, tuple[str, str]] = {
    "gap_surfaced": (
        "Supported coding gap",
        "The evidence passed the required checks and the engine surfaced a reviewer finding.",
    ),
    "no_gap": (
        "No supported gap",
        "The engine reviewed the available records and found no codeable gap. The assessment appears correctly coded.",
    ),
    "citation_unverified": (
        "Citation could not be verified",
        "The proposed supporting phrase did not match the source text, so the candidate was withheld.",
    ),
    "not_active_in_lookback": (
        "Condition not active in the look-back",
        "The documentation describes a resolved or history-only condition, so it was not treated as active.",
    ),
    "conflicting_evidence": (
        "Evidence conflict",
        "The records support incompatible values for the same item. The engine withheld the change for human review.",
    ),
    "rtp_primary": (
        "Return to Provider safety block",
        "The as-coded primary diagnosis maps to Return to Provider. Grouping and dollars were blocked until the primary diagnosis is corrected.",
    ),
    "injected_instruction": (
        "Chart instruction ignored",
        "Instruction-like chart text was treated as clinical data, not as a command, and was ignored.",
    ),
    "outside_lookback": (
        "Evidence outside the look-back",
        "The supporting evidence is outside the assessment window, so it is not codeable for this assessment.",
    ),
    "corpus_incomplete": (
        "Document set not proven complete",
        "A possible over-capture remains review-only because the available document set cannot prove that support is absent.",
    ),
    "payment_period_out_of_v1_scope": (
        "Payment period excluded",
        "The assessment period is outside the supported single-assessment estimate, so no dollar was calculated.",
    ),
    "stale_evidence": (
        "Evidence outside the applicable period",
        "The document date falls outside the coding look-back, so the candidate was withheld.",
    ),
    "historical_evidence": (
        "Historical evidence",
        "The text describes historical, resolved, remote, or copied-forward information.",
    ),
    "negated_target": (
        "Target is negated",
        "The clinical statement negates the specific condition or service being evaluated.",
    ),
    "discontinued_evidence": (
        "Evidence is discontinued",
        "The relevant order or treatment is documented as discontinued or no longer active.",
    ),
    "low_ocr_confidence": (
        "OCR confidence below threshold",
        "The cited OCR span did not meet the 85 percent confidence floor.",
    ),
    "insufficient_support": (
        "Insufficient qualifying context",
        "The target phrase lacks the current order, treatment, task, or assistance context required by the rule.",
    ),
}


def decision_reason(reason: str) -> tuple[str, str]:
    """Return stable friendly copy, including a safe fallback for older reason codes."""
    if reason in DECISION_REASON_COPY:
        return DECISION_REASON_COPY[reason]
    title = reason.replace("_", " ").strip().capitalize() or "Engine decision"
    return title, "The engine recorded this compatibility decision for audit review."


def decision_payload(record: DecisionRecord | Mapping[str, Any]) -> dict[str, Any]:
    """Serialize a decision with the shared reviewer-facing fields."""
    data = record.model_dump(mode="json") if isinstance(record, DecisionRecord) else dict(record)
    reason = str(data.get("reason") or "")
    title, explanation = decision_reason(reason)
    return {
        "decision_id": data.get("decision_id"),
        "run_id": data.get("run_id"),
        "disposition": data.get("disposition"),
        "reason": reason,
        "friendly_title": title,
        "explanation": explanation,
        "target": data.get("target"),
        "timestamp": data.get("created_at"),
        "manifest_version": data.get("manifest_version"),
    }
