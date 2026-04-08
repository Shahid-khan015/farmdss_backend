from __future__ import annotations

import logging
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.models.iot_reading import IoTReading
from app.models.session import IoTAlert, SessionPresetValue

logger = logging.getLogger(__name__)

# Canonical feed_key → session_preset_values.parameter_name (owner/implement presets on session)
FEED_TO_PARAMETER = {
    "forward_speed": "forward_speed",
    "depth_of_operation": "operation_depth",
    "pto_shaft_speed": "pto_shaft_speed",
    "gearbox_temperature": "gearbox_temperature",
    "wheel_slip": "wheel_slip",
    "soil_moisture": "soil_moisture",
    "field_capacity": "field_capacity",
    "vibration": "vibration_level",
}


def get_status_label(feed_key: str, numeric_value: Optional[float]) -> str:
    if numeric_value is None:
        return "normal"

    if feed_key == "gearbox_temperature":
        if numeric_value >= 100:
            return "critical"
        if numeric_value >= 85:
            return "warning"
        return "normal"

    if feed_key == "vibration":
        if numeric_value >= 5.0:
            return "critical"
        if numeric_value >= 3.0:
            return "warning"
        return "normal"

    if feed_key == "wheel_slip":
        if numeric_value >= 25:
            return "critical"
        if numeric_value >= 15:
            return "warning"
        return "normal"

    if feed_key == "soil_moisture":
        if numeric_value <= 10:
            return "critical"
        if numeric_value <= 20:
            return "warning"
        return "normal"

    if feed_key == "forward_speed":
        if numeric_value >= 15:
            return "critical"
        if numeric_value >= 12:
            return "warning"
        return "normal"

    if feed_key == "depth_of_operation":
        if numeric_value <= 6 or numeric_value >= 30:
            return "critical"
        if numeric_value <= 8 or numeric_value >= 25:
            return "warning"
        return "normal"

    if feed_key == "pto_shaft_speed":
        return "normal"

    return "normal"


def _preset_deviation_pct(
    actual: float, target: float, parameter_name: str
) -> Tuple[Optional[float], bool]:
    """
    Return (deviation_pct_for_severity, should_evaluate).
    For gearbox_temperature, session preset is owner's MAX °C — only evaluate when actual > target.
    Other parameters: symmetric % deviation from owner preset target.
    """
    if target == 0:
        return None, False
    if parameter_name == "gearbox_temperature":
        if actual <= target:
            return None, False
        return (actual - target) / abs(target) * 100.0, True
    return abs(actual - target) / abs(target) * 100.0, True


def _human_param_label(parameter_name: str) -> str:
    return parameter_name.replace("_", " ").title()


def _evaluate_threshold(reading: IoTReading, db: Session) -> None:
    status_label = get_status_label(reading.feed_key, reading.numeric_value)
    if status_label not in ("warning", "critical"):
        return

    threshold_ref: Optional[float] = None
    if reading.feed_key == "gearbox_temperature":
        threshold_ref = 100.0 if status_label == "critical" else 85.0
        label = "Gearbox temperature"
        unit = "°C"
    elif reading.feed_key == "vibration":
        threshold_ref = 5.0 if status_label == "critical" else 3.0
        label = "Vibration level"
        unit = "g"
    elif reading.feed_key == "wheel_slip":
        threshold_ref = 25.0 if status_label == "critical" else 15.0
        label = "Wheel slip"
        unit = "%"
    elif reading.feed_key == "soil_moisture":
        threshold_ref = 10.0 if status_label == "critical" else 20.0
        label = "Soil moisture"
        unit = "%"
    elif reading.feed_key == "forward_speed":
        threshold_ref = 15.0 if status_label == "critical" else 12.0
        label = "Forward speed"
        unit = "km/h"
    elif reading.feed_key == "depth_of_operation":
        if status_label == "critical":
            threshold_ref = 6.0 if (reading.numeric_value or 0) <= 6.0 else 30.0
        else:
            threshold_ref = 8.0 if (reading.numeric_value or 0) <= 8.0 else 25.0
        label = "Depth of operation"
        unit = "cm"
    else:
        threshold_ref = None
        label = reading.feed_key
        unit = ""

    existing_threshold = db.query(IoTAlert).filter(
        IoTAlert.session_id == reading.session_id,
        IoTAlert.feed_key == reading.feed_key,
        IoTAlert.alert_type == "threshold",
        IoTAlert.acknowledged == False,  # noqa: E712
    ).first()
    if existing_threshold is None:
        severity_text = "critically high" if status_label == "critical" else "warning"
        if reading.feed_key == "soil_moisture":
            severity_text = "critically low" if status_label == "critical" else "warning"
        value_text = f"{reading.numeric_value:g}" if reading.numeric_value is not None else "n/a"
        threshold_text = f"{threshold_ref:g}" if threshold_ref is not None else "n/a"
        msg = f"{label} {severity_text}: {value_text}{unit} (threshold: {threshold_text}{unit})"
        db.add(
            IoTAlert(
                session_id=reading.session_id,
                reading_id=reading.id,
                feed_key=reading.feed_key,
                alert_type="threshold",
                alert_status=status_label,
                actual_value=reading.numeric_value,
                reference_value=threshold_ref,
                message=msg,
            )
        )
        db.flush()


