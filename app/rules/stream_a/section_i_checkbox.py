"""Stream A (NO AI): Section I active-diagnosis checkbox gap — the I2000-class error (v1 §7.1).

A stay-reason diagnosis coded as the primary (I0020B) whose matching Section I nursing-trigger
checkbox is left unchecked → nursing-component under-capture (these checkboxes drive Extensive
Services / Special Care / Clinically Complex). The diagnosis being the *primary reason for the Part A
stay* is the highest-trust structured signal; codeability still runs the active-diagnosis two-step
(60-day documentation + 7-day active-status), corroborated by a coded-diagnosis signal + an active
MAR/order signal. The structured records ARE the citation (no NLP). The grouper owns the dollar.

Diabetes (E10/E11 → I2900) is deliberately EXCLUDED here: it also moves the NTA component (it is an
NTA comorbidity), so it is the NTA rule's domain — keeping this rule a clean nursing-component signal.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...models.mds import MDSRecord, StructuredSignal
from ...models.period import CandidateItemState
from .base import StreamACandidate, is_unchecked


@dataclass(frozen=True)
class SectionIClass:
    icd_prefixes: tuple[str, ...]
    checkbox: str  # the Section I active-diagnosis checkbox item
    label: str
    nursing_category: str  # the PDPM nursing category the checkbox drives
    corroboration_phrases: tuple[str, ...]  # general active-treatment vocabulary for this condition


# The I2000-class nursing-trigger checkboxes (current FY2026 item codes).
I2000_CLASS: tuple[SectionIClass, ...] = (
    SectionIClass(
        ("J12", "J13", "J14", "J15", "J16", "J17", "J18"),
        "I2000",
        "Pneumonia",
        "Clinically Complex",
        ("antibiotic", "ceftriaxone", "levofloxacin", "azithromycin", "sputum", "supplemental oxygen"),
    ),
    SectionIClass(
        ("A40", "A41", "R65.2"),
        "I2100",
        "Septicemia",
        "Special Care High",
        ("iv antibiotic", "vancomycin", "blood culture", "sepsis", "vasopressor"),
    ),
    SectionIClass(
        ("I60", "I61", "I62", "I63", "I64", "G45"),
        "I4500",
        "CVA, TIA, or Stroke",
        "Clinically Complex",
        ("antiplatelet", "clopidogrel", "anticoagulation", "stroke rehab", "dysphagia precautions"),
    ),
    SectionIClass(
        ("G82.2",),
        "I5100",
        "Paraplegia",
        "Special Care High",
        ("bowel program", "bladder program", "pressure injury care"),
    ),
    SectionIClass(
        ("G82.5",),
        "I5200",
        "Quadriplegia",
        "Special Care High",
        ("ventilator", "bowel program", "bladder program", "tracheostomy care"),
    ),
    SectionIClass(
        ("J44", "J45"),
        "I6200",
        "Asthma / COPD / Chronic Lung Disease",
        "Special Care High",
        ("nebulizer", "bronchodilator", "inhaler", "supplemental oxygen"),
    ),
)


def _matches(icd: str, prefixes: tuple[str, ...]) -> bool:
    code = icd.strip().upper()
    return any(code.startswith(p.upper()) for p in prefixes)


def _corroborates(signal: StructuredSignal, c: SectionIClass) -> bool:
    """An administered MAR / active order signal supporting the condition (by ICD code or phrase)."""
    if signal.code and _matches(signal.code, c.icd_prefixes):
        return True
    low = signal.value.lower()
    return any(p in low for p in c.corroboration_phrases)


class StreamASectionICheckbox:
    rule_id = "STREAM_A_SECTION_I_ACTIVE_DX_CHECKBOX_GAP"

    def detect(self, mds: MDSRecord, signals: list[StructuredSignal]) -> list[StreamACandidate]:
        from ...codeability.active_diagnosis import is_active_diagnosis_in_window

        primary = str(mds.items.get("I0020B", "")).strip()
        if not primary:
            return []
        cls = next((c for c in I2000_CLASS if _matches(primary, c.icd_prefixes)), None)
        if cls is None:
            return []
        if not is_unchecked(mds.items, cls.checkbox):
            return []  # the active-diagnosis checkbox is already coded — no gap

        # Documentation step: a coded-diagnosis signal for this condition (with a date).
        dx = next(
            (
                s
                for s in signals
                if s.signal_type == "coded_diagnosis" and s.code and _matches(s.code, cls.icd_prefixes)
            ),
            None,
        )
        if dx is None:
            return []
        # Active-status step: an administered MAR / active order signal bound to the condition, in window.
        corr = next(
            (s for s in signals if s.trust in ("administered", "order_active") and _corroborates(s, cls)),
            None,
        )
        if corr is None:
            return []  # primary code alone is not enough — needs active-status corroboration (fail closed)
        if not is_active_diagnosis_in_window(
            ard=mds.ard,
            physician_documented_on=dx.effective_date,
            active_status_on=corr.effective_date,
        ):
            return []  # outside the 60+7 look-back -> not codeable (fail closed)

        return [
            StreamACandidate(
                rule_id=self.rule_id,
                direction="under_capture",
                mds_item=cls.checkbox,
                component="NURSING",
                title=(
                    f"Active {cls.label} coded as primary but the Section I checkbox "
                    f"({cls.checkbox}) is unchecked"
                ),
                current_value=(
                    f"{cls.checkbox} ({cls.label}) is unchecked, though {primary} is coded as the "
                    f"primary diagnosis (I0020B)"
                ),
                suggested_value=(
                    f"Check {cls.checkbox} — {cls.label} is the primary stay reason and is "
                    f"documented active in the look-back"
                ),
                rationale=(
                    f"{cls.label} is coded as the primary reason for the Part A stay (I0020B={primary}) "
                    f"and is corroborated by a coded diagnosis + active MAR/order in the active-diagnosis "
                    f"look-back, but its Section I active-diagnosis checkbox ({cls.checkbox}) is left "
                    f"unchecked. The checkbox is the nursing case-mix trigger ({cls.nursing_category}); "
                    f"only the checkbox changes. Dollar impact from the grouper."
                ),
                proposed_item_changes={cls.checkbox: "1"},
                state=CandidateItemState.SUPPORTED,
                structured_provenance=[dx.source_table, corr.source_table],
                candidate_dx_codes=(primary,),
            )
        ]
