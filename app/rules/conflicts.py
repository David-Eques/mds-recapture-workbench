"""Conflicting-evidence detection (resident_007) — WIRED in v1 (decision #1).

When in-window documentation both ASSERTS and CONTRADICTS the same clinical fact, the engine
surfaces no finding and records a ``conflicting_evidence`` decision for human adjudication. v1
detects this by co-occurrence of three signals; intentionally strict so a consistent picture never
false-fires (003: mech-soft diet, no contradicting order; 004: confirmed coughing, no clearance).

v1 limitation (same as v0): co-occurrence does not date-window. ARD-relative supersession is the
AssessmentPeriodRule's job (step 2); this remains the interim guard.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import EngineContext, RawEvidence, document_in_window, find_in_docs, find_positive_unnegated


@dataclass
class ConflictResult:
    target: str
    suppress_prefixes: tuple[str, ...]
    evidence: list[RawEvidence] = field(default_factory=list)


def detect_swallow_diet_conflict(ctx: EngineContext) -> ConflictResult | None:
    """Suppress a favorable diet/swallowing change when current evidence contradicts it."""
    k = ctx.knowledge
    docs = [doc for doc in ctx.case.documents if document_in_window(ctx.case, doc, days=7)]

    indication: RawEvidence | None = None
    for phrase in k.diet_texture_phrases:
        indication = find_in_docs(docs, phrase)
        if indication:
            break
    diet_contra: RawEvidence | None = None
    for phrase in k.diet_contradiction_phrases:
        diet_contra = find_in_docs(docs, phrase)
        if diet_contra:
            break
    if indication is not None and diet_contra is not None:
        return ConflictResult(
            target="K0520C",
            suppress_prefixes=("K0520",),
            evidence=[indication, diet_contra],
        )

    swallowing_cues = tuple(sign for sign, _ in k.k0100_signs) + tuple(k.swallowing_positive)
    swallowing = find_positive_unnegated(docs, swallowing_cues)
    clearance: RawEvidence | None = None
    for phrase in k.swallow_clearance_phrases:
        clearance = find_in_docs(docs, phrase)
        if clearance:
            break
    if swallowing is not None and clearance is not None:
        return ConflictResult(
            target="K0100",
            suppress_prefixes=("K0100",),
            evidence=[swallowing, clearance],
        )
    return None
