"""Tests for the combination-tillage engine (app.core.combi_algorithms), covering
DSS Section 4 (passive-passive) and Section 5 (active-passive)."""

from __future__ import annotations

import math

import pytest

from app.core.combi_algorithms import (
    ActivePassiveInputs,
    ActiveRotorInputs,
    PassivePassiveInputs,
    PassiveToolInputs,
    calculate_active_passive_performance,
    calculate_passive_passive_performance,
    effective_draft_n,
    mobility_number_passive_passive,
    pto_equivalent_rear_load_n,
    pto_reaction_moment_nm,
    rotor_equivalent_force_n,
    rotor_mechanical_power_kw,
    rotor_thrust_n,
)
from app.core.constants import FRONT_BALLAST_TARGET_KWEF, GRAVITY
from app.core.dss_shared import specific_fuel_consumption_l_per_kwh
from app.core.legacy_algorithms import (
    LegacyInputs,
    calculate_legacy_performance,
    engine_torque_limited_pull_n,
    mobility_number,
    py_over_d_ratio,
    rear_ballast_required_kg,
    rolling_resistance_front,
    rolling_resistance_rear,
    solve_slip,
)
from app.models.enums import ImplementType, SoilTexture

TRACTOR_COMMON = dict(
    pto_power_kw=70.0,
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
    soil_texture=SoilTexture.FINE,
    cone_index_kpa=1200.0,
    depth_cm=15.0,
    speed_kmh=5.0,
    field_area_ha=2.0,
    field_width_m=100.0,
)

TOOL_1 = PassiveToolInputs(
    implement_type=ImplementType.MB_PLOUGH, width_m=1.0, weight_kg=250.0,
    cg_distance_from_hitch_m=0.6, asae_param_a=100.0, asae_param_b=50.0, asae_param_c=10.0,
)
# Real ASABE D497 secondary-tillage field-cultivator parameters. That row is
# tabulated PER TOOL, so Eq. 3.1's W is `number_of_tools`, not `width_m`; the
# width is still what field capacity and turning use.
TOOL_2 = PassiveToolInputs(
    implement_type=ImplementType.CULTIVATOR, width_m=1.2, weight_kg=180.0,
    cg_distance_from_hitch_m=1.0, asae_param_a=32.0, asae_param_b=1.9, asae_param_c=0.0,
    number_of_tools=9,
)


def make_pp_inputs(**overrides) -> PassivePassiveInputs:
    base = dict(**TRACTOR_COMMON, tool_1=TOOL_1, tool_2=TOOL_2, interaction_coefficient=0.10)
    base.update(overrides)
    return PassivePassiveInputs(**base)


def make_rotor(**overrides) -> ActiveRotorInputs:
    base = dict(
        weight_kg=150.0, cg_distance_from_hitch_m=0.4, mechanical_resistance_n=300.0,
        rotor_efficiency=0.30, pto_power_draw_kw=3.0, rotor_speed_rpm=540.0,
    )
    base.update(overrides)
    return ActiveRotorInputs(**base)


PASSIVE_TOOL_AP = PassiveToolInputs(
    implement_type=ImplementType.DISC_HARROW, width_m=1.5, weight_kg=280.0,
    cg_distance_from_hitch_m=0.7, asae_param_a=150.0, asae_param_b=25.0, asae_param_c=4.0,
)


def make_ap_inputs(**overrides) -> ActivePassiveInputs:
    base = dict(**TRACTOR_COMMON, passive_tool=PASSIVE_TOOL_AP, rotor=make_rotor())
    base.update(overrides)
    return ActivePassiveInputs(**base)


# --- Passive-passive (DSS Section 4) ----------------------------------------------------


def test_combined_draft_applies_interaction_coefficient():
    # DSS Eq. 4.1: DTotal = (1-ki)*(D1+D2)
    r_no_ki = calculate_passive_passive_performance(make_pp_inputs(interaction_coefficient=0.0))
    r_with_ki = calculate_passive_passive_performance(make_pp_inputs(interaction_coefficient=0.25))
    d1, d2 = r_no_ki["draft_1"], r_no_ki["draft_2"]
    assert r_no_ki["draft_force"] == pytest.approx(d1 + d2)
    assert r_with_ki["draft_force"] == pytest.approx(0.75 * (d1 + d2))
    # DTotal(1-ki) must be smaller than the naive sum -- per the document's own claim
    # that combi passes reduce Ptr/fuel relative to two independent single-tool passes.
    assert r_with_ki["draft_force"] < r_no_ki["draft_force"]


def test_interaction_coefficient_out_of_range_rejected():
    # Validated before any traction maths, so this holds regardless of Bn'.
    with pytest.raises(ValueError):
        calculate_passive_passive_performance(make_pp_inputs(interaction_coefficient=0.30))
    with pytest.raises(ValueError):
        calculate_passive_passive_performance(make_pp_inputs(interaction_coefficient=-0.01))


def test_passive_passive_draft_matches_each_tool_asae_equation():
    results = calculate_passive_passive_performance(make_pp_inputs())
    v = TRACTOR_COMMON["speed_kmh"]
    depth = TRACTOR_COMMON["depth_cm"]
    expected_d1 = 1.0 * (100.0 + 50.0 * v + 10.0 * v**2) * TOOL_1.width_m * depth  # Fine soil, Fi=1.0
    assert results["draft_1"] == pytest.approx(expected_d1)


