"""Seed the reference tractor/implement library.

Data source: `app/utils/seed_data/seed_tractors.json` (18 tractors) and
`seed_implements.json` (27 implements), the canonical HTML-library-format
datasets. Records are mapped onto `Tractor`/`TireSpecification`/`Implement`
kwargs and inserted **exactly as the JSON provides them** -- no merging,
correction, or invented numeric values. The only two departures from a literal
copy are structural, not numeric, and are documented at their exact source:

- `Tractor.model` is `NOT NULL`, but the JSON carries only one `name` string
  with no separate make/model split -- see `_tractor_kwargs_from_json`.
- `transmission_efficiency` / `power_reserve` are not per-tractor fields in
  either the JSON or the reference HTML/Excel tools (they are global operating
  parameters there, defaulted to 86% / 20% in the UI) -- see the two module
  constants below.

**Taxonomy gap.** 3 of the 27 implement records use `"type": "power_harrow"`,
which has no corresponding `ImplementType` enum member (the enum has
MB_PLOUGH / DISC_PLOUGH / CULTIVATOR / DISC_HARROW passive, and ROTAVATOR /
DISC_HARROW_POWERED / CULTIVATOR_POWERED active -- nothing shaped like a power
harrow). Mapping it onto an existing type would be exactly the kind of
correction this module is built to avoid, so these 3 records are skipped, not
guessed at. Adding `ImplementType.POWER_HARROW` (and deciding how
`implement_taxonomy.py` classifies it) would need its own sign-off, since it
touches the engine's type system, not just seed data.

Two entry points:
- `seed_library_if_empty` -- safe for automatic use (only runs against an
  empty library), wired into app startup.
- `reseed_library_replace_all` -- destructive full replace, for deliberate
  manual use only. See its own docstring before calling it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.enums import DriveMode, ImplementType, TireType
from app.models.implement import Implement
from app.models.tire_specification import TireSpecification
from app.models.tractor import Tractor

_SEED_DATA_DIR = Path(__file__).resolve().parent / "seed_data"

# Reference tool's own global operating-parameter defaults -- Excel C27/C28,
# and both HTML tools' own "transEff"/"powerReserve" input defaults (86 / 20).
# Neither the JSON library nor the reference tractor objects carry these
# per-tractor; every tractor gets the same value because the reference itself
# treats them as one shared operating condition, not a tractor attribute.
_DEFAULT_TRANSMISSION_EFFICIENCY_PCT = 86.0
_DEFAULT_POWER_RESERVE_PCT = 20.0

# JSON "type" string -> ImplementType. "power_harrow" is deliberately absent --
# see the module docstring.
_IMPLEMENT_TYPE_BY_JSON_KEY = {
    "moldboard_plough": ImplementType.MB_PLOUGH,
    "disc_plough": ImplementType.DISC_PLOUGH,
    "cultivator": ImplementType.CULTIVATOR,
    "disc_harrow": ImplementType.DISC_HARROW,
    "rotavator": ImplementType.ROTAVATOR,
}


def _load_json(filename: str) -> list[dict]:
    with open(_SEED_DATA_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


def _tractor_kwargs_from_json(record: dict) -> "tuple[dict[str, Any], dict[str, Any]]":
    """Map one `seed_tractors.json` record onto Tractor + TireSpecification kwargs.

    `model` is `NOT NULL` but the JSON carries only a single `name` string with
    no make/model split; `name` is duplicated into `manufacturer` and `model`
    rather than hand-splitting it (as the previous hardcoded seed data did),
    since the JSON draws no line between the two itself and splitting it back
    up would be an inference the source data doesn't license.

    `Wt` (total static weight) has no column of its own -- both `Tractor` and
    the engine derive it fresh from front + rear axle weight every time -- so
    it is present in the JSON but intentionally not stored anywhere.
    """
    tractor_kwargs = dict(
        name=record["name"],
        manufacturer=record["name"],
        model=record["name"],
        pto_power=record["Pt"],
        rated_engine_speed=record["N_engine"],
        max_engine_torque=record["Tmax"],
        wheelbase=record["L_wb"],
        front_axle_weight=record["Wf_kg"],
        rear_axle_weight=record["Wr_kg"],
        hitch_distance_from_rear=record["Hd"],
        cg_distance_from_rear=record["Xcgt"],
        # Redundant fallback the engine reads only when TireSpecification
        # itself lacks a rolling radius (routes/simulations.py's
        # _resolve_rolling_radii) -- derived from this same record's own
        # rr_mm, not a separately sourced figure.
        rear_wheel_rolling_radius=record["rr_mm"] / 1000.0,
        drive_mode=DriveMode.WD2,
        transmission_efficiency=_DEFAULT_TRANSMISSION_EFFICIENCY_PCT,
        power_reserve=_DEFAULT_POWER_RESERVE_PCT,
        is_library=True,
    )
    tire_kwargs = dict(
        tire_type=TireType.BIAS_PLY,
        front_tire_size=record["tireFront"],
        front_overall_diameter=record["df_mm"],
        front_section_width=record["bf_mm"],
        front_static_loaded_radius=record["rslf_mm"],
        front_rolling_radius=record["rf_mm"],
        rear_tire_size=record["tireRear"],
        rear_overall_diameter=record["dr_mm"],
        rear_section_width=record["br_mm"],
        rear_static_loaded_radius=record["rslr_mm"],
        rear_rolling_radius=record["rr_mm"],
    )
    return tractor_kwargs, tire_kwargs


def _implement_kwargs_from_json(record: dict) -> Optional["dict[str, Any]"]:
    """Map one `seed_implements.json` record onto Implement kwargs, or `None`
    when its `type` has no corresponding `ImplementType` -- see the module
    docstring's taxonomy-gap note.

    `category` ("primary"/"secondary"/"active") and `draft_basis`
    ("width"/"tools") are present in the JSON but have no column to hold them:
    `Implement` carries no per-row category, and
    `constants.DRAFT_WIDTH_IS_TOOL_COUNT` is a type-wide frozenset (currently
    empty, matching `tillage_dss (2).html`'s width-in-metres-for-everything
    behaviour), not a per-implement column. Both are read here only to
    confirm they exist in the source record; neither is persisted, and
    neither is silently folded into some other field. `n_units` does have a
    home (`number_of_tools`) and is mapped there, even though the engine does
    not currently consult it for any implement type (see that constant).
    """
    implement_type = _IMPLEMENT_TYPE_BY_JSON_KEY.get(record["type"])
    if implement_type is None:
        return None
    return dict(
        name=record["name"],
        implement_type=implement_type,
        width=record["W"],
        weight=record["Wm_kg"],
        cg_distance_from_hitch=record["Xcgi"],
        vertical_horizontal_ratio=record["PyD"],
        asae_param_a=record["A"],
        asae_param_b=record["B"],
        asae_param_c=record["C"],
        number_of_tools=record["n_units"],
        rotor_pto_power=record["rated_ppto_kw"],
        rotor_speed=record["rated_rotor_rpm"],
        rotor_efficiency=record["suggested_eta_r"],
        rotor_mechanical_resistance=record["suggested_da_n"],
        is_library=True,
    )


def seed_library_if_empty(db: Session) -> None:
    """Seed the reference library for a fresh (empty-library) database.

    Only runs when there are zero `is_library=True` rows of a given kind, so
    it is safe to leave wired into app startup -- it will never touch an
    existing library, populated by this function or otherwise. For a
    deliberate full replace of an already-seeded database, see
    `reseed_library_replace_all` below, which is not called from startup and
    must be invoked explicitly.
    """
    has_library_tractors = db.query(Tractor).filter(Tractor.is_library == True).first()  # noqa: E712
    has_library_implements = db.query(Implement).filter(Implement.is_library == True).first()  # noqa: E712

    if has_library_tractors and has_library_implements:
        return

    if not has_library_tractors:
        for record in _load_json("seed_tractors.json"):
            tractor_kwargs, tire_kwargs = _tractor_kwargs_from_json(record)
            tractor = Tractor(**tractor_kwargs)
            db.add(tractor)
            db.flush()
            db.add(TireSpecification(tractor_id=tractor.id, **tire_kwargs))

    if not has_library_implements:
        for record in _load_json("seed_implements.json"):
            kwargs = _implement_kwargs_from_json(record)
            if kwargs is None:
                continue  # power_harrow -- no ImplementType member; see module docstring
            db.add(Implement(**kwargs))

    db.commit()


def reseed_library_replace_all(db: Session) -> "dict[str, Any]":
    """Delete every existing `is_library=True` tractor/implement and reinsert
    the full `seed_data/*.json` dataset. **Not called automatically anywhere**
    -- unlike `seed_library_if_empty`, this is destructive and must be invoked
    explicitly (e.g. from a one-off script or shell) when a full reset is
    genuinely intended.

    *** Read before calling this against a database with real usage. ***
    `Simulation.tractor_id` / `Simulation.implement_id` both have
    `ondelete="CASCADE"`: deleting a library tractor or implement that any
    `Simulation` references **permanently deletes that Simulation too**.
    `OperationSession.implement_id` has `ondelete="SET NULL"` (a session
    survives, losing its implement link); `OperationSession.tractor_id` has
    `ondelete="RESTRICT"` (the delete is refused outright if any session still
    references that tractor).

    Check first, e.g.:

        SELECT count(*) FROM simulations s
          JOIN tractors t ON s.tractor_id = t.id WHERE t.is_library = true;
        SELECT count(*) FROM simulations s
          JOIN implements i ON s.implement_id = i.id WHERE i.is_library = true;

    Call this only once that count is zero, or the resulting loss is
    genuinely acceptable.

    Returns a report: how many tractors/implements were inserted, and the
    names of any implement records skipped for lacking a matching
    `ImplementType` (see the module docstring).
    """
    db.query(Implement).filter(Implement.is_library == True).delete(synchronize_session=False)  # noqa: E712
    db.query(Tractor).filter(Tractor.is_library == True).delete(synchronize_session=False)  # noqa: E712
    db.flush()

    tractor_records = _load_json("seed_tractors.json")
    for record in tractor_records:
        tractor_kwargs, tire_kwargs = _tractor_kwargs_from_json(record)
        tractor = Tractor(**tractor_kwargs)
        db.add(tractor)
        db.flush()
        db.add(TireSpecification(tractor_id=tractor.id, **tire_kwargs))

    implement_records = _load_json("seed_implements.json")
    inserted_implements = 0
    skipped_implements: list[str] = []
    for record in implement_records:
        kwargs = _implement_kwargs_from_json(record)
        if kwargs is None:
            skipped_implements.append(record["name"])
            continue
        db.add(Implement(**kwargs))
        inserted_implements += 1

    db.commit()
    return {
        "tractors_inserted": len(tractor_records),
        "implements_inserted": inserted_implements,
        "implements_skipped": skipped_implements,
    }
