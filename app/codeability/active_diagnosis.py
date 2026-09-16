"""Section I active-diagnosis logic (ARD/IPA §5): a diagnosis is in-window iff physician-documented
within 60 days of the ARD AND active-status evidence within 7 days of the ARD — EXCEPT I2300 (UTI),
which uses a 30-day look-back + the McGeer/NHSN/Loeb determination instead of the 7-day active step.

GG admission is anchored to A2400B (part_a_start), NOT the ARD (handled by the rule's anchor); it does
not slide with the ARD. This module is the active-diagnosis two-step only.
"""

from __future__ import annotations

from datetime import date, timedelta

_DOC_WINDOW_DAYS = 60
_ACTIVE_WINDOW_DAYS = 7
_UTI_WINDOW_DAYS = 30


def is_active_diagnosis_in_window(
    *, ard: date, physician_documented_on: date | None, active_status_on: date | None
) -> bool:
    """Two-step: documented within 60 days of ARD AND active within 7 days of ARD. Missing either
    date fails closed (NOT_ASSESSED never becomes SUPPORTED)."""
    if physician_documented_on is None or active_status_on is None:
        return False
    doc_start = ard - timedelta(days=_DOC_WINDOW_DAYS - 1)
    active_start = ard - timedelta(days=_ACTIVE_WINDOW_DAYS - 1)
    documented = doc_start <= physician_documented_on <= ard
    active = active_start <= active_status_on <= ard
    return documented and active


def is_uti_in_window(*, ard: date, observation_on: date | None, mcgeer_nhsn_loeb_met: bool) -> bool:
    """I2300 UTI: 30-day look-back + the McGeer/NHSN/Loeb criterion (not the 7-day active step)."""
    if observation_on is None or not mcgeer_nhsn_loeb_met:
        return False
    start = ard - timedelta(days=_UTI_WINDOW_DAYS - 1)
    return start <= observation_on <= ard
