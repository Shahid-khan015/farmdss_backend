from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class RangeCheck:
    field: str
    value: float | None
    minimum: float
    maximum: float
    unit: str


OPERATING_RANGE_CHECKS = (
    RangeCheck("speed", None, 2.0, 8.0, "km/h"),
    RangeCheck("depth", None, 5.0, 35.0, "cm"),
    RangeCheck("cone_index", None, 300.0, 3000.0, "kPa"),
    RangeCheck("implement_width", None, 0.5, 5.0, "m"),
)


def validate_operating_ranges(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    checks = (
        RangeCheck("speed", _to_float(inputs.get("speed")), 2.0, 8.0, "km/h"),
        RangeCheck("depth", _to_float(inputs.get("depth")), 5.0, 35.0, "cm"),
        RangeCheck("cone_index", _to_float(inputs.get("cone_index")), 300.0, 3000.0, "kPa"),
        RangeCheck("implement_width", _to_float(inputs.get("implement_width")), 0.5, 5.0, "m"),
    )
    errors: list[dict[str, Any]] = []
    for check in checks:
        if check.value is None:
            errors.append(
                {
                    "field": check.field,
                    "code": "required",
                    "message": f"{check.field} is required for simulation.",
                    "range": {"min": check.minimum, "max": check.maximum, "unit": check.unit},
                }
            )
            continue
        if check.value < check.minimum or check.value > check.maximum:
            errors.append(
                {
                    "field": check.field,
                    "code": "out_of_range",
                    "message": (
                        f"{check.field} must be between {check.minimum:g} and "
                        f"{check.maximum:g} {check.unit}."
                    ),
                    "value": check.value,
                    "range": {"min": check.minimum, "max": check.maximum, "unit": check.unit},
                }
            )

    pto_power = _to_float(inputs.get("pto_power"))
    if pto_power is None or pto_power <= 10.0:
        errors.append(
            {
                "field": "pto_power",
                "code": "out_of_range",
                "message": "pto_power must be greater than 10 kW.",
                "value": pto_power,
                "range": {"min": 10.0, "exclusive_min": True, "unit": "kW"},
            }
        )
    return errors


def build_recommendations(
    *,
    slip: float | None,
    draft_force: float | None,
    traction_efficiency: float | None,
    fuel_consumption: float | None,
    power_utilization: float | None,
) -> list[str]:
    recommendations: list[str] = []
    if slip is not None and slip > 15.0:
        recommendations.extend(["Reduce operating depth", "Add ballast"])
    if power_utilization is not None and power_utilization > 90.0:
        recommendations.extend(["Reduce implement width", "Increase tractor HP"])
    if traction_efficiency is not None and traction_efficiency < 60.0:
        recommendations.append("Reduce operating speed")
    if fuel_consumption is not None and fuel_consumption > 45.0:
        recommendations.append("Reduce operating depth")
    if draft_force is not None and power_utilization is not None and power_utilization > 85.0:
        recommendations.append("Use a narrower or lighter implement")

    deduped: list[str] = []
    for item in recommendations:
        if item not in deduped:
            deduped.append(item)
    return deduped or ["Operate within the recommended slip and power ranges"]


def derive_simulation_status(
    *,
    slip: float | None,
    power_utilization: float | None,
    field_efficiency: float | None,
    compatible: bool = True,
    converged: bool = True,
) -> str:
    if not compatible:
        return "Not Recommended"
    if not converged or (slip is not None and slip >= 20.0):
        return "Unstable"
    if (
        (slip is not None and slip > 15.0)
        or (power_utilization is not None and power_utilization > 85.0)
        or (field_efficiency is not None and field_efficiency < 65.0)
    ):
        return "Heavy Load"
    return "Stable"


def derive_confidence(
    *,
    validation_errors: list[dict[str, Any]] | None = None,
    compatible: bool = True,
    converged: bool = True,
    slip: float | None = None,
) -> str:
    if validation_errors or not compatible or not converged or (slip is not None and slip >= 20.0):
        return "Low"
    if slip is not None and (slip < 8.0 or slip > 15.0):
        return "Moderate"
    return "High"


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None
