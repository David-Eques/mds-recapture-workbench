"""Deterministic text rules for two explicitly supported NTA diagnoses."""

from __future__ import annotations

from ..base import (
    EngineContext,
    FindingCandidate,
    RawEvidence,
    active_diagnosis_evidence_in_window,
    document_in_window,
    extract_sentence_containing,
    find_in_docs,
)

_I8000_SLOTS = ("I8000A", "I8000B", "I8000C")


class NtaSupportedNotCoded:
    """Section I8000 comorbidities documented + actively treated but not coded."""

    rule_id = "NTA_COMORBIDITY_SUPPORTED_NOT_CODED"

    def detect(self, ctx: EngineContext) -> list[FindingCandidate]:
        k = ctx.knowledge
        fx = ctx.case
        docs = fx.documents
        items = fx.mds_items

        found: list[tuple] = []
        for c in k.nta:
            if "I8000" not in c.mds_items or not c.icd_codes:
                continue
            if not set(c.icd_codes) & {"K86.1", "E11.351"}:
                continue
            if k.coded_in(items, c):
                continue
            active_docs = [doc for doc in docs if document_in_window(fx, doc, days=7)]
            treat_ev = self._treatment_evidence(active_docs, c, k)
            if treat_ev is None:
                # Codeability: no active-treatment language -> refuse (history-only). If MENTIONED,
                # that's a deliberate decline worth logging (009: not_active_in_lookback).
                if ctx.decisions is not None:
                    name_ev = None
                    for phrase in c.name_phrases:
                        name_ev = find_in_docs(docs, phrase)
                        if name_ev:
                            break
                    if name_ev is not None:
                        ctx.decisions.declined(
                            "not_active_in_lookback",
                            target="I8000",
                            detail={
                                "condition": c.condition,
                                "candidate_dx": list(c.icd_codes),
                                "document_id": name_ev.document_id,
                                "quote": name_ev.quote,
                            },
                        )
                continue
            name_ev = self._name_evidence(docs, c, treat_ev)
            if name_ev is None:
                continue
            if not active_diagnosis_evidence_in_window(fx, name_ev, treat_ev):
                if ctx.decisions is not None:
                    ctx.decisions.declined(
                        "not_active_in_lookback",
                        target="I8000",
                        detail={"condition": c.condition, "candidate_dx": list(c.icd_codes)},
                    )
                continue
            found.append((c, name_ev, treat_ev))

        if not found:
            return []

        changes: dict[str, str] = {}
        codes: list[str] = []
        evidence: list[RawEvidence] = []
        for i, (c, name_ev, treat_ev) in enumerate(found):
            code = c.icd_codes[0]
            codes.append(code)
            if i < len(_I8000_SLOTS):
                changes[_I8000_SLOTS[i]] = code
            for ev in (name_ev, treat_ev):
                if ev and ev not in evidence:
                    evidence.append(ev)

        return [
            FindingCandidate(
                rule_id=self.rule_id,
                direction="under_capture",
                mds_item="I8000",
                current_value="Documented NTA comorbidities not present in Section I8000",
                suggested_value="Add " + ", ".join(codes) + " to Section I8000",
                title="Documented NTA comorbidities not coded in Section I8000",
                rationale=(
                    "The chart documents active, physician-treated NTA comorbidities that are "
                    "absent from Section I8000. Coding them raises the NTA comorbidity score; "
                    "NTA payment is banded (not linear), so the dollar impact is computed by "
                    "re-running the grouper as-coded vs as-supported, not by counting points."
                ),
                component="NTA",
                evidence=evidence,
                proposed_item_changes=changes,
                candidate_dx_codes=tuple(codes),
                is_primary_dx_change=False,
                active_treatment_supported=True,
            )
        ]

    @staticmethod
    def _treatment_evidence(docs, c, k) -> RawEvidence | None:  # noqa: ANN001
        for tp in c.treatment_phrases:
            ev = find_in_docs(docs, tp)
            if ev:
                return ev
        for doc in docs:
            for name in c.name_phrases:
                ev = extract_sentence_containing(doc, name)
                if ev and any(am in ev.quote.lower() for am in k.active_treatment_markers):
                    return ev
        return None

    @staticmethod
    def _name_evidence(docs, c, treat_ev) -> RawEvidence | None:  # noqa: ANN001
        treat_doc = next((d for d in docs if d.document_id == treat_ev.document_id), None)
        if treat_doc is not None:
            for phrase in c.name_phrases:
                if phrase.lower() in treat_doc.text.lower():
                    ev = extract_sentence_containing(treat_doc, phrase)
                    if ev:
                        return ev
        for phrase in c.name_phrases:
            ev = find_in_docs(docs, phrase)
            if ev:
                return ev
        return None


class NtaCodedNotSupported:
    """NTA comorbidity coded in the MDS with no support in the available record (over-capture)."""

    rule_id = "NTA_COMORBIDITY_CODED_NOT_SUPPORTED"

    def detect(self, ctx: EngineContext) -> list[FindingCandidate]:
        k = ctx.knowledge
        fx = ctx.case
        docs = fx.documents
        items = fx.mds_items

        out: list[FindingCandidate] = []
        for c in k.nta:
            if not k.coded_in(items, c):
                continue
            if self._supported(docs, c, k):
                continue
            neg_ev = None
            for np in c.negation_phrases:
                neg_ev = find_in_docs(docs, np)
                if neg_ev:
                    break
            if not neg_ev:
                continue
            item = c.mds_items[0]
            label = c.condition.lower()
            out.append(
                FindingCandidate(
                    rule_id=self.rule_id,
                    direction="over_capture",
                    mds_item=item,
                    current_value=f"{c.condition} ({item}) is coded on the in-progress MDS",
                    suggested_value=(
                        f"Review for removal: no active {label} is documented in the available record"
                    ),
                    title=f"Coded {label} ({item}) has no supporting documentation",
                    rationale=(
                        "This NTA comorbidity is coded, but the available record does not support "
                        "it. Surfaced for review (support not found in available documents): never "
                        "an instruction to delete, and the engine does not fabricate support. Over-"
                        "capture is a review flag with no dollar under the review-only safety policy "
                        "(corpus completeness unproven)."
                    ),
                    component="NTA",
                    evidence=[neg_ev],
                    proposed_item_changes={item: ""},
                )
            )
        return out

    @staticmethod
    def _supported(docs, c, k) -> bool:  # noqa: ANN001
        for doc in docs:
            low = doc.text.lower()
            if not any(p.lower() in low for p in c.name_phrases):
                continue
            if any(tp.lower() in low for tp in c.treatment_phrases):
                return True
            if any(am in low for am in k.active_treatment_markers):
                return True
        return False
