from __future__ import annotations

import enum


class DriveMode(str, enum.Enum):
    WD2 = "2WD"
    WD4 = "4WD"


class TireType(str, enum.Enum):
    BIAS_PLY = "Bias Ply"
    RADIAL_PLY = "Radial Ply"


class ImplementType(str, enum.Enum):
    # Passive (unpowered) tillage tools -- these are the only types the DSS
    # draft equation (Eq. 3.1) and the Fi / Py-D tables are defined for.
    MB_PLOUGH = "MB Plough"
    DISC_PLOUGH = "Disc Plough"
    CULTIVATOR = "Cultivator"
    DISC_HARROW = "Disc Harrow"

    # Active (PTO-powered) tools. These never go through the passive draft
    # equation -- they occupy the active-rotor slot of an active-passive
    # combination and are characterised by rotor specs (Da, eta_r, P_PTO, N).
    ROTAVATOR = "Rotavator"
    DISC_HARROW_POWERED = "Disc Harrow (Powered)"
    CULTIVATOR_POWERED = "Cultivator (Powered)"


class ImplementPowerClass(str, enum.Enum):
    """Whether an implement is towed (passive) or PTO-driven (active)."""

    PASSIVE = "passive"
    ACTIVE = "active"


class TillageStage(str, enum.Enum):
    """Conventional-tillage stage. Not applicable to active/powered tools."""

    PRIMARY = "primary"
    SECONDARY = "secondary"


class DiscHarrowConfiguration(str, enum.Enum):
    """Descriptive disc-harrow arrangement. Has NO effect on the calculations --
    the DSS document gives no distinct coefficients for Tandem vs Offset."""

    TANDEM = "Tandem"
    OFFSET = "Offset"


class SoilTexture(str, enum.Enum):
    FINE = "Fine"
    COARSE = "Coarse"
    MEDIUM = "Medium"


class SoilHardness(str, enum.Enum):
    HARD = "Hard"
    FIRM = "Firm"
    TILLED = "Tilled"
    SOFT = "Soft"


class SimulationCombinationType(str, enum.Enum):
    """Which DSS simulation mode a Simulation was run under (Sections 3/4/5)."""

    SINGLE = "single"
    PASSIVE_PASSIVE = "passive_passive"
    ACTIVE_PASSIVE = "active_passive"


class SimulationImplementRole(str, enum.Enum):
    """A given implement's role within a (possibly combined) simulation."""

    PRIMARY = "primary"  # single-implement mode, or tool 1 of a passive-passive pair
    PASSIVE_2 = "passive_2"  # tool 2 of a passive-passive pair
    ACTIVE_ROTOR = "active_rotor"  # PTO-driven rotor of an active-passive combination

