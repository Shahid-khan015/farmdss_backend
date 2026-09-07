from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.constants import (
    FI_TO_SOIL_CONDITION_BOUNDS,
    MU_THRESHOLD_BY_SOIL_CONDITION,
    PTO_POWER_MIN_KW,
    TABLE_4_2_KWEF_MIN,
    TABLE_4_2_PUT_LIMIT_PCT,
    TABLE_4_2_SLIP_LIMIT_PCT,
    TABLE_4_2_SLIP_UNDERUTILIZED_PCT,
)

# Table 4.2's message text, verbatim. Two conditions deliberately share one string.
_MSG_BALLAST_REAR = "Reduce depth or speed of operation or ballast rear axle of tractor"
_MSG_BALLAST_FRONT = "Reduce depth or speed of operation or ballast front axle of tractor"
_MSG_REDUCE = "Reduce depth or speed of operation"
# Not from Table 4.2 -- see TABLE_4_2_SLIP_UNDERUTILIZED_PCT. Worded distinctly from
# the four DSS-EXACT messages above so it is never mistaken for spec text.
_MSG_SLIP_UNDERUTILIZED = (
    "Slip is below 8% -- increase depth or speed of operation to make better use of "
    "available traction"
)


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
    RangeCheck("implement_width", None, 0.2, 5.0, "m"),
)


def validate_operating_ranges(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    checks = (
        RangeCheck("speed", _to_float(inputs.get("speed")), 2.0, 8.0, "km/h"),
        RangeCheck("depth", _to_float(inputs.get("depth")), 5.0, 35.0, "cm"),
        RangeCheck("cone_index", _to_float(inputs.get("cone_index")), 300.0, 3000.0, "kPa"),
        # Was 0.5 m, which rejected the reference's own canonical implements:
        # Excel/HTML's "MB Plough single bottom" (0.3 m, the workbook's shipped
        # default example) and "Disc Plough single disc" (0.45 m). Neither Excel
        # nor either HTML reference imposes any implement-width floor at all;
        # 0.2 m keeps a floor against nonsense input while clearing both.
        RangeCheck("implement_width", _to_float(inputs.get("implement_width")), 0.2, 5.0, "m"),
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

    # The floor exists to reject missing/nonsense power, not to exclude small
    # tractors. It sat at 10 kW, which rejected the catalogue's own 6.6 kW
    # Captain DI 2600 outright -- every simulation against it 422'd. Indian
    # power tillers start around 5 kW, so that is the defensible bound.
    pto_power = _to_float(inputs.get("pto_power"))
    if pto_power is None or pto_power < PTO_POWER_MIN_KW:
        errors.append(
            {
                "field": "pto_power",
                "code": "out_of_range",
                "message": "pto_power must be at least {0:g} kW.".format(PTO_POWER_MIN_KW),
                "value": pto_power,
                "range": {"min": PTO_POWER_MIN_KW, "unit": "kW"},
            }
        )
    return errors


def soil_condition_from_fi(fi: float | None) -> str | None:
    """Map Eq. 3.1's soil *texture* factor onto Table 4.2's soil *condition*.

    [INTERPRETIVE MAPPING -- not specification.] The DSS document indexes the mu
    ceiling by bearing strength (soft / medium / firm-hard) and Fi by texture
    (fine / medium / coarse), and gives no crosswalk between them. See the note on
    `constants.FI_TO_SOIL_CONDITION_BOUNDS` for why the texture the operator has
    already chosen is reused rather than asking for a second soil classification.
    """
    if fi is None:
        return None
    for threshold, condition in FI_TO_SOIL_CONDITION_BOUNDS:
        if fi >= threshold:
            return condition
    return "soft"


def build_recommendations(
    *,
    slip: float | None,
    net_traction_coefficient: float | None,
    front_weight_utilization: float | None,
    power_utilization: float | None,
    fi: float | None,
) -> list[str]:
    """DSS Table 4.2, "Checking conditions and corresponding messages" [DSS-EXACT],
    plus one additive, clearly-labeled legacy condition.

    Four conditions are the document's own, each with its literal message text. A
    condition whose input is `None` is skipped, not failed -- a standalone active
    implement has no slip, mu or Kwef, and only the Put rule applies to it.

    **This replaced an ad hoc rule set** that fired on `power_utilization > 90`
    (the document says 100), plus tractive-efficiency < 60 and fuel > 45 L/ha
    checks that appear nowhere in the specification at all. Those two are gone;
    do not reintroduce them without a source.

    A fifth condition -- slip below `TABLE_4_2_SLIP_UNDERUTILIZED_PCT` -- is not part
    of Table 4.2. It is carried over from the 2006 VB6 tool this DSS derives from,
    which flags under-utilized traction with its own message. Table 4.2 is silent on
    low slip, not opposed to flagging it, so this is purely additive; see the constant's
    docstring for the source. It is independent of the other four (`fi` and Kwef are
    unrelated to it), so it can fire alongside or instead of them.

    Returns `[]` when nothing fires -- the absence of advice is itself the answer,
    and the previous default string ("Operate within the recommended slip and
    power ranges") was advice the document never gives.
    """
    recommendations: list[str] = []

    if slip is not None and slip > TABLE_4_2_SLIP_LIMIT_PCT:
        recommendations.append(_MSG_BALLAST_REAR)

    if slip is not None and slip < TABLE_4_2_SLIP_UNDERUTILIZED_PCT:
        recommendations.append(_MSG_SLIP_UNDERUTILIZED)

    condition = soil_condition_from_fi(fi)
    if net_traction_coefficient is not None and condition is not None:
        if net_traction_coefficient > MU_THRESHOLD_BY_SOIL_CONDITION[condition]:
            recommendations.append(_MSG_BALLAST_REAR)

    if front_weight_utilization is not None and front_weight_utilization < TABLE_4_2_KWEF_MIN:
        recommendations.append(_MSG_BALLAST_FRONT)

    if power_utilization is not None and power_utilization > TABLE_4_2_PUT_LIMIT_PCT:
        recommendations.append(_MSG_REDUCE)

    deduped: list[str] = []
    for item in recommendations:
        if item not in deduped:
            deduped.append(item)
    return deduped


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
