"""CounterfactualMdsBuilder (ARD/IPA §6) — RECAPTURE mode.

Recapture holds the ARD and varies the coding (v1 §4): the builder SEEDS from the as-coded item set
and applies ONLY candidate changes whose CandidateItemState is SUPPORTED. NOT_ASSESSED / UNSUPPORTED /
VALIDATION_ERROR never produce a favorable code (invariant #10, fail closed). Emits conformant MDS XML
for the grouper (never a loose dict). (The ARD/IPA spec's "build from scratch" governs the Mode A track,
not recapture — reconciliation #11.)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..grouper.jar_bridge import sha256_text
from ..grouper.mds_mapping import build_assessment_xml, pps_reason_for
from ..models.mds import MDSRecord
from ..models.period import CandidateItemState


@dataclass
class CounterfactualMdsPayload:
    item_values: dict[str, str]
    item_states: dict[str, CandidateItemState] = field(default_factory=dict)
    mds_xml: str = ""
    mds_xml_sha256: str = ""
    applied_changes: dict[str, str] = field(default_factory=dict)


class CounterfactualMdsBuilder:
    def build(
        self,
        as_coded: MDSRecord,
        candidate_changes: dict[str, str],
        states: dict[str, CandidateItemState],
    ) -> CounterfactualMdsPayload:
        items = dict(as_coded.items)  # seed from as-coded (recapture: hold ARD, vary coding)
        applied: dict[str, str] = {}
        for item, value in candidate_changes.items():
            if states.get(item) is CandidateItemState.SUPPORTED:
                items[item] = value
                applied[item] = value
            # any non-SUPPORTED state is dropped — a favorable addition fails closed
        xml = build_assessment_xml(items, as_coded.ard.isoformat(), pps_reason_for(as_coded.assessment_type))
        return CounterfactualMdsPayload(
            item_values=items,
            item_states=states,
            mds_xml=xml,
            mds_xml_sha256=sha256_text(xml),
            applied_changes=applied,
        )

    def build_as_coded_xml(self, as_coded: MDSRecord) -> str:
        return build_assessment_xml(
            dict(as_coded.items), as_coded.ard.isoformat(), pps_reason_for(as_coded.assessment_type)
        )
