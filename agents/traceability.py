"""Traceability: turns an incident into a containment PROPOSAL. It never executes it."""
import json

from agents.tools import _connect, _query, find_vins_at_station, query_tool_changes


def propose_containment(incident_id: int) -> dict:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT station_id, detected_at, root_cause FROM incidents WHERE id = %s",
                        (incident_id,))
            station, detected_at, root_cause = cur.fetchone()
    finally:
        conn.close()
    detected = detected_at.isoformat()

    start, basis = None, None
    if root_cause == "tool_change_misconfiguration":
        changes = query_tool_changes(station, detected, lookback_min=120)
        if changes:
            start, basis = changes[-1]["ts"], f"latest tool change at {changes[-1]['ts']}"
    if start is None:                                    # fallback: the monitor's own window
        rows = _query("SELECT ts FROM station_passes WHERE station_id = %s "
                      "AND ts <= %s::timestamptz ORDER BY ts DESC LIMIT 8", (station, detected))
        start, basis = rows[-1]["ts"], "start of the monitor's 8-pass window"

    passes = find_vins_at_station(station, start, detected)
    nok = [p["vin"] for p in passes if p["result"] == "NOK"]
    proposal = {
        "station_id": station, "window_start": start, "window_end": detected,
        "window_basis": basis,
        "vins_to_hold": [p["vin"] for p in passes],
        "n_vins": len(passes), "n_nok": len(nok), "nok_vins": nok,
        "actions": [
            {"action": "hold_vins", "status": "pending_approval"},
            {"action": "stop_station", "station_id": station, "status": "pending_approval",
             "reason": "vehicles arriving after this time are also at risk until the cause is fixed"},
        ],
    }
    conn = _connect()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE incidents SET containment = %s WHERE id = %s",
                        (json.dumps(proposal), incident_id))
            cur.execute("INSERT INTO agent_actions (incident_id, agent, tool, args, result) "
                        "VALUES (%s, 'traceability', 'propose_containment', %s, %s)",
                        (incident_id, json.dumps({"incident_id": incident_id}),
                         json.dumps({"n_vins": len(passes), "window_basis": basis})))
    finally:
        conn.close()
    return proposal