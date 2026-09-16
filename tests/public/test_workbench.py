from __future__ import annotations

import json
import shutil
import textwrap
from datetime import date
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.grouper.base import GrouperDelta
from app.intake.case import UploadedArtifact, analyze_uploaded_case, build_uploaded_case
from app.intake.ocr import DocumentProcessingTimeout, OcrDocument, OcrLine, OcrPage
from app.models.classification import PdpmResult


def assessment(*, baseline: dict | None = None) -> dict:
    payload = {
        "stay_id": "fresh-public-case",
        "assessment_type": "01",
        "ard": "2026-05-22",
        "a2400b": "2026-05-17",
        "a2400c": "2026-06-16",
        "items": {
            "I0020B": "I50.22",
            "I2900": "1",
            "I8000A": "",
            "C0500": "14",
            "K0100A": "0",
            "K0100B": "0",
            "K0100C": "0",
            "K0100D": "0",
            "K0520C3": "0",
            "O0110E1B": "0",
            "O0110F1B": "0",
            "GG0130A1": "04",
            "GG0130B1": "04",
            "GG0130C1": "03",
            "GG0170B1": "03",
            "GG0170C1": "03",
            "GG0170D1": "03",
            "GG0170E1": "03",
            "GG0170F1": "03",
            "GG0170I1": "03",
            "GG0170J1": "03",
            "GG0170K1": "03",
        },
    }
    if baseline is not None:
        payload["baseline"] = baseline
    return payload


class Grouper:
    @staticmethod
    def group(_record):  # noqa: ANN001, ANN205
        return PdpmResult(
            hipps="KAXE1",
            estimated_daily_rate="580.15",
            source="test_double",
            groupable=True,
        )

    @staticmethod
    def delta_items(coded, supported, _ard, _pps="01"):  # noqa: ANN001, ANN205
        changed = supported.get("K0520C3") == "1" and coded.get("K0520C3") != "1"
        as_coded = Grouper.group(coded)
        as_supported = (
            PdpmResult(
                hipps="KBXE1",
                estimated_daily_rate="610.69",
                source="test_double",
                groupable=True,
            )
            if changed
            else as_coded
        )
        return GrouperDelta(
            as_coded=as_coded,
            as_supported=as_supported,
            delta_daily="30.54" if changed else "0.00",
            delta_daily_days_1_3="30.54" if changed else "0.00",
        )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr("app.grouper.select.build_grouper", lambda: Grouper())
    return TestClient(app)


def post_text(
    client: TestClient,
    text: str,
    *,
    document_date: str = "2026-05-20",
    filename: str = "fresh-note.txt",
    baseline: dict | None = None,
):
    metadata = [{"filename": filename, "document_date": document_date, "document_type": "dietary"}]
    return client.post(
        "/api/analyze",
        files=[
            (
                "assessment",
                (
                    "assessment.json",
                    json.dumps(assessment(baseline=baseline)),
                    "application/json",
                ),
            ),
            ("documents", (filename, text, "text/plain")),
        ],
        data={"document_metadata": json.dumps(metadata)},
    )


def image_only_pdf(text: str) -> bytes:
    image_module = pytest.importorskip("PIL.Image")
    draw_module = pytest.importorskip("PIL.ImageDraw")
    font_module = pytest.importorskip("PIL.ImageFont")
    image = image_module.new("RGB", (2400, 600), "white")
    font = font_module.load_default(size=72)
    draw_module.Draw(image).text((90, 200), text, fill="black", font=font)
    output = BytesIO()
    image.save(output, format="PDF", resolution=200)
    return output.getvalue()


