"""Tests for the shared DSS primitives (app.core.dss_shared).

These are the blocks all three simulation modes run through, so each is verified
against a hand-computed expectation rather than against the implementation.
"""

from __future__ import annotations

import math

import pytest

from app.core.constants import (
    DIESEL_CALORIFIC_VALUE,
    FIELD_EFFICIENCY_CLAMP,
)
from app.core.dss_shared import (
    draft_force_n,
    field_capacity,
    geometry_terms,
    power_and_fuel,
    put_load_status,
    require_finite,
    require_positive,
    result_envelope,
    round_half_away_from_zero,
    safe_div,
    safe_sqrt,
    specific_fuel_consumption_l_per_kwh,
)
from app.core.legacy_algorithms import engine_torque_limited_pull_n, wheel_response, mobility_number


# --- Numerical safety --------------------------------------------------------


def test_require_positive_rejects_zero_and_negative_and_names_the_quantity():
    for bad in (0.0, -1.0):
        with pytest.raises(ValueError) as exc:
            require_positive("cone index", bad)
        assert "cone index" in str(exc.value)


def test_require_finite_rejects_nan_and_infinity():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError) as exc:
            require_finite("draft force", bad)
        assert "draft force" in str(exc.value)


def test_require_positive_passes_through_valid_values():
    assert require_positive("speed", 4.0) == 4.0
    assert require_finite("speed", -4.0) == -4.0  # finite but signed is fine here


def test_safe_div_reports_the_quantity_on_zero_denominator():
    assert safe_div("power", 10.0, 2.0) == pytest.approx(5.0)
    with pytest.raises(ValueError) as exc:
        safe_div("power utilization", 1.0, 0.0)
    assert "power utilization" in str(exc.value)


def test_safe_sqrt_rejects_negative_radicand():
    assert safe_sqrt("wheel numeric", 9.0) == pytest.approx(3.0)
    with pytest.raises(ValueError) as exc:
        safe_sqrt("wheel numeric", -1.0)
    assert "wheel numeric" in str(exc.value)


# --- Draft (DSS Eq. 3.1) -----------------------------------------------------


def test_draft_force_matches_dss_equation_3_1():
    # D = F*(A + B*S + C*S^2)*W*T -- no divisor on T. W in m, T in cm, D in N.
    # A=652, B=0, C=5.1 with F=0.70 is the ASABE D497 moldboard-plough row and its
    # medium-texture F2, so this doubles as a check against the published model:
    # a 2 m plough at 15 cm must need ~15.4 kN, not ~1.5 kN.
    fi, a, b, c, s, w, t = 0.70, 652.0, 0.0, 5.1, 4.0, 2.0, 15.0
    expected = fi * (a + b * s + c * s * s) * w * t
    got = draft_force_n(
        fi=fi, asae_param_a=a, asae_param_b=b, asae_param_c=c, speed_kmh=s, width_m=w, depth_cm=t
    )
    assert got == pytest.approx(expected)
    assert got == pytest.approx(0.70 * (652.0 + 5.1 * 16.0) * 2.0 * 15.0)
    assert got == pytest.approx(15405.6)


def test_draft_force_is_linear_in_width_and_depth_and_quadratic_in_speed():
    kw = dict(fi=1.0, asae_param_a=100.0, asae_param_b=0.0, asae_param_c=10.0, depth_cm=10.0)
    base = draft_force_n(speed_kmh=4.0, width_m=1.0, **kw)
    assert draft_force_n(speed_kmh=4.0, width_m=2.0, **kw) == pytest.approx(2.0 * base)
    kw_deep = dict(kw, depth_cm=20.0)
    assert draft_force_n(speed_kmh=4.0, width_m=1.0, **kw_deep) == pytest.approx(2.0 * base)
    # Pure C*S^2 term doubles speed -> quadruples that term
    only_c = dict(fi=1.0, asae_param_a=0.0, asae_param_b=0.0, asae_param_c=10.0, depth_cm=10.0, width_m=1.0)
    assert draft_force_n(speed_kmh=8.0, **only_c) == pytest.approx(
        4.0 * draft_force_n(speed_kmh=4.0, **only_c)
    )


def test_draft_force_rejects_non_physical_geometry():
    kw = dict(fi=1.0, asae_param_a=100.0, asae_param_b=0.0, asae_param_c=0.0)
    with pytest.raises(ValueError):
        draft_force_n(speed_kmh=4.0, width_m=0.0, depth_cm=10.0, **kw)
    with pytest.raises(ValueError):
        draft_force_n(speed_kmh=0.0, width_m=1.0, depth_cm=10.0, **kw)
    with pytest.raises(ValueError):
        draft_force_n(speed_kmh=4.0, width_m=1.0, depth_cm=-5.0, **kw)


