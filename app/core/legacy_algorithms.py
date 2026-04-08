from __future__ import annotations

import math
from dataclasses import dataclass

from app.core.constants import GRAVITY
from app.models.enums import ImplementType, SoilTexture


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


def _fi_factor(implement_type: ImplementType, soil_texture: SoilTexture) -> float:
    fi_table = {
        ImplementType.MB_PLOUGH: {
            SoilTexture.FINE: 1.0,
            SoilTexture.COARSE: 0.45,
            SoilTexture.MEDIUM: 0.70,
        },
        ImplementType.DISC_PLOUGH: {
            SoilTexture.FINE: 1.0,
            SoilTexture.COARSE: 0.78,
            SoilTexture.MEDIUM: 0.88,
        },
        ImplementType.DISC_HARROW: {
            SoilTexture.FINE: 1.0,
            SoilTexture.COARSE: 0.78,
            SoilTexture.MEDIUM: 0.88,
        },
        ImplementType.CULTIVATOR: {
            SoilTexture.FINE: 1.0,
            SoilTexture.COARSE: 0.65,
            SoilTexture.MEDIUM: 0.85,
        },
    }
    return fi_table[implement_type][soil_texture]


def calculate_legacy_performance(inputs: LegacyInputs) -> dict:
    if inputs.width_m <= 0:
        raise ValueError("Implement width must be > 0")
    if inputs.speed_kmh <= 0:
        raise ValueError("Speed must be > 0")
    if inputs.field_area_ha <= 0:
        raise ValueError("Field area must be > 0")
    if inputs.pto_power_kw <= 0:
        raise ValueError("PTO power must be > 0")

    fi = _fi_factor(inputs.implement_type, inputs.soil_texture)
    draft_n = fi * (
        inputs.asae_param_a
        + inputs.asae_param_b * inputs.speed_kmh
        + inputs.asae_param_c * (inputs.speed_kmh**2)
    ) * inputs.width_m * inputs.depth_cm

    # Legacy variables
    tw_kg = inputs.front_axle_weight_kg + inputs.rear_axle_weight_kg
    slip = 2.0
    rr_r1 = 0.04
    rr_f1 = 0.04
    er = 0.2 * inputs.rear_rolling_radius_m
    py = inputs.vertical_horizontal_ratio * draft_n

    pull_st = -1e18
    gt = 0.0
    nt2 = 0.0
    rd_n = 0.0
    fd_n = 0.0
    bnr = 0.0
    bnf = 0.0
    max_outer = 10000

    for _ in range(max_outer):
        while True:
            ef = rr_f1 * inputs.front_rolling_radius_m
            rr_r2 = rr_r1

            temp1 = (tw_kg * GRAVITY) * (inputs.cg_distance_from_rear_m - er)
            temp2 = draft_n * inputs.depth_cm * 0.01 * 2.0 / 3.0
            temp3 = (py + (inputs.weight_kg * GRAVITY)) * (
                inputs.cg_distance_from_hitch_m + inputs.hitch_distance_from_rear_m + er
            )
            temp4 = inputs.wheelbase_m + ef - er
            if temp4 == 0:
                raise ValueError("Invalid geometry: wheelbase + ef - er equals zero")

            fd_n = (temp1 + temp2 - temp3) / temp4
            rd_n = ((tw_kg + inputs.weight_kg) * GRAVITY) + py - fd_n

            if rd_n <= 0 or fd_n <= 0:
                raise ValueError("Invalid load distribution: dynamic axle load became non-positive")

            bnr = ((2000.0 * inputs.cone_index_kpa * inputs.rear_section_width_m * inputs.rear_overall_diameter_m) / rd_n) * (
                2.0 / (1.0 + 3.0 * inputs.rear_section_width_m / inputs.rear_overall_diameter_m)
            )
            bnf = ((2000.0 * inputs.cone_index_kpa * inputs.front_section_width_m * inputs.front_overall_diameter_m) / fd_n) * (
                2.0 / (1.0 + 3.0 * inputs.front_section_width_m / inputs.front_overall_diameter_m)
            )
            if bnr <= 0 or bnf <= 0:
                raise ValueError("Invalid mobility number, check tire or cone-index inputs")

            rr_r1 = (1.0 / bnr) + 0.04 + ((0.005 * slip) / (bnr**0.5))
            rr_f1 = (1.0 / bnf) + 0.04

            if (rr_r1 - rr_r2) <= 0.0001:
                break

        gt = 0.88 * (1.0 - math.exp(-0.1 * bnr)) * (1.0 - math.exp(-7.5 * 0.01 * slip)) + 0.04
        # Old VB line uses RRr/RRf; intended behavior is rear+front rolling resistance.
        nt2 = gt - (rr_r1 + rr_f1)
        pull_st = nt2 * rd_n
        slip += 0.1
        if pull_st >= draft_n:
            break

    if pull_st < draft_n:
        raise ValueError("Slip loop did not converge to required draft pull")

    mr_ratio = rr_r1 + rr_f1
    te_pct = (100.0 - slip) * (gt - mr_ratio) / gt
    if te_pct == 0:
        raise ValueError("Tractive efficiency became zero")

    kwf = fd_n / (tw_kg * GRAVITY)
    kwr = rd_n / (tw_kg * GRAVITY)
    pdb_kw = draft_n * inputs.speed_kmh / 3.6 / 1000.0

    fc_th = inputs.speed_kmh * inputs.width_m / 10.0
    if fc_th <= 0:
        raise ValueError("Theoretical field capacity is non-positive")

    turning_time_s = 15.56 + 2.61 * (inputs.width_m / inputs.speed_kmh) - 1.41 * inputs.speed_kmh
    number_turns = max(0, int(round(inputs.field_width_m / inputs.width_m)))
    total_turning_time_h = (turning_time_s * 2.0 * number_turns) / 3600.0
    theoretical_time_h = inputs.field_area_ha / fc_th
    total_time_h = total_turning_time_h + theoretical_time_h
    if total_time_h <= 0:
        raise ValueError("Total operating time is non-positive")

    fc_ac = inputs.field_area_ha / total_time_h
    field_eff_pct = (fc_ac / fc_th) * 100.0

    pow_av = (
        inputs.pto_power_kw
        * (inputs.transmission_efficiency_pct / 100.0)
        * (te_pct / 100.0)
        * ((100.0 - inputs.power_reserve_pct) / 100.0)
    )
    if pow_av == 0:
        raise ValueError("Available power is zero")
    pused_pct = (pdb_kw / pow_av) * 100.0

    ballast_front_kg = 0.0
    if kwf < 0.2:
        rfwd = 0.2 * (tw_kg * GRAVITY)
        ballast_front_kg = (rfwd - fd_n) / GRAVITY

    recommendation = ""
    if slip <= 7.9:
        recommendation = "Increase depth or speed of operation, because slip is less than 8%"
    elif 8.0 <= slip <= 15.0:
        recommendation = "You are working in optimum slip range, 8 to 15 %"
    else:
        recommendation = "Ballast is required to reduce the slip up to 15% so max tractor power can be used"

    ballast_rear_kg = 0.0
    slip3 = None
    nt3 = None
    te3_after = None
    gt3 = None
    crwd = None
    if slip > 15.0:
        rd3 = rd_n
        while True:
            slip3 = 15.0
            bnr3 = ((2000.0 * inputs.cone_index_kpa * inputs.rear_section_width_m * inputs.rear_overall_diameter_m) / rd3) * (
                2.0 / (1.0 + 3.0 * inputs.rear_section_width_m / inputs.rear_overall_diameter_m)
            )
            rr_r3 = (1.0 / bnr3) + 0.04 + ((0.005 * slip3) / (bnr3**0.5))
            gt3 = 0.88 * (1.0 - math.exp(-0.1 * bnr3)) * (1.0 - math.exp(-7.5 * 0.01 * slip3)) + 0.04
            te3 = (1.0 - rr_r3 / gt3) * ((100.0 - slip3) / 100.0) * 100.0
            if te3 < 0:
                raise ValueError("Either decrease depth or speed of operation, since slip is very low")

            crwd = draft_n * (100.0 - slip3) / 100.0 / (gt3 * te3 / 100.0)
            if (crwd - rd3) > 5.0:
                rd3 = rd3 + 5.0
            else:
                break

        ballast_rear_kg = abs((crwd - rd_n) / GRAVITY)
        nt3 = draft_n / crwd
        te3_after = nt3 / gt3 * (100.0 - slip)

    x = (pdb_kw / ((inputs.transmission_efficiency_pct / 100.0) * (te_pct / 100.0))) / inputs.pto_power_kw
    inside = 738.0 * x + 173.0
    if inside < 0:
        raise ValueError("Fuel model invalid input (negative square root)")
    sfc = (2.64 * x + 3.91) - (0.203 * math.sqrt(inside))
    fuel_cons_l_per_ha = sfc * pdb_kw / fc_ac
    overall_pct = pdb_kw * 3600.0 / 1000.0 / (fc_th * fuel_cons_l_per_ha * 35.5) * 100.0

    if 90.0 < pused_pct < 95.0:
        load_status = "Properly Loaded"
    elif pused_pct < 90.0:
        load_status = "Under Loaded"
    else:
        load_status = "Over Loaded"

    status_message = "OK"
    if te_pct < 0:
        status_message = "Either decrease depth or speed of operation"

    return {
        "draft_force": draft_n,
        "drawbar_power": pdb_kw,
        "slip": slip,
        "coefficient_net_traction": nt2,
        "motion_resistance_ratio": mr_ratio,
        "motion_resistance": mr_ratio,
        "traction_efficiency": te_pct,
        "front_weight_utilization": kwf,
        "rear_weight_utilization": kwr,
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
        "legacy_fi": fi,
        "legacy_turning_time_seconds": turning_time_s,
        "legacy_number_of_turns": number_turns,
        "legacy_after_ballast_slip_target": slip3,
        "legacy_after_ballast_cot": nt3,
        "legacy_after_ballast_te": te3_after,
        "legacy_after_ballast_gt": gt3,
        "legacy_crwd_n": crwd,
        "calculation_mode": "legacy_vb",
    }
