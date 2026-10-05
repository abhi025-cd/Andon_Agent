"""MES tools for the agents. Plain Python, no LLM."""
import os

import numpy as np
import psycopg2
from dotenv import load_dotenv

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


def check_spc(station_id: str, signal: str, as_of: str, window: int = 8) -> dict:
    """Look at the last `window` passes at or before `as_of` and return a verdict.
    Only data with ts <= as_of is used, so the agent never sees the future."""
    if signal not in NOMINAL:
        raise ValueError(f"unknown signal: {signal}")
    nominal_mean, sigma = NOMINAL[signal]

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT (measured->>%s)::float, result FROM station_passes "
                "WHERE station_id = %s AND ts <= %s AND measured ? %s "
                "ORDER BY ts DESC LIMIT %s",
                (signal, station_id, as_of, signal, window),
            )
            rows = cur.fetchall()[::-1]      # oldest -> newest
    finally:
        conn.close()

    out = {"verdict": None, "station_id": station_id, "signal": signal,
           "as_of": as_of, "n": len(rows), "mean": None, "std": None,
           "slope_per_pass": None, "nok_count": None}

    if len(rows) < window:
        out["verdict"] = "insufficient_data"
        return out

    values = np.array([r[0] for r in rows])
    nok_count = sum(1 for r in rows if r[1] == "NOK")
    mean, std = float(values.mean()), float(values.std())
    slope = float(np.polyfit(np.arange(len(values)), values, 1)[0])
    out.update(mean=round(mean, 2), std=round(std, 3),
               slope_per_pass=round(slope, 3), nok_count=nok_count)

    if values.max() == values.min():                        # frozen value
        out["verdict"] = "sensor_suspect"
    elif nok_count >= 4 or (abs(mean - nominal_mean) > 2 * sigma
                            and abs(slope) >= 0.1 * sigma):
        out["verdict"] = "drift_suspected"
    else:
        out["verdict"] = "ok"
    return out

from datetime import date, datetime
from psycopg2.extras import RealDictCursor


def _query(sql: str, params: tuple) -> list[dict]:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        out.append({k: (v.isoformat() if isinstance(v, (datetime, date)) else v)
                    for k, v in r.items()})
    return out


def query_tool_changes(station_id: str, as_of: str, lookback_min: int = 60) -> list[dict]:
    """Tool changes at a station in the `lookback_min` minutes up to `as_of`."""
    return _query(
        "SELECT ts, details FROM tool_changes "
        "WHERE station_id = %s AND ts <= %s::timestamptz "
        "AND ts > %s::timestamptz - make_interval(mins => %s) ORDER BY ts",
        (station_id, as_of, as_of, lookback_min))


def query_downtime(station_id: str, as_of: str, lookback_min: int = 60) -> list[dict]:
    """Downtime events at a station that started in the lookback window up to `as_of`."""
    return _query(
        "SELECT start_ts, end_ts, reason_code FROM downtime_events "
        "WHERE station_id = %s AND start_ts <= %s::timestamptz "
        "AND start_ts > %s::timestamptz - make_interval(mins => %s) ORDER BY start_ts",
        (station_id, as_of, as_of, lookback_min))


def query_alarms(station_id: str, as_of: str, lookback_min: int = 60) -> list[dict]:
    """Alarms at a station in the lookback window up to `as_of`."""
    return _query(
        "SELECT ts, code, severity FROM alarms "
        "WHERE station_id = %s AND ts <= %s::timestamptz "
        "AND ts > %s::timestamptz - make_interval(mins => %s) ORDER BY ts",
        (station_id, as_of, as_of, lookback_min))


def find_vins_at_station(station_id: str, start: str, end: str, limit: int = 200) -> list[dict]:
    """VINs that passed a station between `start` and `end`, with measured values and result."""
    return _query(
        "SELECT vin, ts, measured, result FROM station_passes "
        "WHERE station_id = %s AND ts >= %s::timestamptz AND ts <= %s::timestamptz "
        "ORDER BY ts LIMIT %s",
        (station_id, start, end, limit))