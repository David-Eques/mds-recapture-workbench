"""Real uploaded-case intake: deterministic MDS parsing, structured signals, and local OCR."""

from .case import UploadedCase, analyze_uploaded_case, parse_assessment, parse_structured_signals
from .ocr import OcrDocument, OcrError, TesseractOcr

__all__ = [
    "OcrDocument",
    "OcrError",
    "TesseractOcr",
    "UploadedCase",
    "analyze_uploaded_case",
    "parse_assessment",
    "parse_structured_signals",
]
