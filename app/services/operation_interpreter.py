"""
Tractor Operation Interpretation Engine
========================================
Pure, stateless module — no database access.

Given the three most recent IoT signal readings, this module applies the
6-row decision matrix to determine the tractor's current operational state.

Decision Matrix:
  (gps_changed, pto_rotating, vibrating) → state

  (Yes, Yes, Yes) → pto_field_work
  (Yes, No,  Yes) → nonpto_field_work
  (No,  Yes, Yes) → stationary_pto
  (No,  No,  Yes) → engine_on_idle
  (No,  No,  No)  → engine_off       (refined by machine_status)
  (Yes, No,  No)  → transit

Thresholds are configurable module-level constants for future calibration.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Configurable thresholds — adjust during field calibration without code change
# ---------------------------------------------------------------------------

#: Minimum RPM to consider PTO as actively rotating (default: 50 RPM).
#: Idle/noise readings typically remain below this value.
PTO_ACTIVE_RPM_MIN: float = 50.0

#: Minimum g-force to consider implement vibration as active (default: 0.5 g).
#: Engine background vibration is typically below this value at rest.
VIBRATION_ACTIVE_G: float = 0.5

#: Minimum distance in metres between two consecutive GPS readings to consider
#: the tractor as having moved (default: 3.0 m).
#: Accounts for typical GPS accuracy of ±2–4 m.
GPS_MOVEMENT_THRESHOLD_M: float = 3.0

#: Maximum age of the most recent GPS reading (seconds) before it is considered
#: stale and GPS change detection is skipped (default: 120 s = 2 minutes).
GPS_STALE_SECONDS: float = 120.0


# ---------------------------------------------------------------------------
# State definitions
# ---------------------------------------------------------------------------

#: Maps state_key → human-readable label
STATE_LABELS: dict[str, str] = {
    "pto_field_work":    "Tractor is working in field with PTO powered implement",
    "nonpto_field_work": "Tractor is working in field with implement (non-powered)",
    "stationary_pto":    "Tractor is powering stationary PTO powered implement",
    "engine_on_idle":    "Tractor power is ON in stationary condition but not powering any implement",
    "engine_off":        "Tractor is standing at one position with engine power off",
    "transit":           "Tractor is moving without any implement",
    "unavailable":       "Operational state unavailable — insufficient signal data",
}

#: Maps state_key → badge colour (hex) for UI rendering
STATE_COLORS: dict[str, str] = {
    "pto_field_work":    "#16A34A",  # green
    "nonpto_field_work": "#15803D",  # dark green
    "stationary_pto":    "#CA8A04",  # amber
    "engine_on_idle":    "#D97706",  # orange-amber
    "engine_off":        "#6B7280",  # grey
    "transit":           "#2563EB",  # blue
    "unavailable":       "#9CA3AF",  # light grey
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InterpretationResult:
    """The computed tractor operational state and the signal booleans used to derive it."""

    state_key: str
    label: str
    color: str

    #: True if GPS position changed between the two most recent readings.
    gps_changed: Optional[bool]
    #: True if PTO shaft speed exceeds PTO_ACTIVE_RPM_MIN.
    pto_rotating: Optional[bool]
    #: True if vibration exceeds VIBRATION_ACTIVE_G.
    vibrating: Optional[bool]

    #: True if all three signals had data available for interpretation.
    signals_available: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _reading_age_seconds(device_timestamp: Optional[datetime]) -> float:
    """Return how many seconds old a reading is.  Returns infinity if no timestamp."""
    if device_timestamp is None:
        return float("inf")
    ts = device_timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (_now_utc() - ts).total_seconds()


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine great-circle distance in metres.

    Imports field_area_service at call time to avoid circular imports at module
    load.  The implementation there is authoritative; we never duplicate it.
    """
    from app.services.field_area_service import haversine_distance_m
    return haversine_distance_m(lat1, lon1, lat2, lon2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def interpret(
    *,
    gps_readings: list,           # list[IoTReading] — up to 2, newest first
    pto_reading,                   # IoTReading | None
    vibration_reading,             # IoTReading | None
    machine_reading,               # IoTReading | None
) -> InterpretationResult:
    """Apply the 6-row decision matrix and return an InterpretationResult.

    Parameters
    ----------
    gps_readings:
        The two most-recent ``position_tracking`` IoTReading rows, newest first.
        Provided by ``get_two_latest_gps()`` from ``iot_query``.
    pto_reading:
        The latest ``pto_shaft_speed`` IoTReading row, or None.
    vibration_reading:
        The latest ``vibration`` IoTReading row, or None.
    machine_reading:
        The latest ``machine_status`` IoTReading row, or None.
    """

    # ------------------------------------------------------------------
    # 1. GPS change detection
    # ------------------------------------------------------------------
    gps_changed: Optional[bool] = None

    if len(gps_readings) >= 1:
        newest_gps = gps_readings[0]
        age = _reading_age_seconds(newest_gps.device_timestamp)
        if age > GPS_STALE_SECONDS:
            # Most recent GPS reading is too old — treat as unavailable
            gps_changed = None
        elif len(gps_readings) >= 2:
            older_gps = gps_readings[1]
            lat1 = getattr(older_gps, "latitude", None)
            lon1 = getattr(older_gps, "longitude", None)
            lat2 = getattr(newest_gps, "latitude", None)
            lon2 = getattr(newest_gps, "longitude", None)

            if None not in (lat1, lon1, lat2, lon2):
                try:
                    dist = _haversine_m(float(lat1), float(lon1), float(lat2), float(lon2))
                    gps_changed = dist > GPS_MOVEMENT_THRESHOLD_M
                except Exception:
                    gps_changed = None
            else:
                # lat/lon columns null — raw_value may have the data but we skip
                # further parsing here to keep this module stateless/simple.
                gps_changed = None
        else:
            # Only one GPS reading — can't compute movement yet
            gps_changed = False  # conservative: assume stationary on first reading

    # ------------------------------------------------------------------
    # 2. PTO rotation detection
    # ------------------------------------------------------------------
    pto_rotating: Optional[bool] = None
    if pto_reading is not None:
        val = pto_reading.numeric_value
        if val is not None:
            pto_rotating = float(val) > PTO_ACTIVE_RPM_MIN

    # ------------------------------------------------------------------
    # 3. Vibration detection
    # ------------------------------------------------------------------
    vibrating: Optional[bool] = None
    if vibration_reading is not None:
        val = vibration_reading.numeric_value
        if val is not None:
            vibrating = float(val) > VIBRATION_ACTIVE_G

    # ------------------------------------------------------------------
    # 4. Machine status (used only for engine_off disambiguation)
    # ------------------------------------------------------------------
    machine_on: bool = False
    if machine_reading is not None:
        raw = (machine_reading.raw_value or "").upper().strip()
        machine_on = raw == "RUNNING"

    # ------------------------------------------------------------------
    # 5. Check if we have enough signals to interpret
    # ------------------------------------------------------------------
    signals_available = not (
        gps_changed is None and pto_rotating is None and vibrating is None
    )

    if not signals_available:
        return InterpretationResult(
            state_key="unavailable",
            label=STATE_LABELS["unavailable"],
            color=STATE_COLORS["unavailable"],
            gps_changed=None,
            pto_rotating=None,
            vibrating=None,
            signals_available=False,
        )

    # Apply conservative defaults for missing signals
    g = gps_changed  if gps_changed  is not None else False
    p = pto_rotating if pto_rotating is not None else False
    v = vibrating    if vibrating    is not None else False

    # ------------------------------------------------------------------
    # 6. Decision matrix
    # ------------------------------------------------------------------
    if g and p and v:
        key = "pto_field_work"
    elif g and not p and v:
        key = "nonpto_field_work"
    elif not g and p and v:
        key = "stationary_pto"
    elif not g and not p and v:
        key = "engine_on_idle"
    elif not g and not p and not v:
        # Disambiguate: machine_status can tell us if engine is truly off
        # When machine_status = RUNNING but all motion signals are zero,
        # the tractor is idling — mark as engine_on_idle
        key = "engine_on_idle" if machine_on else "engine_off"
    else:
        # (g=True, p=False, v=False) → moving without implement
        key = "transit"

    return InterpretationResult(
        state_key=key,
        label=STATE_LABELS[key],
        color=STATE_COLORS[key],
        gps_changed=g,
        pto_rotating=p,
        vibrating=v,
        signals_available=True,
    )
