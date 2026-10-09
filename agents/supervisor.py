"""Supervisor: a LangGraph state machine that routes the agents and pauses for human approval.
monitor -> diagnose -> traceability -> report -> approval (interrupt) -> END"""
import json
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agents.diagnosis import diagnose
from agents.monitor import scan
from agents.reporting import write_report
from agents.tools import _connect, _query
from agents.traceability import propose_containment


class State(TypedDict, total=False):
    as_of: str
    incident_id: int | None
    proposal: dict
    decision: str
    note: str


def _first_undiagnosed() -> int | None:
    rows = _query("SELECT id FROM incidents WHERE status = 'open' AND diagnosis IS NULL "
                  "ORDER BY id LIMIT 1", ())
    return rows[0]["id"] if rows else None


def monitor_node(state: State) -> dict:
    scan(state["as_of"])
    return {"incident_id": _first_undiagnosed()}


def diagnose_node(state: State) -> dict:
    try:
        r = diagnose(state["incident_id"])
    except Exception as exc:                       # graceful fallback: a human takes over
        return {"note": f"diagnosis failed: {type(exc).__name__}"}
    if not r["diagnosis"]:
        return {"note": "diagnosis could not be parsed"}
    return {}


def traceability_node(state: State) -> dict:
    return {"proposal": propose_containment(state["incident_id"])}


def report_node(state: State) -> dict:
    write_report(state["incident_id"])
    return {}


def approval_node(state: State) -> dict:
    # interrupt() pauses the graph. On resume this node re-runs from the top,
    # so everything BEFORE interrupt() must be free of side effects.
    p = state["proposal"]
    answer = interrupt({
        "incident_id": state["incident_id"],
        "question": f"Hold {p['n_vins']} VINs and stop {p['station_id']}?",
        "n_nok": p["n_nok"], "window": [p["window_start"], p["window_end"]],
    })
    decision = "approved" if answer.get("approved") else "rejected"
    conn = _connect()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for a in p["actions"]:
                cur.execute("INSERT INTO approvals (incident_id, action, decision, decided_by) "
                            "VALUES (%s, %s, %s, %s)",
                            (state["incident_id"], a["action"], decision,
                             answer.get("by", "unknown")))
            cur.execute("INSERT INTO agent_actions (incident_id, agent, tool, args, result) "
                        "VALUES (%s, 'supervisor', 'human_approval', %s, %s)",
                        (state["incident_id"], json.dumps({"by": answer.get("by")}),
                         json.dumps({"decision": decision})))
    finally:
        conn.close()
    return {"decision": decision}


def after_monitor(state: State) -> str:
    return "diagnose" if state.get("incident_id") else END


def after_diagnose(state: State) -> str:
    return END if state.get("note") else "traceability"


def build_graph():
    g = StateGraph(State)
    g.add_node("monitor", monitor_node)
    g.add_node("diagnose", diagnose_node)
    g.add_node("traceability", traceability_node)
    g.add_node("report", report_node)
    g.add_node("approval", approval_node)
    g.add_edge(START, "monitor")
    g.add_conditional_edges("monitor", after_monitor, {"diagnose": "diagnose", END: END})
    g.add_conditional_edges("diagnose", after_diagnose, {"traceability": "traceability", END: END})
    g.add_edge("traceability", "report")
    g.add_edge("report", "approval")
    g.add_edge("approval", END)
    return g.compile(checkpointer=MemorySaver())