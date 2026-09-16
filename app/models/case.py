"""Runtime models for one uploaded analysis request.

These shapes contain uploaded assessment data and source-grounded evidence only. They have no
expected-result or answer-oracle fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from .mds import MDSRecord, StructuredSignal

if TYPE_CHECKING:
    from ..intake.ocr import OcrDocument


VerificationBasis = Literal[
    "ocr_text_exact_substring",
    "native_text_exact_substring",
    "structured_record_reference",
]


@dataclass(frozen=True)
class TextSpan:
    """A range in normalized page text with source-image grounding."""

    text_start: int
    text_end: int
    bbox: list[float] | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class ClinicalDocument:
    """One page of an uploaded clinical document."""

    document_id: str
    document_name: str
    document_type: str
    document_date: str
    text: str
    verification_basis: VerificationBasis
    page: int | None = None
    ocr_confidence: float | None = None
    text_spans: list[TextSpan] = field(default_factory=list)


@dataclass(frozen=True)
class AnalysisCase:
    """As-coded MDS plus uploaded structured and document evidence."""

    assessment: MDSRecord
    documents: list[ClinicalDocument]
    structured_signals: list[StructuredSignal]
    ocr_documents: list[OcrDocument]

    @property
    def resident_id(self) -> str:
        return self.assessment.stay_id

    @property
    def assessment_id(self) -> str:
        return self.assessment.stay_id

    @property
    def assessment_type(self) -> str:
        return self.assessment.assessment_type

    @property
    def ard(self) -> str:
        return self.assessment.ard.isoformat()

    @property
    def part_a_start(self) -> str | None:
        return self.assessment.a2400b.isoformat() if self.assessment.a2400b else None

    @property
    def mds_items(self) -> dict[str, str]:
        return self.assessment.items

    def document(self, document_id: str) -> ClinicalDocument | None:
        return next((doc for doc in self.documents if doc.document_id == document_id), None)
