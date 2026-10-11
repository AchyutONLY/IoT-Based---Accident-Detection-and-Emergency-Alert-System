from __future__ import annotations

import asyncio
import json
import os
import smtplib
import uuid
from datetime import date, datetime, timezone
from email.message import EmailMessage
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt
import psycopg2
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from psycopg2.extras import RealDictCursor


load_dotenv()
app = FastAPI(title="Car Accident Detection System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SENDER_EMAIL = os.getenv("SENDER_EMAIL", "").strip()
SENDER_APP_PASSWORD = os.getenv("SENDER_APP_PASSWORD", "").strip()

RECIPIENT_EMAILS = [
    value.strip()
    for value in (
        os.getenv("RECIPIENT1_EMAIL", ""),
        os.getenv("RECIPIENT2_EMAIL", ""),
        os.getenv("RECIPIENT3_EMAIL", ""),
    )
    if value.strip()
]

PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = os.getenv("PG_PORT", "5432")
PG_DATABASE = os.getenv("PG_DATABASE", "")
PG_USER = os.getenv("PG_USER", "")
PG_PASSWORD = os.getenv("PG_PASSWORD", "")

MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "127.0.0.1")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "").strip()
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "").strip()
MQTT_CLIENT_ID = os.getenv(
    "MQTT_CLIENT_ID",
    "accident-detection-pi",
).strip()
MQTT_TOPIC_PREFIX = os.getenv(
    "MQTT_TOPIC_PREFIX",
    "accident",
).strip().strip("/")
MQTT_KEEPALIVE = int(os.getenv("MQTT_KEEPALIVE", "60"))

APP_TIMEZONE_NAME = os.getenv("APP_TIMEZONE", "Asia/Kolkata")
APP_TIMEZONE = ZoneInfo(APP_TIMEZONE_NAME)

COUNTDOWN_SECONDS = 10
LOCATION_GRACE_SECONDS = 3

accident_states: dict[str, dict[str, Any]] = {}
countdown_tasks: dict[str, asyncio.Task] = {}
state_lock = RLock()
main_event_loop: asyncio.AbstractEventLoop | None = None

def mqtt_event_subscription() -> str:
    return f"{MQTT_TOPIC_PREFIX}/+/event"


def mqtt_cancel_subscription() -> str:
    return f"{MQTT_TOPIC_PREFIX}/+/cancel"


def mqtt_event_topic(device_id: str) -> str:
    return f"{MQTT_TOPIC_PREFIX}/{device_id}/event"


def mqtt_cancel_topic(device_id: str) -> str:
    return f"{MQTT_TOPIC_PREFIX}/{device_id}/cancel"


def mqtt_command_topic(device_id: str) -> str:
    return f"{MQTT_TOPIC_PREFIX}/{device_id}/command"


def mqtt_status_topic(device_id: str) -> str:
    return f"{MQTT_TOPIC_PREFIX}/{device_id}/status"

mqtt_client = mqtt.Client(
    client_id=MQTT_CLIENT_ID,
    protocol=mqtt.MQTTv311,
)

if MQTT_USERNAME:
    mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

mqtt_connected = False

def get_db_connection():
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        database=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD,
    )

