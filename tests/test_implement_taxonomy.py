"""Tests for the implement taxonomy (app.core.implement_taxonomy).

Encodes the reference implement-flow diagram and guards the two lookup tables
that are keyed by implement type -- a missing key there used to surface as an
uncaught KeyError (HTTP 500).
"""

from __future__ import annotations

import pytest

from app.core.constants import PY_OVER_D_RATIO_BY_IMPLEMENT
from app.core.implement_taxonomy import (
    ACTIVE_IMPLEMENT_TYPES,
    IMPLEMENT_POWER_CLASS,
    IMPLEMENT_TILLAGE_STAGE,
    PASSIVE_IMPLEMENT_TYPES,
    SlotAssignmentError,
    is_active,
    is_passive,
    power_class,
    tillage_stage,
    validate_slot_assignment,
)
from app.core.legacy_algorithms import fi_factor, py_over_d_ratio
from app.models.enums import (
    ImplementPowerClass,
    ImplementType,
    SimulationCombinationType,
    SoilTexture,
    TillageStage,
)


# --- Exhaustiveness guards --------------------------------------------------
# These are the cheap permanent defence against adding an ImplementType member
# without classifying it (or without giving a passive type its Py-D entry).
# Fi is no longer among them: it is keyed on soil texture alone, so there is no
# per-implement entry left to omit. `fi_factor`'s own passive-only guard is
# pinned by test_fi_factor_raises_value_error_for_active_types below.


@pytest.mark.parametrize("implement_type", list(ImplementType))
def test_every_implement_type_has_a_power_class(implement_type):
    assert implement_type in IMPLEMENT_POWER_CLASS
    assert isinstance(power_class(implement_type), ImplementPowerClass)


@pytest.mark.parametrize("implement_type", list(ImplementType))
def test_every_implement_type_has_a_tillage_stage_entry(implement_type):
    assert implement_type in IMPLEMENT_TILLAGE_STAGE


@pytest.mark.parametrize("implement_type", list(ImplementType))
def test_passive_types_have_draft_table_entries_and_active_types_do_not(implement_type):
    """The DSS passive-draft model is defined only for unpowered tools."""
    if is_passive(implement_type):
        assert implement_type.value in PY_OVER_D_RATIO_BY_IMPLEMENT
    else:
        assert implement_type.value not in PY_OVER_D_RATIO_BY_IMPLEMENT


# --- Diagram fidelity -------------------------------------------------------


def test_passive_and_active_sets_match_the_diagram():
    assert set(PASSIVE_IMPLEMENT_TYPES) == {
        ImplementType.MB_PLOUGH,
        ImplementType.DISC_PLOUGH,
        ImplementType.DISC_HARROW,
        ImplementType.CULTIVATOR,
    }
    assert set(ACTIVE_IMPLEMENT_TYPES) == {
        ImplementType.ROTAVATOR,
        ImplementType.DISC_HARROW_POWERED,
        ImplementType.CULTIVATOR_POWERED,
    }


def test_conventional_tillage_stages_match_the_diagram():
    assert tillage_stage(ImplementType.MB_PLOUGH) is TillageStage.PRIMARY
    for secondary in (ImplementType.DISC_PLOUGH, ImplementType.DISC_HARROW, ImplementType.CULTIVATOR):
        assert tillage_stage(secondary) is TillageStage.SECONDARY


def test_active_types_have_no_tillage_stage():
    """The diagram classifies Primary/Secondary only under Conventional tillage."""
    for active in ACTIVE_IMPLEMENT_TYPES:
        assert tillage_stage(active) is None


def test_is_active_and_is_passive_are_complementary():
    for implement_type in ImplementType:
        assert is_active(implement_type) is not is_passive(implement_type)


# --- Draft-table lookups fail loudly, not with a bare KeyError ---------------


