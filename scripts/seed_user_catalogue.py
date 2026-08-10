"""One-off, idempotent seed of a realistic "My Tractor" / "My Implement" catalogue
for a single existing user, so the simulation engine can be exercised end-to-end
(single, passive-passive and active-passive modes) against owner-scoped data
instead of only the shared library.

Run from `backend/`:

    .venv/Scripts/python.exe scripts/seed_user_catalogue.py

Idempotent: matches existing rows by (owner_id, name) and skips them, so it is
safe to re-run after partial failures or schema changes.

Tractor specs are sourced from published manufacturer/dealer spec sheets (PTO
power, wheelbase, kerb weight, tyre sizes); axle-load split, CG position and
rolling radius are not published anywhere public and are estimated using the
same proportions already implicit in `app/utils/seed_library.py`'s library
tractors (front axle ~37% of kerb weight, CG at ~52% of wheelbase from the
rear axle, loaded tyre radius ~0.4745x the nominal rim-diameter-in-mm figure
stored in `overall_diameter`). Implement ASAE A/B/C and rotor specs reuse the
same representative per-type placeholders as the library implements, per the
existing disclaimer in `seed_library.py`.
"""

from __future__ import annotations

from app.database import SessionLocal
from app.models.enums import DiscHarrowConfiguration, DriveMode, ImplementType, TireType
from app.models.implement import Implement
from app.models.tire_specification import TireSpecification
from app.models.tractor import Tractor
from app.models.user import User

TARGET_PHONE = "+914141414141"

TRACTORS = [
    {
        "name": "Mahindra 265 DI",
        "manufacturer": "Mahindra",
        "model": "265 DI",
        "pto_power": 19.0,
        "rated_engine_speed": 2000,
        "max_engine_torque": 107,
        "wheelbase": 1.83,
        "front_axle_weight": 662,
        "rear_axle_weight": 1128,
        "hitch_distance_from_rear": 0.72,
        "cg_distance_from_rear": 0.95,
        "rear_wheel_rolling_radius": 0.34,
        "tire": {
            "tire_type": TireType.BIAS_PLY,
            "front_overall_diameter": 406,
            "front_section_width": 152,
            "front_static_loaded_radius": 193,
            "front_rolling_radius": 197,
            "rear_overall_diameter": 711,
            "rear_section_width": 315,
            "rear_static_loaded_radius": 337,
            "rear_rolling_radius": 344,
        },
    },
    {
        "name": "Swaraj 744 FE",
        "manufacturer": "Swaraj",
        "model": "744 FE",
        "pto_power": 31.2,
        "rated_engine_speed": 2000,
        "max_engine_torque": 175,
        "wheelbase": 2.135,
        "front_axle_weight": 736,
        "rear_axle_weight": 1254,
        "hitch_distance_from_rear": 0.72,
        "cg_distance_from_rear": 1.11,
        "rear_wheel_rolling_radius": 0.34,
        "tire": {
            "tire_type": TireType.BIAS_PLY,
            "front_overall_diameter": 406,
            "front_section_width": 152,
            "front_static_loaded_radius": 193,
            "front_rolling_radius": 197,
            "rear_overall_diameter": 711,
            "rear_section_width": 345,
            "rear_static_loaded_radius": 337,
            "rear_rolling_radius": 344,
        },
    },
    {
        "name": "John Deere 5310",
        "manufacturer": "John Deere",
        "model": "5310",
        "pto_power": 34.8,
        "rated_engine_speed": 2200,
        "max_engine_torque": 178,
        "wheelbase": 2.05,
        "front_axle_weight": 781,
        "rear_axle_weight": 1329,
        "hitch_distance_from_rear": 0.72,
        "cg_distance_from_rear": 1.07,
        "rear_wheel_rolling_radius": 0.34,
        "tire": {
            "tire_type": TireType.BIAS_PLY,
            "front_overall_diameter": 508,
            "front_section_width": 165,
            "front_static_loaded_radius": 241,
            "front_rolling_radius": 246,
            "rear_overall_diameter": 711,
            "rear_section_width": 429,
            "rear_static_loaded_radius": 337,
            "rear_rolling_radius": 344,
        },
    },
    {
        "name": "Massey Ferguson 1035 DI",
        "manufacturer": "Massey Ferguson",
        "model": "1035 DI",
        "pto_power": 22.8,
        "rated_engine_speed": 2500,
        "max_engine_torque": 102,
        "wheelbase": 1.935,
        "front_axle_weight": 655,
        "rear_axle_weight": 1115,
        "hitch_distance_from_rear": 0.72,
        "cg_distance_from_rear": 1.01,
        "rear_wheel_rolling_radius": 0.34,
        "tire": {
            "tire_type": TireType.BIAS_PLY,
            "front_overall_diameter": 406,
            "front_section_width": 152,
            "front_static_loaded_radius": 193,
            "front_rolling_radius": 197,
            "rear_overall_diameter": 711,
            "rear_section_width": 315,
            "rear_static_loaded_radius": 337,
            "rear_rolling_radius": 344,
        },
    },
    {
        "name": "Sonalika DI 750 III",
        "manufacturer": "Sonalika",
        "model": "DI 750 III",
        "pto_power": 32.5,
        "rated_engine_speed": 2000,
        "max_engine_torque": 235,
        "wheelbase": 2.215,
        "front_axle_weight": 796,
        "rear_axle_weight": 1354,
        "hitch_distance_from_rear": 0.72,
        "cg_distance_from_rear": 1.15,
        "rear_wheel_rolling_radius": 0.34,
        "tire": {
            "tire_type": TireType.BIAS_PLY,
            "front_overall_diameter": 406,
            "front_section_width": 191,
            "front_static_loaded_radius": 193,
            "front_rolling_radius": 197,
            "rear_overall_diameter": 711,
            "rear_section_width": 378,
            "rear_static_loaded_radius": 337,
            "rear_rolling_radius": 344,
        },
    },
    {
        "name": "New Holland 3630 TX Plus",
        "manufacturer": "New Holland",
        "model": "3630 TX Plus",
        "pto_power": 31.7,
        "rated_engine_speed": 2100,
        "max_engine_torque": 170,
        "wheelbase": 2.045,
        "front_axle_weight": 770,
        "rear_axle_weight": 1310,
        "hitch_distance_from_rear": 0.72,
        "cg_distance_from_rear": 1.06,
        "rear_wheel_rolling_radius": 0.34,
        "tire": {
            "tire_type": TireType.BIAS_PLY,
            "front_overall_diameter": 406,
            "front_section_width": 191,
            "front_static_loaded_radius": 193,
            "front_rolling_radius": 197,
            "rear_overall_diameter": 711,
            "rear_section_width": 378,
            "rear_static_loaded_radius": 337,
            "rear_rolling_radius": 344,
        },
    },
    {
        "name": "Farmtrac 60 Powermaxx",
        "manufacturer": "Farmtrac",
        "model": "60 Powermaxx",
        "pto_power": 33.6,
        "rated_engine_speed": 2100,
        "max_engine_torque": 187,
        "wheelbase": 2.13,
        "front_axle_weight": 875,
        "rear_axle_weight": 1490,
        "hitch_distance_from_rear": 0.72,
        "cg_distance_from_rear": 1.11,
        "rear_wheel_rolling_radius": 0.34,
        "tire": {
            "tire_type": TireType.BIAS_PLY,
            "front_overall_diameter": 406,
            "front_section_width": 191,
            "front_static_loaded_radius": 193,
            "front_rolling_radius": 197,
            "rear_overall_diameter": 711,
            "rear_section_width": 429,
            "rear_static_loaded_radius": 337,
            "rear_rolling_radius": 344,
        },
    },
]

