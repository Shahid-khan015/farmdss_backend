"""Regression cover for the session-charges engine.

The rules under test are the owner's and are not defined here: every operation is charged
per hectare except Threshing and Grading, which are charged per hour, at whatever rate the
tractor's owner configured.

What these tests pin is *when* those inputs are read and how the arithmetic is done --
which is where the engine used to be wrong:

* the rate was resolved at session END and re-resolved on every summary read, so an owner
  editing their rate card silently re-billed sessions that were already settled;
* an owner-less session fell back to whichever single owner had configured the operation;
* hours were wall-clock, so a paused Threshing session kept accruing charge, and GPS
  travelled during a pause was billed as hectares covered;
* the total was float arithmetic with `round()` (half-to-even), so a printed bill could
  disagree with its own stated multiplication.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.billing_units import (
    CHARGE_UNIT_PER_HA,
    CHARGE_UNIT_PER_HOUR,
    charge_unit_for,
)
from app.database import Base
from app.models.iot_reading import IoTReading
from app.models.operation_charge import OperationCharge
from app.models.session import OperationSession, SessionPause, WageRecord
from app.services.field_area_service import (
    compute_total_path_distance_m,
    finalize_session_area,
    parse_gps_points,
    worked_gps_points,
)
from app.services.operation_cost_service import (
    RateNotConfigured,
    billable_hours,
    close_open_pause,
    finalize_cancelled_session,
    finalize_session_billing,
    lock_session_rate,
    resolve_operation_charge,
)

T0 = datetime(2026, 5, 1, 6, 0, 0, tzinfo=timezone.utc)


# --- fixtures -----------------------------------------------------------------


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def make_charge(db, owner_id, operation_type, *, per_ha=None, per_hour=None) -> OperationCharge:
    row = OperationCharge(
        id=uuid.uuid4(),
        owner_id=owner_id,
        operation_type=operation_type,
        charge_per_ha=Decimal(str(per_ha)) if per_ha is not None else Decimal("0"),
        charge_per_hour=Decimal(str(per_hour)) if per_hour is not None else None,
        currency="INR",
    )
    db.add(row)
    db.commit()
    return row


def make_session(
    db,
    *,
    operation_type="Tillage",
    started_at=T0,
    ended_at=None,
    status="active",
    implement_width_m=2.5,
    area_ha=None,
) -> OperationSession:
    row = OperationSession(
        id=uuid.uuid4(),
        tractor_id=uuid.uuid4(),
        operator_id=uuid.uuid4(),
        operation_type=operation_type,
        gps_tracking_enabled=True,
        status=status,
        started_at=started_at,
        ended_at=ended_at,
        implement_width_m=implement_width_m,
        area_ha=area_ha,
    )
    db.add(row)
    db.commit()
    return row


def add_pause(db, session, *, start_offset_min, end_offset_min=None) -> SessionPause:
    row = SessionPause(
        id=uuid.uuid4(),
        session_id=session.id,
        paused_at=session.started_at + timedelta(minutes=start_offset_min),
        resumed_at=(
            session.started_at + timedelta(minutes=end_offset_min)
            if end_offset_min is not None
            else None
        ),
    )
    db.add(row)
    db.commit()
    return row


def add_gps(db, session, lat, lon, *, offset_min) -> IoTReading:
    row = IoTReading(
        id=uuid.uuid4(),
        device_id="default",
        feed_key="position_tracking",
        raw_value=f'{{"lat": {lat}, "lon": {lon}}}',
        numeric_value=None,
        unit="",
        latitude=lat,
        longitude=lon,
        device_timestamp=session.started_at + timedelta(minutes=offset_min),
        adafruit_id=f"gps-{uuid.uuid4()}",
        session_id=session.id,
    )
    db.add(row)
    db.commit()
    return row


# --- the per-hour rule --------------------------------------------------------


@pytest.mark.parametrize(
    "operation_type,expected",
    [
        ("Tillage", CHARGE_UNIT_PER_HA),
        ("Sowing", CHARGE_UNIT_PER_HA),
        ("Spraying", CHARGE_UNIT_PER_HA),
        ("Weeding", CHARGE_UNIT_PER_HA),
        ("Harvesting", CHARGE_UNIT_PER_HA),
        ("Threshing", CHARGE_UNIT_PER_HOUR),
        ("Grading", CHARGE_UNIT_PER_HOUR),
    ],
)
def test_every_operation_maps_to_its_documented_unit(operation_type, expected):
    assert charge_unit_for(operation_type) == expected


@pytest.mark.parametrize("spelling", ["Threshing", "threshing", "  THRESHING  ", "ThReShInG"])
def test_the_unit_rule_is_case_and_whitespace_insensitive(spelling):
    """The mobile app's owner-config screen used to match Title-Case exactly.

    A `'threshing'` row would have shown the owner a per-hectare rate editor while the
    backend billed it per hour.
    """
    assert charge_unit_for(spelling) == CHARGE_UNIT_PER_HOUR


# --- rate locking -------------------------------------------------------------


def test_lock_snapshots_the_owners_rate_unit_and_provenance(db):
    owner_id = uuid.uuid4()
    charge = make_charge(db, owner_id, "Tillage", per_ha=1200)
    session = make_session(db)

    lock_session_rate(session, owner_id=owner_id, db=db)

    assert session.charge_unit == CHARGE_UNIT_PER_HA
    assert session.charge_per_ha_applied == Decimal("1200")
    assert session.rate_currency == "INR"
    assert session.operation_charge_id == charge.id


def test_lock_picks_the_hourly_rate_for_threshing(db):
    owner_id = uuid.uuid4()
    make_charge(db, owner_id, "Threshing", per_ha=0, per_hour=450)
    session = make_session(db, operation_type="Threshing")

    lock_session_rate(session, owner_id=owner_id, db=db)

    assert session.charge_unit == CHARGE_UNIT_PER_HOUR
    assert session.charge_per_ha_applied == Decimal("450")


def test_editing_the_rate_after_start_does_not_change_the_charge(db):
    """The whole point of locking at start."""
    owner_id = uuid.uuid4()
    charge = make_charge(db, owner_id, "Tillage", per_ha=1200)
    session = make_session(db, area_ha=2.0)
    lock_session_rate(session, owner_id=owner_id, db=db)
    db.commit()

    charge.charge_per_ha = Decimal("1800")
    db.commit()

    session.ended_at = T0 + timedelta(hours=1)
    session.status = "completed"
    finalize_session_billing(session, db)
    db.commit()

    assert session.total_cost_inr == Decimal("2400.00")  # 1200 x 2.0, not 1800 x 2.0


def test_editing_the_rate_after_the_charge_is_final_cannot_re_bill_it(db):
    """`finalize_session_billing` is a no-op once `cost_finalized_at` is set.

    The summary endpoint used to re-resolve billing on every request and overwrite the
    stored total whenever it differed from the owner's *current* rate.
    """
    owner_id = uuid.uuid4()
    charge = make_charge(db, owner_id, "Tillage", per_ha=1000)
    session = make_session(db, area_ha=3.0, ended_at=T0 + timedelta(hours=2), status="completed")
    lock_session_rate(session, owner_id=owner_id, db=db)
    finalize_session_billing(session, db)
    db.commit()

    settled = session.total_cost_inr
    finalized_at = session.cost_finalized_at
    assert settled == Decimal("3000.00")

    charge.charge_per_ha = Decimal("9999")
    session.charge_per_ha_applied = Decimal("9999")  # even a corrupted snapshot
    db.commit()

    for _ in range(3):
        finalize_session_billing(session, db)
        db.commit()

    assert session.total_cost_inr == settled
    assert session.cost_finalized_at == finalized_at


def test_an_ownerless_session_is_refused_not_billed_at_someone_elses_rate(db):
    """The removed fallback: "if exactly one owner configured this operation, use theirs"."""
    other_owner = uuid.uuid4()
    make_charge(db, other_owner, "Tillage", per_ha=1200)
    session = make_session(db)

    with pytest.raises(RateNotConfigured):
        lock_session_rate(session, owner_id=None, db=db)

    assert session.charge_per_ha_applied is None
    assert session.charge_unit is None


def test_one_owners_rate_is_never_used_for_another_owner(db):
    owner_a, owner_b = uuid.uuid4(), uuid.uuid4()
    make_charge(db, owner_b, "Tillage", per_ha=1200)
    session = make_session(db)

    assert resolve_operation_charge(db=db, owner_id=owner_a, operation_type="Tillage") is None
    with pytest.raises(RateNotConfigured):
        lock_session_rate(session, owner_id=owner_a, db=db)


def test_missing_rate_card_is_refused(db):
    session = make_session(db)
    with pytest.raises(RateNotConfigured) as exc:
        lock_session_rate(session, owner_id=uuid.uuid4(), db=db)
    assert "Tillage" in exc.value.reason


def test_threshing_without_an_hourly_rate_is_refused(db):
    """A per-hectare-only rate card cannot price a per-hour operation."""
    owner_id = uuid.uuid4()
    make_charge(db, owner_id, "Threshing", per_ha=900, per_hour=None)
    session = make_session(db, operation_type="Threshing")

    with pytest.raises(RateNotConfigured) as exc:
        lock_session_rate(session, owner_id=owner_id, db=db)
    assert "per hour" in exc.value.reason


# --- billable hours -----------------------------------------------------------


def test_billable_hours_is_wall_clock_when_never_paused(db):
    session = make_session(db, ended_at=T0 + timedelta(hours=3))
    assert billable_hours(session, db) == Decimal(3)


def test_billable_hours_excludes_paused_intervals(db):
    session = make_session(db, ended_at=T0 + timedelta(hours=4))
    add_pause(db, session, start_offset_min=60, end_offset_min=90)  # 30 min
    add_pause(db, session, start_offset_min=150, end_offset_min=180)  # 30 min

    assert billable_hours(session, db) == Decimal(3)  # 4h wall clock - 1h paused


def test_stopping_while_paused_closes_the_interval_and_does_not_bill_it(db):
    session = make_session(db, status="paused")
    add_pause(db, session, start_offset_min=60, end_offset_min=None)

    ended_at = T0 + timedelta(hours=2)
    close_open_pause(session, db, at=ended_at)
    session.ended_at = ended_at
    session.status = "completed"
    db.commit()

    open_rows = [p for p in db.query(SessionPause).all() if p.resumed_at is None]
    assert open_rows == []
    assert billable_hours(session, db) == Decimal(1)  # 2h wall clock - 1h trailing pause


def test_billable_hours_is_none_before_the_session_ends(db):
    session = make_session(db)
    assert billable_hours(session, db) is None


def test_billable_hours_never_goes_negative(db):
    session = make_session(db, ended_at=T0 + timedelta(hours=1))
    add_pause(db, session, start_offset_min=0, end_offset_min=600)  # absurd, longer than the session
    assert billable_hours(session, db) == Decimal(0)


# --- worked area --------------------------------------------------------------


def test_gps_recorded_while_paused_is_excluded_from_the_worked_area(db):
    """A machine driven elsewhere during a pause must not be billed for that transit.

    The points stay stored and still reach the map -- only the billed path drops them.
    """
    session = make_session(db, ended_at=T0 + timedelta(hours=2), implement_width_m=2.0)
    # Worked leg: ~111 m
    add_gps(db, session, 20.0000, 75.0000, offset_min=1)
    add_gps(db, session, 20.0010, 75.0000, offset_min=2)
    # Transit during the pause. Steps stay under MAX_REASONABLE_GPS_STEP_M so the noise
    # gate cannot be what excludes them -- the pause window has to be.
    add_pause(db, session, start_offset_min=10, end_offset_min=20)
    add_gps(db, session, 20.0030, 75.0000, offset_min=12)
    add_gps(db, session, 20.0060, 75.0000, offset_min=18)

    all_points = parse_gps_points(session.id, db)
    billed_points = worked_gps_points(session.id, db)

    assert len(all_points) == 4  # map trail keeps everything
    assert len(billed_points) == 2  # billing sees only the worked leg

    worked_distance = compute_total_path_distance_m(billed_points)
    full_distance = compute_total_path_distance_m(all_points)
    assert worked_distance < full_distance

    finalize_session_area(session.id, db)
    db.commit()
    # area == worked path length x implement width / 10000, to the stored 4dp
    assert session.area_ha == pytest.approx(worked_distance * 2.0 / 10000.0, abs=1e-4)


def test_area_reconciles_with_the_reported_worked_distance(db):
    """distance_m x width_m / 10000 == area_ha is what makes the bill checkable by hand."""
    session = make_session(db, ended_at=T0 + timedelta(hours=1), implement_width_m=3.0)
    add_gps(db, session, 20.0000, 75.0000, offset_min=1)
    add_gps(db, session, 20.0020, 75.0000, offset_min=2)
    add_gps(db, session, 20.0040, 75.0000, offset_min=3)

    points = worked_gps_points(session.id, db)
    distance_m = compute_total_path_distance_m(points)
    finalize_session_area(session.id, db)
    db.commit()

    assert session.area_ha == pytest.approx(distance_m * 3.0 / 10000.0, abs=1e-4)


def test_a_session_with_no_gps_finalizes_at_zero_with_an_audit_note(db):
    owner_id = uuid.uuid4()
    make_charge(db, owner_id, "Tillage", per_ha=1200)
    session = make_session(db, ended_at=T0 + timedelta(hours=1), status="completed")
    lock_session_rate(session, owner_id=owner_id, db=db)

    finalize_session_area(session.id, db)
    db.refresh(session)
    finalize_session_billing(session, db)
    db.commit()

    assert session.area_ha == 0.0
    assert session.total_cost_inr == Decimal("0.00")
    assert session.cost_finalized_at is not None
    assert "no measured work recorded" in session.cost_note


# --- the charge ---------------------------------------------------------------


def test_per_hectare_charge_is_rate_times_measured_area(db):
    owner_id = uuid.uuid4()
    make_charge(db, owner_id, "Tillage", per_ha=1250)
    session = make_session(db, area_ha=3.2468, ended_at=T0 + timedelta(hours=5), status="completed")
    lock_session_rate(session, owner_id=owner_id, db=db)

    finalize_session_billing(session, db)
    db.commit()

    expected = (Decimal("1250") * Decimal("3.2468")).quantize(Decimal("0.01"))
    assert session.total_cost_inr == expected == Decimal("4058.50")
    assert "Rs 1250/ha x 3.2468 ha = Rs 4058.50" in session.cost_note


def test_per_hour_charge_is_rate_times_worked_hours(db):
    owner_id = uuid.uuid4()
    make_charge(db, owner_id, "Threshing", per_ha=0, per_hour=450)
    session = make_session(
        db, operation_type="Threshing", ended_at=T0 + timedelta(hours=4), status="completed"
    )
    add_pause(db, session, start_offset_min=60, end_offset_min=120)  # 1h paused
    lock_session_rate(session, owner_id=owner_id, db=db)

    finalize_session_billing(session, db)
    db.commit()

    assert session.billable_hours == pytest.approx(3.0)
    assert session.total_cost_inr == Decimal("1350.00")  # 450 x 3, not 450 x 4


def test_a_per_hour_session_is_not_billed_on_area(db):
    """Threshing over a large GPS trail still bills only on time."""
    owner_id = uuid.uuid4()
    make_charge(db, owner_id, "Grading", per_ha=0, per_hour=200)
    session = make_session(
        db, operation_type="Grading", ended_at=T0 + timedelta(hours=2), status="completed"
    )
    add_gps(db, session, 20.0000, 75.0000, offset_min=1)
    add_gps(db, session, 20.0040, 75.0000, offset_min=30)
    lock_session_rate(session, owner_id=owner_id, db=db)

    finalize_session_area(session.id, db)
    db.refresh(session)
    finalize_session_billing(session, db)
    db.commit()

    assert session.area_ha > 0  # area is still recorded
    assert session.total_cost_inr == Decimal("400.00")  # but the charge is 200 x 2h


def test_money_rounds_half_up_not_half_to_even(db):
    """`round()` is half-to-even: round(2.675, 2) -> 2.67, contradicting the printed note."""
    owner_id = uuid.uuid4()
    make_charge(db, owner_id, "Tillage", per_ha=Decimal("2.5"))
    session = make_session(db, area_ha=1.07, ended_at=T0 + timedelta(hours=1), status="completed")
    lock_session_rate(session, owner_id=owner_id, db=db)

    finalize_session_billing(session, db)
    db.commit()

    # 2.5 x 1.07 = 2.675 exactly -> 2.68 half-up (banker's rounding would give 2.67)
    assert session.total_cost_inr == Decimal("2.68")


def test_hours_are_not_pre_rounded_before_multiplying(db):
    """Rounding hours to 4dp first made the note unreproducible from the timestamps."""
    owner_id = uuid.uuid4()
    make_charge(db, owner_id, "Threshing", per_ha=0, per_hour=Decimal("1000"))
    # 1 second short of an hour: 3599/3600 h
    session = make_session(
        db, operation_type="Threshing", ended_at=T0 + timedelta(seconds=3599), status="completed"
    )
    lock_session_rate(session, owner_id=owner_id, db=db)

    finalize_session_billing(session, db)
    db.commit()

    expected = (Decimal("1000") * (Decimal(3599) / Decimal(3600))).quantize(Decimal("0.01"))
    assert session.total_cost_inr == expected == Decimal("999.72")


def test_the_cost_note_reproduces_its_own_arithmetic(db):
    """A farmer must be able to check rate x quantity = total from the summary alone."""
    owner_id = uuid.uuid4()
    make_charge(db, owner_id, "Sowing", per_ha=Decimal("875.50"))
    session = make_session(
        db,
        operation_type="Sowing",
        area_ha=4.125,
        ended_at=T0 + timedelta(hours=6),
        status="completed",
    )
    lock_session_rate(session, owner_id=owner_id, db=db)
    finalize_session_billing(session, db)
    db.commit()

    assert session.cost_note == "Sowing: Rs 875.5/ha x 4.1250 ha = Rs 3611.44"
    assert (Decimal("875.50") * Decimal("4.1250")).quantize(Decimal("0.01")) == Decimal("3611.44")
    assert session.total_cost_inr == Decimal("3611.44")


def test_a_per_hour_cost_note_reproduces_its_own_arithmetic(db):
    """The billed hours and the printed hours must be the same number.

    Elapsed-seconds/3600 is non-terminating, so if the note prints a differently-rounded
    figure from the one that was multiplied, the note states a multiplication that does
    not produce its own total. With Rs 487.25/hr over 4h 0m 17s minus a 1h pause, printing
    4 decimal places gave Rs 1951.29 against a stored Rs 1951.30.
    """
    owner_id = uuid.uuid4()
    make_charge(db, owner_id, "Threshing", per_ha=0, per_hour=Decimal("487.25"))
    session = make_session(
        db,
        operation_type="Threshing",
        ended_at=T0 + timedelta(hours=5, seconds=17),
        status="completed",
    )
    add_pause(db, session, start_offset_min=45, end_offset_min=105)  # 1h
    lock_session_rate(session, owner_id=owner_id, db=db)
    finalize_session_billing(session, db)
    db.commit()

    # Pull the two numbers back out of the note and re-do the multiplication.
    printed = session.cost_note.split(" x ")[1].split(" hr")[0]
    assert Decimal(printed) == Decimal(str(session.billable_hours))
    assert (Decimal("487.25") * Decimal(printed)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    ) == session.total_cost_inr


def test_the_billed_hours_are_the_persisted_hours(db):
    """`billable_hours` is what was charged, not a separate display figure."""
    owner_id = uuid.uuid4()
    make_charge(db, owner_id, "Grading", per_ha=0, per_hour=Decimal("333.33"))
    session = make_session(
        db,
        operation_type="Grading",
        ended_at=T0 + timedelta(hours=2, minutes=37, seconds=41),
        status="completed",
    )
    lock_session_rate(session, owner_id=owner_id, db=db)
    finalize_session_billing(session, db)
    db.commit()

    assert (Decimal("333.33") * Decimal(str(session.billable_hours))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    ) == session.total_cost_inr


# --- cancellation -------------------------------------------------------------


def test_a_cancelled_session_is_finalized_at_zero(db):
    owner_id = uuid.uuid4()
    make_charge(db, owner_id, "Tillage", per_ha=1200)
    session = make_session(db, area_ha=5.0, ended_at=T0 + timedelta(hours=1), status="aborted")
    lock_session_rate(session, owner_id=owner_id, db=db)

    finalize_cancelled_session(session, db)
    db.commit()

    assert session.total_cost_inr == Decimal("0.00")
    assert session.cost_note == "Session cancelled - no charge"
    assert session.cost_finalized_at is not None


def test_a_cancelled_session_cannot_be_re_priced_afterwards(db):
    owner_id = uuid.uuid4()
    make_charge(db, owner_id, "Tillage", per_ha=1200)
    session = make_session(db, area_ha=5.0, ended_at=T0 + timedelta(hours=1), status="aborted")
    lock_session_rate(session, owner_id=owner_id, db=db)
    finalize_cancelled_session(session, db)
    db.commit()

    finalize_session_billing(session, db)  # would otherwise be 6000.00
    db.commit()

    assert session.total_cost_inr == Decimal("0.00")


# --- report aggregation -------------------------------------------------------


def test_charges_and_wages_are_reported_as_two_distinct_totals(db):
    """They were the same expression under both keys, so one number was shown twice."""
    from app.services.report_service import ReportFilters, generate_report

    owner_id = uuid.uuid4()
    make_charge(db, owner_id, "Tillage", per_ha=1000)
    session = make_session(db, area_ha=2.0, ended_at=T0 + timedelta(hours=1), status="completed")
    session.tractor_owner_id = owner_id
    lock_session_rate(session, owner_id=owner_id, db=db)
    finalize_session_billing(session, db)
    db.add(
        WageRecord(
            id=uuid.uuid4(),
            session_id=session.id,
            operator_id=session.operator_id,
            rate_type="per_ha",
            rate_amount=150.0,
            area_ha=2.0,
            total_amount=300.0,
        )
    )
    db.commit()

    report = generate_report(ReportFilters(tractor_id=session.tractor_id), db)

    assert report["total_operation_charges"] == pytest.approx(2000.0)
    assert report["total_wages_paid"] == pytest.approx(300.0)
    assert report["total_operation_charges"] != report["total_wages_paid"]


def test_a_cancelled_session_does_not_add_to_owner_charge_totals(db):
    from app.services.report_service import ReportFilters, generate_report

    owner_id = uuid.uuid4()
    make_charge(db, owner_id, "Tillage", per_ha=1000)
    tractor_id = uuid.uuid4()

    billed = make_session(db, area_ha=2.0, ended_at=T0 + timedelta(hours=1), status="completed")
    billed.tractor_id = tractor_id
    lock_session_rate(billed, owner_id=owner_id, db=db)
    finalize_session_billing(billed, db)

    cancelled = make_session(db, area_ha=9.0, ended_at=T0 + timedelta(hours=1), status="aborted")
    cancelled.tractor_id = tractor_id
    lock_session_rate(cancelled, owner_id=owner_id, db=db)
    finalize_cancelled_session(cancelled, db)
    db.commit()

    report = generate_report(ReportFilters(tractor_id=tractor_id), db)
    assert report["total_operation_charges"] == pytest.approx(2000.0)


def test_generating_a_report_does_not_re_price_settled_sessions(db):
    from app.services.report_service import ReportFilters, generate_report

    owner_id = uuid.uuid4()
    charge = make_charge(db, owner_id, "Tillage", per_ha=1000)
    session = make_session(db, area_ha=2.0, ended_at=T0 + timedelta(hours=1), status="completed")
    lock_session_rate(session, owner_id=owner_id, db=db)
    finalize_session_billing(session, db)
    db.commit()

    charge.charge_per_ha = Decimal("5000")
    db.commit()

    generate_report(ReportFilters(tractor_id=session.tractor_id), db)
    db.refresh(session)

    assert session.total_cost_inr == Decimal("2000.00")
