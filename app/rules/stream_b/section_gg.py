"""Stream B Section GG rules: walking coded not-attempted despite documented ambulation
(under-capture), and mobility coded more dependent than documented (over-capture). Ported from v0.
Both localize to the Section GG function score; the grouper owns the dollar."""

from __future__ import annotations

from ..base import (
    EngineContext,
    FindingCandidate,
    RawEvidence,
    document_in_window,
    extract_sentence_containing,
)

_WALKING_ITEMS = ("GG0170I1", "GG0170J1", "GG0170K1")
_GG_REAL_PERFORMANCE_CODES = {"01", "02", "03", "04", "05", "06"}


def _supported_assist_for_task(docs, task_phrases, assist_map):  # noqa: ANN001
    """First sentence naming one of ``task_phrases`` that also carries a mapped assist level →
    (evidence, GG code). Same-sentence match keeps one task's assist level from another item."""
    for doc in docs:
        for task in task_phrases:
            ev = extract_sentence_containing(doc, task)
            if ev is None:
                continue
            low = ev.quote.lower()
            for phrase, code in assist_map:
                if phrase.lower() in low:
                    return ev, code
    return None, None


class SectionGgWalking:
    """Walking items coded not-attempted, but therapy documents ambulation with an assist level.
    Never overstates: assist level maps deterministically to a GG performance code."""

    rule_id = "SECTION_GG_MOBILITY_OBSERVATION_REVIEW"

    def detect(self, ctx: EngineContext) -> list[FindingCandidate]:
        k = ctx.knowledge
        fx = ctx.case
        items = fx.mds_items
        if not any(str(items.get(w, "")).strip() in k.gg_not_attempted_codes for w in _WALKING_ITEMS):
            return []

        therapy_docs = [
            d for d in fx.documents if d.document_type == "therapy_eval" or "PT" in d.document_id
        ] or fx.documents
        therapy_docs = [d for d in therapy_docs if document_in_window(fx, d, days=3, anchor="part_a_start")]
        task_map = (
            ("GG0170I1", ("walk 10 feet", "ambulated 10 feet", "able to walk 10 feet")),
            ("GG0170J1", ("walk 50 feet", "ambulated 50 feet")),
            ("GG0170K1", ("walk 150 feet", "ambulated 150 feet")),
        )
        proposed: dict[str, str] = {}
        evidence: list[RawEvidence] = []
        for item, phrases in task_map:
            if str(items.get(item, "")).strip() not in k.gg_not_attempted_codes:
                continue
            ev, performance = _supported_assist_for_task(therapy_docs, phrases, k.assist_level_to_gg)
            if ev is not None and performance is not None:
                proposed[item] = performance
                evidence.append(ev)
        if not proposed:
            return []

        codes = "/".join(sorted(set(proposed.values())))

        return [
            FindingCandidate(
                rule_id=self.rule_id,
                direction="under_capture",
                mds_item="/".join(proposed),
                current_value="Walking coded as not attempted (GG0170I1=88; walk-50ft and walk-150ft score 0)",
                suggested_value=(f"Update the specifically evidenced Section GG walking task(s) to {codes}"),
                title="Section GG walking coded not-attempted despite documented ambulation",
                rationale=(
                    "Section GG should reflect usual performance across the assessment window using "
                    "all observations, including therapy. The walking items were coded not-attempted "
                    "from day-1 status, but the therapy evaluation documents ambulation at a partial/"
                    "moderate assistance level. Only the walking items change (PT/OT function score). "
                    "Dollar impact from the grouper."
                ),
                component="PT_OT",
                evidence=evidence,
                proposed_item_changes=proposed,
            )
        ]


class SectionGgOverCapture:
    """Section GG mobility coded MORE dependent than the chart supports (over-capture). Guards:
    assist level co-located with the task in one sentence; coded value must be a real performance
    code (01-06). GG scale is inverted (higher = more independent)."""

    rule_id = "SECTION_GG_OVERCODED_DEPENDENCY_REVIEW"

    def detect(self, ctx: EngineContext) -> list[FindingCandidate]:
        k = ctx.knowledge
        fx = ctx.case
        items = fx.mds_items
        docs = [d for d in fx.documents if document_in_window(fx, d, days=3, anchor="part_a_start")]

        proposed: dict[str, str] = {}
        evidence: list[RawEvidence] = []
        for item, task_phrases in k.gg_overcapture_items:
            coded = str(items.get(item, "")).strip()
            if coded not in _GG_REAL_PERFORMANCE_CODES:
                continue
            ev, supported = _supported_assist_for_task(docs, task_phrases, k.assist_level_to_gg)
            if supported is None:
                continue
            if int(supported) > int(coded):  # documented MORE independent than coded -> over-coded
                proposed[item] = supported
                if ev is not None and ev not in evidence:
                    evidence.append(ev)
        if not proposed or not evidence:
            return []

        codes = "/".join(sorted(set(proposed.values())))
        return [
            FindingCandidate(
                rule_id=self.rule_id,
                direction="over_capture",
                mds_item="/".join(proposed),
                current_value="Section GG mobility coded at a higher dependency than the record documents",
                suggested_value=(
                    f"Review Section GG: the therapy/nursing notes document supervision-level "
                    f"performance (GG {codes}) for these tasks, below the coded dependency"
                ),
                title="Section GG mobility coded more dependent than the record supports",
                rationale=(
                    "Section GG mobility items are coded at a higher dependency than the therapy and "
                    "nursing notes document for the same tasks. Over-stated GG dependency inflates the "
                    "function score. Surfaced as review-required (over-capture is a review flag with no "
                    "dollar under the review-only safety policy); a human corrects the assessment before lock."
                ),
                component="PT_OT",
                evidence=evidence,
                proposed_item_changes=proposed,
            )
        ]
