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
    DRAFT_WIDTH_IS_TOOL_COUNT,
    FI_FACTOR_BY_TEXTURE,
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
    require_finite,
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
from app.core.implement_taxonomy import is_passive
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
    #: Py/D for this implement. None falls back to the per-type table in
    #: `constants.PY_OVER_D_RATIO_BY_IMPLEMENT`, since the DB column is nullable.
    vertical_horizontal_ratio: Optional[float]
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

    #: Number of ground-engaging tools. Eq. 3.1's `W` for per-tool implement
    #: classes (see `constants.DRAFT_WIDTH_IS_TOOL_COUNT`); ignored for the rest.
    number_of_tools: Optional[int] = None

    #: Rated PTO draw of a PTO-powered implement, kW. Required only when such an
    #: implement is run *standalone* -- see `calculate_standalone_active_performance`.
    rotor_pto_power_kw: Optional[float] = None
    #: Rated rotor shaft speed, rpm. Reported, not used in the standalone path.
    rotor_speed_rpm: Optional[float] = None


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

    [REFERENCE-ALIGNED] One value per soil texture, applied to every implement --
    **reverted** to match `docs/tillage_dss (2).html`'s engine exactly, whose
    `readCommonInputs` reads Fi from a single texture selector
    (`fi: parseFloat(...)`) with no implement dimension at all.

    A per-implement table (ASABE D497 Table 1: disc tools 1.0/0.88/0.78, cultivators
    1.0/0.85/0.65) was reinstated for one session -- independently corroborated by the
    2006 VB6 original this DSS derives from -- and is still available as
    `constants.FI_FACTOR_BY_IMPLEMENT_TYPE`, unused by this function now. Reverting
    understates draft on every disc/cultivator implement in non-fine soil again
    (measured: disc harrow in coarse soil, draft back down 1.73x). See that
    constant's docstring, and `constants.FI_FACTOR_BY_TEXTURE`'s, for the full
    provenance either way.

    `implement_type` is still validated, though it no longer selects the value: the
    DSS passive-draft model is defined solely for unpowered tools, and this raises
    for the rest -- without it, routing a rotor through Eq. 3.1 would silently
    succeed and model a PTO-powered implement as if it were unpowered.
    """
    if not is_passive(implement_type):
        raise _passive_only_lookup_error(implement_type, "Fi soil-texture factor")
    try:
        return FI_FACTOR_BY_TEXTURE[soil_texture.value]
    except KeyError:
        raise ValueError(
            f"No Fi factor for soil texture '{getattr(soil_texture, 'value', soil_texture)}'."
        )


def py_over_d_ratio(
    implement_type: ImplementType, ratio: Optional[float] = None
) -> float:
    """Vertical:horizontal soil-reaction ratio Py/D, used as `Py = (Py/D) * D`.

    Both reference implementations carry this as a **per-implement input** -- the
    spreadsheet's "Vertical to Horizontal Ratio" cell and the HTML library's
    `PyD` field -- not as a per-type constant. `ratio` is that per-implement
    value (`Implement.vertical_horizontal_ratio`); when it is None, because the
    column is nullable, the per-type table in `constants` is used instead.

    A negative ratio is rejected rather than silently used: it would reverse the
    direction of the vertical soil reaction.
    """
    if ratio is not None:
        require_finite("Py/D ratio", ratio)
        if ratio < 0:
            raise ValueError(
                "Invalid Py/D ratio: must not be negative, got {0!r}".format(ratio)
            )
        return float(ratio)
    try:
        return PY_OVER_D_RATIO_BY_IMPLEMENT[implement_type.value]
    except KeyError:
        raise _passive_only_lookup_error(implement_type, "Py/D ratio")


def draft_width_parameter(
    implement_type: ImplementType,
    width_m: float,
    number_of_tools: Optional[int] = None,
) -> float:
    """Eq. 3.1's `W`: working width in m for every implement.

    See `constants.DRAFT_WIDTH_IS_TOOL_COUNT` -- now kept empty deliberately, to
    match `docs/tillage_dss (2).html`'s engine, which has no tool-count concept at
    all. Only Eq. 3.1 uses this -- field capacity, turning time and swath always
    take the width in metres regardless.

    The tool-count path below is unreachable with an empty
    `DRAFT_WIDTH_IS_TOOL_COUNT`, but is left in place rather than deleted: it is a
    one-line revert (repopulate that frozenset) back to per-tool fidelity for
    implement classes ASABE D497 Table 1 tabulates per tool rather than per metre,
    should HTML parity ever stop being the goal. See that constant's docstring for
    the size of what per-tool fidelity was worth.
    """
    if implement_type.value not in DRAFT_WIDTH_IS_TOOL_COUNT:
        return width_m
    if number_of_tools is None:
        raise ValueError(
            "Implement type '{0}' is tabulated per tool in ASABE D497, so Eq. 3.1's W "
            "is the number of tools, not the width in metres. This implement has no "
            "`number_of_tools` recorded, and substituting the width would understate "
            "draft by roughly 4x (far more in an active-passive combination). Set the "
            "tool count on the implement.".format(implement_type.value)
        )
    if number_of_tools <= 0:
        raise ValueError(
            "Invalid number of tools: must be a positive whole number, got {0!r}".format(
                number_of_tools
            )
        )
    return float(number_of_tools)


def draft_width_is_tool_count(implement_type: ImplementType) -> bool:
    """True when Eq. 3.1's `W` is a tool count rather than a width in metres."""
    return implement_type.value in DRAFT_WIDTH_IS_TOOL_COUNT


