"""Construct the only public grouper implementation: the verified CMS JAR."""

from __future__ import annotations

from .cms_jar_grouper import CmsJarGrouperClient


def build_grouper() -> CmsJarGrouperClient:
    return CmsJarGrouperClient()
