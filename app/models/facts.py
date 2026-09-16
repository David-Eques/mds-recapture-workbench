"""Exact source citations produced by deterministic evidence rules."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class EvidenceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    quote: str
    text_start: int | None = None
    text_end: int | None = None
    verified: bool = False
    verification_basis: (
        Literal[
            "ocr_text_exact_substring",
            "native_text_exact_substring",
            "structured_record_reference",
        ]
        | None
    ) = None
    document_name: str | None = None
    document_type: str | None = None
    document_date: str | None = None
    note: str | None = None
    page: int | None = None
    bbox: list[float] | None = None
    confidence: float | None = None
