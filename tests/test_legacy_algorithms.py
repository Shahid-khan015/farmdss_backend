"""Tests for the single-implement DSS simulation engine (app.core.legacy_algorithms).

Formula-level tests hand-verify each function against the DSS specification
document (docs/Simulation for DSS _Sahid.docx, Section 3) independently of the
implementation. Pipeline tests check physical invariants and the end-to-end
behaviour of calculate_legacy_performance.
"""

from __future__ import annotations

import math

import pytest

from app.core.constants import GRAVITY
from app.core.legacy_algorithms import (
    LegacyInputs,
    calculate_legacy_performance,
    dynamic_axle_loads,
    estimate_draft_force,
    draft_width_parameter,
    fi_factor,
    front_ballast_required_kg,
    gross_traction_at_slip,
    gross_traction_ratio,
    mobility_number,
    net_traction_coefficient,
    put_load_status,
    rear_ballast_required_kg,
    resolve_axle_loads,
    rolling_resistance_front,
    rolling_resistance_rear,
    solve_slip,
    specific_fuel_consumption_l_per_kwh,
    traction_efficiency_at_slip_pct,
    traction_efficiency_percent,
)
from app.models.enums import ImplementType, SoilTexture


def make_inputs(**overrides) -> LegacyInputs:
    base = dict(
        pto_power_kw=45.0,
        wheelbase_m=2.3,
        front_axle_weight_kg=900.0,
        rear_axle_weight_kg=1500.0,
        hitch_distance_from_rear_m=0.5,
        cg_distance_from_rear_m=1.2,
        transmission_efficiency_pct=86.0,
        power_reserve_pct=20.0,
        front_rolling_radius_m=0.40,
        rear_rolling_radius_m=0.58,
        front_overall_diameter_m=0.90,
        rear_overall_diameter_m=1.30,
        front_section_width_m=0.24,
        rear_section_width_m=0.34,
        implement_type=ImplementType.MB_PLOUGH,
        width_m=1.5,
        weight_kg=320.0,
        cg_distance_from_hitch_m=0.8,
        # None exercises the fallback to the per-type Py/D table (MB Plough -> 0.20).
        # The engine reads this field first when it is set, as both references do.
        vertical_horizontal_ratio=None,
        asae_param_a=100.0,
        asae_param_b=50.0,
        asae_param_c=10.0,
        soil_texture=SoilTexture.FINE,
        cone_index_kpa=1200.0,
        depth_cm=15.0,
        speed_kmh=5.0,
        field_area_ha=2.0,
        field_width_m=100.0,
    )
    base.update(overrides)
    return LegacyInputs(**base)


# --- Draft equation (DSS Eq. 3.1: D = F*(A+B*S+C*S^2)*W*T) --------------------------


def test_draft_force_matches_dss_equation():
    inputs = make_inputs()
    # Fi(MB Plough, Fine) = 1.0. W in m, T in cm, no divisor on T.
    expected = 1.0 * (100.0 + 50.0 * 5.0 + 10.0 * 5.0**2) * 1.5 * 15.0
    assert expected == pytest.approx(13500.0)
    assert estimate_draft_force(inputs) == pytest.approx(expected)


def test_draft_force_is_linear_in_depth():
    inputs_10cm = make_inputs(depth_cm=10.0)
    inputs_20cm = make_inputs(depth_cm=20.0)
    # Draft is linear in T, so doubling depth must exactly double draft.
    assert estimate_draft_force(inputs_20cm) == pytest.approx(2.0 * estimate_draft_force(inputs_10cm))


@pytest.mark.parametrize(
    "implement_type",
    [
        ImplementType.MB_PLOUGH,
        ImplementType.DISC_PLOUGH,
        ImplementType.DISC_HARROW,
        ImplementType.CULTIVATOR,
    ],
)
@pytest.mark.parametrize(
    "texture,expected_fi",
    [(SoilTexture.FINE, 1.0), (SoilTexture.MEDIUM, 0.70), (SoilTexture.COARSE, 0.45)],
)
def test_fi_soil_texture_factor_is_global(implement_type, texture, expected_fi):
    """Fi depends on soil texture alone -- all 12 implement x texture combinations.

    Measured through the draft equation rather than by reading the table, so this
    also pins that `Fi` is the only thing texture changes: everything else in
    Eq. 3.1 is held fixed, and the ratio to fine soil must therefore be exactly
    Fi for every implement class.
    """
    # A cultivator's Eq. 3.1 `W` is its tool count, which the engine requires.
    extra = (
        {"number_of_tools": 9}
        if implement_type is ImplementType.CULTIVATOR
        else {}
    )
    fine_draft = estimate_draft_force(
        make_inputs(implement_type=implement_type, soil_texture=SoilTexture.FINE, **extra)
    )
    draft = estimate_draft_force(
        make_inputs(implement_type=implement_type, soil_texture=texture, **extra)
    )
    assert draft / fine_draft == pytest.approx(expected_fi)


# --- Mobility number (DSS Section 3: Bn = CI*b*d/Wd, W in kN) ------------------------


def test_mobility_number_formula_and_units():
    # DSS image7, verbatim: Bn = CI*b*d/Wd. No shape factor, no contact-patch
    # correction -- the source equation has a single term.
    # W must be in kN for CI in kPa and b/d in m, so that Bn is dimensionless.
    ci, b, d, w_n = 1200.0, 0.34, 1.30, 7357.0
    w_kn = w_n / 1000.0
    expected = ci * b * d / w_kn
    got = mobility_number(ci, b, d, w_n)
    assert got == pytest.approx(expected)
    # Sanity: realistic tractor tire/load combos should land in the ~5-80 literature range.
    assert 5.0 < got < 80.0