# Passive (towed) implements — cover MB Plough, Disc Plough, Disc Harrow
# (both Tandem and Offset) and Cultivator so single-implement and
# passive-passive runs have real variety to combine.
PASSIVE_IMPLEMENTS = [
    {
        "name": "Fieldking 2-Furrow MB Plough",
        "manufacturer": "Fieldking",
        "implement_type": ImplementType.MB_PLOUGH,
        "width": 0.65,
        "weight": 300,
        "cg_distance_from_hitch": 0.42,
        "vertical_horizontal_ratio": 0.65,
        "asae_param_a": 500,
        "asae_param_b": 50,
        "asae_param_c": 8,
    },
    {
        "name": "Fieldking 3-Furrow MB Plough",
        "manufacturer": "Fieldking",
        "implement_type": ImplementType.MB_PLOUGH,
        "width": 0.95,
        "weight": 400,
        "cg_distance_from_hitch": 0.46,
        "vertical_horizontal_ratio": 0.65,
        "asae_param_a": 500,
        "asae_param_b": 50,
        "asae_param_c": 8,
    },
    {
        "name": "Lemken Opal 3-Disc Plough",
        "manufacturer": "Lemken",
        "implement_type": ImplementType.DISC_PLOUGH,
        "width": 1.05,
        "weight": 400,
        "cg_distance_from_hitch": 0.44,
        "vertical_horizontal_ratio": 0.60,
        "asae_param_a": 400,
        "asae_param_b": 45,
        "asae_param_c": 7,
    },
    {
        "name": "Maschio Gaspardo Tandem Disc Harrow (20 Discs)",
        "manufacturer": "Maschio Gaspardo",
        "implement_type": ImplementType.DISC_HARROW,
        "configuration": DiscHarrowConfiguration.TANDEM,
        "width": 1.7,
        "weight": 300,
        "cg_distance_from_hitch": 0.40,
        "vertical_horizontal_ratio": 0.50,
        "asae_param_a": 150,
        "asae_param_b": 25,
        "asae_param_c": 4,
    },
    {
        "name": "Kartar Offset Disc Harrow (24 Discs)",
        "manufacturer": "Kartar",
        "implement_type": ImplementType.DISC_HARROW,
        "configuration": DiscHarrowConfiguration.OFFSET,
        "width": 2.0,
        "weight": 360,
        "cg_distance_from_hitch": 0.44,
        "vertical_horizontal_ratio": 0.50,
        "asae_param_a": 150,
        "asae_param_b": 25,
        "asae_param_c": 4,
    },
    {
        "name": "Shaktiman 9-Tyne Spring Cultivator",
        "manufacturer": "Shaktiman",
        "implement_type": ImplementType.CULTIVATOR,
        "width": 1.4,
        "weight": 210,
        "cg_distance_from_hitch": 0.36,
        "vertical_horizontal_ratio": 0.55,
        "asae_param_a": 200,
        "asae_param_b": 30,
        "asae_param_c": 5,
    },
    {
        "name": "Shaktiman 13-Tyne Spring Cultivator",
        "manufacturer": "Shaktiman",
        "implement_type": ImplementType.CULTIVATOR,
        "width": 2.0,
        "weight": 300,
        "cg_distance_from_hitch": 0.42,
        "vertical_horizontal_ratio": 0.55,
        "asae_param_a": 200,
        "asae_param_b": 30,
        "asae_param_c": 5,
    },
]

