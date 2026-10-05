"""Step 4: scenario injection.
Timestamps come from a simulated clock, never datetime.now()."""
import argparse
import json
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
import yaml

BROKER_HOST = "localhost"
BROKER_PORT = 1883
SCENARIO_DIR = Path(__file__).resolve().parent.parent / "scenarios"

SIM_START = datetime(2026, 10, 4, 10, 0, 0, tzinfo=timezone.utc)
VEHICLE_INTERVAL_S = 60
STATION_TRAVEL_S = 300

STATIONS = {
    "ST-010": {"signals": {"weld_current_kA": {"mean": 9.5, "std": 0.4}}},
    "ST-020": {"signals": {"booth_temp_C": {"mean": 24.0, "std": 1.0},
                           "humidity_pct": {"mean": 55.0, "std": 5.0}}},
    "ST-030": {"signals": {"cycle_time_s": {"mean": 52.0, "std": 3.0}}},
    "ST-040": {"signals": {"torque_nm": {"mean": 45.0, "std": 1.5}}},
    "ST-050": {"signals": {}},
}
STATION_ORDER = ["ST-010", "ST-020", "ST-030", "ST-040", "ST-050"]


def fmt(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_vin(n: int) -> str:
    return "MA3EX11S0P" + str(n).zfill(7)


def sim_time(vehicle_n: int, station_index: int) -> datetime:
    offset = (vehicle_n - 1) * VEHICLE_INTERVAL_S + station_index * STATION_TRAVEL_S
    return SIM_START + timedelta(seconds=offset)


def load_scenario(name: str | None) -> dict | None:
    if not name:
        return None
    with open(SCENARIO_DIR / f"{name}.yaml") as f:
        return yaml.safe_load(f)


def read_station(station_id: str, ts: datetime, scenario: dict | None):
    """Return (measured, result). Scenario signal injections change the mean
    (drift) or freeze the value (stuck). NOK is judged against the NOMINAL mean."""
    t_min = (ts - SIM_START).total_seconds() / 60
    measured, result = {}, "OK"

    for name, p in STATIONS[station_id]["signals"].items():
        mean, stuck = p["mean"], None
        if scenario:
            for inj in scenario["inject"]:
                if (inj["type"] != "signal"
                        or inj.get("station", scenario["station"]) != station_id
                        or inj["signal"] != name
                        or not inj["at_min"] <= t_min <= inj["until_min"]):
                    continue
                if inj["pattern"] == "linear_drift":
                    frac = (t_min - inj["at_min"]) / (inj["until_min"] - inj["at_min"])
                    mean = inj["from_value"] + frac * (inj["to_value"] - inj["from_value"])
                elif inj["pattern"] == "stuck_at":
                    stuck = inj["value"]

        value = random.gauss(mean, p["std"])   # always draw: keeps RNG order identical
        if stuck is not None:
            value = stuck
        value = round(value, 2)
        measured[name] = value
        if stuck is None and abs(value - p["mean"]) > 3 * p["std"]:
            result = "NOK"

    if station_id == "ST-050":
        measured["leak_test_pass"] = True
    return measured, result


def build_messages(num_vehicles: int, scenario: dict | None):
    messages = []
    for n in range(1, num_vehicles + 1):
        for i, station_id in enumerate(STATION_ORDER):
            ts = sim_time(n, i)
            measured, result = read_station(station_id, ts, scenario)
            payload = {"vin": make_vin(n), "station_id": station_id, "ts": fmt(ts),
                       "measured": measured, "result": result}
            messages.append((ts, f"plant/line1/{station_id}/pass", payload))

    if scenario:
        for inj in scenario["inject"]:
            station = inj.get("station", scenario["station"])
            ts = SIM_START + timedelta(minutes=inj["at_min"])
            if inj["type"] == "event":
                payload = {"station_id": station, "ts": fmt(ts), "type": inj["event"],
                           "details": inj.get("details", {})}
                messages.append((ts, f"plant/line1/{station}/event", payload))
            elif inj["type"] == "alarm":
                payload = {"station_id": station, "ts": fmt(ts), "code": inj["code"],
                           "severity": inj.get("severity", "warning")}
                messages.append((ts, f"plant/line1/{station}/alarm", payload))

    messages.sort(key=lambda m: m[0])   # stable sort
    return messages


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", help="name of a file in scenarios/, without .yaml")
    parser.add_argument("--vehicles", type=int, default=None)
    parser.add_argument("--speed", type=float, default=60.0)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    scenario = load_scenario(args.scenario)
    vehicles = args.vehicles or (scenario["duration_min"] if scenario else 10)

    random.seed(args.seed)
    messages = build_messages(vehicles, scenario)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(BROKER_HOST, BROKER_PORT)
    client.loop_start()

    last_info, prev_ts = None, messages[0][0]
    for ts, topic, payload in messages:
        gap_s = (ts - prev_ts).total_seconds()
        if gap_s > 0:
            time.sleep(gap_s / args.speed)
        prev_ts = ts
        last_info = client.publish(topic, json.dumps(payload), qos=1)
        print(payload["ts"], topic.split("/", 2)[2], payload.get("vin", ""),
              payload.get("measured", payload.get("code", payload.get("type"))),
              payload.get("result", ""))

    if last_info is not None:
        last_info.wait_for_publish()
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()