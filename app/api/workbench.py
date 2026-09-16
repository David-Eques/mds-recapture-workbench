"""Public uploaded-case workbench: real files in, OCR/rules/grouper results out."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..grouper.jar_bridge import (
    GrouperError,
    GrouperIntegrityError,
    GrouperTimeout,
    GrouperUnavailable,
)
from ..intake.case import (
    IntakeError,
    IntakeLimitError,
    UploadedArtifact,
    analyze_uploaded_case,
    build_uploaded_case,
)
from ..intake.ocr import (
    MAX_DOCUMENT_BYTES,
    DocumentProcessingTimeout,
    DocumentToolUnavailableError,
    OcrError,
    OcrLimitError,
    UnsupportedDocumentError,
)

router = APIRouter()
_STATIC = Path(__file__).parent / "static"
_MAX_DOCUMENTS = 10
_MAX_ASSESSMENT_BYTES = 5 * 1024 * 1024
_MAX_SIGNALS_BYTES = 10 * 1024 * 1024
_MAX_TOTAL_BYTES = 50 * 1024 * 1024
_ANALYSIS_TIMEOUT_SECONDS = int(os.environ.get("ANALYSIS_TIMEOUT_SECONDS", "180"))
_ANALYSIS_LIMITER = asyncio.Semaphore(int(os.environ.get("ANALYSIS_CONCURRENCY", "2")))
_LOG = logging.getLogger("mds_workbench.api")


class DocumentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    document_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    document_type: str = Field(min_length=1, max_length=64)


class PublicApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def _error_response(error: PublicApiError, request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=error.status,
        content={"error": {"code": error.code, "message": error.message, "request_id": request_id}},
    )


async def _bounded_read(upload: UploadFile, limit: int, label: str) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise PublicApiError(413, "upload_too_large", f"{label} exceeds the upload limit")
    if not data:
        raise PublicApiError(400, "empty_upload", f"{label} is empty")
    return data


def _safe_filename(filename: str | None) -> str:
    value = filename or ""
    if not value or "\x00" in value or "/" in value or "\\" in value:
        raise PublicApiError(400, "invalid_filename", "uploaded filenames must be plain filenames")
    return value


def _metadata(raw: str | None, uploads: list[UploadFile]) -> dict[str, DocumentMetadata]:
    if not uploads:
        if raw not in (None, "", "[]"):
            raise PublicApiError(
                400, "inconsistent_document_metadata", "document metadata was supplied without documents"
            )
        return {}
    if raw is None:
        raise PublicApiError(
            400,
            "missing_document_metadata",
            "each clinical document requires one metadata record",
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PublicApiError(
            422, "invalid_document_metadata", "document metadata must be valid JSON"
        ) from exc
    if not isinstance(payload, list):
        raise PublicApiError(422, "invalid_document_metadata", "document metadata must be a JSON array")
    try:
        records = [DocumentMetadata.model_validate(item) for item in payload]
    except ValidationError as exc:
        raise PublicApiError(
            422, "invalid_document_metadata", "document metadata contains an invalid record"
        ) from exc
    upload_names = [_safe_filename(upload.filename) for upload in uploads]
    metadata_names = [record.filename for record in records]
    if len(upload_names) != len(set(upload_names)) or len(metadata_names) != len(set(metadata_names)):
        raise PublicApiError(400, "duplicate_filename", "clinical document filenames must be unique")
    if set(upload_names) != set(metadata_names) or len(records) != len(uploads):
        raise PublicApiError(
            400,
            "inconsistent_document_metadata",
            "document metadata must match the uploaded document filenames exactly",
        )
    for record in records:
        _safe_filename(record.filename)
        try:
            date.fromisoformat(record.document_date)
        except ValueError as exc:
            raise PublicApiError(
                422, "invalid_document_metadata", "document dates must be valid ISO dates"
            ) from exc
    return {record.filename: record for record in records}


@router.get("/", response_class=HTMLResponse)
def workbench_home() -> HTMLResponse:
    index = _STATIC / "workbench.html"
    html = index.read_text(encoding="utf-8")
    css = (_STATIC / "workbench.css").read_text(encoding="utf-8")
    javascript = (_STATIC / "workbench.js").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="/static/workbench.css">', f"<style>{css}</style>")
    html = html.replace(
        '<script src="/static/workbench.js" defer></script>', f"<script>{javascript}</script>"
    )
    return HTMLResponse(html)


@router.post("/api/analyze", response_model=None)
async def analyze(
    request: Request,
    assessment: Annotated[UploadFile, File(description="MDS XML or canonical JSON")],
    documents: Annotated[
        list[UploadFile] | None, File(description="Clinical PDF/image/text documents")
    ] = None,
    signals: Annotated[
        UploadFile | None,
        File(description="Optional canonical structured-signal JSON or CSV"),
    ] = None,
    case_id: Annotated[str | None, Form()] = None,
    document_metadata: Annotated[str | None, Form()] = None,
) -> dict | JSONResponse:
    request_id = request.headers.get("x-request-id") or uuid4().hex
    documents = documents or []
    try:
        if not documents and signals is None:
            raise PublicApiError(
                400,
                "missing_evidence",
                "upload at least one clinical document or a structured-signal file",
            )
        if len(documents) > _MAX_DOCUMENTS:
            raise PublicApiError(413, "too_many_documents", f"upload no more than {_MAX_DOCUMENTS} documents")
        metadata = _metadata(document_metadata, documents)
        assessment_name = _safe_filename(assessment.filename)
        assessment_data = await _bounded_read(assessment, _MAX_ASSESSMENT_BYTES, "assessment")
        artifacts: list[UploadedArtifact] = []
        total_bytes = len(assessment_data)
        for upload in documents:
            filename = _safe_filename(upload.filename)
            remaining = _MAX_TOTAL_BYTES - total_bytes
            if remaining <= 0:
                raise PublicApiError(
                    413, "request_too_large", "multipart upload exceeds the 50 MB request limit"
                )
            data = await _bounded_read(upload, min(MAX_DOCUMENT_BYTES, remaining), "clinical document")
            total_bytes += len(data)
            meta = metadata[filename]
            artifacts.append(
                UploadedArtifact(
                    filename=filename,
                    content_type=upload.content_type,
                    data=data,
                    document_date=date.fromisoformat(meta.document_date),
                    document_type=meta.document_type,
                )
            )
        signals_data = None
        if signals is not None:
            remaining = _MAX_TOTAL_BYTES - total_bytes
            if remaining <= 0:
                raise PublicApiError(
                    413, "request_too_large", "multipart upload exceeds the 50 MB request limit"
                )
            signals_data = await _bounded_read(
                signals, min(_MAX_SIGNALS_BYTES, remaining), "structured signals"
            )
        total_bytes += len(signals_data or b"")
        if total_bytes > _MAX_TOTAL_BYTES:
            raise PublicApiError(413, "request_too_large", "multipart upload exceeds the 50 MB request limit")

        def process() -> dict:
            from ..grouper.select import build_grouper

            case = build_uploaded_case(
                assessment_data=assessment_data,
                assessment_filename=assessment_name,
                document_artifacts=artifacts,
                signals_data=signals_data,
                signals_filename=_safe_filename(signals.filename) if signals is not None else None,
                case_id=case_id,
            )
            return analyze_uploaded_case(case, build_grouper())

        async with _ANALYSIS_LIMITER:
            async with asyncio.timeout(_ANALYSIS_TIMEOUT_SECONDS):
                result = await run_in_threadpool(process)
        result["request_id"] = request_id
        return result
    except PublicApiError as exc:
        return _error_response(exc, request_id)
    except (IntakeLimitError, OcrLimitError) as exc:
        return _error_response(PublicApiError(413, "processing_limit_exceeded", str(exc)), request_id)
    except UnsupportedDocumentError as exc:
        return _error_response(PublicApiError(415, "unsupported_document_type", str(exc)), request_id)
    except (DocumentToolUnavailableError, GrouperUnavailable, GrouperIntegrityError, FileNotFoundError):
        return _error_response(
            PublicApiError(
                503, "processing_dependency_unavailable", "a required processing dependency is unavailable"
            ),
            request_id,
        )
    except (DocumentProcessingTimeout, GrouperTimeout, TimeoutError):
        return _error_response(PublicApiError(504, "processing_timeout", "analysis timed out"), request_id)
    except IntakeError as exc:
        return _error_response(PublicApiError(422, "invalid_input", str(exc)), request_id)
    except OcrError as exc:
        return _error_response(PublicApiError(422, "invalid_document", str(exc)), request_id)
    except GrouperError:
        return _error_response(
            PublicApiError(503, "grouper_failed", "the CMS grouper could not process the assessment"),
            request_id,
        )
    except Exception:  # noqa: BLE001
        _LOG.error("analysis_failed request_id=%s category=unexpected", request_id)
        return _error_response(PublicApiError(500, "internal_error", "analysis failed safely"), request_id)
