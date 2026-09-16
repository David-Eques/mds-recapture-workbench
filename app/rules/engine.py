"""The recapture findings engine: facts (in chart text) -> ranked, costed Findings.

Pipeline: run rules -> candidates -> conflicting-evidence suppression -> safety gates ->
citation verify + drop unverified (invariant #3) -> build proposed as-supported MDS -> grouper for
the dollar (invariant #1) -> assemble Finding -> rank -> deterministic finding ids. The engine never
computes a dollar itself; rules never see one.

v1 deltas from v0: money fields renamed to the Scenario-A names; OVER-CAPTURE CARRIES NO DOLLAR
(a review flag — invariant #2 / §5.7); every Finding/DecisionRecord is manifest-stamped (§0).
``conflicting_evidence`` is WIRED (decision #1).
"""

from __future__ import annotations

from decimal import Decimal

from ..audit.decision_log import DecisionLog
from ..config.manifest import manifest_version
from ..grouper.mds_mapping import pps_reason_for
from ..models.case import AnalysisCase
from ..models.findings import Finding, dollar_label_for
from ..verification.citation_verify import has_verified_citation, verify_evidence
from .base import EngineContext, Rule, passes_codeability_gate, passes_rtp_gate
from .conflicts import detect_swallow_diet_conflict
from .knowledge import load_knowledge
from .qualification import citation_confidence_qualifies, qualify_candidate
from .stream_b.nta import NtaCodedNotSupported, NtaSupportedNotCoded
from .stream_b.section_gg import SectionGgOverCapture, SectionGgWalking
from .stream_b.section_k import SectionKMechAlteredDiet, SectionKSwallowingDisorder

# Fixed rule order (deterministic candidate generation).
RULES: list[Rule] = [
    NtaSupportedNotCoded(),
    NtaCodedNotSupported(),
    SectionKMechAlteredDiet(),
    SectionKSwallowingDisorder(),
    SectionGgWalking(),
    SectionGgOverCapture(),
]

_COMPONENT_ORDER = {"NTA": 0, "SLP": 1, "PT_OT": 2, "NURSING": 3}


def rtp_block(case: AnalysisCase, grouper, decisions: DecisionLog | None = None) -> bool:  # noqa: ANN001
    """Grouper-derived RTP pre-check (spec §5.1). If the primary maps to Return to Provider /
    the record is ungroupable, emit blocked/rtp_primary and return True (caller short-circuits)."""
    try:
        baseline = grouper.group(case.assessment)
    except Exception:
        return False
    is_rtp = baseline.clinical_category == "Return to Provider" or baseline.groupable is False
    if is_rtp and decisions is not None:
        decisions.blocked(
            "rtp_primary",
            target="I0020B",
            detail={
                "primary_diagnosis": (case.mds_items.get("I0020B") or "").strip(),
                "clinical_category": baseline.clinical_category,
                "note": "primary maps to Return to Provider / ungroupable; recapture not run (short-circuit)",
            },
        )
    return is_rtp