def test_mobility_number_has_no_shape_factor():
    """Regression guard: an earlier implementation multiplied Bn by 1/(1+3b/d).

    That factor is not in the DSS document. Halving b/d must not change Bn by the
    ratio that factor would have produced.
    """
    ci, d, w_n = 1200.0, 1.30, 7357.0
    wide = mobility_number(ci, 0.40, d, w_n)
    narrow = mobility_number(ci, 0.20, d, w_n)
    # Bn is exactly linear in b; with the old shape factor it was not.
    assert wide == pytest.approx(2.0 * narrow)


def test_mobility_number_is_inversely_proportional_to_load():
    ci, b, d = 1200.0, 0.34, 1.30
    assert mobility_number(ci, b, d, 5000.0) == pytest.approx(2.0 * mobility_number(ci, b, d, 10000.0))


def test_mobility_number_rejects_nonpositive_load():
    with pytest.raises(ValueError):
        mobility_number(1200.0, 0.34, 1.3, 0.0)


# --- Rolling resistance / traction coefficients ---------------------------------------


def test_rolling_resistance_rear_includes_slip_term():
    bn = 40.0
    slip = 0.10
    expected = (1.0 / bn) + 0.04 + (0.5 * slip) / math.sqrt(bn)
    assert rolling_resistance_rear(bn, slip) == pytest.approx(expected)


def test_rolling_resistance_front_has_no_slip_term():
    bn = 40.0
    expected = (1.0 / bn) + 0.04
    assert rolling_resistance_front(bn) == pytest.approx(expected)


def test_gross_traction_ratio_formula():
    bn = 40.0
    expected = 0.88 * (1.0 - math.exp(-0.1 * bn))
    assert gross_traction_ratio(bn) == pytest.approx(expected)


def test_net_traction_coefficient_rises_with_slip():
    bn = 40.0
    mu_low = net_traction_coefficient(bn, 0.02)
    mu_high = net_traction_coefficient(bn, 0.15)
    assert mu_high > mu_low
    # With the corrected 7.5 exponent coefficient, traction should develop
    # meaningfully within the practical 2-20% slip range (physical-plausibility
    # check -- the literal-0.3 reading stayed within +-2% across this whole range).
    assert net_traction_coefficient(bn, 0.20) > 0.5


def test_traction_efficiency_formula():
    bn = 40.0
    slip = 0.10
    mu = net_traction_coefficient(bn, slip)
    mu_g = gross_traction_ratio(bn)
    # DSS spec Eq. (3.2): TE = mu*(1-S)/mu_g -- the Brixius ENVELOPE, no +0.04 term.
    expected = (mu * (1.0 - slip) / mu_g) * 100.0
    assert traction_efficiency_percent(mu, mu_g, slip, bn_rear=bn) == pytest.approx(expected)


def test_traction_efficiency_requires_an_explicit_bn():
    """`bn_rear` is mandatory so the envelope can never be passed in by accident."""
    bn, slip = 40.0, 0.10
    mu_g = gross_traction_ratio(bn)
    mu = net_traction_coefficient(bn, slip)
    with pytest.raises(TypeError):
        traction_efficiency_percent(mu, mu_g, slip)  # type: ignore[call-arg]


# --- Dynamic axle loads (DSS Eq. 3.5, 3.6) --------------------------------------------


def test_dynamic_axle_loads_conserve_total_weight():
    """Rr + Rf must always equal Wt + Wm + Py (DSS Eq. 3.6 is a pure remainder)."""
    tractor_weight_n = 2400.0 * 9.81
    implement_weight_n = 320.0 * 9.81
    py_n = 0.15 * 1350.0
    rr, rf = dynamic_axle_loads(
        draft_n=1350.0,
        depth_cm=15.0,
        wheelbase_m=2.3,
        hitch_distance_from_rear_m=0.5,
        cg_distance_from_rear_m=1.2,
        cg_distance_from_hitch_m=0.8,
        rear_rolling_radius_m=0.58,
        front_rolling_radius_m=0.40,
        tractor_weight_n=tractor_weight_n,
        implement_weight_n=implement_weight_n,
        py_n=py_n,
    )
    assert (rr + rf) == pytest.approx(tractor_weight_n + implement_weight_n + py_n)
    assert rr > 0
    assert rf > 0


def test_dynamic_axle_loads_match_the_hand_worked_moment_balance():
    """DSS Eq. 3.5, term by term:

    Rr = [(Wm+Py)(Xcgi+Hd+L+ef) + Wt(L+ef-Xcgt) - D*Yd] / (L - er + ef)
    """
    draft_n, depth_cm = 1350.0, 15.0
    wheelbase_m, hd, xcgt, xcgi = 2.3, 0.5, 1.2, 0.8
    rear_rr, front_rr = 0.58, 0.40
    tractor_weight_n = 2400.0 * 9.81
    implement_weight_n = 320.0 * 9.81
    py_n = 0.15 * draft_n

    er = 0.1 * rear_rr
    ef = 0.1 * front_rr
    yd = (2.0 / 3.0) * (depth_cm / 100.0)
    expected_rr = (
        (implement_weight_n + py_n) * (xcgi + hd + wheelbase_m + ef)
        + tractor_weight_n * (wheelbase_m + ef - xcgt)
        - draft_n * yd
    ) / (wheelbase_m - er + ef)

    rr, rf = dynamic_axle_loads(
        draft_n=draft_n,
        depth_cm=depth_cm,
        wheelbase_m=wheelbase_m,
        hitch_distance_from_rear_m=hd,
        cg_distance_from_rear_m=xcgt,
        cg_distance_from_hitch_m=xcgi,
        rear_rolling_radius_m=rear_rr,
        front_rolling_radius_m=front_rr,
        tractor_weight_n=tractor_weight_n,
        implement_weight_n=implement_weight_n,
        py_n=py_n,
    )
    assert rr == pytest.approx(expected_rr)
    assert rf == pytest.approx(tractor_weight_n + implement_weight_n + py_n - expected_rr)