# --- Geometry ----------------------------------------------------------------


def test_geometry_terms_match_dss_definitions():
    g = geometry_terms(depth_cm=15.0, rear_rolling_radius_m=0.58, front_rolling_radius_m=0.40)
    assert g.yd_m == pytest.approx((2.0 / 3.0) * 0.15)  # Yd = (2/3)*Td, Td in m
    assert g.er_m == pytest.approx(0.1 * 0.58)
    assert g.ef_m == pytest.approx(0.1 * 0.40)


def test_geometry_terms_reject_non_physical_radii():
    with pytest.raises(ValueError):
        geometry_terms(depth_cm=15.0, rear_rolling_radius_m=0.0, front_rolling_radius_m=0.40)


# --- Field capacity ----------------------------------------------------------


def test_field_capacity_theoretical_matches_dss():
    cap = field_capacity(speed_kmh=5.0, width_m=1.5, field_area_ha=2.0, field_width_m=100.0)
    assert cap.fc_th == pytest.approx(5.0 * 1.5 / 10.0)  # FCth = S*W/10


def test_field_capacity_actual_and_turning_time_are_internally_consistent():
    cap = field_capacity(speed_kmh=5.0, width_m=1.5, field_area_ha=2.0, field_width_m=100.0)
    expected_turning = 15.56 + 2.61 * (1.5 / 5.0) - 1.41 * 5.0
    assert cap.turning_time_s == pytest.approx(expected_turning)
    assert cap.number_turns == round(100.0 / 1.5)
    expected_total = (expected_turning * 2.0 * cap.number_turns) / 3600.0 + 2.0 / cap.fc_th
    assert cap.total_time_h == pytest.approx(expected_total)
    assert cap.fc_ac == pytest.approx(2.0 / expected_total)
    assert cap.fc_ac < cap.fc_th  # turning always costs time


def test_field_capacity_exposes_raw_efficiency_alongside_the_clamped_one():
    # A field so small that turning dominates drives the raw ratio below the clamp.
    cap = field_capacity(speed_kmh=5.0, width_m=0.6, field_area_ha=0.05, field_width_m=200.0)
    assert cap.field_eff_raw_pct < FIELD_EFFICIENCY_CLAMP[0]
    assert cap.field_eff_pct == pytest.approx(FIELD_EFFICIENCY_CLAMP[0])
    assert cap.field_eff_raw_pct == pytest.approx((cap.fc_ac / cap.fc_th) * 100.0)


def test_round_half_away_from_zero_disagrees_with_python_builtin_at_ties():
    """Pins the exact cases where Excel's ROUND()/JS's Math.round() diverge from
    Python's banker's-rounding builtin -- an even integer part with a .5 tie."""
    assert round_half_away_from_zero(2.5) == 3
    assert round(2.5) == 2  # the builtin's tie-to-even, for contrast
    assert round_half_away_from_zero(0.5) == 1
    assert round(0.5) == 0
    assert round_half_away_from_zero(4.5) == 5
    # Odd integer part: both rules agree, so no behavioural change there.
    assert round_half_away_from_zero(1.5) == round(1.5) == 2
    assert round_half_away_from_zero(3.5) == round(3.5) == 4
    # Negative ties round away from zero too.
    assert round_half_away_from_zero(-2.5) == -3


def test_field_capacity_number_turns_matches_excel_and_html_at_a_half_integer_ratio():
    """`field_width/width == 2.5` is exactly the ratio where Excel's
    `C68 = ROUND(C12/C14, 0)` and both HTML's `Math.round()` round up to 3,
    while Python's builtin `round()` would round down to 2. Regression guard
    for the fix: `field_capacity` must agree with the reference, not the
    builtin's tie-to-even rule.
    """
    cap = field_capacity(speed_kmh=4.0, width_m=40.0, field_area_ha=2.0, field_width_m=100.0)
    assert cap.number_turns == 3
    assert cap.number_turns != round(100.0 / 40.0)  # the builtin would say 2


# --- Specific fuel consumption ------------------------------------------------


@pytest.mark.parametrize("x", [0.0, 0.25, 0.5, 0.75, 1.0])
def test_specific_fuel_consumption_matches_asabe_equation(x):
    expected = (2.64 * x + 3.91) - (0.203 * math.sqrt(738.0 * x + 173.0))
    assert specific_fuel_consumption_l_per_kwh(x) == pytest.approx(expected)


def test_specific_fuel_consumption_floors_negative_fraction_at_zero():
    assert specific_fuel_consumption_l_per_kwh(-0.3) == pytest.approx(
        specific_fuel_consumption_l_per_kwh(0.0)
    )


