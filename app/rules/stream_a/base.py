"""Shared scaffolding for the Stream A structured-comparison rules (v1 §7.1, NO AI).

A Stream A ``Rule.detect`` is pure and deterministic: it compares the as-coded ``MDSRecord`` items
against ``StructuredSignal``s (coded dx / MAR / diet order / therapy observation) and returns zero or
more ``StreamACandidate``s. It NEVER computes a dollar — the engine attaches dollars from the grouper
seam (invariant #1). The structured record IS the citation (``source_table``), so there is no NLP and
no chart text. Codeability is the ``AssessmentPeriodRule`` window + the Section I active-diagnosis
two-step; missing dates / unresolved windows FAIL CLOSED (invariant #10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from ...codeability.period_rules import UnresolvedPeriodRule, load_period_rules
from ...codeability.windows import AnchorDates, WindowError, in_window
from ...models.mds import MDSRecord, StructuredSignal
from ...models.period import CandidateItemState, assessment_type_key


@dataclass
class StreamACandidate:
    """A proposed structured coding change, with its own narrative + provenance. ``state`` gates the
    counterfactual build (only SUPPORTED is applied — fail closed). Over-capture carries no dollar."""

    rule_id: str
    direction: str  # "under_capture" | "over_capture"
    mds_item: str
    component: str  # NTA | SLP | PT_OT | NURSING — selects the contractual dollar label
    title: str
    current_value: str
    suggested_value: str
    rationale: str
    proposed_item_changes: dict[str, str]
    state: CandidateItemState = CandidateItemState.SUPPORTED
    structured_provenance: list[str] = field(default_factory=list)
    candidate_dx_codes: tuple[str, ...] = ()


@runtime_checkable
class StreamARule(Protocol):
    rule_id: str

    def detect(self, mds: MDSRecord, signals: list[StructuredSignal]) -> list[StreamACandidate]: ...


def is_unchecked(items: dict[str, str], key: str) -> bool:
    """An MDS checkbox item is blank or coded 0 (i.e. not asserted)."""
    return str(items.get(key, "")).strip() in ("", "0")


def signal_in_window(
    mds: MDSRecord,
    effective_date: date | None,
    item_id: str,
    *,
    subitem_id: str | None = None,
    column_id: str | None = None,
) -> bool:
    """True iff ``effective_date`` falls inside the resolved AssessmentPeriodRule window for this item.

    Fails closed: a missing date, an unmapped assessment type, an unresolved rule, or an
    unresolvable anchor (e.g. GG_admission with no A2400B) all return False — NOT_ASSESSED never
    becomes SUPPORTED (invariant #10)."""
    if effective_date is None:
        return False
    try:
        at = assessment_type_key(mds.assessment_type)
    except ValueError:
        return False
    try:
        rule = load_period_rules().resolve(item_id, at, subitem_id=subitem_id, column_id=column_id)
    except (UnresolvedPeriodRule, ValueError):
        return False
    anchors = AnchorDates(ard=mds.ard, part_a_start=mds.a2400b, admission_entry_reentry=mds.a2400b)
    try:
        return in_window(effective_date, rule, anchors)
    except WindowError:
        return False
