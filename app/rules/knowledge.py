"""Clinical reference data + detection lexicons for the deterministic rulepack.

The NTA comorbidity table is loaded from the FY2026 data pack
(``app/rules/data/nta_comorbidities_fy2026.csv``: condition -> MDS item -> points). Detection
lexicons are GENERAL clinical vocabulary (condition names derived from the CSV; standard treatment /
diet / ambulation terms) — never the unique sentences of any specific chart — so the engine
generalizes rather than encoding answers. The anti-overfitting guard test enforces this.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

NTA_CSV = Path(__file__).parent / "data" / "nta_comorbidities_fy2026.csv"

_LABEL_PREFIXES = (
    "active diagnoses:",
    "special treatments/programs:",
    "other foot skin problems:",
    "bladder and bowel appliances:",
    "nutritional approaches while a patient:",
    "parenteral/iv feeding:",
    "disorders of immunity",
)

_ICD_BY_CONDITION = {
    "proliferative diabetic retinopathy and vitreous hemorrhage": ("E11.351",),
    "chronic pancreatitis": ("K86.1",),
}
_TREATMENT_BY_CONDITION = {
    "proliferative diabetic retinopathy and vitreous hemorrhage": (
        "anti-vegf",
        "intravitreal injection",
        "panretinal photocoagulation",
    ),
    "chronic pancreatitis": (
        "pancrelipase",
        "creon",
        "pancreatic enzyme replacement",
        "enzyme replacement",
    ),
    "active diagnoses: diabetes mellitus (dm)": (
        "metformin",
        "insulin",
        "hba1c",
        "fingerstick",
        "glycemic",
    ),
}
_NEGATION_BY_CONDITION = {
    "wound infection": (
        "no signs or symptoms of infection",
        "skin intact",
        "no open wounds",
        "no surgical wounds",
        "no pressure injuries",
        "no open areas",
    ),
}

ACTIVE_TREATMENT_MARKERS = (
    "continue",
    "scheduled",
    "administered",
    "ongoing",
    "managed with",
    "active",
    "started",
    "initiated",
    "daily",
    "with each meal",
    "replacement therapy",
)
HISTORY_ONLY_MARKERS = ("history of", "h/o", "pmh", "past medical history", "resolved", "remote")

DIET_TEXTURE_PHRASES = (
    "mechanical soft",
    "texture modified",
    "texture-modified",
    "mechanically altered",
    "pureed",
    "minced and moist",
)
SWALLOWING_POSITIVE = ("dysphagia", "aspiration", "coughing with", "choking", "wet vocal quality")
SWALLOWING_NEGATION = (
    "no signs or symptoms of dysphagia",
    "no clinical signs or symptoms of dysphagia",
    "not for swallowing",
    "no coughing, choking",
    "no signs of aspiration",
    "no signs/symptoms of aspiration",
    "no swallowing difficulty",
    "no swallowing complaints",
    "swallowing screen negative",
)
K0100_SIGNS = (
    ("loss of liquids", "K0100A"),
    ("loss of solids", "K0100A"),
    ("pocketing", "K0100B"),
    ("holding food", "K0100B"),
    ("coughing during meals", "K0100C"),
    ("coughing while drinking", "K0100C"),
    ("coughing with", "K0100C"),
    ("choking", "K0100C"),
    ("throat clearing", "K0100C"),
    ("difficulty swallowing", "K0100D"),
    ("difficulty with swallowing", "K0100D"),
    ("painful swallowing", "K0100D"),
    ("pain with swallowing", "K0100D"),
)
DIET_CONTRADICTION_PHRASES = ("regular diet",)
SWALLOW_CLEARANCE_PHRASES = (
    "no coughing or choking",
    "no swallowing precautions",
    "swallowing screen negative",
)

GG_AMBULATION_PHRASES = ("ambulated", "ambulation", "walked", "walk 10 feet", "able to walk")
GG_NOT_ATTEMPTED_CODES = {"88", "07", "09", "10"}
ASSIST_LEVEL_TO_GG = (
    ("partial/moderate assist", "03"),
    ("partial/moderate assistance", "03"),
    ("moderate assist", "03"),
    ("substantial/maximal assist", "02"),
    ("maximal assist", "02"),
    ("supervision", "04"),
    ("setup", "05"),
    ("independent", "06"),
)
GG_OVERCAPTURE_ITEMS = (
    ("GG0170D1", ("sit-to-stand", "sit to stand")),
    ("GG0170E1", ("bed-to-chair", "bed to chair", "chair transfer")),
    ("GG0170J1", ("ambulated 50", "walk 50 feet", "walked 50")),
)

# Suggestion-side backstop for passes_rtp_gate ("never PROPOSE an RTP code as I0020B"); the
# input-RTP decision (does the as-coded primary block recapture?) is GROUPER-derived in engine.rtp_block.
RTP_CODES = frozenset({"Z00.00", "Z51.89", "R69", "B99.9"})


@dataclass(frozen=True)
class NtaComorbidity:
    condition: str
    mds_items: tuple[str, ...]
    points: int
    name_phrases: tuple[str, ...]
    icd_codes: tuple[str, ...]
    treatment_phrases: tuple[str, ...]
    negation_phrases: tuple[str, ...]


@dataclass
class Knowledge:
    nta: list[NtaComorbidity]
    rtp_codes: frozenset[str] = RTP_CODES
    history_only_markers: tuple[str, ...] = HISTORY_ONLY_MARKERS
    active_treatment_markers: tuple[str, ...] = ACTIVE_TREATMENT_MARKERS
    diet_texture_phrases: tuple[str, ...] = DIET_TEXTURE_PHRASES
    swallowing_positive: tuple[str, ...] = SWALLOWING_POSITIVE
    swallowing_negation: tuple[str, ...] = SWALLOWING_NEGATION
    k0100_signs: tuple[tuple[str, str], ...] = K0100_SIGNS
    diet_contradiction_phrases: tuple[str, ...] = DIET_CONTRADICTION_PHRASES
    swallow_clearance_phrases: tuple[str, ...] = SWALLOW_CLEARANCE_PHRASES
    gg_ambulation_phrases: tuple[str, ...] = GG_AMBULATION_PHRASES
    gg_not_attempted_codes: frozenset[str] = field(default_factory=lambda: frozenset(GG_NOT_ATTEMPTED_CODES))
    assist_level_to_gg: tuple[tuple[str, str], ...] = ASSIST_LEVEL_TO_GG
    gg_overcapture_items: tuple[tuple[str, tuple[str, ...]], ...] = GG_OVERCAPTURE_ITEMS

    def coded_in(self, items: dict[str, str], c: NtaComorbidity) -> bool:
        for item in c.mds_items:
            if str(items.get(item, "")).strip() not in ("", "0"):
                return True
        coded_codes = {str(items.get(k, "")).strip() for k in ("I8000A", "I8000B", "I8000C", "I0020B")}
        return any(code in coded_codes for code in c.icd_codes)


def _strip_prefix(label: str) -> str:
    low = label.lower()
    for p in _LABEL_PREFIXES:
        if low.startswith(p):
            return label[len(p) :].strip()
    return label


def condition_to_phrases(condition: str) -> tuple[str, ...]:
    base = _strip_prefix(condition).lower()
    base = re.sub(r"\([^)]*\)", " ", base)
    parts = re.split(r"\s+and\s+|/|,|;", base)
    phrases = []
    for p in parts:
        p = p.strip(" .")
        if len(p) >= 5:
            phrases.append(p)
    return tuple(dict.fromkeys(phrases))


@lru_cache(maxsize=1)
def load_knowledge() -> Knowledge:
    comorbidities: list[NtaComorbidity] = []
    with NTA_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            condition = row["condition"].strip()
            key = condition.lower()
            items = tuple(i.strip() for i in row["mds_item"].split(",") if i.strip())
            comorbidities.append(
                NtaComorbidity(
                    condition=condition,
                    mds_items=items,
                    points=int(row["points"]),
                    name_phrases=condition_to_phrases(condition),
                    icd_codes=_ICD_BY_CONDITION.get(key, ()),
                    treatment_phrases=_TREATMENT_BY_CONDITION.get(key, ()),
                    negation_phrases=_NEGATION_BY_CONDITION.get(key, ()),
                )
            )
    return Knowledge(nta=comorbidities)


def all_lexicon_phrases(k: Knowledge) -> list[str]:
    phrases: list[str] = []
    for c in k.nta:
        phrases += list(c.name_phrases) + list(c.treatment_phrases) + list(c.negation_phrases)
    phrases += list(k.history_only_markers) + list(k.active_treatment_markers)
    phrases += list(k.diet_texture_phrases) + list(k.swallowing_positive) + list(k.swallowing_negation)
    phrases += [sign for sign, _ in k.k0100_signs]
    phrases += list(k.diet_contradiction_phrases) + list(k.swallow_clearance_phrases)
    phrases += list(k.gg_ambulation_phrases) + [a for a, _ in k.assist_level_to_gg]
    phrases += [t for _, tasks in k.gg_overcapture_items for t in tasks]
    return phrases