def test_passive_passive_chain_converges_end_to_end():
    """Bn -> mu_g -> slip -> mu -> TE -> pull -> ballast/power, on the real chain.

    Replaces an earlier test that could only run behind a monkeypatched wheel
    numeric, because the Section 4 `Bn'` expression made every realistic input
    fail. Passive-passive now solves like any other mode.
    """
    results = calculate_passive_passive_performance(make_pp_inputs())
    assert results["combination_type"] == "passive_passive"
    assert results["converged"] is True
    assert 2.0 <= results["slip"] <= 20.0
    assert 0.0 < results["traction_efficiency"] <= 100.0
    assert results["coefficient_net_traction"] > 0
    assert results["drawbar_power"] > 0
    assert results["fuel_consumption_per_hectare"] > 0
    assert results["ballast_front_required"] >= 0
    assert results["ballast_rear_required"] >= 0
    assert results["calculation_mode"] == "dss_spec_v1_passive_passive"


def test_passive_passive_requires_both_tools():
    with pytest.raises(ValueError):
        calculate_passive_passive_performance(make_pp_inputs(tool_2=None))


# --- Active-passive (DSS Section 5) -----------------------------------------------------


def test_pto_reaction_moment_formula():
    # DSS Eq. 5.4: MPTO = 9550 * PPTO / N
    results = calculate_active_passive_performance(make_ap_inputs())
    expected = 9550.0 * 3.0 / 540.0
    assert results["pto_reaction_moment"] == pytest.approx(expected)


def test_rotor_thrust_formula():
    # DSS Eq. 5.2: Ta = eta_r * PPTO[W] / V[m/s]
    results = calculate_active_passive_performance(make_ap_inputs())
    v_mps = TRACTOR_COMMON["speed_kmh"] / 3.6
    expected_ta = 0.30 * (3.0 * 1000.0) / v_mps
    assert results["rotor_thrust"] == pytest.approx(expected_ta)


def test_effective_draft_is_passive_plus_mechanical_minus_thrust():
    results = calculate_active_passive_performance(make_ap_inputs())
    expected_deff = results["draft_passive"] + results["draft_active_mechanical"] - results["rotor_thrust"]
    assert results["draft_force"] == pytest.approx(expected_deff)


def test_rotor_efficiency_out_of_documented_range_rejected():
    with pytest.raises(ValueError):
        calculate_active_passive_performance(make_ap_inputs(rotor=make_rotor(rotor_efficiency=0.10)))
    with pytest.raises(ValueError):
        calculate_active_passive_performance(make_ap_inputs(rotor=make_rotor(rotor_efficiency=0.50)))


def test_excess_rotor_thrust_is_rejected_not_silently_broken():
    # A rotor whose thrust exceeds total resistance drives Deff <= 0, which would
    # otherwise cascade into negative drawbar power and a division by zero in the
    # fuel/efficiency chain -- must be a clear ValueError instead.
    # 40 kW rather than 15: the corrected Eq. 3.1 raises the passive draft this thrust
    # has to overcome (Dp ~= 8.4 kN here), so a bigger rotor is needed to drive Deff
    # negative at all.
    overpowered_rotor = make_rotor(pto_power_draw_kw=40.0, rotor_efficiency=0.35)
    with pytest.raises(ValueError, match="Effective draft"):
        calculate_active_passive_performance(make_ap_inputs(rotor=overpowered_rotor))


def test_active_passive_power_utilization_includes_pto_draw():
    # DSS Section 5.9: Put must include both Ptr (drawbar-equivalent) and the
    # rotor's own PTO power draw, since both come from the same engine.
    results = calculate_active_passive_performance(make_ap_inputs())
    ptr = results["required_pto_power"]
    pto_draw = results["rotor_pto_power"]
    pt = TRACTOR_COMMON["pto_power_kw"]
    fs = TRACTOR_COMMON["power_reserve_pct"] / 100.0
    expected_put = ((ptr + pto_draw) / (pt * (1.0 - fs))) * 100.0
    assert results["power_utilization"] == pytest.approx(expected_put)


def test_active_passive_end_to_end_is_sane():
    results = calculate_active_passive_performance(make_ap_inputs())
    assert results["combination_type"] == "active_passive"
    assert results["converged"] is True
    assert 2.0 <= results["slip"] <= 20.0
    assert results["drawbar_power"] > 0
    assert results["fuel_consumption_per_hectare"] > 0
    assert results["ballast_front_required"] >= 0
    assert results["ballast_rear_required"] >= 0
    assert results["calculation_mode"] == "dss_spec_v1_active_passive"


def test_active_passive_requires_rotor_and_passive_tool():
    with pytest.raises(ValueError):
        calculate_active_passive_performance(make_ap_inputs(rotor=None))
    with pytest.raises(ValueError):
        calculate_active_passive_performance(make_ap_inputs(passive_tool=None))


# --- Front wheel numeric / rolling resistance -------------------------------
#
# DSS image8 defines rho_f from Bn = CI*b*d/Wf -- the Section 3 model with the
# front load and front tire. Section 4 never redefines the front wheel: its Bn'
# text is entirely about the wheel numeric / gross traction / coefficient of
# traction at trial slip, i.e. the *driven* wheel. Bn' must therefore not appear
# on the front axle in any mode.


def test_passive_passive_computes_front_mobility_number_separately_from_rear():
    results = calculate_passive_passive_performance(make_pp_inputs())
    bnf = results["legacy_mobility_number_front"]
    bnr = results["legacy_mobility_number_rear"]
    expected_bnf = mobility_number(
        TRACTOR_COMMON["cone_index_kpa"],
        TRACTOR_COMMON["front_section_width_m"],
        TRACTOR_COMMON["front_overall_diameter_m"],
        results["legacy_front_axle_load_n"] / 2.0,
    )
    assert bnf == pytest.approx(expected_bnf)
    assert bnf != pytest.approx(bnr)
    assert bnf > 0