# --- Power and fuel -----------------------------------------------------------


POWER_KW = dict(
    draft_n=5000.0,
    speed_kmh=5.0,
    te_pct=65.0,
    transmission_efficiency_pct=86.0,
    power_reserve_pct=20.0,
    pto_power_kw=45.0,
    fc_th=0.75,
    fc_ac=0.6,
)


def test_power_chain_matches_dss_equations_3_4_3_11_3_12():
    p = power_and_fuel(**POWER_KW)
    expected_pdb = 5000.0 * 5.0 / 3.6 / 1000.0
    assert p.pdb_kw == pytest.approx(expected_pdb)
    expected_ptr = expected_pdb / (0.65 * 0.86)
    assert p.ptr_kw == pytest.approx(expected_ptr)
    assert p.put_pct == pytest.approx(expected_ptr / (45.0 * 0.8) * 100.0)
    assert p.x_fraction == pytest.approx(expected_ptr / 45.0)


def test_extra_pto_power_enters_put_and_x_but_not_drawbar_power():
    """DSS Section 5: Put and Xeff add PPTO; Section 4 is the PPTO=0 special case."""
    base = power_and_fuel(**POWER_KW)
    with_rotor = power_and_fuel(extra_pto_kw=6.0, **POWER_KW)
    assert with_rotor.pdb_kw == pytest.approx(base.pdb_kw)
    assert with_rotor.ptr_kw == pytest.approx(base.ptr_kw)
    assert with_rotor.x_fraction == pytest.approx((base.ptr_kw + 6.0) / 45.0)
    assert with_rotor.put_pct == pytest.approx((base.ptr_kw + 6.0) / (45.0 * 0.8) * 100.0)
    # extra_pto_kw=0 must be exactly the Section 3/4 form
    assert power_and_fuel(extra_pto_kw=0.0, **POWER_KW).put_pct == pytest.approx(base.put_pct)


def test_fuel_uses_the_drawbar_basis_and_reports_the_pto_basis_separately():
    """Fuel is billed against drawbar power, matching `docs/tillage_dss (2).html`
    exactly (`powerAndFuel`: `fuelLph = sfc * pdbKw`) and the spreadsheet's `C65`.

    A PTO-power basis was adopted for one session on physical grounds
    (`Ptr = DBp/(TE*eta_t)` is what the engine actually makes, and burns fuel to
    make it regardless of how much survives wheel slip) -- that argument still
    holds physically, but production reverted to drawbar for HTML parity. The PTO
    reading survives as the diagnostic `fuel_lph_pto_basis`, feeding nothing.
    """
    p = power_and_fuel(extra_pto_kw=6.0, **POWER_KW)
    assert p.fuel_basis == "drawbar"
    assert p.fuel_lph == pytest.approx(p.sfc * p.pdb_kw)
    assert p.fuel_lph == pytest.approx(p.fuel_lph_drawbar_basis)
    # The PTO-power reading survives as a diagnostic, and must feed nothing.
    assert p.fuel_lph_pto_basis == pytest.approx(p.sfc * (p.ptr_kw + 6.0))
    assert p.fuel_lph != pytest.approx(p.fuel_lph_pto_basis)
    assert p.fuel_l_per_ha == pytest.approx(p.fuel_lph / POWER_KW["fc_ac"])


def test_the_two_fuel_bases_differ_by_exactly_the_traction_loss():
    """Pins the size of the difference: the ratio is 1/(TE x eta_t), nothing else."""
    p = power_and_fuel(extra_pto_kw=0.0, **POWER_KW)
    expected = 1.0 / (
        POWER_KW["te_pct"] / 100.0 * POWER_KW["transmission_efficiency_pct"] / 100.0
    )
    assert p.fuel_lph_pto_basis / p.fuel_lph == pytest.approx(expected)


def test_overall_efficiency_uses_the_centralised_calorific_value():
    p = power_and_fuel(**POWER_KW)
    expected = (
        p.pdb_kw * 3600.0 / 1000.0
        / (POWER_KW["fc_th"] * p.fuel_l_per_ha * DIESEL_CALORIFIC_VALUE)
        * 100.0
    )
    assert p.overall_pct == pytest.approx(expected)


def test_fuel_per_hectare_is_floored_but_never_capped():
    """Both reference implementations report the raw L/h ÷ ha/h ratio.

    An earlier 200 L/ha cap disguised genuinely over-worked pairings behind a
    plausible-looking number; only the floor at 0 survives.
    """
    p = power_and_fuel(**dict(POWER_KW, draft_n=400000.0, fc_ac=0.01))
    assert p.fuel_l_per_ha == pytest.approx(p.fuel_lph / 0.01)
    assert p.fuel_l_per_ha > 200.0


