# Follows pattern from: app/main.py (background thread), app/config.py, app/database.py (SessionLocal)
from __future__ import annotations

import logging
import threading
from typing import Any

import paho.mqtt.client as mqtt

from app.config import settings
from app.database import SessionLocal
from app.services.ingestion_pipeline import ingest_reading
from app.services.normalizer import FEEDS, feed_key_from_adafruit_topic_or_slug, process_iot_data

logger = logging.getLogger(__name__)


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


def run_mqtt_subscriber(stop: threading.Event) -> None:
    """
    Connect to Adafruit IO MQTT, subscribe to all feeds, push through the same normalizer + ingestion.
    """
    username = settings.AIO_USERNAME
    key = settings.AIO_KEY
    if not username or not key:
        logger.warning("IoT MQTT disabled: missing AIO_USERNAME / AIO_KEY")
        return

    broker = settings.IOT_MQTT_BROKER
    port = int(settings.IOT_MQTT_PORT)
    client = _make_client()
    client.username_pw_set(username, key)

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

    def on_message(client: mqtt.Client, userdata: object, msg: Any) -> None:
        _ = client, userdata
        try:
            topic = msg.topic or ""
            payload_raw = msg.payload.decode("utf-8", errors="replace")
            fk = feed_key_from_adafruit_topic_or_slug(topic)
            if not fk:
                logger.debug("MQTT topic not mapped to feed_key: %s", topic)
                return
            synthetic = {"value": payload_raw, "id": None}
            normalized = process_iot_data(
                fk,
                synthetic,
                default_device_id=settings.IOT_DEFAULT_DEVICE_ID,
            )
            if normalized is None:
                return
            db = SessionLocal()
            try:
                ingest_reading(db, normalized)
            finally:
                db.close()
        except Exception:
            logger.exception("MQTT ingest failed topic=%s", getattr(msg, "topic", ""))

    client.on_connect = on_connect
    client.on_message = on_message

    while not stop.is_set():
        try:
            client.reconnect_delay_set(min_delay=1, max_delay=120)
            client.connect(broker, port, keepalive=60)
            client.loop_start()
            while not stop.is_set() and client.is_connected():
                stop.wait(0.5)
            client.loop_stop()
        except Exception:
            logger.exception("MQTT session error; retrying")
            try:
                client.loop_stop()
            except Exception:
                pass
            try:
                client.disconnect()
            except Exception:
                pass
        if stop.wait(timeout=5.0):
            break
