"""Real CMS PDPM grouping plus deterministic Scenario-A pricing."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..config.manifest import get_manifest
from ..models.classification import PdpmResult
from ..models.mds import MDSRecord
from ..pricing import rates
from .base import GrouperDelta
from .jar_bridge import GrouperError, JarBridge, sha256_text
from .mds_mapping import build_assessment_xml, pps_reason_for


class CmsJarGrouperClient:
    """Implements the GrouperClient Protocol against the CMS PDPM Grouper JAR."""

    def __init__(self, bridge: JarBridge | None = None, region: str = "urban"):
        self._bridge = bridge if bridge is not None else JarBridge.from_config()
        self._region = region
        self._cache: dict[str, PdpmResult] = {}

    def available(self) -> bool:
        return self._bridge.available()

    def _items_ard_pps(self, assessment: MDSRecord | dict[str, Any]) -> tuple[dict[str, str], str, str]:
        if isinstance(assessment, MDSRecord):
            return (
                dict(assessment.items),
                assessment.ard.isoformat(),
                pps_reason_for(assessment.assessment_type),
            )
        if not isinstance(assessment, dict) or not isinstance(assessment.get("items"), dict):
            raise TypeError("grouper input must be an MDSRecord or a dictionary containing items")
        raw_ard = assessment.get("ard")
        if not isinstance(raw_ard, str) or not raw_ard:
            raise ValueError("grouper input requires an assessment reference date")
        raw_items = assessment["items"]
        items = {str(key): str(value) for key, value in raw_items.items() if value is not None}
        assessment_type = str(assessment.get("pps") or "01")
        return items, raw_ard, assessment_type

    # --- grouping ----------------------------------------------------------
    def _cache_key(self, xml: str) -> str:
        m = get_manifest()
        return sha256_text(f"{xml}\x00{m.grouper_version}\x00{m.version}")

    def _group_items(self, items: dict, ard: str | None, pps: str) -> PdpmResult:
        xml = build_assessment_xml(items, ard, pps)
        key = self._cache_key(xml)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        hipps, _version, errors = self._bridge.run(xml)
        if errors:
            raise GrouperError(f"grouper errors (count={len(errors)})")  # redacted: no raw error text
        if not hipps:
            # Empty HIPPS (no error) = ungroupable primary (e.g. an RTP ICD). Surface it so the RTP
            # pre-check blocks with rtp_primary (spec §5.1).
            result = PdpmResult(
                hipps="",
                estimated_daily_rate="",
                source="cms_jar",
                clinical_category="Return to Provider",
                groupable=False,
            )
        else:
            rate = rates.price_hipps(hipps, self._region)
            result = PdpmResult(hipps=hipps, estimated_daily_rate=rate, source="cms_jar", groupable=True)
        self._cache[key] = result
        return result

    def group(self, assessment: MDSRecord | dict[str, Any]) -> PdpmResult:
        items, ard, pps = self._items_ard_pps(assessment)
        return self._group_items(items, ard, pps)

    def delta(
        self,
        assessment: MDSRecord | dict[str, Any],
        proposed_changes: dict[str, str] | None = None,
    ) -> GrouperDelta:
        items, ard, pps = self._items_ard_pps(assessment)
        return self.delta_items(items, {**items, **(proposed_changes or {})}, ard, pps)

    def delta_items(
        self, coded_items: dict, supported_items: dict, ard: str | None, pps: str = "01"
    ) -> GrouperDelta:
        """Dual-grouper delta over two arbitrary MDS item dicts (the Stream A path; no fixture needed).
        The grouper owns classification + pricing; the rules layer never touches the dollar math."""
        coded = self._group_items(coded_items, ard, pps)
        supported = self._group_items(supported_items, ard, pps)
        baseline = Decimal(supported.estimated_daily_rate or "0") - Decimal(coded.estimated_daily_rate or "0")
        factor = rates.vpd_factor(rates.components_changed(coded.hipps, supported.hipps))
        return GrouperDelta(
            as_coded=coded,
            as_supported=supported,
            delta_daily=rates.money(baseline),
            delta_daily_days_1_3=rates.money(baseline * factor),
        )
