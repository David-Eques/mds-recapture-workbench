"""Uploaded MDS assessment and structured evidence models."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict


class MDSRecord(BaseModel):
    """Deterministic parse of the MDS export: as-coded item values + the as-billed HIPPS baseline,
    resolved by the §6 baseline-source hierarchy (NEVER assumed equal to the as-coded MDS HIPPS)."""

    model_config = ConfigDict(extra="forbid")

    stay_id: str
    assessment_type: str  # "01"=5-day, "08"=IPA, ...  (raw A0310B reason)
    ard: date
    items: dict[str, str]  # current FY2026 item codes (v1.20.1)
    a2400b: date | None = None  # Part A start (GG admission anchor)
    a2400c: date | None = None  # Part A end; missing/unexpected -> payment-period exclusion (§5.7)
    # As-billed baseline — resolved by hierarchy, NOT assumed equal to the as-coded MDS HIPPS.
    baseline_source: Literal["claim_paid", "iqies_accepted", "not_billed", "unknown"] = "unknown"
    baseline_hipps: str | None = None  # null/unknown or unreconciled -> excluded from the $ aggregate
    baseline_reconciled: bool = False
    source: Literal["uploaded"] = "uploaded"


class StructuredSignal(BaseModel):
    """Stream A structured ancillary evidence (NO AI). The trust tier sets candidate STRENGTH, not truth."""

    model_config = ConfigDict(extra="forbid")

    stay_id: str
    signal_type: Literal["coded_diagnosis", "medication", "diet_order", "therapy"]
    trust: Literal[
        "mds_item",  # accepted MDS value (highest)
        "administered",  # MAR administration in window
        "order_active",  # active order in window
        "physician_dx_current",  # current physician diagnosis
        "functional_observation",  # GG/functional performance observation in the assessment window
        # (authorized Section GG source per RAI — distinct from therapy_minutes; DOES drive GG case-mix)
        "billing_or_problem_list",  # review tier only — never alone satisfies an item
        "therapy_minutes",  # coverage/compliance only — does NOT drive case-mix
    ]
    value: str
    code: str | None = None  # ICD-10 / NDC / etc. when present
    effective_date: date | None = None  # tested against the AssessmentPeriodRule window
    source_table: str = ""  # provenance = the structured record (the "citation")
