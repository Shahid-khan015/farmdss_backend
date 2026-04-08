from __future__ import annotations

from dataclasses import dataclass

from app.core.legacy_algorithms import LegacyInputs, calculate_legacy_performance
from app.models.enums import ImplementType, SoilTexture


@dataclass(frozen=True)
class PerformanceInputs:
    # Tractor
    pto_power_kw: float
    wheelbase_m: float
    front_axle_weight_kg: float
    rear_axle_weight_kg: float
    hitch_distance_from_rear_m: float
    cg_distance_from_rear_m: float
    transmission_efficiency_pct: float
    power_reserve_pct: float

    # Tire
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


def calculate_performance(inputs: PerformanceInputs) -> dict:
    """
    Faithful implementation of the legacy DSS formulas from Front _screen.frm.
    """
    legacy_inputs = LegacyInputs(
        pto_power_kw=inputs.pto_power_kw,
        wheelbase_m=inputs.wheelbase_m,
        front_axle_weight_kg=inputs.front_axle_weight_kg,
        rear_axle_weight_kg=inputs.rear_axle_weight_kg,
        hitch_distance_from_rear_m=inputs.hitch_distance_from_rear_m,
        cg_distance_from_rear_m=inputs.cg_distance_from_rear_m,
        transmission_efficiency_pct=inputs.transmission_efficiency_pct,
        power_reserve_pct=inputs.power_reserve_pct,
        front_rolling_radius_m=inputs.front_rolling_radius_m,
        rear_rolling_radius_m=inputs.rear_rolling_radius_m,
        front_overall_diameter_m=inputs.front_overall_diameter_m,
        rear_overall_diameter_m=inputs.rear_overall_diameter_m,
        front_section_width_m=inputs.front_section_width_m,
        rear_section_width_m=inputs.rear_section_width_m,
        implement_type=inputs.implement_type,
        width_m=inputs.width_m,
        weight_kg=inputs.weight_kg,
        cg_distance_from_hitch_m=inputs.cg_distance_from_hitch_m,
        vertical_horizontal_ratio=inputs.vertical_horizontal_ratio,
        asae_param_a=inputs.asae_param_a,
        asae_param_b=inputs.asae_param_b,
        asae_param_c=inputs.asae_param_c,
        soil_texture=inputs.soil_texture,
        cone_index_kpa=inputs.cone_index_kpa,
        depth_cm=inputs.depth_cm,
        speed_kmh=inputs.speed_kmh,
        field_area_ha=inputs.field_area_ha,
        field_width_m=inputs.field_width_m,
    )
    return calculate_legacy_performance(legacy_inputs)

