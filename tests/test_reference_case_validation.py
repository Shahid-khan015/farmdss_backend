"""Reference-case validation against the spreadsheet's formula algebra.

These tests pin the engine to `docs/Tractor_Implement_Performance_Calculator Updated.xlsx`
stage by stage, so that a future edit cannot silently drift away from the validated
reference chain.

**Read this before "fixing" anything here to match the workbook's own numbers.**

The workbook's *formulas* are authoritative and the engine reproduces them exactly. Its
*computed outputs* are not usable: sheet1's tyre block (rows 29-36) was pasted one row
off from the data sheet and is labelled `m` while holding `mm`, so cell `C32` (labelled
`rf`, the front rolling radius) actually contains the **rear overall diameter**, 789.43.
The workbook therefore computes `ef = 0.1 * 789.43 = 78.94 metres` of wheel eccentricity,
and every traction figure downstream of it is an artefact:

    Rf       =         27.2 N   (2.8 kg carried on the front axle)
    Bn_rear  = 44,539,161.7     (a wheel numeric; the physical band is ~5-80)
    TE       =         13.65 %
    Put      =        189.6 %

The reference case below therefore takes its tyre dimensions **corrected**, from the
"tractor and implement data" sheet (column F, mm -> m), and compares against the
workbook's formula chain evaluated on those corrected inputs.
"""
from __future__ import annotations

import math

import pytest

from app.core.constants import (
    ROLLING_RESISTANCE_BASE,
    ROLLING_RESISTANCE_SLIP_COEFF,
    TRACTION_BN_EXPONENT_COEFF,
    TRACTION_MU_G_SCALE,
    TRACTION_SLIP_EXPONENT_COEFF,
)
from app.core.dss_shared import draft_force_n, field_capacity
from app.core.legacy_algorithms import LegacyInputs, calculate_legacy_performance
from app.models.enums import ImplementType, SoilTexture

# --- The reference case -------------------------------------------------------
# Spreadsheet sheet1 "Performance Calculator" with its default inputs:
# VST Shakti MT 180D HS/JAI + MB Plough single bottom, fine soil.
# Tyre dimensions from sheet2 column F (mm -> m), NOT from the corrupted sheet1 block.

GRAVITY = 9.81
FI = 1.0  # sheet2 D51:F51 -- "Fine" soil
ASAE_A, ASAE_B, ASAE_C = 652.0, 0.0, 5.1  # sheet2 E11:E13, MB plough
CONE_INDEX_KPA = 1500.0  # C10
DEPTH_CM = 15.0  # C13
WIDTH_M = 0.3  # C14 / sheet2 E7
IMPLEMENT_MASS_KG = 125.0  # C15 / sheet2 E8
XCGI_M = 0.45  # C16 / sheet2 E9
PY_OVER_D = 0.2  # C17 / sheet2 E10
SPEED_KMH = 2.5  # C18
PTO_POWER_KW = 12.0  # C19 / sheet2 F22
WHEELBASE_M = 1.42  # C22 / sheet2 F25
FRONT_AXLE_KG = 315.0  # C23 / sheet2 F26
REAR_AXLE_KG = 440.0  # C24 / sheet2 F27
HITCH_M = 0.72  # C25 / sheet2 F30
XCGT_M = 0.59  # C26 / sheet2 F29
TRANS_EFF_PCT = 86.0  # C27 (0.86 as a fraction in the sheet)
POWER_RESERVE_PCT = 20.0  # C28 (0.20 as a fraction in the sheet)
MAX_ENGINE_TORQUE_NM = 45.5  # C21 / sheet2 F24

# sheet2 F37:F44, mm -> m
FRONT_DIAMETER_M = 513.59 / 1000
FRONT_SECTION_WIDTH_M = 127.0 / 1000
FRONT_ROLLING_RADIUS_M = 245.01 / 1000
REAR_DIAMETER_M = 789.43 / 1000
REAR_SECTION_WIDTH_M = 203.2 / 1000
REAR_ROLLING_RADIUS_M = 375.85 / 1000

FIELD_LENGTH_M = 200.0  # C11
FIELD_WIDTH_M = 100.0  # C12
FIELD_AREA_HA = (FIELD_LENGTH_M * FIELD_WIDTH_M) / 10000.0  # C69