def test_deeper_draft_transfers_load_off_the_rear_axle():
    """The -D*Yd term: a deeper, higher-draft pass reduces Rr."""
    common = dict(
        wheelbase_m=2.3, hitch_distance_from_rear_m=0.5, cg_distance_from_rear_m=1.2,
        cg_distance_from_hitch_m=0.8, rear_rolling_radius_m=0.58, front_rolling_radius_m=0.40,
        tractor_weight_n=2400.0 * 9.81, implement_weight_n=320.0 * 9.81, py_n=0.0,
    )
    shallow, _ = dynamic_axle_loads(draft_n=1350.0, depth_cm=10.0, **common)
    deep, _ = dynamic_axle_loads(draft_n=1350.0, depth_cm=30.0, **common)
    assert deep < shallow


# --- Slip iteration -------------------------------------------------------------------


def test_slip_iteration_follows_the_dss_schedule():
    """DSS Section 3.4.6: start at 2%, step 0.1%, stop as soon as Pst = mu*Rr >= D.

    The *stepped* slip is what must sit on the schedule's grid; the reported slip
    is interpolated between the last two grid points (see the test below).
    """
    solution = solve_slip(
        draft_n=1350.0,
        rear_axle_load_n=9000.0,
        ci_kpa=1200.0,
        rear_section_width_m=0.34,
        rear_overall_diameter_m=1.30,
    )
    steps = (solution.stepped_slip_pct - 2.0) / 0.1
    assert steps == pytest.approx(round(steps), abs=1e-6)
    assert steps >= 0

    # The loop stopped at the *first* sufficient grid point, not a later one.
    stepped_pull = net_traction_coefficient(solution.bn_rear, solution.stepped_slip_pct / 100.0) * 9000.0
    assert stepped_pull >= 1350.0
    if solution.stepped_slip_pct > 2.0:
        previous = net_traction_coefficient(
            solution.bn_rear, (solution.stepped_slip_pct - 0.1) / 100.0
        ) * 9000.0
        assert previous < 1350.0


def test_slip_is_interpolated_between_the_last_two_grid_points():
    """The reported slip is where Pst == D, not the grid point that overshot it.

    Matches the reference implementation. `mu` and `pull_n` are re-evaluated at
    the interpolated slip, so the whole solution stays self-consistent.
    """
    draft_n, rr_n = 1350.0, 9000.0
    solution = solve_slip(
        draft_n=draft_n,
        rear_axle_load_n=rr_n,
        ci_kpa=1200.0,
        rear_section_width_m=0.34,
        rear_overall_diameter_m=1.30,
    )
    assert solution.converged is True
    # Interpolation lands at or below the grid point that first exceeded draft,
    # and no lower than the grid point before it.
    assert solution.stepped_slip_pct - 0.1 <= solution.slip_pct <= solution.stepped_slip_pct
    # Self-consistency: mu and pull correspond to the reported slip.
    assert solution.mu == pytest.approx(
        net_traction_coefficient(solution.bn_rear, solution.slip_pct / 100.0)
    )
    assert solution.pull_n == pytest.approx(solution.mu * rr_n)
    # And that pull is essentially the draft -- which is the point of interpolating.
    assert solution.pull_n == pytest.approx(draft_n, rel=2e-3)


def test_solve_slip_starts_at_two_percent_when_traction_is_ample():
    """An easy pull must converge on the very first trial slip."""
    solution = solve_slip(
        draft_n=50.0,
        rear_axle_load_n=9000.0,
        ci_kpa=1200.0,
        rear_section_width_m=0.34,
        rear_overall_diameter_m=1.30,
    )
    assert solution.slip_pct == pytest.approx(2.0)
    assert solution.converged is True



def test_solve_slip_converges_and_stays_in_bounds():
    solution = solve_slip(
        draft_n=1350.0,
        rear_axle_load_n=9000.0,
        ci_kpa=1200.0,
        rear_section_width_m=0.34,
        rear_overall_diameter_m=1.30,
    )
    assert 2.0 <= solution.slip_pct <= 20.0
    assert solution.converged is True
    assert solution.pull_n >= 1350.0 - 1e-6


def test_solve_slip_caps_at_max_and_reports_unconverged_for_impossible_draft():
    solution = solve_slip(
        draft_n=1_000_000.0,  # unreasonably large draft, unreachable
        rear_axle_load_n=9000.0,
        ci_kpa=1200.0,
        rear_section_width_m=0.34,
        rear_overall_diameter_m=1.30,
    )
    assert solution.slip_pct == pytest.approx(20.0)
    assert solution.converged is False


# --- Ballast ----------------------------------------------------------------------------


def test_front_ballast_zero_when_kwef_already_sufficient():
    wt = 23544.0
    # Front axle already carries 25% of tractor weight -> nothing to add.
    kg, reachable = front_ballast_required_kg(
        tractor_weight_n=wt,
        rf_for_added_weight_n=lambda extra_n: 0.25 * (wt + extra_n),
    )
    assert kg == 0.0
    assert reachable is True


def test_front_ballast_solves_to_the_kwef_target():
    """The returned mass must actually put Kwef on 0.20, per the balance given."""
    wt = 23544.0
    # Half of any added ballast reaches the front axle.
    rf = lambda extra_n: 0.10 * wt + 0.5 * extra_n
    kg, reachable = front_ballast_required_kg(tractor_weight_n=wt, rf_for_added_weight_n=rf)
    assert reachable is True
    added_n = kg * GRAVITY
    assert rf(added_n) / (wt + added_n) == pytest.approx(0.20, abs=1e-6)


