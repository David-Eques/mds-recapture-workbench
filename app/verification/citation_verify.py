"""Verify exact evidence quotes against the document and OCR line that produced them."""

from __future__ import annotations

from ..models.case import AnalysisCase, ClinicalDocument
from ..models.facts import EvidenceCitation


def verify_citation(case: AnalysisCase, raw_evidence: dict) -> EvidenceCitation:
    """Verify one evidence quote against its source document. Offsets are recomputed (not trusted)."""
    quote = raw_evidence.get("quote", "")
    document_id = raw_evidence.get("document_id", "")
    note = raw_evidence.get("_note") or raw_evidence.get("note")

    doc = case.document(document_id)
    if doc is not None:
        start = doc.text.find(quote)
        if start != -1:
            bbox, confidence = _ocr_grounding(doc, start, start + len(quote))
            return EvidenceCitation(
                document_id=document_id,
                quote=quote,
                text_start=start,
                text_end=start + len(quote),
                verified=True,
                verification_basis=_verification_basis(doc),
                document_name=doc.document_name,
                document_type=doc.document_type,
                document_date=doc.document_date,
                note=note,
                page=doc.page,
                bbox=bbox,
                confidence=confidence,
            )

    return EvidenceCitation(
        document_id=document_id,
        quote=quote,
        text_start=None,
        text_end=None,
        verified=False,
        document_name=doc.document_name if doc else None,
        document_type=doc.document_type if doc else None,
        document_date=doc.document_date if doc else None,
        note=note,
    )


def _ocr_grounding(
    doc: ClinicalDocument, quote_start: int, quote_end: int
) -> tuple[list[float] | None, float | None]:
    """Union OCR-line boxes overlapping a verified quote and average their confidences."""
    spans = doc.text_spans
    overlaps = [span for span in spans if span.text_end > quote_start and span.text_start < quote_end]
    boxes = [span.bbox for span in overlaps if span.bbox and len(span.bbox) == 4]
    if boxes:
        left = min(box[0] for box in boxes)
        top = min(box[1] for box in boxes)
        right = max(box[0] + box[2] for box in boxes)
        bottom = max(box[1] + box[3] for box in boxes)
        bbox: list[float] | None = [left, top, right - left, bottom - top]
    else:
        bbox = None
    confidences = [span.confidence for span in overlaps if span.confidence is not None]
    confidence = min(confidences) if confidences else doc.ocr_confidence
    return bbox, confidence


def _verification_basis(doc: ClinicalDocument) -> str:
    explicit = getattr(doc, "verification_basis", None)
    if explicit:
        return str(explicit)
    return (
        "ocr_text_exact_substring"
        if getattr(doc, "page", None) is not None
        else "native_text_exact_substring"
    )


def verify_evidence(case: AnalysisCase, raw_finding: dict) -> list[EvidenceCitation]:
    return [verify_citation(case, evidence) for evidence in raw_finding.get("evidence", [])]


def has_verified_citation(evidence: list[EvidenceCitation]) -> bool:
    return any(e.verified for e in evidence)
