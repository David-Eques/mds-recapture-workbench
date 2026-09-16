"""Stream B Section K rules: mechanically-altered diet (SLP) and a guarded swallowing rule.

Ported from v0. K0520C3 is the FY2026 item (K0510C2 retired). The grouper owns the dollar.
"""

from __future__ import annotations

from ...models.decisions import DecisionReason
from ...verification.citation_verify import verify_evidence
from ..base import (
    EngineContext,
    FindingCandidate,
    RawEvidence,
    document_in_window,
    evidence_in_window,
    extract_sentence_containing,
    find_in_docs,
    find_positive_unnegated,
    is_target_negated,
)


def _is_unchecked(items: dict, key: str) -> bool:
    return str(items.get(key, "")).strip() in ("", "0")


class SectionKMechAlteredDiet:
    """K0520C3 (mechanically-altered diet while a resident) supported but not coded."""

    rule_id = "SECTION_K_MECH_ALTERED_DIET_SUPPORTED"

    def detect(self, ctx: EngineContext) -> list[FindingCandidate]:
        k = ctx.knowledge
        fx = ctx.case
        docs = [doc for doc in fx.documents if document_in_window(fx, doc, days=7)]
        if not _is_unchecked(fx.mds_items, "K0520C3"):
            return []

        # NEGATION-AWARE trigger (fixes v0's negation-blind defect, encoded in the negative
        # disallowlist): a negated mention ("no mechanically altered diet") must NOT trigger a
        # favorable K0520C3 finding. RES003's evidence is un-negated, so positive parity holds.
        diet_ev = None
        for phrase in k.diet_texture_phrases:
            diet_ev = find_positive_unnegated(docs, (phrase,))
            if diet_ev:
                break
        if not diet_ev:
            for document in fx.documents:
                for phrase in k.diet_texture_phrases:
                    mention = extract_sentence_containing(document, phrase)
                    if mention is None:
                        continue
                    reason: DecisionReason = (
                        "stale_evidence"
                        if not document_in_window(fx, document, days=7)
                        else "negated_target"
                        if is_target_negated(mention.quote, phrase)
                        else "insufficient_support"
                    )
                    if ctx.decisions is not None:
                        ctx.decisions.declined(
                            reason,
                            target="K0520C3",
                            detail={"rule_id": self.rule_id},
                            evidence=verify_evidence(
                                fx,
                                {"evidence": [{"document_id": mention.document_id, "quote": mention.quote}]},
                            ),
                        )
                    return []
            return []
        if not evidence_in_window(fx, diet_ev, days=7):
            return []

        evidence: list[RawEvidence] = [diet_ev]
        for np in k.swallowing_negation:
            ev = find_in_docs(docs, np)
            if ev and ev not in evidence:
                evidence.append(ev)
                break

        return [
            FindingCandidate(
                rule_id=self.rule_id,
                direction="under_capture",
                mds_item="K0520C3",
                current_value="K0520C3 (mechanically altered diet - while a resident) is not checked",
                suggested_value="Check K0520C3 - a texture-modified diet is documented in the dietary order",
                title="Documented mechanically-altered diet not coded in K0520C3",
                rationale=(
                    "A texture-modified (mechanically altered) diet is documented in the dietary "
                    "order but K0520C3 is not checked. Coding it moves the SLP component up one "
                    "diet/swallowing step. No swallowing disorder is asserted (none is documented), "
                    "so the correct step is the diet axis only. Dollar impact from the grouper."
                ),
                component="SLP",
                evidence=evidence,
                proposed_item_changes={"K0520C3": "1"},
            )
        ]


class SectionKSwallowingDisorder:
    """K0100 swallowing disorder supported but not coded -> SLP uplift. Guarded: emits nothing when
    the chart documents the ABSENCE of dysphagia, and only fires when no K0100 sub-item is checked."""

    rule_id = "SECTION_K_SWALLOWING_DISORDER_SUPPORTED"

    def detect(self, ctx: EngineContext) -> list[FindingCandidate]:
        k = ctx.knowledge
        fx = ctx.case
        docs = [doc for doc in fx.documents if document_in_window(fx, doc, days=7)]
        blob = "\n".join(d.text for d in docs).lower()
        if any(n.lower() in blob for n in k.swallowing_negation):
            return []

        if any(str(fx.mds_items.get(f"K0100{c}", "")).strip() == "1" for c in "ABCD"):
            return []

        target: str | None = None
        for sign, subitem in k.k0100_signs:
            if find_positive_unnegated(docs, (sign,)) is not None:
                target = subitem
                break
        if target is None:
            return []

        evidence: list[RawEvidence] = []
        for sign, subitem in k.k0100_signs:
            if subitem != target:
                continue
            ev = find_positive_unnegated(docs, (sign,))
            if ev is not None and ev not in evidence:
                evidence.append(ev)
        evidence = [ev for ev in evidence if evidence_in_window(fx, ev, days=7)]
        if not evidence:
            return []

        return [
            FindingCandidate(
                rule_id=self.rule_id,
                direction="under_capture",
                mds_item=target,
                current_value="No swallowing disorder coded in Section K0100 (K0100Z = none of the above)",
                suggested_value=f"Review {target} - a swallowing-disorder sign is documented within the look-back",
                title="Documented swallowing disorder not coded in Section K0100",
                rationale=(
                    "Independent documents within the look-back record swallowing-disorder signs "
                    f"(mapped to {target}) but Section K0100 is coded 'none of the above'. Coding "
                    "the swallowing disorder raises the SLP case-mix. Dollar impact from the grouper."
                ),
                component="SLP",
                evidence=evidence,
                proposed_item_changes={target: "1", "K0100Z": "0"},
            )
        ]