def test_front_ballast_unreachable_target_is_flagged_not_silently_zero():
    """When ballast cannot raise Kwef to 0.20, report it rather than guess.

    Here every added Newton goes to the rear axle, so Rf is fixed and
    Rf/(Wt + BRf) falls monotonically towards zero -- the target is genuinely
    unreachable, and the solver must say so instead of returning its ceiling.
    """
    wt = 23544.0
    kg, reachable = front_ballast_required_kg(
        tractor_weight_n=wt,
        rf_for_added_weight_n=lambda extra_n: 0.10 * wt,
    )
    assert reachable is False
    assert kg is None


def test_front_ballast_is_solvable_where_the_old_equation_37_saturated():
    """A rear-biased CG that DSS Eq. 3.7 declared unreachable now has a solution.

    Eq. 3.7's implicit form saturated below its own target for geometries like
    this one and reported "no finite ballast reaches Kwef=0.20". Re-solving the
    actual axle balance instead -- as both reference implementations do -- yields
    a finite answer, so the pipeline reports a mass and emits no warning.

    Note the mass is large (thousands of kg) because the model adds ballast at the
    tractor CG rather than ahead of the front axle; see the module notes.
    """
    results = calculate_legacy_performance(make_inputs(cg_distance_from_rear_m=0.6))
    assert results["front_weight_utilization"] < 0.20
    assert results["ballast_front_required"] is not None
    assert results["ballast_front_required"] > 0
    assert not any("Kwef=0.20" in w for w in results["warnings"])


def test_rear_ballast_positive_when_slip_exceeds_target_and_hits_fixed_point():
    # A deeper cut pushes slip above the 15% target (verified: this combination
    # converges at ~17.6% slip), which must trigger the DSS Eq. 3.8/3.9
    # rear-ballast solve while still converging.
    results = calculate_legacy_performance(make_inputs(depth_cm=13.0))
    assert results["converged"] is True
    assert results["slip"] > 15.0
    assert results["ballast_rear_required"] > 0.0


def test_rear_ballast_also_triggered_by_soft_soil():
    # Same effect reached through a low cone index rather than a deeper cut:
    # Bn falls, traction falls, slip climbs past the 15% target (~18.7%).
    results = calculate_legacy_performance(
        make_inputs(depth_cm=12.0, pto_power_kw=70.0, cone_index_kpa=600.0)
    )
    assert results["converged"] is True
    assert results["slip"] > 15.0
    assert results["ballast_rear_required"] > 0.0


def test_rear_ballast_zero_when_axle_load_already_sufficient():
    """No early return on slip: R' simply lands below Rr and max() yields 0."""
    kg, problem = rear_ballast_required_kg(
        draft_n=1350.0,
        rear_axle_load_n=9000.0,
        ci_kpa=1200.0,
        rear_section_width_m=0.34,
        rear_overall_diameter_m=1.30,
    )
    assert problem is None
    assert kg == 0.0


def test_rear_ballast_is_the_shortfall_against_the_required_axle_load():
    """BRr = (R' - Rr)/g, with R' = D / mu'(target slip, Bn at W = R'/2).

    Recovers R' from the reported mass and checks it against that defining
    relation -- the same expression both reference implementations use.
    """
    draft_n, rear_axle_load_n = 15000.0, 9000.0
    kg, problem = rear_ballast_required_kg(
        draft_n=draft_n,
        rear_axle_load_n=rear_axle_load_n,
        ci_kpa=1200.0,
        rear_section_width_m=0.34,
        rear_overall_diameter_m=1.30,
    )
    assert problem is None
    assert kg > 0.0

    r_prime = kg * GRAVITY + rear_axle_load_n
    mu_prime = net_traction_coefficient(mobility_number(1200.0, 0.34, 1.30, r_prime / 2.0), 0.15)
    assert r_prime == pytest.approx(draft_n / mu_prime, rel=1e-6)


def test_rear_ballast_honours_a_non_default_target_slip():
    """Active-passive sizes at the solved slip (DSS Eq. 5.12/5.13), not 15%."""
    common = dict(
        draft_n=15000.0, rear_axle_load_n=9000.0, ci_kpa=1200.0,
        rear_section_width_m=0.34, rear_overall_diameter_m=1.30,
    )
    at_default, _ = rear_ballast_required_kg(**common)          # 15% target
    at_low, _ = rear_ballast_required_kg(**common, target_slip_fraction=0.10)
    at_high, _ = rear_ballast_required_kg(**common, target_slip_fraction=0.20)
    # Less slip allowed -> less traction available -> more axle load needed.
    assert at_high < at_default < at_low


def test_rear_ballast_reports_infeasibility_instead_of_raising():
    """Soft soil under a heavy axle cannot develop the pull at the target slip.

    That is a real verdict, not a crash: the solver must report it and return no
    number, so the caller keeps the rest of the simulation (draft, slip, power,
    fuel) as the evidence for it. Mirrors `front_ballast_required_kg`.
    """
    kg, problem = rear_ballast_required_kg(
        draft_n=25000.0,
        rear_axle_load_n=40000.0,
        ci_kpa=300.0,
        rear_section_width_m=0.24,
        rear_overall_diameter_m=0.90,
    )
    assert kg is None
    assert problem is not None
    assert "could not be sized" in problem


# --- Engine-torque pull limit, Pet (DSS Eq. 3.4) ---------------------------------------


def test_pet_is_absent_unless_engine_torque_is_supplied():
    assert calculate_legacy_performance(make_inputs())["engine_torque_limited_pull"] is None


def test_pet_matches_equation_3_4_in_the_full_pipeline():
    results = calculate_legacy_performance(make_inputs(max_engine_torque_nm=300.0))
    rho_r = rolling_resistance_rear(results["legacy_mobility_number_rear"], results["slip"] / 100.0)
    rho_f = rolling_resistance_front(results["legacy_mobility_number_front"])
    expected = (300.0 * 0.86) / 0.58 - (
        rho_r * results["legacy_rear_axle_load_n"] + rho_f * results["legacy_front_axle_load_n"]
    )
    assert results["engine_torque_limited_pull"] == pytest.approx(expected)