def initialize_database() -> None:
    connection = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS accident_logs (
                id SERIAL PRIMARY KEY,
                accident_id VARCHAR(100) UNIQUE NOT NULL,
                device_id VARCHAR(100) NOT NULL,
                delta_v DOUBLE PRECISION NOT NULL,
                "date" DATE NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                latitude DOUBLE PRECISION,
                longitude DOUBLE PRECISION,
                google_maps_link TEXT,
                confirmed_at TIMESTAMPTZ,
                email_sent BOOLEAN DEFAULT FALSE
            );
            """
        )

        cursor.execute(
            """
            ALTER TABLE accident_logs
            ADD COLUMN IF NOT EXISTS "date" DATE;
            """
        )

        cursor.execute(
            """
            ALTER TABLE accident_logs
            ADD COLUMN IF NOT EXISTS google_maps_link TEXT;
            """
        )

        cursor.execute(
            """
            ALTER TABLE accident_logs
            ALTER COLUMN latitude DROP NOT NULL;
            """
        )

        cursor.execute(
            """
            ALTER TABLE accident_logs
            ALTER COLUMN longitude DROP NOT NULL;
            """
        )

        cursor.execute(
            """
            UPDATE accident_logs
            SET "date" = (timestamp AT TIME ZONE %s)::date
            WHERE "date" IS NULL;
            """,
            (APP_TIMEZONE_NAME,),
        )

        cursor.execute(
            """
            UPDATE accident_logs
            SET google_maps_link =
                'https://www.google.com/maps?q='
                || latitude::text || ',' || longitude::text
            WHERE google_maps_link IS NULL
              AND latitude IS NOT NULL
              AND longitude IS NOT NULL;
            """
        )

        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            accident_logs_accident_id_unique
            ON accident_logs(accident_id);
            """
        )

        connection.commit()
        cursor.close()

        print("Database initialized successfully.")

    except Exception as exc:
        print("======================================")
        print("DATABASE INITIALIZATION FAILED")
        print("======================================")
        print("Error:", exc)
        print("======================================")
        raise

    finally:
        if connection is not None:
            connection.close()



class AccidentData(BaseModel):
    device_id: str
    delta_v: float


class LocationData(BaseModel):
    latitude: float
    longitude: float


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def local_date(value: datetime) -> date:
    return value.astimezone(APP_TIMEZONE).date()


def maps_link(latitude: float | None, longitude: float | None) -> str | None:
    if latitude is None or longitude is None:
        return None

    return f"https://www.google.com/maps?q={latitude},{longitude}"


def remaining_seconds(started_at: datetime) -> int:
    elapsed = (now_utc() - started_at).total_seconds()
    return max(0, COUNTDOWN_SECONDS - int(elapsed))


def clean_device_id(device_id: str) -> str:
    return device_id.strip()


def send_alert_email(
    accident_id: str,
    device_id: str,
    latitude: float | None,
    longitude: float | None,
    delta_v: float,
    timestamp: datetime,
    maps_url: str | None,
) -> bool:
    if not SENDER_EMAIL:
        print("ERROR: SENDER_EMAIL is not configured.")
        return False

    if not SENDER_APP_PASSWORD:
        print("ERROR: SENDER_APP_PASSWORD is not configured.")
        return False

    if not RECIPIENT_EMAILS:
        print("ERROR: No recipient email is configured.")
        return False

    location_text = (
        f"Latitude: {latitude}\n"
        f"Longitude: {longitude}\n"
        f"Google Maps Location: {maps_url}"
        if latitude is not None and longitude is not None
        else "Phone GPS location was not available at the time the email was generated."
    )

    message = EmailMessage()
    message["Subject"] = (
        f"🚨 ACCIDENT DETECTED - {device_id} - {accident_id}"
    )
    message["From"] = SENDER_EMAIL
    message["To"] = ", ".join(RECIPIENT_EMAILS)

    message.set_content(
        f"""🚨 ACCIDENT DETECTED 🚨

Accident ID:
{accident_id}

Vehicle:
{device_id}

Date:
{local_date(timestamp)}

Timestamp:
{timestamp.isoformat()}

Change in Velocity (Δv):
{delta_v} m/s

Driver Phone Location:
{location_text}

No cancellation was received within 10 seconds.

Please check on the driver immediately.
"""
    )

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_APP_PASSWORD)
            server.send_message(
                message,
                from_addr=SENDER_EMAIL,
                to_addrs=RECIPIENT_EMAILS,
            )

        print("======================================")
        print("EMAIL SENT SUCCESSFULLY")
        print("======================================")
        print(f"Accident ID : {accident_id}")
        print(f"Vehicle     : {device_id}")
        print(f"Δv          : {delta_v} m/s")
        print(f"Timestamp   : {timestamp}")
        print("Recipients:")
        for recipient in RECIPIENT_EMAILS:
            print(f"  - {recipient}")
        print("======================================")

        return True

    except Exception as exc:
        print("======================================")
        print("EMAIL SENDING FAILED")
        print("======================================")
        print("Error:", exc)
        print("======================================")
        return False


