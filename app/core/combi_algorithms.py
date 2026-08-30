"""Combination-tillage tractor performance engine.

Implements Sections 4 ("When combi tillage implement is used" - passive-passive)
and 5 ("CASE II - Active + Passive combination") of the DSS specification
document (docs/Simulation for DSS _Sahid.docx).

Both sections explicitly reuse the single-implement tractive-performance,
ballast, and power/fuel sub-models of Section 3 "with D(Total)/D(eff) in place
of D" -- this module therefore builds on top of app.core.legacy_algorithms
rather than duplicating that math, and only implements what is genuinely new:
combined/effective draft, the combination-specific axle-load moment balances,
and (for the active-passive case) the PTO rotor sub-model.

Two combining rules are needed that the document states in words but does not
give as a symbol-for-symbol formula (Section 4's Rr equation sums W1*X1+W2*X2
directly rather than using a single Wm*Xcgi term, and Section 3's ballast
equations -- explicitly reused "as-is" per the document -- are written in
terms of a single Wm/Xcgi/Py). These are documented at each call site rather
than silently assumed:

- Effective combined implement CG distance for the (reused) ballast equations:
  Xcgi_eff = (W1*X1 + W2*X2) / (W1+W2), i.e. the weight-weighted combined CG
  -- consistent with the first-moment sum the Rr equation itself uses.
- Combined vertical soil reaction Py = ratio1*D1 + ratio2*D2, i.e. each tool's
  own Py/D ratio (Section 3.3 table) applied to its own draft, then summed --
  consistent with Py being, physically, the sum of each tool's own vertical
  soil reaction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from app.core.constants import (
    GRAVITY,
    KI_RANGE,
    ROTOR_EFFICIENCY_RANGE,
)
from app.core.dss_shared import (
    draft_force_n,
    field_capacity,
    geometry_terms,
    power_and_fuel,
    require_positive,
    result_envelope,
    safe_div,
)
from app.core.legacy_algorithms import (
    draft_width_parameter,
    gross_traction_at_slip,
    resolve_axle_loads,
    SlipSolution,
    _engine_torque_warnings,
    engine_torque_limited_pull_n,
    fi_factor,
    py_over_d_ratio,
    front_ballast_required_kg,
    rear_ballast_required_kg,
    solve_slip,
    traction_efficiency_at_slip_pct,
    traction_efficiency_envelope_percent,
    traction_efficiency_percent,
    wheel_response,
)
from app.core.engineering_validation import clamp
from app.models.enums import ImplementType, SoilTexture

__all__ = [
    "PassiveToolInputs",
    "PassivePassiveInputs",
    "ActiveRotorInputs",
    "ActivePassiveInputs",
    "mobility_number_passive_passive",
    "rotor_mechanical_power_kw",
    "rotor_equivalent_force_n",
    "calculate_passive_passive_performance",
    "calculate_active_passive_performance",
]


def mobility_number_passive_passive(
    ci_kpa: float, section_width_m: float, overall_diameter_m: float, wheel_load_n: float
) -> float:
    """The Section 4 wheel-numeric expression `Bn'`, transcribed exactly:

        Bn' = (b*d/W) * sqrt( CI / (W/(b*d)) ) * (1 + b/(2*d))

    ⚠️ **This is not a valid wheel numeric and does not drive any result.** It is
    retained only so the source expression stays exercised and auditable; the
    engine reports its value as the diagnostic `dss_section4_wheel_numeric`.

    Why it is not used
    ------------------
    A wheel numeric is dimensionless by definition. This expression is not::

        b*d / W        ->  m^2 / kN     NOT dimensionless
        CI / (W/(b*d)) ->  kPa / kPa    dimensionless
        1 + b/(2*d)    ->  dimensionless

    so the product carries units of m^2/kN. The consequence is measurable: it
    scales as ``W**-1.5`` where a dimensionless group must scale as ``1/W``, and
    it falls two orders of magnitude below the 5-80 band the Brixius-form
    traction equations that consume it assume. At a realistic 6.9 kN wheel load
    it evaluates to ~0.64, which drives the net traction coefficient negative
    (mu ~ -1.63 at 15% slip) so no pull develops at any slip.

    What is used instead
    --------------------
    `legacy_algorithms.mobility_number` -- `Bn = CI*b*d/Wd`, the Section 3 model,
    evaluated at the combination's own rear axle load. That is the only
    dimensionally valid wheel numeric the specification defines, it is what the
    Section 4 prose calls for ("the tractive-performance sub-model of Section
    3.4.5 applies unchanged, with DTotal in place of D"), and it is exactly the
    term appearing *under* this expression's own square root.

    Physically this is also the only defensible reading: the wheel numeric
    describes the tyre-soil-load interaction of the driven wheel and cannot
    depend on how many implements are being towed. The implement configuration
    already enters through the axle load `W`, which changes with `DTotal`.

    Units: `CI` in kPa, `b`/`d` in m, `W` converted from N to kN here.
    """
    if wheel_load_n <= 0:
        raise ValueError("Invalid mobility number: wheel load must be positive")
    if overall_diameter_m <= 0 or section_width_m <= 0:
        raise ValueError("Invalid mobility number: tire dimensions must be positive")

    wheel_load_kn = wheel_load_n / 1000.0
    contact_area_m2 = section_width_m * overall_diameter_m  # b*d
    mean_ground_pressure_kpa = wheel_load_kn / contact_area_m2  # W/(b*d)

    return (
        (contact_area_m2 / wheel_load_kn)
        * math.sqrt(ci_kpa / mean_ground_pressure_kpa)
        * (1.0 + section_width_m / (2.0 * overall_diameter_m))
    )


@dataclass(frozen=True)
class PassiveToolInputs:
    """One towed (passive) implement's own parameters (mirrors LegacyInputs' implement block)."""

    implement_type: ImplementType
    width_m: float
    weight_kg: float
    cg_distance_from_hitch_m: float  # X1 or X2
    asae_param_a: float
    asae_param_b: float
    asae_param_c: float
    #: Number of ground-engaging tools -- Eq. 3.1's `W` for per-tool implement
    #: classes (see `constants.DRAFT_WIDTH_IS_TOOL_COUNT`); ignored for the rest.
    number_of_tools: Optional[int] = None
    #: Py/D for this tool. Both reference implementations carry the ratio per
    #: implement rather than per type; None falls back to the type table.
    vertical_horizontal_ratio: Optional[float] = None


@dataclass(frozen=True)
class ActiveRotorInputs:
    """PTO-driven active rotor unit's own parameters."""

    weight_kg: float
    cg_distance_from_hitch_m: float  # Xa
    mechanical_resistance_n: float  # Da: frame/bearing resistance, independent of thrust
    rotor_efficiency: float  # eta_r, user-selectable 0.25-0.45 per DSS doc
    pto_power_draw_kw: float  # P_PTO delivered to the rotor
    rotor_speed_rpm: float  # N
    dynamic_vertical_force_n: float = 0.0  # Fv: doc introduces this term without a derivation
    # formula (it is implicitly an empirical/measured rotor-spec quantity); defaults to 0.


