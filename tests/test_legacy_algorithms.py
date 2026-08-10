"""Tests for the single-implement DSS simulation engine (app.core.legacy_algorithms).

Formula-level tests hand-verify each function against the DSS specification
document (docs/Simulation for DSS _Sahid.docx, Section 3) independently of the
implementation. Pipeline tests check physical invariants and the end-to-end
behaviour of calculate_legacy_performance.
"""

from __future__ import annotations

import math

import pytest

from app.core.legacy_algorithms import (
    LegacyInputs,
    calculate_legacy_performance,
    dynamic_axle_loads,
    estimate_draft_force,
    front_ballast_required_kg,
    gross_traction_ratio,
    mobility_number,
    net_traction_coefficient,
    put_load_status,
    rear_ballast_required_kg,
    rolling_resistance_front,
    rolling_resistance_rear,
    solve_slip,
    specific_fuel_consumption_l_per_kwh,
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
        vertical_horizontal_ratio=0.65,  # no longer used by the engine; DSS Py/D table supersedes it
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


# --- Draft equation (DSS Eq. 3.1: D = F*(A+B*S+C*S^2)*W*(T/10)) ---------------------


def test_draft_force_matches_dss_equation():
    inputs = make_inputs()
    # Fi(MB Plough, Fine) = 1.0
    expected = 1.0 * (100.0 + 50.0 * 5.0 + 10.0 * 5.0**2) * 1.5 * (15.0 / 10.0)
    assert expected == pytest.approx(1350.0)
    assert estimate_draft_force(inputs) == pytest.approx(expected)


def test_draft_force_depth_uses_divide_by_10():
    # DSS Eq 3.1 explicitly divides tillage depth by 10 (confirmed unambiguous in the
    # source OMML XML, not an image-legibility question).
    inputs_10cm = make_inputs(depth_cm=10.0)
    inputs_20cm = make_inputs(depth_cm=20.0)
    # Draft is linear in T/10, so doubling depth must exactly double draft.
    assert estimate_draft_force(inputs_20cm) == pytest.approx(2.0 * estimate_draft_force(inputs_10cm))


@pytest.mark.parametrize(
    "texture,expected_fi",
    [(SoilTexture.FINE, 1.0), (SoilTexture.MEDIUM, 0.70), (SoilTexture.COARSE, 0.45)],
)
def test_fi_soil_texture_factor_for_mb_plough(texture, expected_fi):
    inputs = make_inputs(soil_texture=texture)
    fine_draft = estimate_draft_force(make_inputs(soil_texture=SoilTexture.FINE))
    draft = estimate_draft_force(inputs)
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
    expected = (mu * (1.0 - slip) / mu_g) * 100.0
    assert traction_efficiency_percent(mu, mu_g, slip) == pytest.approx(expected)


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
    """DSS Section 3.4.6: start at 2%, step 0.1%, stop as soon as Pst = mu*Rr >= D."""
    solution = solve_slip(
        draft_n=1350.0,
        rear_axle_load_n=9000.0,
        ci_kpa=1200.0,
        rear_section_width_m=0.34,
        rear_overall_diameter_m=1.30,
    )
    # The converged slip must be on the 2% + k*0.1% grid.
    steps = (solution.slip_pct - 2.0) / 0.1
    assert steps == pytest.approx(round(steps), abs=1e-6)
    assert steps >= 0

    # Pst = mu*Rr at the converged slip, and the previous grid point fell short --
    # i.e. the loop stopped at the *first* sufficient slip, not a later one.
    assert solution.pull_n == pytest.approx(solution.mu * 9000.0)
    assert solution.pull_n >= 1350.0
    if solution.slip_pct > 2.0:
        previous = net_traction_coefficient(solution.bn_rear, (solution.slip_pct - 0.1) / 100.0) * 9000.0
        assert previous < 1350.0


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
    kg, feasible = front_ballast_required_kg(
        kwef=0.25,
        tractor_weight_n=23544.0,
        rsf_n=8829.0,
        draft_n=1350.0,
        yd_m=0.10,
        implement_weight_n=3139.2,
        py_n=202.5,
        cg_distance_from_hitch_m=0.8,
        hitch_distance_from_rear_m=0.5,
        er_m=0.058,
        ef_m=0.040,
        wheelbase_m=2.3,
    )
    assert kg == 0.0
    assert feasible is True


def test_front_ballast_infeasible_case_is_flagged_not_silently_zero():
    # For some geometries DSS Eq. 3.7 has no finite solution (RHS saturates below
    # the ever-growing LHS target) -- verified analytically for this input set.
    # The solver must report feasible=False rather than a falsely-precise number,
    # and the full pipeline must surface a warning rather than staying silent.
    tractor_weight_n = 23544.0
    rsf_n = 3000.0
    kwef = rsf_n / tractor_weight_n
    assert kwef < 0.20

    kg, feasible = front_ballast_required_kg(
        kwef=kwef,
        tractor_weight_n=tractor_weight_n,
        rsf_n=rsf_n,
        draft_n=1350.0,
        yd_m=0.10,
        implement_weight_n=3139.2,
        py_n=202.5,
        cg_distance_from_hitch_m=0.8,
        hitch_distance_from_rear_m=0.5,
        er_m=0.058,
        ef_m=0.040,
        wheelbase_m=2.3,
    )
    assert feasible is False
    assert kg > 0


def test_calculate_legacy_performance_warns_when_front_ballast_target_unreachable():
    results = calculate_legacy_performance(make_inputs(cg_distance_from_rear_m=0.6))
    assert results["front_weight_utilization"] < 0.20
    assert any("Kwef=0.20" in w for w in results["warnings"])


def test_rear_ballast_positive_when_slip_exceeds_target_and_hits_fixed_point():
    # A wide/deep implement on a modest tractor pushes slip above the 15% target
    # (verified: this combination converges at ~18.6% slip), which must trigger
    # the DSS Eq. 3.8/3.9 rear-ballast solve.
    results = calculate_legacy_performance(
        make_inputs(width_m=5.5, depth_cm=35.0, pto_power_kw=70.0)
    )
    assert results["converged"] is True
    assert results["slip"] > 15.0
    assert results["ballast_rear_required"] > 0.0


def test_rear_ballast_also_triggered_by_soft_soil():
    # Same effect reached through a low cone index rather than a bigger implement:
    # Bn falls, traction falls, slip climbs past the 15% target.
    results = calculate_legacy_performance(
        make_inputs(width_m=4.8, depth_cm=35.0, pto_power_kw=70.0, cone_index_kpa=600.0)
    )
    assert results["converged"] is True
    assert results["slip"] > 15.0
    assert results["ballast_rear_required"] > 0.0


def test_rear_ballast_zero_when_slip_within_target():
    assert rear_ballast_required_kg(
        slip_pct=10.0,
        draft_n=1350.0,
        rear_axle_load_n=9000.0,
        rsr_n=14715.0,
        ci_kpa=1200.0,
        rear_section_width_m=0.34,
        rear_overall_diameter_m=1.30,
        yd_m=0.10,
        implement_weight_n=3139.2,
        py_n=202.5,
        cg_distance_from_hitch_m=0.8,
        hitch_distance_from_rear_m=0.5,
        tractor_weight_n=23544.0,
        er_m=0.058,
        ef_m=0.040,
        wheelbase_m=2.3,
    ) == 0.0


def test_rear_ballast_r_prime_fixed_point_satisfies_r_prime_equals_d_over_mu():
    """DSS Eq. 3.9: R' = D / mu'(S=0.15, Bn evaluated at W = R'/2).

    The solver's converged R' is recovered from the reported BRr by inverting
    Eq. 3.8, then checked against the defining relation.
    """
    draft_n = 15000.0
    geom = dict(
        rsr_n=14715.0, yd_m=0.10, implement_weight_n=3139.2, py_n=202.5,
        cg_distance_from_hitch_m=0.8, hitch_distance_from_rear_m=0.5,
        tractor_weight_n=23544.0, er_m=0.058, ef_m=0.040, wheelbase_m=2.3,
    )
    br_r_kg = rear_ballast_required_kg(
        slip_pct=18.0,
        draft_n=draft_n,
        rear_axle_load_n=9000.0,
        ci_kpa=1200.0,
        rear_section_width_m=0.34,
        rear_overall_diameter_m=1.30,
        **geom,
    )
    assert br_r_kg > 0.0

    # Invert Eq. 3.8 for R'.
    br_r_n = br_r_kg * 9.81
    r_prime = (
        br_r_n * (geom["wheelbase_m"] + geom["ef_m"])
        - draft_n * geom["yd_m"]
        + geom["rsr_n"] * geom["wheelbase_m"]
        + geom["tractor_weight_n"] * geom["ef_m"]
        + (geom["implement_weight_n"] + geom["py_n"])
        * (geom["cg_distance_from_hitch_m"] + geom["hitch_distance_from_rear_m"] + geom["er_m"])
    ) / (geom["wheelbase_m"] - geom["er_m"] + geom["ef_m"])

    mu_prime = net_traction_coefficient(mobility_number(1200.0, 0.34, 1.30, r_prime / 2.0), 0.15)
    assert r_prime == pytest.approx(draft_n / mu_prime, rel=1e-6)


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
    """With enough torque to clear motion resistance, the real limit message applies."""
    results = calculate_legacy_performance(make_inputs(max_engine_torque_nm=1200.0))
    pet = results["engine_torque_limited_pull"]
    assert 0 < pet < results["draft_force"]
    pet_warnings = [w for w in results["warnings"] if "Engine-torque pull limit" in w]
    assert len(pet_warnings) == 1
    assert "binding constraint" in pet_warnings[0]


def test_ample_engine_torque_produces_no_pet_warning():
    results = calculate_legacy_performance(make_inputs(max_engine_torque_nm=5000.0))
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
    results = calculate_legacy_performance(make_inputs())

    assert results["draft_force"] == pytest.approx(1350.0)
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
    results = calculate_legacy_performance(make_inputs())
    s = 5.0

    # Bn = CI*b*d/(Rr/2 in kN)
    assert results["legacy_mobility_number_rear"] == pytest.approx(
        mobility_number(1200.0, 0.34, 1.30, results["legacy_rear_axle_load_n"] / 2.0)
    )
    # mu_g = 0.88(1-e^-0.1Bn); TE = mu(1-S)/mu_g
    assert results["legacy_gross_traction_ratio"] == pytest.approx(
        gross_traction_ratio(results["legacy_mobility_number_rear"])
    )
    assert results["traction_efficiency"] == pytest.approx(
        traction_efficiency_percent(
            results["coefficient_net_traction"],
            results["legacy_gross_traction_ratio"],
            results["slip"] / 100.0,
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
