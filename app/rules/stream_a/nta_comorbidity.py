"""Stream A (NO AI): NTA comorbidity coded-vs-supported from STRUCTURED signals (v1 §7.1 headline).

A structured coded-diagnosis signal is only the review tier alone (billing/problem-list); it needs
corroboration by an administered MAR / active order signal in the same look-back window to enter as a
candidate (the trust-tier policy). Codeability is the Section I active-diagnosis 60+7 two-step. The
structured record IS the citation (no NLP, no chart text). The grouper still owns every dollar.
"""

from __future__ import annotations

from ...models.mds import MDSRecord, StructuredSignal
from ...models.period import CandidateItemState
from ..knowledge import NtaComorbidity, load_knowledge
from .base import StreamACandidate

_I8000_SLOTS = ("I8000A", "I8000B", "I8000C")


def _corroborates(signal: StructuredSignal, c: NtaComorbidity) -> bool:
    """An administered MAR / active order signal supporting this comorbidity (by code or treatment phrase)."""
    if signal.trust not in ("administered", "order_active"):
        return False
    if signal.code and signal.code in c.icd_codes:
        return True
    low = signal.value.lower()
    return any(tp in low for tp in c.treatment_phrases)


class StreamANtaComorbidity:
    rule_id = "STREAM_A_NTA_COMORBIDITY_SUPPORTED_NOT_CODED"

    def detect(self, mds: MDSRecord, signals: list[StructuredSignal]) -> list[StreamACandidate]:
        from ...codeability.active_diagnosis import is_active_diagnosis_in_window

        k = load_knowledge()
        dx_by_code = {s.code: s for s in signals if s.signal_type == "coded_diagnosis" and s.code}

        changes: dict[str, str] = {}
        codes: list[str] = []
        provenance: list[str] = []
        slot = 0
        for c in k.nta:
            if "I8000" not in c.mds_items or not c.icd_codes:
                continue
            if k.coded_in(mds.items, c):
                continue
            icd = c.icd_codes[0]
            dx = dx_by_code.get(icd)
            if dx is None:
                continue  # no coded-diagnosis signal -> nothing to propose
            corr = next((s for s in signals if _corroborates(s, c)), None)
            if corr is None:
                continue  # coded dx alone is review-tier; needs structured corroboration (trust policy)
            # Codeability: the active-diagnosis 60-day documentation + 7-day active two-step.
            in_window = is_active_diagnosis_in_window(
                ard=mds.ard,
                physician_documented_on=dx.effective_date,
                active_status_on=corr.effective_date,
            )
            if not in_window:
                continue  # fail closed: outside the look-back window -> not codeable
            if slot < len(_I8000_SLOTS):
                changes[_I8000_SLOTS[slot]] = icd
                slot += 1
            codes.append(icd)
            provenance += [dx.source_table, corr.source_table]

        if not changes:
            return []
        return [
            StreamACandidate(
                rule_id=self.rule_id,
                direction="under_capture",
                mds_item="I8000",
                component="NTA",
                title="Documented NTA comorbidity not coded in Section I8000 (structured)",
                current_value="NTA comorbidity supported by structured signals but absent from Section I8000",
                suggested_value="Add " + ", ".join(codes) + " to Section I8000",
                rationale=(
                    "Structured coded diagnosis + active MAR/order corroboration for an NTA comorbidity "
                    "absent from Section I8000, in the active-diagnosis look-back. NTA payment is banded; "
                    "the dollar impact is the dual-grouper delta (as-coded vs as-supported), not a point count."
                ),
                proposed_item_changes=changes,
                state=CandidateItemState.SUPPORTED,
                structured_provenance=provenance,
                candidate_dx_codes=tuple(codes),
            )
        ]