def _reference_inputs() -> LegacyInputs:
    return LegacyInputs(
        pto_power_kw=PTO_POWER_KW,
        wheelbase_m=WHEELBASE_M,
        front_axle_weight_kg=FRONT_AXLE_KG,
        rear_axle_weight_kg=REAR_AXLE_KG,
        hitch_distance_from_rear_m=HITCH_M,
        cg_distance_from_rear_m=XCGT_M,
        transmission_efficiency_pct=TRANS_EFF_PCT,
        power_reserve_pct=POWER_RESERVE_PCT,
        front_rolling_radius_m=FRONT_ROLLING_RADIUS_M,
        rear_rolling_radius_m=REAR_ROLLING_RADIUS_M,
        front_overall_diameter_m=FRONT_DIAMETER_M,
        rear_overall_diameter_m=REAR_DIAMETER_M,
        front_section_width_m=FRONT_SECTION_WIDTH_M,
        rear_section_width_m=REAR_SECTION_WIDTH_M,
        implement_type=ImplementType.MB_PLOUGH,
        width_m=WIDTH_M,
        weight_kg=IMPLEMENT_MASS_KG,
        cg_distance_from_hitch_m=XCGI_M,
        vertical_horizontal_ratio=PY_OVER_D,
        asae_param_a=ASAE_A,
        asae_param_b=ASAE_B,
        asae_param_c=ASAE_C,
        soil_texture=SoilTexture.FINE,
        cone_index_kpa=CONE_INDEX_KPA,
        depth_cm=DEPTH_CM,
        speed_kmh=SPEED_KMH,
        field_area_ha=FIELD_AREA_HA,
        field_width_m=FIELD_WIDTH_M,
        max_engine_torque_nm=MAX_ENGINE_TORQUE_NM,
    )


@pytest.fixture(scope="module")
def result() -> dict:
    return calculate_legacy_performance(_reference_inputs())


# --- Stage-by-stage agreement with the spreadsheet formula chain ---------------
#
# Expected values are the workbook's own formulas evaluated on the corrected tyre
# inputs. Every one of these agreed to every printed digit at the time of writing.


def test_draft_matches_spreadsheet_c43_with_depth_in_cm_and_no_divisor(result):
    """`C43 = C6*(C7+C8*C18+C9*C18^2)*C14*C13` -- note there is no `/10` on depth.

    The workbook's *note* column claims `W*(Td/10)`, which is where the historical
    10x-too-small draft bug came from. The formula beside it has no divisor.
    """
    assert result["draft_force"] == pytest.approx(3077.4375, abs=1e-6)


def test_axle_loads_match_spreadsheet_c48_and_c49(result):
    """Eq. 3.5/3.6 -- `C48` and `C49`, character-for-character the engine's balance."""
    assert result["legacy_rear_axle_load_n"] == pytest.approx(7702.226122, abs=1e-5)
    assert result["legacy_front_axle_load_n"] == pytest.approx(1546.061378, abs=1e-5)


def test_wheel_numerics_match_spreadsheet_c52_and_c53(result):
    """`Bn = CI*b*d/Wd` with `Wd = (axle/2)/1000` kN -- `C50`-`C53`, no shape factor."""
    assert result["legacy_mobility_number_rear"] == pytest.approx(62.480187, abs=1e-5)
    assert result["legacy_mobility_number_front"] == pytest.approx(126.565344, abs=1e-5)


def test_wheel_numerics_land_in_the_physically_meaningful_band(result):
    """A wheel numeric is dimensionless and lives in roughly 5-80.

    This is the guard the workbook itself lacks: with its shipped tyre block it
    computes 44,539,162, and nothing in the sheet notices.
    """
    for key in ("legacy_mobility_number_rear", "legacy_mobility_number_front"):
        assert 5.0 <= result[key] <= 200.0, f"{key} = {result[key]} is not a wheel numeric"


def test_gross_traction_ratio_matches_spreadsheet_c57(result):
    """`C57 = 0.88*(1-EXP(-0.1*Bn))`."""
    assert result["legacy_gross_traction_ratio"] == pytest.approx(0.878298, abs=1e-6)


def test_drawbar_power_matches_spreadsheet_c60(result):
    """`C60 = D*(S/3.6)/1000` -- the only km/h -> m/s conversion in the chain."""
    assert result["drawbar_power"] == pytest.approx(2.137109, abs=1e-6)


def test_theoretical_field_capacity_matches_spreadsheet_c66(result):
    """`C66 = (S*W)/10`."""
    assert result["field_capacity_theoretical"] == pytest.approx(0.075, abs=1e-9)


def test_slip_solves_rather_than_being_assumed(result):
    """The spreadsheet hard-codes `C55 = 0.02`; the engine solves the DSS schedule.

    This is a capability the reference lacks, not a disagreement with it.
    """
    assert result["converged"] is True
    assert result["slip"] == pytest.approx(8.703846, abs=1e-4)
    assert 2.0 < result["slip"] < 20.0


# --- The traction-efficiency conflict, pinned with its evidence ----------------


