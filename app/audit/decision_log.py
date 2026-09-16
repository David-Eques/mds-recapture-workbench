"""DecisionLog — the in-run accumulator for the Decision Audit Log (ported from v0).

A pure, in-memory collector the engine writes dispositions to during a run. Produces
``DecisionRecord`` objects (persistence is the DB ``writer`` in step 1). Every record is stamped
with the regulatory ``manifest_version`` (§0). The sink is OPTIONAL everywhere it is threaded
(default None), so a caller that passes nothing sees byte-identical engine behavior.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from ..config.manifest import manifest_version
from ..models.decisions import DecisionReason, DecisionRecord, Disposition
from ..models.facts import EvidenceCitation
from ..models.findings import Finding


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class DecisionLog:
    """Accumulates DecisionRecords for one run. Helpers per disposition keep call sites terse."""

    def __init__(self, *, run_id: str, assessment_id: str, resident_surrogate_id: str):
        self.run_id = run_id
        self.assessment_id = assessment_id
        self.resident_surrogate_id = resident_surrogate_id
        self.records: list[DecisionRecord] = []

    def _add(
        self,
        disposition: Disposition,
        reason: DecisionReason,
        *,
        target: str | None = None,
        finding_id: str | None = None,
        detail: dict | None = None,
        evidence: list[EvidenceCitation] | None = None,
    ) -> DecisionRecord:
        rec = DecisionRecord(
            decision_id=uuid4().hex,
            run_id=self.run_id,
            assessment_id=self.assessment_id,
            resident_surrogate_id=self.resident_surrogate_id,
            disposition=disposition,
            reason=reason,
            target=target,
            finding_id=finding_id,
            detail=detail or {},
            evidence=list(evidence or []),
            manifest_version=manifest_version(),
            created_at=_now_iso(),
        )
        self.records.append(rec)
        return rec

    # --- disposition helpers ----------------------------------------------
    def surfaced(self, finding: Finding) -> DecisionRecord:
        return self._add(
            "surfaced",
            "gap_surfaced",
            target=finding.mds_item,
            finding_id=finding.finding_id,
            detail={"direction": finding.direction, "title": finding.title},
            evidence=finding.evidence,
        )

    def declined(
        self,
        reason: DecisionReason,
        *,
        target: str | None = None,
        detail: dict | None = None,
        evidence: list[EvidenceCitation] | None = None,
    ) -> DecisionRecord:
        return self._add("declined", reason, target=target, detail=detail, evidence=evidence)

    def blocked(
        self,
        reason: DecisionReason,
        *,
        target: str | None = None,
        detail: dict | None = None,
        evidence: list[EvidenceCitation] | None = None,
    ) -> DecisionRecord:
        return self._add("blocked", reason, target=target, detail=detail, evidence=evidence)

    def ignored(
        self,
        reason: DecisionReason,
        *,
        target: str | None = None,
        detail: dict | None = None,
        evidence: list[EvidenceCitation] | None = None,
    ) -> DecisionRecord:
        return self._add("ignored", reason, target=target, detail=detail, evidence=evidence)

    def no_gap(self, *, detail: dict | None = None) -> DecisionRecord:
        """One per-run summary when the run surfaced nothing and declined/blocked nothing of
        concern — so a clean run is not an empty log (§8)."""
        return self._add("declined", "no_gap", detail=detail)

    def has_concern(self) -> bool:
        return any(r.disposition in ("declined", "blocked", "ignored") for r in self.records)
