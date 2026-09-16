"""Deterministic PDPM pricing from the FY2026 rate pack (the reference math).

The grouper owns CLASSIFICATION (HIPPS); this module owns PRICING — turning a HIPPS into the
Scenario-A single-assessment per-diem. Keeping pricing here (not in the rules engine) honors
invariant #1: dollars originate only on the grouper/pricing side of the boundary.

Convention (Scenario A): urban rates, day 4–20 baseline (variable-per-diem factor = 1.0), no wage
index. The non-case-mix base add-on comes from the regulatory manifest (the source of truth for
base rates); the per-component CMI-adjusted rates come from the pinned CMI lookup table
(``case_mix_indexes_fy2026.csv``: rate = base × CMI, cross-checked to the FY2026 final rule).

HIPPS layout: char 1 = PT and OT group, char 2 = SLP, char 3 = nursing CMG char, char 4 = NTA,
char 5 = assessment indicator (not priced). VPD: NTA pays 3× on days 1–3; PT/OT/SLP/Nursing are
flat across days 1–20. So a finding's days-1–3 delta differs from baseline only when NTA moved.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from functools import lru_cache

from ..config.manifest import get_manifest
from ..config.settings import pricing_data_dir

# Days-1–3 variable-per-diem multiplier per component (FY2026: NTA 3×; others flat within day 1–20).
_VPD_DAYS_1_3 = {
    "NTA": Decimal("3"),
    "PT": Decimal("1"),
    "OT": Decimal("1"),
    "SLP": Decimal("1"),
    "Nursing": Decimal("1"),
}

# HIPPS character position (0-indexed) -> the case-mix components it governs. Position 4 (the
# assessment indicator) is not priced.
_POS_COMPONENTS: dict[int, tuple[str, ...]] = {
    0: ("PT", "OT"),
    1: ("SLP",),
    2: ("Nursing",),
    3: ("NTA",),
}


class UnknownHippsError(ValueError):
    """A HIPPS character has no rate-table entry for its component."""


def money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


@lru_cache(maxsize=1)
def _cmi_rows() -> list[dict[str, str]]:
    path = pricing_data_dir() / get_manifest().cmi_table_file
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@lru_cache(maxsize=1)
def _rate_by_component_char() -> dict[str, dict[str, dict[str, Decimal]]]:
    """component -> hipps_char -> {"urban","rural"} adjusted day-4–20 rate (precomputed base × CMI)."""
    out: dict[str, dict[str, dict[str, Decimal]]] = {}
    for row in _cmi_rows():
        out.setdefault(row["component"], {})[row["hipps_char"]] = {
            "urban": Decimal(row["rate_urban"]),
            "rural": Decimal(row["rate_rural"]),
        }
    return out


@lru_cache(maxsize=1)
def _group_by_component_char() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for row in _cmi_rows():
        out.setdefault(row["component"], {})[row["hipps_char"]] = row["group"]
    return out


def group_for_char(component: str, char: str) -> str | None:
    return _group_by_component_char().get(component, {}).get(char)


def _component_rate(component: str, char: str, region: str) -> Decimal:
    try:
        return _rate_by_component_char()[component][char][region]
    except KeyError as exc:
        raise UnknownHippsError(f"no FY2026 {region} rate for {component} HIPPS character {char!r}") from exc


def _non_case_mix(region: str) -> Decimal:
    """The flat non-case-mix base add-on — from the regulatory manifest (source of truth)."""
    return get_manifest().base_rates(region)["non_case_mix"]


def component_breakdown(hipps: str, region: str = "urban") -> dict[str, Decimal]:
    h = (hipps or "").strip().upper()
    if len(h) < 4:
        raise UnknownHippsError(f"HIPPS {hipps!r} is too short to price (need >= 4 characters)")
    pt_ot, slp, nursing, nta = h[0], h[1], h[2], h[3]
    return {
        "PT": _component_rate("PT", pt_ot, region),
        "OT": _component_rate("OT", pt_ot, region),
        "SLP": _component_rate("SLP", slp, region),
        "Nursing": _component_rate("Nursing", nursing, region),
        "NTA": _component_rate("NTA", nta, region),
        "non_case_mix": _non_case_mix(region),
    }


def price_hipps(hipps: str, region: str = "urban") -> str:
    total = sum(component_breakdown(hipps, region).values(), Decimal("0"))
    return money(total)


def components_changed(as_coded_hipps: str, as_supported_hipps: str) -> list[str]:
    a = (as_coded_hipps or "").strip().upper()
    b = (as_supported_hipps or "").strip().upper()
    changed: list[str] = []
    for pos, comps in _POS_COMPONENTS.items():
        if pos < len(a) and pos < len(b) and a[pos] != b[pos]:
            changed.extend(comps)
    return changed


def vpd_factor(components: list[str]) -> Decimal:
    """Days-1–3 multiplier = max VPD factor over the changed components (NTA=3, else 1)."""
    return max((_VPD_DAYS_1_3.get(c, Decimal("1")) for c in components), default=Decimal("1"))


def price_delta(as_coded_hipps: str, as_supported_hipps: str, region: str = "urban") -> tuple[str, str]:
    coded = sum(component_breakdown(as_coded_hipps, region).values(), Decimal("0"))
    supported = sum(component_breakdown(as_supported_hipps, region).values(), Decimal("0"))
    baseline = supported - coded
    factor = vpd_factor(components_changed(as_coded_hipps, as_supported_hipps))
    return money(baseline), money(baseline * factor)
