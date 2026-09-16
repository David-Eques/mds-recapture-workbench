"""Conservative payment-period scope for Scenario-A movement."""

from __future__ import annotations

from .mds import MDSRecord

PAYMENT_PERIOD_OUT_OF_SCOPE = "payment_period_out_of_v1_scope"


class SingleSegmentPaymentPeriodEngine:
    """Exclude IPA, incomplete Part-A dates, and unreconciled baselines from dollar aggregation."""

    def in_v1_dollar_scope(self, record: MDSRecord) -> bool:
        if record.assessment_type == "08":
            return False
        if record.a2400b is None or record.a2400c is None:
            return False
        if record.baseline_source == "unknown" or record.baseline_hipps is None:
            return False
        return record.baseline_reconciled

    def exclusion_reason(self, record: MDSRecord) -> str | None:
        return None if self.in_v1_dollar_scope(record) else PAYMENT_PERIOD_OUT_OF_SCOPE
