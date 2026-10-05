"""Diagnosis agent (ReAct): the LLM chooses which tools to call to find the root cause.
Step 1 gathers evidence with tools and writes a plain-text conclusion.
Step 2 (no tools) converts that conclusion to JSON."""
import json
import os
import time
from datetime import timezone

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_groq import ChatGroq

from agents.analyst import ROOT_CAUSES
from agents.mes_tools import (Clock, check_spc_tool, query_alarms_tool,
                              query_downtime_tool, query_tool_changes_tool)
from agents.tools import _connect
from agents.util import invoke_with_retries, is_transient, parse_json_object, text_of

load_dotenv()

DIAG_TOOLS = [check_spc_tool, query_tool_changes_tool, query_downtime_tool, query_alarms_tool]

SYSTEM_PROMPT = f"""You are a root-cause analyst for an automotive assembly line.
You are given one incident: a station and signal that the monitor flagged as drifting.

Rules:
- Gather evidence with the tools before concluding. Never claim anything a tool result does not support.
- Look for events shortly BEFORE the drift began: tool changes, alarms, downtime.
- Say "unknown" if the evidence does not point to a cause. Do not guess.
- Allowed root causes: {", ".join(ROOT_CAUSES)}.

When you have enough evidence, reply in plain text with exactly these four lines. Do NOT write JSON:
Root cause: <one allowed value>
Confidence: <high, medium or low>
Evidence: <short facts taken from tool results, separated by semicolons>
Next step: <one sentence>"""

FORMAT_PROMPT = """Convert the analyst conclusion below into ONE JSON object and output nothing else.
Keys: "root_cause" (one of: {causes}), "confidence" ("high", "medium" or "low"),
"evidence" (list of short strings), "recommended_next_step" (string).
Copy facts exactly. Add nothing new.

CONCLUSION:
{text}"""


def _iso(dt) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(incident_id: int) -> dict:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT station_id, signal, detected_at, evidence FROM incidents "
                        "WHERE id = %s", (incident_id,))
            s, sig, det, ev = cur.fetchone()
    finally:
        conn.close()
    return {"station_id": s, "signal": sig, "detected_at": _iso(det), "evidence": ev}


def _format_json(conclusion: str, retries: int = 3) -> dict | None:
    """Tools-off LLM call that turns the plain-text conclusion into a JSON dict."""
    llm = ChatGroq(model=os.environ["GROQ_MODEL"], temperature=0)
    prompt = FORMAT_PROMPT.format(causes=", ".join(ROOT_CAUSES), text=conclusion)
    for attempt in range(retries):
        try:
            return parse_json_object(text_of(llm.invoke(prompt)), "root_cause")
        except Exception as exc:
            if is_transient(str(exc)) and attempt < retries - 1:
                time.sleep(2 * 2 ** attempt)
                continue
            raise


def _audit_and_save(incident_id: int, messages, diagnosis: dict | None) -> None:
    """Write every tool call and result to agent_actions, then store the diagnosis."""
    calls, results = {}, {}
    for m in messages:
        for tc in getattr(m, "tool_calls", None) or []:
            calls[tc["id"]] = (tc["name"], tc["args"])
        if type(m).__name__ == "ToolMessage":
            results[m.tool_call_id] = text_of(m)
    conn = _connect()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for cid, (name, args) in calls.items():
                cur.execute(
                    "INSERT INTO agent_actions (incident_id, agent, tool, args, result) "
                    "VALUES (%s, 'diagnosis', %s, %s, %s)",
                    (incident_id, name, json.dumps(args),
                     json.dumps({"output": results.get(cid, "")[:4000]})))
            if diagnosis:
                cur.execute("UPDATE incidents SET root_cause = %s, diagnosis = %s WHERE id = %s",
                            (diagnosis.get("root_cause"), json.dumps(diagnosis), incident_id))
    finally:
        conn.close()


def diagnose(incident_id: int) -> dict:
    inc = _load(incident_id)
    Clock.now = inc["detected_at"]               # the agent cannot read past this time
    llm = ChatGroq(model=os.environ["GROQ_MODEL"], temperature=0)
    agent = create_agent(llm, tools=DIAG_TOOLS, system_prompt=SYSTEM_PROMPT)
    prompt = (f"Incident {incident_id}: {inc['station_id']} signal {inc['signal']} was flagged "
              f"as drifting at {inc['detected_at']}. Monitor evidence: "
              f"{json.dumps(inc['evidence'])}. Find the most likely root cause.")
    try:
        result, retries = invoke_with_retries(agent, prompt)       # step 1: tools on
    finally:
        Clock.now = None
    msgs = result["messages"]
    conclusion = next((text_of(m) for m in reversed(msgs)
                       if type(m).__name__ == "AIMessage" and text_of(m).strip()), "")

    diagnosis = _format_json(conclusion) if conclusion else None   # step 2: tools off
    if diagnosis and diagnosis.get("root_cause") not in ROOT_CAUSES:
        diagnosis["root_cause"] = "unknown"       # never store a value outside the label set
    _audit_and_save(incident_id, msgs, diagnosis)
    return {"diagnosis": diagnosis, "raw": conclusion[-600:], "retries": retries,
            "tools_used": [tc["name"] for m in msgs
                           for tc in (getattr(m, "tool_calls", None) or [])]}