def test_passive_passive_front_mobility_uses_section3_bn_not_bn_prime():
    """Regression guard: Bn' must stay scoped to the driven rear wheel.

    An earlier implementation applied Bn' to the front axle too. The two models
    give materially different numbers at the same load and tire, so this pins
    which one the front wheel actually used.
    """
    results = calculate_passive_passive_performance(make_pp_inputs())
    ci = TRACTOR_COMMON["cone_index_kpa"]
    b = TRACTOR_COMMON["front_section_width_m"]
    d = TRACTOR_COMMON["front_overall_diameter_m"]
    front_wheel_load_n = results["legacy_front_axle_load_n"] / 2.0

    section3_bn = mobility_number(ci, b, d, front_wheel_load_n)
    bn_prime = mobility_number_passive_passive(ci, b, d, front_wheel_load_n)
    assert section3_bn != pytest.approx(bn_prime)  # the two models really do differ
    assert results["legacy_mobility_number_front"] == pytest.approx(section3_bn)
    assert results["legacy_mobility_number_front"] != pytest.approx(bn_prime)


def test_passive_passive_rear_wheel_uses_section3_bn_at_its_own_axle_load():
    """Both wheels use the Section 3 `Bn`; the combination enters through the load.

    The Section 4 `Bn'` expression is dimensionally invalid (m^2/kN) and drives
    nothing. What makes passive-passive differ from a single implement is the
    rear axle load it produces via DTotal, not a different wheel-numeric model.
    """
    results = calculate_passive_passive_performance(make_pp_inputs())
    ci = TRACTOR_COMMON["cone_index_kpa"]
    b = TRACTOR_COMMON["rear_section_width_m"]
    d = TRACTOR_COMMON["rear_overall_diameter_m"]
    rear_wheel_load_n = results["legacy_rear_axle_load_n"] / 2.0

    assert results["legacy_mobility_number_rear"] == pytest.approx(
        mobility_number(ci, b, d, rear_wheel_load_n)
    )
    # ...and definitely not the Section 4 expression.
    assert results["legacy_mobility_number_rear"] != pytest.approx(
        mobility_number_passive_passive(ci, b, d, rear_wheel_load_n)
    )


def test_section4_wheel_numeric_reported_as_diagnostic_only():
    """The transcription stays visible for audit, without influencing any result."""
    results = calculate_passive_passive_performance(make_pp_inputs())
    reported = results["dss_section4_wheel_numeric"]
    assert reported == pytest.approx(
        mobility_number_passive_passive(
            TRACTOR_COMMON["cone_index_kpa"],
            TRACTOR_COMMON["rear_section_width_m"],
            TRACTOR_COMMON["rear_overall_diameter_m"],
            results["legacy_rear_axle_load_n"] / 2.0,
        )
    )
    # It is two orders of magnitude below the band the traction equations need,
    # which is precisely why it cannot be the wheel numeric.
    assert reported < 1.0
    assert results["legacy_mobility_number_rear"] > 5.0


def test_passive_passive_motion_resistance_ratio_includes_front_and_rear():
    results = calculate_passive_passive_performance(make_pp_inputs())
    bnf = results["legacy_mobility_number_front"]
    expected_rho_f = rolling_resistance_front(bnf)
    # motion_resistance_ratio = rho_r + rho_f; rho_f alone must be a strictly
    # smaller, positive contribution.
    assert 0.0 < expected_rho_f < results["motion_resistance_ratio"]


def test_active_passive_computes_front_mobility_number_separately_from_rear():
    results = calculate_active_passive_performance(make_ap_inputs())
    bnf = results["legacy_mobility_number_front"]
    bnr = results["legacy_mobility_number_rear"]
    expected_bnf = mobility_number(
        TRACTOR_COMMON["cone_index_kpa"],
        TRACTOR_COMMON["front_section_width_m"],
        TRACTOR_COMMON["front_overall_diameter_m"],
        results["legacy_front_axle_load_n"] / 2.0,
    )
    assert bnf == pytest.approx(expected_bnf)
    assert bnf != pytest.approx(bnr)
    assert 5.0 < bnf < 80.0
    assert results["motion_resistance_ratio"] > 0.0


# --- Rotor power / equivalent force diagnostics (DSS Section 5.9) -----------


def test_rotor_mechanical_power_formula():
    # Pr = 2*pi*N*T/60, in kW (T in Nm, N in rpm)
    torque_nm, speed_rpm = 53.06, 540.0
    expected_kw = (2.0 * math.pi * speed_rpm * torque_nm / 60.0) / 1000.0
    assert rotor_mechanical_power_kw(torque_nm, speed_rpm) == pytest.approx(expected_kw)


def test_rotor_equivalent_force_formula():
    # Fr = Pr[W] / V[m/s]
    power_kw, speed_kmh = 3.0, 5.0
    expected_fr = (power_kw * 1000.0) / (speed_kmh / 3.6)
    assert rotor_equivalent_force_n(power_kw, speed_kmh) == pytest.approx(expected_fr)


# --- DSS Section 4 wheel numeric Bn' ---------------------------------------
#
# Bn' = (b*d/W) * sqrt( CI / (W/(b*d)) ) * (1 + b/(2*d))
# with the (1 + b/(2*d)) factor OUTSIDE the square root, and W = Rr/2.


def _bn_prime_reference(ci_kpa, b, d, wheel_load_n):
    """Independent re-derivation of the DSS Section 4 formula, for cross-checking."""
    w_kn = wheel_load_n / 1000.0
    area = b * d
    return (area / w_kn) * math.sqrt(ci_kpa / (w_kn / area)) * (1.0 + b / (2.0 * d))


def test_bn_prime_matches_dss_section_4_formula_exactly():
    ci, b, d, w_n = 1200.0, 0.34, 1.30, 7000.0
    assert mobility_number_passive_passive(ci, b, d, w_n) == pytest.approx(_bn_prime_reference(ci, b, d, w_n))


