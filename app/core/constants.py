"""Engine constants for the DSS simulation modules.

Every value carries a provenance tag from the audit vocabulary of
`docs/SIMULATION_ENGINE_FORMULAS.md`:
DSS-EXACT | DSS-AMBIGUOUS | IMPLEMENTATION-ASSUMPTION | LEGACY | EXTERNAL-MODEL.

Nothing in `legacy_algorithms.py`, `combi_algorithms.py` or `dss_shared.py` may
re-declare these as inline literals -- a value that appears in two places is a
value that can drift.
"""

from __future__ import annotations

GRAVITY = 9.81  # m/s^2
DIESEL_CALORIFIC_VALUE = 35.5  # MJ/l  [DSS-EXACT]

# --- Slip iteration (DSS spec: start at 2%, step 0.1%, until Pst >= D) ---
SLIP_INITIAL_PCT = 2.0  # [DSS-EXACT]
SLIP_INCREMENT_PCT = 0.1  # [DSS-EXACT]
MAX_SLIP_PCT = 20.0  # [IMPLEMENTATION-ASSUMPTION] the document gives no upper bound
MAX_SLIP_ITERATIONS = 500  # [IMPLEMENTATION-ASSUMPTION] loop safety net

# --- Wheel eccentricity: e = 0.1 * rolling_radius (Liljedahl et al., 1996) ---
# [DSS-EXACT] for er; [IMPLEMENTATION-ASSUMPTION] for ef (doc states 0.1 only for er).
WHEEL_ECCENTRICITY_COEFF = 0.1

# --- Depth at which draft acts: Yd = (2/3) * tillage depth ---  [DSS-EXACT]
DRAFT_DEPTH_ACTION_FRACTION = 2.0 / 3.0

# --- Rolling resistance / traction coefficient (DSS Section 3.4) ---
ROLLING_RESISTANCE_BASE = 0.04  # [DSS-EXACT]
ROLLING_RESISTANCE_SLIP_COEFF = 0.5  # [DSS-EXACT]
TRACTION_MU_G_SCALE = 0.88  # [DSS-EXACT]
TRACTION_BN_EXPONENT_COEFF = 0.1  # [DSS-EXACT]
# [DSS-AMBIGUOUS -> IMPLEMENTATION-ASSUMPTION]
# The DSS document's Eq. 3.9 image literally shows exp(-0.3*S). Implemented
# literally, mu stays near zero across the whole practical 2-20% slip range, so
# the slip solver never converges for any realistic input -- in any of the three
# modes. 7.5 is the standard Wismer-Luth/Brixius literature value and produces
# physically realistic traction curves. Confirmed with the user as a corrected
# transcription, not a literal DSS value. This is the single place it is defined.
TRACTION_SLIP_EXPONENT_COEFF = 7.5

# --- Ballast requirement targets (DSS spec) ---
FRONT_BALLAST_TARGET_KWEF = 0.20  # [DSS-EXACT] Kwef = Rf/Wt >= 0.20
REAR_BALLAST_TARGET_SLIP_PCT = 15.0  # [DSS-EXACT]
BALLAST_SOLVER_TOLERANCE = 1e-4  # [IMPLEMENTATION-ASSUMPTION] solver tuning
BALLAST_SOLVER_MAX_ITERATIONS = 200  # [IMPLEMENTATION-ASSUMPTION] solver tuning

# --- Py/D ratio (vertical:horizontal soil-reaction ratio) by implement type ---
# DSS spec: "Use this ratio Py/D as 0.15 for Moldboard plough, 0.40 for disk plough,
# 0.50 for disc harrow and 0 for cultivator" (Kepner et al., 1978).  [DSS-EXACT]
PY_OVER_D_RATIO_BY_IMPLEMENT = {
    "MB Plough": 0.15,
    "Disc Plough": 0.40,
    "Disc Harrow": 0.50,
    "Cultivator": 0.0,
}

# --- Soil-texture adjustment factor F (DSS Eq. 3.1) ---
# [LEGACY] The document names the texture classes (fine/medium/coarse) but gives
# no numeric table; these values are carried over from the pre-existing codebase
# and have no cited source. Keyed by ImplementType.value / SoilTexture.value.
FI_FACTOR_BY_IMPLEMENT_AND_TEXTURE = {
    "MB Plough": {"Fine": 1.0, "Medium": 0.70, "Coarse": 0.45},
    "Disc Plough": {"Fine": 1.0, "Medium": 0.88, "Coarse": 0.78},
    "Disc Harrow": {"Fine": 1.0, "Medium": 0.88, "Coarse": 0.78},
    "Cultivator": {"Fine": 1.0, "Medium": 0.85, "Coarse": 0.65},
}

# --- ASABE (2001) specific fuel consumption ---  [DSS-EXACT]
# SFC = 2.64X + 3.91 - 0.203*sqrt(738X + 173), L/kW-h
SFC_COEFF_A = 2.64
SFC_COEFF_B = 3.91
SFC_COEFF_C = 0.203
SFC_RADICAND_COEFF = 738.0
SFC_RADICAND_OFFSET = 173.0

# --- Power-utilization status bands (DSS "Check Put value" table) ---  [DSS-EXACT]
PUT_PROPERLY_LOADED_RANGE = (95.0, 100.0)

# --- Field capacity / turning time ---
# [LEGACY] Absent from the DSS document; preserved unchanged from the
# pre-existing implementation. turning_time_s = 15.56 + 2.61*(W/S) - 1.41*S.
TURNING_TIME_COEFF_CONST = 15.56
TURNING_TIME_COEFF_WIDTH_OVER_SPEED = 2.61
TURNING_TIME_COEFF_SPEED = 1.41
TURNING_TIME_CLAMP = (8.0, 45.0)  # seconds
FIELD_EFFICIENCY_CLAMP = (50.0, 95.0)  # percent
FUEL_L_PER_HA_CLAMP = (0.0, 200.0)
OVERALL_EFFICIENCY_CLAMP = (0.0, 100.0)

# --- Input ranges the DSS document states explicitly ---
KI_RANGE = (0.0, 0.25)  # [DSS-EXACT] tool-interaction coefficient, Section 4
ROTOR_EFFICIENCY_RANGE = (0.25, 0.45)  # [DSS-EXACT] eta_r, Section 5
