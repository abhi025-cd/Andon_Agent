"""Monitor: scans watched signals with check_spc and opens incidents (idempotent)."""
import json

from agents.tools import _connect, check_spc

WATCH = [
    ("ST-010", "weld_current_kA"),
    ("ST-020", "booth_temp_C"),
    ("ST-020", "humidity_pct"),
    ("ST-030", "cycle_time_s"),
    ("ST-040", "torque_nm"),
]


def _log(cur, incident_id, tool, args, result):
    cur.execute(
        "INSERT INTO agent_actions (incident_id, agent, tool, args, result) "
        "VALUES (%s, 'monitor', %s, %s, %s)",
        (incident_id, tool, json.dumps(args), json.dumps(result, default=str)))


def scan(as_of: str) -> dict:
    """Check every watched signal at `as_of`. Returns new incident ids and sensor suspects."""
    new_incidents, sensor_suspects, verdicts = [], [], {}
    conn = _connect()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for station, signal in WATCH:
                res = check_spc(station, signal, as_of)
                verdicts[f"{station}/{signal}"] = res["verdict"]
                _log(cur, None, "check_spc", {"station_id": station, "signal": signal,
                                              "as_of": as_of}, res)
                if res["verdict"] == "sensor_suspect":
                    sensor_suspects.append(f"{station}/{signal}")      # no incident
                elif res["verdict"] == "drift_suspected":
                    cur.execute(
                        "INSERT INTO incidents (station_id, signal, status, detected_at, evidence) "
                        "VALUES (%s, %s, 'open', %s, %s) "
                        "ON CONFLICT (station_id, signal) WHERE status = 'open' DO NOTHING "
                        "RETURNING id",
                        (station, signal, as_of, json.dumps(res)))
                    row = cur.fetchone()
                    if row:
                        new_incidents.append(row[0])
                        _log(cur, row[0], "open_incident", {"station_id": station,
                                                            "signal": signal}, {"incident_id": row[0]})
    finally:
        conn.close()
    return {"verdicts": verdicts, "new_incidents": new_incidents,
            "sensor_suspects": sensor_suspects}