def test_bn_prime_shape_factor_is_outside_the_square_root():
    """(1 + b/2d) must multiply the root, not sit under it."""
    ci, b, d, w_n = 1200.0, 0.34, 1.30, 7000.0
    w_kn = w_n / 1000.0
    area = b * d
    shape = 1.0 + b / (2.0 * d)

    outside = (area / w_kn) * math.sqrt(ci_ratio := ci / (w_kn / area)) * shape
    inside = (area / w_kn) * math.sqrt(ci_ratio * shape)

    got = mobility_number_passive_passive(ci, b, d, w_n)
    assert got == pytest.approx(outside)
    assert got != pytest.approx(inside)


def test_bn_prime_uses_kn_load_convention():
    """CI/(W/(b*d)) is only dimensionless with W in kN, so N->kN conversion must be applied."""
    ci, b, d = 1200.0, 0.34, 1.30
    # Bn' ~ (1/W) * sqrt(1/W) = W^-1.5, so a 10x heavier wheel scales it by 10^-1.5.
    light = mobility_number_passive_passive(ci, b, d, 1000.0)
    heavy = mobility_number_passive_passive(ci, b, d, 10000.0)
    assert heavy == pytest.approx(light * 10.0**-1.5)


def test_bn_prime_decreases_monotonically_with_wheel_load():
    ci, b, d = 1200.0, 0.34, 1.30
    values = [mobility_number_passive_passive(ci, b, d, w) for w in (2000.0, 4000.0, 8000.0, 16000.0)]
    assert values == sorted(values, reverse=True)


def test_bn_prime_is_a_distinct_model_from_single_tool_bn():
    ci, b, d, w_n = 1200.0, 0.34, 1.30, 7000.0
    assert mobility_number_passive_passive(ci, b, d, w_n) != pytest.approx(mobility_number(ci, b, d, w_n))


def test_bn_prime_rejects_non_physical_inputs():
    with pytest.raises(ValueError):
        mobility_number_passive_passive(1200.0, 0.34, 1.30, 0.0)
    with pytest.raises(ValueError):
        mobility_number_passive_passive(1200.0, 0.0, 1.30, 7000.0)
    with pytest.raises(ValueError):
        mobility_number_passive_passive(1200.0, 0.34, 0.0, 7000.0)


def test_section4_wheel_numeric_is_dimensionally_inconsistent():
    """The measurable defect that disqualified `Bn'` as a wheel numeric.

    A dimensionless group built on CI*b*d/W must scale as 1/W. This expression
    carries an extra `b*d/W` factor (units m^2/kN), so it scales as W^-1.5.
    Doubling the load must therefore divide it by 2^1.5, not by 2.
    """
    ci, b, d = 1200.0, 0.34, 1.30
    base_w = 5000.0

    bn_1 = mobility_number(ci, b, d, base_w)
    bn_2 = mobility_number(ci, b, d, 2 * base_w)
    assert bn_2 == pytest.approx(bn_1 / 2.0)  # dimensionless: 1/W

    bnp_1 = mobility_number_passive_passive(ci, b, d, base_w)
    bnp_2 = mobility_number_passive_passive(ci, b, d, 2 * base_w)
    assert bnp_2 == pytest.approx(bnp_1 / 2.0**1.5)  # dimensional: W^-1.5
    assert bnp_2 != pytest.approx(bnp_1 / 2.0)


def test_passive_passive_reduces_exactly_to_the_single_implement_result():
    """The central consistency property of the combination model.

    With a second tool that contributes no weight, no draft and no extra working
    width, and ki = 0, a passive-passive run *is* a single-implement run. Every
    shared quantity must agree exactly -- not approximately -- because both paths
    now use the same Section 3 equations.

    The previous implementation could not satisfy this at all: it used a separate
    axle balance (~21% different) and a wheel numeric that made the mode fail.
    """
    null_tool = PassiveToolInputs(
        implement_type=ImplementType.CULTIVATOR,
        width_m=TOOL_1.width_m,  # no wider, so field capacity is unchanged
        weight_kg=0.0,
        cg_distance_from_hitch_m=TOOL_1.cg_distance_from_hitch_m,
        asae_param_a=0.0,
        asae_param_b=0.0,
        asae_param_c=0.0,
        number_of_tools=9,
    )
    combi_result = calculate_passive_passive_performance(
        make_pp_inputs(tool_1=TOOL_1, tool_2=null_tool, interaction_coefficient=0.0)
    )
    single_result = calculate_legacy_performance(
        LegacyInputs(
            **TRACTOR_COMMON,
            implement_type=TOOL_1.implement_type,
            width_m=TOOL_1.width_m,
            weight_kg=TOOL_1.weight_kg,
            cg_distance_from_hitch_m=TOOL_1.cg_distance_from_hitch_m,
            # Must come from the same source as the combi path: Py/D is now read
            # from the implement record, so a mismatch here would change Py and
            # break the reduction for a reason unrelated to the combination model.
            vertical_horizontal_ratio=TOOL_1.vertical_horizontal_ratio,
            asae_param_a=TOOL_1.asae_param_a,
            asae_param_b=TOOL_1.asae_param_b,
            asae_param_c=TOOL_1.asae_param_c,
        )
    )

    for key in (
        "draft_force",
        "legacy_rear_axle_load_n",
        "legacy_front_axle_load_n",
        "legacy_mobility_number_rear",
        "legacy_mobility_number_front",
        "slip",
        "coefficient_net_traction",
        "traction_efficiency",
        "drawbar_power",
        "required_pto_power",
        "power_utilization",
        "fuel_consumption_per_hectare",
        "field_capacity_theoretical",
        "field_capacity_actual",
        "ballast_front_required",
        "ballast_rear_required",
    ):
        assert combi_result[key] == pytest.approx(single_result[key]), key


