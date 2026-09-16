"""Stream A deterministic engine: MDSRecord + StructuredSignals -> candidates ->
CounterfactualMdsBuilder -> dual-grouper -> Scenario-A delta -> Finding. The grouper owns every dollar
(invariant #1); over-capture carries no dollar; the payment-period gate excludes unsupported stays
from the dollar aggregate (a finding may still surface).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...codeability.counterfactual import CounterfactualMdsBuilder
from ...config.manifest import manifest_version
from ...grouper.mds_mapping import pps_reason_for
from ...models.facts import EvidenceCitation
from ...models.findings import SCENARIO_A_EXCLUDES, Finding, dollar_label_for
from ...models.mds import MDSRecord, StructuredSignal
from ...models.protocols import SingleSegmentPaymentPeriodEngine
from .base import StreamACandidate, StreamARule

if TYPE_CHECKING:
    from ...audit.decision_log import DecisionLog
from .nta_comorbidity import StreamANtaComorbidity
from .section_gg_therapy import StreamASectionGgTherapy
from .section_i_checkbox import StreamASectionICheckbox
from .section_k_diet import StreamASectionKDiet

# The full §7.1 structured-comparison catalog (no AI): NTA comorbidity, Section I active-diagnosis
# checkbox gap (the I2000-class error), Section K diet, Section GG vs. structured therapy data.
STREAM_A_RULES: list[StreamARule] = [
    StreamANtaComorbidity(),
    StreamASectionICheckbox(),
    StreamASectionKDiet(),
    StreamASectionGgTherapy(),
]


def _provenance_citations(candidate: StreamACandidate) -> list[EvidenceCitation]:
    """For Stream A the structured record IS the citation — provenance = source_table (no chart text)."""
    seen: list[str] = []
    for src in candidate.structured_provenance:
        if src and src not in seen:
            seen.append(src)
    return [
        EvidenceCitation(
            document_id=src,
            quote=src,
            verified=True,
            verification_basis="structured_record_reference",
            document_type="structured_signal",
        )
        for src in seen
    ]


def derive_stream_a(
    mds: MDSRecord,
    signals: list[StructuredSignal],
    grouper,  # noqa: ANN001  (GrouperClient seam: group(assessment) + delta_items(...) -> GrouperDelta)
    *,
    payment_engine: SingleSegmentPaymentPeriodEngine | None = None,
    decisions: DecisionLog | None = None,
) -> list[Finding]:
    payment_engine = payment_engine or SingleSegmentPaymentPeriodEngine()
    builder = CounterfactualMdsBuilder()
    in_scope = payment_engine.in_v1_dollar_scope(mds)
    stamp = manifest_version()
    ard_str = mds.ard.isoformat()

    # Safety short-circuit: a stay whose as-coded baseline the grouper cannot price (an RTP / ungroupable
    # primary -> empty HIPPS) gets NO recapture findings — block it (grouper-derived, never a lexicon
    # guess). The negative space stays traceable when a DecisionLog is threaded.
    baseline = grouper.group(mds)
    if not getattr(baseline, "groupable", True) or not getattr(baseline, "hipps", ""):
        if decisions is not None:
            decisions.blocked("rtp_primary", target="I0020B", detail={"as_coded_hipps": ""})
        return []

    candidates: list[StreamACandidate] = []
    for rule in STREAM_A_RULES:
        candidates.extend(rule.detect(mds, signals))

    findings: list[Finding] = []
    for i, c in enumerate(candidates, start=1):
        states = {item: c.state for item in c.proposed_item_changes}
        payload = builder.build(mds, c.proposed_item_changes, states)
        if not payload.applied_changes:
            continue  # nothing SUPPORTED was applied -> no counterfactual change

        is_under = c.direction == "under_capture"
        # Review-only over-capture never enters the counterfactual dollar path. Favorable deltas come
        # only from the grouper seam (invariant #1); the rules layer never imports pricing.
        if is_under:
            delta = grouper.delta_items(
                mds.items,
                payload.item_values,
                ard_str,
                pps_reason_for(mds.assessment_type),
            )
            baseline_delta, days_1_3 = delta.delta_daily, delta.delta_daily_days_1_3
        else:
            delta = None
            baseline_delta, days_1_3 = None, None

        priced = is_under and in_scope  # over-capture carries no dollar; out-of-scope is excluded
        excludes = list(SCENARIO_A_EXCLUDES)
        if not in_scope:
            excludes.append("payment_period_out_of_v1_scope")

        findings.append(
            Finding(
                finding_id=f"{mds.stay_id}-A{i}",
                stay_id=mds.stay_id,
                assessment_id=mds.stay_id,
                direction=c.direction,
                mds_item=c.mds_item,
                current_value=c.current_value,
                suggested_value=c.suggested_value,
                title=c.title,
                rationale=c.rationale,
                evidence=_provenance_citations(c),
                proposed_item_changes=dict(c.proposed_item_changes),
                as_coded_hipps=(delta.as_coded.hipps if delta else baseline.hipps),
                as_coded_scenario_a_rate=(
                    delta.as_coded.estimated_daily_rate if delta else baseline.estimated_daily_rate
                ),
                as_supported_hipps=(delta.as_supported.hipps if delta else None),
                as_supported_scenario_a_rate=(delta.as_supported.estimated_daily_rate if delta else None),
                grouper_version=getattr(delta.as_supported, "grouper_version", None) if delta else None,
                scenario_a_component_delta=(baseline_delta if priced else None),
                scenario_a_component_delta_days_1_3=(days_1_3 if priced else None),
                excludes=excludes,
                dollar_label=dollar_label_for(c.component, c.direction),
                manifest_version=stamp,
            )
        )

    if decisions is not None:
        for f in findings:
            decisions.surfaced(f)
        if not findings:
            decisions.no_gap(detail={"stream": "A", "candidates": len(candidates)})
    return findings
