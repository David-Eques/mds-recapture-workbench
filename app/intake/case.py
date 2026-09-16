"""Uploaded assessment + real documents -> reviewable MDS/PDPM coding findings.

This is the non-fixture vertical slice. It accepts an arbitrary MDS XML/JSON assessment, optional
canonical structured signals, and OCR results from uploaded chart documents. Structured and
unstructured evidence run through the existing rule/grouper boundary; no expected outcome or fixture
oracle participates in the result.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from ..audit.decision_log import DecisionLog
from ..config.manifest import get_manifest
from ..grouper.mds_mapping import parse_assessment_xml, pps_reason_for
from ..models.case import AnalysisCase, ClinicalDocument, TextSpan
from ..models.findings import Finding, dollar_label_for
from ..models.mds import MDSRecord, StructuredSignal
from ..models.protocols import SingleSegmentPaymentPeriodEngine
from ..rules import engine as text_rule_engine
from ..rules.stream_a.engine import derive_stream_a
from .ocr import OcrDocument, TesseractOcr


class IntakeError(ValueError):
    """An uploaded case does not satisfy the public intake contract."""


class IntakeLimitError(IntakeError):
    """An uploaded case exceeds a documented resource limit."""


_MDS_ITEM_RE = re.compile(r"^[A-Z]{1,2}[0-9]{4}[A-Z0-9]*$")


@dataclass(frozen=True)
class UploadedArtifact:
    filename: str
    content_type: str | None
    data: bytes
    document_date: date | None = None
    document_type: str = "clinical_document"


UploadedCase = AnalysisCase


def _date_value(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise IntakeError(f"invalid date {raw!r}; expected YYYY-MM-DD")


def _unwrap_mds_json(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise IntakeError("assessment JSON must be an object")
    nested = payload.get("mds")
    return dict(nested) if isinstance(nested, dict) else dict(payload)


def parse_assessment(data: bytes, *, filename: str, case_id: str | None = None) -> MDSRecord:
    """Parse canonical MDS JSON or a conformant MDS 3.0 XML assessment."""
    suffix = Path(filename).suffix.lower()
    generated_id = case_id or f"upload-{uuid4().hex[:12]}"
    if suffix == ".xml" or data.lstrip().startswith(b"<"):
        try:
            parsed = parse_assessment_xml(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise IntakeError("assessment XML could not be parsed") from exc
        if parsed.ard_iso is None:
            raise IntakeError("assessment XML is missing a valid A2300 assessment reference date")
        items = dict(parsed.items)
        a2400b = _date_value(items.pop("A2400B", None))
        a2400c = _date_value(items.pop("A2400C", None))
        record = MDSRecord(
            stay_id=generated_id,
            assessment_type=parsed.a0310b or "01",
            ard=date.fromisoformat(parsed.ard_iso),
            items=items,
            a2400b=a2400b,
            a2400c=a2400c,
            source="uploaded",
        )
        _validate_supported_ard(record.ard)
        return record

    try:
        payload = _unwrap_mds_json(json.loads(data.decode("utf-8-sig")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntakeError("assessment must be valid MDS XML or JSON") from exc
    json_items = payload.get("items")
    if not isinstance(json_items, dict):
        raise IntakeError("assessment JSON requires an 'items' object")
    ard = _date_value(payload.get("ard") or payload.get("A2300"))
    if ard is None:
        raise IntakeError("assessment JSON requires an ARD")
    baseline_source, baseline_hipps, baseline_reconciled = _parse_baseline(payload)
    record = MDSRecord(
        stay_id=str(payload.get("stay_id") or payload.get("assessment_id") or generated_id),
        assessment_type=str(payload.get("assessment_type") or payload.get("a0310b") or "01"),
        ard=ard,
        items={
            str(key): str(value)
            for key, value in json_items.items()
            if value is not None and _MDS_ITEM_RE.match(str(key))
        },
        a2400b=_date_value(payload.get("a2400b") or payload.get("A2400B")),
        a2400c=_date_value(payload.get("a2400c") or payload.get("A2400C")),
        baseline_source=baseline_source,
        baseline_hipps=baseline_hipps,
        baseline_reconciled=baseline_reconciled,
        source="uploaded",
    )
    _validate_supported_ard(record.ard)
    return record


_SUPPORTED_ARD_START = date(2025, 10, 1)
_SUPPORTED_ARD_END = date(2026, 9, 30)
_HIPPS_RE = re.compile(r"^[A-Z]{4}[0-9]$")


def _validate_supported_ard(ard: date) -> None:
    if not _SUPPORTED_ARD_START <= ard <= _SUPPORTED_ARD_END:
        raise IntakeError(
            "assessment ARD is outside the supported FY2026 period (2025-10-01 through 2026-09-30)"
        )


def _parse_baseline(payload: dict[str, Any]) -> tuple[str, str | None, bool]:
    legacy = {"baseline_source", "baseline_hipps", "baseline_reconciled"} & payload.keys()
    if legacy:
        raise IntakeError("use the nested 'baseline' object; legacy baseline fields are not accepted")
    raw = payload.get("baseline")
    if raw is None:
        return "unknown", None, False
    if not isinstance(raw, dict):
        raise IntakeError("baseline must be an object")
    extra = set(raw) - {"source", "hipps", "reconciled"}
    if extra:
        raise IntakeError(f"baseline contains unsupported field {sorted(extra)[0]!r}")
    source = raw.get("source")
    reconciled = raw.get("reconciled")
    hipps_raw = raw.get("hipps")
    if type(reconciled) is not bool:
        raise IntakeError("baseline.reconciled must be a JSON boolean")
    if source == "not_billed":
        if hipps_raw not in (None, "") or reconciled:
            raise IntakeError("a not_billed baseline must omit HIPPS and set reconciled to false")
        return "not_billed", None, False
    if source not in {"claim_paid", "iqies_accepted"}:
        raise IntakeError("baseline.source must be claim_paid, iqies_accepted, or not_billed")
    hipps = str(hipps_raw or "").strip().upper()
    if not _HIPPS_RE.fullmatch(hipps):
        raise IntakeError("baseline.hipps must be a five-character PDPM HIPPS code")
    return str(source), hipps, reconciled


def parse_structured_signals(
    data: bytes | None, *, filename: str | None, stay_id: str
) -> list[StructuredSignal]:
    """Parse the canonical JSON/CSV signal contract consumed by Stream A."""
    if not data:
        return []
    suffix = Path(filename or "signals.json").suffix.lower()
    try:
        if suffix == ".csv":
            rows: Any = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
        else:
            payload = json.loads(data.decode("utf-8-sig"))
            rows = payload.get("signals") if isinstance(payload, dict) else payload
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntakeError("structured signals must be valid JSON or CSV") from exc
    if not isinstance(rows, list):
        raise IntakeError("structured signal input must contain a list of signals")
    out: list[StructuredSignal] = []
    for index, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            raise IntakeError(f"structured signal {index} must be an object")
        row = dict(raw)
        row["stay_id"] = str(row.get("stay_id") or stay_id)
        row["source_table"] = str(row.get("source_table") or f"uploaded_signals:{index}")
        if row.get("effective_date") == "":
            row["effective_date"] = None
        if row.get("code") == "":
            row["code"] = None
        try:
            out.append(StructuredSignal.model_validate(row))
        except ValidationError as exc:
            raise IntakeError(f"structured signal {index} is invalid: {exc.errors()[0]['msg']}") from exc
    wrong_stay = [signal for signal in out if signal.stay_id != stay_id]
    if wrong_stay:
        raise IntakeError("all structured signals must identify the uploaded assessment stay_id")
    return out


def chart_documents(
    ocr_documents: list[OcrDocument], artifacts: list[UploadedArtifact]
) -> list[ClinicalDocument]:
    """Turn OCR pages into citation-addressable chart documents."""
    documents: list[ClinicalDocument] = []
    for doc_index, (document, artifact) in enumerate(zip(ocr_documents, artifacts, strict=True), start=1):
        if artifact.document_date is None:
            raise IntakeError(f"document metadata is missing a date for {artifact.filename!r}")
        for page in document.pages:
            doc_type = artifact.document_type or "clinical_document"
            suffix = "PT" if doc_type == "therapy_eval" else doc_type.upper()[:8]
            document_id = f"D{doc_index:03d}-P{page.number:03d}-{suffix}"
            heading = f"## {document_id} - {document.filename}, page {page.number}\n"
            body = page.text.strip()
            full_text = f"{heading}{body}" if body else heading.rstrip()
            spans: list[TextSpan] = []
            cursor = len(heading)
            for line in page.lines:
                start = full_text.find(line.text, cursor)
                if start == -1:
                    continue
                end = start + len(line.text)
                spans.append(
                    TextSpan(
                        text_start=start,
                        text_end=end,
                        bbox=line.bbox,
                        confidence=line.confidence,
                    )
                )
                cursor = end
            documents.append(
                ClinicalDocument(
                    document_id=document_id,
                    document_name=document.filename,
                    document_type=doc_type,
                    document_date=artifact.document_date.isoformat(),
                    text=full_text,
                    verification_basis=(
                        "native_text_exact_substring"
                        if document.provider == "native_text"
                        else "ocr_text_exact_substring"
                    ),
                    page=page.number,
                    ocr_confidence=page.confidence,
                    text_spans=spans,
                )
            )
    return documents


def build_uploaded_case(
    *,
    assessment_data: bytes,
    assessment_filename: str,
    document_artifacts: list[UploadedArtifact],
    signals_data: bytes | None = None,
    signals_filename: str | None = None,
    case_id: str | None = None,
    ocr: TesseractOcr | None = None,
) -> AnalysisCase:
    assessment = parse_assessment(assessment_data, filename=assessment_filename, case_id=case_id)
    signals = parse_structured_signals(signals_data, filename=signals_filename, stay_id=assessment.stay_id)
    ocr = ocr or TesseractOcr()
    ocr_documents: list[OcrDocument] = []
    total_pages = 0
    for artifact in document_artifacts:
        if artifact.document_date is None:
            raise IntakeError(f"document metadata is missing a date for {artifact.filename!r}")
        document = ocr.extract(
            artifact.data,
            filename=artifact.filename,
            content_type=artifact.content_type,
            max_pages=50 - total_pages,
        )
        total_pages += len(document.pages)
        if total_pages > 50:
            raise IntakeLimitError("clinical documents exceed the 50-page request limit")
        ocr_documents.append(document)
    return AnalysisCase(
        assessment=assessment,
        documents=chart_documents(ocr_documents, document_artifacts),
        structured_signals=signals,
        ocr_documents=ocr_documents,
    )


def _merge_findings(structured: list[Finding], unstructured: list[Finding]) -> list[tuple[str, Finding]]:
    entries = [("structured", finding) for finding in structured] + [
        ("unstructured", finding) for finding in unstructured
    ]
    values_by_item: dict[str, set[str]] = {}
    for _stream, finding in entries:
        for item, value in finding.proposed_item_changes.items():
            values_by_item.setdefault(item, set()).add(value)
    conflicting_items = {item for item, values in values_by_item.items() if len(values) > 1}

    merged: list[tuple[str, Finding]] = []
    by_key: dict[tuple[Any, ...], int] = {}
    for stream, finding in entries:
        if set(finding.proposed_item_changes) & conflicting_items:
            continue
        proposal_key = tuple(sorted(finding.proposed_item_changes.items()))
        key: tuple[Any, ...] = (
            (finding.direction, proposal_key) if proposal_key else (finding.direction, finding.mds_item)
        )
        existing_index = by_key.get(key)
        if existing_index is None:
            by_key[key] = len(merged)
            merged.append((stream, finding))
            continue
        existing_stream, existing = merged[existing_index]
        evidence = list(existing.evidence)
        seen = {(item.document_id, item.quote) for item in evidence}
        evidence.extend(item for item in finding.evidence if (item.document_id, item.quote) not in seen)
        combined_stream = "structured+unstructured" if existing_stream != stream else existing_stream
        merged[existing_index] = (combined_stream, existing.model_copy(update={"evidence": evidence}))

    for item in sorted(conflicting_items):
        sources = [finding for _stream, finding in entries if item in finding.proposed_item_changes]
        first = sources[0]
        evidence = []
        conflict_seen: set[tuple[str, str]] = set()
        for source in sources:
            for citation in source.evidence:
                key = (citation.document_id, citation.quote)
                if key not in conflict_seen:
                    evidence.append(citation)
                    conflict_seen.add(key)
        merged.append(
            (
                "conflict",
                first.model_copy(
                    update={
                        "direction": "review",
                        "mds_item": item,
                        "title": f"Conflicting proposed values for {item}",
                        "rationale": (
                            "Structured and text evidence propose incompatible values. No favorable "
                            "counterfactual is constructed until a reviewer resolves the conflict."
                        ),
                        "suggested_value": "Review conflicting evidence",
                        "evidence": evidence,
                        "proposed_item_changes": {},
                        "as_supported_hipps": None,
                        "as_supported_scenario_a_rate": None,
                        "scenario_a_component_delta": None,
                        "scenario_a_component_delta_days_1_3": None,
                        "dollar_label": dollar_label_for("", "review"),
                    }
                ),
            )
        )
    return merged


def _baseline_status(mds: MDSRecord, as_coded_hipps: str) -> str:
    if mds.baseline_source == "unknown":
        return "missing"
    if mds.baseline_source == "not_billed":
        return "not_billed"
    if not mds.baseline_reconciled:
        return "unreconciled"
    return "matched" if mds.baseline_hipps == as_coded_hipps else "mismatch"


def _finding_payload(stream: str, finding: Finding, *, baseline_status: str) -> dict[str, Any]:
    if finding.direction != "under_capture":
        support_tier = "review_flag"
    elif stream.startswith("structured"):
        support_tier = "structured_supported"
    else:
        support_tier = "text_review_candidate"
    potential = finding.scenario_a_component_delta
    counted = potential if support_tier == "structured_supported" and baseline_status == "matched" else None
    as_supported = (
        {
            "hipps": finding.as_supported_hipps,
            "scenario_a_rate": finding.as_supported_scenario_a_rate,
        }
        if finding.direction == "under_capture" and finding.as_supported_hipps
        else None
    )
    return {
        "finding_id": finding.finding_id,
        "support_tier": support_tier,
        "stream": stream,
        "direction": finding.direction,
        "mds_item": finding.mds_item,
        "title": finding.title,
        "rationale": finding.rationale,
        "current_value": finding.current_value,
        "suggested_value": finding.suggested_value,
        "proposed_item_changes": dict(finding.proposed_item_changes),
        "requires_human_review": True,
        "evidence": [item.model_dump(mode="json") for item in finding.evidence],
        "counterfactual": {
            "as_coded": {
                "hipps": finding.as_coded_hipps,
                "scenario_a_rate": finding.as_coded_scenario_a_rate,
            },
            "as_supported": as_supported,
            "potential_per_day_delta": potential,
            "counted_per_day_delta": counted,
            "grouper_version": get_manifest().grouper_version,
            "manifest_version": finding.manifest_version,
        },
        "dollar_label": finding.dollar_label,
        "not_claim_payment_estimate": finding.not_claim_payment_estimate,
        "excludes": list(finding.excludes),
    }


def _combined_changes(findings: list[dict[str, Any]], *, structured_only: bool) -> dict[str, str]:
    changes: dict[str, str] = {}
    conflicts: set[str] = set()
    for finding in findings:
        if finding["direction"] != "under_capture":
            continue
        if structured_only and finding["support_tier"] != "structured_supported":
            continue
        for item, value in finding["proposed_item_changes"].items():
            if item in changes and changes[item] != value:
                conflicts.add(item)
            else:
                changes[item] = value
    for item in conflicts:
        changes.pop(item, None)
    return changes


def _summary_counterfactual(
    *,
    grouper: Any,
    mds: MDSRecord,
    baseline: Any,
    changes: dict[str, str],
    price_allowed: bool,
) -> dict[str, Any]:
    if not changes:
        return {
            "as_coded_hipps": getattr(baseline, "hipps", "") or None,
            "as_supported_hipps": getattr(baseline, "hipps", "") or None,
            "per_day_delta": None,
        }
    supported = {**mds.items, **changes}
    delta = grouper.delta_items(
        mds.items,
        supported,
        mds.ard.isoformat(),
        pps_reason_for(mds.assessment_type),
    )
    return {
        "as_coded_hipps": delta.as_coded.hipps or None,
        "as_supported_hipps": delta.as_supported.hipps or None,
        "per_day_delta": delta.delta_daily if price_allowed else None,
    }


def analyze_uploaded_case(case: AnalysisCase, grouper: Any) -> dict[str, Any]:
    """Run the real assessment/documents through both evidence streams and the CMS grouper."""
    mds = case.assessment
    run_id = f"upload-{uuid4().hex}"
    baseline = grouper.group(mds)
    groupable = bool(getattr(baseline, "groupable", True) and getattr(baseline, "hipps", ""))
    reconciliation = _baseline_status(mds, str(getattr(baseline, "hipps", "") or ""))

    structured_log = DecisionLog(
        run_id=f"{run_id}-structured",
        assessment_id=mds.stay_id,
        resident_surrogate_id=mds.stay_id,
    )
    structured_findings = derive_stream_a(
        mds,
        case.structured_signals,
        grouper,
        decisions=structured_log,
    )

    text_log = DecisionLog(
        run_id=f"{run_id}-unstructured",
        assessment_id=mds.stay_id,
        resident_surrogate_id=mds.stay_id,
    )
    text_findings: list[Finding] = []
    if case.documents:
        text_findings = text_rule_engine.derive(case, grouper, decisions=text_log)
    if not SingleSegmentPaymentPeriodEngine().in_v1_dollar_scope(mds):
        text_findings = [
            finding.model_copy(
                update={
                    "scenario_a_component_delta": None,
                    "scenario_a_component_delta_days_1_3": None,
                    "excludes": [*finding.excludes, "payment_period_out_of_v1_scope"],
                }
            )
            for finding in text_findings
        ]

    merged = _merge_findings(structured_findings, text_findings)
    finding_payloads = [
        _finding_payload(stream, finding, baseline_status=reconciliation) for stream, finding in merged
    ]
    in_payment_scope = SingleSegmentPaymentPeriodEngine().in_v1_dollar_scope(mds)
    potential = _summary_counterfactual(
        grouper=grouper,
        mds=mds,
        baseline=baseline,
        changes=_combined_changes(finding_payloads, structured_only=False),
        price_allowed=in_payment_scope,
    )
    counted = (
        _summary_counterfactual(
            grouper=grouper,
            mds=mds,
            baseline=baseline,
            changes=_combined_changes(finding_payloads, structured_only=True),
            price_allowed=in_payment_scope,
        )
        if reconciliation == "matched"
        else None
    )
    decisions = [
        {"stream": stream, **record.model_dump(mode="json")}
        for stream, log in (("structured", structured_log), ("unstructured", text_log))
        for record in log.records
    ]
    ocr_payload = [
        {
            "filename": document.filename,
            "sha256": document.sha256,
            "provider": document.provider,
            "provider_version": document.provider_version,
            "pages": len(document.pages),
            "characters": sum(len(page.text) for page in document.pages),
            "mean_confidence": (
                sum(page.confidence for page in document.pages if page.confidence is not None)
                / len([page for page in document.pages if page.confidence is not None])
                if any(page.confidence is not None for page in document.pages)
                else None
            ),
        }
        for document in case.ocr_documents
    ]
    return {
        "run_id": run_id,
        "status": "completed" if groupable else "blocked",
        "assessment": {
            "stay_id": mds.stay_id,
            "assessment_type": mds.assessment_type,
            "ard": mds.ard.isoformat(),
            "as_coded_hipps": getattr(baseline, "hipps", "") or None,
            "as_coded_scenario_a_rate": getattr(baseline, "estimated_daily_rate", "") or None,
            "groupable": groupable,
            "baseline": {
                "status": reconciliation,
                "source": None if mds.baseline_source == "unknown" else mds.baseline_source,
                "supplied_hipps": mds.baseline_hipps,
                "reconciled": mds.baseline_reconciled,
                "prototype_user_supplied": True,
            },
        },
        "inputs": {
            "mds_items": len(mds.items),
            "structured_signals": len(case.structured_signals),
            "documents": len(case.ocr_documents),
            "pages": sum(len(document.pages) for document in case.ocr_documents),
        },
        "ocr": ocr_payload,
        "findings": finding_payloads,
        "decisions": decisions,
        "summary": {
            "findings": len(finding_payloads),
            "structured_supported": sum(
                item["support_tier"] == "structured_supported" for item in finding_payloads
            ),
            "text_review_candidates": sum(
                item["support_tier"] == "text_review_candidate" for item in finding_payloads
            ),
            "review_flags": sum(item["support_tier"] == "review_flag" for item in finding_payloads),
            "potential_scenario_a_movement": potential,
            "counted_movement": counted,
        },
        "scope": {
            "rule_families": ["NTA", "Section I", "Section K", "Section GG"],
            "review_required": True,
            "general_medical_coding": False,
            "free_text_is_review_candidate_only": True,
            "structured_signals_supported": True,
            "scenario_a_is_not_claim_payment": True,
            "supported_ard_start": _SUPPORTED_ARD_START.isoformat(),
            "supported_ard_end": _SUPPORTED_ARD_END.isoformat(),
        },
    }
