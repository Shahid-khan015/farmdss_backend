"""Parity harness against the DSS reference stack.

**Scope note.** The engine's headline tractive efficiency divides by the Brixius
**envelope** `mu_g`, per DSS specification Eq. (3.2) -- stored in the DOCX as a
MathType/OLE object (`word/media/image1.wmf`) and reading `TE = mu*(1-S)/mu_g`.
All three reference artifacts agree on that denominator:

    DOCX Eq. (3.2)                          -> mu_g   [SPECIFICATION]
    tillage_dss.html tractiveEfficiencyPct  -> mu_g
    spreadsheet C59 = (C58*(1-C55))/C57     -> mu_g

An earlier revision of this engine divided by the gross traction ratio developed
*at the operating slip*, which made it the sole outlier against its own spec. That
value is retained as the diagnostic `traction_efficiency_at_slip_percent`.

This file pins both bases against transcriptions of the HTML's own functions, so
the engine stays reconcilable with the reference stack line by line.
"""
from __future__ import annotations

import math

import pytest

from app.core.combi_algorithms import (
    calculate_active_passive_performance,
    calculate_passive_passive_performance,
)
from app.core.constants import (
    MAX_SLIP_PCT,
    ROLLING_RESISTANCE_BASE,
    SLIP_INCREMENT_PCT,
    SLIP_INITIAL_PCT,
    TRACTION_BN_EXPONENT_COEFF,
    TRACTION_MU_G_SCALE,
    TRACTION_SLIP_EXPONENT_COEFF,
)
from app.core.dss_shared import draft_force_n
from app.core.legacy_algorithms import (
    calculate_legacy_performance,
    gross_traction_ratio,
    mobility_number,
    net_traction_coefficient,
    solve_slip,
    traction_efficiency_at_slip_pct,
    traction_efficiency_envelope_percent,
    traction_efficiency_percent,
)

from test_combi_algorithms import make_ap_inputs, make_pp_inputs, make_rotor
from test_reference_case_validation import _reference_inputs

BN_GRID = (5.0, 8.0, 15.0, 25.0, 40.0)
SLIP_GRID = (0.02, 0.06, 0.10, 0.15, 0.20)


# --- Reference formulas, transcribed from tillage_dss.html --------------------


def _html_mu_g(bn: float) -> float:
    """`grossTractionRatio` (tillage_dss.html:648)."""
    return TRACTION_MU_G_SCALE * (1.0 - math.exp(-TRACTION_BN_EXPONENT_COEFF * bn))


def _html_mu(bn: float, s: float) -> float:
    """`netTractionCoefficient` (tillage_dss.html:651)."""
    return _html_mu_g(bn) * (1.0 - math.exp(-TRACTION_SLIP_EXPONENT_COEFF * s)) - 1.0 / bn - 0.5 * s / math.sqrt(bn)


def _html_te_envelope(bn: float, s: float) -> float:
    """`tractiveEfficiencyPct` (tillage_dss.html:654) -- divides by the envelope."""
    return (_html_mu(bn, s) * (1.0 - s) / _html_mu_g(bn)) * 100.0


def _engine_te_at_slip(bn: float, s: float) -> float:
    """The engine's primary: divides by the gross traction ratio developed at slip."""
    mu_g = _html_mu_g(bn)
    gt = mu_g * (1.0 - math.exp(-TRACTION_SLIP_EXPONENT_COEFF * s)) + ROLLING_RESISTANCE_BASE
    return (_html_mu(bn, s) * (1.0 - s) / gt) * 100.0


# --- A. Unit parity -----------------------------------------------------------


def test_engine_mu_g_and_mu_match_the_html_exactly():
    """Everything upstream of the TE denominator is identical to the reference."""
    for bn in BN_GRID:
        assert gross_traction_ratio(bn) == pytest.approx(_html_mu_g(bn), abs=1e-12)
        for s in SLIP_GRID:
            mu = net_traction_coefficient(bn, s, mu_g=gross_traction_ratio(bn))
            assert mu == pytest.approx(_html_mu(bn, s), abs=1e-12), (bn, s)


def test_te_matches_spec_eq_3_2():
    """A. The PRIMARY TE reproduces spec Eq. (3.2) / `tractiveEfficiencyPct` to 1e-6."""
    for bn in BN_GRID:
        mu_g = gross_traction_ratio(bn)
        for s in SLIP_GRID:
            mu = net_traction_coefficient(bn, s, mu_g=mu_g)
            got = traction_efficiency_percent(mu, mu_g, s, bn_rear=bn)
            assert got == pytest.approx(_html_te_envelope(bn, s), abs=1e-6), (bn, s)


