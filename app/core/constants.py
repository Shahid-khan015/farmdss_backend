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
# [REFERENCE-CONFIRMED] -- was DSS-AMBIGUOUS until the spreadsheet formulas were read.
# The DSS document's Eq. 3.9 image literally shows exp(-0.3*S). Implemented
# literally, mu stays near zero across the whole practical 2-20% slip range, so
# the slip solver never converges for any realistic input -- in any of the three
# modes. 7.5 is the standard Wismer-Luth/Brixius literature value.
#
# This is no longer an assumption: the spreadsheet's own cell formula uses 7.5
# explicitly -- `C58 = C57*(1-EXP(-7.5*C55))-(1/C52)-(0.5*C55)/(SQRT(C52))` -- as
# does `tillage_dss.html` (K.TRACTION_SLIP_EXPONENT_COEFF). The document's 0.3 is
# a transcription error in the equation image. This is the single place it is defined.
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
# **Reverted to metres-for-every-implement**, to match `docs/tillage_dss (2).html`'s
# engine exactly (its `draftForceN` takes only `widthM`, with no tool-count concept
# at all). This re-admits a known, previously-fixed defect: reading `W` as metres for
# a cultivator gives a draft of ~505 N/m at every size -- the width cancels, so the
# figure carries no information -- against ~1975 N/m for a disc plough and
# ~4050 N/m for a disc harrow, and drops a 9-tine cultivator's total draft (measured
# ~4.97x too low against the per-tool reading) below a rotavator's forward thrust in
# active-passive combinations, making `Deff` non-positive and failing the run
# outright. Per-tool gave a consistent ~2070 N/m across sizes -- that fidelity is
# what this reversion gives up.
#
# Kept as an empty frozenset (not deleted) so `draft_width_parameter` and
# `draft_width_is_tool_count` need no changes: both already fall back to metres for
# any implement type absent from this set. Field capacity, turning and swath always
# use the width in metres regardless -- only Eq. 3.1's `W` was ever affected.
DRAFT_WIDTH_IS_TOOL_COUNT = frozenset()

# --- Soil-texture adjustment factor F (DSS Eq. 3.1) ---  [EXTERNAL-MODEL, ASABE D497 Table 1]
# Per-implement, per-texture. Keyed by ImplementType.value -> {SoilTexture.value: Fi}.
#
# **Currently unused** -- `legacy_algorithms.fi_factor` no longer reads this table.
# It was reinstated for one session (`farmdss/Rakesh Dss/Front _screen.frm`,
# Command6_Click lines 3011-3059, the 2006 VB6 tool this whole DSS derives from,
# independently carries this exact per-implement table -- confirmed from its own
# soil-texture radio buttons, Option6=Fine/Option7=Coarse/Option8=Medium, lines
# 193-222), then **reverted** in the next session so the engine matches
# `docs/tillage_dss (2).html` exactly -- that HTML reads Fi from a single texture
# selector with no implement dimension at all (see `FI_FACTOR_BY_TEXTURE` below,
# now used for the draft equation again, not just Table 4.2).
#
# Left defined, not deleted: this is a one-line revert
# (`fi_factor` back to `FI_FACTOR_BY_IMPLEMENT_TYPE[implement_type.value][...]`) if
# ASABE D497 fidelity is ever prioritised over HTML parity again. Flattening
# understates draft on non-moldboard tools in non-fine soil -- most steeply for disc
# tools in coarse soil, where Fi reads 0.45 instead of this table's 0.78 (draft
# 1.733x lower). MB Plough and fine soil are numerically unchanged either way.
FI_FACTOR_BY_IMPLEMENT_TYPE = {
    "MB Plough": {"Fine": 1.0, "Medium": 0.70, "Coarse": 0.45},
    "Disc Plough": {"Fine": 1.0, "Medium": 0.88, "Coarse": 0.78},
    "Disc Harrow": {"Fine": 1.0, "Medium": 0.88, "Coarse": 0.78},
    "Cultivator": {"Fine": 1.0, "Medium": 0.85, "Coarse": 0.65},
}

