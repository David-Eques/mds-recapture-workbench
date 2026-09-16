"""Deterministic CMS grouper output models."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class PdpmResult(BaseModel):
    """CMS classification plus the separately calculated Scenario-A rate."""

    model_config = ConfigDict(extra="ignore")

    hipps: str
    nta_points: int | None = None
    estimated_daily_rate: str = ""
    source: Literal["cms_jar", "test_double"] = "cms_jar"
    clinical_category: str | None = None
    groupable: bool | None = None


class ComponentClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: str
    cmg: str | None = None
    cmi: Decimal | None = None


class GrouperResult(BaseModel):
    """The widened grouper seam output (v1). HIPPS comes from the JAR; CMGs/CMIs are derived
    deterministically from the HIPPS characters + the manifest CMI table (not emitted by the JAR)."""

    model_config = ConfigDict(extra="forbid")

    hipps: str
    groupable: bool
    clinical_category: str | None = None
    component_cmgs: dict[str, str] = {}
    component_cmis: dict[str, Decimal] = {}
    grouper_version: str = ""
    manifest_version: str = ""
    mds_xml_sha256: str = ""
    errors: list[str] = []
