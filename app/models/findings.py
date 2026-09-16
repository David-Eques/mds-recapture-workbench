"""Internal finding model and honest Scenario-A labels."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .facts import EvidenceCitation

Direction = Literal["under_capture", "over_capture", "review"]

SCENARIO_A_EXCLUDES: tuple[str, ...] = (
    "wage_index",
    "sequestration",
    "vbp",
    "interrupted_stay",
    "denials",
    "payment_corrections",
)

_UNDER_CAPTURE_LABELS: dict[str, str] = {
    "NTA": (
        "Estimated single-assessment federal rate impact (urban, day 4-20 baseline, "
        "no wage index); not claim-level reimbursement"
    ),
    "SLP": (
        "Estimated single-assessment federal rate impact (urban, flat - SLP has no "
        "variable per diem, no wage index); not claim-level reimbursement"
    ),
    "PT_OT": (
        "Estimated single-assessment federal rate impact (urban, PT/OT day 1-20 baseline, "
        "no wage index); not claim-level reimbursement"
    ),
}

DEFAULT_DOLLAR_LABEL = (
    "Estimated single-assessment federal rate impact (urban, day 4-20 baseline, no "
    "wage index); not claim-level reimbursement"
)
OVER_CAPTURE_REVIEW_LABEL = "possible inconsistency: review required"


def dollar_label_for(component: str, direction: str) -> str:
    if direction == "under_capture":
        return _UNDER_CAPTURE_LABELS.get(component, DEFAULT_DOLLAR_LABEL)
    return OVER_CAPTURE_REVIEW_LABEL


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str
    stay_id: str
    assessment_id: str
    direction: Direction
    mds_item: str
    current_value: str | None = None
    suggested_value: str | None = None
    title: str
    rationale: str
    evidence: list[EvidenceCitation] = Field(default_factory=list)
    proposed_item_changes: dict[str, str] = Field(default_factory=dict)
    as_coded_hipps: str | None = None
    as_coded_scenario_a_rate: str | None = None
    as_supported_hipps: str | None = None
    as_supported_scenario_a_rate: str | None = None
    grouper_version: str | None = None
    scenario_a_component_delta: str | None = None
    scenario_a_component_delta_days_1_3: str | None = None
    not_claim_payment_estimate: bool = True
    excludes: list[str] = Field(default_factory=lambda: list(SCENARIO_A_EXCLUDES))
    dollar_label: str = DEFAULT_DOLLAR_LABEL
    manifest_version: str
