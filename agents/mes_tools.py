"""LangChain tool wrappers. Logic stays in agents/tools.py.
Every time argument is clamped to Clock.now, so the agent cannot read the future.
Tools return JSON strings (an empty list becomes a non-empty string)."""
import json
from datetime import datetime, timezone

from langchain_core.tools import tool

from agents.tools import (check_spc, find_vins_at_machine, query_alarms,
                          query_downtime, query_tool_changes)


class Clock:
    """The 'current time' of the incident being analysed (None = no limit)."""
    now: str | None = None


def _dt(ts: str) -> datetime:
    d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _clamp(ts: str) -> str:
    if Clock.now and _dt(ts) > _dt(Clock.now):
        return Clock.now
    return ts


def _out(data, empty_msg: str = "no records found") -> str:
    if isinstance(data, list) and not data:
        return json.dumps({"result": [], "note": empty_msg})
    return json.dumps(data, default=str)


@tool
def check_spc_tool(machine_id: str, signal: str, as_of: str, window: int = 8) -> str:
    """SPC check on one machine signal using the last `window` passes up to `as_of`
    (ISO time, e.g. 2026-10-04T10:25:00Z). Returns a verdict: ok, drift_suspected,
    sensor_suspect (frozen value) or insufficient_data, with mean, slope and a reason."""
    return _out(check_spc(machine_id, signal, _clamp(as_of), window))


@tool
def query_tool_changes_tool(machine_id: str, as_of: str, lookback_min: int = 60) -> str:
    """List tool changes (e.g. nut runner swapped) on a machine in the last
    `lookback_min` minutes up to `as_of` (ISO time). Use to look for a cause of a drift."""
    return _out(query_tool_changes(machine_id, _clamp(as_of), lookback_min),
                "no tool changes in this window")


@tool
def query_downtime_tool(machine_id: str, as_of: str, lookback_min: int = 60) -> str:
    """List downtime events (stops, jams) on a machine in the last
    `lookback_min` minutes up to `as_of` (ISO time)."""
    return _out(query_downtime(machine_id, _clamp(as_of), lookback_min),
                "no downtime events in this window")


@tool
def query_alarms_tool(machine_id: str, as_of: str, lookback_min: int = 60) -> str:
    """List alarms raised on a machine in the last `lookback_min` minutes up to `as_of`."""
    return _out(query_alarms(machine_id, _clamp(as_of), lookback_min),
                "no alarms in this window")


@tool
def find_vins_at_machine_tool(machine_id: str, start: str, end: str) -> str:
    """List VINs processed by a machine between `start` and `end` (ISO times),
    with measured values and OK/NOK result."""
    return _out(find_vins_at_machine(machine_id, _clamp(start), _clamp(end)),
                "no passes in this window")


ALL_TOOLS = [check_spc_tool, query_tool_changes_tool, query_downtime_tool,
             query_alarms_tool, find_vins_at_machine_tool]