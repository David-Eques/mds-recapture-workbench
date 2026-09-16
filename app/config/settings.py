"""Small, environment-driven runtime configuration for the public workbench."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TypedDict

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "app" / "config"
REGULATORY_MANIFEST_YAML = CONFIG_DIR / "regulatory_manifest.yaml"
ASSESSMENT_PERIOD_RULES_YAML = CONFIG_DIR / "assessment_period_rules.yaml"
PRICING_DATA_DIR = REPO_ROOT / "app" / "pricing" / "data"
DEFAULT_GROUPER_JAR_DIR = REPO_ROOT / ".cache" / "cms-grouper" / "v2.4000" / "jars"


class GrouperConfig(TypedDict):
    transport: str
    jar_dir: Path
    java: str
    timeout_seconds: int
    memory_cap_mb: int


def grouper_config() -> GrouperConfig:
    """Return the pinned CMS subprocess configuration.

    The public runtime has one grouper implementation. Environment variables may
    relocate installed tools, but cannot switch to a fixture or alternate oracle.
    """

    jar_dir = Path(os.environ.get("CMS_GROUPER_JAR_DIR", str(DEFAULT_GROUPER_JAR_DIR)))
    if not jar_dir.is_absolute():
        jar_dir = REPO_ROOT / jar_dir
    return {
        "transport": "subprocess",
        "jar_dir": jar_dir,
        "java": os.environ.get("JAVA", "java"),
        "timeout_seconds": int(os.environ.get("CMS_GROUPER_TIMEOUT_SECONDS", "30")),
        "memory_cap_mb": int(os.environ.get("CMS_GROUPER_MEMORY_MB", "1024")),
    }


def pricing_data_dir() -> Path:
    override = os.environ.get("PDPM_DATA_DIR")
    return Path(override) if override else PRICING_DATA_DIR
