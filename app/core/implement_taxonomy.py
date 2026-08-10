"""Implement taxonomy: power class, tillage stage, and simulation slot rules.

Encodes the reference implement-flow diagram:

    Conventional tillage
      Primary   -> MB Plough
      Secondary -> Disc Plough, Disc Harrow (Tandem/Offset), Cultivator
    Combi tillage
      Passive + Passive -> two passive tools
      Active + Passive  -> a passive tool + a PTO-powered tool
                           (Rotavator / Disc Harrow (Powered) / Cultivator (Powered))

This module is pure (no FastAPI/DB imports), matching the rest of `app.core`,
so the classification and the slot rules can be unit-tested in isolation and
reused by the API layer, seeding, and any future report/export code.

Why this matters beyond labelling: the DSS draft equation (Eq. 3.1) and the
Fi / Py-D tables are defined **only** for passive tools. Letting an active
implement reach them would either raise or silently model a powered tool as
unpowered. `validate_slot_assignment` is the single enforcement point.
"""

from __future__ import annotations

from typing import Optional

from app.models.enums import (
    ImplementPowerClass,
    ImplementType,
    SimulationCombinationType,
    TillageStage,
)

__all__ = [
    "IMPLEMENT_POWER_CLASS",
    "IMPLEMENT_TILLAGE_STAGE",
    "PASSIVE_IMPLEMENT_TYPES",
    "ACTIVE_IMPLEMENT_TYPES",
    "power_class",
    "tillage_stage",
    "is_active",
    "is_passive",
    "SlotAssignmentError",
    "validate_slot_assignment",
]


# Exhaustive over every ImplementType member -- see test_implement_taxonomy.py,
# which fails if a new member is added without being classified here.
IMPLEMENT_POWER_CLASS = {
    ImplementType.MB_PLOUGH: ImplementPowerClass.PASSIVE,
    ImplementType.DISC_PLOUGH: ImplementPowerClass.PASSIVE,
    ImplementType.CULTIVATOR: ImplementPowerClass.PASSIVE,
    ImplementType.DISC_HARROW: ImplementPowerClass.PASSIVE,
    ImplementType.ROTAVATOR: ImplementPowerClass.ACTIVE,
    ImplementType.DISC_HARROW_POWERED: ImplementPowerClass.ACTIVE,
    ImplementType.CULTIVATOR_POWERED: ImplementPowerClass.ACTIVE,
}

# The diagram classifies Primary/Secondary only under Conventional tillage, so
# active (powered) tools are deliberately unclassified (None) rather than being
# assigned a stage the reference does not state.
IMPLEMENT_TILLAGE_STAGE = {
    ImplementType.MB_PLOUGH: TillageStage.PRIMARY,
    ImplementType.DISC_PLOUGH: TillageStage.SECONDARY,
    ImplementType.CULTIVATOR: TillageStage.SECONDARY,
    ImplementType.DISC_HARROW: TillageStage.SECONDARY,
    ImplementType.ROTAVATOR: None,
    ImplementType.DISC_HARROW_POWERED: None,
    ImplementType.CULTIVATOR_POWERED: None,
}

PASSIVE_IMPLEMENT_TYPES = tuple(
    t for t, cls in IMPLEMENT_POWER_CLASS.items() if cls is ImplementPowerClass.PASSIVE
)
ACTIVE_IMPLEMENT_TYPES = tuple(
    t for t, cls in IMPLEMENT_POWER_CLASS.items() if cls is ImplementPowerClass.ACTIVE
)


def power_class(implement_type: ImplementType) -> ImplementPowerClass:
    try:
        return IMPLEMENT_POWER_CLASS[implement_type]
    except KeyError:
        raise ValueError(
            f"Implement type '{getattr(implement_type, 'value', implement_type)}' has no power "
            "classification. Add it to IMPLEMENT_POWER_CLASS in app/core/implement_taxonomy.py."
        )


def tillage_stage(implement_type: ImplementType) -> Optional[TillageStage]:
    """Primary/Secondary for conventional tools; None for active/powered tools."""
    try:
        return IMPLEMENT_TILLAGE_STAGE[implement_type]
    except KeyError:
        raise ValueError(
            f"Implement type '{getattr(implement_type, 'value', implement_type)}' has no tillage "
            "stage entry. Add it to IMPLEMENT_TILLAGE_STAGE in app/core/implement_taxonomy.py."
        )


def is_active(implement_type: ImplementType) -> bool:
    return power_class(implement_type) is ImplementPowerClass.ACTIVE


def is_passive(implement_type: ImplementType) -> bool:
    return power_class(implement_type) is ImplementPowerClass.PASSIVE


class SlotAssignmentError(ValueError):
    """An implement was assigned to a simulation slot its power class cannot fill."""


def _passive_type_list() -> str:
    return ", ".join(t.value for t in PASSIVE_IMPLEMENT_TYPES)


def _active_type_list() -> str:
    return ", ".join(t.value for t in ACTIVE_IMPLEMENT_TYPES)


def _require_passive(implement_type: ImplementType, *, slot: str) -> None:
    if is_active(implement_type):
        raise SlotAssignmentError(
            f"{slot} must be a passive (unpowered) implement, but "
            f"'{implement_type.value}' is PTO-powered. The DSS draft equation is defined only "
            f"for passive tools. Valid choices: {_passive_type_list()}. To simulate a powered "
            "tool, use the Active + Passive combination and assign it to the rotor slot."
        )


def validate_slot_assignment(
    *,
    combination_type: SimulationCombinationType,
    implement_type: ImplementType,
    implement_2_type: Optional[ImplementType] = None,
    implement_id=None,
    implement_2_id=None,
) -> None:
    """Enforce the diagram's slot rules. Raises SlotAssignmentError (-> HTTP 422).

    single           : the implement must be passive.
    passive_passive  : both tools passive, and they must be two different records.
    active_passive   : the passive tool must be passive; the rotor (when selected
                       from the catalogue) must be active.
    """
    if combination_type == SimulationCombinationType.SINGLE:
        _require_passive(implement_type, slot="A conventional (single-implement) simulation")
        return

    if combination_type == SimulationCombinationType.PASSIVE_PASSIVE:
        _require_passive(implement_type, slot="Tool 1 of a passive-passive combination")
        if implement_2_type is not None:
            _require_passive(implement_type=implement_2_type, slot="Tool 2 of a passive-passive combination")
        if implement_id is not None and implement_2_id is not None and implement_id == implement_2_id:
            raise SlotAssignmentError(
                "A passive-passive combination needs two different implements; "
                "the same implement was selected for both tool 1 and tool 2."
            )
        return

    if combination_type == SimulationCombinationType.ACTIVE_PASSIVE:
        _require_passive(implement_type, slot="The passive tool of an active-passive combination")
        if implement_2_type is not None and not is_active(implement_2_type):
            raise SlotAssignmentError(
                f"The rotor slot of an active-passive combination must be a PTO-powered implement, "
                f"but '{implement_2_type.value}' is passive. Valid choices: {_active_type_list()}."
            )
        return