def test_power_and_fuel_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        power_and_fuel(**dict(POWER_KW, pto_power_kw=0.0))
    with pytest.raises(ValueError):
        power_and_fuel(**dict(POWER_KW, te_pct=0.0))
    with pytest.raises(ValueError):
        power_and_fuel(**dict(POWER_KW, power_reserve_pct=100.0))


# --- Put status table ---------------------------------------------------------


@pytest.mark.parametrize(
    "put,expected",
    [
        (94.9, "Tractor is Underloaded"),
        (95.0, "Tractor is properly loaded"),
        (100.0, "Tractor is properly loaded"),
        (100.1, "Tractor is Overloaded"),
    ],
)
def test_put_load_status_table(put, expected):
    assert put_load_status(put) == expected


# --- Result envelope ----------------------------------------------------------


def test_result_envelope_reports_load_status_when_converged():
    env = result_envelope(
        slip=8.0, net_traction_coefficient=0.30, front_weight_utilization=0.28,
        fi=0.70, put_pct=97.0, field_eff_pct=80.0, converged=True,
    )
    assert env.load_status == "Tractor is properly loaded"
    assert env.status_message == env.load_status
    # Nothing in Table 4.2 fires here: slip 8 < 15, mu 0.30 < 0.55 (medium),
    # Kwef 0.28 > 0.20, Put 97 < 100. The document gives no "all clear" message,
    # so the absence of advice is the answer -- and the list must be empty, not [""].
    assert env.recommendations == ""
    assert env.recommendation_messages == []


def test_result_envelope_falls_back_to_status_when_not_converged():
    env = result_envelope(
        slip=20.0, net_traction_coefficient=0.70, front_weight_utilization=0.10,
        fi=0.70, put_pct=130.0, field_eff_pct=55.0, converged=False,
    )
    assert env.status_message == env.status
    assert env.status_message != env.load_status


# --- Wheel response (front model is hard-wired to Section 3's Bn) --------------


def test_wheel_response_front_uses_section3_bn_regardless_of_rear_model():
    wr = wheel_response(
        ci_kpa=1200.0,
        front_section_width_m=0.24,
        front_overall_diameter_m=0.90,
        front_axle_load_n=9000.0,
        bn_rear=0.7,  # a deliberately Bn'-like rear value
        slip_fraction=0.10,
    )
    assert wr.bn_front == pytest.approx(mobility_number(1200.0, 0.24, 0.90, 4500.0))
    assert wr.mr_ratio == pytest.approx(wr.rho_r + wr.rho_f)
    assert wr.rho_f == pytest.approx(1.0 / wr.bn_front + 0.04)  # no slip term on the front


# --- Engine-torque pull limit (DSS Eq. 3.4) -----------------------------------


def test_engine_torque_limited_pull_matches_dss_equation_3_4():
    # Pet = T*eta_ea/r - (rho_r*Rr + rho_f*Rf)
    pet = engine_torque_limited_pull_n(
        max_engine_torque_nm=300.0,
        rear_rolling_radius_m=0.58,
        transmission_efficiency_pct=86.0,
        rho_r=0.09,
        rho_f=0.05,
        rear_axle_load_n=20000.0,
        front_axle_load_n=8000.0,
    )
    expected = (300.0 * 0.86) / 0.58 - (0.09 * 20000.0 + 0.05 * 8000.0)
    assert pet == pytest.approx(expected)


def test_engine_torque_limited_pull_rises_with_torque_and_falls_with_motion_resistance():
    kw = dict(
        rear_rolling_radius_m=0.58, transmission_efficiency_pct=86.0,
        rho_r=0.09, rho_f=0.05, rear_axle_load_n=20000.0, front_axle_load_n=8000.0,
    )
    low = engine_torque_limited_pull_n(max_engine_torque_nm=300.0, **kw)
    high = engine_torque_limited_pull_n(max_engine_torque_nm=600.0, **kw)
    assert high > low
    draggy = engine_torque_limited_pull_n(
        max_engine_torque_nm=300.0, **dict(kw, rho_r=0.20)
    )
    assert draggy < low


def test_engine_torque_limited_pull_rejects_invalid_geometry():
    kw = dict(
        transmission_efficiency_pct=86.0, rho_r=0.09, rho_f=0.05,
        rear_axle_load_n=20000.0, front_axle_load_n=8000.0,
    )
    with pytest.raises(ValueError):
        engine_torque_limited_pull_n(max_engine_torque_nm=300.0, rear_rolling_radius_m=0.0, **kw)
    with pytest.raises(ValueError):
        engine_torque_limited_pull_n(max_engine_torque_nm=0.0, rear_rolling_radius_m=0.58, **kw)
