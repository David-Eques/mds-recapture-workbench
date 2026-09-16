"""MDS-state -> CMS grouper input (MDS 3.0 XML <ASSESSMENT>).

Turns ``mds_items`` (item code -> value) plus the ARD and PPS reason into one ``<ASSESSMENT>``
XML document the grouper reads. Element names are MDS item codes (the grouper's ``Rai300`` enum
names); any item NOT emitted is treated as blank / not-checked (the default-fill). Tags the
grouper doesn't read are ignored. Diagnosis codes are emitted verbatim; the grouper normalizes ICD
codes for lookup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

DIAGNOSIS_ITEMS = {"I0020B"} | {f"I8000{c}" for c in "ABCDEFGHIJ"}

# An MDS 3.0 item code: 1-2 section letters (GG is two) + 4 digits + optional sub-item suffix
# (A0310B, GG0170I1, K0520C3, I8000A, O0110E1B, C0500). Metadata/filler tags (ASMT_SYS_CD,
# ITEM_FILLER_045) do not match.
_MDS_ITEM_RE = re.compile(r"^[A-Z]{1,2}[0-9]{4}[A-Z0-9]*$")
_BLANK_VALUES = {"", "^", "-"}  # jRAVEN encodes not-applicable/blank as '^'

# DEFENSIVE crosswalk (pre-Oct-2023 -> current V3.10.2), a NO-OP for v1 (we author current codes
# natively). Kept as a safety net so a stray retired code reaches the grouper in the form it reads
# (K0510C2 does nothing on V2.4000; K0520C3=1 moves SLP). K0510 must otherwise be rejected upstream.
ITEM_ALIASES = {
    "K0510A1": "K0520A2",
    "K0510A2": "K0520A3",
    "K0510B1": "K0520B2",
    "K0510B2": "K0520B3",
    "K0510C2": "K0520C3",
    "O0100A2": "O0110A1B",
    "O0100B2": "O0110B1B",
    "O0100C2": "O0110C1B",
    "O0100D2": "O0110D1B",
    "O0100E2": "O0110E1B",
    "O0100F2": "O0110F1B",
    "O0100H2": "O0110H1B",
    "O0100I2": "O0110I1B",
    "O0100J2": "O0110J1B",
    "O0100M2": "O0110M1B",
    "D0300": "D0160",
}


def ard_to_yyyymmdd(ard: str | None) -> str:
    """'2026-05-20' -> '20260520' (the grouper's BASIC_ISO_DATE A2300 format)."""
    return (ard or "").replace("-", "").strip()


def pps_reason_for(assessment_type: str | None) -> str:
    """Return the uploaded two-digit A0310B PPS reason, defaulting to a 5-day assessment."""
    value = (assessment_type or "").strip()
    return value if re.fullmatch(r"\d{2}", value) else "01"


def build_assessment_xml(items: dict[str, str], ard: str | None, pps_reason: str = "01") -> str:
    """Build the MDS XML for one assessment from item key->value pairs."""
    elems: list[tuple[str, str]] = [
        ("A0310A", "99"),  # OBRA: none of the above (pure PPS assessment)
        ("A0310B", pps_reason),  # PPS reason (01 = 5-day)
        ("A2300", ard_to_yyyymmdd(ard)),  # assessment reference date
    ]
    for key in sorted(items):
        value = items[key]
        if value is None or str(value).strip() == "":
            continue  # blank -> omit -> grouper treats as not-checked
        elems.append((ITEM_ALIASES.get(key, key), str(value).strip()))

    body = "\n".join(f"  <{tag}>{escape(val)}</{tag}>" for tag, val in elems)
    return f'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n<ASSESSMENT>\n{body}\n</ASSESSMENT>\n'


@dataclass
class ParsedAssessment:
    """The deterministic parse of a conformant MDS 3.0 <ASSESSMENT> XML (e.g. jRAVEN output)."""

    items: dict[str, str] = field(default_factory=dict)
    a2300: str | None = None  # assessment reference date (ARD), BASIC_ISO_DATE (yyyymmdd)
    a0310b: str | None = None  # PPS reason

    @property
    def ard_iso(self) -> str | None:
        """'20260520' -> '2026-05-20' (None if absent / malformed)."""
        d = (self.a2300 or "").strip()
        return f"{d[0:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else None


def parse_assessment_xml(xml: str) -> ParsedAssessment:
    """Parse a conformant MDS 3.0 <ASSESSMENT> XML into MDS item values (the inverse of
    ``build_assessment_xml``). Tolerates the full jRAVEN export shape: metadata tags
    (``ASMT_SYS_CD``…), ``ITEM_FILLER_*`` placeholders, and ``^``-blanked items are all skipped;
    only real MDS item codes with a non-blank value are kept. PHI-bearing items (names/MRNs) are
    returned verbatim if present — never log this dict (invariant #7); the caller takes only what it needs."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError("assessment XML is malformed") from exc
    items: dict[str, str] = {}
    for el in root.iter():
        tag = el.tag
        if not _MDS_ITEM_RE.match(tag):
            continue
        value = (el.text or "").strip()
        if value in _BLANK_VALUES:
            continue
        items[tag] = value
    a2300 = items.pop("A2300", None)
    a0310b = items.pop("A0310B", None)
    return ParsedAssessment(items=items, a2300=a2300, a0310b=a0310b)