def save_accident_log(
    accident_id: str,
    device_id: str,
    delta_v: float,
    timestamp: datetime,
    confirmed_at: datetime,
    latitude: float | None,
    longitude: float | None,
    email_sent: bool,
) -> bool:
    connection = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        link = maps_link(latitude, longitude)

        cursor.execute(
            """
            INSERT INTO accident_logs
            (
                accident_id,
                device_id,
                delta_v,
                "date",
                timestamp,
                latitude,
                longitude,
                google_maps_link,
                confirmed_at,
                email_sent
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (accident_id)
            DO UPDATE SET
                device_id = EXCLUDED.device_id,
                delta_v = EXCLUDED.delta_v,
                "date" = EXCLUDED."date",
                timestamp = EXCLUDED.timestamp,
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude,
                google_maps_link = EXCLUDED.google_maps_link,
                confirmed_at = EXCLUDED.confirmed_at,
                email_sent = EXCLUDED.email_sent;
            """,
            (
                accident_id,
                device_id,
                delta_v,
                local_date(timestamp),
                timestamp,
                latitude,
                longitude,
                link,
                confirmed_at,
                email_sent,
            ),
        )

        connection.commit()
        cursor.close()

        print(f"Accident log saved: {accident_id}")
        return True

    except Exception as exc:
        print("======================================")
        print("FAILED TO SAVE ACCIDENT LOG")
        print("======================================")
        print("Error:", exc)
        print("======================================")
        return False

    finally:
        if connection is not None:
            connection.close()


def publish_mqtt_command(
    device_id: str,
    command: str,
    accident_id: str | None = None,
) -> bool:
    if not mqtt_connected:
        print("MQTT command not sent: broker is not connected.")
        return False

    payload = {
        "command": command,
        "device_id": device_id,
        "accident_id": accident_id,
        "timestamp": now_utc().isoformat(),
    }

    try:
        result = mqtt_client.publish(
            mqtt_command_topic(device_id),
            json.dumps(payload),
            qos=1,
            retain=False,
        )

        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            print("MQTT publish failed. rc=", result.rc)
            return False

        print(
            f"MQTT command published -> "
            f"{mqtt_command_topic(device_id)}: {payload}"
        )
        return True

    except Exception as exc:
        print("MQTT publish exception:", exc)
        return False


def publish_mqtt_status(device_id: str, state: dict[str, Any]) -> None:
    if not mqtt_connected:
        return

    payload = {
        "device_id": device_id,
        "accident_id": state.get("accident_id"),
        "status": state.get("status"),
        "remaining_seconds": (
            remaining_seconds(state["started_at"])
            if state.get("status") == "COUNTDOWN"
            else 0
        ),
        "timestamp": now_utc().isoformat(),
    }

    try:
        mqtt_client.publish(
            mqtt_status_topic(device_id),
            json.dumps(payload),
            qos=1,
            retain=False,
        )
    except Exception as exc:
        print("MQTT status publish exception:", exc)

async def start_accident(
    device_id: str,
    delta_v: float,
    source: str,
) -> dict[str, Any]:
    device_id = clean_device_id(device_id)

    if not device_id:
        raise ValueError("device_id cannot be empty")

    if delta_v < 0:
        raise ValueError("delta_v cannot be negative")

    with state_lock:
        current = accident_states.get(device_id)

        if current and current.get("status") == "COUNTDOWN":
            return {
                "message": "Countdown already running",
                "device_id": device_id,
                "status": "COUNTDOWN",
                "accident_id": current["accident_id"],
                "countdown": remaining_seconds(current["started_at"]),
            }

        old_task = countdown_tasks.get(device_id)
        if old_task is not None and not old_task.done():
            old_task.cancel()

        accident_id = str(uuid.uuid4())
        started_at = now_utc()

        state = {
            "accident_id": accident_id,
            "device_id": device_id,
            "status": "COUNTDOWN",
            "delta_v": float(delta_v),
            "started_at": started_at,
            "confirmed_at": None,
            "latitude": None,
            "longitude": None,
            "email_sent": False,
            "log_saved": False,
            "source": source,
        }

        accident_states[device_id] = state

        task = asyncio.create_task(
            accident_countdown(device_id, accident_id)
        )
        countdown_tasks[device_id] = task

    print("======================================")
    print("POSSIBLE ACCIDENT DETECTED")
    print("======================================")
    print(f"Source        : {source}")
    print(f"Vehicle       : {device_id}")
    print(f"Accident ID   : {accident_id}")
    print(f"Δv            : {delta_v} m/s")
    print(f"Timestamp     : {started_at}")
    print("10-second countdown started.")
    print("======================================")

    publish_mqtt_status(device_id, state)

    return {
        "message": "Possible accident detected",
        "accident_id": accident_id,
        "device_id": device_id,
        "status": "COUNTDOWN",
        "countdown": COUNTDOWN_SECONDS,
        "delta_v": float(delta_v),
        "timestamp": started_at.isoformat(),
        "location_required": True,
        "source": source,
    }