# --- Fi consistency across all three modes ----------------------------------


@pytest.mark.parametrize(
    "texture,expected_fi",
    [(SoilTexture.MEDIUM, 0.70), (SoilTexture.COARSE, 0.45)],
)
def test_fi_is_applied_identically_in_all_three_modes(texture, expected_fi):
    """One global Fi, reached the same way by Single, PP and AP.

    All three modes route through `legacy_algorithms.fi_factor`, so this is meant
    to stay true by construction -- the test exists to catch a mode acquiring its
    own lookup. Measured as draft(texture)/draft(fine), which isolates Fi: it is
    the only term in Eq. 3.1 that texture touches.

    Deliberately parametrised on MEDIUM and COARSE only. Every other test in this
    module runs on FINE, where Fi = 1.0 both before and after the switch to a
    global table -- so a mode that drifted would sail straight through them.

    Active-passive is checked on `draft_passive` rather than `draft_force`:
    Deff = Dp + Da - Ta also carries the rotor terms, which do not scale with Fi.
    """
    def ratio(run, key, **kwargs):
        fine = run(soil_texture=SoilTexture.FINE, **kwargs)[key]
        return run(soil_texture=texture, **kwargs)[key] / fine

    def single(*, soil_texture):
        tractor = dict(TRACTOR_COMMON, soil_texture=soil_texture)
        return calculate_legacy_performance(
            LegacyInputs(
                **tractor,
                implement_type=TOOL_1.implement_type,
                width_m=TOOL_1.width_m,
                weight_kg=TOOL_1.weight_kg,
                cg_distance_from_hitch_m=TOOL_1.cg_distance_from_hitch_m,
                vertical_horizontal_ratio=TOOL_1.vertical_horizontal_ratio,
                asae_param_a=TOOL_1.asae_param_a,
                asae_param_b=TOOL_1.asae_param_b,
                asae_param_c=TOOL_1.asae_param_c,
            )
        )

    def passive_passive(*, soil_texture):
        return calculate_passive_passive_performance(make_pp_inputs(soil_texture=soil_texture))

    def active_passive(*, soil_texture):
        return calculate_active_passive_performance(make_ap_inputs(soil_texture=soil_texture))

    assert ratio(single, "draft_force") == pytest.approx(expected_fi)
    assert ratio(passive_passive, "draft_force") == pytest.approx(expected_fi)
    assert ratio(active_passive, "draft_passive") == pytest.approx(expected_fi)

    # Both tools of the pair scale together -- an MB plough and a cultivator,
    # which had *different* Fi rows before this change (0.70 vs 0.85 in medium).
    assert ratio(passive_passive, "draft_1") == pytest.approx(expected_fi)
    assert ratio(passive_passive, "draft_2") == pytest.approx(expected_fi)


# --- Wheel-numeric injection wiring (solve_slip / rear ballast) --------------


def test_solve_slip_evaluates_wheel_numeric_at_half_the_rear_axle_load():
    seen = []

    def spy(ci, b, d, wheel_load_n):
        seen.append(wheel_load_n)
        return 40.0

    rear_axle_load_n = 9000.0
    solve_slip(
        draft_n=1350.0,
        rear_axle_load_n=rear_axle_load_n,
        ci_kpa=1200.0,
        rear_section_width_m=0.34,
        rear_overall_diameter_m=1.30,
        mobility_fn=spy,
    )
    assert seen and all(w == pytest.approx(rear_axle_load_n / 2.0) for w in seen)


def test_solve_slip_defaults_to_single_tool_bn_when_no_model_injected():
    kwargs = dict(
        draft_n=1350.0,
        rear_axle_load_n=9000.0,
        ci_kpa=1200.0,
        rear_section_width_m=0.34,
        rear_overall_diameter_m=1.30,
    )
    assert solve_slip(**kwargs).bn_rear == pytest.approx(solve_slip(**kwargs, mobility_fn=mobility_number).bn_rear)


def test_rear_ballast_evaluates_wheel_numeric_at_half_the_trial_rear_load():
    """DSS: Bn' is evaluated at W = R'/2, re-derived on each fixed-point iteration."""
    seen = []

    def spy(ci, b, d, wheel_load_n):
        seen.append(wheel_load_n)
        return 40.0

    rear_ballast_required_kg(
        draft_n=1350.0,
        rear_axle_load_n=2200.0,
        ci_kpa=1200.0,
        rear_section_width_m=0.34,
        rear_overall_diameter_m=1.30,
        mobility_fn=spy,
    )
    # The seed is max(Rr, D) -- here Rr -- and every call is at W = R'/2.
    assert seen[0] == pytest.approx(2200.0 / 2.0)
    assert all(w > 0 for w in seen)


def test_active_passive_reports_rotor_power_and_force_diagnostics():
    results = calculate_active_passive_performance(make_ap_inputs())
    mpto = results["pto_reaction_moment"]
    rotor_speed_rpm = 540.0
    expected_power_kw = rotor_mechanical_power_kw(mpto, rotor_speed_rpm)
    expected_force_n = rotor_equivalent_force_n(expected_power_kw, TRACTOR_COMMON["speed_kmh"])
    assert results["rotor_mechanical_power"] == pytest.approx(expected_power_kw)
    assert results["rotor_equivalent_force"] == pytest.approx(expected_force_n)
    # These are diagnostic-only: they must not have altered the primary
    # Ta-based effective draft (checked in test_effective_draft_is_...).
    assert "rotor_thrust" in results and "rotor_mechanical_power" in results


# --- Section 5 rotor sub-model, as named functions --------------------------


