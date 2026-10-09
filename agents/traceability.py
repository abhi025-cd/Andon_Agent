"""Traceability: turns an incident into a containment PROPOSAL for one machine.
It never executes anything."""
import json

from agents.tools import _connect, _query, find_vins_at_machine, query_tool_changes


def propose_containment(incident_id: int) -> dict:
    inc = _query("SELECT station_id, machine_id, detected_at, root_cause FROM incidents "
                 "WHERE id = %s", (incident_id,))[0]
    machine, detected = inc["machine_id"], inc["detected_at"]

    start, basis = None, None
    if inc["root_cause"] == "tool_change_misconfiguration":
        changes = query_tool_changes(machine, detected, lookback_min=120)
        if changes:
            start, basis = changes[-1]["ts"], f"latest tool change at {changes[-1]['ts']}"
    if start is None:                              # fallback: the monitor's own window
        rows = _query("SELECT ts FROM station_passes WHERE machine_id = %s "
                      "AND ts <= %s::timestamptz ORDER BY ts DESC LIMIT 8", (machine, detected))
        start, basis = rows[-1]["ts"], "start of the monitor's 8-pass window"

    passes = find_vins_at_machine(machine, start, detected)
    nok = [p["vin"] for p in passes if p["result"] == "NOK"]
    proposal = {
        "station_id": inc["station_id"], "machine_id": machine,
        "window_start": start, "window_end": detected, "window_basis": basis,
        "vins_to_hold": [p["vin"] for p in passes],
        "n_vins": len(passes), "n_nok": len(nok), "nok_vins": nok,
        "actions": [
            {"action": "hold_vins", "status": "pending_approval"},
            {"action": "stop_machine", "machine_id": machine, "status": "pending_approval",
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