def estimate_draft_force(inputs: LegacyInputs) -> float:
    """Implement draft force D, N (DSS Eq. 3.1): D = F*(A + B*S + C*S^2)*W*T.

    Thin binding of `LegacyInputs` onto the single Eq. 3.1 kernel in
    `dss_shared.draft_force_n`, which all three modes share.
    """
    return draft_force_n(
        fi=fi_factor(inputs.implement_type, inputs.soil_texture),
        asae_param_a=inputs.asae_param_a,
        asae_param_b=inputs.asae_param_b,
        asae_param_c=inputs.asae_param_c,
        speed_kmh=inputs.speed_kmh,
        width_m=draft_width_parameter(
            inputs.implement_type, inputs.width_m, inputs.number_of_tools
        ),
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
    """mu_g = 0.88 * (1 - exp(-0.1*Bn)) [DSS-EXACT].

    This is the Brixius *envelope* factor -- the asymptotic ceiling the gross
    traction ratio approaches as slip grows -- NOT the gross traction ratio at a
    given slip. It is the correct quantity inside `net_traction_coefficient`,
    which multiplies it by the slip term. For the ratio actually developed at an
    operating slip (the denominator of tractive efficiency), use
    `gross_traction_at_slip`.
    """
    require_positive("rear wheel numeric", bn_rear)
    return TRACTION_MU_G_SCALE * (1.0 - math.exp(-TRACTION_BN_EXPONENT_COEFF * bn_rear))


def gross_traction_at_slip(
    bn_rear: float, slip_fraction: float, mu_g: Optional[float] = None
) -> float:
    """Gross traction ratio actually developed at slip S -- Brixius (1987):

        GT = 0.88*(1 - exp(-0.1*Bn))*(1 - exp(-7.5*S)) + 0.04

    Brixius' companion motion-resistance ratio is
    `MR = 0.04 + 1/Bn + 0.5*S/sqrt(Bn)`, and `net_traction_coefficient` returns
    exactly `GT - MR` (the two 0.04 terms cancel), which identifies the model
    beyond doubt and fixes what `GT` must be here.
    """
    require_positive("rear wheel numeric", bn_rear)
    if mu_g is None:
        mu_g = gross_traction_ratio(bn_rear)
    return (
        mu_g * (1.0 - math.exp(-TRACTION_SLIP_EXPONENT_COEFF * slip_fraction))
        + ROLLING_RESISTANCE_BASE
    )


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


def traction_efficiency_percent(
    mu: float, mu_g: float, slip_fraction: float, *, bn_rear: float
) -> float:
    """TE = mu*(1-S) / mu_g -- **DSS specification Eq. (3.2)**, as a percentage.

    The denominator is the Brixius **envelope** gross traction ratio
    `mu_g = 0.88*(1 - exp(-0.1*Bn))`, the same quantity the document uses
    everywhere else. There is no `+0.04` term and no slip factor in it.

    **Provenance.** Eq. (3.2) is stored in the specification as a MathType/OLE
    object (`word/media/image1.wmf`), which is why plain-text extraction of the
    DOCX shows only the label "(3.2)" and its variable legend. Rendered, it reads
    `TE = mu*(1-S)/mu_g`, with the document's own legend giving `mu` as the
    coefficient of traction and `mu_g` as the gross traction ratio. All three
    reference artifacts agree:

    | source | denominator |
    |---|---|
    | DOCX Eq. (3.2) -- the specification | `mu_g` (envelope) |
    | `tillage_dss.html` `tractiveEfficiencyPct` | `mu_g` (envelope) |
    | spreadsheet `C59 = (C58*(1-C55))/C57` | `mu_g` (envelope) |

    An earlier revision of this engine divided by the gross traction ratio
    *developed at the operating slip* (`gross_traction_at_slip`), on the physical
    argument that Eq. 3.2's denominator ought to be the ratio actually developed
    rather than its asymptotic ceiling. That made the engine the sole outlier
    against its own specification, and it has been reverted.

    **Accepted, documented consequence:** the envelope reads TE *lower* -- and
    therefore `Ptr = DBp/(TE*eta_t)` and power utilisation *higher* -- than the
    at-slip form, most markedly at high mobility numbers (firm soil). That is a
    property of the model the specification prescribes, not a defect here, and it
    is deliberately not compensated for anywhere downstream.

    The at-slip value is retained as the diagnostic
    `traction_efficiency_at_slip_percent` (see `traction_efficiency_at_slip_pct`),
    alongside the `gross_traction_at_slip` ratio it is built from, so the two
    readings stay comparable. See "RESOLVED: tractive-efficiency denominator" in
    docs/SIMULATION_ENGINE_FORMULAS.md.

    `bn_rear` is keyword-only and retained: three call sites pass it, and it
    records which wheel numeric the traction solution came from. The envelope
    denominator does not consume it, so it is asserted rather than silently unused.
    """
    if mu_g == 0:
        raise ValueError("Gross traction ratio is zero")
    require_positive("rear wheel numeric", bn_rear)
    return traction_efficiency_envelope_percent(mu, mu_g, slip_fraction)


def traction_efficiency_envelope_percent(
    mu: float, mu_g: float, slip_fraction: float
) -> float:
    """`TE = mu*(1-S)/mu_g` as a percentage -- the specification's Eq. (3.2) form.

    This is the **primary** TE basis; `traction_efficiency_percent` delegates here.
    It is kept as a separate entry point because it is also what reconciles a run
    against `tillage_dss.html` and the spreadsheet without re-deriving anything.

    Returns 0.0 for a zero envelope rather than raising, so it stays safe to call
    from a diagnostic context. `traction_efficiency_percent` applies the raising
    guard before delegating, so the primary path still fails loudly.
    """
    if not mu_g:
        return 0.0
    return mu * (1.0 - slip_fraction) / mu_g * 100.0


def traction_efficiency_at_slip_pct(
    mu: float, mu_g: float, slip_fraction: float, *, bn_rear: float
) -> float:
    """TE divided by the gross traction ratio developed **at the operating slip**.

    `TE_at_slip = mu*(1-S) / (mu_g*(1 - exp(-7.5*S)) + 0.04) * 100`

    **Diagnostic only -- drives nothing.** This was the engine's primary until the
    specification's Eq. (3.2) image was read (see `traction_efficiency_percent`);
    it is retained because it is the physically-argued alternative and because
    keeping it reported makes the difference between the two readings measurable
    rather than a matter of recollection. Reported as
    `traction_efficiency_at_slip_percent`.

    Returns 0.0 rather than raising on a non-positive denominator, for the same
    reason the envelope helper does.
    """
    if not mu_g:
        return 0.0
    denominator = gross_traction_at_slip(bn_rear, slip_fraction, mu_g=mu_g)
    if denominator <= 0:
        return 0.0
    return mu * (1.0 - slip_fraction) / denominator * 100.0


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
    #: Slip actually reported and used downstream. Linearly interpolated between
    #: the last two trial steps, so it is the slip at which Pst == D rather than
    #: the 0.1%-quantised step that first exceeded it.
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
    #: The raw trial step the search stopped on, before interpolation. Reported
    #: as a diagnostic so the DSS Section 3.4.6 schedule stays auditable.
    stepped_slip_pct: float = 0.0


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

    On convergence the reported slip is **linearly interpolated** between the
    last two trial steps, matching the reference implementation:

        s = s_prev + (D - Pst_prev) * (s_step - s_prev) / (Pst_step - Pst_prev)

    The stepped value is the first 0.1% increment at which pull exceeds draft, so
    it systematically overstates slip by up to one step; interpolating recovers
    the slip at which Pst == D. `mu` and `pull_n` are re-evaluated there so the
    whole solution stays self-consistent.
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
    prev: Optional[tuple] = None  # (slip_pct, pull_n) of the last step short of draft

    for _ in range(MAX_SLIP_ITERATIONS):
        mu = net_traction_coefficient(bn_rear, slip_pct / 100.0, mu_g=mu_g)
        best_mu = max(best_mu, mu)
        pull_n = mu * rear_axle_load_n
        if pull_n >= draft_n:
            converged = True
            break
        prev = (slip_pct, pull_n)
        slip_pct += SLIP_INCREMENT_PCT
        if slip_pct >= MAX_SLIP_PCT:
            slip_pct = MAX_SLIP_PCT
            mu = net_traction_coefficient(bn_rear, slip_pct / 100.0, mu_g=mu_g)
            best_mu = max(best_mu, mu)
            pull_n = mu * rear_axle_load_n
            break

    stepped_slip_pct = slip_pct
    if converged and prev is not None:
        prev_slip, prev_pull = prev
        span = pull_n - prev_pull
        if span > 0:
            slip_pct = prev_slip + (draft_n - prev_pull) * (stepped_slip_pct - prev_slip) / span
            # Re-evaluate at the interpolated slip so mu/pull match the reported slip.
            mu = net_traction_coefficient(bn_rear, slip_pct / 100.0, mu_g=mu_g)
            pull_n = mu * rear_axle_load_n

    return SlipSolution(
        slip_pct=slip_pct,
        bn_rear=bn_rear,
        mu=mu,
        mu_g=mu_g,
        pull_n=pull_n,
        converged=converged,
        traction_possible=best_mu > 0.0,
        stepped_slip_pct=stepped_slip_pct,
    )


def front_ballast_required_kg(
    *,
    tractor_weight_n: float,
    rf_for_added_weight_n: Callable[[float], float],
) -> tuple[Optional[float], bool]:
    """Front ballast BRf, in kg, so that Kwef = Rf/(Wt + BRf) >= 0.20.

    `rf_for_added_weight_n(extra_n)` must return the dynamic front axle load with
    `extra_n` Newtons of ballast added to the tractor -- i.e. the caller's own
    Eq. 3.5/3.6 balance re-solved. Passing it as a callable keeps this solver
    usable by all three modes without this module importing the combination
    balance (which imports from here).

    Solved by bisection on the ballast mass, per the reference implementation:
    the residual re-evaluates the *actual* axle balance rather than an implicit
    closed form, so it cannot disagree with the axle loads reported elsewhere.

    Returns `(ballast_kg, reachable)`. When the target cannot be met by any
    ballast within the search ceiling, returns `(None, False)` so the caller can
    warn rather than quote a falsely precise number.

    Supersedes a transcription of DSS Eq. 3.7, an implicit form whose right-hand
    side saturates below the ever-growing target for some geometries, making the
    target unreachable as an artefact of the equation rather than the physics.

    **Kwef denominator -- reference conflict, resolved to `Wt`.** The spreadsheet
    disagrees with itself here: cell `C77`'s formula is `Rf/(Rr+Rf)` (the *total*
    dynamic weight) while its own note column beside it reads `Rf / Wt`. The
    document's `image21` and `tillage_dss.html` both give `Rf/Wt`, which is what is
    implemented. The choice is not cosmetic -- on the reference case it is 0.2087
    (>= the 0.20 target, so no ballast) against 0.1672 (below target, ballast
    demanded). See `docs/SIMULATION_ENGINE_FORMULAS.md` A11.
    """
    require_positive("tractor weight", tractor_weight_n)

    def kwef_gap(ballast_kg: float) -> float:
        added_n = ballast_kg * GRAVITY
        return rf_for_added_weight_n(added_n) / (tractor_weight_n + added_n) - FRONT_BALLAST_TARGET_KWEF

    if kwef_gap(0.0) >= 0:
        return 0.0, True

    lo, hi = 0.0, 5000.0
    for _ in range(40):
        if kwef_gap(hi) >= 0:
            break
        hi *= 1.6
    else:
        return None, False
    if kwef_gap(hi) < 0:
        return None, False

    for _ in range(BALLAST_SOLVER_MAX_ITERATIONS):
        mid = (lo + hi) / 2.0
        if hi - lo < BALLAST_SOLVER_TOLERANCE:
            break
        if kwef_gap(mid) < 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0, True


def rear_ballast_required_kg(
    *,
    draft_n: float,
    rear_axle_load_n: float,
    ci_kpa: float,
    rear_section_width_m: float,
    rear_overall_diameter_m: float,
    target_slip_fraction: Optional[float] = None,
    mobility_fn: MobilityNumberFn = None,
) -> tuple[Optional[float], Optional[str]]:
    """Rear ballast BRr, in kg, to bring slip down to the target (default 15%).

        R'  = D / mu'(s_target, Bn evaluated at W = R'/2)     fixed point
        BRr = max(0, (R' - Rr) / g)

    `R'` is the rear-axle load that would develop the required pull at the target
    slip; the ballast is simply the shortfall against the current load. The wheel
    numeric is re-evaluated at the trial load on every iteration, per the DSS note
    "Bn' = mobility number at W = R'/2".

    `target_slip_fraction` defaults to the 15% DSS target. Active-passive passes
    its solved slip instead, matching the reference implementation and DSS
    Eq. 5.12/5.13 -- which is the same expression, so all three modes now share
    this one solver.

    No early return for slip already below target: `R'` then comes out below `Rr`
    and the max() yields 0 naturally.

    Returns `(ballast_kg, problem)`. `problem` is None on success; when the
    requirement cannot be sized it is an explanatory message and `ballast_kg` is
    None -- mirroring `front_ballast_required_kg`.

    Infeasibility is reported rather than raised deliberately. Both failure modes
    below mean "this pairing is too heavy for this soil", which is precisely the
    verdict the DSS exists to deliver; raising would discard the whole result set
    (draft, slip, power, fuel) and leave the caller with a bare error instead of
    the evidence for that verdict. The value is still never fabricated -- callers
    get None plus the reason, and surface it as a warning.

    Supersedes a transcription of DSS Eq. 3.8, a moment-balance form that
    disagreed with both reference implementations.
    """
    resolved_mobility_fn = mobility_fn or mobility_number
    if target_slip_fraction is None:
        target_slip_fraction = REAR_BALLAST_TARGET_SLIP_PCT / 100.0
    # Seed at the larger of the current axle load and the draft, per the
    # reference: starting below the draft can send the first iterate far away.
    r_prime = max(float(rear_axle_load_n), float(draft_n))
    settled = False
    for _ in range(BALLAST_SOLVER_MAX_ITERATIONS):
        bn_prime = resolved_mobility_fn(ci_kpa, rear_section_width_m, rear_overall_diameter_m, r_prime / 2.0)
        mu_prime = net_traction_coefficient(bn_prime, target_slip_fraction)
        if mu_prime <= 0:
            return None, (
                "Rear ballast could not be sized: at the {0:.1f}% target slip the wheel "
                "numeric Bn = {1:.3f} (W = R'/2 = {2:.0f} N) yields a non-positive "
                "coefficient of traction, so no rear-axle load develops the required pull. "
                "Work firmer soil, or reduce depth/speed/width to lower the draft.".format(
                    target_slip_fraction * 100.0, bn_prime, r_prime / 2.0
                )
            )
        r_prime_new = draft_n / mu_prime
        if abs(r_prime_new - r_prime) < BALLAST_SOLVER_TOLERANCE:
            r_prime = r_prime_new
            settled = True
            break
        r_prime = r_prime_new

    if not settled:
        return None, (
            "Rear ballast could not be sized: the fixed point did not converge in {0} "
            "iterations (last R' = {1:.1f} N). The requirement is unreliable for these "
            "inputs and has not been reported.".format(BALLAST_SOLVER_MAX_ITERATIONS, r_prime)
        )

    return max(0.0, (r_prime - rear_axle_load_n) / GRAVITY), None


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


@dataclass(frozen=True)
class AxleLoadResolution:
    """Axle loads, after fitting stabilising front ballast if the front end lifts."""

    rear_axle_load_n: float
    front_axle_load_n: float
    #: Front ballast fitted to make the combination driveable; 0.0 if none was needed.
    stabilising_ballast_kg: float
    infeasible_without_ballast: bool


def resolve_axle_loads(
    *,
    rear_axle_load_n: float,
    front_axle_load_n: float,
    tractor_weight_n: float,
    axle_loads_for_added_weight: Callable[[float], "tuple[float, float]"],
    warnings: list,
) -> AxleLoadResolution:
    """Keep a front-lifting combination answerable instead of failing the run.

    A non-positive front axle load means the draft has lifted the front end. That
    is a real physical limit, but it is exactly the condition the DSS exists to
    advise on: front ballast usually restores it, and the engine already has a
    solver for how much. Raising here instead -- which is what the code used to
    do -- refused the question rather than answering it, and did so for about 15%
    of catalogue pairings.

    A non-positive *rear* load is not rescuable this way (adding front ballast
    moves load off the driven axle), so it still raises.
    """
    if rear_axle_load_n <= 0:
        raise ValueError(
            "Invalid load distribution: the rear (driven) axle load became non-positive "
            "({0:.0f} N). The implement's weight and vertical soil reaction are carried "
            "so far behind the tractor that it cannot stay on its driven wheels; front "
            "ballast cannot fix this. Reduce working depth, or use a lighter implement "
            "or one whose centre of gravity sits closer to the hitch.".format(rear_axle_load_n)
        )
    if front_axle_load_n > 0:
        return AxleLoadResolution(rear_axle_load_n, front_axle_load_n, 0.0, False)

    ballast_kg, reachable = front_ballast_required_kg(
        tractor_weight_n=tractor_weight_n,
        rf_for_added_weight_n=lambda extra_n: axle_loads_for_added_weight(extra_n)[1],
    )
    if not reachable or ballast_kg is None:
        raise ValueError(
            "Invalid load distribution: the draft lifts the front axle ({0:.0f} N) and no "
            "amount of front ballast restores it. This tractor is too light for this "
            "implement at these settings -- reduce depth or speed, or pair a narrower "
            "implement with it.".format(front_axle_load_n)
        )

    rd_n, fd_n = axle_loads_for_added_weight(ballast_kg * GRAVITY)
    warnings.append(
        "Front axle lifts under this draft: the results below assume {0:.0f} kg of front "
        "ballast, the minimum that restores the Kwef=0.20 steering-weight target. Without "
        "it this combination is not driveable.".format(ballast_kg)
    )
    return AxleLoadResolution(rd_n, fd_n, ballast_kg, True)


def calculate_standalone_active_performance(inputs: LegacyInputs) -> dict:
    """A PTO-powered implement (rotavator, power harrow) used on its own.

    There is no towed passive draft to model: these implements are rigidly
    three-point mounted and the library deliberately carries no A/B/C for them, so
    Eq. 3.1's whole chain -- draft, axle loads, wheel numeric, slip, mu, tractive
    efficiency, ballast -- has nothing to compute. Rather than fabricate zeros,
    every one of those is returned as **None** so the API and UI render an em-dash.

    What *is* knowable is computed: field capacity from width and speed, and the
    power/fuel chain driven directly by the implement's own rated PTO draw on the
    same PTO fuel basis the towed modes use.

    Previously this raised outright ("the DSS passive-draft model is defined only
    for unpowered tools"), which is true of Eq. 3.1 but was over-applied: it also
    refused the one configuration where Eq. 3.1 is simply not needed.
    """
    require_positive("implement width", inputs.width_m)
    require_positive("operating speed", inputs.speed_kmh)
    require_positive("field area", inputs.field_area_ha)
    require_positive("rated PTO power", inputs.pto_power_kw)

    ppto_kw = inputs.rotor_pto_power_kw
    if ppto_kw is None or ppto_kw <= 0:
        raise ValueError(
            "A standalone PTO-powered implement needs its rated PTO power draw "
            "(rotor_pto_power_kw). Set it on the implement record, or run the "
            "implement as the rotor of an active-passive combination instead."
        )

    capacity = field_capacity(
        speed_kmh=inputs.speed_kmh,
        width_m=inputs.width_m,
        field_area_ha=inputs.field_area_ha,
        field_width_m=inputs.field_width_m,
    )

    power_reserve_frac = inputs.power_reserve_pct / 100.0
    if power_reserve_frac >= 1.0:
        raise ValueError(
            "Invalid power reserve: must be below 100%, got {0!r}%".format(inputs.power_reserve_pct)
        )
    put_pct = safe_div(
        "power utilization", ppto_kw, inputs.pto_power_kw * (1.0 - power_reserve_frac)
    ) * 100.0
    x_fraction = safe_div("PTO power fraction", ppto_kw, inputs.pto_power_kw)
    sfc = specific_fuel_consumption_l_per_kwh(x_fraction)
    fuel_lph = sfc * ppto_kw
    fuel_l_per_ha = max(0.0, safe_div("fuel consumption per hectare", fuel_lph, capacity.fc_ac))

    # Table 4.2 skips any condition whose input is None, so only the Put rule can
    # fire here -- there is no slip, no mu and no Kwef to judge.
    envelope = result_envelope(
        slip=None,
        net_traction_coefficient=None,
        front_weight_utilization=None,
        fi=None,
        put_pct=put_pct,
        field_eff_pct=capacity.field_eff_pct,
        converged=True,
    )

    return {
        "calculation_mode": "dss_spec_v1_standalone_active",
        "is_standalone_active_implement": True,
        # Not applicable -- deliberately None, never 0.0.
        "draft_force": None,
        "drawbar_power": None,
        "slip": None,
        "coefficient_net_traction": None,
        "traction_efficiency": None,
        "legacy_front_axle_load_n": None,
        "legacy_rear_axle_load_n": None,
        "legacy_mobility_number_rear": None,
        "legacy_mobility_number_front": None,
        "legacy_gross_traction_ratio": None,
        "front_weight_utilization": None,
        "rear_weight_utilization": None,
        "ballast_front_required": None,
        "ballast_rear_required": None,
        "converged": None,
        # Knowable.
        "rotor_pto_power": ppto_kw,
        "rotor_speed_rpm": inputs.rotor_speed_rpm,
        "required_pto_power": ppto_kw,
        "power_utilization": put_pct,
        "pto_power_fraction_effective": x_fraction,
        "specific_fuel_consumption": sfc,
        "fuel_l_per_hour": fuel_lph,
        "fuel_basis": "pto",
        "fuel_l_per_hour_pto_basis": fuel_lph,
        # No drawbar power exists, so there is no drawbar fuel reading to report.
        "fuel_l_per_hour_drawbar_basis": None,
        "fuel_consumption_per_hectare": fuel_l_per_ha,
        "overall_efficiency": None,
        "field_capacity_theoretical": capacity.fc_th,
        "field_capacity_actual": capacity.fc_ac,
        "field_efficiency": capacity.field_eff_pct,
        "legacy_field_efficiency_raw": capacity.field_eff_raw_pct,
        "legacy_turning_time_seconds": capacity.turning_time_s,
        "legacy_number_of_turns": capacity.number_turns,
        "total_time_hours": capacity.total_time_h,
        "headland_turning_time_hours": capacity.total_turning_time_h,
        "headland_turning_time_single_pass_basis_hours": capacity.turning_time_single_pass_basis_h,
        "load_status": envelope.load_status,
        "recommendations": envelope.recommendations,
        "recommendation_messages": envelope.recommendation_messages,
        "status": envelope.status,
        "status_message": envelope.status_message,
        "confidence": envelope.confidence,
        "warnings": [
            "Standalone PTO-powered implement: draft, axle loads, traction, slip and "
            "ballast are not applicable and are reported as null."
        ],
    }


def calculate_legacy_performance(inputs: LegacyInputs) -> dict:
    # A PTO-powered implement on its own has no passive draft chain; route it to
    # the standalone path rather than failing in fi_factor.
    if not is_passive(inputs.implement_type):
        return calculate_standalone_active_performance(inputs)

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
    # Py/D comes from the implement record when present (both references treat it
    # as a per-implement input); the per-type table is only a fallback.
    py_over_d = py_over_d_ratio(inputs.implement_type, inputs.vertical_horizontal_ratio)
    py_n = py_over_d * draft_n
    geometry = geometry_terms(
        depth_cm=inputs.depth_cm,
        rear_rolling_radius_m=inputs.rear_rolling_radius_m,
        front_rolling_radius_m=inputs.front_rolling_radius_m,
    )
    yd_m, er_m, ef_m = geometry.yd_m, geometry.er_m, geometry.ef_m

    def _axle_loads_with_ballast(extra_n: float) -> "tuple[float, float]":
        """Both axle loads with `extra_n` N of front ballast, same Eq. 3.5 balance."""
        return dynamic_axle_loads(
            draft_n=draft_n,
            depth_cm=inputs.depth_cm,
            wheelbase_m=inputs.wheelbase_m,
            hitch_distance_from_rear_m=inputs.hitch_distance_from_rear_m,
            cg_distance_from_rear_m=inputs.cg_distance_from_rear_m,
            cg_distance_from_hitch_m=inputs.cg_distance_from_hitch_m,
            rear_rolling_radius_m=inputs.rear_rolling_radius_m,
            front_rolling_radius_m=inputs.front_rolling_radius_m,
            tractor_weight_n=tractor_weight_n + extra_n,
            implement_weight_n=implement_weight_n,
            py_n=py_n,
        )

    rd_n, fd_n = _axle_loads_with_ballast(0.0)

    warnings: list[str] = []
    axles = resolve_axle_loads(
        rear_axle_load_n=rd_n,
        front_axle_load_n=fd_n,
        tractor_weight_n=tractor_weight_n,
        axle_loads_for_added_weight=_axle_loads_with_ballast,
        warnings=warnings,
    )
    rd_n, fd_n = axles.rear_axle_load_n, axles.front_axle_load_n
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

    te_pct = clamp(
        traction_efficiency_percent(mu, mu_g, slip / 100.0, bn_rear=bnr), 0.0, 100.0
    )
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
        tractor_weight_n=tractor_weight_n,
        rf_for_added_weight_n=lambda extra_n: _axle_loads_with_ballast(extra_n)[1],
    )
    if not front_ballast_feasible:
        warnings.append(
            "Front-axle weight-utilization target (Kwef=0.20) cannot be reached with any "
            "amount of front ballast for this tractor/implement combination; consider a "
            "different implement or tractor pairing."
        )

    ballast_rear_kg, rear_ballast_problem = rear_ballast_required_kg(
        draft_n=draft_n,
        rear_axle_load_n=rd_n,
        ci_kpa=inputs.cone_index_kpa,
        rear_section_width_m=inputs.rear_section_width_m,
        rear_overall_diameter_m=inputs.rear_overall_diameter_m,
    )
    if rear_ballast_problem:
        warnings.append(rear_ballast_problem)

    sfc = power.sfc
    fuel_cons_l_per_ha = power.fuel_l_per_ha
    overall_pct = power.overall_pct

    # `fi` (Eq. 3.1's draft Fi) is texture-only again now that fi_factor() is
    # reverted, so it's also exactly Table 4.2's soil-condition reading -- no
    # separate variable needed, unlike the per-implement-Fi revision this undoes.
    envelope = result_envelope(
        slip=slip,
        net_traction_coefficient=mu,
        front_weight_utilization=kwf,
        fi=fi,
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
        # Which power the fuel figure is billed against. Explicit so a consumer
        # never has to infer it -- this basis changed once already.
        "fuel_basis": power.fuel_basis,
        "fuel_l_per_hour_pto_basis": power.fuel_lph_pto_basis,
        # Diagnostic: the spreadsheet's SFC x DBp basis, kept so a run stays
        # reconcilable cell-for-cell against the workbook. Feeds nothing.
        "fuel_l_per_hour_drawbar_basis": power.fuel_lph_drawbar_basis,
        "legacy_field_efficiency_raw": capacity.field_eff_raw_pct,
        # Diagnostics only. The headland time actually used carries an undocumented
        # factor of 2 that the spreadsheet's C71 does not have; both bases are
        # reported so the unresolved discrepancy is visible. See A15.
        "headland_turning_time_hours": capacity.total_turning_time_h,
        "headland_turning_time_single_pass_basis_hours": capacity.turning_time_single_pass_basis_h,
        "legacy_fi": fi,
        # Correctly-spelled key. `legacypy_over_d_ratio` (missing underscore) is
        # retained alongside it for one release so existing consumers keep working.
        "legacy_py_over_d_ratio": py_over_d,
        "legacypy_over_d_ratio": py_over_d,
        "legacy_turning_time_seconds": turning_time_s,
        "legacy_number_of_turns": number_turns,
        "legacy_front_axle_load_n": fd_n,
        "legacy_rear_axle_load_n": rd_n,
        "legacy_mobility_number_rear": bnr,
        "legacy_mobility_number_front": bnf,
        "legacy_gross_traction_ratio": mu_g,
        # Gross traction ratio developed AT the operating slip -- the denominator
        # Eq. 3.2 actually calls for. Reported so the TE figure is checkable.
        "gross_traction_at_slip": gross_traction_at_slip(bnr, slip / 100.0, mu_g=mu_g),
        # TE divided by the gross traction ratio developed AT the operating slip --
        # the engine's former primary. Diagnostic ONLY; the specification's Eq. (3.2)
        # divides by the envelope. See "RESOLVED: tractive-efficiency denominator".
        "traction_efficiency_at_slip_percent": traction_efficiency_at_slip_pct(
            mu, mu_g, slip / 100.0, bn_rear=bnr
        ),
        # Retained for compatibility: now identical to the headline
        # `traction_efficiency`, since the envelope IS the specified basis.
        "traction_efficiency_reference_basis": traction_efficiency_envelope_percent(
            mu, mu_g, slip / 100.0
        ),
        "slip_stepped": slip_solution.stepped_slip_pct,
        # The solver schedule, reported so a non-converged run is self-explaining:
        # the cap is where the search gave up, not a predicted operating slip.
        "slip_assumed_start_pct": SLIP_INITIAL_PCT,
        "slip_limit_pct": MAX_SLIP_PCT,
        "slip_hit_limit": not slip_solution.converged,
        # Front ballast fitted to keep a front-lifting combination answerable.
        # Non-zero means the figures above are conditional on carrying it.
        "stabilising_front_ballast_kg": axles.stabilising_ballast_kg,
        "infeasible_without_ballast": axles.infeasible_without_ballast,
        "calculation_mode": "dss_spec_v1",
    }
