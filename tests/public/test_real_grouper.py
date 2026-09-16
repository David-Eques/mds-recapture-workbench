from __future__ import annotations

import json
from datetime import date

import pytest

from app.grouper.select import build_grouper
from app.intake.case import UploadedArtifact, analyze_uploaded_case, build_uploaded_case

from .test_workbench import assessment


def test_fresh_bytes_drive_real_cms_grouper():
    grouper = build_grouper()
    if not grouper.available():
        pytest.skip("verified CMS grouper runtime is unavailable")
    case = build_uploaded_case(
        assessment_data=json.dumps(
            assessment(baseline={"source": "claim_paid", "hipps": "KAXE1", "reconciled": True})
        ).encode(),
        assessment_filename="assessment.json",
        document_artifacts=[
            UploadedArtifact(
                "fresh-independent-note.txt",
                "text/plain",
                b"Diet order: mechanical soft. No concentrated sweets.",
                date(2026, 5, 20),
                "dietary",
            )
        ],
    )
    result = analyze_uploaded_case(case, grouper)
    finding = result["findings"][0]
    assert finding["support_tier"] == "text_review_candidate"
    assert finding["counterfactual"]["as_coded"]["hipps"] == "KAXE1"
    assert finding["counterfactual"]["as_supported"]["hipps"] == "KBXE1"
    assert finding["counterfactual"]["potential_per_day_delta"] == "30.54"
    assert finding["counterfactual"]["counted_per_day_delta"] is None