# [REFERENCE-ALIGNED] One factor per soil texture, applied to every implement --
# **reverted to being Eq. 3.1's draft Fi again**, to match `docs/tillage_dss (2).html`
# exactly: its `readCommonInputs` reads Fi from one texture selector
# (`fi: parseFloat(...)`), with no implement dimension. It is *also* still what Table
# 4.2's soil-condition classification uses (`soil_condition_from_fi` /
# `FI_TO_SOIL_CONDITION_BOUNDS` below) -- with `fi_factor` reverted, these two uses
# are the same value again, so there is no separate texture-only variable to keep in
# sync any more (see `calculate_legacy_performance`, which used to compute one).
#
# This is the flattened MB-Plough row the reference stack (spreadsheet D50:F53,
# `tillage_dss (2).html`) hard-codes; `FI_TO_SOIL_CONDITION_BOUNDS`'s 0.85/0.55
# thresholds are calibrated against exactly this set and must not be re-tuned if
# this table's values ever change.
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

# --- DSS Table 4.2 advisory thresholds ---  [DSS-EXACT]
# "Checking conditions and corresponding messages for different parameters": four
# conditions, each with its own literal message. See engineering_validation.
TABLE_4_2_SLIP_LIMIT_PCT = 15.0
TABLE_4_2_KWEF_MIN = 0.20
TABLE_4_2_PUT_LIMIT_PCT = 100.0

# [LEGACY -- VB6-sourced, not in DSS Table 4.2.] `Front _screen.frm` (Command6_Click,
# lines 3194-3201) flags slip below this floor with its own message ("Increase depth
# or speed of operation, Because slip is less than 8%"), reasoning that a run this far
# under the 8-15% band the DSS itself treats as optimal is leaving available traction
# unused. Table 4.2 (the DSS document's own advisory table) is silent on low slip --
# not opposed to flagging it, just missing it -- so this is purely additive, and kept
# distinct from the four DSS-EXACT messages so it is never mistaken for one.
TABLE_4_2_SLIP_UNDERUTILIZED_PCT = 8.0

# Net-traction-coefficient ceiling, by soil *condition*.  [DSS-EXACT values]
MU_THRESHOLD_BY_SOIL_CONDITION = {"soft": 0.40, "medium": 0.55, "firm": 0.60}

# [INTERPRETIVE MAPPING -- not specification.] Table 4.2's mu ceiling is indexed by
# soil *condition* (soft / medium / firm-hard, a bearing-strength taxonomy), while
# Eq. 3.1's Fi is indexed by soil *texture* (fine / medium / coarse). The DSS
# document defines both and provides **no crosswalk between them**. Rather than ask
# the operator for a second, overlapping soil classification, the texture already
# selected is mapped on the common agronomic association of coarse/sandy soils with
# lower bearing strength and fine/clay soils with higher.
#
# This is the weakest link in Table 4.2 and is worth confirming with the DSS author:
# it decides which mu ceiling a run is judged against, and therefore whether the
# "ballast rear axle" advice appears at all.
FI_TO_SOIL_CONDITION_BOUNDS = ((0.85, "firm"), (0.55, "medium"))  # else "soft"

# --- Field capacity / turning time ---
# [REFERENCE-ALIGNED] -- was tagged LEGACY/"absent from the DSS document", which was
# wrong. The turning-time expression is the spreadsheet's `C67 = 15.56 + 2.61*(C14/C18)
# - 1.41*C18`, and the 50-95% field-efficiency clamp is its `C73 = MIN(MAX(...,50),95)`.
# Both are reference-sourced, not inventions of the pre-existing implementation.
TURNING_TIME_COEFF_CONST = 15.56
TURNING_TIME_COEFF_WIDTH_OVER_SPEED = 2.61
TURNING_TIME_COEFF_SPEED = 1.41
FIELD_EFFICIENCY_CLAMP = (50.0, 95.0)  # percent

# [LEGACY] These two clamps are genuinely engine-only: the spreadsheet applies
# neither, and both originate in this implementation (mirrored by the HTML port).
TURNING_TIME_CLAMP = (8.0, 45.0)  # seconds
OVERALL_EFFICIENCY_CLAMP = (0.0, 100.0)
# NOTE: there is deliberately no upper clamp on fuel per hectare. An earlier
# FUEL_L_PER_HA_CLAMP = (0.0, 200.0) capped it, which neither reference does
# (xlsx `Fuel_Lha = Fuel_Lh / FCact`; tillage_dss.html floors at 0 only). A cap
# hides a genuinely heavy pairing behind a plausible-looking number.

# --- Input ranges the DSS document states explicitly ---
KI_RANGE = (0.0, 0.25)  # [DSS-EXACT] tool-interaction coefficient, Section 4
ROTOR_EFFICIENCY_RANGE = (0.25, 0.45)  # [DSS-EXACT] eta_r, Section 5
