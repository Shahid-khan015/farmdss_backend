# Follows pattern from: app/main.py (background thread), app/config.py, app/database.py (SessionLocal)
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict

import paho.mqtt.client as mqtt

from app.config import settings
from app.services.ingest_buffer import BUFFER
from app.services.live_hub import HUB
from app.services.normalizer import (
    FEEDS,
    NormalizedReading,
    feed_key_from_adafruit_topic_or_slug,
    process_iot_data,
)
from app.services.session_gate import GATE

logger = logging.getLogger(__name__)

#: How long to wait for CONNACK + subscribe before giving up and retrying.
_CONNACK_TIMEOUT_SEC = 15.0


def _make_client() -> mqtt.Client:
    """paho-mqtt 2.x prefers explicit callback API version; fall back for older installs."""
    try:
        return mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="tractor_dss_iot_sub",
        )
    except AttributeError:
        return mqtt.Client(client_id="tractor_dss_iot_sub")


def _connect_failed(reason_code: Any) -> bool:
    if hasattr(reason_code, "is_failure"):
        return bool(reason_code.is_failure)
    return reason_code not in (0, "Success")


def parse_adafruit_envelope(payload_raw: str) -> Dict[str, Any]:
    """Turn a `.../json` MQTT payload into the record `process_iot_data` expects.

    Adafruit's `/json` topic carries the **whole data-point record** --
    ``{"id": ..., "value": ..., "feed_id": ..., "created_at": ..., "lat": ..., "lon": ...}``.
    An earlier implementation wrapped the entire envelope as the *value*
    (``{"value": payload_raw, "id": None}``), which broke two things at once:

    1. `_parse_numeric` then regexed the first number out of the JSON text -- usually the
       record id -- and stored it as `numeric_value`. Every scalar feed was wrong.
    2. The discarded `id` forced a synthetic ``mqtt-<uuid4>`` dedup key that could never
       match a row the HTTP poller had already stored, so running both transports
       double-stored every point.

    Passing the record through intact fixes both: the reading is the reading, and the
    real Adafruit `id` becomes the dedup key, so MQTT and the poller converge on one row.

    Falls back to the bare-value shape for the plain (non-`/json`) topic form, or for a
    payload that is not a JSON object.
    """
    try:
        parsed = json.loads(payload_raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {"value": payload_raw, "id": None}
    if isinstance(parsed, dict) and "value" in parsed:
        return parsed
    return {"value": payload_raw, "id": None}


def _handle_payload(topic: str, payload_raw: str) -> None:
    """Normalize one message, push it to watching clients, and queue it for persistence."""
    feed_key = feed_key_from_adafruit_topic_or_slug(topic)
    if not feed_key:
        logger.debug("MQTT topic not mapped to feed_key: %s", topic)
        return

    record = parse_adafruit_envelope(payload_raw)
    normalized = process_iot_data(
        feed_key, record, default_device_id=settings.IOT_DEFAULT_DEVICE_ID
    )
    if normalized is None:
        return

    # Hot path first: the socket is fed before the database, which is what makes the
    # gauge sub-second. The buffer catches up within IOT_BUFFER_MAX_AGE_SEC.
    _publish_live(normalized)
    BUFFER.add(normalized)


def _publish_live(normalized: NormalizedReading) -> None:
    """Push a normalized-but-not-yet-stored reading to whoever is watching.

    `broadcast_update` in the ingestion pipeline publishes *stored* rows post-commit and
    carries the session id. This is the pre-persistence twin: it exists so the operator's
    gauge moves immediately rather than waiting on a flush. It therefore cannot know the
    session id, so it asks the hub which session is being watched and publishes there.
    """
    from app.services.alert_engine import get_status_label

    payload = {
        "type": "reading",
        "feed_key": normalized.feed_key,
        "raw_value": normalized.raw_value,
        "numeric_value": normalized.numeric_value,
        "unit": normalized.unit,
        "device_timestamp": normalized.device_timestamp.isoformat(),
        "lat": normalized.latitude,
        "lon": normalized.longitude,
        "status_label": get_status_label(normalized.feed_key, normalized.numeric_value),
        "provisional": True,
    }
    for session_id in HUB.watched_sessions():
        HUB.publish_threadsafe(session_id, payload)


def run_mqtt_subscriber(stop: threading.Event) -> None:
    """
    Session-gated Adafruit IO MQTT subscriber.

    While no session is attachable the thread holds **no broker connection at all** and
    makes no database calls -- it parks on the gate. That is what lets an idle deployment
    stay dormant and the database compute suspend.
    """
    username = settings.AIO_USERNAME
    key = settings.AIO_KEY
    if not username or not key:
        logger.warning("IoT MQTT disabled: missing AIO_USERNAME / AIO_KEY")
        return

    broker = settings.IOT_MQTT_BROKER
    port = int(settings.IOT_MQTT_PORT)
    use_tls = bool(getattr(settings, "IOT_MQTT_TLS", True))
    recheck = float(getattr(settings, "IOT_GATE_RECHECK_SEC", 30.0))

    # Set once CONNACK has actually been received. Without waiting on this, the loop
    # below would check `is_connected()` before the handshake completed, immediately tear
    # the connection down, and reconnect forever -- never reaching on_connect, and so
    # never subscribing to anything.
    connected = threading.Event()

    def on_connect(
        client: mqtt.Client,
        userdata: object,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        _ = userdata, flags, properties
        if _connect_failed(reason_code):
            logger.error("MQTT connect failed: %s", reason_code)
            return
        for fk in FEEDS:
            slug = FEEDS[fk].split("/feeds/")[-1]
            client.subscribe(f"{username}/feeds/{slug}/json", qos=0)
        logger.info("MQTT subscribed to %s topics", len(FEEDS))
        connected.set()

    def on_disconnect(client: mqtt.Client, userdata: object, *args: Any) -> None:
        _ = client, userdata, args
        connected.clear()

    def on_message(client: mqtt.Client, userdata: object, msg: Any) -> None:
        _ = client, userdata
        try:
            _handle_payload(msg.topic or "", msg.payload.decode("utf-8", errors="replace"))
        except Exception:
            logger.exception("MQTT ingest failed topic=%s", getattr(msg, "topic", ""))

    logger.info(
        "IoT MQTT subscriber ready (broker=%s port=%s tls=%s); waiting for a session",
        broker,
        port,
        use_tls,
    )

    while not stop.is_set():
        if not GATE.is_open():
            # Dormant: no socket, no Adafruit traffic, no writes.
            GATE.wait_for_change(timeout=recheck)
            continue

        client = _make_client()
        client.username_pw_set(username, key)
        if use_tls:
            # Without this the AIO key crosses the wire in the clear.
            client.tls_set()
        client.on_connect = on_connect
        client.on_message = on_message
        client.on_disconnect = on_disconnect
        connected.clear()

        try:
            client.reconnect_delay_set(min_delay=1, max_delay=120)
            client.connect(broker, port, keepalive=60)
            client.loop_start()

            # Wait for CONNACK + subscribe before treating the link as usable.
            if not connected.wait(timeout=_CONNACK_TIMEOUT_SEC):
                logger.warning(
                    "MQTT did not complete the handshake within %ss; retrying",
                    _CONNACK_TIMEOUT_SEC,
                )
                raise TimeoutError("MQTT CONNACK timeout")

            logger.info("MQTT connected and subscribed; streaming while a session is running")
            while not stop.is_set() and GATE.is_open() and connected.is_set():
                stop.wait(0.5)
        except Exception:
            logger.exception("MQTT session error; retrying")
        finally:
            try:
                client.loop_stop()
            except Exception:
                pass
            try:
                client.disconnect()
            except Exception:
                pass
            # Never strand buffered readings across a disconnect: on the gate-close path
            # this is what guarantees the last points of a session are persisted.
            BUFFER.flush()

        if not stop.is_set() and GATE.is_open():
            # Dropped while a session is still running -- back off before reconnecting.
            if stop.wait(timeout=5.0):
                break

    logger.info("IoT MQTT subscriber stopped")