@pytest.mark.parametrize("active_type", list(ACTIVE_IMPLEMENT_TYPES))
def test_fi_factor_raises_value_error_for_active_types(active_type):
    with pytest.raises(ValueError, match="Fi soil-texture factor"):
        fi_factor(active_type, SoilTexture.FINE)


@pytest.mark.parametrize("active_type", list(ACTIVE_IMPLEMENT_TYPES))
def test_py_over_d_ratio_raises_value_error_for_active_types(active_type):
    with pytest.raises(ValueError, match="Py/D ratio"):
        py_over_d_ratio(active_type)


def test_passive_lookups_match_the_reference_implementations():
    """Fallback Py/D values, pinned to both reference implementations.

    The spreadsheet's "Vertical to Horizontal force ratio" row and the HTML
    library's `PyD` field agree exactly on these. They supersede an earlier table
    (0.15 / 0.40 / 0.50 / 0.0) taken from the DSS document's Kepner citation,
    which disagreed with both references on every row.
    """
    assert fi_factor(ImplementType.MB_PLOUGH, SoilTexture.FINE) == 1.0
    assert py_over_d_ratio(ImplementType.MB_PLOUGH) == 0.20
    assert py_over_d_ratio(ImplementType.DISC_PLOUGH) == 0.0
    assert py_over_d_ratio(ImplementType.DISC_HARROW) == 0.0
    assert py_over_d_ratio(ImplementType.CULTIVATOR) == 0.20


def test_per_implement_ratio_overrides_the_type_table():
    """Both references carry Py/D per implement; the table is only a fallback."""
    assert py_over_d_ratio(ImplementType.MB_PLOUGH, 0.35) == 0.35
    assert py_over_d_ratio(ImplementType.DISC_PLOUGH, 0.0) == 0.0
    # None means "not recorded on this implement" -> fall back to the table.
    assert py_over_d_ratio(ImplementType.MB_PLOUGH, None) == 0.20
    with pytest.raises(ValueError):
        py_over_d_ratio(ImplementType.MB_PLOUGH, -0.1)


# --- Slot assignment rules --------------------------------------------------


def test_single_mode_accepts_passive_and_rejects_active():
    validate_slot_assignment(
        combination_type=SimulationCombinationType.SINGLE,
        implement_type=ImplementType.MB_PLOUGH,
    )
    with pytest.raises(SlotAssignmentError, match="passive"):
        validate_slot_assignment(
            combination_type=SimulationCombinationType.SINGLE,
            implement_type=ImplementType.ROTAVATOR,
        )


def test_passive_passive_requires_two_distinct_passive_tools():
    validate_slot_assignment(
        combination_type=SimulationCombinationType.PASSIVE_PASSIVE,
        implement_type=ImplementType.MB_PLOUGH,
        implement_2_type=ImplementType.CULTIVATOR,
        implement_id="a",
        implement_2_id="b",
    )
    # An active tool in either passive slot is rejected.
    with pytest.raises(SlotAssignmentError):
        validate_slot_assignment(
            combination_type=SimulationCombinationType.PASSIVE_PASSIVE,
            implement_type=ImplementType.ROTAVATOR,
            implement_2_type=ImplementType.CULTIVATOR,
        )
    with pytest.raises(SlotAssignmentError):
        validate_slot_assignment(
            combination_type=SimulationCombinationType.PASSIVE_PASSIVE,
            implement_type=ImplementType.MB_PLOUGH,
            implement_2_type=ImplementType.CULTIVATOR_POWERED,
        )
    # The same record cannot fill both slots.
    with pytest.raises(SlotAssignmentError, match="two different implements"):
        validate_slot_assignment(
            combination_type=SimulationCombinationType.PASSIVE_PASSIVE,
            implement_type=ImplementType.MB_PLOUGH,
            implement_2_type=ImplementType.MB_PLOUGH,
            implement_id="same",
            implement_2_id="same",
        )