def _evaluate_preset_deviation(reading: IoTReading, db: Session, preset: SessionPresetValue) -> None:
    if reading.numeric_value is None:
        return
    if preset.required_value is None:
        return

    parameter_name = preset.parameter_name
    actual = float(reading.numeric_value)
    target = float(preset.required_value)

    deviation_pct, should_eval = _preset_deviation_pct(actual, target, parameter_name)
    if not should_eval or deviation_pct is None:
        return

    if deviation_pct >= float(preset.deviation_pct_crit):
        deviation_status = "critical"
    elif deviation_pct >= float(preset.deviation_pct_warn):
        deviation_status = "warning"
    else:
        return

    label = _human_param_label(parameter_name)
    unit = preset.unit or ""

    if parameter_name == "gearbox_temperature":
        msg = (
            f"{label} exceeds owner preset max by {deviation_pct:.0f}%: "
            f"{actual:g}{unit} (max allowed: {target:g}{unit})"
        )
    else:
        direction = "above" if actual >= target else "below"
        msg = (
            f"{label} deviates {deviation_pct:.0f}% {direction} owner preset: "
            f"{actual:g}{unit} (preset: {target:g}{unit})"
        )

    existing = db.query(IoTAlert).filter(
        IoTAlert.session_id == reading.session_id,
        IoTAlert.feed_key == reading.feed_key,
        IoTAlert.alert_type == "deviation",
        IoTAlert.acknowledged == False,  # noqa: E712
    ).first()

    if existing is not None:
        existing.actual_value = actual
        existing.reference_value = target
        existing.alert_status = deviation_status
        existing.message = msg
        existing.reading_id = reading.id
        db.flush()
        return

    db.add(
        IoTAlert(
            session_id=reading.session_id,
            reading_id=reading.id,
            feed_key=reading.feed_key,
            alert_type="deviation",
            alert_status=deviation_status,
            actual_value=actual,
            reference_value=target,
            message=msg,
        )
    )
    db.flush()


def evaluate(reading: IoTReading, db: Session) -> None:
    try:
        if reading.session_id is None:
            return

        parameter_name = FEED_TO_PARAMETER.get(reading.feed_key)
        owner_preset: Optional[SessionPresetValue] = None
        if parameter_name is not None:
            owner_preset = (
                db.query(SessionPresetValue)
                .filter(
                    SessionPresetValue.session_id == reading.session_id,
                    SessionPresetValue.parameter_name == parameter_name,
                )
                .first()
            )

        has_owner_target = (
            owner_preset is not None
            and owner_preset.required_value is not None
            and float(owner_preset.required_value) != 0.0
        )

        # Owner/implement presets on the session take precedence over generic thresholds
        if has_owner_target:
            _evaluate_preset_deviation(reading, db, owner_preset)
        else:
            _evaluate_threshold(reading, db)

    except Exception as exc:  # pragma: no cover - defensive pipeline guard
        logger.exception(
            "alert evaluation failed for iot_reading_id=%s: %s",
            getattr(reading, "id", None),
            exc,
        )
