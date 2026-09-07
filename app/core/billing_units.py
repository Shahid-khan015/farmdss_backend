"""The one place that says how an operation is charged.

The rule itself is unchanged and owner-independent: every operation is charged per
hectare except Threshing and Grading, which are charged per hour. What changed is that
it used to be restated in five places (the cost service, the charge schema, the CSV/PDF
exporter, and twice in the mobile app) -- one of which compared Title-Case names
case-sensitively while the rest lower-cased, so the same operation could be billed per
hour by the backend while the owner was shown a per-hectare rate editor.

The *unit* for a session is resolved once, at session start, and stored on the session
row (``sessions.charge_unit``). Nothing downstream should re-derive it from the operation
name -- read the column.
"""
from __future__ import annotations

#: Operations charged on elapsed worked time rather than on area covered.
PER_HOUR_OPERATION_TYPES = frozenset({"threshing", "grading"})

CHARGE_UNIT_PER_HA = "per_ha"
CHARGE_UNIT_PER_HOUR = "per_hour"

#: Both permitted values of ``sessions.charge_unit``.
CHARGE_UNITS = (CHARGE_UNIT_PER_HA, CHARGE_UNIT_PER_HOUR)

#: Short suffix used in human-readable cost notes and rate labels.
UNIT_SUFFIX = {CHARGE_UNIT_PER_HA: "ha", CHARGE_UNIT_PER_HOUR: "hr"}


def normalize_operation_type(operation_type: str | None) -> str:
    """Canonical comparison form: trimmed and lower-cased."""
    return (operation_type or "").strip().lower()


def is_per_hour_operation(operation_type: str | None) -> bool:
    return normalize_operation_type(operation_type) in PER_HOUR_OPERATION_TYPES


def charge_unit_for(operation_type: str | None) -> str:
    """Return the billing unit an operation is charged in."""
    return CHARGE_UNIT_PER_HOUR if is_per_hour_operation(operation_type) else CHARGE_UNIT_PER_HA


def unit_suffix(charge_unit: str | None) -> str:
    """``'ha'`` / ``'hr'`` for display; falls back to hectares for legacy NULL rows."""
    return UNIT_SUFFIX.get(charge_unit or CHARGE_UNIT_PER_HA, "ha")


def rate_label(charge_unit: str | None) -> str:
    """Column heading for the applied rate in exports."""
    if charge_unit == CHARGE_UNIT_PER_HOUR:
        return "Charge / hour (INR)"
    return "Charge / ha (INR)"