def test_active_passive_requires_passive_tool_and_active_rotor():
    validate_slot_assignment(
        combination_type=SimulationCombinationType.ACTIVE_PASSIVE,
        implement_type=ImplementType.MB_PLOUGH,
        implement_2_type=ImplementType.ROTAVATOR,
    )
    # Rotor slot left empty (specs supplied inline) is still valid.
    validate_slot_assignment(
        combination_type=SimulationCombinationType.ACTIVE_PASSIVE,
        implement_type=ImplementType.MB_PLOUGH,
        implement_2_type=None,
    )
    with pytest.raises(SlotAssignmentError, match="PTO-powered"):
        validate_slot_assignment(
            combination_type=SimulationCombinationType.ACTIVE_PASSIVE,
            implement_type=ImplementType.MB_PLOUGH,
            implement_2_type=ImplementType.DISC_HARROW,
        )
    with pytest.raises(SlotAssignmentError):
        validate_slot_assignment(
            combination_type=SimulationCombinationType.ACTIVE_PASSIVE,
            implement_type=ImplementType.ROTAVATOR,
            implement_2_type=ImplementType.ROTAVATOR,
        )


def test_slot_errors_name_the_offending_type_and_valid_alternatives():
    with pytest.raises(SlotAssignmentError) as exc:
        validate_slot_assignment(
            combination_type=SimulationCombinationType.SINGLE,
            implement_type=ImplementType.ROTAVATOR,
        )
    message = str(exc.value)
    assert "Rotavator" in message
    assert "MB Plough" in message  # lists valid passive alternatives
    assert "Active + Passive" in message  # points at the supported route


# --- Primary vs secondary tillage draft coefficients ---------------------------


def test_library_carries_both_primary_and_secondary_tillage_coefficients():
    """ASABE D497 Table 1 tabulates the two stages separately; so must the library.

    The same cultivator pulls ~1.44x harder doing primary work. Carrying only the
    secondary triple under-predicted every primary pass.

    Coefficients now live in `seed_data/seed_implements.json` (a JSON dataset
    read verbatim by `seed_library.py`, not hardcoded Python literals), so this
    checks the data file directly rather than the module's source text.
    """
    from app.utils.seed_library import _load_json

    records = _load_json("seed_implements.json")
    a_b_pairs = {(r["A"], r["B"]) for r in records}
    # Secondary (cultivator, disc harrow) and primary (both implements' higher
    # rows) coefficient sets must both be present.
    assert (32, 1.9) in a_b_pairs
    assert (46, 2.8) in a_b_pairs
    assert (254, 13.2) in a_b_pairs
    assert (364, 18.8) in a_b_pairs


def test_the_sync_migration_no_longer_collapses_deliberate_coefficients():
    """Regression guard on `l7m8n9o0p1q2`.

    It used to UPDATE every library row of a type unconditionally, so a
    primary-tillage row was silently reset to the secondary triple on the next
    migration run -- the data gap was defended by the migration meant to fix it.
    The `asae_param_a IS NULL` guard makes it the backfill it was meant to be.
    """
    # `alembic/versions` is not a package, so read the file rather than import it.
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "l7m8n9o0p1q2_sync_library_draft_parameters.py"
    )
    src = path.read_text(encoding="utf-8")
    assert "asae_param_a IS NULL" in src, (
        "the sync migration must only backfill rows without coefficients"
    )


def test_primary_tillage_draft_is_meaningfully_higher_than_secondary():
    """Pins the size of the gap the missing rows were hiding."""
    from app.core.dss_shared import draft_force_n

    common = dict(fi=0.7, asae_param_c=0.0, speed_kmh=4.0, depth_cm=15.0, width_m=9.0)
    secondary = draft_force_n(asae_param_a=32.0, asae_param_b=1.9, **common)
    primary = draft_force_n(asae_param_a=46.0, asae_param_b=2.8, **common)
    assert primary / secondary == pytest.approx(1.444, abs=0.005)
