"""Reporting agent: drafts an 8D report from facts, critiques it, then revises (reflection)."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402
from langchain_groq import ChatGroq  # noqa: E402

from agents.tools import (_connect, _query, query_alarms, query_downtime,  # noqa: E402
                          query_tool_changes)
from agents.util import is_transient, parse_json_object, text_of  # noqa: E402

load_dotenv()
REPORT_DIR = Path(__file__).resolve().parent.parent / "reports"


def _llm(prompt: str, retries: int = 4) -> str:
    llm = ChatGroq(model=os.environ["GROQ_MODEL"], temperature=0)
    for attempt in range(retries):
        try:
            return text_of(llm.invoke(prompt))
        except Exception as exc:
            if is_transient(str(exc)) and attempt < retries - 1:
                time.sleep(2 * 2 ** attempt)
                continue
            raise


def build_facts(incident_id: int) -> dict:
    inc = _query("SELECT id, station_id, machine_id, signal, detected_at, root_cause, "
                 "diagnosis, containment, evidence FROM incidents WHERE id = %s",
                 (incident_id,))[0]
    if not inc["diagnosis"]:
        raise ValueError(f"incident {incident_id} has no diagnosis yet")
    m, det = inc["machine_id"], inc["detected_at"]
    c = inc["containment"] or {}
    return {
        "incident_id": inc["id"], "station_id": inc["station_id"], "machine_id": m,
        "signal": inc["signal"], "detected_at": det, "monitor_evidence": inc["evidence"],
        "diagnosis": inc["diagnosis"],
        "tool_changes": query_tool_changes(m, det, 120),
        "alarms": query_alarms(m, det, 120),
        "downtime": query_downtime(m, det, 120),
        "containment_proposal": {k: c.get(k) for k in
                                 ("window_start", "window_end", "window_basis", "n_vins",
                                  "n_nok", "nok_vins", "vins_to_hold", "actions")},
        "human_decision": _query("SELECT action, decision, decided_by, ts FROM approvals "
                                 "WHERE incident_id = %s ORDER BY id", (incident_id,)),
        "audit_log": [{"agent": a["agent"], "tool": a["tool"], "ts": a["ts"]}
                      for a in _query("SELECT agent, tool, ts FROM agent_actions "
                                      "WHERE incident_id = %s ORDER BY id", (incident_id,))],
    }


DRAFT_PROMPT = """Write an 8D problem-solving report in Markdown for an automotive assembly line incident.

STRICT RULES:
- Use ONLY the facts in FACTS. Do not invent names, times, numbers, part numbers or causes.
- D1 (team) and D5 to D8 (corrective action, validation, prevention, closure) cannot be known:
  write "Pending - requires human input" for each.
- D3 containment: state the proposal and the human decision exactly as given in
  human_decision. There is no system that physically executes holds or stops, so never say
  vehicles were held or the machine was stopped. Say "approved" or "rejected" only as recorded.
- D4 must state the root cause with its evidence and confidence exactly as given.
- Mention the machine (machine_id) and station.
- Keep every section short. Sections: D1 to D8 with those headings.

FACTS:
{facts}
"""

CRITIQUE_PROMPT = """You are a strict reviewer. Compare the REPORT with the FACTS.
List every statement in the report that is NOT supported by the facts (invented detail,
wrong number, containment described as physically executed, overstated certainty), and every
required item that is missing (root cause, evidence, containment proposal, human decision,
pending sections).
Reply with ONLY one JSON object:
{{"verdict": "pass" or "revise", "issues": ["short description", ...]}}

FACTS:
{facts}

REPORT:
{report}
"""

REVISE_PROMPT = """Rewrite the REPORT to fix every issue listed. Use ONLY the FACTS.
Keep the same 8D structure. Output only the corrected Markdown report.

ISSUES:
{issues}

FACTS:
{facts}

REPORT:
{report}
"""


def write_report(incident_id: int) -> dict:
    facts = json.dumps(build_facts(incident_id), indent=1, default=str)
    draft = _llm(DRAFT_PROMPT.format(facts=facts))
    review = parse_json_object(_llm(CRITIQUE_PROMPT.format(facts=facts, report=draft)),
                               "verdict") or {"verdict": "unknown", "issues": []}
    final = draft
    if review["verdict"] == "revise" and review["issues"]:
        final = _llm(REVISE_PROMPT.format(issues="\n".join(f"- {i}" for i in review["issues"]),
                                          facts=facts, report=draft))

    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"incident_{incident_id}.md"
    path.write_text(final, encoding="utf-8")

    conn = _connect()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE incidents SET report = %s, report_review = %s WHERE id = %s",
                        (final, json.dumps(review), incident_id))
            cur.execute("INSERT INTO agent_actions (incident_id, agent, tool, args, result) "
                        "VALUES (%s, 'reporting', 'write_8d_report', %s, %s)",
                        (incident_id, json.dumps({"incident_id": incident_id}),
                         json.dumps({"verdict": review["verdict"],
                                     "n_issues": len(review["issues"])})))
    finally:
        conn.close()
    return {"path": str(path), "review": review, "draft_changed": final != draft}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("incident_id", type=int)
    out = write_report(ap.parse_args().incident_id)
    print("saved:", out["path"])
    print("review verdict:", out["review"]["verdict"], "| issues:", out["review"]["issues"])