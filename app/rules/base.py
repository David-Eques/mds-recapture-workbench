"""Rule protocol, finding candidates, safety gates, and the verbatim quote extractor.

A ``Rule.detect`` is pure and deterministic: it reads the as-coded MDS items and the parsed chart
documents and returns zero or more ``FindingCandidate``s. It NEVER reads the oracle and NEVER
computes a dollar — the engine attaches dollars from the grouper (invariant #1). The contractual
dollar labels live in ``app.models.findings`` (the models layer), not here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from ..models.case import AnalysisCase, ClinicalDocument

if TYPE_CHECKING:
    from ..audit.decision_log import DecisionLog
    from .knowledge import Knowledge

Component = Literal["NTA", "SLP", "PT_OT", "NURSING"]
Direction = Literal["under_capture", "over_capture", "review"]


@dataclass(frozen=True)
class RawEvidence:
    document_id: str
    quote: str  # a verbatim substring of the source document (guaranteed by extract_*)


@dataclass
class FindingCandidate:
    rule_id: str
    direction: Direction
    mds_item: str
    current_value: str | None
    suggested_value: str | None
    title: str
    rationale: str
    component: Component
    evidence: list[RawEvidence] = field(default_factory=list)
    proposed_item_changes: dict[str, str] = field(default_factory=dict)
    candidate_dx_codes: tuple[str, ...] = ()
    is_primary_dx_change: bool = False
    active_treatment_supported: bool = False  # set by under-capture dx detection


@dataclass
class EngineContext:
    case: AnalysisCase
    knowledge: Knowledge
    decisions: DecisionLog | None = None  # optional Decision Audit Log sink


@runtime_checkable
class Rule(Protocol):
    rule_id: str

    def detect(self, ctx: EngineContext) -> list[FindingCandidate]: ...


# --------------------------------------------------------------------------- #
# Verbatim quote extraction                                                     #
# --------------------------------------------------------------------------- #
_BOUNDARY = re.compile(r"[.!?\n]")
_TRIM_LEAD = " \t\n*#>-|"
_TRIM_TRAIL = " \t\n*"


def extract_sentence_containing(doc: ClinicalDocument, phrase: str) -> RawEvidence | None:
    """Return a clean, VERBATIM substring of ``doc.text`` containing ``phrase`` (sentence/line
    bounded, narrowed past inline markdown bold) — so ``str.find`` locates it (invariant #3)."""
    text = doc.text
    idx = text.lower().find(phrase.lower())
    if idx == -1:
        return None
    plen = len(phrase)

    start = 0
    for m in _BOUNDARY.finditer(text, 0, idx):
        start = m.end()
    bb = text.rfind("**", start, idx)
    if bb != -1:
        start = bb + 2

    end = len(text)
    tail = _BOUNDARY.search(text, idx + plen)
    if tail:
        end = tail.end() if text[tail.start()] in ".!?" else tail.start()
    eb = text.find("**", idx + plen, end)
    if eb != -1:
        end = eb

    while start < end and text[start] in _TRIM_LEAD:
        start += 1
    while end > start and text[end - 1] in _TRIM_TRAIL:
        end -= 1

    quote = text[start:end]
    return RawEvidence(document_id=doc.document_id, quote=quote) if quote.strip() else None


def find_in_docs(docs: list[ClinicalDocument], phrase: str) -> RawEvidence | None:
    for doc in docs:
        ev = extract_sentence_containing(doc, phrase)
        if ev is not None:
            return ev
    return None


def doc_contains(doc: ClinicalDocument, phrase: str) -> bool:
    return phrase.lower() in doc.text.lower()


_NEGATION_TOKENS = (
    " no ",
    " not ",
    "without",
    "negative",
    "denies",
    "no signs",
    "no clinical",
    "no complaints",
)


def is_target_negated(sentence: str, target: str) -> bool:
    """Detect negation scoped to the target, not unrelated uses of the word ``no``."""
    low = sentence.lower()
    index = low.find(target.lower())
    if index == -1:
        return False
    prefix = low[max(0, index - 48) : index]
    return bool(
        re.search(r"(?:\bno\b|\bnot\b|\bwithout\b|\bdenies?\b|\bnegative for\b)\s+(?:\w+\s+){0,4}$", prefix)
    )


def find_positive_unnegated(docs: list[ClinicalDocument], cues: tuple[str, ...]) -> RawEvidence | None:
    """First verbatim sentence containing a cue that is NOT negated (the interim semantic guard
    under invariant #3 — the negation/temporality-aware SemanticSupportGate is the deferred upgrade)."""
    for doc in docs:
        for cue in cues:
            ev = extract_sentence_containing(doc, cue)
            if ev and not is_target_negated(ev.quote, cue):
                return ev
    return None


def any_doc_contains(docs: list[ClinicalDocument], phrases: tuple[str, ...]) -> bool:
    blob = "\n".join(d.text for d in docs).lower()
    return any(p.lower() in blob for p in phrases)


def document_in_window(
    case: AnalysisCase,
    doc: ClinicalDocument,
    *,
    days: int,
    anchor: Literal["ard", "part_a_start"] = "ard",
) -> bool:
    """Validate uploaded-document dates against an inclusive evidence window.

    Uploaded cases fail closed on a missing or unparseable metadata date.
    """
    if doc.document_date is None:
        return False
    anchor_raw = case.ard if anchor == "ard" else case.part_a_start
    if anchor_raw is None:
        return False
    try:
        evidence_date = date.fromisoformat(doc.document_date)
        anchor_date = date.fromisoformat(anchor_raw)
    except ValueError:
        return False
    if anchor == "part_a_start":
        return anchor_date <= evidence_date <= anchor_date + timedelta(days=days - 1)
    return anchor_date - timedelta(days=days - 1) <= evidence_date <= anchor_date


def evidence_in_window(
    case: AnalysisCase,
    evidence: RawEvidence,
    *,
    days: int,
    anchor: Literal["ard", "part_a_start"] = "ard",
) -> bool:
    doc = case.document(evidence.document_id)
    return doc is not None and document_in_window(case, doc, days=days, anchor=anchor)


def active_diagnosis_evidence_in_window(
    case: AnalysisCase, physician_evidence: RawEvidence, active_evidence: RawEvidence
) -> bool:
    """Section I two-step: physician documentation in 60 days and active status in 7 days."""
    return evidence_in_window(case, physician_evidence, days=60) and evidence_in_window(
        case, active_evidence, days=7
    )


# --------------------------------------------------------------------------- #
# Safety gates                                                                  #
# --------------------------------------------------------------------------- #
def passes_rtp_gate(c: FindingCandidate, rtp_codes: frozenset[str]) -> bool:
    """Never PROPOSE a return-to-provider code as the primary diagnosis (I0020B)."""
    if not c.is_primary_dx_change:
        return True
    new_primary = c.proposed_item_changes.get("I0020B")
    return new_primary is not None and new_primary not in rtp_codes


def passes_codeability_gate(c: FindingCandidate, ctx: EngineContext) -> bool:
    """Never propose a diagnosis whose only support is a history/resolved/problem-list mention with
    no active-treatment language. (The interim guard; superseded by AssessmentPeriodRule resolution
    for dated structured evidence in step 2.)"""
    if c.direction != "under_capture" or not c.candidate_dx_codes:
        return True
    return c.active_treatment_supported is True
