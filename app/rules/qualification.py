"""Conservative qualification for deterministic text-rule candidates."""

from __future__ import annotations

import re

from ..models.decisions import DecisionReason
from ..models.facts import EvidenceCitation
from .base import EngineContext, FindingCandidate

_STATUS_PATTERNS = (
    re.compile(r"\bhistory of\b", re.I),
    re.compile(r"\bhistorical\b", re.I),
    re.compile(r"\bresolved\b", re.I),
    re.compile(r"\bremote\b", re.I),
    re.compile(r"\bprior\b", re.I),
    re.compile(r"\bcopied[- ]forward\b", re.I),
    re.compile(r"\bdiscontinued\b", re.I),
    re.compile(r"\bno longer\b", re.I),
    re.compile(r"\bcurrently not receiving\b", re.I),
    re.compile(r"\brule[sd]? out\b", re.I),
    re.compile(r"\bpossible\b", re.I),
    re.compile(r"\buncertain\b", re.I),
)

_DIET_ORDER_CUES = (
    "diet order",
    "ordered diet",
    "current diet",
    "continues on",
    "continue mechanical",
    "receiving mechanical",
    "maintain mechanical",
)


def qualify_candidate(ctx: EngineContext, candidate: FindingCandidate) -> tuple[bool, DecisionReason | None]:
    """Return whether a favorable text candidate has enough current, local context."""
    if candidate.direction != "under_capture":
        return True, None
    quotes = [evidence.quote for evidence in candidate.evidence]
    if not quotes:
        return False, "insufficient_support"
    for quote in quotes:
        if any(pattern.search(quote) for pattern in _STATUS_PATTERNS):
            if re.search(r"\b(discontinued|no longer|currently not receiving)\b", quote, re.I):
                return False, "discontinued_evidence"
            return False, "historical_evidence"

    if candidate.mds_item == "K0520C3":
        texture_phrases = ctx.knowledge.diet_texture_phrases
        supported = any(
            any(texture.lower() in quote.lower() for texture in texture_phrases)
            and any(cue in quote.lower() for cue in _DIET_ORDER_CUES)
            for quote in quotes
        )
        if not supported:
            return False, "insufficient_support"

    if candidate.mds_item == "I8000":
        if len({evidence.document_id for evidence in candidate.evidence}) < 1 or len(quotes) < 2:
            return False, "insufficient_support"

    if candidate.mds_item.startswith("GG0170"):
        task_phrases = tuple(
            phrase for _item, phrases in ctx.knowledge.gg_overcapture_items for phrase in phrases
        ) + tuple(ctx.knowledge.gg_ambulation_phrases)
        supported = any(
            any(task.lower() in quote.lower() for task in task_phrases)
            and any(assist.lower() in quote.lower() for assist, _code in ctx.knowledge.assist_level_to_gg)
            for quote in quotes
        )
        if not supported:
            return False, "insufficient_support"
    return True, None


def citation_confidence_qualifies(evidence: list[EvidenceCitation]) -> bool:
    """OCR evidence must be grounded in lines whose aggregate confidence meets the floor."""
    for citation in evidence:
        if citation.verification_basis != "ocr_text_exact_substring":
            continue
        if citation.confidence is None or citation.confidence < 0.85:
            return False
    return True