def test_pet_warns_when_below_draft_but_leaves_the_solution_untouched():
    """The document never states Pet caps Pst, so it must stay diagnostic."""
    baseline = calculate_legacy_performance(make_inputs())
    starved = calculate_legacy_performance(make_inputs(max_engine_torque_nm=1.0))
    assert starved["engine_torque_limited_pull"] < starved["draft_force"]
    assert any("Engine-torque pull limit" in w for w in starved["warnings"])
    assert starved["slip"] == pytest.approx(baseline["slip"])
    assert starved["drawbar_power"] == pytest.approx(baseline["drawbar_power"])
    assert starved["required_pto_power"] == pytest.approx(baseline["required_pto_power"])


def test_non_positive_pet_is_reported_as_a_formula_artefact_not_an_engine_limit():
    """Eq. 3.4 omits the engine->wheel gear reduction, so Pet <= 0 for real tractors.

    The engine must say so, rather than concluding "the engine is the binding
    constraint" from a known-incomplete formula.
    """
    results = calculate_legacy_performance(make_inputs(max_engine_torque_nm=180.0))
    assert results["engine_torque_limited_pull"] < 0
    pet_warnings = [w for w in results["warnings"] if "Engine-torque pull limit" in w]
    assert len(pet_warnings) == 1
    assert "non-physical" in pet_warnings[0]
    assert "gear reduction" in pet_warnings[0]
    assert "binding constraint" not in pet_warnings[0]


def test_pet_reports_a_genuine_engine_limit_when_the_thrust_term_is_physical():
    """With enough torque to clear motion resistance, the real limit message applies.

    The torque figures here and below are synthetic: Eq. 3.4 omits the transmission
    gear reduction (see `engine_torque_limited_pull_n`), so the values needed to put
    Pet either side of the draft are far above any real engine. They are chosen
    against the corrected Eq. 3.1 draft (9000 N at 10 cm).
    """
    results = calculate_legacy_performance(
        make_inputs(depth_cm=10.0, max_engine_torque_nm=3000.0)
    )
    pet = results["engine_torque_limited_pull"]
    assert 0 < pet < results["draft_force"]
    pet_warnings = [w for w in results["warnings"] if "Engine-torque pull limit" in w]
    assert len(pet_warnings) == 1
    assert "binding constraint" in pet_warnings[0]


def test_ample_engine_torque_produces_no_pet_warning():
    results = calculate_legacy_performance(
        make_inputs(depth_cm=10.0, max_engine_torque_nm=12000.0)
    )
    assert results["engine_torque_limited_pull"] > results["draft_force"]
    assert not any("Engine-torque pull limit" in w for w in results["warnings"])


# --- Fuel consumption (ASABE 2001) -----------------------------------------------------


def test_specific_fuel_consumption_asabe_formula():
    x = 0.6
    expected = (2.64 * x + 3.91) - (0.203 * math.sqrt(738.0 * x + 173.0))
    assert specific_fuel_consumption_l_per_kwh(x) == pytest.approx(expected)


# --- Put ("Check Put value") status table ----------------------------------------------


@pytest.mark.parametrize(
    "put_pct,expected",
    [
        (94.9, "Tractor is Underloaded"),
        (95.0, "Tractor is properly loaded"),
        (97.5, "Tractor is properly loaded"),
        (100.0, "Tractor is properly loaded"),
        (100.1, "Tractor is Overloaded"),
    ],
)
def test_put_load_status_thresholds(put_pct, expected):
    assert put_load_status(put_pct) == expected


# --- Full pipeline ------------------------------------------------------------------------


def test_calculate_legacy_performance_end_to_end_converges_and_is_sane():
    # 10 cm rather than the fixture default of 15 cm: at 15 cm this 1.5 m mouldboard
    # genuinely overloads the 45 kW / 2700 kg tractor (slip pins at the 20% cap), which
    # is the correct physical answer but not what a "converges and is sane" test should
    # be exercising. 10 cm is a realistic working depth for this pairing.
    results = calculate_legacy_performance(make_inputs(depth_cm=10.0))

    assert results["draft_force"] == pytest.approx(9000.0)
    assert results["converged"] is True
    assert results["warnings"] == []
    assert 2.0 <= results["slip"] <= 20.0
    assert 0.0 < results["traction_efficiency"] <= 100.0
    assert results["drawbar_power"] > 0
    assert results["fuel_consumption_per_hectare"] > 0
    assert results["ballast_front_required"] >= 0
    assert results["ballast_rear_required"] >= 0
    assert results["status_message"] in (
        "Tractor is properly loaded",
        "Tractor is Underloaded",
        "Tractor is Overloaded",
    )
    assert results["calculation_mode"] == "dss_spec_v1"