@dataclass(frozen=True)
class _TractorCommon:
    pto_power_kw: float
    wheelbase_m: float
    front_axle_weight_kg: float
    rear_axle_weight_kg: float
    hitch_distance_from_rear_m: float
    cg_distance_from_rear_m: float
    transmission_efficiency_pct: float
    power_reserve_pct: float
    front_rolling_radius_m: float
    rear_rolling_radius_m: float
    front_overall_diameter_m: float
    rear_overall_diameter_m: float
    front_section_width_m: float
    rear_section_width_m: float
    soil_texture: SoilTexture
    cone_index_kpa: float
    depth_cm: float
    speed_kmh: float
    field_area_ha: float
    field_width_m: float
    # Optional: enables the DSS Eq. 3.4 engine-torque pull limit (Pet) diagnostic.
    max_engine_torque_nm: Optional[float] = None


@dataclass(frozen=True)
class PassivePassiveInputs(_TractorCommon):
    tool_1: PassiveToolInputs = None  # type: ignore[assignment]
    tool_2: PassiveToolInputs = None  # type: ignore[assignment]
    interaction_coefficient: float = 0.0  # ki, 0.00-0.25 (DSS Section 4.2)


@dataclass(frozen=True)
class ActivePassiveInputs(_TractorCommon):
    passive_tool: PassiveToolInputs = None  # type: ignore[assignment]
    rotor: ActiveRotorInputs = None  # type: ignore[assignment]


def _combined_axle_load_shared(
    *,
    tractor_weight_n: float,
    cg_distance_from_rear_m: float,
    combined_cg_from_hitch_m: float,
    py_n: float,
    hitch_distance_from_rear_m: float,
    draft_n: float,
    yd_m: float,
    er_m: float,
    ef_m: float,
    wheelbase_m: float,
    total_implement_weight_n: float,
    extra_rear_load_n: float = 0.0,
) -> tuple[float, float]:
    """Dynamic axle loads for a combination implement, from DSS Eq. 3.5/3.6.

        Rr = [ (Wi+Py)(Xcgi_eff + Hd + L + ef) + Wt(L + ef - Xcgt) - D*Yd ] / (L - er + ef)
             + extra_rear_load_n            <- Section 5's MPTO/L + Fv
        Rf = (Wt + Wi + Py) - Rr

    `Wi` is the combined implement weight and `Xcgi_eff` its weight-weighted CG
    offset from the hitch. Because `sum(Wn*Xn) == Wi*Xcgi_eff` by definition of a
    weight-weighted centroid, the individual tool moments are represented
    **exactly** -- no approximation is introduced by collapsing the tools.

    Why Section 3's balance rather than the Section 4/5 form
    -------------------------------------------------------
    The combi sections state their own axle balance as

        Rr = [Wt*Xcgt + sum(Wn*Xn) + Py*Hd + D*Yd] / L

    and describe it as Section 3's equation in "simplified-eccentricity form".
    It is not. Re-deriving the balance from the combi free-body diagram (moments
    about the front-axle contact) reproduces Eq. 3.5 exactly, and shows the combi
    form uses the wrong moment arms -- `Xcgt` where the geometry requires
    `L - Xcgt`, `Xn` where it requires `L + Hd + Xn`, `Hd` where it requires
    `L + Hd` -- and the opposite sign on the draft term. Reduced to a single tool
    the two disagree by ~21% on rear-axle load for identical inputs, so they
    cannot both be right, and Section 3 is the validated conventional path.

    Using Eq. 3.5 here also restores the wheel eccentricity terms the combi form
    dropped, and makes a combination reduce *exactly* to the single-implement
    result when the second tool contributes nothing.
    """
    denominator = wheelbase_m - er_m + ef_m
    if denominator == 0:
        raise ValueError("Invalid geometry: wheelbase - er + ef equals zero")

    rr = (
        (total_implement_weight_n + py_n)
        * (combined_cg_from_hitch_m + hitch_distance_from_rear_m + wheelbase_m + ef_m)
        + tractor_weight_n * (wheelbase_m + ef_m - cg_distance_from_rear_m)
        - draft_n * yd_m
    ) / denominator + extra_rear_load_n
    rf = (tractor_weight_n + total_implement_weight_n + py_n) - rr
    return rr, rf


