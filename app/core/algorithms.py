from __future__ import annotations

import math
from dataclasses import dataclass

from app.core.constants import (
    BALLAST_INCREMENT_N,
    BALLAST_TOLERANCE_N,
    DIESEL_CALORIFIC_VALUE,
    GRAVITY,
    RR_BIAS_BASE,
    RR_RADIAL_BASE,
    SLIP_INCREMENT,
    SLIP_MAX_ITERATIONS,
    SLIP_TOLERANCE_N,
)


@dataclass(frozen=True)
class SlipSolution:
    slip: float  # %
    bnr: float
    rrr: float
    gt: float
    nt2: float
    pull: float  # N
    converged: bool
    iterations: int


def draft_force_asae(a: float, b: float, c: float, v_kmh: float, width_m: float, depth_cm: float) -> float:
    # Draft = (A + (B * V) + (C * V²)) * W * d
    return (a + (b * v_kmh) + (c * (v_kmh**2))) * width_m * depth_cm


def drawbar_power_kw(draft_n: float, v_kmh: float) -> float:
    # Pdb = (Draft * V) / 3600
    return (draft_n * v_kmh) / 3600.0


def total_weight_kg(wf_kg: float, wr_kg: float, wi_kg: float) -> float:
    # TW = Wf + Wr + Wi
    return wf_kg + wr_kg + wi_kg


def dynamic_rear_weight_n_2wd(
    wr_kg: float,
    wi_kg: float,
    a_m: float,
    wb_m: float,
    draft_n: float,
    v_over_h: float,
    h_m: float,
) -> float:
    # Rd = (Wr * 9.81) + (Wi * 9.81 * a / WB) + ((Draft * V/H) * h / WB)
    return (wr_kg * GRAVITY) + (wi_kg * GRAVITY * a_m / wb_m) + ((draft_n * v_over_h) * h_m / wb_m)


def dynamic_front_weight_n_4wd(
    wf_kg: float,
    wi_kg: float,
    a_m: float,
    wb_m: float,
    draft_n: float,
    v_over_h: float,
    h_m: float,
) -> float:
    # Fd = (Wf * 9.81) - (Wi * 9.81 * (WB - a) / WB) - ((Draft * V/H) * (WB - h) / WB)
    return (wf_kg * GRAVITY) - (wi_kg * GRAVITY * (wb_m - a_m) / wb_m) - ((draft_n * v_over_h) * (wb_m - h_m) / wb_m)


def mobility_number(ci_kpa: float, sw_m: float, od_m: float, dynamic_weight_n: float) -> float:
    # Bn = ((2000 * CI * SW * OD) / R) * (2 / (1 + 3 * SW / OD))
    return ((2000.0 * ci_kpa * sw_m * od_m) / dynamic_weight_n) * (2.0 / (1.0 + 3.0 * sw_m / od_m))


def rolling_resistance_coefficient(bn: float, slip_pct: float, tire_is_bias: bool) -> float:
    # RRr = (1 / Bn) + base + ((0.005 * slip) / (Bn^0.5))
    base = RR_BIAS_BASE if tire_is_bias else RR_RADIAL_BASE
    return (1.0 / bn) + base + ((0.005 * slip_pct) / (bn**0.5))


def gross_traction_coefficient(bn: float, slip_pct: float) -> float:
    # GT = 0.88 * (1 - exp(-0.1 * Bn)) * (1 - exp(-7.5 * 0.01 * slip)) + 0.04
    return 0.88 * (1.0 - math.exp(-0.1 * bn)) * (1.0 - math.exp(-7.5 * 0.01 * slip_pct)) + 0.04


def net_traction_coefficient(gt: float, rr: float) -> float:
    # NT2 = GT - RRr
    return gt - rr


def pull_force_n(dynamic_weight_n: float, nt2: float) -> float:
    # pull = (Rd / 9.81) * NT2 * 9.81
    return (dynamic_weight_n / GRAVITY) * nt2 * GRAVITY


def solve_slip_iterative(
    *,
    draft_n: float,
    rd_n: float,
    ci_kpa: float,
    sw_m: float,
    od_m: float,
    tire_is_bias: bool,
    initial_slip_pct: float = 10.0,
) -> SlipSolution:
    slip = float(initial_slip_pct)
    for i in range(SLIP_MAX_ITERATIONS):
        bnr = mobility_number(ci_kpa, sw_m, od_m, rd_n)
        rr = rolling_resistance_coefficient(bnr, slip, tire_is_bias)
        gt = gross_traction_coefficient(bnr, slip)
        nt2 = net_traction_coefficient(gt, rr)
        pull = pull_force_n(rd_n, nt2)

        if abs(pull - draft_n) < SLIP_TOLERANCE_N:
            return SlipSolution(
                slip=slip,
                bnr=bnr,
                rrr=rr,
                gt=gt,
                nt2=nt2,
                pull=pull,
                converged=True,
                iterations=i + 1,
            )

        if pull > draft_n:
            slip = slip - SLIP_INCREMENT
        else:
            slip = slip + SLIP_INCREMENT

        # Physical constraint from doc: slip between 0 and 100
        if slip < 0.0:
            slip = 0.0
        if slip > 100.0:
            slip = 100.0

    return SlipSolution(
        slip=slip,
        bnr=bnr,
        rrr=rr,
        gt=gt,
        nt2=nt2,
        pull=pull,
        converged=False,
        iterations=SLIP_MAX_ITERATIONS,
    )


