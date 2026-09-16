"""Look-back / codeability models (imported from the ARD/IPA spec §4). v1 uses the recapture-relevant
subset. CandidateItemState drives fail-closed favorable additions (invariant #10): NOT_ASSESSED never
becomes SUPPORTED.

Reconciliation #12: v1's MDSRecord.assessment_type uses raw A0310B reason codes ("01"/"08"); the
ARD/IPA period rules key on "5_day"/"ipa". A single normalization map bridges the boundary.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .facts import EvidenceCitation

AssessmentTypeKey = Literal["5_day", "ipa", "obra", "discharge"]

# A0310B reason code  <->  ARD/IPA period-rule assessment_type key (reconciliation #12).
_A0310B_TO_KEY: dict[str, AssessmentTypeKey] = {"01": "5_day", "08": "ipa"}


def assessment_type_key(a0310b_reason: str) -> AssessmentTypeKey:
    """Normalize a raw A0310B reason code to the period-rule key. Unknown codes fail closed."""
    try:
        return _A0310B_TO_KEY[a0310b_reason]
    except KeyError as exc:
        raise ValueError(
            f"unmapped A0310B reason {a0310b_reason!r} (expected one of {_A0310B_TO_KEY})"
        ) from exc


class AssessmentPeriodRule(BaseModel):
    """Resolved EXACTLY on (item_id, subitem_id, column_id, assessment_type). window_kind is explicit."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    subitem_id: str | None = None
    column_id: str | None = None
    assessment_type: AssessmentTypeKey
    anchor: Literal["part_a_start", "ard", "admission_entry_reentry", "discharge"]
    window_kind: Literal["fixed", "lookback", "prior_to_anchor"]
    start_offset_days: int | None = None
    end_offset_days: int | None = None
    lookback_days: int | None = None
    inclusive: bool = True
    special_criteria: str | None = None


class EvidenceDate(BaseModel):
    """Mechanically-verifiable date provenance for an ARD-windowed fact (ARD/IPA §4 invariant #3).
    A fact whose date_kind is unknown/reported_history or verified=False is BLOCKED from windowing."""

    model_config = ConfigDict(extra="forbid")

    date_value: date
    date_kind: Literal[
        "service_administered",
        "observation_date",
        "order_date",
        "document_created",
        "document_signed",
        "reported_history",
        "unknown",
    ]
    citation: EvidenceCitation | None = None
    source_field: str | None = None
    verified: bool = False

    def is_windowable(self) -> bool:
        return self.verified and self.date_kind not in ("unknown", "reported_history")


class CandidateItemState(StrEnum):
    SUPPORTED = "supported"  # verified, in-window, gate-passing -> code positive
    UNSUPPORTED = "unsupported"  # claimed but no qualifying support at this ARD
    NEGATIVE = "negative"  # affirmatively absent
    NOT_ASSESSED = "not_assessed"
    DASH_ALLOWED = "dash_allowed"
    DASH_NOT_ALLOWED = "dash_not_allowed"
    VALIDATION_ERROR = "validation_error"


class AssessmentContext(BaseModel):
    """The minimal recapture context: hold the ARD, vary the coding (v1 §4)."""

    model_config = ConfigDict(extra="forbid")

    assessment_type: AssessmentTypeKey
    a0310b: Literal["01", "08"]
    ard: date
    part_a_start: date | None = None  # A2400B
    admission_entry_reentry: date | None = None
