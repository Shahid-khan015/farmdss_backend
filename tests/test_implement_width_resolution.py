"""Regression tests for `Implement.resolved_width_m` (F4).

This is the single width-resolution rule every subsystem now shares --
`working_width_m` takes precedence, `width` is the fallback. Before this fix,
`routes/simulations.py` read `width` alone while `routes/sessions.py` read
`working_width_m` first: a record with only `working_width_m` set tracked field
area correctly but 422'd on every simulation. These tests pin the resolution
rule itself; `test_engineering_validation.py` and the simulation-path tests
cover it downstream of the API.
"""

from __future__ import annotations

from decimal import Decimal

from app.models.implement import Implement


def _implement(*, working_width_m=None, width=None) -> Implement:
    obj = Implement()
    obj.working_width_m = working_width_m
    obj.width = width
    return obj


# --- Scenario 1: working_width_m only ----------------------------------------


def test_resolved_width_uses_working_width_m_when_width_is_absent():
    obj = _implement(working_width_m=2.4, width=None)
    assert obj.resolved_width_m == Decimal("2.4")


# --- Scenario 2: width only ---------------------------------------------------


def test_resolved_width_falls_back_to_width_when_working_width_m_is_absent():
    obj = _implement(working_width_m=None, width=Decimal("1.8"))
    assert obj.resolved_width_m == Decimal("1.8")


# --- Scenario 3: both fields set -----------------------------------------------


def test_resolved_width_prefers_working_width_m_when_both_are_set():
    """working_width_m wins even when it disagrees with width -- this is the
    exact case that used to make simulation and session tracking disagree
    silently (simulation used `width`, sessions used `working_width_m`)."""
    obj = _implement(working_width_m=3.0, width=Decimal("1.2"))
    assert obj.resolved_width_m == Decimal("3.0")


def test_resolved_width_type_is_always_decimal():
    """working_width_m is a plain float column, unlike width's Decimal -- callers
    must see one consistent numeric type regardless of which column answered."""
    from_working_width = _implement(working_width_m=2.4, width=None).resolved_width_m
    from_width = _implement(working_width_m=None, width=Decimal("2.4")).resolved_width_m
    assert isinstance(from_working_width, Decimal)
    assert isinstance(from_width, Decimal)
    assert from_working_width == from_width


# --- Neither set ---------------------------------------------------------------


def test_resolved_width_is_none_when_neither_field_is_set():
    obj = _implement(working_width_m=None, width=None)
    assert obj.resolved_width_m is None