async def wait_for_location(
    device_id: str,
    accident_id: str,
    timeout_seconds: float,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds

    while asyncio.get_running_loop().time() < deadline:
        state = accident_states.get(device_id)

        if not state or state.get("accident_id") != accident_id:
            return

        if (
            state.get("latitude") is not None
            and state.get("longitude") is not None
        ):
            return

        await asyncio.sleep(0.1)


async def accident_countdown(
    device_id: str,
    accident_id: str,
) -> None:
    current_task = asyncio.current_task()

    try:
        await asyncio.sleep(COUNTDOWN_SECONDS)

        state = accident_states.get(device_id)

        if not state or state.get("accident_id") != accident_id:
            return

        if state.get("status") != "COUNTDOWN":
            return

        # The 10-second cancellation window has ended.
        state["status"] = "ACCIDENT_CONFIRMED"
        state["confirmed_at"] = now_utc()

        # Give the phone GPS request a short grace period.
        await wait_for_location(
            device_id,
            accident_id,
            LOCATION_GRACE_SECONDS,
        )

        state = accident_states.get(device_id)

        if not state or state.get("accident_id") != accident_id:
            return

        latitude = state.get("latitude")
        longitude = state.get("longitude")
        delta_v = state["delta_v"]
        started_at = state["started_at"]
        confirmed_at = state["confirmed_at"]

        link = maps_link(latitude, longitude)

        state["alert"] = {
            "accident_id": accident_id,
            "title": "🚨 ACCIDENT DETECTED 🚨",
            "description": (
                "The accident detection system detected a possible accident "
                "and no cancellation was received within 10 seconds."
            ),
            "device_id": device_id,
            "delta_v": delta_v,
            "latitude": latitude,
            "longitude": longitude,
            "timestamp": started_at.isoformat(),
            "confirmed_at": confirmed_at.isoformat(),
            "maps_link": link,
        }

        email_sent = await asyncio.to_thread(
            send_alert_email,
            accident_id,
            device_id,
            latitude,
            longitude,
            delta_v,
            started_at,
            link,
        )

        state["email_sent"] = email_sent

        log_saved = await asyncio.to_thread(
            save_accident_log,
            accident_id,
            device_id,
            delta_v,
            started_at,
            confirmed_at,
            latitude,
            longitude,
            email_sent,
        )

        state["log_saved"] = log_saved

        print("======================================")
        print("🚨 ACCIDENT CONFIRMED")
        print("======================================")
        print(f"Accident ID   : {accident_id}")
        print(f"Vehicle       : {device_id}")
        print(f"Δv            : {delta_v} m/s")
        print(f"Latitude      : {latitude}")
        print(f"Longitude     : {longitude}")
        print(f"Timestamp     : {started_at}")
        print(f"Email Sent    : {email_sent}")
        print(f"Log Saved     : {log_saved}")
        print("======================================")

        publish_mqtt_status(device_id, state)

    except asyncio.CancelledError:
        print(f"Countdown cancelled for {device_id}")
        raise

    except Exception as exc:
        print("======================================")
        print("ACCIDENT COUNTDOWN ERROR")
        print("======================================")
        print("Error:", exc)
        print("======================================")

    finally:
        if countdown_tasks.get(device_id) is current_task:
            countdown_tasks.pop(device_id, None)


async def cancel_active_accident(
    device_id: str,
    source: str,
    notify_esp32: bool,
) -> dict[str, Any]:
    device_id = clean_device_id(device_id)

    state = accident_states.get(device_id)

    if not state:
        return {
            "device_id": device_id,
            "message": "No active accident found",
            "status": "NORMAL",
        }

    if state.get("status") == "ACCIDENT_CONFIRMED":
        return {
            "device_id": device_id,
            "message": "Accident already confirmed. Cancellation rejected.",
            "status": "ACCIDENT_CONFIRMED",
            "accident_id": state.get("accident_id"),
        }

    if state.get("status") != "COUNTDOWN":
        return {
            "device_id": device_id,
            "message": "No active accident countdown",
            "status": "NORMAL",
        }

    accident_id = state.get("accident_id")

    task = countdown_tasks.get(device_id)
    if task is not None and not task.done():
        task.cancel()

    with state_lock:
        # Remove the active accident completely so all new status
        # queries show NORMAL after cancellation.
        accident_states.pop(device_id, None)
        countdown_tasks.pop(device_id, None)

    if notify_esp32:
        esp_command_sent = publish_mqtt_command(
            device_id,
            "CANCEL",
            accident_id,
        )
    else:
        esp_command_sent = False

    print("======================================")
    print("ACCIDENT CANCELLED")
    print("======================================")
    print(f"Source        : {source}")
    print(f"Vehicle       : {device_id}")
    print(f"Accident ID   : {accident_id}")
    print(f"ESP32 command : {esp_command_sent}")
    print("======================================")

    return {
        "device_id": device_id,
        "message": "Accident cancelled. Driver is okay.",
        "status": "NORMAL",
        "accident_id": accident_id,
        "source": source,
        "esp_command_sent": esp_command_sent,
    }


def parse_mqtt_payload(payload: bytes) -> dict[str, Any]:
    try:
        decoded = payload.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("MQTT payload is not valid UTF-8") from exc

    if not decoded:
        return {}

    try:
        value = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise ValueError("MQTT payload must be JSON") from exc

    if not isinstance(value, dict):
        raise ValueError("MQTT payload must be a JSON object")

    return value


def mqtt_device_from_topic(topic: str) -> str | None:
    parts = topic.strip("/").split("/")

    # accident/<device_id>/event
    if len(parts) != 3:
        return None

    if parts[0] != MQTT_TOPIC_PREFIX:
        return None

    return parts[1].strip() or None


def on_mqtt_connect(client, userdata, flags, rc):
    global mqtt_connected

    if rc == 0:
        mqtt_connected = True
        client.subscribe(mqtt_event_subscription(), qos=1)
        client.subscribe(mqtt_cancel_subscription(), qos=1)

        print("======================================")
        print("MQTT CONNECTED")
        print("======================================")
        print(f"Broker : {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
        print(f"Event  : {mqtt_event_subscription()}")
        print(f"Cancel : {mqtt_cancel_subscription()}")
        print("======================================")
    else:
        mqtt_connected = False
        print("MQTT connection failed. rc=", rc)


def on_mqtt_disconnect(client, userdata, rc):
    global mqtt_connected
    mqtt_connected = False
    print("MQTT disconnected. rc=", rc)


def on_mqtt_message(client, userdata, message):
    if main_event_loop is None:
        print("MQTT message ignored: application event loop is not ready.")
        return

    device_id = mqtt_device_from_topic(message.topic)
    if not device_id:
        print("MQTT message ignored: invalid topic:", message.topic)
        return

    try:
        payload = parse_mqtt_payload(message.payload)
    except ValueError as exc:
        print("MQTT message ignored:", exc)
        return

    topic_parts = message.topic.strip("/").split("/")
    message_type = topic_parts[2]

    if message_type == "event":
        payload_device_id = str(
            payload.get("device_id", device_id)
        ).strip()

        if payload_device_id != device_id:
            print("MQTT accident ignored: device_id mismatch")
            return

        try:
            delta_v = float(payload["delta_v"])
        except (KeyError, TypeError, ValueError):
            print("MQTT accident ignored: invalid delta_v")
            return

        future = asyncio.run_coroutine_threadsafe(
            start_accident(
                device_id,
                delta_v,
                source="ESP32_MQTT",
            ),
            main_event_loop,
        )

        try:
            future.result(timeout=5)
        except Exception as exc:
            print("Error processing MQTT accident:", exc)

    elif message_type == "cancel":
        future = asyncio.run_coroutine_threadsafe(
            cancel_active_accident(
                device_id,
                source="ESP32_BUTTON",
                notify_esp32=False,
            ),
            main_event_loop,
        )

        try:
            future.result(timeout=5)
        except Exception as exc:
            print("Error processing MQTT cancellation:", exc)


mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_disconnect = on_mqtt_disconnect
mqtt_client.on_message = on_mqtt_message


@app.on_event("startup")
async def startup_event():
    global main_event_loop

    main_event_loop = asyncio.get_running_loop()

    initialize_database()

    print("Starting MQTT client...")
    mqtt_client.connect_async(
        MQTT_BROKER_HOST,
        MQTT_BROKER_PORT,
        MQTT_KEEPALIVE,
    )
    mqtt_client.loop_start()


@app.on_event("shutdown")
async def shutdown_event():
    for task in list(countdown_tasks.values()):
        if task is not None and not task.done():
            task.cancel()

    mqtt_client.loop_stop()
    try:
        mqtt_client.disconnect()
    except Exception:
        pass

@app.get("/")
def home():
    return {
        "message": "Accident Detection Backend is running",
        "mqtt_connected": mqtt_connected,
        "mqtt_event_topic": mqtt_event_subscription(),
        "mqtt_cancel_topic": mqtt_cancel_subscription(),
    }


@app.get("/health")
def health():
    return {
        "backend": "OK",
        "mqtt": "CONNECTED" if mqtt_connected else "DISCONNECTED",
        "database": "PostgreSQL",
    }



@app.post("/api/accident/trigger")
async def trigger_accident(data: AccidentData):
    try:
        return await start_accident(
            data.device_id,
            data.delta_v,
            source="APP_SIMULATION",
        )
    except ValueError as exc:
        return {
            "message": str(exc),
            "status": "NORMAL",
            "device_id": data.device_id,
        }



@app.post("/api/accident/location/{device_id}")
def update_accident_location(device_id: str, data: LocationData):
    device_id = clean_device_id(device_id)

    if not (-90 <= data.latitude <= 90):
        return {
            "device_id": device_id,
            "message": "Invalid latitude",
        }

    if not (-180 <= data.longitude <= 180):
        return {
            "device_id": device_id,
            "message": "Invalid longitude",
        }

    state = accident_states.get(device_id)

    if not state:
        return {
            "device_id": device_id,
            "message": "No active accident found",
            "status": "NORMAL",
        }

    if state.get("status") not in (
        "COUNTDOWN",
        "ACCIDENT_CONFIRMED",
    ):
        return {
            "device_id": device_id,
            "message": "No active accident",
            "status": "NORMAL",
        }

    state["latitude"] = float(data.latitude)
    state["longitude"] = float(data.longitude)

    print("======================================")
    print("DRIVER PHONE LOCATION RECEIVED")
    print("======================================")
    print(f"Vehicle   : {device_id}")
    print(f"Latitude  : {data.latitude}")
    print(f"Longitude : {data.longitude}")
    print("======================================")

    # If confirmation happened just before the GPS response arrived,
    # update the already-created DB row as well.
    if state.get("status") == "ACCIDENT_CONFIRMED":
        try:
            log_saved = save_accident_location_update(
                accident_id=state["accident_id"],
                latitude=state["latitude"],
                longitude=state["longitude"],
            )
            state["log_saved"] = state.get("log_saved", False) or log_saved
        except Exception as exc:
            print("Late location DB update failed:", exc)

    return {
        "device_id": device_id,
        "accident_id": state["accident_id"],
        "message": "Driver phone location updated",
        "status": state["status"],
        "latitude": state["latitude"],
        "longitude": state["longitude"],
    }



def save_accident_location_update(
    accident_id: str,
    latitude: float,
    longitude: float,
) -> bool:
    connection = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE accident_logs
            SET latitude = %s,
                longitude = %s,
                google_maps_link = %s
            WHERE accident_id = %s;
            """,
            (
                latitude,
                longitude,
                maps_link(latitude, longitude),
                accident_id,
            ),
        )

        connection.commit()
        cursor.close()
        return True

    except Exception as exc:
        print("Failed to update accident location:", exc)
        return False

    finally:
        if connection is not None:
            connection.close()



@app.get("/api/accident/status/{device_id}")
def accident_status(device_id: str):
    device_id = clean_device_id(device_id)
    state = accident_states.get(device_id)

    if not state:
        return {
            "device_id": device_id,
            "status": "NORMAL",
            "remaining_seconds": 0,
            "delta_v": None,
            "latitude": None,
            "longitude": None,
            "accident_id": None,
        }

    response = {
        "accident_id": state.get("accident_id"),
        "device_id": device_id,
        "status": state.get("status"),
        "remaining_seconds": (
            remaining_seconds(state["started_at"])
            if state.get("status") == "COUNTDOWN"
            else 0
        ),
        "delta_v": state.get("delta_v"),
        "latitude": state.get("latitude"),
        "longitude": state.get("longitude"),
        "timestamp": state.get("started_at").isoformat(),
    }

    if state.get("status") == "ACCIDENT_CONFIRMED":
        response["alert"] = state.get("alert")
        response["email_sent"] = state.get("email_sent", False)
        response["log_saved"] = state.get("log_saved", False)

    return response



@app.post("/api/accident/cancel/{device_id}")
async def cancel_accident(device_id: str):
    return await cancel_active_accident(
        device_id=device_id,
        source="MOBILE_APP",
        notify_esp32=True,
    )



@app.get("/api/accident/alert/{device_id}")
def get_alert(device_id: str):
    device_id = clean_device_id(device_id)
    state = accident_states.get(device_id)

    if not state:
        return {
            "device_id": device_id,
            "message": "No accident found",
            "status": "NORMAL",
        }

    if state.get("status") != "ACCIDENT_CONFIRMED":
        return {
            "device_id": device_id,
            "message": "Alert not generated yet",
            "current_status": state.get("status"),
        }

    return {
        "accident_id": state["accident_id"],
        "device_id": device_id,
        "status": "ACCIDENT_CONFIRMED",
        "alert": state.get("alert"),
        "email_sent": state.get("email_sent", False),
        "log_saved": state.get("log_saved", False),
    }



@app.get("/api/accident/logs/{device_id}")
def get_accident_logs(device_id: str):
    device_id = clean_device_id(device_id)
    connection = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(cursor_factory=RealDictCursor)

        cursor.execute(
            """
            SELECT
                id,
                accident_id,
                device_id,
                delta_v,
                "date",
                timestamp,
                confirmed_at,
                latitude,
                longitude,
                google_maps_link,
                email_sent
            FROM accident_logs
            WHERE device_id = %s
            ORDER BY timestamp DESC;
            """,
            (device_id,),
        )

        logs = cursor.fetchall()
        cursor.close()

        return {
            "device_id": device_id,
            "total_accidents": len(logs),
            "logs": logs,
        }

    except Exception as exc:
        return {
            "device_id": device_id,
            "error": str(exc),
        }

    finally:
        if connection is not None:
            connection.close()



@app.get("/api/accident/logs")
def get_all_accident_logs():
    connection = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(cursor_factory=RealDictCursor)

        cursor.execute(
            """
            SELECT
                id,
                accident_id,
                device_id,
                delta_v,
                "date",
                timestamp,
                confirmed_at,
                latitude,
                longitude,
                google_maps_link,
                email_sent
            FROM accident_logs
            ORDER BY timestamp DESC;
            """
        )

        logs = cursor.fetchall()
        cursor.close()

        return {
            "total_accidents": len(logs),
            "logs": logs,
        }

    except Exception as exc:
        return {
            "error": str(exc),
        }

    finally:
        if connection is not None:
            connection.close()
'