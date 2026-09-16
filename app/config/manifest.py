"""Regulatory manifest loader + version stamper (v1 spec §0).

The manifest is the single source of truth for pinned versions, FY2026 base rates, and the
grouper JAR hash. ``manifest_version`` is the sha256 of the manifest file's bytes, so a stamp
pins the exact regulatory content of a run. Every Finding / DecisionRecord / audit row carries
it; CI fails if any is missing (``tests/parity/test_manifest_stamps.py``).
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from functools import lru_cache
from typing import Any

import yaml

from .settings import REGULATORY_MANIFEST_YAML


@lru_cache(maxsize=1)
def _raw() -> dict[str, Any]:
    return dict(yaml.safe_load(REGULATORY_MANIFEST_YAML.read_text(encoding="utf-8")) or {})


@lru_cache(maxsize=1)
def manifest_version() -> str:
    """Stable content hash of the manifest file (short sha256). The version stamp on every row."""
    digest = hashlib.sha256(REGULATORY_MANIFEST_YAML.read_bytes()).hexdigest()
    return f"sha256:{digest[:16]}"


class RegulatoryManifest:
    """Typed accessors over the pinned manifest."""

    def __init__(self, data: dict[str, Any], version: str):
        self._d = data
        self.version = version

    @property
    def fiscal_year(self) -> int:
        return int(self._d["fiscal_year"])

    @property
    def rai_manual_version(self) -> str:
        return str(self._d["rai_manual"]["version"])

    @property
    def mds_item_sets_version(self) -> str:
        return str(self._d["mds_item_sets"]["version"])

    @property
    def grouper_version(self) -> str:
        return str(self._d["pdpm_grouper"]["reports_as"])

    @property
    def grouper_jar_sha256(self) -> str:
        return str(self._d["pdpm_grouper"]["primary_jar_sha256"])

    @property
    def grouper_java_runtime(self) -> int:
        return int(self._d["pdpm_grouper"]["runtime_java"])

    def base_rates(self, region: str = "urban") -> dict[str, Decimal]:
        return {k: Decimal(v) for k, v in self._d["base_rates"][region].items()}

    @property
    def cmi_table_file(self) -> str:
        return str(self._d["cmi_table_file"])

    @property
    def nutritional_approaches_item(self) -> str:
        return str(self._d["nutritional_approaches_item"])

    def as_stamp(self) -> dict[str, str]:
        """The version block embedded on findings/audit rows and asserted against fixtures."""
        return {
            "manifest_version": self.version,
            "rai_manual": self.rai_manual_version,
            "mds_item_sets": self.mds_item_sets_version,
            "pdpm_grouper": self.grouper_version,
        }


@lru_cache(maxsize=1)
def get_manifest() -> RegulatoryManifest:
    return RegulatoryManifest(_raw(), manifest_version())
