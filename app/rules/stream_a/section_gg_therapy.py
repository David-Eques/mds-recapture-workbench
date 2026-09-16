"""Stream A (NO AI): Section GG vs. structured therapy data (v1 §7.1) — symmetric.

UNDER-capture: walking items coded not-attempted while a structured therapy observation documents
ambulation at a mapped assist level → PT/OT under-capture (deterministic assist→GG code; never
overstates). OVER-capture (the Stream A over-capture path): a mobility item coded MORE dependent than
a structured therapy observation documents for the same task → a review flag carrying NO dollar
(invariant #2 / §7.3). Over-capture is asserted only on this *explicit contrary structured evidence*,
never on inferred absence; ``corpus_complete`` defaults false.

(Section GG performance observations from therapy are an authorized RAI source — the
``functional_observation`` trust tier — and are distinct from therapy MINUTES, which are
coverage/compliance only and never drive case-mix.) GG admission anchors to A2400B, not the ARD; the
window resolution (and a missing A2400B) fail closed. The grouper owns the dollar.
"""

from __future__ import annotations

from ...models.mds import MDSRecord, StructuredSignal
from ..knowledge import Knowledge, load_knowledge
from .base import StreamACandidate, signal_in_window

_GG_REAL_PERFORMANCE_CODES = {"01", "02", "03", "04", "05", "06"}
_GG_OBS_TRUST = ("functional_observation", "mds_item")
_WALKING_TASKS = (
    ("GG0170I1", ("walk 10 feet", "ambulated 10 feet", "able to walk 10 feet")),
    ("GG0170J1", ("walk 50 feet", "ambulated 50 feet", "walked 50 feet")),
    ("GG0170K1", ("walk 150 feet", "ambulated 150 feet", "walked 150 feet")),
)


def _assist_code(value: str, k: Knowledge) -> str | None:
    """Map the first assist-level phrase in a therapy observation to its deterministic GG code."""
    low = value.lower()
    for phrase, code in k.assist_level_to_gg:
        if phrase.lower() in low:
            return code
    return None


def _gg_observations(mds: MDSRecord, signals: list[StructuredSignal]) -> list[StructuredSignal]:
    """Therapy/functional performance observations inside the GG admission window (fail closed)."""
    return [
        s
        for s in signals
        if s.signal_type == "therapy"
        and s.trust in _GG_OBS_TRUST
        and signal_in_window(mds, s.effective_date, "GG_admission")
    ]


class StreamASectionGgTherapy:
    rule_id = "STREAM_A_SECTION_GG_THERAPY"
    rule_id_under = "STREAM_A_SECTION_GG_WALKING_NOT_ATTEMPTED"
    rule_id_over = "STREAM_A_SECTION_GG_OVERCODED_DEPENDENCY_REVIEW"

    def detect(self, mds: MDSRecord, signals: list[StructuredSignal]) -> list[StreamACandidate]:
        k = load_knowledge()
        obs = _gg_observations(mds, signals)
        if not obs:
            return []
        return self._under_capture(mds, obs, k) + self._over_capture(mds, obs, k)

    def _under_capture(
        self, mds: MDSRecord, obs: list[StructuredSignal], k: Knowledge
    ) -> list[StreamACandidate]:
        proposed: dict[str, str] = {}
        provenance: list[str] = []
        for item, task_phrases in _WALKING_TASKS:
            if str(mds.items.get(item, "")).strip() not in k.gg_not_attempted_codes:
                continue
            matches: list[tuple[StructuredSignal, str]] = []
            for signal in obs:
                low = signal.value.lower()
                if not any(task in low for task in task_phrases):
                    continue
                code = _assist_code(signal.value, k)
                if code is not None:
                    matches.append((signal, code))
            task_codes = {code for _signal, code in matches}
            if len(task_codes) != 1:
                continue
            signal, code = matches[0]
            proposed[item] = code
            provenance.append(signal.source_table)
        if not proposed:
            return []

        code_label = "/".join(sorted(set(proposed.values())))
        return [
            StreamACandidate(
                rule_id=self.rule_id_under,
                direction="under_capture",
                mds_item="/".join(proposed),
                component="PT_OT",
                title="Section GG walking coded not-attempted despite documented ambulation (structured therapy)",
                current_value="One or more specifically observed walking tasks are coded not attempted",
                suggested_value=(
                    f"Review the evidenced Section GG task(s) at documented performance {code_label}"
                ),
                rationale=(
                    "Section GG should reflect usual performance across the assessment window using all "
                    "observations, including therapy. The walking items were coded not-attempted from "
                    "day-1 status, but a structured therapy observation in the GG admission window records "
                    "a named walking task and assist level in the same observation. Only each explicitly "
                    "named task changes. Dollar impact comes from the grouper."
                ),
                proposed_item_changes=proposed,
                structured_provenance=provenance,
            )
        ]

    def _over_capture(
        self, mds: MDSRecord, obs: list[StructuredSignal], k: Knowledge
    ) -> list[StreamACandidate]:
        proposed: dict[str, str] = {}
        provenance: list[str] = []
        for item, task_phrases in k.gg_overcapture_items:
            coded = str(mds.items.get(item, "")).strip()
            if coded not in _GG_REAL_PERFORMANCE_CODES:
                continue
            for s in obs:
                low = s.value.lower()
                if not any(t in low for t in task_phrases):
                    continue
                code = _assist_code(s.value, k)
                if code is None:
                    continue
                if int(code) > int(coded):  # GG scale is inverted: documented MORE independent than coded
                    proposed[item] = code
                    if s.source_table not in provenance:
                        provenance.append(s.source_table)
                    break
        if not proposed:
            return []

        codes = "/".join(sorted(set(proposed.values())))
        return [
            StreamACandidate(
                rule_id=self.rule_id_over,
                direction="over_capture",
                mds_item="/".join(proposed),
                component="PT_OT",
                title="Section GG mobility coded more dependent than structured therapy documents",
                current_value="Section GG mobility coded at a higher dependency than a structured therapy observation",
                suggested_value=(
                    f"Review Section GG: a structured therapy observation documents more independent "
                    f"performance (GG {codes}) for these tasks, below the coded dependency"
                ),
                rationale=(
                    "A structured therapy observation in the assessment window documents these mobility tasks "
                    "at a MORE independent performance than Section GG was coded (the GG scale is inverted: "
                    "higher = more independent). Over-stated GG dependency inflates the function score. Surfaced "
                    "as review-required: over-capture is a review flag with no dollar under the "
                    "review-only safety policy, asserted only on "
                    "this explicit contrary structured evidence; a human corrects the assessment before lock."
                ),
                proposed_item_changes=proposed,
                structured_provenance=provenance,
            )
        ]