def test_envelope_helper_and_primary_are_the_same_quantity():
    """`traction_efficiency_percent` delegates to the envelope helper -- bit-identical."""
    for bn in BN_GRID:
        mu_g = gross_traction_ratio(bn)
        for s in SLIP_GRID:
            mu = net_traction_coefficient(bn, s, mu_g=mu_g)
            assert traction_efficiency_percent(mu, mu_g, s, bn_rear=bn) == (
                traction_efficiency_envelope_percent(mu, mu_g, s)
            ), (bn, s)


def test_at_slip_diagnostic_preserves_the_former_primary_formula():
    """B. The retained diagnostic still equals the old at-slip formula to 1e-6."""
    for bn in BN_GRID:
        mu_g = gross_traction_ratio(bn)
        for s in SLIP_GRID:
            mu = net_traction_coefficient(bn, s, mu_g=mu_g)
            got = traction_efficiency_at_slip_pct(mu, mu_g, s, bn_rear=bn)
            assert got == pytest.approx(_engine_te_at_slip(bn, s), abs=1e-6), (bn, s)


def test_the_two_bases_are_different_enough_to_matter():
    """Guards against the diagnostic silently becoming an alias of the primary."""
    bn, s = 15.0, 0.10
    mu_g = gross_traction_ratio(bn)
    mu = net_traction_coefficient(bn, s, mu_g=mu_g)
    primary = traction_efficiency_percent(mu, mu_g, s, bn_rear=bn)
    at_slip = traction_efficiency_at_slip_pct(mu, mu_g, s, bn_rear=bn)
    assert at_slip > primary
    assert at_slip / primary > 1.5


def test_envelope_helper_returns_zero_rather_than_raising_on_a_zero_envelope():
    """A diagnostic must never fail a run the primary path computed successfully."""
    assert traction_efficiency_envelope_percent(0.4, 0.0, 0.1) == 0.0


# --- B. Power recompute from the reported TE ----------------------------------


def test_power_utilization_is_consistent_with_the_reported_te():
    """Recompute Ptr and Put by hand from the engine's own reported TE.

    Catches any divergence between the TE the solver used and the TE it reported --
    the failure mode that made this whole area worth auditing.
    """
    inputs = _reference_inputs()
    r = calculate_legacy_performance(inputs)

    te_frac = r["traction_efficiency"] / 100.0
    trans_frac = inputs.transmission_efficiency_pct / 100.0
    reserve_frac = inputs.power_reserve_pct / 100.0

    expected_ptr = r["drawbar_power"] / (te_frac * trans_frac)
    expected_put = expected_ptr / (inputs.pto_power_kw * (1.0 - reserve_frac)) * 100.0

    assert r["required_pto_power"] == pytest.approx(expected_ptr, rel=1e-4)
    assert r["power_utilization"] == pytest.approx(expected_put, rel=1e-4)


def test_reported_te_equals_the_spec_formula_at_the_engines_own_slip():
    """Direction-agnostic: assert the value, not which way it moved."""
    r = calculate_legacy_performance(_reference_inputs())
    bn = r["legacy_mobility_number_rear"]
    s = r["slip"] / 100.0
    assert r["traction_efficiency"] == pytest.approx(_html_te_envelope(bn, s), abs=1e-4)
    assert r["traction_efficiency_at_slip_percent"] == pytest.approx(
        _engine_te_at_slip(bn, s), abs=1e-4
    )
    # Retained compatibility alias, now the same quantity as the headline.
    assert r["traction_efficiency_reference_basis"] == pytest.approx(
        r["traction_efficiency"], abs=1e-9
    )


# --- C. Cross-mode consistency ------------------------------------------------


@pytest.mark.parametrize(
    "name,runner",
    [
        ("single", lambda: calculate_legacy_performance(_reference_inputs())),
        ("passive_passive", lambda: calculate_passive_passive_performance(make_pp_inputs())),
        (
            "active_passive",
            lambda: calculate_active_passive_performance(
                make_ap_inputs(rotor=make_rotor(pto_power_draw_kw=0.5))
            ),
        ),
    ],
)
def test_every_mode_reports_both_te_bases_consistently(name, runner):
    """D. All three entry points share `traction_efficiency_percent`, so the spec's
    denominator propagates to every mode. Pin that, and the diagnostic alongside."""
    r = runner()
    bn = r.get("legacy_mobility_number_rear")
    assert bn is not None, f"{name} did not report a rear wheel numeric"
    s = r["slip"] / 100.0

    assert r["traction_efficiency"] == pytest.approx(_html_te_envelope(bn, s), abs=1e-3), name
    assert r["traction_efficiency_at_slip_percent"] == pytest.approx(
        _engine_te_at_slip(bn, s), abs=1e-3
    ), name
    assert r["traction_efficiency_at_slip_percent"] > r["traction_efficiency"], name


