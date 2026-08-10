"""Primitives shared by all three DSS simulation modes.

The DSS document derives the single-implement case (Section 3) in full and then
defines the passive-passive (Section 4) and active-passive (Section 5) cases as
*substitutions* into that same chain -- `DTotal` or `Deff` in place of `D`. This
module holds the parts of the chain that are genuinely identical across the three
modes, so that a change to a DSS equation is made in exactly one place.

Layering: this module must NOT import from `legacy_algorithms` or
`combi_algorithms` -- they import from it. The wheel-numeric-dependent helpers
(`wheel_response`) therefore live in `legacy_algorithms` alongside the wheel
models themselves.

Formula provenance is tagged in each docstring using the audit vocabulary of
`docs/SIMULATION_ENGINE_FORMULAS.md`:
DSS-EXACT | DSS-AMBIGUOUS | IMPLEMENTATION-ASSUMPTION | LEGACY | EXTERNAL-MODEL.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.core.constants import (
    DIESEL_CALORIFIC_VALUE,
    DRAFT_DEPTH_ACTION_FRACTION,
    FIELD_EFFICIENCY_CLAMP,
    FUEL_L_PER_HA_CLAMP,
    OVERALL_EFFICIENCY_CLAMP,
    PUT_PROPERLY_LOADED_RANGE,
    SFC_COEFF_A,
    SFC_COEFF_B,
    SFC_COEFF_C,
    SFC_RADICAND_COEFF,
    SFC_RADICAND_OFFSET,
    TURNING_TIME_CLAMP,
    TURNING_TIME_COEFF_CONST,
    TURNING_TIME_COEFF_SPEED,
    TURNING_TIME_COEFF_WIDTH_OVER_SPEED,
    WHEEL_ECCENTRICITY_COEFF,
)
from app.core.engineering_validation import (
    build_recommendations,
    clamp,
    derive_confidence,
    derive_simulation_status,
)

__all__ = [
    "require_finite",
    "require_positive",
    "safe_div",
    "safe_sqrt",
    "draft_force_n",
    "GeometryTerms",
    "geometry_terms",
    "FieldCapacity",
    "field_capacity",
    "specific_fuel_consumption_l_per_kwh",
    "PowerFuel",
    "power_and_fuel",
    "put_load_status",
    "ResultEnvelope",
    "result_envelope",
]


# --- Numerical safety -------------------------------------------------------
#
# Every guard raises a ValueError naming the offending quantity and its value.
# None of them substitutes a fallback value: a simulation that cannot be
# computed must fail visibly rather than report a fabricated number.


def require_finite(name: str, value: float) -> float:
    """Reject NaN/Infinity, which would otherwise propagate silently to the API."""
    if not math.isfinite(value):
        raise ValueError("Invalid {0}: expected a finite number, got {1!r}".format(name, value))
    return value


def require_positive(name: str, value: float) -> float:
    """Reject non-positive values for quantities that are physically positive-definite."""
    require_finite(name, value)
    if value <= 0:
        raise ValueError("Invalid {0}: must be greater than zero, got {1!r}".format(name, value))
    return value


def safe_div(name: str, numerator: float, denominator: float) -> float:
    """Division that names what was being computed when the denominator vanished."""
    require_finite("{0} numerator".format(name), numerator)
    require_finite("{0} denominator".format(name), denominator)
    if denominator == 0:
        raise ValueError("Cannot compute {0}: division by zero".format(name))
    return require_finite(name, numerator / denominator)


def safe_sqrt(name: str, value: float) -> float:
    """Square root that reports a negative radicand instead of raising a bare ValueError."""
    require_finite(name, value)
    if value < 0:
        raise ValueError("Cannot compute {0}: square root of a negative value ({1!r})".format(name, value))
    return math.sqrt(value)


# --- Draft (DSS Eq. 3.1) ----------------------------------------------------


def draft_force_n(
    *,
    fi: float,
    asae_param_a: float,
    asae_param_b: float,
    asae_param_c: float,
    speed_kmh: float,
    width_m: float,
    depth_cm: float,
) -> float:
    """Implement draft force D, N -- DSS Eq. 3.1 [DSS-EXACT]:

        D = F * (A + B*S + C*S^2) * W * (T/10)

    Units: `S` in km/h, `W` in m, `T` in cm; `F` is the dimensionless soil-texture
    factor. The `/10` is a real denominator in the source equation (verified in
    the document's OMML markup), not a unit conversion added here.

    This is the single transcription of Eq. 3.1 in the engine; the single-tool,
    passive-passive and active-passive paths all call it.
    """
    require_positive("implement width", width_m)
    require_positive("operating speed", speed_kmh)
    require_positive("tillage depth", depth_cm)
    return require_finite(
        "draft force",
        fi
        * (asae_param_a + asae_param_b * speed_kmh + asae_param_c * (speed_kmh**2))
        * width_m
        * (depth_cm / 10.0),
    )


# --- Geometry ---------------------------------------------------------------


@dataclass(frozen=True)
class GeometryTerms:
    yd_m: float
    er_m: float
    ef_m: float


def geometry_terms(
    *,
    depth_cm: float,
    rear_rolling_radius_m: float,
    front_rolling_radius_m: float,
) -> GeometryTerms:
    """Depth at which draft acts and the two wheel eccentricities.

    Yd = (2/3) * Td, in m               [DSS-EXACT, document's "assumed" value]
    er = 0.1 * rr                       [DSS-EXACT, Liljedahl et al. 1996]
    ef = 0.1 * rf                       [IMPLEMENTATION-ASSUMPTION: the document
                                         states 0.1 only for er; applied to ef by
                                         symmetry]
    """
    require_positive("tillage depth", depth_cm)
    require_positive("rear rolling radius", rear_rolling_radius_m)
    require_positive("front rolling radius", front_rolling_radius_m)
    return GeometryTerms(
        yd_m=DRAFT_DEPTH_ACTION_FRACTION * (depth_cm / 100.0),
        er_m=WHEEL_ECCENTRICITY_COEFF * rear_rolling_radius_m,
        ef_m=WHEEL_ECCENTRICITY_COEFF * front_rolling_radius_m,
    )


# --- Field capacity ---------------------------------------------------------


@dataclass(frozen=True)
class FieldCapacity:
    fc_th: float
    fc_ac: float
    field_eff_pct: float
    field_eff_raw_pct: float
    turning_time_s: float
    number_turns: int
    total_time_h: float


def field_capacity(
    *,
    speed_kmh: float,
    width_m: float,
    field_area_ha: float,
    field_width_m: float,
) -> FieldCapacity:
    """Theoretical/actual field capacity and field efficiency.

    FCth = S * W / 10, ha/h                                        [DSS-EXACT]

    Everything below it -- turning time, number of turns, the derivation of
    actual capacity from total operating time, and the 50-95% clamp on field
    efficiency -- is [LEGACY]: it is absent from the DSS document and is
    preserved unchanged from the pre-existing implementation rather than
    invented or "corrected".

    `field_eff_raw_pct` exposes the unclamped ratio, because the clamp can make
    the reported efficiency inconsistent with the reported capacities.
    """
    require_positive("operating speed", speed_kmh)
    require_positive("implement width", width_m)
    require_positive("field area", field_area_ha)

    fc_th = speed_kmh * width_m / 10.0
    if fc_th <= 0:
        raise ValueError("Theoretical field capacity is non-positive")

    turning_time_s = clamp(
        TURNING_TIME_COEFF_CONST
        + TURNING_TIME_COEFF_WIDTH_OVER_SPEED * (width_m / speed_kmh)
        - TURNING_TIME_COEFF_SPEED * speed_kmh,
        *TURNING_TIME_CLAMP,
    )
    number_turns = max(0, int(round(field_width_m / width_m)))
    total_turning_time_h = (turning_time_s * 2.0 * number_turns) / 3600.0
    theoretical_time_h = field_area_ha / fc_th
    total_time_h = total_turning_time_h + theoretical_time_h
    if total_time_h <= 0:
        raise ValueError("Total operating time is non-positive")

    fc_ac = field_area_ha / total_time_h
    field_eff_raw_pct = (fc_ac / fc_th) * 100.0
    return FieldCapacity(
        fc_th=fc_th,
        fc_ac=fc_ac,
        field_eff_pct=clamp(field_eff_raw_pct, *FIELD_EFFICIENCY_CLAMP),
        field_eff_raw_pct=field_eff_raw_pct,
        turning_time_s=turning_time_s,
        number_turns=number_turns,
        total_time_h=total_time_h,
    )


# --- Power and fuel ---------------------------------------------------------


def specific_fuel_consumption_l_per_kwh(pto_power_fraction: float) -> float:
    """ASABE (2001) specific fuel consumption, L/kW-h -- DSS `image30` [DSS-EXACT]:

        SFC = 2.64X + 3.91 - 0.203*sqrt(738X + 173)

    `X` is the ratio of required PTO power to rated PTO power (Xeff for the
    active-passive case, which adds the rotor's own PTO draw).
    """
    x = max(0.0, require_finite("PTO power fraction", pto_power_fraction))
    return (SFC_COEFF_A * x + SFC_COEFF_B) - (
        SFC_COEFF_C * safe_sqrt("specific fuel consumption", SFC_RADICAND_COEFF * x + SFC_RADICAND_OFFSET)
    )


@dataclass(frozen=True)
class PowerFuel:
    pdb_kw: float
    ptr_kw: float
    put_pct: float
    x_fraction: float
    sfc: float
    fuel_lph: float
    fuel_lph_pto_basis: float
    fuel_l_per_ha: float
    overall_pct: float


def power_and_fuel(
    *,
    draft_n: float,
    speed_kmh: float,
    te_pct: float,
    transmission_efficiency_pct: float,
    power_reserve_pct: float,
    pto_power_kw: float,
    fc_th: float,
    fc_ac: float,
    extra_pto_kw: float = 0.0,
) -> PowerFuel:
    """Drawbar power, required PTO power, power utilization and fuel consumption.

    DBp  = D * S                            (Eq. 3.4)          [DSS-EXACT]
    Ptr  = DBp / (TE * eta_t)               (Eq. 3.11)         [DSS-EXACT]
    Put  = (Ptr + PPTO) / (Pt(1-fs)) * 100  (Eq. 3.12 / 5.14)  [DSS-EXACT]
    X    = (Ptr + PPTO) / Pt                (Eq. 5.15)         [DSS-EXACT]
    SFC  = ASABE (2001)                                        [DSS-EXACT]

    `extra_pto_kw` is the rotor's own PTO draw: 0 for the single-implement and
    passive-passive modes, PPTO for active-passive. With it at 0 the Put and X
    expressions collapse to Eq. 3.12's `Ptr/(Pt(1-fs))` and `Ptr/Pt` -- which is
    exactly how the document derives Section 4 from Section 5 ("Section 4's
    equations are the special case of Section 5's obtained by setting PPTO = 0").

    Fuel basis [DSS-AMBIGUOUS / LEGACY]: the document gives SFC in L/kW-h and
    stops there -- it never converts to L/h or L/ha, so there is no DSS-intended
    multiplicand to recover. `fuel_lph = SFC * DBp` is preserved from the
    pre-existing implementation and drives every reported fuel figure.
    `fuel_lph_pto_basis = SFC * (Ptr + PPTO)` -- the reading that is
    dimensionally consistent with X -- is computed alongside it as a diagnostic
    and deliberately feeds nothing.
    """
    require_positive("rated PTO power", pto_power_kw)
    require_positive("operating speed", speed_kmh)
    require_positive("theoretical field capacity", fc_th)
    require_positive("actual field capacity", fc_ac)
    require_positive("tractive efficiency", te_pct)

    trans_eff_frac = require_positive("transmission efficiency", transmission_efficiency_pct / 100.0)
    power_reserve_frac = power_reserve_pct / 100.0
    if power_reserve_frac >= 1.0:
        raise ValueError(
            "Invalid power reserve: must be below 100%, got {0!r}%".format(power_reserve_pct)
        )

    pdb_kw = draft_n * speed_kmh / 3.6 / 1000.0
    ptr_kw = safe_div("required PTO power", pdb_kw, te_pct / 100.0 * trans_eff_frac)

    total_pto_kw = ptr_kw + extra_pto_kw
    put_pct = safe_div("power utilization", total_pto_kw, pto_power_kw * (1.0 - power_reserve_frac)) * 100.0
    x_fraction = safe_div("PTO power fraction", total_pto_kw, pto_power_kw)

    sfc = specific_fuel_consumption_l_per_kwh(x_fraction)
    fuel_lph = sfc * pdb_kw
    fuel_lph_pto_basis = sfc * total_pto_kw
    fuel_l_per_ha = clamp(safe_div("fuel consumption per hectare", fuel_lph, fc_ac), *FUEL_L_PER_HA_CLAMP)

    if fuel_l_per_ha > 0:
        overall_pct = clamp(
            pdb_kw * 3600.0 / 1000.0 / (fc_th * fuel_l_per_ha * DIESEL_CALORIFIC_VALUE) * 100.0,
            *OVERALL_EFFICIENCY_CLAMP,
        )
    else:
        # No fuel burnt means no useful-work ratio to report; 0 matches the
        # lower clamp bound rather than inventing a value.
        overall_pct = 0.0

    return PowerFuel(
        pdb_kw=pdb_kw,
        ptr_kw=ptr_kw,
        put_pct=put_pct,
        x_fraction=x_fraction,
        sfc=sfc,
        fuel_lph=fuel_lph,
        fuel_lph_pto_basis=fuel_lph_pto_basis,
        fuel_l_per_ha=fuel_l_per_ha,
        overall_pct=overall_pct,
    )


def put_load_status(power_utilization_pct: float) -> str:
    """DSS "Check Put value" table [DSS-EXACT]:

    95-100% => properly loaded, <95% => underloaded, >100% => overloaded.
    """
    low, high = PUT_PROPERLY_LOADED_RANGE
    if low <= power_utilization_pct <= high:
        return "Tractor is properly loaded"
    if power_utilization_pct < low:
        return "Tractor is Underloaded"
    return "Tractor is Overloaded"


# --- Result envelope --------------------------------------------------------


@dataclass(frozen=True)
class ResultEnvelope:
    load_status: str
    recommendations: str
    recommendation_messages: list
    status: str
    status_message: str
    confidence: str


def result_envelope(
    *,
    slip: float,
    draft_n: float,
    te_pct: float,
    fuel_l_per_ha: float,
    put_pct: float,
    field_eff_pct: float,
    converged: bool,
) -> ResultEnvelope:
    """Status / recommendation / confidence tail shared by all three modes.

    [LEGACY] -- the thresholds live in `engineering_validation` and are advisory
    UI text, not part of the DSS derivation.
    """
    load_status = put_load_status(put_pct)
    recommendation_items = build_recommendations(
        slip=slip,
        draft_force=draft_n,
        traction_efficiency=te_pct,
        fuel_consumption=fuel_l_per_ha,
        power_utilization=put_pct,
    )
    recommendation = "; ".join(recommendation_items)
    simulation_status = derive_simulation_status(
        slip=slip,
        power_utilization=put_pct,
        field_efficiency=field_eff_pct,
        converged=converged,
    )
    return ResultEnvelope(
        load_status=load_status,
        recommendations=recommendation,
        recommendation_messages=recommendation.split("; "),
        status=simulation_status,
        status_message=load_status if converged else simulation_status,
        confidence=derive_confidence(compatible=True, converged=converged, slip=slip),
    )