def test_calculate_legacy_performance_intermediates_chain_together():
    """Spot-check the DSS chain end to end, not just the final numbers."""
    results = calculate_legacy_performance(make_inputs(depth_cm=10.0))
    s = 5.0

    # Bn = CI*b*d/(Rr/2 in kN)
    assert results["legacy_mobility_number_rear"] == pytest.approx(
        mobility_number(1200.0, 0.34, 1.30, results["legacy_rear_axle_load_n"] / 2.0)
    )
    # mu_g = 0.88(1-e^-0.1Bn) is the Brixius envelope; TE = mu(1-S)/GT, where GT
    # is the gross traction ratio developed AT the operating slip, not the envelope.
    assert results["legacy_gross_traction_ratio"] == pytest.approx(
        gross_traction_ratio(results["legacy_mobility_number_rear"])
    )
    assert results["traction_efficiency"] == pytest.approx(
        traction_efficiency_percent(
            results["coefficient_net_traction"],
            results["legacy_gross_traction_ratio"],
            results["slip"] / 100.0,
            bn_rear=results["legacy_mobility_number_rear"],
        )
    )
    # DBp = D*S; Ptr = DBp/(TE*eta_t); Put = Ptr/(Pt(1-fs))*100
    assert results["drawbar_power"] == pytest.approx(results["draft_force"] * s / 3.6 / 1000.0)
    assert results["required_pto_power"] == pytest.approx(
        results["drawbar_power"] / (results["traction_efficiency"] / 100.0 * 0.86)
    )
    assert results["power_utilization"] == pytest.approx(
        results["required_pto_power"] / (45.0 * 0.8) * 100.0
    )
    # FCth = S*W/10
    assert results["field_capacity_theoretical"] == pytest.approx(s * 1.5 / 10.0)


def test_reported_fuel_uses_the_preserved_drawbar_basis():
    """Pins the LEGACY fuel basis so it cannot drift without a deliberate change.

    The DSS gives SFC in L/kW-h and never states the multiplicand; the engine
    preserves `SFC * DBp` and reports the PTO-power reading only as a diagnostic.
    """
    results = calculate_legacy_performance(make_inputs())
    sfc = results["specific_fuel_consumption"]
    assert sfc == pytest.approx(
        specific_fuel_consumption_l_per_kwh(results["required_pto_power"] / 45.0)
    )
    assert results["fuel_l_per_hour"] == pytest.approx(sfc * results["drawbar_power"])
    assert results["fuel_consumption_per_hectare"] == pytest.approx(
        results["fuel_l_per_hour"] / results["field_capacity_actual"]
    )
    # The dimensionally-consistent alternative is reported but must feed nothing.
    assert results["fuel_l_per_hour_pto_basis"] == pytest.approx(sfc * results["required_pto_power"])
    assert results["fuel_l_per_hour_pto_basis"] != pytest.approx(results["fuel_l_per_hour"])


def test_calculate_legacy_performance_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        calculate_legacy_performance(make_inputs(width_m=0.0))
    with pytest.raises(ValueError):
        calculate_legacy_performance(make_inputs(speed_kmh=0.0))
    with pytest.raises(ValueError):
        calculate_legacy_performance(make_inputs(pto_power_kw=0.0))
    with pytest.raises(ValueError):
        calculate_legacy_performance(make_inputs(cone_index_kpa=0.0))
    with pytest.raises(ValueError):
        calculate_legacy_performance(make_inputs(depth_cm=0.0))
    with pytest.raises(ValueError):
        calculate_legacy_performance(make_inputs(field_area_ha=0.0))


def test_calculate_legacy_performance_underpowered_tractor_reports_overloaded():
    # A tiny tractor pulling a wide, deep implement should end up overloaded/unstable
    # rather than silently succeeding.
    results = calculate_legacy_performance(
        make_inputs(pto_power_kw=12.0, width_m=3.0, depth_cm=30.0)
    )
    assert results["status_message"] in ("Tractor is Overloaded", "Not Recommended", "Unstable")


# --- Brixius tractive efficiency -------------------------------------------


def test_net_traction_is_brixius_gross_minus_motion_resistance():
    """`mu` must equal Brixius GT - MR exactly.

    This is what identifies the traction model, and therefore what fixes the
    denominator tractive efficiency has to use.
    """
    bn, s = 48.7, 0.10
    mu_g = gross_traction_ratio(bn)
    gt = 0.88 * (1 - math.exp(-0.1 * bn)) * (1 - math.exp(-7.5 * s)) + 0.04
    mr = 0.04 + 1.0 / bn + 0.5 * s / math.sqrt(bn)
    assert gross_traction_at_slip(bn, s) == pytest.approx(gt)
    assert net_traction_coefficient(bn, s, mu_g=mu_g) == pytest.approx(gt - mr)


def test_tractive_efficiency_uses_the_envelope_per_spec_eq_3_2():
    """DSS spec Eq. (3.2) divides by the envelope `mu_g`, not by GT at slip.

    Eq. (3.2) lives in the DOCX as a MathType/OLE object (`word/media/image1.wmf`),
    which is why text extraction shows only the label. Rendered, it reads
    `TE = mu*(1-S)/mu_g`. `tillage_dss.html` and spreadsheet `C59` agree.

    An earlier revision used `gross_traction_at_slip` here, on the argument that
    field TE sits near 70-80% and the envelope gives ~45%. That made the engine the
    only outlier against its own specification; the reading is now conformance, and
    the at-slip value is kept as the `traction_efficiency_at_slip_percent`
    diagnostic.
    """
    bn, s = 48.7, 0.10
    mu_g = gross_traction_ratio(bn)
    mu = net_traction_coefficient(bn, s, mu_g=mu_g)

    te = traction_efficiency_percent(mu, mu_g, s, bn_rear=bn)
    assert te == pytest.approx((mu * (1 - s) / mu_g) * 100.0)
    assert te == pytest.approx(44.6323, abs=1e-3)
    assert te < 100.0

    # The at-slip form is still available, and still reads markedly higher.
    at_slip = traction_efficiency_at_slip_pct(mu, mu_g, s, bn_rear=bn)
    assert at_slip == pytest.approx(77.8326, abs=1e-3)
    assert at_slip > te


