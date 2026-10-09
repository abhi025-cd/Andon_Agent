"""Machine-level simulator: every machine at a station measures every vehicle.
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

PLANT_ID = "PLANT-01"
LINE_ID = "LINE-01"
SIM_START = datetime(2026, 10, 4, 10, 0, 0, tzinfo=timezone.utc)
VEHICLE_INTERVAL_S = 60       # a new VIN enters the line every 60 s
STATION_TRAVEL_S = 300        # 5 min between stations

STATION_ORDER = ["ST-010", "ST-020", "ST-030", "ST-040", "ST-050"]

MACHINES = {
    "M-0101": {"station": "ST-010", "signals": {"weld_current_kA": {"mean": 9.5, "std": 0.4}}},
    "M-0102": {"station": "ST-010", "signals": {"weld_current_kA": {"mean": 9.5, "std": 0.4}}},
    "M-0201": {"station": "ST-020", "signals": {"humidity_pct": {"mean": 55.0, "std": 5.0}}},
    "M-0202": {"station": "ST-020", "signals": {"booth_temp_C": {"mean": 24.0, "std": 1.0}}},
    "M-0301": {"station": "ST-030", "signals": {"cycle_time_s": {"mean": 52.0, "std": 3.0}}},
    "M-0302": {"station": "ST-030", "signals": {"cycle_time_s": {"mean": 52.0, "std": 3.0}}},
    "M-0401": {"station": "ST-040", "signals": {"torque_nm": {"mean": 45.0, "std": 1.5}}},
    "M-0402": {"station": "ST-040", "signals": {"torque_nm": {"mean": 45.0, "std": 1.5}}},
    "M-0403": {"station": "ST-040", "signals": {"torque_nm": {"mean": 45.0, "std": 1.5}}},
    "M-0404": {"station": "ST-040", "signals": {"torque_nm": {"mean": 45.0, "std": 1.5}}},
    "M-0501": {"station": "ST-050", "signals": {}},
    "M-0502": {"station": "ST-050", "signals": {}},
}
STATION_MACHINES = {s: [m for m, c in MACHINES.items() if c["station"] == s]
                    for s in STATION_ORDER}


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


def read_machine(machine_id: str, ts: datetime, scenario: dict | None):
    """Return (measured, result). Scenario injections only affect the named machine.
    NOK is judged against the NOMINAL mean, like a real machine with fixed limits."""
    t_min = (ts - SIM_START).total_seconds() / 60
    measured, result = {}, "OK"

    for name, p in MACHINES[machine_id]["signals"].items():
        mean, stuck = p["mean"], None
        if scenario:
            for inj in scenario["inject"]:
                if (inj["type"] != "signal"
                        or inj.get("machine", scenario["machine"]) != machine_id
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

    if MACHINES[machine_id]["station"] == "ST-050":
        measured["leak_test_pass"] = True
    return measured, result


def topic_for(machine_id: str, kind: str) -> str:
    return f"plant/line1/{MACHINES[machine_id]['station']}/{machine_id}/{kind}"


def build_messages(num_vehicles: int, scenario: dict | None):
    messages = []
    for n in range(1, num_vehicles + 1):
        for i, station_id in enumerate(STATION_ORDER):
            ts = sim_time(n, i)
            for machine_id in STATION_MACHINES[station_id]:
                measured, result = read_machine(machine_id, ts, scenario)
                payload = {"plant_id": PLANT_ID, "line_id": LINE_ID, "vin": make_vin(n),
                           "station_id": station_id, "machine_id": machine_id,
                           "ts": fmt(ts), "measured": measured, "result": result}
                messages.append((ts, topic_for(machine_id, "pass"), payload))

    if scenario:
        for inj in scenario["inject"]:
            machine_id = inj.get("machine", scenario["machine"])
            station_id = MACHINES[machine_id]["station"]
            ts = SIM_START + timedelta(minutes=inj["at_min"])
            base = {"plant_id": PLANT_ID, "line_id": LINE_ID, "station_id": station_id,
                    "machine_id": machine_id, "ts": fmt(ts)}
            if inj["type"] == "event":
                payload = {**base, "type": inj["event"], "details": inj.get("details", {})}
                messages.append((ts, topic_for(machine_id, "event"), payload))
            elif inj["type"] == "alarm":
                payload = {**base, "code": inj["code"], "severity": inj.get("severity", "warning")}
                messages.append((ts, topic_for(machine_id, "alarm"), payload))

    messages.sort(key=lambda m: m[0])   # stable sort
    return messages


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", help="name of a file in scenarios/, without .yaml")
    parser.add_argument("--vehicles", type=int, default=None)
    parser.add_argument("--speed", type=float, default=60.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--quiet", action="store_true")
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
        if not args.quiet:
            print(payload["ts"], topic.split("/", 2)[2], payload.get("vin", ""),
                  payload.get("measured", payload.get("code", payload.get("type"))),
                  payload.get("result", ""))

    if last_info is not None:
        last_info.wait_for_publish()
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()