def test_rotor_thrust_helper_matches_equation_5_2():
    ta = rotor_thrust_n(rotor_efficiency=0.30, pto_power_draw_kw=3.0, speed_kmh=5.0)
    assert ta == pytest.approx(0.30 * 3000.0 / (5.0 / 3.6))


def test_rotor_thrust_helper_enforces_the_documented_efficiency_range():
    for bad in (0.24, 0.46):
        with pytest.raises(ValueError, match="Rotor efficiency"):
            rotor_thrust_n(rotor_efficiency=bad, pto_power_draw_kw=3.0, speed_kmh=5.0)


def test_effective_draft_helper_matches_equation_5_3():
    d_eff = effective_draft_n(
        passive_draft_n=2000.0, rotor_mechanical_resistance_n=500.0, thrust_n=800.0
    )
    assert d_eff == pytest.approx(1700.0)
    with pytest.raises(ValueError, match="Effective draft"):
        effective_draft_n(passive_draft_n=100.0, rotor_mechanical_resistance_n=50.0, thrust_n=200.0)


def test_pto_moment_and_equivalent_rear_load_helpers():
    mpto = pto_reaction_moment_nm(pto_power_draw_kw=3.0, rotor_speed_rpm=540.0)
    assert mpto == pytest.approx(9550.0 * 3.0 / 540.0)  # Eq. 5.4
    weq = pto_equivalent_rear_load_n(pto_reaction_moment=mpto, wheelbase_m=2.3)
    assert weq == pytest.approx(mpto / 2.3)  # Eq. 5.5
    with pytest.raises(ValueError):
        pto_reaction_moment_nm(pto_power_draw_kw=3.0, rotor_speed_rpm=0.0)


def test_active_passive_rear_axle_load_adds_mpto_over_l_and_fv():
    """DSS Eq. 5.6/5.7a: Rr* = Rr + MPTO/L + Fv."""
    base = calculate_active_passive_performance(make_ap_inputs())
    fv = 400.0
    with_fv = calculate_active_passive_performance(
        make_ap_inputs(rotor=make_rotor(dynamic_vertical_force_n=fv))
    )
    # Fv adds directly to the rear axle and is removed from the front by the
    # vertical-equilibrium expression Rf = Wt + Wp + Wa + Py - Rr.
    assert with_fv["legacy_rear_axle_load_n"] == pytest.approx(base["legacy_rear_axle_load_n"] + fv)
    assert with_fv["legacy_front_axle_load_n"] == pytest.approx(base["legacy_front_axle_load_n"] - fv)
    # And the MPTO/L term is present in the reported equivalent load.
    assert base["pto_equivalent_rear_load"] == pytest.approx(
        base["pto_reaction_moment"] / TRACTOR_COMMON["wheelbase_m"]
    )


def test_active_passive_front_ballast_uses_the_shared_solver():
    """Active-passive no longer has its own front-ballast closed form.

    DSS Eq. 5.10/5.11 (`BRf = (0.20*Wt - Rf)/0.80`) assumed ballast lands entirely
    on the front axle. The shared solver instead re-solves this mode's own
    Eq. 3.5 balance -- including the rotor's `Weq + Fv` rear load -- so the
    reported mass is the one that actually puts Kwef on 0.20 under the same
    balance the axle loads themselves come from. Both references do it this way.
    """
    # A heavy rotor set back from the hitch unloads the front axle below
    # Kwef = 0.20. Under the Section 3 balance every kilogram hung behind the rear
    # axle removes front-axle load, so this needs no exaggerated geometry.
    inputs = make_ap_inputs(rotor=make_rotor(weight_kg=900.0, cg_distance_from_hitch_m=1.2))
    results = calculate_active_passive_performance(inputs)
    assert results["front_weight_utilization"] < FRONT_BALLAST_TARGET_KWEF

    ballast_kg = results["ballast_front_required"]
    assert ballast_kg is not None and ballast_kg > 0

    # The old closed form would have under-called it: it credits the whole mass to
    # the front axle, where the real balance sends part of it rearward.
    wt_n = (TRACTOR_COMMON["front_axle_weight_kg"] + TRACTOR_COMMON["rear_axle_weight_kg"]) * GRAVITY
    closed_form_kg = (
        (FRONT_BALLAST_TARGET_KWEF * wt_n - results["legacy_front_axle_load_n"])
        / (1.0 - FRONT_BALLAST_TARGET_KWEF)
    ) / GRAVITY
    assert ballast_kg > closed_form_kg


def test_active_passive_rear_ballast_matches_rreq_minus_rr():
    """DSS Eq. 5.12/5.13: Rreq = Deff/mu(S), BRr = Rreq - Rr."""
    results = calculate_active_passive_performance(make_ap_inputs())
    r_req = results["draft_force"] / results["coefficient_net_traction"]
    raw_br_r = r_req - results["legacy_rear_axle_load_n"]
    assert results["ballast_rear_required"] == pytest.approx(max(0.0, raw_br_r) / GRAVITY)


def test_active_passive_negative_rear_ballast_reported_as_zero_with_a_note():
    """A rotor that already over-loads the rear axle needs no ballast (Section 5.8)."""
    results = calculate_active_passive_performance(make_ap_inputs())
    r_req = results["draft_force"] / results["coefficient_net_traction"]
    assert r_req < results["legacy_rear_axle_load_n"]  # the negative-BRr branch
    assert results["ballast_rear_required"] == 0.0
    assert any("Rear ballast is not required" in w for w in results["warnings"])


def test_active_passive_xeff_and_fuel_use_combined_pto_power():
    """DSS Eq. 5.15: Xeff = (Ptr + PPTO)/Pt, and FCcombi is SFC evaluated at Xeff."""
    results = calculate_active_passive_performance(make_ap_inputs())
    expected_x = (results["required_pto_power"] + results["rotor_pto_power"]) / TRACTOR_COMMON["pto_power_kw"]
    assert results["pto_power_fraction_effective"] == pytest.approx(expected_x)
    assert results["specific_fuel_consumption"] == pytest.approx(
        specific_fuel_consumption_l_per_kwh(expected_x)
    )