def rotor_mechanical_power_kw(torque_nm: float, speed_rpm: float) -> float:
    """Rotor mechanical power Pr = 2*pi*N*T/60 (DSS Section 5.9), in kW.

    Diagnostic only: the document presents this, and the equivalent force Fr
    below, as "an alternate (torque-based) route to the same thrust term Ta"
    (Eq. 5.2) -- a cross-check meant for an *independently measured* rotor
    shaft torque, used to back-calculate rotor_efficiency if it disagrees with
    Ta. There is no measured-torque input in this engine yet, so callers that
    pass the PTO reaction moment (MPTO = 9550*PPTO/N) as torque_nm will get a
    value that trivially reproduces PPTO (T was itself derived from PPTO and
    N via the same 9550 relation) -- an internal-consistency identity, not new
    information. Genuine diagnostic value requires a real measured torque.
    """
    return (2.0 * math.pi * speed_rpm * torque_nm / 60.0) / 1000.0


def rotor_equivalent_force_n(rotor_power_kw: float, speed_kmh: float) -> float:
    """Rotor power expressed as an equivalent horizontal force Fr = Pr/V (DSS Section 5.9)."""
    speed_mps = speed_kmh / 3.6
    return (rotor_power_kw * 1000.0) / speed_mps


def _engine_torque_limit(
    inputs: _TractorCommon,
    *,
    wheels,
    rd_n: float,
    fd_n: float,
    draft_n: float,
    warnings: list,
) -> Optional[float]:
    """DSS Eq. 3.4 engine-torque pull limit (Pet), when engine torque is known.

    Diagnostic only -- the document never states that Pet caps the soil-limited
    pull, so it does not constrain the slip solution. Returns None when the
    tractor record carries no torque figure.
    """
    if inputs.max_engine_torque_nm is None or inputs.max_engine_torque_nm <= 0:
        return None
    pet_n = engine_torque_limited_pull_n(
        max_engine_torque_nm=inputs.max_engine_torque_nm,
        rear_rolling_radius_m=inputs.rear_rolling_radius_m,
        transmission_efficiency_pct=inputs.transmission_efficiency_pct,
        rho_r=wheels.rho_r,
        rho_f=wheels.rho_f,
        rear_axle_load_n=rd_n,
        front_axle_load_n=fd_n,
    )
    warnings.extend(_engine_torque_warnings(pet_n, draft_n))
    return pet_n


def _draft_for_tool(tool: PassiveToolInputs, *, soil_texture: SoilTexture, speed_kmh: float, depth_cm: float) -> float:
    """DSS Eq. 3.1 for one passive tool, via the shared kernel."""
    return draft_force_n(
        fi=fi_factor(tool.implement_type, soil_texture),
        asae_param_a=tool.asae_param_a,
        asae_param_b=tool.asae_param_b,
        asae_param_c=tool.asae_param_c,
        speed_kmh=speed_kmh,
        width_m=draft_width_parameter(
            tool.implement_type, tool.width_m, tool.number_of_tools
        ),
        depth_cm=depth_cm,
    )


def combined_draft_n(draft_1_n: float, draft_2_n: float, interaction_coefficient: float) -> float:
    """Combined draft of a passive-passive pair -- DSS Eq. 4.1 [DSS-EXACT]:

        DTotal = (1 - ki) * (D1 + D2),   ki in [0.00, 0.25]

    `DTotal` replaces `D` throughout the rest of the Section 4 chain: axle loads,
    the slip iteration's convergence target, R', both ballast equations, drawbar
    power and fuel.
    """
    low, high = KI_RANGE
    if not (low <= interaction_coefficient <= high):
        raise ValueError(
            "Interaction coefficient ki must be between {0:.2f} and {1:.2f}, got {2!r}".format(
                low, high, interaction_coefficient
            )
        )
    return (1.0 - interaction_coefficient) * (draft_1_n + draft_2_n)