def test_envelope_te_is_monotonic_in_slip_so_no_optimum_exists():
    """Accepted consequence of the specified model -- do not compensate for it.

    With the envelope denominator, TE rises monotonically across the whole 1-25%
    working band, so there is no interior optimum for a slip recommendation to aim
    at. The at-slip form did peak in the 8-15% band. This test records the
    behavioural difference so it cannot be reintroduced by accident, and so anyone
    building slip advice knows the headline TE will not give them a turning point.
    """
    bn = 48.7
    mu_g = gross_traction_ratio(bn)

    def te(slip_fraction: float) -> float:
        mu = net_traction_coefficient(bn, slip_fraction, mu_g=mu_g)
        return traction_efficiency_percent(mu, mu_g, slip_fraction, bn_rear=bn)

    def te_at_slip(slip_fraction: float) -> float:
        mu = net_traction_coefficient(bn, slip_fraction, mu_g=mu_g)
        return traction_efficiency_at_slip_pct(mu, mu_g, slip_fraction, bn_rear=bn)

    grid = [i / 1000.0 for i in range(10, 251)]  # 1% .. 25%
    assert all(te(grid[i + 1]) >= te(grid[i]) - 1e-12 for i in range(len(grid) - 1))
    assert max(grid, key=te) == pytest.approx(0.25)

    # The retained diagnostic still has its interior optimum.
    assert 0.08 <= max(grid, key=te_at_slip) <= 0.15


# --- Eq. 3.1 `W`: metres or tool count --------------------------------------


def test_draft_width_is_metres_for_full_width_tools():
    for it in (ImplementType.MB_PLOUGH, ImplementType.DISC_PLOUGH, ImplementType.DISC_HARROW):
        assert draft_width_parameter(it, 1.8, 9) == pytest.approx(1.8)


def test_draft_width_is_the_tool_count_for_cultivators():
    assert draft_width_parameter(ImplementType.CULTIVATOR, 2.2, 9) == pytest.approx(9.0)


def test_draft_width_rejects_a_missing_tool_count():
    """Refusing beats silently substituting the width.

    An earlier revision fell back to metres so rows predating the column kept
    running. That produces a meaningless answer rather than a merely understated
    one -- see `test_missing_tool_count_would_have_nearly_cancelled_active_draft`.
    """
    with pytest.raises(ValueError, match="number_of_tools"):
        draft_width_parameter(ImplementType.CULTIVATOR, 2.2, None)


def test_draft_width_ignores_the_tool_count_for_full_width_tools():
    """Ploughs and harrows must be unaffected, tool count present or not."""
    for it in (ImplementType.MB_PLOUGH, ImplementType.DISC_PLOUGH, ImplementType.DISC_HARROW):
        assert draft_width_parameter(it, 1.8, None) == pytest.approx(1.8)
        assert draft_width_parameter(it, 1.8, 9) == pytest.approx(1.8)


def test_draft_width_rejects_a_nonsensical_tool_count():
    with pytest.raises(ValueError):
        draft_width_parameter(ImplementType.CULTIVATOR, 2.2, 0)


def test_cultivator_draft_per_metre_is_physically_plausible():
    """W-in-metres makes draft/m identical at every size, which carries no
    information and lands ~4x below a disc harrow. The tool count fixes both."""
    common = dict(
        implement_type=ImplementType.CULTIVATOR,
        asae_param_a=32.0, asae_param_b=1.9, asae_param_c=0.0,
        soil_texture=SoilTexture.MEDIUM, speed_kmh=4.0, depth_cm=15.0,
        vertical_horizontal_ratio=0.2,
    )
    per_metre = []
    for width, tools in ((2.2, 9), (3.13, 13), (4.15, 17)):
        d = estimate_draft_force(make_inputs(width_m=width, number_of_tools=tools, **common))
        per_metre.append(d / width)
    # Draft per metre must now vary only through tine spacing, and sit between a
    # disc plough (~1975 N/m) and a disc harrow (~4050 N/m).
    for v in per_metre:
        assert 1500.0 < v < 4500.0
    assert max(per_metre) - min(per_metre) < 0.05 * max(per_metre)


# --- Front lift is answered with ballast, not refused ------------------------


def test_front_lift_returns_a_ballasted_result_instead_of_raising():
    # A heavy, far-hitched implement on a light tractor lifts the front end.
    inputs = make_inputs(
        front_axle_weight_kg=315.0, rear_axle_weight_kg=440.0, wheelbase_m=1.42,
        cg_distance_from_rear_m=0.5925, cg_distance_from_hitch_m=0.55,
        width_m=1.5, weight_kg=225.0, depth_cm=20.0,
    )
    results = calculate_legacy_performance(inputs)

    assert results["infeasible_without_ballast"] is True
    assert results["stabilising_front_ballast_kg"] > 0
    assert results["legacy_front_axle_load_n"] > 0
    assert any("Front axle lifts" in w for w in results["warnings"])
    # It still produces a usable answer rather than a dead end.
    assert results["power_utilization"] > 0
    assert results["draft_force"] > 0


def test_a_comfortable_pairing_reports_no_stabilising_ballast():
    results = calculate_legacy_performance(make_inputs(depth_cm=10.0))
    assert results["infeasible_without_ballast"] is False
    assert results["stabilising_front_ballast_kg"] == pytest.approx(0.0)


def test_rear_axle_lift_still_raises_because_ballast_cannot_fix_it():
    """Front ballast moves load OFF the driven axle, so rear lift is not rescuable."""
    with pytest.raises(ValueError, match=r"rear \(driven\) axle"):
        resolve_axle_loads(
            rear_axle_load_n=-100.0,
            front_axle_load_n=5000.0,
            tractor_weight_n=20000.0,
            axle_loads_for_added_weight=lambda extra_n: (-100.0, 5000.0 + extra_n),
            warnings=[],
        )


def test_front_lift_raises_only_when_no_ballast_can_fix_it():
    """A tractor that cannot reach the steering-weight target at any ballast."""
    with pytest.raises(ValueError, match="no amount of front ballast"):
        resolve_axle_loads(
            rear_axle_load_n=5000.0,
            front_axle_load_n=-10.0,
            tractor_weight_n=20000.0,
            # Front load never rises with ballast -> target unreachable.
            axle_loads_for_added_weight=lambda extra_n: (5000.0, -10.0),
            warnings=[],
        )