def _spreadsheet_te_fraction(mu: float, slip_fraction: float, mu_g: float) -> float:
    """`C59 = (C58*(1-C55))/C57` -- the workbook's TE, using the Brixius *envelope*."""
    return (mu * (1.0 - slip_fraction)) / mu_g


def test_engine_te_matches_the_spreadsheet_te_formula(result):
    """The engine's headline TE is now the workbook's own `C59` form.

    `C59 = (C58*(1-C55))/C57` divides by the envelope `mu_g`, which is also what
    DSS spec Eq. (3.2) prescribes. The at-slip form the engine used previously is
    retained as `traction_efficiency_at_slip_percent`.
    """
    mu = result["coefficient_net_traction"]
    mu_g = result["legacy_gross_traction_ratio"]
    s = result["slip"] / 100.0

    assert result["traction_efficiency"] == pytest.approx(
        _spreadsheet_te_fraction(mu, s, mu_g) * 100.0, abs=1e-6
    )
    assert result["traction_efficiency"] == pytest.approx(41.532126, abs=1e-4)
    # The retained diagnostic still reads markedly higher.
    assert result["traction_efficiency_at_slip_percent"] == pytest.approx(79.116267, abs=1e-4)


def test_the_specified_te_form_costs_headroom_on_a_matched_pairing():
    """The accepted consequence of Eq. (3.2), stated as a number rather than a claim.

    A 12 kW tractor pulling a single-bottom 0.3 m mouldboard plough at 2.5 km/h and
    15 cm is the canonical pairing that size of tractor exists for. Evaluated at the
    workbook's own hard-coded 2% slip, the specified envelope form puts it above
    200% of available power where the at-slip form leaves headroom.

    This is retained deliberately: it is the sharpest available illustration of what
    the specified denominator does to the load verdict, and it is the number to
    revisit if a field measurement is ever taken. It is NOT an argument for changing
    the engine -- conformance to Eq. (3.2) is settled.
    """
    slip_fraction = 0.02  # the workbook's hard-coded C55
    bn_rear = 62.480187
    mu_g = TRACTION_MU_G_SCALE * (1.0 - math.exp(-TRACTION_BN_EXPONENT_COEFF * bn_rear))
    mu = (
        mu_g * (1.0 - math.exp(-TRACTION_SLIP_EXPONENT_COEFF * slip_fraction))
        - 1.0 / bn_rear
        - ROLLING_RESISTANCE_SLIP_COEFF * slip_fraction / math.sqrt(bn_rear)
    )
    te_envelope = _spreadsheet_te_fraction(mu, slip_fraction, mu_g)
    te_at_slip = (mu * (1.0 - slip_fraction)) / (
        mu_g * (1.0 - math.exp(-TRACTION_SLIP_EXPONENT_COEFF * slip_fraction))
        + ROLLING_RESISTANCE_BASE
    )

    pdb_kw = 3077.4375 * (SPEED_KMH / 3.6) / 1000.0
    trans_eff = TRANS_EFF_PCT / 100.0
    available_kw = PTO_POWER_KW * (1.0 - POWER_RESERVE_PCT / 100.0)

    put_envelope = (pdb_kw / (te_envelope * trans_eff)) / available_kw * 100.0
    put_at_slip = (pdb_kw / (te_at_slip * trans_eff)) / available_kw * 100.0

    assert put_envelope > 200.0, "the specified TE form calls this pairing overloaded"
    assert put_at_slip < 60.0, "the retained at-slip diagnostic leaves headroom"


# --- Eq. 3.1's `W` for tined implements ---------------------------------------


def test_cultivator_draft_scales_with_whatever_number_w_is_given():
    """ASABE D497 tabulates field cultivators per tool; `A = 32` is a per-tool row,
    so a tool-count `W` (e.g. 9 for a 9-tine cultivator) is the D497-faithful
    reading. `draft_force_n` itself takes whichever number it's given for `width_m`
    and applies Eq. 3.1 uniformly -- this pins the raw arithmetic for both readings,
    it does not say which one `draft_width_parameter` picks.

    Production (`legacy_algorithms.draft_width_parameter`,
    `constants.DRAFT_WIDTH_IS_TOOL_COUNT`) currently reads **metres**, matching both
    the workbook and `docs/tillage_dss (2).html` -- which makes draft scale with
    width in a way that cancels (~505 N/m at every cultivator size, so the number
    carries no size information) and can drop a 9-tine cultivator's draft below a
    rotavator's forward thrust in active-passive combinations. A tool-count reading
    was adopted for one session instead, and is a one-line revert away
    (`DRAFT_WIDTH_IS_TOOL_COUNT = frozenset({"Cultivator"})`) if D497 fidelity is
    ever prioritised over HTML parity again.

    The workbook corroborates the tool-count reading against itself, for the record:
    its own cultivator widths (2.20 / 2.66 / 3.13 m for 9 / 11 / 13 tines) imply a
    tine spacing of 244 / 242 / 241 mm -- textbook and consistent.
    """
    common = dict(
        fi=1.0, asae_param_a=32.0, asae_param_b=1.9, asae_param_c=0.0,
        speed_kmh=SPEED_KMH, depth_cm=DEPTH_CM,
    )
    per_tool = draft_force_n(width_m=9.0, **common)  # W := number of tools
    per_metre = draft_force_n(width_m=2.2, **common)  # W := width in metres

    assert per_tool == pytest.approx(4961.25, abs=1e-6)
    assert per_metre == pytest.approx(1212.75, abs=1e-6)
    assert per_tool > 4 * per_metre / 1.1

    for width_m, tines in ((2.20, 9), (2.66, 11), (3.13, 13)):
        spacing_mm = width_m / tines * 1000.0
        assert 235.0 <= spacing_mm <= 250.0


