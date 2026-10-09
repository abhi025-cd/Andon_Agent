"""MES tools for the agents (machine level). Plain Python, no LLM."""
import os
from datetime import date, datetime

import numpy as np
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

load_dotenv()

# nominal mean and sigma per signal (same values the simulator uses)
NOMINAL = {
    "weld_current_kA": (9.5, 0.4),
    "booth_temp_C": (24.0, 1.0),
    "humidity_pct": (55.0, 5.0),
    "cycle_time_s": (52.0, 3.0),
    "torque_nm": (45.0, 1.5),
}


def _connect():
    return psycopg2.connect(
        host="localhost", port=5432,
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["POSTGRES_DB"],
    )


def _query(sql: str, params: tuple) -> list[dict]:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{k: (v.isoformat() if isinstance(v, (datetime, date)) else v)
             for k, v in r.items()} for r in rows]


def classify(values, results, nominal_mean: float, sigma: float, window: int = 8) -> dict:
    """Pure SPC decision on one window of readings (oldest -> newest). No database."""
    n = len(values)
    out = {"verdict": None, "n": n, "mean": None, "std": None, "slope_per_pass": None,
           "r": None, "nok_count": None, "reason": None}
    if n < window:
        out.update(verdict="insufficient_data", reason=f"only {n} of {window} readings")
        return out

    v = np.asarray(values, dtype=float)
    nok = sum(1 for r in results if r == "NOK")
    out.update(mean=round(float(v.mean()), 2), std=round(float(v.std()), 3), nok_count=nok)

    if v.max() == v.min():                       # frozen value
        out.update(verdict="sensor_suspect", slope_per_pass=0.0, r=0.0,
                   reason="all readings identical")
        return out

    x = np.arange(n)
    slope = float(np.polyfit(x, v, 1)[0])
    r = float(np.corrcoef(x, v)[0, 1])
    out.update(slope_per_pass=round(slope, 3), r=round(r, 3))

    if nok >= 4:
        out.update(verdict="drift_suspected", reason=f"{nok} NOK in last {n}")
    elif abs(v.mean() - nominal_mean) > 2 * sigma and abs(slope) >= 0.1 * sigma:
        out.update(verdict="drift_suspected", reason="mean shifted over 2 sigma and sloping")
    elif abs(r) >= 0.9 and abs(slope) * (n - 1) >= 1.5 * sigma:
        out.update(verdict="drift_suspected", reason="steady trend over 1.5 sigma")
    else:
        out.update(verdict="ok", reason="within normal behaviour")
    return out


def check_spc(machine_id: str, signal: str, as_of: str, window: int = 8) -> dict:
    """SPC check on one machine using the last `window` passes at or before `as_of`.
    Only data with ts <= as_of is used, so the agent never sees the future."""
    if signal not in NOMINAL:
        raise ValueError(f"unknown signal: {signal}")
    nominal_mean, sigma = NOMINAL[signal]

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT (measured->>%s)::float, result FROM station_passes "
                "WHERE machine_id = %s AND ts <= %s AND measured ? %s "
                "ORDER BY ts DESC LIMIT %s",
                (signal, machine_id, as_of, signal, window))
            rows = cur.fetchall()[::-1]          # oldest -> newest
    finally:
        conn.close()

    out = classify([r[0] for r in rows], [r[1] for r in rows], nominal_mean, sigma, window)
    out.update(machine_id=machine_id, signal=signal, as_of=as_of)
    return out


def query_tool_changes(machine_id: str, as_of: str, lookback_min: int = 60) -> list[dict]:
    """Tool changes on a machine in the `lookback_min` minutes up to `as_of`."""
    return _query(
        "SELECT ts, details FROM tool_changes "
        "WHERE machine_id = %s AND ts <= %s::timestamptz "
        "AND ts > %s::timestamptz - make_interval(mins => %s) ORDER BY ts",
        (machine_id, as_of, as_of, lookback_min))


def query_downtime(machine_id: str, as_of: str, lookback_min: int = 60) -> list[dict]:
    """Downtime events on a machine that started in the lookback window up to `as_of`."""
    return _query(
        "SELECT start_ts, end_ts, reason_code FROM downtime_events "
        "WHERE machine_id = %s AND start_ts <= %s::timestamptz "
        "AND start_ts > %s::timestamptz - make_interval(mins => %s) ORDER BY start_ts",
        (machine_id, as_of, as_of, lookback_min))


def query_alarms(machine_id: str, as_of: str, lookback_min: int = 60) -> list[dict]:
    """Alarms on a machine in the lookback window up to `as_of`."""
    return _query(
        "SELECT ts, code, severity FROM alarms "
        "WHERE machine_id = %s AND ts <= %s::timestamptz "
        "AND ts > %s::timestamptz - make_interval(mins => %s) ORDER BY ts",
        (machine_id, as_of, as_of, lookback_min))


def find_vins_at_machine(machine_id: str, start: str, end: str, limit: int = 200) -> list[dict]:
    """VINs processed by a machine between `start` and `end`, with measurements and result."""
    return _query(
        "SELECT vin, ts, measured, result FROM station_passes "
        "WHERE machine_id = %s AND ts >= %s::timestamptz AND ts <= %s::timestamptz "
        "ORDER BY ts LIMIT %s",
        (machine_id, start, end, limit))