def test_resolve_axle_loads_passes_a_healthy_pair_through_untouched():
    warnings: list = []
    got = resolve_axle_loads(
        rear_axle_load_n=14000.0,
        front_axle_load_n=6000.0,
        tractor_weight_n=20000.0,
        axle_loads_for_added_weight=lambda extra_n: (14000.0, 6000.0 + extra_n),
        warnings=warnings,
    )
    assert (got.rear_axle_load_n, got.front_axle_load_n) == (14000.0, 6000.0)
    assert got.stabilising_ballast_kg == 0.0
    assert got.infeasible_without_ballast is False
    assert warnings == []


# --- Documented divergences from the reference implementations ---------------
#
# The engine is bit-identical to `docs/tillage_dss (2).html` in every formula, in
# all three modes. Exactly four things make the outputs differ, and each is a
# deliberate decision recorded in SIMULATION_ENGINE_FORMULAS.md. These tests fail
# if any of them is changed silently.


def test_at_slip_te_is_reported_but_drives_nothing():
    """The headline TE is the spec's envelope form; the at-slip form is diagnostic."""
    results = calculate_legacy_performance(make_inputs(depth_cm=10.0))
    bn = results["legacy_mobility_number_rear"]
    mu = results["coefficient_net_traction"]
    mu_g = results["legacy_gross_traction_ratio"]
    s = results["slip"] / 100.0

    # Headline: DSS spec Eq. (3.2), divided by the envelope.
    assert results["traction_efficiency"] == pytest.approx(mu * (1 - s) / mu_g * 100.0)
    # Retained for compatibility; now the same quantity as the headline.
    assert results["traction_efficiency_reference_basis"] == pytest.approx(
        results["traction_efficiency"]
    )

    # Diagnostic: the former primary, and the ratio it is built from.
    assert results["gross_traction_at_slip"] == pytest.approx(gross_traction_at_slip(bn, s))
    assert results["traction_efficiency_at_slip_percent"] == pytest.approx(
        mu * (1 - s) / results["gross_traction_at_slip"] * 100.0
    )
    assert results["traction_efficiency_at_slip_percent"] > results["traction_efficiency"]

    # The power chain follows the headline, not the diagnostic.
    assert results["required_pto_power"] == pytest.approx(
        results["drawbar_power"] / (results["traction_efficiency"] / 100.0 * 0.86)
    )


def test_fi_is_global_and_matches_the_reference_stack():
    """Fi is keyed on soil texture alone, matching the spreadsheet and the HTML.

    The authority is the spreadsheet's "tractor and implement data" sheet, cells
    D50:F53 -- a three-row Soil Type/Fi table with no implement dimension -- and
    the HTML tool's texture selector, which hard-codes the same three values.

    These are D497's *moldboard-plough* F row applied to every implement, so this
    is a deliberate departure from D497's per-implement F rows (disc tools would
    be 0.88/0.78, cultivators 0.85/0.65). It was chosen for cross-tool
    consistency with the reference stack; see constants.FI_FACTOR_BY_TEXTURE.
    """
    from app.core.constants import FI_FACTOR_BY_TEXTURE as FI

    assert FI == {"Fine": 1.0, "Medium": 0.70, "Coarse": 0.45}
    # Flat: one value per texture, with no implement key anywhere in the table.
    assert all(isinstance(v, float) for v in FI.values())

    # And the lookup ignores the implement, for every passive class.
    for texture in (SoilTexture.FINE, SoilTexture.MEDIUM, SoilTexture.COARSE):
        applied = {
            fi_factor(implement_type, texture)
            for implement_type in (
                ImplementType.MB_PLOUGH,
                ImplementType.DISC_PLOUGH,
                ImplementType.DISC_HARROW,
                ImplementType.CULTIVATOR,
            )
        }
        assert applied == {FI[texture.value]}


def test_missing_tool_count_would_have_nearly_cancelled_active_draft():
    """Why the missing tool count raises instead of falling back to metres.

    In an active-passive combination `Deff = Dp + Da - Ta`. Substituting the
    width in metres for a cultivator understates `Dp` roughly 4x, which here is
    the same order as the rotor's forward thrust -- so `Deff` collapses to
    nothing and the run SUCCEEDS with a plausible-looking verdict built on no
    draft. That is why the fallback was removed; this pins the arithmetic.

    `fi` is the global medium-soil factor. It was 0.85 (the old per-implement
    cultivator value) while Fi was implement-keyed; under the global table the
    understated draft no longer merely cancels the thrust but overshoots into
    negative effective draft, which the engine rejects outright. The
    demonstration is strictly stronger, so the bound below is one-sided.
    """
    fi, a, b, c = 0.70, 32.0, 1.9, 0.0
    speed_kmh, depth_cm, width_m, tools = 4.0, 12.0, 2.2, 9

    def draft(w: float) -> float:
        return fi * (a + b * speed_kmh + c * speed_kmh**2) * w * depth_cm

    correct = draft(float(tools))
    substituted = draft(width_m)

    # Rotavator 7 ft: eta_r 0.32, P_PTO 4.5 kW -> Ta = eta_r*P/V
    thrust_n = 0.32 * 4500.0 / (speed_kmh / 3.6)
    rotor_resistance_n = 420.0

    assert correct / substituted == pytest.approx(float(tools) / width_m)
    # Correct effective draft is a real, sizeable load...
    assert (correct + rotor_resistance_n - thrust_n) > 2000.0
    # ...whereas the substituted one collapses to nothing (here, past zero).
    assert (substituted + rotor_resistance_n - thrust_n) < 50.0