# --- Change 3: slip-loop upper-bound parity with the HTML ---------------------


def _html_solve_slip(draft_n: float, rear_axle_load_n: float, bn: float):
    """`solveSlip` (tillage_dss.html:658): `while (slipPct <= MAX_SLIP_PCT + 1e-9)`."""
    slip_pct = SLIP_INITIAL_PCT
    converged = False
    last = prev = None
    while slip_pct <= MAX_SLIP_PCT + 1e-9:
        mu = _html_mu(bn, slip_pct / 100.0)
        pull = mu * rear_axle_load_n
        last = (slip_pct, pull)
        if pull >= draft_n:
            converged = True
            break
        prev = last
        slip_pct += SLIP_INCREMENT_PCT

    actual = last[0]
    if converged and prev is not None:
        actual = prev[0] + (draft_n - prev[1]) * (last[0] - prev[0]) / (last[1] - prev[1])
    return actual, converged


#: `solve_slip` derives Bn from the tyre geometry, so the replica reads it back off
#: the solution rather than inventing one.
_SLIP_TYRE = dict(ci_kpa=1200.0, rear_section_width_m=0.34, rear_overall_diameter_m=1.30)
_SLIP_REAR_LOAD_N = 12000.0


def test_slip_loop_matches_the_html_bound_when_the_cap_is_hit():
    """The engine increments-then-clamps; the HTML tests the bound in the while
    condition. Both must evaluate at exactly 20.0 % before declaring non-convergence."""
    impossible_draft = 1.0e9  # no slip can develop this pull
    engine = solve_slip(
        draft_n=impossible_draft, rear_axle_load_n=_SLIP_REAR_LOAD_N, **_SLIP_TYRE
    )
    html_slip, html_converged = _html_solve_slip(
        impossible_draft, _SLIP_REAR_LOAD_N, engine.bn_rear
    )

    assert engine.converged is False and html_converged is False
    assert engine.slip_pct == pytest.approx(MAX_SLIP_PCT, abs=1e-9)
    assert engine.slip_pct == pytest.approx(html_slip, abs=0.05)


def test_slip_loop_matches_the_html_when_it_converges():
    for draft_n in (1500.0, 2500.0, 3500.0, 4200.0):
        engine = solve_slip(
            draft_n=draft_n, rear_axle_load_n=_SLIP_REAR_LOAD_N, **_SLIP_TYRE
        )
        html_slip, html_converged = _html_solve_slip(
            draft_n, _SLIP_REAR_LOAD_N, engine.bn_rear
        )
        assert engine.converged == html_converged, draft_n
        if html_converged:
            assert engine.slip_pct == pytest.approx(html_slip, abs=0.05), draft_n


# --- D. Guardrail smoke tests -------------------------------------------------


def test_draft_uses_depth_in_cm_with_no_divisor():
    """The historical `/10` bug: draft at 15 cm must be 10x draft at 1.5 cm."""
    common = dict(fi=1.0, asae_param_a=652.0, asae_param_b=0.0, asae_param_c=5.1,
                  speed_kmh=2.5, width_m=0.3)
    assert draft_force_n(depth_cm=15.0, **common) == pytest.approx(
        10.0 * draft_force_n(depth_cm=1.5, **common), rel=1e-12
    )


def test_mobility_number_is_metres_and_kn_not_millimetres():
    """The spreadsheet's mm-based Bn gives ~4.5e7. Metre-scale dims must give 5-80."""
    bn = mobility_number(1500.0, 0.2032, 0.78943, 7702.0)
    assert 5.0 <= bn <= 80.0, bn


def test_active_passive_rejects_a_non_positive_effective_draft():
    """Deff <= 0 guard: rotor thrust exceeding passive draft + rotor drag must raise."""
    with pytest.raises(ValueError):
        calculate_active_passive_performance(
            make_ap_inputs(rotor=make_rotor(pto_power_draw_kw=60.0, rotor_efficiency=0.45))
        )


def test_fuel_per_hectare_has_no_upper_clamp():
    """Neither reference caps it; a cap would disguise a genuinely heavy pairing."""
    from app.core import constants

    assert not hasattr(constants, "FUEL_L_PER_HA_CLAMP")
