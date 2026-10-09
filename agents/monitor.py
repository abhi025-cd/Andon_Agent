"""Monitor: scans every watched machine signal with check_spc and opens incidents (idempotent)."""
import json

from agents.tools import _connect, _query, check_spc

WATCH = [
    ("M-0101", "weld_current_kA"), ("M-0102", "weld_current_kA"),
    ("M-0201", "humidity_pct"), ("M-0202", "booth_temp_C"),
    ("M-0301", "cycle_time_s"), ("M-0302", "cycle_time_s"),
    ("M-0401", "torque_nm"), ("M-0402", "torque_nm"),
    ("M-0403", "torque_nm"), ("M-0404", "torque_nm"),
]


def _log(cur, incident_id, tool, args, result):
    cur.execute(
        "INSERT INTO agent_actions (incident_id, agent, tool, args, result) "
        "VALUES (%s, 'monitor', %s, %s, %s)",
        (incident_id, tool, json.dumps(args), json.dumps(result, default=str)))


def scan(as_of: str) -> dict:
    """Check every watched machine at `as_of`. Returns new incident ids and sensor suspects."""
    station_of = {r["machine_id"]: r["station_id"]
                  for r in _query("SELECT machine_id, station_id FROM machines", ())}
    new_incidents, sensor_suspects, verdicts = [], [], {}
    conn = _connect()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for machine, signal in WATCH:
                res = check_spc(machine, signal, as_of)
                verdicts[f"{machine}/{signal}"] = res["verdict"]
                _log(cur, None, "check_spc", {"machine_id": machine, "signal": signal,
                                              "as_of": as_of}, res)
                if res["verdict"] == "sensor_suspect":
                    sensor_suspects.append(f"{machine}/{signal}")      # no incident
                elif res["verdict"] == "drift_suspected":
                    cur.execute(
                        "INSERT INTO incidents (station_id, machine_id, signal, status, "
                        "detected_at, evidence) VALUES (%s, %s, %s, 'open', %s, %s) "
                        "ON CONFLICT (machine_id, signal) WHERE status = 'open' DO NOTHING "
                        "RETURNING id",
                        (station_of[machine], machine, signal, as_of, json.dumps(res)))
                    row = cur.fetchone()
                    if row:
                        new_incidents.append(row[0])
                        _log(cur, row[0], "open_incident",
                             {"machine_id": machine, "signal": signal}, {"incident_id": row[0]})
    finally:
        conn.close()
    return {"verdicts": verdicts, "new_incidents": new_incidents,
            "sensor_suspects": sensor_suspects}