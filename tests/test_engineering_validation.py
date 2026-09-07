"""Tests for app.core.engineering_validation's operating-range checks.

`validate_operating_ranges` gates every simulation request; the implement-width
floor in particular must not reject the reference's own canonical data.
"""

from __future__ import annotations

from app.core.engineering_validation import validate_operating_ranges


def _base(**overrides) -> dict:
    base = dict(speed=4.0, depth=15.0, cone_index=1200.0, implement_width=1.5, pto_power=25.0)
    base.update(overrides)
    return base


def _errors_for(field: str, errors: list) -> list:
    return [e for e in errors if e["field"] == field]


# --- Implement-width floor ----------------------------------------------------


def test_implement_width_floor_accepts_the_reference_single_bottom_mb_plough():
    """Excel's "tractor and implement data" sheet / both HTML libraries ship a
    0.3 m single-bottom MB plough as their own default worked example. The
    floor used to sit at 0.5 m, which rejected it outright.
    """
    errors = validate_operating_ranges(_base(implement_width=0.3))
    assert _errors_for("implement_width", errors) == []


def test_implement_width_floor_accepts_the_reference_single_disc_plough():
    """Same reference, its 0.45 m single-disc-plough row."""
    errors = validate_operating_ranges(_base(implement_width=0.45))
    assert _errors_for("implement_width", errors) == []


def test_implement_width_floor_still_rejects_nonsense_input():
    """The floor is lowered, not removed -- a near-zero width is still refused."""
    errors = validate_operating_ranges(_base(implement_width=0.1))
    width_errors = _errors_for("implement_width", errors)
    assert len(width_errors) == 1
    assert width_errors[0]["code"] == "out_of_range"
    assert width_errors[0]["range"]["min"] == 0.2


def test_implement_width_upper_bound_is_unchanged():
    errors = validate_operating_ranges(_base(implement_width=5.5))
    width_errors = _errors_for("implement_width", errors)
    assert len(width_errors) == 1
    assert width_errors[0]["range"]["max"] == 5.0


def test_implement_width_at_the_new_floor_is_accepted():
    assert _errors_for("implement_width", validate_operating_ranges(_base(implement_width=0.2))) == []
