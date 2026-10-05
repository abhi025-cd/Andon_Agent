"""The analyst agent: scans the line and decides whether there is a real incident."""
import os

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_groq import ChatGroq

from agents.mes_tools import ALL_TOOLS

load_dotenv()

# Ground-truth labels in scenarios/*.yaml must come from this list.
ROOT_CAUSES = [
    "tool_change_misconfiguration",
    "sensor_stuck_false_reading",
    "worn_electrode",
    "jam",
    "changeover_excursion",
    "comm_loss",
    "unknown",
    "no_fault",
]

SYSTEM_PROMPT = f"""You are a fault analyst for an automotive assembly line (LINE-1).

Stations and numeric signals:
- ST-010 body welding: weld_current_kA
- ST-020 paint booth: booth_temp_C, humidity_pct
- ST-030 door/cockpit fit: cycle_time_s
- ST-040 bolting: torque_nm
- ST-050 end-of-line test: no numeric signal

Rules:
- Gather evidence with the tools. Never claim anything a tool result does not support.
- Run check_spc_tool on every numeric signal before deciding.
- A sensor_suspect verdict (frozen value) is a probable sensor fault, not a process fault. Do not raise an incident for it.
- A single out-of-limit reading is not an incident.
- Raise an incident only when a real process drift or fault is supported by evidence.
- If an incident is real, look for causes: tool changes, alarms, downtime.
- Allowed root_cause values: {", ".join(ROOT_CAUSES)}.

Finish with ONE line of JSON, on its own line, and nothing after it:
{{"incident": true or false, "station_id": "ST-040" or null, "root_cause": "<allowed value>", "affected_window": {{"start": "<ISO>", "end": "<ISO>"}} or null}}
Use root_cause "no_fault" when there is no incident. affected_window.start is when
the fault began (estimate from the data) and end is the current time."""


def build_agent():
    llm = ChatGroq(model=os.environ["GROQ_MODEL"], temperature=0)
    return create_agent(llm, tools=ALL_TOOLS, system_prompt=SYSTEM_PROMPT)