# --- Turning time: the unresolved factor of 2 ---------------------------------


def test_field_capacity_reports_both_turning_time_bases():
    """The engine's headland time carries a factor of 2 the spreadsheet does not have.

    `C71 = (C68*C67)/3600`; the engine uses `turning_time_s * 2 * number_turns`.
    No source derives the 2 either way, so both are reported until the DSS author
    rules. This test exists so the discrepancy cannot quietly disappear.
    """
    fc = field_capacity(
        speed_kmh=SPEED_KMH,
        width_m=WIDTH_M,
        field_area_ha=FIELD_AREA_HA,
        field_width_m=FIELD_WIDTH_M,
    )
    assert fc.number_turns == 333  # C68 = ROUND(100/0.3, 0)
    assert fc.turning_time_s == pytest.approx(12.3482, abs=1e-4)  # C67
    assert fc.turning_time_single_pass_basis_h == pytest.approx(1.142208, abs=1e-6)  # C71
    assert fc.total_turning_time_h == pytest.approx(2.284417, abs=1e-6)
    assert fc.total_turning_time_h == pytest.approx(2.0 * fc.turning_time_single_pass_basis_h)


def test_field_efficiency_matches_the_engines_own_turning_basis(result):
    """`C73 = MIN(MAX((FCac/FCth)*100, 50), 95)`, on the engine's doubled turn time."""
    assert result["field_efficiency"] == pytest.approx(92.109390, abs=1e-5)


def test_both_turning_bases_reach_the_result_payload(result):
    """The discrepancy must be visible to a caller, not only inside the dataclass."""
    doubled = result["headland_turning_time_hours"]
    single = result["headland_turning_time_single_pass_basis_hours"]
    assert doubled == pytest.approx(2.284417, abs=1e-6)
    assert single == pytest.approx(1.142208, abs=1e-6)
    assert doubled == pytest.approx(2.0 * single)


# --- Constants confirmed by the workbook's formulas ---------------------------


def test_slip_exponent_is_the_value_the_workbook_uses_not_the_documents():
    """`C58` uses `EXP(-7.5*C55)`. The DSS document's equation image shows 0.3.

    Implemented literally, 0.3 leaves mu near zero across the whole 2-20% band and
    the slip solver never converges in any mode. The workbook settles it.
    """
    assert TRACTION_SLIP_EXPONENT_COEFF == 7.5


def test_fuel_per_hour_matches_the_workbooks_own_drawbar_basis(result):
    """`C65 = C64*C60` is SFC x DBp -- matched exactly, and also matches
    `docs/tillage_dss (2).html`'s `fuelLph = sfc * pdbKw`.

    A PTO-power basis (billing against `Ptr`, the power actually produced) was
    adopted for one session on physical grounds and is still emitted as the
    `fuel_l_per_hour_pto_basis` diagnostic; the two differ by exactly the traction
    loss `1/(TE x eta_t)`.
    """
    sfc = result["specific_fuel_consumption"]
    assert result["fuel_basis"] == "drawbar"
    assert result["fuel_l_per_hour"] == pytest.approx(
        sfc * result["drawbar_power"], rel=1e-9
    )
    assert result["fuel_l_per_hour_pto_basis"] == pytest.approx(
        sfc * result["required_pto_power"], rel=1e-9
    )
    ratio = result["fuel_l_per_hour_pto_basis"] / result["fuel_l_per_hour"]
    assert ratio == pytest.approx(
        1.0 / (result["traction_efficiency"] / 100.0 * TRANS_EFF_PCT / 100.0), rel=1e-9
    )
