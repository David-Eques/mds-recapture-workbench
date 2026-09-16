"""Look-back window math (ARD/IPA §5 helpers).

  fixed           -> [anchor + start_offset, anchor + end_offset]
  lookback        -> [anchor - (lookback_days - 1), anchor]   (ends ON the anchor)
  prior_to_anchor -> [anchor - lookback_days, anchor - 1]     (ends the DAY BEFORE the anchor)

`while_not_resident` uses prior_to_anchor: it covers pre-entry days, so the window ends the day
BEFORE the entry anchor (ending it ON the anchor was the off-by-one defect the ARD/IPA spec fixed).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ..models.period import AssessmentPeriodRule


@dataclass(frozen=True)
class AnchorDates:
    ard: date
    part_a_start: date | None = None
    admission_entry_reentry: date | None = None


class WindowError(ValueError):
    """A rule could not be resolved to a concrete window (missing anchor / offsets)."""


def _anchor_date(rule: AssessmentPeriodRule, anchors: AnchorDates) -> date:
    if rule.anchor == "ard":
        return anchors.ard
    if rule.anchor == "part_a_start":
        if anchors.part_a_start is None:
            raise WindowError(f"{rule.item_id}: anchor part_a_start (A2400B) is unknown")
        return anchors.part_a_start
    if rule.anchor == "admission_entry_reentry":
        if anchors.admission_entry_reentry is None:
            raise WindowError(f"{rule.item_id}: anchor admission_entry_reentry is unknown")
        return anchors.admission_entry_reentry
    raise WindowError(f"{rule.item_id}: unsupported anchor {rule.anchor!r}")


def window_for(rule: AssessmentPeriodRule, anchors: AnchorDates) -> tuple[date, date]:
    anchor = _anchor_date(rule, anchors)
    if rule.window_kind == "fixed":
        if rule.start_offset_days is None or rule.end_offset_days is None:
            raise WindowError(f"{rule.item_id}: fixed window needs start/end offsets")
        return (
            anchor + timedelta(days=rule.start_offset_days),
            anchor + timedelta(days=rule.end_offset_days),
        )
    if rule.window_kind == "lookback":
        if rule.lookback_days is None:
            raise WindowError(f"{rule.item_id}: lookback window needs lookback_days")
        return (anchor - timedelta(days=rule.lookback_days - 1), anchor)  # ends ON the anchor
    if rule.window_kind == "prior_to_anchor":
        if rule.lookback_days is None:
            raise WindowError(f"{rule.item_id}: prior_to_anchor window needs lookback_days")
        return (anchor - timedelta(days=rule.lookback_days), anchor - timedelta(days=1))  # ends DAY BEFORE
    raise WindowError(f"{rule.item_id}: unsupported window_kind {rule.window_kind!r}")


def in_window(value: date, rule: AssessmentPeriodRule, anchors: AnchorDates) -> bool:
    start, end = window_for(rule, anchors)
    return start <= value <= end