# Active (PTO-driven) implements — occupy the rotor slot of an active-passive
# combination. No ASAE draft params, per the same convention as
# `seed_library.py`'s ACTIVE_LIBRARY_IMPLEMENTS.
ACTIVE_IMPLEMENTS = [
    {
        "name": "Shaktiman Rotavator (5 ft)",
        "manufacturer": "Shaktiman",
        "implement_type": ImplementType.ROTAVATOR,
        "width": 1.55,
        "weight": 350,
        "cg_distance_from_hitch": 0.45,
        "rotor_mechanical_resistance": 310,
        "rotor_efficiency": 0.30,
        "rotor_pto_power": 3.2,
        "rotor_speed": 540,
    },
    {
        "name": "Fieldking Powered Disc Harrow (18 Discs)",
        "manufacturer": "Fieldking",
        "implement_type": ImplementType.DISC_HARROW_POWERED,
        "width": 1.6,
        "weight": 380,
        "cg_distance_from_hitch": 0.44,
        "rotor_mechanical_resistance": 340,
        "rotor_efficiency": 0.29,
        "rotor_pto_power": 3.4,
        "rotor_speed": 540,
    },
    {
        "name": "Maschio Powered Cultivator (9 Tines)",
        "manufacturer": "Maschio Gaspardo",
        "implement_type": ImplementType.CULTIVATOR_POWERED,
        "width": 1.4,
        "weight": 300,
        "cg_distance_from_hitch": 0.40,
        "rotor_mechanical_resistance": 270,
        "rotor_efficiency": 0.28,
        "rotor_pto_power": 2.9,
        "rotor_speed": 540,
    },
]


def seed_user_catalogue(db, *, owner_id) -> None:
    existing_tractor_names = {
        name
        for (name,) in db.query(Tractor.name).filter(Tractor.owner_id == owner_id).all()
    }
    existing_implement_names = {
        name
        for (name,) in db.query(Implement.name).filter(Implement.owner_id == owner_id).all()
    }

    added_tractors = 0
    for spec in TRACTORS:
        if spec["name"] in existing_tractor_names:
            continue
        tire_data = dict(spec["tire"])
        tractor = Tractor(
            name=spec["name"],
            manufacturer=spec["manufacturer"],
            model=spec["model"],
            pto_power=spec["pto_power"],
            rated_engine_speed=spec["rated_engine_speed"],
            max_engine_torque=spec["max_engine_torque"],
            wheelbase=spec["wheelbase"],
            front_axle_weight=spec["front_axle_weight"],
            rear_axle_weight=spec["rear_axle_weight"],
            hitch_distance_from_rear=spec["hitch_distance_from_rear"],
            cg_distance_from_rear=spec["cg_distance_from_rear"],
            rear_wheel_rolling_radius=spec["rear_wheel_rolling_radius"],
            drive_mode=DriveMode.WD2,
            transmission_efficiency=86.0,
            power_reserve=20.0,
            owner_id=owner_id,
            is_library=False,
        )
        db.add(tractor)
        db.flush()
        db.add(TireSpecification(tractor_id=tractor.id, **tire_data))
        added_tractors += 1

    added_implements = 0
    for spec in [*PASSIVE_IMPLEMENTS, *ACTIVE_IMPLEMENTS]:
        if spec["name"] in existing_implement_names:
            continue
        db.add(Implement(**spec, owner_id=owner_id, is_library=False))
        added_implements += 1

    db.commit()
    print(f"Added {added_tractors} tractors and {added_implements} implements.")
    print(
        f"Totals for owner {owner_id}: "
        f"{len(existing_tractor_names) + added_tractors} tractors, "
        f"{len(existing_implement_names) + added_implements} implements."
    )


def main() -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.phone_number == TARGET_PHONE).first()
        if not user:
            raise SystemExit(f"No user found with phone_number={TARGET_PHONE!r}")
        seed_user_catalogue(db, owner_id=user.id)
    finally:
        db.close()


if __name__ == "__main__":
    main()
