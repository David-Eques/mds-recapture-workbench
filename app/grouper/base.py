"""The deterministic grouper interface used by the evidence pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..models.classification import PdpmResult


@dataclass
class GrouperDelta:
    as_coded: PdpmResult
    as_supported: PdpmResult
    delta_daily: str
    delta_daily_days_1_3: str


@runtime_checkable
class GrouperClient(Protocol):
    def group(self, assessment) -> PdpmResult:  # noqa: ANN001
        """Return the PDPM result (HIPPS + estimated daily rate) for one MDS record."""
        ...