def image_png(text: str = "Diet order: mechanical soft.") -> bytes:
    image_module = pytest.importorskip("PIL.Image")
    draw_module = pytest.importorskip("PIL.ImageDraw")
    image = image_module.new("RGB", (1200, 300), "white")
    draw_module.Draw(image).text((40, 120), text, fill="black")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def assessment_xml() -> str:
    items = assessment()["items"]
    body = "\n".join(f"  <{key}>{value}</{key}>" for key, value in items.items() if value)
    return textwrap.dedent(
        f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <ASSESSMENT>
          <A0310B>01</A0310B>
          <A2300>20260522</A2300>
          <A2400B>20260517</A2400B>
          <A2400C>20260616</A2400C>
        {body}
        </ASSESSMENT>
        """
    ).lstrip()


def test_public_route_inventory_is_exact():
    assert set(app.openapi()["paths"]) == {
        "/",
        "/healthz",
        "/readyz",
        "/api/capabilities",
        "/api/analyze",
    }


def test_text_result_is_review_candidate_and_never_counted(client: TestClient):
    response = post_text(
        client,
        "Diet order: mechanical soft. No concentrated sweets.",
        baseline={"source": "claim_paid", "hipps": "KAXE1", "reconciled": True},
    )
    assert response.status_code == 200, response.text
    finding = response.json()["findings"][0]
    assert finding["support_tier"] == "text_review_candidate"
    assert finding["proposed_item_changes"] == {"K0520C3": "1"}
    assert finding["counterfactual"]["as_coded"]["hipps"] == "KAXE1"
    assert finding["counterfactual"]["as_supported"]["hipps"] == "KBXE1"
    assert finding["counterfactual"]["potential_per_day_delta"] == "30.54"
    assert finding["counterfactual"]["counted_per_day_delta"] is None
    assert finding["evidence"][0]["verification_basis"] == "native_text_exact_substring"
    assert response.json()["summary"]["counted_movement"]["per_day_delta"] is None


@pytest.mark.parametrize(
    ("text", "document_date", "filename"),
    [
        ("Routine nursing note. Resident comfortable.", "2026-05-20", "clean.txt"),
        ("Diet order: mechanical soft.", "2026-05-01", "2026-05-20-current.txt"),
        (
            "Diet order: mechanical soft was discontinued. Current diet: regular diet.",
            "2026-05-20",
            "diet.txt",
        ),
        (
            "History of chronic pancreatitis, resolved years ago. Continue pancrelipase per copied-forward list.",
            "2026-05-20",
            "history.txt",
        ),
    ],
)
def test_clean_stale_discontinued_and_historical_text_do_not_trigger(
    client: TestClient, text: str, document_date: str, filename: str
):
    response = post_text(client, text, document_date=document_date, filename=filename)
    assert response.status_code == 200, response.text
    assert response.json()["findings"] == []


@pytest.mark.parametrize(
    ("text", "document_date", "reason"),
    [
        ("Diet order: mechanical soft.", "2026-05-01", "stale_evidence"),
        ("No mechanical soft diet order is active.", "2026-05-20", "negated_target"),
        ("Diet order: mechanical soft was discontinued.", "2026-05-20", "discontinued_evidence"),
    ],
)
def test_declined_text_reasons_are_explicit(client: TestClient, text: str, document_date: str, reason: str):
    response = post_text(client, text, document_date=document_date)
    assert response.status_code == 200
    assert response.json()["findings"] == []
    assert any(decision["reason"] == reason for decision in response.json()["decisions"])


def test_structured_signal_only_counts_with_matched_baseline(client: TestClient):
    signals = {
        "signals": [
            {
                "signal_type": "diet_order",
                "trust": "order_active",
                "value": "Diet order: mechanical soft",
                "effective_date": "2026-05-20",
                "source_table": "diet_orders:unique-1",
            }
        ]
    }
    response = client.post(
        "/api/analyze",
        files={
            "assessment": (
                "assessment.json",
                json.dumps(
                    assessment(baseline={"source": "claim_paid", "hipps": "KAXE1", "reconciled": True})
                ),
                "application/json",
            ),
            "signals": ("signals.json", json.dumps(signals), "application/json"),
        },
    )
    assert response.status_code == 200, response.text
    finding = response.json()["findings"][0]
    assert finding["support_tier"] == "structured_supported"
    assert finding["counterfactual"]["counted_per_day_delta"] == "30.54"
    assert finding["evidence"][0]["verification_basis"] == "structured_record_reference"
    assert response.json()["summary"]["counted_movement"]["per_day_delta"] == "30.54"


def test_structured_csv_and_xml_assessment_are_parsed(client: TestClient):
    csv_signals = (
        "signal_type,trust,value,effective_date,source_table\n"
        'diet_order,order_active,"Diet order: mechanical soft",2026-05-20,orders:fresh-csv\n'
    )
    response = client.post(
        "/api/analyze",
        files={
            "assessment": ("assessment.xml", assessment_xml(), "application/xml"),
            "signals": ("signals.csv", csv_signals, "text/csv"),
        },
        data={"case_id": "fresh-xml-case"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["assessment"]["stay_id"] == "fresh-xml-case"
    assert response.json()["findings"][0]["support_tier"] == "structured_supported"
    assert response.json()["summary"]["counted_movement"] is None


def test_stale_structured_signal_is_withheld(client: TestClient):
    signals = {
        "signals": [
            {
                "signal_type": "diet_order",
                "trust": "order_active",
                "value": "Diet order: mechanical soft",
                "effective_date": "2026-04-01",
                "source_table": "orders:stale",
            }
        ]
    }
    response = client.post(
        "/api/analyze",
        files={
            "assessment": ("assessment.json", json.dumps(assessment()), "application/json"),
            "signals": ("signals.json", json.dumps(signals), "application/json"),
        },
    )
    assert response.status_code == 200
    assert response.json()["findings"] == []


def test_baseline_mismatch_preserves_analysis_but_disables_counting(client: TestClient):
    response = post_text(
        client,
        "Diet order: mechanical soft.",
        baseline={"source": "claim_paid", "hipps": "WRNG1", "reconciled": True},
    )
    assert response.status_code == 200
    assert response.json()["assessment"]["baseline"]["status"] == "mismatch"
    assert response.json()["summary"]["counted_movement"] is None


@pytest.mark.parametrize(
    ("baseline", "status"),
    [
        (None, "missing"),
        ({"source": "not_billed", "reconciled": False}, "not_billed"),
        ({"source": "claim_paid", "hipps": "KAXE1", "reconciled": False}, "unreconciled"),
    ],
)
def test_non_counting_baseline_states(client: TestClient, baseline: dict | None, status: str):
    response = post_text(client, "Diet order: mechanical soft.", baseline=baseline)
    assert response.status_code == 200
    assert response.json()["assessment"]["baseline"]["status"] == status
    assert response.json()["summary"]["counted_movement"] is None


def test_strict_boolean_and_required_metadata_fail_explicitly(client: TestClient):
    invalid = assessment(baseline={"source": "claim_paid", "hipps": "KAXE1", "reconciled": "false"})
    response = client.post(
        "/api/analyze",
        files={
            "assessment": ("assessment.json", json.dumps(invalid), "application/json"),
            "signals": ("signals.json", "[]", "application/json"),
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"

    missing_metadata = client.post(
        "/api/analyze",
        files=[
            ("assessment", ("assessment.json", json.dumps(assessment()), "application/json")),
            ("documents", ("note.txt", "Routine note", "text/plain")),
        ],
    )
    assert missing_metadata.status_code == 400
    assert missing_metadata.json()["error"]["code"] == "missing_document_metadata"


def test_request_validation_and_metadata_matching_are_safe(client: TestClient):
    missing_assessment = client.post(
        "/api/analyze",
        files={"signals": ("signals.json", "[]", "application/json")},
    )
    assert missing_assessment.status_code == 422
    assert set(missing_assessment.json()) == {"error"}
    assert missing_assessment.json()["error"]["code"] == "invalid_request"

    for metadata, expected in [
        (
            [
                {
                    "filename": "note.txt",
                    "document_date": "2026-05-20",
                    "document_type": "dietary",
                    "unexpected": True,
                }
            ],
            "invalid_document_metadata",
        ),
        (
            [{"filename": "different.txt", "document_date": "2026-05-20", "document_type": "dietary"}],
            "inconsistent_document_metadata",
        ),
    ]:
        response = client.post(
            "/api/analyze",
            files=[
                ("assessment", ("assessment.json", json.dumps(assessment()), "application/json")),
                ("documents", ("note.txt", "Routine note", "text/plain")),
            ],
            data={"document_metadata": json.dumps(metadata)},
        )
        assert response.status_code in {400, 422}
        assert response.json()["error"]["code"] == expected

    path_name = client.post(
        "/api/analyze",
        files=[
            ("assessment", ("assessment.json", json.dumps(assessment()), "application/json")),
            ("documents", ("../note.txt", "Routine note", "text/plain")),
        ],
        data={
            "document_metadata": json.dumps(
                [{"filename": "../note.txt", "document_date": "2026-05-20", "document_type": "dietary"}]
            )
        },
    )
    assert path_name.status_code == 400
    assert path_name.json()["error"]["code"] == "invalid_filename"


def test_out_of_period_and_invalid_hipps_are_rejected(client: TestClient):
    old = assessment()
    old["ard"] = "2025-09-30"
    invalid_hipps = assessment(baseline={"source": "claim_paid", "hipps": "INVALID", "reconciled": True})
    for payload in (old, invalid_hipps):
        response = client.post(
            "/api/analyze",
            files={
                "assessment": ("assessment.json", json.dumps(payload), "application/json"),
                "signals": ("signals.json", "[]", "application/json"),
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_input"


def test_unsupported_document_and_document_count_limits(client: TestClient):
    metadata = [{"filename": "payload.bin", "document_date": "2026-05-20", "document_type": "other"}]
    unsupported = client.post(
        "/api/analyze",
        files=[
            ("assessment", ("assessment.json", json.dumps(assessment()), "application/json")),
            ("documents", ("payload.bin", b"\x00\x01\x02", "application/octet-stream")),
        ],
        data={"document_metadata": json.dumps(metadata)},
    )
    assert unsupported.status_code == 415
    assert unsupported.json()["error"]["code"] == "unsupported_document_type"

    too_many = client.post(
        "/api/analyze",
        files=[
            ("assessment", ("assessment.json", json.dumps(assessment()), "application/json")),
            *[("documents", (f"note-{index}.txt", "note", "text/plain")) for index in range(11)],
        ],
    )
    assert too_many.status_code == 413

    binary_text = client.post(
        "/api/analyze",
        files=[
            ("assessment", ("assessment.json", json.dumps(assessment()), "application/json")),
            ("documents", ("payload.txt", b"\x00\x01\x02", "text/plain")),
        ],
        data={
            "document_metadata": json.dumps(
                [{"filename": "payload.txt", "document_date": "2026-05-20", "document_type": "other"}]
            )
        },
    )
    assert binary_text.status_code == 415


def test_per_document_size_limit(client: TestClient):
    response = client.post(
        "/api/analyze",
        files=[
            ("assessment", ("assessment.json", json.dumps(assessment()), "application/json")),
            ("documents", ("large.txt", b"a" * (20 * 1024 * 1024 + 1), "text/plain")),
        ],
        data={
            "document_metadata": json.dumps(
                [{"filename": "large.txt", "document_date": "2026-05-20", "document_type": "other"}]
            )
        },
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "upload_too_large"


def test_unavailable_ocr_executable_is_503(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TESSERACT_BINARY", "definitely-not-a-real-tesseract-binary")
    response = client.post(
        "/api/analyze",
        files=[
            ("assessment", ("assessment.json", json.dumps(assessment()), "application/json")),
            ("documents", ("note.png", image_png(), "image/png")),
        ],
        data={
            "document_metadata": json.dumps(
                [{"filename": "note.png", "document_date": "2026-05-20", "document_type": "dietary"}]
            )
        },
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "processing_dependency_unavailable"


@pytest.mark.skipif(
    shutil.which("tesseract") is None or shutil.which("pdftoppm") is None,
    reason="local OCR dependencies unavailable",
)
def test_image_only_pdf_runs_real_poppler_and_tesseract(client: TestClient):
    filename = "unique-scanned-diet.pdf"
    response = client.post(
        "/api/analyze",
        files=[
            ("assessment", ("assessment.json", json.dumps(assessment()), "application/json")),
            ("documents", (filename, image_only_pdf("Diet order: mechanical soft."), "application/pdf")),
        ],
        data={
            "document_metadata": json.dumps(
                [{"filename": filename, "document_date": "2026-05-20", "document_type": "dietary"}]
            )
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["ocr"][0]["provider"] == "Tesseract"
    finding = response.json()["findings"][0]
    assert finding["support_tier"] == "text_review_candidate"
    assert finding["evidence"][0]["page"] == 1
    assert finding["evidence"][0]["bbox"]
    assert finding["evidence"][0]["confidence"] >= 0.85


def test_low_confidence_ocr_is_declined():
    class LowConfidenceOcr:
        def extract(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN201
            return OcrDocument(
                filename="low.png",
                sha256="a" * 64,
                provider="Tesseract",
                provider_version="test",
                pages=[
                    OcrPage(
                        number=1,
                        text="Diet order: mechanical soft.",
                        confidence=0.72,
                        lines=[
                            OcrLine(
                                text="Diet order: mechanical soft.",
                                bbox=[1.0, 2.0, 3.0, 4.0],
                                confidence=0.72,
                            )
                        ],
                    )
                ],
            )

    case = build_uploaded_case(
        assessment_data=json.dumps(assessment()).encode(),
        assessment_filename="assessment.json",
        document_artifacts=[UploadedArtifact("low.png", "image/png", b"bytes", date(2026, 5, 20), "dietary")],
        ocr=LowConfidenceOcr(),
    )
    result = analyze_uploaded_case(case, Grouper())
    assert result["findings"] == []
    assert any(decision["reason"] == "low_ocr_confidence" for decision in result["decisions"])


def test_processing_timeout_is_sanitized(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    def timeout(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise DocumentProcessingTimeout("source text must not escape")

    monkeypatch.setattr("app.intake.ocr.TesseractOcr.extract", timeout)
    filename = "timeout.png"
    response = client.post(
        "/api/analyze",
        files=[
            ("assessment", ("assessment.json", json.dumps(assessment()), "application/json")),
            ("documents", (filename, b"\x89PNG\r\n\x1a\n", "image/png")),
        ],
        data={
            "document_metadata": json.dumps(
                [{"filename": filename, "document_date": "2026-05-20", "document_type": "other"}]
            )
        },
    )
    assert response.status_code == 504
    assert response.json()["error"]["message"] == "analysis timed out"
    assert "source text" not in response.text
