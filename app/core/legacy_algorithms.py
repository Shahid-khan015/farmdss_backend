"""Single-implement tractor-tillage performance engine.

Implements Section 3 ("When single conventional tillage implement is used") of the
DSS specification document (docs/Simulation for DSS _Sahid.docx) exactly: draft
prediction, tractor dynamic axle loads, mobility number / traction-coefficient /
slip iteration, ballast requirements, power utilization and fuel consumption.

Each formula is implemented as an independent, pure, unit-testable function; the
`calculate_legacy_performance` orchestrator wires them together in the same order
as the DSS document's derivation. Two sub-formulas are NOT specified anywhere in
the DSS document excerpt (theoretical/actual field capacity, turning time, field
efficiency) and are therefore preserved unchanged from the pre-existing
implementation rather than invented.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Callable, Optional

from app.core.constants import (
    BALLAST_SOLVER_MAX_ITERATIONS,
    BALLAST_SOLVER_TOLERANCE,
    FI_FACTOR_BY_IMPLEMENT_AND_TEXTURE,
    FRONT_BALLAST_TARGET_KWEF,
    GRAVITY,
    MAX_SLIP_ITERATIONS,
    MAX_SLIP_PCT,
    PY_OVER_D_RATIO_BY_IMPLEMENT,
    REAR_BALLAST_TARGET_SLIP_PCT,
    ROLLING_RESISTANCE_BASE,
    ROLLING_RESISTANCE_SLIP_COEFF,
    SLIP_INCREMENT_PCT,
    SLIP_INITIAL_PCT,
    TRACTION_BN_EXPONENT_COEFF,
    TRACTION_MU_G_SCALE,
    TRACTION_SLIP_EXPONENT_COEFF,
)
from app.core.dss_shared import (
    draft_force_n,
    field_capacity,
    geometry_terms,
    power_and_fuel,
    require_positive,
    result_envelope,
    safe_div,
    safe_sqrt,
)

# Re-exported for backward compatibility: these moved to dss_shared during the
# de-duplication refactor, but callers and tests still import them from here.
from app.core.dss_shared import (  # noqa: F401
    put_load_status,
    specific_fuel_consumption_l_per_kwh,
)
from app.core.engineering_validation import clamp
from app.models.enums import ImplementType, SoilTexture

logger = logging.getLogger(__name__)

# Backward-compatible aliases (some callers/tests reference the old names).
MAX_SLIP = MAX_SLIP_PCT
MAX_ITERATIONS = MAX_SLIP_ITERATIONS

# Signature of a wheel-numeric (mobility number) model:
#   (cone_index_kPa, section_width_m, overall_diameter_m, wheel_load_N) -> Bn
# Lets the traction/ballast solvers stay model-agnostic so the single-tool
# Section 3 `Bn` and the passive-passive Section 4 `Bn'` remain separate
# implementations rather than one being silently reused for the other.
MobilityNumberFn = Callable[[float, float, float, float], float]


@dataclass(frozen=True)
class LegacyInputs:
    # Tractor
    pto_power_kw: float
    wheelbase_m: float
    front_axle_weight_kg: float
    rear_axle_weight_kg: float
    hitch_distance_from_rear_m: float
    cg_distance_from_rear_m: float
    transmission_efficiency_pct: float
    power_reserve_pct: float

    # Tire (legacy model uses both front and rear values)
    front_rolling_radius_m: float
    rear_rolling_radius_m: float
    front_overall_diameter_m: float
    rear_overall_diameter_m: float
    front_section_width_m: float
    rear_section_width_m: float

    # Implement
    implement_type: ImplementType
    width_m: float
    weight_kg: float
    cg_distance_from_hitch_m: float
    vertical_horizontal_ratio: float
    asae_param_a: float
    asae_param_b: float
    asae_param_c: float

    # Operating conditions
    soil_texture: SoilTexture
    cone_index_kpa: float
    depth_cm: float
    speed_kmh: float
    field_area_ha: float
    field_width_m: float

    # Optional: enables the DSS Eq. 3.4 engine-torque pull limit (Pet) diagnostic.
    max_engine_torque_nm: Optional[float] = None


def _passive_only_lookup_error(implement_type: ImplementType, table_name: str) -> ValueError:
    """Both tables are defined only for passive tools -- see DSS Eq. 3.1 / Section 3.3.

    Raised as ValueError (not KeyError) so the API surfaces an actionable 422
    rather than a 500. Reaching here means slot validation was bypassed.
    """
    return ValueError(
        f"No {table_name} entry for implement type "
        f"'{getattr(implement_type, 'value', implement_type)}'. The DSS passive-draft model is "
        "defined only for unpowered tools; PTO-powered implements must be used as the rotor of "
        "an active-passive combination, where draft comes from the rotor sub-model instead."
    )


def fi_factor(implement_type: ImplementType, soil_texture: SoilTexture) -> float:
    """Dimensionless soil-texture adjustment parameter F (DSS Eq. 3.1).

    [LEGACY] The document names the texture classes but gives no numeric table;
    see constants.FI_FACTOR_BY_IMPLEMENT_AND_TEXTURE.
    """
    try:
        by_texture = FI_FACTOR_BY_IMPLEMENT_AND_TEXTURE[implement_type.value]
    except KeyError:
        raise _passive_only_lookup_error(implement_type, "Fi soil-texture factor")
    try:
        return by_texture[soil_texture.value]
    except KeyError:
        raise ValueError(
            f"No Fi factor for soil texture '{getattr(soil_texture, 'value', soil_texture)}' "
            f"with implement type '{implement_type.value}'."
        )


def py_over_d_ratio(implement_type: ImplementType) -> float:
    """Vertical:horizontal soil-reaction ratio Py/D (DSS Section 3.3, Kepner et al. 1978)."""
    try:
        return PY_OVER_D_RATIO_BY_IMPLEMENT[implement_type.value]
    except KeyError:
        raise _passive_only_lookup_error(implement_type, "Py/D ratio")


def estimate_draft_force(inputs: LegacyInputs) -> float:
    """Implement draft force D, N (DSS Eq. 3.1): D = F*(A + B*S + C*S^2)*W*(T/10).

    Thin binding of `LegacyInputs` onto the single Eq. 3.1 kernel in
    `dss_shared.draft_force_n`, which all three modes share.
    """
    return draft_force_n(
        fi=fi_factor(inputs.implement_type, inputs.soil_texture),
        asae_param_a=inputs.asae_param_a,
        asae_param_b=inputs.asae_param_b,
        asae_param_c=inputs.asae_param_c,
        speed_kmh=inputs.speed_kmh,
        width_m=inputs.width_m,
        depth_cm=inputs.depth_cm,
    )


def dynamic_axle_loads(
    *,
    draft_n: float,
    depth_cm: float,
    wheelbase_m: float,
    hitch_distance_from_rear_m: float,
    cg_distance_from_rear_m: float,
    cg_distance_from_hitch_m: float,
    rear_rolling_radius_m: float,
    front_rolling_radius_m: float,
    tractor_weight_n: float,
    implement_weight_n: float,
    py_n: float,
) -> tuple[float, float]:
    """Dynamic rear/front axle loads Rr, Rf (DSS Eq. 3.5, 3.6).

    Rr = [(Wm+Py)(Xcgi+Hd+L+ef) + Wt(L+ef-Xcgt) - D*Yd] / (L - er + ef)
    Rf = (Wt + Wm + Py) - Rr
    """
    geometry = geometry_terms(
        depth_cm=depth_cm,
        rear_rolling_radius_m=rear_rolling_radius_m,
        front_rolling_radius_m=front_rolling_radius_m,
    )
    er, ef, yd = geometry.er_m, geometry.ef_m, geometry.yd_m

    numerator = (
        (implement_weight_n + py_n) * (cg_distance_from_hitch_m + hitch_distance_from_rear_m + wheelbase_m + ef)
        + tractor_weight_n * (wheelbase_m + ef - cg_distance_from_rear_m)
        - draft_n * yd
    )
    denominator = wheelbase_m - er + ef
    if denominator == 0:
        raise ValueError("Invalid geometry: wheelbase - er + ef equals zero")

    rr = numerator / denominator
    rf = (tractor_weight_n + implement_weight_n + py_n) - rr
    return rr, rf


def mobility_number(ci_kpa: float, section_width_m: float, overall_diameter_m: float, wheel_load_n: float) -> float:
    """Wheel numeric / mobility number Bn -- DSS Section 3 [DSS-EXACT]:

        Bn = CI * b * d / Wd

    where `Wd` is the dynamic vertical load on the *tire* (not the axle).

    Units: since 1 kPa == 1 kN/m^2, CI[kPa]*b[m]*d[m] has units of kN, so `Wd`
    must be in kN for Bn to be dimensionless; `wheel_load_n` (Newtons) is
    converted here. No shape factor, contact-patch correction or other
    multiplier appears in the source equation, and none is applied.

    Callers pass the per-wheel load W = axle load / 2 (equal wheel loading) --
    that halving is an [IMPLEMENTATION-ASSUMPTION]: the document specifies a
    per-tire load without saying how to split the axle.

    This is the Section 3 model. It is used for the driven rear wheel in the
    single-implement and active-passive modes, and for the *undriven front
    wheel* in all three modes (DSS defines rho_f from this Bn with the front
    load and front tire). The Section 4 `Bn'` is a separate function --
    `combi_algorithms.mobility_number_passive_passive` -- and the two are never
    interchanged.
    """
    require_positive("mobility number tire section width", section_width_m)
    require_positive("mobility number tire overall diameter", overall_diameter_m)
    require_positive("mobility number cone index", ci_kpa)
    if wheel_load_n <= 0:
        raise ValueError("Invalid mobility number: wheel load must be positive")
    wheel_load_kn = wheel_load_n / 1000.0
    return safe_div(
        "mobility number", ci_kpa * section_width_m * overall_diameter_m, wheel_load_kn
    )


def rolling_resistance_rear(bn_rear: float, slip_fraction: float) -> float:
    """Rear rolling-resistance coefficient rho_r [DSS-EXACT]:

        rho_r = 1/Bn + 0.04 + 0.5*s/sqrt(Bn)
    """
    require_positive("rear wheel numeric", bn_rear)
    return (
        safe_div("rear rolling resistance", 1.0, bn_rear)
        + ROLLING_RESISTANCE_BASE
        + (ROLLING_RESISTANCE_SLIP_COEFF * slip_fraction) / safe_sqrt("rear wheel numeric", bn_rear)
    )


def rolling_resistance_front(bn_front: float) -> float:
    """Front rolling-resistance coefficient rho_f [DSS-EXACT]:

        rho_f = 1/Bn + 0.04

    No slip term: the front wheel is undriven. `Bn` here is the Section 3 wheel
    numeric evaluated with the *front* cone index, tire width, tire diameter and
    front dynamic load, exactly as the document specifies.
    """
    require_positive("front wheel numeric", bn_front)
    return safe_div("front rolling resistance", 1.0, bn_front) + ROLLING_RESISTANCE_BASE


def gross_traction_ratio(bn_rear: float) -> float:
    """mu_g = 0.88 * (1 - exp(-0.1*Bn)) [DSS-EXACT]."""
    require_positive("rear wheel numeric", bn_rear)
    return TRACTION_MU_G_SCALE * (1.0 - math.exp(-TRACTION_BN_EXPONENT_COEFF * bn_rear))


def net_traction_coefficient(
    bn_rear: float, slip_fraction: float, mu_g: Optional[float] = None
) -> float:
    """Coefficient of traction mu at trial slip S (DSS Eq. 3.9's mu' generalized to any S):

    mu = mu_g*(1 - exp(-TRACTION_SLIP_EXPONENT_COEFF*S)) - 1/Bn - 0.5*S/sqrt(Bn)

    See constants.TRACTION_SLIP_EXPONENT_COEFF for why this uses 7.5, not the
    document's literal (but physically implausible) 0.3.
    """
    require_positive("rear wheel numeric", bn_rear)
    # `mu_g` depends only on Bn. Callers iterating slip at fixed Bn can pass it in
    # to keep the exponential out of the loop; the value is identical either way.
    if mu_g is None:
        mu_g = gross_traction_ratio(bn_rear)
    return (
        mu_g * (1.0 - math.exp(-TRACTION_SLIP_EXPONENT_COEFF * slip_fraction))
        - (1.0 / bn_rear)
        - (ROLLING_RESISTANCE_SLIP_COEFF * slip_fraction) / safe_sqrt("rear wheel numeric", bn_rear)
    )


def traction_efficiency_percent(mu: float, mu_g: float, slip_fraction: float) -> float:
    """TE = mu*(1-S) / mu_g (DSS Eq. 3.2), as a percentage."""
    if mu_g == 0:
        raise ValueError("Gross traction ratio is zero")
    return (mu * (1.0 - slip_fraction) / mu_g) * 100.0


@dataclass(frozen=True)
class WheelResponse:
    """Front wheel numeric and the two rolling-resistance coefficients."""

    bn_front: float
    rho_r: float
    rho_f: float
    mr_ratio: float


def wheel_response(
    *,
    ci_kpa: float,
    front_section_width_m: float,
    front_overall_diameter_m: float,
    front_axle_load_n: float,
    bn_rear: float,
    slip_fraction: float,
) -> WheelResponse:
    """Motion-resistance block shared by all three modes.

    `bn_rear` is supplied already computed, by whichever *driven-wheel* model the
    mode selected (Section 3 `Bn`, or Section 4 `Bn'` for passive-passive). The
    front wheel numeric is always the Section 3 `mobility_number`, per DSS
    `image8`, which defines rho_f from `Bn = CI*b*d/Wf`.

    This function deliberately takes no wheel-numeric callable: the front wheel
    is undriven and outside the scope of Section 4's `Bn'`, and hard-wiring the
    front model here is what prevents `Bn'` from leaking onto it again.
    """
    bn_front = mobility_number(
        ci_kpa, front_section_width_m, front_overall_diameter_m, front_axle_load_n / 2.0
    )
    rho_r = rolling_resistance_rear(bn_rear, slip_fraction)
    rho_f = rolling_resistance_front(bn_front)
    return WheelResponse(bn_front=bn_front, rho_r=rho_r, rho_f=rho_f, mr_ratio=rho_r + rho_f)


def engine_torque_limited_pull_n(
    *,
    max_engine_torque_nm: float,
    rear_rolling_radius_m: float,
    transmission_efficiency_pct: float,
    rho_r: float,
    rho_f: float,
    rear_axle_load_n: float,
    front_axle_load_n: float,
) -> float:
    """Maximum pull the engine torque can develop, Pet -- DSS Eq. 3.4 [DSS-EXACT]:

        Pet = F - MR = T*eta_ea/r - (rho_r*Rr + rho_f*Rf)

    where `T` is engine torque (N-m), `r` the driving-wheel rolling radius (m)
    and `eta_ea` the engine-to-axle transmission efficiency.

    Reported as a diagnostic only. The document introduces Pet but never states
    its role in the algorithm flow -- in particular it never says Pet caps the
    soil-limited pull Pst -- so it does not constrain the slip solution here.

    ⚠️ Caveat (DSS-AMBIGUOUS): as written, the thrust term applies **engine**
    torque directly at the driving-wheel radius, with no transmission gear
    reduction between them. A real tractor has a total reduction of roughly
    25-40:1, so `T*eta_ea/r` computed literally is about 30x too small and Pet
    comes out negative for every realistic tractor. The equation is implemented
    exactly as the document gives it -- no gear ratio has been invented -- so a
    non-positive Pet indicates the missing ratio, not an actual engine
    limitation. Callers must not read a negative Pet as "the engine is the
    binding constraint".
    """
    require_positive("engine torque", max_engine_torque_nm)
    require_positive("rear rolling radius", rear_rolling_radius_m)
    thrust_n = safe_div(
        "engine-torque thrust",
        max_engine_torque_nm * (transmission_efficiency_pct / 100.0),
        rear_rolling_radius_m,
    )
    motion_resistance_n = rho_r * rear_axle_load_n + rho_f * front_axle_load_n
    return thrust_n - motion_resistance_n


@dataclass(frozen=True)
class SlipSolution:
    slip_pct: float
    bn_rear: float
    mu: float
    mu_g: float
    pull_n: float
    converged: bool
    #: False when mu <= 0 at every trial slip -- motion resistance exceeds the
    #: gross traction available, so no amount of extra slip develops net pull.
    #: Distinguishes "this soil/load cannot pull at all" from "the pull is simply
    #: short of the draft at the 20% cap", which need different advice.
    traction_possible: bool = True


def solve_slip(
    *,
    draft_n: float,
    rear_axle_load_n: float,
    ci_kpa: float,
    rear_section_width_m: float,
    rear_overall_diameter_m: float,
    mobility_fn: MobilityNumberFn = None,
) -> SlipSolution:
    """Iterate trial slip S (DSS Section 3.4.6): start at 2%, step 0.1%, until Pst = mu*Rr >= D.

    `mobility_fn` selects which wheel-numeric model computes Bn from the
    per-wheel load (W = Rr/2). Defaults to the Section 3 `Bn`, which every mode
    now uses; the seam is kept so an alternative model can be supplied without
    touching the solver.
    """
    resolved_mobility_fn = mobility_fn or mobility_number
    bn_rear = resolved_mobility_fn(ci_kpa, rear_section_width_m, rear_overall_diameter_m, rear_axle_load_n / 2.0)

    slip_pct = SLIP_INITIAL_PCT
    mu = 0.0
    # mu_g depends only on Bn, which is fixed for the whole search -- computing it
    # once keeps it out of the (up to 180-step) loop below.
    mu_g = gross_traction_ratio(bn_rear)
    pull_n = 0.0
    converged = False
    best_mu = float("-inf")

    for _ in range(MAX_SLIP_ITERATIONS):
        mu = net_traction_coefficient(bn_rear, slip_pct / 100.0, mu_g=mu_g)
        best_mu = max(best_mu, mu)
        pull_n = mu * rear_axle_load_n
        if pull_n >= draft_n:
            converged = True
            break
        slip_pct += SLIP_INCREMENT_PCT
        if slip_pct >= MAX_SLIP_PCT:
            slip_pct = MAX_SLIP_PCT
            mu = net_traction_coefficient(bn_rear, slip_pct / 100.0, mu_g=mu_g)
            best_mu = max(best_mu, mu)
            pull_n = mu * rear_axle_load_n
            break

    return SlipSolution(
        slip_pct=slip_pct,
        bn_rear=bn_rear,
        mu=mu,
        mu_g=mu_g,
        pull_n=pull_n,
        converged=converged,
        traction_possible=best_mu > 0.0,
    )


def front_ballast_required_kg(
    *,
    kwef: float,
    tractor_weight_n: float,
    rsf_n: float,
    draft_n: float,
    yd_m: float,
    implement_weight_n: float,
    py_n: float,
    cg_distance_from_hitch_m: float,
    hitch_distance_from_rear_m: float,
    er_m: float,
    ef_m: float,
    wheelbase_m: float,
) -> tuple[float, bool]:
    """Front ballast BRf so that Kwef = Rf/Wt = 0.20 (DSS Eq. 3.7), solved by bisection.

    0.2*(Wt+BRf) = [Wt*((Rsf+BRf)/(Wt+BRf) - er) + D*Yd - (Wm+Py)*(Xcgi+Hd+er)] / (L-er+ef)

    Returns (ballast_kg, feasible). For some geometries this equation has no
    finite solution (the RHS saturates below the ever-growing LHS target) --
    that is a real property of the DSS formula, not a solver bug. When
    infeasible, ballast_kg is the search-ceiling estimate and feasible=False,
    so callers can surface a warning instead of a falsely-precise number.
    """
    if kwef >= FRONT_BALLAST_TARGET_KWEF:
        return 0.0, True

    denom = wheelbase_m - er_m + ef_m

    def residual(br_f: float) -> float:
        lhs = FRONT_BALLAST_TARGET_KWEF * (tractor_weight_n + br_f)
        rhs = (
            tractor_weight_n * ((rsf_n + br_f) / (tractor_weight_n + br_f) - er_m)
            + draft_n * yd_m
            - (implement_weight_n + py_n) * (cg_distance_from_hitch_m + hitch_distance_from_rear_m + er_m)
        ) / denom
        return rhs - lhs

    lo, hi = 0.0, tractor_weight_n * 5.0 + 1.0
    f_lo, f_hi = residual(lo), residual(hi)
    if abs(f_lo) < BALLAST_SOLVER_TOLERANCE:
        return 0.0, True
    if (f_lo > 0) == (f_hi > 0):
        # Residual never brackets zero within the search range: the front-axle
        # deficit persists (or is already satisfied) for every ballast amount
        # tried. If it's a persistent deficit (negative throughout), no finite
        # front ballast can reach Kwef=0.20 under this equation.
        return (0.0 if f_lo > 0 else hi) / GRAVITY, False
    for _ in range(BALLAST_SOLVER_MAX_ITERATIONS):
        mid = (lo + hi) / 2.0
        f_mid = residual(mid)
        if abs(f_mid) < BALLAST_SOLVER_TOLERANCE or (hi - lo) < BALLAST_SOLVER_TOLERANCE:
            return mid / GRAVITY, True
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return ((lo + hi) / 2.0) / GRAVITY, True


def rear_ballast_required_kg(
    *,
    slip_pct: float,
    draft_n: float,
    rear_axle_load_n: float,
    rsr_n: float,
    ci_kpa: float,
    rear_section_width_m: float,
    rear_overall_diameter_m: float,
    yd_m: float,
    implement_weight_n: float,
    py_n: float,
    cg_distance_from_hitch_m: float,
    hitch_distance_from_rear_m: float,
    tractor_weight_n: float,
    er_m: float,
    ef_m: float,
    wheelbase_m: float,
    mobility_fn: MobilityNumberFn = None,
) -> float:
    """Rear ballast BRr to limit slip to 15% (DSS Eq. 3.8, 3.9), R' solved by fixed-point iteration.

    R' = D / mu'(S=0.15, Bn evaluated at W = R'/2)
    BRr = [R'(L-er+ef) + D*Yd - Rsr*L - Wt*ef - (Wm+Py)*(Xcgi+Hd+er)] / (L+ef)

    The wheel numeric is re-evaluated at the *trial* rear load R' on every
    iteration (W = R'/2), per the DSS note "Bn' = mobility number at W=R'/2".
    `mobility_fn` selects the model; all modes now use the Section 3 `Bn` default.

    Raises `ValueError` when the fixed point does not settle within
    `BALLAST_SOLVER_MAX_ITERATIONS`, rather than returning the last trial value as
    if it had converged.
    """
    if slip_pct <= REAR_BALLAST_TARGET_SLIP_PCT:
        return 0.0

    resolved_mobility_fn = mobility_fn or mobility_number
    target_slip_fraction = REAR_BALLAST_TARGET_SLIP_PCT / 100.0
    r_prime = float(rear_axle_load_n)
    settled = False
    for _ in range(BALLAST_SOLVER_MAX_ITERATIONS):
        bn_prime = resolved_mobility_fn(ci_kpa, rear_section_width_m, rear_overall_diameter_m, r_prime / 2.0)
        mu_prime = net_traction_coefficient(bn_prime, target_slip_fraction)
        if mu_prime <= 0:
            raise ValueError(
                "Cannot size rear ballast: at the 15% target slip the wheel numeric "
                "Bn = {0:.3f} (W = R'/2 = {1:.0f} N) yields a non-positive coefficient of "
                "traction, so no rear-axle load develops the required pull. Work firmer "
                "soil or reduce draft.".format(bn_prime, r_prime / 2.0)
            )
        r_prime_new = draft_n / mu_prime
        if abs(r_prime_new - r_prime) < BALLAST_SOLVER_TOLERANCE:
            r_prime = r_prime_new
            settled = True
            break
        r_prime = r_prime_new

    if not settled:
        raise ValueError(
            "Rear-ballast fixed point did not converge in {0} iterations "
            "(last R' = {1:.1f} N). The ballast requirement is unreliable for these "
            "inputs and has not been reported.".format(BALLAST_SOLVER_MAX_ITERATIONS, r_prime)
        )

    br_r = (
        r_prime * (wheelbase_m - er_m + ef_m)
        + draft_n * yd_m
        - rsr_n * wheelbase_m
        - tractor_weight_n * ef_m
        - (implement_weight_n + py_n) * (cg_distance_from_hitch_m + hitch_distance_from_rear_m + er_m)
    ) / (wheelbase_m + ef_m)

    return max(0.0, br_r / GRAVITY)


def _engine_torque_warnings(pet_n: float, draft_n: float) -> list:
    """Warning text for the Eq. 3.4 pull limit, shared by all three engines.

    Distinguishes the two cases deliberately: a non-positive Pet is an artefact
    of the equation omitting the transmission gear ratio (see
    `engine_torque_limited_pull_n`), not evidence that the engine is too small.
    Reporting it as the latter would be a false conclusion drawn from a
    known-incomplete formula.
    """
    if pet_n <= 0:
        return [
            "Engine-torque pull limit (Pet = {0:.0f} N, DSS Eq. 3.4) is non-physical: "
            "the equation as written applies engine torque directly at the wheel "
            "radius with no transmission gear reduction (a real tractor has roughly "
            "25-40:1), so Pet is about 30x too small. Reported for traceability "
            "only -- it does not indicate an engine limitation.".format(pet_n)
        ]
    if pet_n < draft_n:
        return [
            "Engine-torque pull limit (Pet = {0:.0f} N, DSS Eq. 3.4) is below the "
            "required draft ({1:.0f} N): the engine, not the soil, is the binding "
            "constraint for this combination.".format(pet_n, draft_n)
        ]
    return []


def calculate_legacy_performance(inputs: LegacyInputs) -> dict:
    require_positive("implement width", inputs.width_m)
    require_positive("operating speed", inputs.speed_kmh)
    require_positive("field area", inputs.field_area_ha)
    require_positive("rated PTO power", inputs.pto_power_kw)
    require_positive("cone index", inputs.cone_index_kpa)
    require_positive("tillage depth", inputs.depth_cm)
    require_positive("wheelbase", inputs.wheelbase_m)

    fi = fi_factor(inputs.implement_type, inputs.soil_texture)
    draft_n = estimate_draft_force(inputs)
    logger.info("legacy_simulation.draft_force", extra={"draft_force_n": draft_n})

    tractor_weight_n = (inputs.front_axle_weight_kg + inputs.rear_axle_weight_kg) * GRAVITY
    implement_weight_n = inputs.weight_kg * GRAVITY
    py_over_d = py_over_d_ratio(inputs.implement_type)
    py_n = py_over_d * draft_n
    geometry = geometry_terms(
        depth_cm=inputs.depth_cm,
        rear_rolling_radius_m=inputs.rear_rolling_radius_m,
        front_rolling_radius_m=inputs.front_rolling_radius_m,
    )
    yd_m, er_m, ef_m = geometry.yd_m, geometry.er_m, geometry.ef_m

    rd_n, fd_n = dynamic_axle_loads(
        draft_n=draft_n,
        depth_cm=inputs.depth_cm,
        wheelbase_m=inputs.wheelbase_m,
        hitch_distance_from_rear_m=inputs.hitch_distance_from_rear_m,
        cg_distance_from_rear_m=inputs.cg_distance_from_rear_m,
        cg_distance_from_hitch_m=inputs.cg_distance_from_hitch_m,
        rear_rolling_radius_m=inputs.rear_rolling_radius_m,
        front_rolling_radius_m=inputs.front_rolling_radius_m,
        tractor_weight_n=tractor_weight_n,
        implement_weight_n=implement_weight_n,
        py_n=py_n,
    )
    if rd_n <= 0 or fd_n <= 0:
        raise ValueError("Invalid load distribution: dynamic axle load became non-positive")

    warnings: list[str] = []
    slip_solution = solve_slip(
        draft_n=draft_n,
        rear_axle_load_n=rd_n,
        ci_kpa=inputs.cone_index_kpa,
        rear_section_width_m=inputs.rear_section_width_m,
        rear_overall_diameter_m=inputs.rear_overall_diameter_m,
    )
    slip = slip_solution.slip_pct
    bnr = slip_solution.bn_rear
    mu = slip_solution.mu
    mu_g = slip_solution.mu_g
    converged = slip_solution.converged
    if not converged:
        warnings.append("Slip iteration reached the 20% engineering limit before full draft convergence.")
        logger.warning(
            "legacy_simulation.convergence_warning",
            extra={"slip_pct": slip, "pull_n": slip_solution.pull_n, "draft_force_n": draft_n},
        )
        warnings.append("Simulation retained bounded partial results from the last stable iteration.")

    # Front wheel numeric always uses the Section 3 Bn (DSS defines rho_f from
    # CI*b*d/Wf); the rear value comes from whichever driven-wheel model ran.
    wheels = wheel_response(
        ci_kpa=inputs.cone_index_kpa,
        front_section_width_m=inputs.front_section_width_m,
        front_overall_diameter_m=inputs.front_overall_diameter_m,
        front_axle_load_n=fd_n,
        bn_rear=bnr,
        slip_fraction=slip / 100.0,
    )
    bnf, rho_r, rho_f, mr_ratio = wheels.bn_front, wheels.rho_r, wheels.rho_f, wheels.mr_ratio

    te_pct = clamp(traction_efficiency_percent(mu, mu_g, slip / 100.0), 0.0, 100.0)
    if te_pct <= 0:
        raise ValueError("Either decrease depth or speed of operation, since slip is very low")

    kwf = fd_n / tractor_weight_n
    kwr = rd_n / tractor_weight_n

    capacity = field_capacity(
        speed_kmh=inputs.speed_kmh,
        width_m=inputs.width_m,
        field_area_ha=inputs.field_area_ha,
        field_width_m=inputs.field_width_m,
    )
    fc_th, fc_ac = capacity.fc_th, capacity.fc_ac
    field_eff_pct = capacity.field_eff_pct
    turning_time_s, number_turns = capacity.turning_time_s, capacity.number_turns
    total_time_h = capacity.total_time_h

    power = power_and_fuel(
        draft_n=draft_n,
        speed_kmh=inputs.speed_kmh,
        te_pct=te_pct,
        transmission_efficiency_pct=inputs.transmission_efficiency_pct,
        power_reserve_pct=inputs.power_reserve_pct,
        pto_power_kw=inputs.pto_power_kw,
        fc_th=fc_th,
        fc_ac=fc_ac,
    )
    pdb_kw, ptr_kw, pused_pct = power.pdb_kw, power.ptr_kw, power.put_pct

    # DSS Eq. 3.4: maximum pull the engine torque can develop. Diagnostic only --
    # the document never states that Pet caps the soil-limited pull Pst.
    pet_n = None
    if inputs.max_engine_torque_nm is not None and inputs.max_engine_torque_nm > 0:
        pet_n = engine_torque_limited_pull_n(
            max_engine_torque_nm=inputs.max_engine_torque_nm,
            rear_rolling_radius_m=inputs.rear_rolling_radius_m,
            transmission_efficiency_pct=inputs.transmission_efficiency_pct,
            rho_r=rho_r,
            rho_f=rho_f,
            rear_axle_load_n=rd_n,
            front_axle_load_n=fd_n,
        )
        warnings.extend(_engine_torque_warnings(pet_n, draft_n))

    ballast_front_kg, front_ballast_feasible = front_ballast_required_kg(
        kwef=kwf,
        tractor_weight_n=tractor_weight_n,
        rsf_n=inputs.front_axle_weight_kg * GRAVITY,
        draft_n=draft_n,
        yd_m=yd_m,
        implement_weight_n=implement_weight_n,
        py_n=py_n,
        cg_distance_from_hitch_m=inputs.cg_distance_from_hitch_m,
        hitch_distance_from_rear_m=inputs.hitch_distance_from_rear_m,
        er_m=er_m,
        ef_m=ef_m,
        wheelbase_m=inputs.wheelbase_m,
    )
    if not front_ballast_feasible:
        warnings.append(
            "Front-axle weight-utilization target (Kwef=0.20) cannot be reached with any "
            "amount of front ballast for this tractor/implement combination; consider a "
            "different implement or tractor pairing."
        )

    ballast_rear_kg = rear_ballast_required_kg(
        slip_pct=slip,
        draft_n=draft_n,
        rear_axle_load_n=rd_n,
        rsr_n=inputs.rear_axle_weight_kg * GRAVITY,
        ci_kpa=inputs.cone_index_kpa,
        rear_section_width_m=inputs.rear_section_width_m,
        rear_overall_diameter_m=inputs.rear_overall_diameter_m,
        yd_m=yd_m,
        implement_weight_n=implement_weight_n,
        py_n=py_n,
        cg_distance_from_hitch_m=inputs.cg_distance_from_hitch_m,
        hitch_distance_from_rear_m=inputs.hitch_distance_from_rear_m,
        tractor_weight_n=tractor_weight_n,
        er_m=er_m,
        ef_m=ef_m,
        wheelbase_m=inputs.wheelbase_m,
    )

    sfc = power.sfc
    fuel_cons_l_per_ha = power.fuel_l_per_ha
    overall_pct = power.overall_pct

    envelope = result_envelope(
        slip=slip,
        draft_n=draft_n,
        te_pct=te_pct,
        fuel_l_per_ha=fuel_cons_l_per_ha,
        put_pct=pused_pct,
        field_eff_pct=field_eff_pct,
        converged=converged,
    )
    load_status = envelope.load_status
    recommendation = envelope.recommendations
    simulation_status = envelope.status
    confidence = envelope.confidence
    status_message = envelope.status_message

    logger.info(
        "legacy_simulation.outputs",
        extra={
            "draft_force_n": draft_n,
            "slip_pct": slip,
            "traction_efficiency_pct": te_pct,
            "fuel_l_per_ha": fuel_cons_l_per_ha,
            "front_axle_load_n": fd_n,
            "rear_axle_load_n": rd_n,
            "power_utilization_pct": pused_pct,
        },
    )

    return {
        "draft_force": draft_n,
        "drawbar_power": pdb_kw,
        "slip": slip,
        "coefficient_net_traction": mu,
        "motion_resistance_ratio": mr_ratio,
        "motion_resistance": mr_ratio,
        "traction_efficiency": te_pct,
        "front_weight_utilization": kwf,
        "rear_weight_utilization": kwr,
        "required_pto_power": ptr_kw,
        "power_utilization": pused_pct,
        "field_capacity_theoretical": fc_th,
        "field_capacity_actual": fc_ac,
        "field_efficiency": field_eff_pct,
        "total_time_hours": total_time_h,
        "specific_fuel_consumption": sfc,
        "fuel_consumption_per_hectare": fuel_cons_l_per_ha,
        "overall_efficiency": overall_pct,
        "ballast_front_required": ballast_front_kg,
        "ballast_rear_required": ballast_rear_kg,
        "status_message": status_message,
        "recommendations": recommendation,
        "load_status": load_status,
        "status": simulation_status,
        "warnings": warnings,
        "confidence": confidence,
        "recommendation_messages": envelope.recommendation_messages,
        "converged": converged,
        "engine_torque_limited_pull": pet_n,
        "fuel_l_per_hour": power.fuel_lph,
        "fuel_l_per_hour_pto_basis": power.fuel_lph_pto_basis,
        "legacy_field_efficiency_raw": capacity.field_eff_raw_pct,
        "legacy_fi": fi,
        "legacypy_over_d_ratio": py_over_d,
        "legacy_turning_time_seconds": turning_time_s,
        "legacy_number_of_turns": number_turns,
        "legacy_front_axle_load_n": fd_n,
        "legacy_rear_axle_load_n": rd_n,
        "legacy_mobility_number_rear": bnr,
        "legacy_mobility_number_front": bnf,
        "legacy_gross_traction_ratio": mu_g,
        "calculation_mode": "dss_spec_v1",
    }
