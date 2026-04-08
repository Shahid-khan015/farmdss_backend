from __future__ import annotations

import enum


class DriveMode(str, enum.Enum):
    WD2 = "2WD"
    WD4 = "4WD"


class TireType(str, enum.Enum):
    BIAS_PLY = "Bias Ply"
    RADIAL_PLY = "Radial Ply"


class ImplementType(str, enum.Enum):
    MB_PLOUGH = "MB Plough"
    DISC_PLOUGH = "Disc Plough"
    CULTIVATOR = "Cultivator"
    DISC_HARROW = "Disc Harrow"


class SoilTexture(str, enum.Enum):
    FINE = "Fine"
    COARSE = "Coarse"
    MEDIUM = "Medium"


class SoilHardness(str, enum.Enum):
    HARD = "Hard"
    FIRM = "Firm"
    TILLED = "Tilled"
    SOFT = "Soft"

