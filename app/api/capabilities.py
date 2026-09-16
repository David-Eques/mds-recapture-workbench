"""Truthful, runtime-derived capability metadata."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from ..config.manifest import get_manifest
from ..config.settings import REPO_ROOT, grouper_config
from ..grouper.jar_bridge import GrouperError, GrouperIntegrityError, GrouperUnavailable, JarBridge

router = APIRouter()


def _version(binary: str, *args: str) -> dict[str, Any]:
    resolved = shutil.which(binary)
    if resolved is None:
        return {"available": False, "version": None}
    try:
        process = subprocess.run(
            [resolved, *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "version": None}
    output = ((process.stdout or "") + "\n" + (process.stderr or "")).strip()
    first = output.splitlines()[0] if output else None
    return {"available": process.returncode == 0, "version": first}


def _grouper() -> dict[str, Any]:
    manifest = get_manifest()
    try:
        available = JarBridge.from_config().available() and _cms_hashes_valid()
    except (GrouperUnavailable, GrouperIntegrityError, GrouperError, OSError):
        available = False
    return {
        "available": available,
        "version": manifest.grouper_version,
        "manifest_version": manifest.version,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cms_hashes_valid() -> bool:
    try:
        lock = json.loads((REPO_ROOT / "cms-grouper.lock.json").read_text(encoding="utf-8"))
        jar_root = Path(grouper_config()["jar_dir"])
        package_root = jar_root.parent
        return all(
            (package_root / member["output_path"]).is_file()
            and _sha256(package_root / member["output_path"]) == member["sha256"]
            for member in lock["members"]
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def collect_capabilities() -> dict[str, Any]:
    runtime = {
        "tesseract": _version("tesseract", "--version"),
        "pdfinfo": _version("pdfinfo", "-v"),
        "pdftoppm": _version("pdftoppm", "-v"),
        "java": _version("java", "-version"),
        "cms_grouper": _grouper(),
    }
    return {
        "ready": all(item["available"] for item in runtime.values()),
        "runtime": runtime,
        "scope": {
            "fiscal_year": 2026,
            "assessment_dates": {"start": "2025-10-01", "end": "2026-09-30"},
            "structured": {
                "nta_diagnoses": ["K86.1", "E11.351"],
                "section_i_primary_classes": 6,
                "section_k": ["K0520C3"],
                "section_gg": "selected walking and transfer tasks",
            },
            "text": {
                "interpretation": "deterministic phrase and context matching",
                "nta_diagnoses": ["K86.1", "E11.351"],
                "result_tier": "text_review_candidate",
                "counted_revenue": False,
            },
        },
        "limits": {
            "documents": 10,
            "document_bytes": 20 * 1024 * 1024,
            "request_bytes": 50 * 1024 * 1024,
            "pages": 50,
            "image_pixels": 50_000_000,
            "analysis_concurrency": 2,
            "analysis_timeout_seconds": 180,
        },
    }


@router.get("/api/capabilities")
def capabilities() -> dict[str, Any]:
    return collect_capabilities()
