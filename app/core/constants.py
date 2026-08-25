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
# [EXTERNAL-MODEL] Values from the reference implementations: the "Vertical to
# Horizontal force ratio" row of `Tractor_Implement_Performance_Calculator
# Updated.xlsx` (sheet 2, row 10) and the `PyD` field of `tillage_dss.html`'s
# implement library. The two agree exactly.
#
# This is a FALLBACK only. Both references carry Py/D as a per-implement input,
# not a per-type constant -- see `Implement.vertical_horizontal_ratio`, which the
# engine now reads first. This table is used only when that column is NULL.
#
# Supersedes an earlier table (MB 0.15 / disc plough 0.40 / disc harrow 0.50 /
# cultivator 0.0) attributed to Kepner et al. 1978 via the DSS document. Those
# values disagree with both references on every row -- nearly inverted for the
# disc tools and the cultivator -- and materially changed the axle-load split.
PY_OVER_D_RATIO_BY_IMPLEMENT = {
    "MB Plough": 0.20,
    "Disc Plough": 0.0,
    "Disc Harrow": 0.0,
    "Cultivator": 0.20,
}

# --- Eq. 3.1 `W`: width in metres, or number of tools ---  [EXTERNAL-MODEL]
# ASABE D497 Table 1 does not use one unit for `W`. For full-width tools
# (mouldboard/disc ploughs, disc harrows) `W` is the working width in metres.
# For tined implements the row is tabulated *per tool*, so `W` is the number of
# tools -- `A = 32, B = 1.9, C = 0` is D497's secondary-tillage field cultivator,
# which is a per-tool row.
#
# Reading `W` as metres for a cultivator yields a draft of ~505 N/m at every
# size -- the width cancels, so the figure carries no information -- against
# ~1975 N/m for a disc plough and ~4050 N/m for a disc harrow. It also drops a
# 9-tine cultivator's total draft below a rotavator's forward thrust, which makes
# the effective draft of an active-passive combination non-positive and fails the
# run outright. Per-tool gives a consistent ~2070 N/m across sizes.
#
# Both reference implementations use metres for every implement; this is a
# deliberate, documented divergence from them. Field capacity, turning and swath
# always use the width in metres -- only Eq. 3.1's `W` is affected.
DRAFT_WIDTH_IS_TOOL_COUNT = frozenset({"Cultivator"})

# --- Soil-texture adjustment factor F (DSS Eq. 3.1) ---
# [REFERENCE-ALIGNED] One factor per soil texture, applied to every implement.
# Keyed by SoilTexture.value.
#
# This is the reference stack's Fi, adopted deliberately so that the engine, the
# spreadsheet and the HTML tool agree. Its authority is the spreadsheet
# `docs/Tractor_Implement_Performance_Calculator Updated.xlsx`, sheet "tractor
# and implement data" cells D50:F53 -- a three-row Soil Type/Fi table with no
# implement dimension -- and `docs/tillage_dss (2).html`, which hard-codes the
# same three values in its texture selector.
#
# Known departure from ASABE D497: D497 Table 1 carries its own F1/F2/F3 per
# implement row (disc tools 1.0/0.88/0.78, cultivators 1.0/0.85/0.65), and these
# three values are specifically its *moldboard-plough* row. Using them for every
# implement therefore understates draft on non-moldboard tools in non-fine soil
# -- most steeply for disc tools in coarse soil, where Fi falls 0.78 -> 0.45 and
# draft with it (0.577x), carrying slip, power utilisation and fuel down with it.
# MB Plough is unaffected in every texture, as is fine soil for every implement.
# This is an accepted, deliberate trade of D497 fidelity for cross-tool
# consistency; see docs/SIMULATION_ENGINE_FORMULAS.md.
FI_FACTOR_BY_TEXTURE = {"Fine": 1.0, "Medium": 0.70, "Coarse": 0.45}

# --- ASABE (2001) specific fuel consumption ---  [DSS-EXACT]
# SFC = 2.64X + 3.91 - 0.203*sqrt(738X + 173), L/kW-h
SFC_COEFF_A = 2.64
SFC_COEFF_B = 3.91
SFC_COEFF_C = 0.203
SFC_RADICAND_COEFF = 738.0
SFC_RADICAND_OFFSET = 173.0

# --- Minimum rated PTO power accepted by input validation ---
# A floor to catch missing/nonsense input, not an equipment-class restriction.
# Indian power tillers start around 5 kW and the library's smallest tractor is
# 6.6 kW, so 10 kW (the previous value) rejected our own catalogue.
PTO_POWER_MIN_KW = 5.0

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
OVERALL_EFFICIENCY_CLAMP = (0.0, 100.0)
# NOTE: there is deliberately no upper clamp on fuel per hectare. An earlier
# FUEL_L_PER_HA_CLAMP = (0.0, 200.0) capped it, which neither reference does
# (xlsx `Fuel_Lha = Fuel_Lh / FCact`; tillage_dss.html floors at 0 only). A cap
# hides a genuinely heavy pairing behind a plausible-looking number.

# --- Input ranges the DSS document states explicitly ---
KI_RANGE = (0.0, 0.25)  # [DSS-EXACT] tool-interaction coefficient, Section 4
ROTOR_EFFICIENCY_RANGE = (0.25, 0.45)  # [DSS-EXACT] eta_r, Section 5