def derive(case: AnalysisCase, grouper, decisions: DecisionLog | None = None) -> list[Finding]:  # noqa: ANN001
    """Derive findings. ``decisions`` is an OPTIONAL Decision Audit Log sink; when None, the returned
    findings are unchanged. When provided, each disposition is recorded as a companion record."""
    knowledge = load_knowledge()
    ctx = EngineContext(case=case, knowledge=knowledge, decisions=decisions)

    if rtp_block(case, grouper, decisions):
        return []

    candidates = []
    for rule in RULES:
        candidates.extend(rule.detect(ctx))

    qualified = []
    for candidate in candidates:
        accepted, reason = qualify_candidate(ctx, candidate)
        if not accepted:
            if decisions is not None:
                decisions.declined(
                    reason or "insufficient_support",
                    target=candidate.mds_item,
                    detail={"rule_id": candidate.rule_id},
                )
            continue
        qualified.append(candidate)
    candidates = qualified

    # Conflicting-evidence suppression (decision #1): when in-window evidence both ASSERTS and
    # CONTRADICTS the same swallowing/diet item, surface NO finding and record one
    # conflicting_evidence decision (both-sides citations). Runs BEFORE gates/pricing.
    conflict = detect_swallow_diet_conflict(ctx)
    if conflict is not None:
        kept = [
            c for c in candidates if not any(c.mds_item.startswith(p) for p in conflict.suppress_prefixes)
        ]
        if len(kept) < len(candidates):
            if decisions is not None:
                raw = {
                    "evidence": [{"document_id": e.document_id, "quote": e.quote} for e in conflict.evidence]
                }
                decisions.declined(
                    "conflicting_evidence",
                    target=conflict.target,
                    detail={
                        "note": "in-window evidence both asserts and contradicts this swallowing/diet item; human adjudication required",
                        "suppressed_items": [c.mds_item for c in candidates if c not in kept],
                    },
                    evidence=verify_evidence(case, raw),
                )
        candidates = kept

    # Safety gates (split so each refusal logs its own reason).
    gated: list = []
    for c in candidates:
        if not passes_rtp_gate(c, knowledge.rtp_codes):
            if decisions is not None:
                decisions.blocked(
                    "rtp_primary",
                    target="I0020B",
                    detail={"rule_id": c.rule_id, "proposed_primary": c.proposed_item_changes.get("I0020B")},
                )
            continue
        if not passes_codeability_gate(c, ctx):
            if decisions is not None:
                decisions.declined(
                    "not_active_in_lookback",
                    target=c.mds_item,
                    detail={"rule_id": c.rule_id, "candidate_dx": list(c.candidate_dx_codes)},
                )
            continue
        gated.append(c)
    candidates = gated

    built: list[tuple] = []  # (candidate, verified_evidence, delta, days_1_3)
    for c in candidates:
        raw = {"evidence": [{"document_id": e.document_id, "quote": e.quote} for e in c.evidence]}
        verified = verify_evidence(case, raw)
        if not has_verified_citation(verified):
            if decisions is not None:
                decisions.declined(
                    "citation_unverified",
                    target=c.mds_item,
                    detail={
                        "component": c.component,
                        "rule_id": c.rule_id,
                        "unverified_quotes": [e.quote for e in c.evidence],
                    },
                )
            continue
        if not citation_confidence_qualifies(verified):
            if decisions is not None:
                decisions.declined(
                    "low_ocr_confidence",
                    target=c.mds_item,
                    detail={"rule_id": c.rule_id, "minimum_confidence": 0.85},
                    evidence=verified,
                )
            continue
        # A candidate that cannot price a valid as_supported state (grouper raises) or prices a zero
        # delta (no grouper change) is not a surfaceable gap. Skip it; the run resolves to no_gap.
        try:
            supported_items = {**case.mds_items, **c.proposed_item_changes}
            d = grouper.delta_items(
                case.mds_items,
                supported_items,
                case.ard,
                pps_reason_for(case.assessment_type),
            )
            if Decimal(d.delta_daily or "0") == 0:
                continue
        except Exception:
            continue
        days_1_3 = d.delta_daily_days_1_3 if d.delta_daily_days_1_3 != d.delta_daily else None
        built.append((c, verified, d.delta_daily, days_1_3))

    def _rank_key(item):
        c, _verified, delta, _d13 = item
        return (
            -abs(Decimal(delta or "0")),
            0 if c.direction == "under_capture" else 1,
            _COMPONENT_ORDER.get(c.component, 9),
            c.mds_item,
        )

    built.sort(key=_rank_key)

    stamp = manifest_version()
    findings: list[Finding] = []
    for i, (c, verified, delta, days_1_3) in enumerate(built, start=1):
        is_under = c.direction == "under_capture"
        findings.append(
            Finding(
                finding_id=f"{case.resident_id}-F{i}",
                stay_id=case.resident_id,
                assessment_id=case.assessment_id,
                direction=c.direction,
                mds_item=c.mds_item,
                current_value=c.current_value,
                suggested_value=c.suggested_value,
                title=c.title,
                rationale=c.rationale,
                evidence=verified,
                proposed_item_changes=dict(c.proposed_item_changes),
                as_coded_hipps=d.as_coded.hipps,
                as_coded_scenario_a_rate=d.as_coded.estimated_daily_rate,
                as_supported_hipps=d.as_supported.hipps,
                as_supported_scenario_a_rate=d.as_supported.estimated_daily_rate,
                grouper_version=getattr(d.as_supported, "grouper_version", None),
                # Dollars ride ONLY surfaced, in-scope under-capture (invariant #2 / §5.7).
                scenario_a_component_delta=(delta if is_under else None),
                scenario_a_component_delta_days_1_3=(days_1_3 if is_under else None),
                dollar_label=dollar_label_for(c.component, c.direction),
                manifest_version=stamp,
            )
        )

    # Decision Audit Log: a surfaced record per finding, and EXACTLY ONE no_gap summary iff the run
    # surfaced nothing AND recorded nothing of concern (no declines/ignores/blocks). 008 returns
    # before reaching here (RTP short-circuit); 009 records a decline+ignore upstream (no summary).
    if decisions is not None:
        for f in findings:
            decisions.surfaced(f)
        if not findings and not decisions.has_concern():
            decisions.no_gap(
                detail={
                    "candidates_considered": len(candidates),
                    "gaps_surfaced": 0,
                    "declines_of_concern": 0,
                }
            )
    return findings
