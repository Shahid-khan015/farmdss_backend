"""Adapter from the public `PerformanceInputs` shape onto the Section 3 engine.

`PerformanceInputs` is field-for-field identical to `LegacyInputs`; the two are
kept as separate types only so callers outside `app.core` do not depend on the
engine's own dataclass. The conversion is therefore a straight `asdict` splat
rather than a hand-maintained field list that can silently fall out of sync.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from app.core.legacy_algorithms import LegacyInputs, calculate_legacy_performance, estimate_draft_force
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
    #: Py/D; None falls back to the per-implement-type table.
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

    #: Eq. 3.1's `W` for per-tool implement classes; see LegacyInputs.
    number_of_tools: Optional[int] = None


def _to_legacy_inputs(inputs: PerformanceInputs) -> LegacyInputs:
    return LegacyInputs(**asdict(inputs))


def calculate_performance(inputs: PerformanceInputs) -> dict:
    """Run the DSS Section 3 single-implement engine."""
    return calculate_legacy_performance(_to_legacy_inputs(inputs))


def estimate_required_draft_power(inputs: PerformanceInputs) -> tuple[float, float]:
    """Draft force (N) and the drawbar power it implies (kW), without the full run."""
    draft_force = estimate_draft_force(_to_legacy_inputs(inputs))
    speed_mps = inputs.speed_kmh / 3.6
    return draft_force, (draft_force * speed_mps) / 1000.0