def calculate_passive_passive_performance(inputs: PassivePassiveInputs) -> dict:
    require_positive("operating speed", inputs.speed_kmh)
    require_positive("field area", inputs.field_area_ha)
    require_positive("rated PTO power", inputs.pto_power_kw)
    require_positive("cone index", inputs.cone_index_kpa)
    require_positive("tillage depth", inputs.depth_cm)
    require_positive("wheelbase", inputs.wheelbase_m)

    t1, t2 = inputs.tool_1, inputs.tool_2
    if t1 is None or t2 is None:
        raise ValueError("Both tools are required for a passive-passive combination")

    d1 = _draft_for_tool(t1, soil_texture=inputs.soil_texture, speed_kmh=inputs.speed_kmh, depth_cm=inputs.depth_cm)
    d2 = _draft_for_tool(t2, soil_texture=inputs.soil_texture, speed_kmh=inputs.speed_kmh, depth_cm=inputs.depth_cm)
    d_total = combined_draft_n(d1, d2, inputs.interaction_coefficient)  # DSS Eq. 4.1

    tractor_weight_n = (inputs.front_axle_weight_kg + inputs.rear_axle_weight_kg) * GRAVITY
    w1_n = t1.weight_kg * GRAVITY
    w2_n = t2.weight_kg * GRAVITY
    wi_n = w1_n + w2_n
    # Combined vertical soil reaction: each tool's own Py/D ratio applied to its
    # own draft, then summed (see module docstring).
    py_n = (
        py_over_d_ratio(t1.implement_type, t1.vertical_horizontal_ratio) * d1
        + py_over_d_ratio(t2.implement_type, t2.vertical_horizontal_ratio) * d2
    )
    geometry = geometry_terms(
        depth_cm=inputs.depth_cm,
        rear_rolling_radius_m=inputs.rear_rolling_radius_m,
        front_rolling_radius_m=inputs.front_rolling_radius_m,
    )
    yd_m, er_m, ef_m = geometry.yd_m, geometry.er_m, geometry.ef_m

    # Weight-weighted CG of the pair, used identically by the axle balance and the
    # Section 3 ballast solvers so the whole chain sees one combined implement.
    moment_terms = w1_n * t1.cg_distance_from_hitch_m + w2_n * t2.cg_distance_from_hitch_m
    xcgi_eff = moment_terms / wi_n if wi_n > 0 else 0.0

    def _axle_loads_with_ballast(extra_n: float) -> "tuple[float, float]":
        """Both axle loads with `extra_n` N of front ballast, same combined balance."""
        return _combined_axle_load_shared(
            tractor_weight_n=tractor_weight_n + extra_n,
            cg_distance_from_rear_m=inputs.cg_distance_from_rear_m,
            combined_cg_from_hitch_m=xcgi_eff,
            py_n=py_n,
            hitch_distance_from_rear_m=inputs.hitch_distance_from_rear_m,
            draft_n=d_total,
            yd_m=yd_m,
            er_m=er_m,
            ef_m=ef_m,
            wheelbase_m=inputs.wheelbase_m,
            total_implement_weight_n=wi_n,
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
    # Driven-wheel numeric: the Section 3 `Bn = CI*b*d/Wd`, evaluated at this
    # combination's own rear axle load (W = Rr/2). The combination enters through
    # that load -- via DTotal -- not through a different wheel-numeric model.
    # See `mobility_number_passive_passive` for why the Section 4 `Bn'` cannot be
    # used here.
    slip_solution: SlipSolution = solve_slip(
        draft_n=d_total,
        rear_axle_load_n=rd_n,
        ci_kpa=inputs.cone_index_kpa,
        rear_section_width_m=inputs.rear_section_width_m,
        rear_overall_diameter_m=inputs.rear_overall_diameter_m,
    )
    slip = slip_solution.slip_pct
    if not slip_solution.converged:
        if slip_solution.traction_possible:
            warnings.append(
                "Slip iteration reached the 20% engineering limit before full draft convergence."
            )
            warnings.append(
                "Simulation retained bounded partial results from the last stable iteration."
            )
        else:
            # Distinct cause: the soil cannot generate net pull at any trial slip,
            # so raising slip further will not help.
            warnings.append(
                "No net traction develops at any trial slip: motion resistance exceeds the "
                "gross traction this soil and wheel load can produce. Increase rear-axle "
                "load or work firmer soil."
            )

    te_pct = clamp(
        traction_efficiency_percent(
            slip_solution.mu,
            slip_solution.mu_g,
            slip / 100.0,
            bn_rear=slip_solution.bn_rear,
        ),
        0.0,
        100.0,
    )
    if te_pct <= 0:
        raise ValueError("Either decrease depth or speed of operation, since slip is very low")

    kwf = fd_n / tractor_weight_n
    kwr = rd_n / tractor_weight_n
    # Front wheel numeric / rolling resistance (rho_f) uses the *Section 3* Bn,
    # not Bn'. DSS image8 defines rho_f from Bn = CI*b*d/Wf, and Section 4 never
    # redefines the front wheel: its Bn' text is entirely about the wheel
    # numeric / gross traction / coefficient of traction at trial slip, i.e. the
    # driven rear wheel. Bn' therefore stays scoped to solve_slip and the R'
    # ballast fixed point below.
    wheels = wheel_response(
        ci_kpa=inputs.cone_index_kpa,
        front_section_width_m=inputs.front_section_width_m,
        front_overall_diameter_m=inputs.front_overall_diameter_m,
        front_axle_load_n=fd_n,
        bn_rear=slip_solution.bn_rear,
        slip_fraction=slip / 100.0,
    )
    bnf, mr_ratio = wheels.bn_front, wheels.mr_ratio
    pet_n = _engine_torque_limit(inputs, wheels=wheels, rd_n=rd_n, fd_n=fd_n, draft_n=d_total, warnings=warnings)

    working_width_m = max(t1.width_m, t2.width_m)
    capacity = field_capacity(
        speed_kmh=inputs.speed_kmh,
        width_m=working_width_m,
        field_area_ha=inputs.field_area_ha,
        field_width_m=inputs.field_width_m,
    )
    fc_th, fc_ac = capacity.fc_th, capacity.fc_ac
    field_eff_pct, total_time_h = capacity.field_eff_pct, capacity.total_time_h

    # DSS Eq. 3.4/3.11/3.12 with DTotal in place of D.
    power = power_and_fuel(
        draft_n=d_total,
        speed_kmh=inputs.speed_kmh,
        te_pct=te_pct,
        transmission_efficiency_pct=inputs.transmission_efficiency_pct,
        power_reserve_pct=inputs.power_reserve_pct,
        pto_power_kw=inputs.pto_power_kw,
        fc_th=fc_th,
        fc_ac=fc_ac,
    )
    pdb_kw, ptr_kw, pused_pct = power.pdb_kw, power.ptr_kw, power.put_pct

    # Ballast uses the same two solvers as every other mode; the front one is fed
    # this combination's own Eq. 3.5 balance, re-solved with the trial ballast.
    ballast_front_kg, front_ballast_feasible = front_ballast_required_kg(
        tractor_weight_n=tractor_weight_n,
        rf_for_added_weight_n=lambda extra_n: _combined_axle_load_shared(
            tractor_weight_n=tractor_weight_n + extra_n,
            cg_distance_from_rear_m=inputs.cg_distance_from_rear_m,
            combined_cg_from_hitch_m=xcgi_eff,
            py_n=py_n,
            hitch_distance_from_rear_m=inputs.hitch_distance_from_rear_m,
            draft_n=d_total,
            yd_m=yd_m,
            er_m=er_m,
            ef_m=ef_m,
            wheelbase_m=inputs.wheelbase_m,
            total_implement_weight_n=wi_n,
        )[1],
    )
    if not front_ballast_feasible:
        warnings.append(
            "Front-axle weight-utilization target (Kwef=0.20) cannot be reached with any "
            "amount of front ballast for this tractor/implement combination."
        )

    ballast_rear_kg, rear_ballast_problem = rear_ballast_required_kg(
        draft_n=d_total,
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

    envelope = result_envelope(
        slip=slip,
        draft_n=d_total,
        te_pct=te_pct,
        fuel_l_per_ha=fuel_cons_l_per_ha,
        put_pct=pused_pct,
        field_eff_pct=field_eff_pct,
        converged=slip_solution.converged,
    )
    load_status = envelope.load_status
    recommendation = envelope.recommendations
    simulation_status = envelope.status
    confidence = envelope.confidence
    status_message = envelope.status_message

    return {
        "combination_type": "passive_passive",
        "draft_1": d1,
        "draft_2": d2,
        "interaction_coefficient": inputs.interaction_coefficient,
        "draft_force": d_total,
        "drawbar_power": pdb_kw,
        "slip": slip,
        "coefficient_net_traction": slip_solution.mu,
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
        "converged": slip_solution.converged,
        # Front ballast fitted to keep a front-lifting combination answerable;
        # non-zero means the figures above are conditional on carrying it.
        "stabilising_front_ballast_kg": axles.stabilising_ballast_kg,
        "infeasible_without_ballast": axles.infeasible_without_ballast,
        "engine_torque_limited_pull": pet_n,
        "fuel_l_per_hour": power.fuel_lph,
        "fuel_l_per_hour_pto_basis": power.fuel_lph_pto_basis,
        "legacy_field_efficiency_raw": capacity.field_eff_raw_pct,
        # Diagnostics only. The headland time actually used carries an undocumented
        # factor of 2 that the spreadsheet's C71 does not have; both bases are
        # reported so the unresolved discrepancy is visible. See A15.
        "headland_turning_time_hours": capacity.total_turning_time_h,
        "headland_turning_time_single_pass_basis_hours": capacity.turning_time_single_pass_basis_h,
        "legacy_front_axle_load_n": fd_n,
        "legacy_rear_axle_load_n": rd_n,
        "legacy_mobility_number_rear": slip_solution.bn_rear,
        "legacy_mobility_number_front": bnf,
        "legacy_gross_traction_ratio": slip_solution.mu_g,
        # Gross traction ratio developed AT the operating slip -- the denominator
        # Eq. 3.2 actually calls for. Reported so the TE figure is checkable.
        "gross_traction_at_slip": gross_traction_at_slip(slip_solution.bn_rear, slip / 100.0, mu_g=slip_solution.mu_g),
        # TE divided by the gross traction ratio developed AT the operating slip --
        # the engine's former primary. Diagnostic ONLY; the specification's Eq. (3.2)
        # divides by the envelope. See "RESOLVED: tractive-efficiency denominator".
        "traction_efficiency_at_slip_percent": traction_efficiency_at_slip_pct(
            slip_solution.mu,
            slip_solution.mu_g,
            slip / 100.0,
            bn_rear=slip_solution.bn_rear,
        ),
        # Retained for compatibility: now identical to the headline
        # `traction_efficiency`, since the envelope IS the specified basis.
        "traction_efficiency_reference_basis": traction_efficiency_envelope_percent(
            slip_solution.mu, slip_solution.mu_g, slip / 100.0
        ),
        "motion_resistance_ratio": mr_ratio,
        "motion_resistance": mr_ratio,
        # Audit trail only: what the Section 4 expression would have produced at the
        # same wheel load. Dimensionally invalid (m^2/kN) and drives nothing --
        # see mobility_number_passive_passive.
        "dss_section4_wheel_numeric": mobility_number_passive_passive(
            inputs.cone_index_kpa,
            inputs.rear_section_width_m,
            inputs.rear_overall_diameter_m,
            rd_n / 2.0,
        ),
        "calculation_mode": "dss_spec_v1_passive_passive",
    }


def rotor_thrust_n(*, rotor_efficiency: float, pto_power_draw_kw: float, speed_kmh: float) -> float:
    """Forward thrust developed by the PTO-driven rotor -- DSS Eq. 5.2 [DSS-EXACT]:

        Ta = eta_r * PPTO / V

    Units: `PPTO` in **W** here and `V` in m/s, giving Ta in N. (Note the
    document uses PPTO in **kW** in Eq. 5.4's 9550 relation -- the two equations
    genuinely take different units for the same symbol.)
    """
    low, high = ROTOR_EFFICIENCY_RANGE
    if not (low <= rotor_efficiency <= high):
        raise ValueError(
            "Rotor efficiency must be between {0:.2f} and {1:.2f} per the DSS "
            "specification, got {2!r}".format(low, high, rotor_efficiency)
        )
    speed_mps = require_positive("operating speed", speed_kmh) / 3.6
    return safe_div("rotor thrust", rotor_efficiency * (pto_power_draw_kw * 1000.0), speed_mps)


def effective_draft_n(*, passive_draft_n: float, rotor_mechanical_resistance_n: float, thrust_n: float) -> float:
    """Effective draft of the active-passive combination -- DSS Eq. 5.1/5.3 [DSS-EXACT]:

        Deff = Dp + Da - Ta = Dp + Da - eta_r*PPTO/V

    `Deff` replaces `D` throughout the rest of the Section 5 chain.
    """
    d_eff = passive_draft_n + rotor_mechanical_resistance_n - thrust_n
    if d_eff <= 0:
        raise ValueError(
            "Effective draft (Deff) is non-positive: the rotor's forward thrust "
            "({0:.0f} N) exceeds the passive tool's draft plus the rotor's own "
            "mechanical drag ({1:.0f} N = {2:.0f} + {3:.0f}). The combination would "
            "push the tractor rather than need pull from it, so the traction model "
            "does not apply. Reduce the rotor's PTO power draw or efficiency, raise "
            "the operating speed, or pair the rotor with a heavier-draft tool.".format(
                thrust_n,
                passive_draft_n + rotor_mechanical_resistance_n,
                passive_draft_n,
                rotor_mechanical_resistance_n,
            )
        )
    return d_eff


def pto_reaction_moment_nm(*, pto_power_draw_kw: float, rotor_speed_rpm: float) -> float:
    """PTO torque reaction moment -- DSS Eq. 5.4 [DSS-EXACT]:

        MPTO = 9550 * PPTO / N

    Units: `PPTO` in **kW** (the 9550 constant fixes this), `N` in rpm, MPTO in N-m.
    """
    require_positive("rotor speed", rotor_speed_rpm)
    return safe_div("PTO reaction moment", 9550.0 * pto_power_draw_kw, rotor_speed_rpm)


def pto_equivalent_rear_load_n(*, pto_reaction_moment: float, wheelbase_m: float) -> float:
    """PTO reaction moment as an equivalent rear-axle load -- DSS Eq. 5.5 [DSS-EXACT]:

        Weq = MPTO / L,  and  Rr* = Rr + Weq  (Eq. 5.6)
    """
    require_positive("wheelbase", wheelbase_m)
    return safe_div("PTO equivalent rear load", pto_reaction_moment, wheelbase_m)


def calculate_active_passive_performance(inputs: ActivePassiveInputs) -> dict:
    require_positive("operating speed", inputs.speed_kmh)
    require_positive("field area", inputs.field_area_ha)
    require_positive("rated PTO power", inputs.pto_power_kw)
    require_positive("cone index", inputs.cone_index_kpa)
    require_positive("tillage depth", inputs.depth_cm)
    require_positive("wheelbase", inputs.wheelbase_m)

    passive, rotor = inputs.passive_tool, inputs.rotor
    if passive is None or rotor is None:
        raise ValueError("Both a passive tool and an active rotor are required")

    dp = _draft_for_tool(
        passive, soil_texture=inputs.soil_texture, speed_kmh=inputs.speed_kmh, depth_cm=inputs.depth_cm
    )
    da = rotor.mechanical_resistance_n

    ta_n = rotor_thrust_n(  # DSS Eq. 5.2
        rotor_efficiency=rotor.rotor_efficiency,
        pto_power_draw_kw=rotor.pto_power_draw_kw,
        speed_kmh=inputs.speed_kmh,
    )
    d_eff = effective_draft_n(  # DSS Eq. 5.1/5.3
        passive_draft_n=dp, rotor_mechanical_resistance_n=da, thrust_n=ta_n
    )

    mpto_nm = pto_reaction_moment_nm(  # DSS Eq. 5.4
        pto_power_draw_kw=rotor.pto_power_draw_kw, rotor_speed_rpm=rotor.rotor_speed_rpm
    )
    weq_n = pto_equivalent_rear_load_n(  # DSS Eq. 5.5
        pto_reaction_moment=mpto_nm, wheelbase_m=inputs.wheelbase_m
    )

    # Rotor power / equivalent force diagnostics (DSS Section 5.9, Pr=2*pi*N*T/60,
    # Fr=Pr/V) -- reported for cross-check only, using T=MPTO (the shaft torque
    # already derived above). See rotor_mechanical_power_kw's docstring: since
    # MPTO was itself derived from pto_power_draw_kw and rotor_speed_rpm via the
    # same 9550 relation, this reproduces rotor_thrust/pto_power_draw_kw by
    # construction (an internal-consistency identity) rather than adding
    # independent information -- a real cross-check needs a measured torque,
    # which this engine does not currently collect as an input.
    rotor_power_kw = rotor_mechanical_power_kw(mpto_nm, rotor.rotor_speed_rpm)
    rotor_force_n = rotor_equivalent_force_n(rotor_power_kw, inputs.speed_kmh)

    tractor_weight_n = (inputs.front_axle_weight_kg + inputs.rear_axle_weight_kg) * GRAVITY
    wp_n = passive.weight_kg * GRAVITY
    wa_n = rotor.weight_kg * GRAVITY
    py_n = py_over_d_ratio(passive.implement_type, passive.vertical_horizontal_ratio) * dp
    geometry = geometry_terms(
        depth_cm=inputs.depth_cm,
        rear_rolling_radius_m=inputs.rear_rolling_radius_m,
        front_rolling_radius_m=inputs.front_rolling_radius_m,
    )
    yd_m, er_m, ef_m = geometry.yd_m, geometry.er_m, geometry.ef_m

    # Weight-weighted CG of the passive tool + rotor unit, treated as one
    # combination implement by the shared Section 3 balance.
    wi_n = wp_n + wa_n
    moment_terms = wp_n * passive.cg_distance_from_hitch_m + wa_n * rotor.cg_distance_from_hitch_m
    xcgi_eff = moment_terms / wi_n if wi_n > 0 else 0.0

    def _axle_loads_with_ballast(extra_n: float) -> "tuple[float, float]":
        """Both axle loads with `extra_n` N of front ballast, same combined balance."""
        return _combined_axle_load_shared(
            tractor_weight_n=tractor_weight_n + extra_n,
            cg_distance_from_rear_m=inputs.cg_distance_from_rear_m,
            combined_cg_from_hitch_m=xcgi_eff,
            py_n=py_n,
            hitch_distance_from_rear_m=inputs.hitch_distance_from_rear_m,
            draft_n=d_eff,
            yd_m=yd_m,
            er_m=er_m,
            ef_m=ef_m,
            wheelbase_m=inputs.wheelbase_m,
            total_implement_weight_n=wi_n,
            extra_rear_load_n=weq_n + rotor.dynamic_vertical_force_n,  # DSS Eq. 5.6/5.7a
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
    # Tractive performance uses only Deff -- the rotor's own thrust/torque terms
    # already left the traction sub-model via Deff and Rr (DSS Section 5.9).
    # Rear mobility number reuses the single-tool Bn (see the equivalent, more
    # detailed comment in calculate_passive_passive_performance): the document
    # gives no distinct Bn' formula for the active-passive case, only for
    # Section 4, so there is no analogous contradiction to resolve here.
    slip_solution: SlipSolution = solve_slip(
        draft_n=d_eff,
        rear_axle_load_n=rd_n,
        ci_kpa=inputs.cone_index_kpa,
        rear_section_width_m=inputs.rear_section_width_m,
        rear_overall_diameter_m=inputs.rear_overall_diameter_m,
    )
    slip = slip_solution.slip_pct
    if not slip_solution.converged:
        warnings.append("Slip iteration reached the 20% engineering limit before full draft convergence.")
        warnings.append("Simulation retained bounded partial results from the last stable iteration.")

    te_pct = clamp(
        traction_efficiency_percent(
            slip_solution.mu,
            slip_solution.mu_g,
            slip / 100.0,
            bn_rear=slip_solution.bn_rear,
        ),
        0.0,
        100.0,
    )
    if te_pct <= 0:
        raise ValueError("Either decrease depth or speed of operation, since slip is very low")

    kwf = fd_n / tractor_weight_n
    kwr = rd_n / tractor_weight_n
    # Front wheel numeric / rolling resistance (rho_f) uses the Section 3 Bn with
    # the front load and front tire, per DSS image8. Section 5 defines no Bn' of
    # its own, so both wheels use the Section 3 model here.
    wheels = wheel_response(
        ci_kpa=inputs.cone_index_kpa,
        front_section_width_m=inputs.front_section_width_m,
        front_overall_diameter_m=inputs.front_overall_diameter_m,
        front_axle_load_n=fd_n,
        bn_rear=slip_solution.bn_rear,
        slip_fraction=slip / 100.0,
    )
    bnf, mr_ratio = wheels.bn_front, wheels.mr_ratio
    pet_n = _engine_torque_limit(inputs, wheels=wheels, rd_n=rd_n, fd_n=fd_n, draft_n=d_eff, warnings=warnings)

    capacity = field_capacity(
        speed_kmh=inputs.speed_kmh,
        width_m=passive.width_m,
        field_area_ha=inputs.field_area_ha,
        field_width_m=inputs.field_width_m,
    )
    fc_th, fc_ac = capacity.fc_th, capacity.fc_ac
    field_eff_pct, total_time_h = capacity.field_eff_pct, capacity.total_time_h

    # DSS Eq. 3.4/3.11 with Deff in place of D, and Section 5.9's Put/Xeff, which
    # must include the directly-delivered PTO power drawn by the rotor as well as
    # the drawbar-equivalent power (both come from the same engine).
    power = power_and_fuel(
        draft_n=d_eff,
        speed_kmh=inputs.speed_kmh,
        te_pct=te_pct,
        transmission_efficiency_pct=inputs.transmission_efficiency_pct,
        power_reserve_pct=inputs.power_reserve_pct,
        pto_power_kw=inputs.pto_power_kw,
        fc_th=fc_th,
        fc_ac=fc_ac,
        extra_pto_kw=rotor.pto_power_draw_kw,
    )
    pdb_kw, ptr_kw, pused_pct = power.pdb_kw, power.ptr_kw, power.put_pct
    x_eff = power.x_fraction

    # Ballast uses the same two shared solvers as the other modes. DSS Eq. 5.12/5.13
    # (Rreq = Deff/mu(S), BRr = Rreq - Rr) is exactly what `rear_ballast_required_kg`
    # now computes, differing only in the target slip -- Section 5 sizes at the
    # *solved* slip rather than the 15% target, which the reference implementation
    # also does -- so it is passed as `target_slip_fraction` instead of being
    # re-implemented here. Eq. 5.10/5.11's closed form is likewise subsumed by the
    # shared front solver, which re-solves this mode's own axle balance.
    ballast_front_kg, front_ballast_feasible = front_ballast_required_kg(
        tractor_weight_n=tractor_weight_n,
        rf_for_added_weight_n=lambda extra_n: _combined_axle_load_shared(
            tractor_weight_n=tractor_weight_n + extra_n,
            cg_distance_from_rear_m=inputs.cg_distance_from_rear_m,
            combined_cg_from_hitch_m=xcgi_eff,
            py_n=py_n,
            hitch_distance_from_rear_m=inputs.hitch_distance_from_rear_m,
            draft_n=d_eff,
            yd_m=yd_m,
            er_m=er_m,
            ef_m=ef_m,
            wheelbase_m=inputs.wheelbase_m,
            total_implement_weight_n=wi_n,
            extra_rear_load_n=weq_n + rotor.dynamic_vertical_force_n,
        )[1],
    )
    if not front_ballast_feasible:
        warnings.append(
            "Front-axle weight-utilization target (Kwef=0.20) cannot be reached with any "
            "amount of front ballast for this tractor/implement combination."
        )

    ballast_rear_kg, rear_ballast_problem = rear_ballast_required_kg(
        draft_n=d_eff,
        rear_axle_load_n=rd_n,
        ci_kpa=inputs.cone_index_kpa,
        rear_section_width_m=inputs.rear_section_width_m,
        rear_overall_diameter_m=inputs.rear_overall_diameter_m,
        target_slip_fraction=slip / 100.0,
    )
    if rear_ballast_problem:
        warnings.append(rear_ballast_problem)
    elif ballast_rear_kg == 0.0:
        warnings.append(
            "Rear ballast is not required: the active rotor's thrust and PTO reaction "
            "moment already supply sufficient rear-axle loading."
        )

    # DSS Section 5.9: FCcombi uses Xeff = (Ptr + PPTO)/Pt, already computed above.
    sfc = power.sfc
    fuel_cons_l_per_ha = power.fuel_l_per_ha
    overall_pct = power.overall_pct

    envelope = result_envelope(
        slip=slip,
        draft_n=d_eff,
        te_pct=te_pct,
        fuel_l_per_ha=fuel_cons_l_per_ha,
        put_pct=pused_pct,
        field_eff_pct=field_eff_pct,
        converged=slip_solution.converged,
    )
    load_status = envelope.load_status
    recommendation = envelope.recommendations
    simulation_status = envelope.status
    confidence = envelope.confidence
    status_message = envelope.status_message

    return {
        "combination_type": "active_passive",
        "draft_passive": dp,
        "draft_active_mechanical": da,
        "rotor_thrust": ta_n,
        "pto_reaction_moment": mpto_nm,
        "pto_equivalent_rear_load": weq_n,
        "draft_force": d_eff,
        "drawbar_power": pdb_kw,
        "slip": slip,
        "coefficient_net_traction": slip_solution.mu,
        "traction_efficiency": te_pct,
        "front_weight_utilization": kwf,
        "rear_weight_utilization": kwr,
        "required_pto_power": ptr_kw,
        "rotor_pto_power": rotor.pto_power_draw_kw,
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
        "converged": slip_solution.converged,
        # Front ballast fitted to keep a front-lifting combination answerable;
        # non-zero means the figures above are conditional on carrying it.
        "stabilising_front_ballast_kg": axles.stabilising_ballast_kg,
        "infeasible_without_ballast": axles.infeasible_without_ballast,
        "engine_torque_limited_pull": pet_n,
        "pto_power_fraction_effective": x_eff,
        "fuel_l_per_hour": power.fuel_lph,
        "fuel_l_per_hour_pto_basis": power.fuel_lph_pto_basis,
        "legacy_field_efficiency_raw": capacity.field_eff_raw_pct,
        # Diagnostics only. The headland time actually used carries an undocumented
        # factor of 2 that the spreadsheet's C71 does not have; both bases are
        # reported so the unresolved discrepancy is visible. See A15.
        "headland_turning_time_hours": capacity.total_turning_time_h,
        "headland_turning_time_single_pass_basis_hours": capacity.turning_time_single_pass_basis_h,
        "legacy_front_axle_load_n": fd_n,
        "legacy_rear_axle_load_n": rd_n,
        "legacy_mobility_number_rear": slip_solution.bn_rear,
        "legacy_mobility_number_front": bnf,
        "legacy_gross_traction_ratio": slip_solution.mu_g,
        # Gross traction ratio developed AT the operating slip -- the denominator
        # Eq. 3.2 actually calls for. Reported so the TE figure is checkable.
        "gross_traction_at_slip": gross_traction_at_slip(slip_solution.bn_rear, slip / 100.0, mu_g=slip_solution.mu_g),
        # TE divided by the gross traction ratio developed AT the operating slip --
        # the engine's former primary. Diagnostic ONLY; the specification's Eq. (3.2)
        # divides by the envelope. See "RESOLVED: tractive-efficiency denominator".
        "traction_efficiency_at_slip_percent": traction_efficiency_at_slip_pct(
            slip_solution.mu,
            slip_solution.mu_g,
            slip / 100.0,
            bn_rear=slip_solution.bn_rear,
        ),
        # Retained for compatibility: now identical to the headline
        # `traction_efficiency`, since the envelope IS the specified basis.
        "traction_efficiency_reference_basis": traction_efficiency_envelope_percent(
            slip_solution.mu, slip_solution.mu_g, slip / 100.0
        ),
        "motion_resistance_ratio": mr_ratio,
        "motion_resistance": mr_ratio,
        "rotor_mechanical_power": rotor_power_kw,
        "rotor_equivalent_force": rotor_force_n,
        "calculation_mode": "dss_spec_v1_active_passive",
    }
