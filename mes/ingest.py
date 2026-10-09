"""Ingestion worker: MQTT -> Postgres (machine level).
Idempotent: replaying the same messages never creates duplicate rows."""
import json
import os
from datetime import datetime

import paho.mqtt.client as mqtt
import psycopg2
from dotenv import load_dotenv

load_dotenv()

BROKER_HOST = "localhost"
BROKER_PORT = 1883
ALARM_BUCKET_MIN = 5   # same alarm on the same machine within 5 sim minutes counts as one

conn = psycopg2.connect(
    host="localhost", port=5432,
    user=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
    dbname=os.environ["POSTGRES_DB"],
)
conn.autocommit = True


def handle_pass(p: dict) -> None:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO vehicles (vin, created_ts) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING", (p["vin"], p["ts"]))
        cur.execute(
            "INSERT INTO station_passes (vin, station_id, machine_id, ts, measured, result) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (vin, machine_id) DO NOTHING",
            (p["vin"], p["station_id"], p["machine_id"], p["ts"],
             json.dumps(p["measured"]), p["result"]))


def handle_event(p: dict) -> None:
    etype = p["type"]
    with conn.cursor() as cur:
        if etype == "tool_change":
            cur.execute("INSERT INTO tool_changes (station_id, machine_id, ts, details) "
                        "VALUES (%s, %s, %s, %s)",
                        (p["station_id"], p["machine_id"], p["ts"],
                         json.dumps(p.get("details", {}))))
        elif etype in ("machine_stop", "jam_start", "comm_loss"):
            cur.execute("INSERT INTO downtime_events (station_id, machine_id, start_ts, reason_code) "
                        "VALUES (%s, %s, %s, %s)",
                        (p["station_id"], p["machine_id"], p["ts"], etype))
        elif etype in ("machine_restart", "jam_end"):
            cur.execute(
                "UPDATE downtime_events SET end_ts = %s WHERE id = ("
                "  SELECT id FROM downtime_events WHERE machine_id = %s AND end_ts IS NULL "
                "  ORDER BY start_ts DESC LIMIT 1)", (p["ts"], p["machine_id"]))
        else:
            print("unhandled event type:", etype)


def make_dedup_key(p: dict) -> str:
    ts = datetime.fromisoformat(p["ts"].replace("Z", "+00:00"))
    bucket = int(ts.timestamp() // (ALARM_BUCKET_MIN * 60))
    return f"{p['machine_id']}:{p['code']}:{bucket}"


def handle_alarm(p: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO alarms (station_id, machine_id, ts, code, severity, dedup_key) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (dedup_key) DO NOTHING",
            (p["station_id"], p["machine_id"], p["ts"], p["code"],
             p.get("severity", "warning"), make_dedup_key(p)))


HANDLERS = {"pass": handle_pass, "event": handle_event, "alarm": handle_alarm}


def on_connect(client, userdata, flags, reason_code, properties):
    print("connected to broker:", reason_code)
    client.subscribe("plant/#", qos=1)


def on_message(client, userdata, msg):
    kind = msg.topic.split("/")[-1]          # pass | event | alarm
    handler = HANDLERS.get(kind)
    if handler is None:
        return
    try:
        handler(json.loads(msg.payload))
    except Exception as exc:                  # never crash the worker on one bad message
        print("FAIL", msg.topic, exc)


def main() -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER_HOST, BROKER_PORT)
    client.loop_forever()


if __name__ == "__main__":
    main()