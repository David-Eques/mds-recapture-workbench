"""AssessmentPeriodRule resolver (ARD/IPA §5). Resolution is EXACT on
(item_id, subitem_id, column_id, assessment_type) — NO prefix matching. Any unresolved item FAILS
CLOSED. K0510 is rejected (FY2026 uses K0520).
"""

from __future__ import annotations

from functools import lru_cache

import yaml

from ..config.settings import ASSESSMENT_PERIOD_RULES_YAML
from ..models.period import AssessmentPeriodRule, AssessmentTypeKey


class UnresolvedPeriodRule(KeyError):
    """No AssessmentPeriodRule resolves for the given key — fail closed (invariant #10)."""


class RetiredItemError(ValueError):
    """A retired item code (e.g. K0510) was used. FY2026 uses K0520."""


def _key(item_id: str, subitem_id: str | None, column_id: str | None, at: str) -> tuple:
    return (item_id, subitem_id, column_id, at)


class PeriodRuleResolver:
    def __init__(self, rules: list[AssessmentPeriodRule]):
        self._by_key: dict[tuple, AssessmentPeriodRule] = {}
        for r in rules:
            self._by_key[_key(r.item_id, r.subitem_id, r.column_id, r.assessment_type)] = r

    def resolve(
        self,
        item_id: str,
        assessment_type: AssessmentTypeKey,
        *,
        subitem_id: str | None = None,
        column_id: str | None = None,
    ) -> AssessmentPeriodRule:
        if item_id.startswith("K0510"):
            raise RetiredItemError(f"{item_id} is retired; FY2026 uses K0520")
        try:
            return self._by_key[_key(item_id, subitem_id, column_id, assessment_type)]
        except KeyError as exc:
            raise UnresolvedPeriodRule(
                f"no AssessmentPeriodRule for (item={item_id}, subitem={subitem_id}, "
                f"column={column_id}, type={assessment_type})"
            ) from exc


@lru_cache(maxsize=1)
def load_period_rules() -> PeriodRuleResolver:
    data = yaml.safe_load(ASSESSMENT_PERIOD_RULES_YAML.read_text(encoding="utf-8")) or {}
    rules = [AssessmentPeriodRule(**row) for row in data.get("rules", [])]
    return PeriodRuleResolver(rules)