def traction_efficiency_percent(rr: float, gt: float, slip_pct: float) -> float:
    # TE = (1 - RRr / GT) * ((100 - slip) / 100) * 100
    return (1.0 - rr / gt) * ((100.0 - slip_pct) / 100.0) * 100.0


def power_utilization_percent(pdb_kw: float, trans_eff_pct: float, te_pct: float, ppto_kw: float) -> float:
    # Pused = (Pdb / ((trans_eff/100) * (TE/100))) / Ppto * 100
    return (pdb_kw / ((trans_eff_pct / 100.0) * (te_pct / 100.0))) / ppto_kw * 100.0


def field_capacity_theoretical_ha_per_h(width_m: float, v_kmh: float) -> float:
    # FC_th = W * V / 10
    return width_m * v_kmh / 10.0


def turn_time_seconds(width_m: float, v_kmh: float) -> float:
    # turn_time = (W / 1.2 / V) * 3600
    return (width_m / 1.2 / v_kmh) * 3600.0


def total_operating_time_hours(area_ha: float, fc_th_ha_per_h: float, turns: int, turn_time_s: float) -> float:
    # total_time = (area / FC_th) + ((Turns * turn_time) / 3600)
    return (area_ha / fc_th_ha_per_h) + ((turns * turn_time_s) / 3600.0)


def field_capacity_actual_ha_per_h(area_ha: float, total_time_h: float) -> float:
    # FC_ac = area / total_time
    return area_ha / total_time_h


def field_efficiency_percent(fc_ac: float, fc_th: float) -> float:
    # Field_eff = (FC_ac / FC_th) * 100
    return (fc_ac / fc_th) * 100.0


def front_ballast_required_kg_4wd(total_weight_kg_: float, fd_n: float) -> float:
    # RFWD = 0.2 * TW * 9.81 ; If Fd < RFWD: BALLAST_F = (RFWD - Fd) / 9.81
    rfwd = 0.2 * total_weight_kg_ * GRAVITY
    if fd_n < rfwd:
        return (rfwd - fd_n) / GRAVITY
    return 0.0


def rear_ballast_required_kg(
    *,
    draft_n: float,
    rd_n: float,
    ci_kpa: float,
    sw_m: float,
    od_m: float,
    tire_is_bias: bool,
) -> float:
    """
    Rear ballast loop (exact pseudocode from md):
    - target slip3 = 15%
    - iterate Rd3, increase by 5N until (CRWD - Rd3) <= 5
    - BALLAST_R = (CRWD - Rd) / 9.81
    """
    slip3 = 15.0
    rd3 = float(rd_n)

    while True:
        bnr3 = mobility_number(ci_kpa, sw_m, od_m, rd3)
        rr3 = rolling_resistance_coefficient(bnr3, slip3, tire_is_bias)
        gt3 = gross_traction_coefficient(bnr3, slip3)
        te3 = traction_efficiency_percent(rr3, gt3, slip3)
        crwd = draft_n * (100.0 - slip3) / 100.0 / (gt3 * te3 / 100.0)

        if (crwd - rd3) > BALLAST_TOLERANCE_N:
            rd3 = rd3 + BALLAST_INCREMENT_N
        else:
            break

    return (crwd - rd_n) / GRAVITY


def specific_fuel_consumption_l_per_kw_h(pdb_kw: float, trans_eff_pct: float, te_pct: float, ppto_kw: float) -> float:
    # X = (Pdb / ((trans_eff/100) * (TE/100))) / Ppto
    x = (pdb_kw / ((trans_eff_pct / 100.0) * (te_pct / 100.0))) / ppto_kw
    # SFC = ((2.64 * X + 3.91) - (0.203 * sqrt(738 * X + 173)))
    return (2.64 * x + 3.91) - (0.203 * math.sqrt(738.0 * x + 173.0))


def fuel_consumption_l_per_ha(sfc_l_per_kw_h: float, pdb_kw: float, fc_ac_ha_per_h: float) -> float:
    # Fuel_cons = SFC * Pdb / FC_ac
    return sfc_l_per_kw_h * pdb_kw / fc_ac_ha_per_h


def overall_performance_efficiency_percent(pdb_kw: float, fc_th_ha_per_h: float, fuel_l_per_ha: float) -> float:
    # over_perf = (Pdb * 3600 / 1000) / (FC_th * Fuel_cons * 35.5) * 100
    return (pdb_kw * 3600.0 / 1000.0) / (fc_th_ha_per_h * fuel_l_per_ha * DIESEL_CALORIFIC_VALUE) * 100.0

