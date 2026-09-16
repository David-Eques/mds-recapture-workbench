"""Stream A (NO AI): Section K diet — a structured texture-modified / mechanically-altered diet order
with K0520C3 unchecked → SLP under-capture (v1 §7.1; the proven RES003 SA→SB transition).

K0510 is retired (FY2026 uses K0520C). The diet ORDER is the structured signal; an active order in the
K0520 ``while_resident`` look-back (resolved via the AssessmentPeriodRule) is the codeability test —
NO NLP, NO swallowing inference. Only K0520C3 changes (the diet axis); the grouper owns the dollar.
"""

from __future__ import annotations

from ...models.mds import MDSRecord, StructuredSignal
from ..knowledge import load_knowledge
from .base import StreamACandidate, is_unchecked, signal_in_window

# Trust tiers that can carry a diet order strong enough to drive case-mix (review/billing tiers cannot).
_DIET_TRUST = ("mds_item", "order_active", "administered")
# A structured contradiction in the same order stream — never code a texture-modified diet over it.
_DIET_CONTRADICTIONS = ("regular diet", "regular texture", "no diet restriction", "diet discontinued")


class StreamASectionKDiet:
    rule_id = "STREAM_A_SECTION_K_DIET_SUPPORTED_NOT_CODED"

    def detect(self, mds: MDSRecord, signals: list[StructuredSignal]) -> list[StreamACandidate]:
        if not is_unchecked(mds.items, "K0520C3"):
            return []  # already coded
        k = load_knowledge()

        current_orders = [
            signal
            for signal in signals
            if signal.signal_type == "diet_order"
            and signal.trust in _DIET_TRUST
            and signal_in_window(mds, signal.effective_date, "K0520", column_id="while_resident")
        ]
        if any(
            any(phrase in signal.value.lower() for phrase in _DIET_CONTRADICTIONS)
            for signal in current_orders
        ):
            return []
        order = next(
            (
                signal
                for signal in current_orders
                if any(phrase in signal.value.lower() for phrase in k.diet_texture_phrases)
            ),
            None,
        )
        if order is None:
            return []

        return [
            StreamACandidate(
                rule_id=self.rule_id,
                direction="under_capture",
                mds_item="K0520C3",
                component="SLP",
                title="Documented mechanically-altered diet not coded in K0520C3 (structured)",
                current_value="K0520C3 (mechanically altered diet - while a resident) is not checked",
                suggested_value="Check K0520C3 — an active texture-modified diet order is documented in the look-back",
                rationale=(
                    "An active texture-modified (mechanically altered) diet order is present in the K0520 "
                    "while-resident look-back, but K0520C3 is not checked. Coding it moves the SLP component "
                    "up one diet/swallowing step. No swallowing disorder is asserted (none is in the "
                    "structured data), so the correct step is the diet axis only. Dollar impact from the grouper."
                ),
                proposed_item_changes={"K0520C3": "1"},
                structured_provenance=[order.source_table],
            )
        ]