# --- DTotal substitution through the rest of the Section 4 chain -------------


def test_dtotal_not_the_naive_sum_drives_axle_load_power_and_fuel():
    """DTotal must replace D everywhere Section 3 wrote D."""
    low_ki = calculate_passive_passive_performance(make_pp_inputs(interaction_coefficient=0.0))
    high_ki = calculate_passive_passive_performance(make_pp_inputs(interaction_coefficient=0.25))

    # Rr carries the -DTotal*Yd term of Eq. 3.5: draft acting below the ground
    # reference transfers weight *off* the rear axle, so reducing DTotal (higher
    # ki) leaves slightly more load on it. The opposite sign in the Section 4
    # restatement is one of the two defects that made that form unusable.
    assert high_ki["legacy_rear_axle_load_n"] > low_ki["legacy_rear_axle_load_n"]
    # DBp = DTotal * S, exactly.
    assert high_ki["drawbar_power"] == pytest.approx(
        high_ki["draft_force"] * TRACTOR_COMMON["speed_kmh"] / 3.6 / 1000.0
    )
    # ... and the document's own claim: less draft => less required PTO power and fuel.
    assert high_ki["required_pto_power"] < low_ki["required_pto_power"]
    assert high_ki["fuel_consumption_per_hectare"] < low_ki["fuel_consumption_per_hectare"]


def test_passive_passive_reports_combined_py_from_each_tools_own_ratio():
    """Documented gap-fill: Py = sum(ratio_i * D_i), not a single ratio on DTotal."""
    results = calculate_passive_passive_performance(make_pp_inputs())
    expected_py = (
        py_over_d_ratio(TOOL_1.implement_type) * results["draft_1"]
        + py_over_d_ratio(TOOL_2.implement_type) * results["draft_2"]
    )
    # Recover Py from the vertical-equilibrium identity Rf = Wt + Wi + Py - Rr.
    wt_n = (TRACTOR_COMMON["front_axle_weight_kg"] + TRACTOR_COMMON["rear_axle_weight_kg"]) * GRAVITY
    wi_n = (TOOL_1.weight_kg + TOOL_2.weight_kg) * GRAVITY
    recovered_py = (
        results["legacy_front_axle_load_n"] + results["legacy_rear_axle_load_n"] - wt_n - wi_n
    )
    assert recovered_py == pytest.approx(expected_py)


# --- Engine-torque pull limit wiring (DSS Eq. 3.4) --------------------------


def test_combi_modes_report_pet_only_when_engine_torque_is_known():
    assert calculate_active_passive_performance(make_ap_inputs())["engine_torque_limited_pull"] is None
    with_torque = calculate_active_passive_performance(make_ap_inputs(max_engine_torque_nm=300.0))
    assert with_torque["engine_torque_limited_pull"] == pytest.approx(
        engine_torque_limited_pull_n(
            max_engine_torque_nm=300.0,
            rear_rolling_radius_m=TRACTOR_COMMON["rear_rolling_radius_m"],
            transmission_efficiency_pct=TRACTOR_COMMON["transmission_efficiency_pct"],
            rho_r=rolling_resistance_rear(
                with_torque["legacy_mobility_number_rear"], with_torque["slip"] / 100.0
            ),
            rho_f=rolling_resistance_front(with_torque["legacy_mobility_number_front"]),
            rear_axle_load_n=with_torque["legacy_rear_axle_load_n"],
            front_axle_load_n=with_torque["legacy_front_axle_load_n"],
        )
    )


def test_pet_warns_but_does_not_cap_the_solved_pull():
    """The document never states Pet limits Pst, so it must stay diagnostic."""
    unlimited = calculate_active_passive_performance(make_ap_inputs())
    starved = calculate_active_passive_performance(make_ap_inputs(max_engine_torque_nm=1.0))
    assert starved["engine_torque_limited_pull"] < starved["draft_force"]
    assert any("Engine-torque pull limit" in w for w in starved["warnings"])
    # Slip, traction and power are untouched by the limit.
    assert starved["slip"] == pytest.approx(unlimited["slip"])
    assert starved["traction_efficiency"] == pytest.approx(unlimited["traction_efficiency"])
    assert starved["required_pto_power"] == pytest.approx(unlimited["required_pto_power"])


# --- Passive-passive physical sweep -----------------------------------------
#
# Realistic operating envelopes across the ranges the API accepts (speed 2-8 km/h,
# depth 5-35 cm, cone index 300-3000 kPa, width 0.5-5 m). Every case must produce
# a physically coherent solution, not merely "not crash".

PP_SWEEP = [
    # (label, condition overrides, ki)
    ("normal", {}, 0.10),
    ("light-shallow-slow", dict(depth_cm=6.0, speed_kmh=2.5), 0.10),
    ("heavy-deep-fast", dict(depth_cm=32.0, speed_kmh=7.5), 0.10),
    ("soft-soil-high-slip", dict(depth_cm=30.0, cone_index_kpa=400.0), 0.10),
    ("hard-soil", dict(cone_index_kpa=2800.0), 0.10),
    ("boundary-low", dict(speed_kmh=2.0, depth_cm=5.0, cone_index_kpa=300.0), 0.0),
    ("boundary-high", dict(speed_kmh=8.0, depth_cm=35.0, cone_index_kpa=3000.0), 0.25),
    ("ki-zero", {}, 0.0),
    ("ki-max", {}, 0.25),
    ("coarse-soil", dict(soil_texture=SoilTexture.COARSE), 0.10),
    ("medium-soil", dict(soil_texture=SoilTexture.MEDIUM), 0.10),
]


@pytest.mark.parametrize("label,overrides,ki", PP_SWEEP, ids=[c[0] for c in PP_SWEEP])
def test_passive_passive_sweep_is_physically_coherent(label, overrides, ki):
    results = calculate_passive_passive_performance(
        make_pp_inputs(interaction_coefficient=ki, **overrides)
    )

    wt_n = (
        TRACTOR_COMMON["front_axle_weight_kg"] + TRACTOR_COMMON["rear_axle_weight_kg"]
    ) * GRAVITY
    wi_n = (TOOL_1.weight_kg + TOOL_2.weight_kg) * GRAVITY
    py_n = (
        py_over_d_ratio(TOOL_1.implement_type) * results["draft_1"]
        + py_over_d_ratio(TOOL_2.implement_type) * results["draft_2"]
    )

    # Vertical equilibrium: the axle split must account for every applied force.
    assert results["legacy_rear_axle_load_n"] + results["legacy_front_axle_load_n"] == pytest.approx(
        wt_n + wi_n + py_n
    )
    # Both axles carry load -- neither wheel has lifted.
    assert results["legacy_rear_axle_load_n"] > 0
    assert results["legacy_front_axle_load_n"] > 0

    # DTotal is the reduced sum, and drawbar power follows from it exactly.
    assert results["draft_force"] == pytest.approx(
        (1.0 - ki) * (results["draft_1"] + results["draft_2"])
    )
    speed_kmh = overrides.get("speed_kmh", TRACTOR_COMMON["speed_kmh"])
    assert results["drawbar_power"] == pytest.approx(
        results["draft_force"] * speed_kmh / 3.6 / 1000.0
    )

    # Wheel numerics land in the band the traction equations are valid over.
    assert results["legacy_mobility_number_rear"] > 1.0
    assert results["legacy_mobility_number_front"] > 1.0

    # Slip stays on the DSS schedule and traction is physical.
    assert 2.0 <= results["slip"] <= 20.0
    assert 0.0 < results["traction_efficiency"] <= 100.0
    assert results["coefficient_net_traction"] > 0.0

    # Costs are positive and finite.
    assert results["required_pto_power"] > 0.0
    assert results["fuel_consumption_per_hectare"] > 0.0
    assert results["field_capacity_actual"] <= results["field_capacity_theoretical"]
    assert results["ballast_front_required"] >= 0.0
    # Rear ballast is None when the requirement genuinely cannot be sized -- the real
    # outcome for the heaviest sweep cases, where the soil develops no net pull at the
    # 15% target slip. That is reported with a warning rather than raised, so the rest
    # of the result set survives. Both states are valid; a fabricated number is not.
    rear_ballast = results["ballast_rear_required"]
    if rear_ballast is None:
        assert any("Rear ballast could not be sized" in w for w in results["warnings"])
    else:
        assert rear_ballast >= 0.0


def test_passive_passive_draft_rises_with_depth_speed_and_width():
    """Monotonic response to the three ASAE draft drivers."""
    base = calculate_passive_passive_performance(make_pp_inputs())

    deeper = calculate_passive_passive_performance(make_pp_inputs(depth_cm=25.0))
    assert deeper["draft_force"] > base["draft_force"]

    faster = calculate_passive_passive_performance(make_pp_inputs(speed_kmh=7.0))
    assert faster["draft_force"] > base["draft_force"]

    wider = calculate_passive_passive_performance(
        make_pp_inputs(
            tool_1=PassiveToolInputs(
                implement_type=TOOL_1.implement_type,
                width_m=TOOL_1.width_m * 2.0,
                weight_kg=TOOL_1.weight_kg,
                cg_distance_from_hitch_m=TOOL_1.cg_distance_from_hitch_m,
                asae_param_a=TOOL_1.asae_param_a,
                asae_param_b=TOOL_1.asae_param_b,
                asae_param_c=TOOL_1.asae_param_c,
            )
        )
    )
    assert wider["draft_force"] > base["draft_force"]


def test_passive_passive_softer_soil_lowers_bn_and_raises_slip():
    """Cone index drives the wheel numeric, which drives slip."""
    firm = calculate_passive_passive_performance(make_pp_inputs(cone_index_kpa=2000.0))
    soft = calculate_passive_passive_performance(make_pp_inputs(cone_index_kpa=500.0))

    assert soft["legacy_mobility_number_rear"] < firm["legacy_mobility_number_rear"]
    assert soft["slip"] >= firm["slip"]
    assert soft["traction_efficiency"] <= firm["traction_efficiency"]


def test_passive_passive_reports_a_clean_diagnostic_when_soil_cannot_pull():
    """Very soft soil under a heavy pull must explain itself, not crash.

    The engine either converges, or reports non-convergence with a warning naming
    the cause. It must never raise an unhandled arithmetic error.
    """
    heavy_tool = PassiveToolInputs(
        implement_type=ImplementType.MB_PLOUGH, width_m=4.5, weight_kg=600.0,
        cg_distance_from_hitch_m=0.9, asae_param_a=100.0, asae_param_b=50.0, asae_param_c=10.0,
    )
    try:
        results = calculate_passive_passive_performance(
            make_pp_inputs(
                tool_1=heavy_tool, depth_cm=35.0, speed_kmh=8.0, cone_index_kpa=300.0,
                interaction_coefficient=0.0,
            )
        )
    except ValueError as exc:
        # A refusal is acceptable, provided it explains itself.
        assert str(exc).strip()
        return

    if not results["converged"]:
        assert any(
            "20%" in w or "No net traction" in w for w in results["warnings"]
        ), results["warnings"]
