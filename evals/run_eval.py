"""Scenario eval: replay each scenario, run the agent at checkpoints, grade vs ground truth.

ingest.py must be running in another terminal.
    uv run python evals/run_eval.py
    uv run python evals/run_eval.py --scenarios torque_drift --repeats 3
    uv run python evals/run_eval.py --gate        # exit code 1 if thresholds are missed
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from agents.analyst import ROOT_CAUSES, build_agent  # noqa: E402
from agents.mes_tools import Clock  # noqa: E402
from agents.tools import _connect  # noqa: E402

load_dotenv()

SCENARIO_DIR = ROOT / "scenarios"
RESULTS_DIR = ROOT / "evals" / "results"
SIM_START = datetime(2026, 10, 4, 10, 0, 0, tzinfo=timezone.utc)


# ---------- time helpers ----------
def at(minute: float) -> datetime:
    return SIM_START + timedelta(minutes=minute)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ---------- scenarios ----------
def load_scenarios(names):
    scenarios = []
    for path in sorted(SCENARIO_DIR.glob("*.yaml")):
        sc = yaml.safe_load(path.read_text())
        if names and sc["id"] not in names:
            continue
        gt = sc["ground_truth"]
        if gt["root_cause"] not in ROOT_CAUSES:
            raise ValueError(f"{path.name}: root_cause '{gt['root_cause']}' "
                             f"is not in ROOT_CAUSES (agents/analyst.py)")
        scenarios.append(sc)
    if not scenarios:
        raise SystemExit("no scenarios found")
    return scenarios


def checkpoints(sc) -> list[int]:
    gt = sc["ground_truth"]
    if gt["should_raise_incident"]:
        return list(range(20, int(gt["affected_window_min"][1]), 2))   # 20, 22, ... 30
    return [20, 30, 40, 50, 58]       # 58 is after the lone NOK at 10:57


def replay(name: str) -> None:
    subprocess.run([sys.executable, str(ROOT / "simulator" / "run_scenario.py"), name, "600"],
                   check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
    time.sleep(2)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM station_passes")
            n = cur.fetchone()[0]
    finally:
        conn.close()
    if n == 0:
        raise RuntimeError("no data ingested. Is mes/ingest.py running?")


def vins_in_window(station: str, start: datetime, end: datetime) -> set[str]:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT vin FROM station_passes "
                        "WHERE station_id = %s AND ts >= %s AND ts <= %s",
                        (station, start, end))
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


# ---------- running the agent ----------
def text_of(msg) -> str:
    c = msg.content
    if isinstance(c, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)
    return c or ""


def parse_answer(text: str):
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                break
    m = re.search(r"\{.*\}", text, re.S)          # fallback: multi-line JSON
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def is_transient(msg: str) -> bool:
    msg = msg.lower()
    return (("rate" in msg and "limit" in msg) or "429" in msg or "503" in msg
            or "output_parse_failed" in msg or "failed_generation" in msg)


def run_agent(agent, prompt: str, retries: int = 4):
    """Returns (result, retries_used). Retries transient API/model failures with backoff."""
    for attempt in range(retries):
        try:
            result = agent.invoke({"messages": [{"role": "user", "content": prompt}]},
                                  config={"recursion_limit": 30})        # step limit
            return result, attempt
        except Exception as exc:
            if is_transient(str(exc)) and attempt < retries - 1:
                print(f"    retry {attempt + 1}: {type(exc).__name__}")
                time.sleep(2 * 2 ** attempt)                              # 2s, 4s, 8s
                continue
            raise


def run_checkpoint(agent, minute: int) -> dict:
    Clock.now = iso(at(minute))
    prompt = (f"Current time is {Clock.now}. Review all stations on LINE-1 and report "
              f"whether there is a real incident right now.")
    t0 = time.time()
    try:
        result, retries_used = run_agent(agent, prompt)
    except Exception as exc:
        return {"minute": minute, "answer": None, "tools": [], "tokens": 0, "retries": 0,
                "latency_s": round(time.time() - t0, 1),
                "error": f"{type(exc).__name__}: {exc}"[:300]}
    msgs = result["messages"]
    tools, tokens = [], 0
    for m in msgs:
        for tc in getattr(m, "tool_calls", None) or []:
            tools.append(tc["name"])
        um = getattr(m, "usage_metadata", None)
        if um:
            tokens += um.get("total_tokens", 0)
    return {"minute": minute, "answer": parse_answer(text_of(msgs[-1])), "tools": tools,
            "tokens": tokens, "retries": retries_used,
            "latency_s": round(time.time() - t0, 1), "error": None}


# ---------- grading ----------
def grade(sc, cps) -> dict:
    gt = sc["ground_truth"]
    should = gt["should_raise_incident"]
    station = gt["affected_station"]

    detected_min, false_alarms = None, 0
    for cp in cps:
        a = cp["answer"]
        if not (a and a.get("incident")):
            continue
        if should and a.get("station_id") == station:
            detected_min = detected_min if detected_min is not None else cp["minute"]
        else:
            false_alarms += 1

    out = {"scenario": sc["id"], "should_raise_incident": should,
           "checkpoints": cps, "n_checkpoints": len(cps),
           "detected": detected_min is not None, "detected_at_min": detected_min,
           "ttd_min": None, "false_alarm_checkpoints": false_alarms,
           "root_cause_correct": None, "precision": None, "recall": None, "f1": None}
    if not should:
        return out

    fault_start, gt_end = gt["affected_window_min"]
    if detected_min is not None:
        out["ttd_min"] = detected_min - fault_start

    last = cps[-1]
    a = last["answer"] or {}
    out["root_cause_correct"] = (a.get("incident") is True and
                                 a.get("root_cause") == gt["root_cause"])

    # containment, graded at the last checkpoint against VINs affected so far
    T = at(last["minute"])
    truth = vins_in_window(station, at(fault_start), min(at(gt_end), T))
    pred = set()
    w = a.get("affected_window")
    if a.get("incident") and a.get("station_id") == station and isinstance(w, dict):
        try:
            pred = vins_in_window(station, parse_iso(w["start"]), min(parse_iso(w["end"]), T))
        except (KeyError, ValueError, TypeError):
            pred = set()
    tp = len(pred & truth)
    precision = tp / len(pred) if pred else 0.0
    recall = tp / len(truth) if truth else 1.0
    out.update(precision=round(precision, 3), recall=round(recall, 3),
               f1=round(2 * precision * recall / (precision + recall), 3)
               if precision + recall else 0.0)
    return out


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def summarize(results) -> dict:
    inc = [r for r in results if r["should_raise_incident"]]
    non = [r for r in results if not r["should_raise_incident"]]
    cps = [cp for r in results for cp in r["checkpoints"]]
    return {
        "runs": len(results),
        "detection_rate": mean([1.0 if r["detected"] else 0.0 for r in inc]),
        "mean_ttd_sim_min": mean([r["ttd_min"] for r in inc]),
        "root_cause_accuracy": mean([1.0 if r["root_cause_correct"] else 0.0 for r in inc]),
        "containment_precision": mean([r["precision"] for r in inc]),
        "containment_recall": mean([r["recall"] for r in inc]),
        "containment_f1": mean([r["f1"] for r in inc]),
        "false_alarm_scenario_rate": mean([1.0 if r["false_alarm_checkpoints"] else 0.0
                                           for r in non]),
        "false_alarm_checkpoints": sum(r["false_alarm_checkpoints"] for r in results),
        "total_checkpoints": len(cps),
        "parse_failures": sum(1 for c in cps if not c["error"] and c["answer"] is None),
        "errors": sum(1 for c in cps if c["error"]),
        "total_retries": sum(c.get("retries", 0) for c in cps),
        "avg_tokens_per_checkpoint": mean([c["tokens"] for c in cps]),
        "avg_latency_s": mean([c["latency_s"] for c in cps]),
        "avg_tool_calls": mean([len(c["tools"]) for c in cps]),
    }


def gate(summary, args) -> list[str]:
    fails = []
    if summary["detection_rate"] is not None and summary["detection_rate"] < args.min_detection:
        fails.append(f"detection_rate {summary['detection_rate']} < {args.min_detection}")
    if (summary["root_cause_accuracy"] is not None
            and summary["root_cause_accuracy"] < args.min_root_cause):
        fails.append(f"root_cause_accuracy {summary['root_cause_accuracy']} < {args.min_root_cause}")
    if (summary["false_alarm_scenario_rate"] is not None
            and summary["false_alarm_scenario_rate"] > args.max_false_alarm):
        fails.append(f"false_alarm_scenario_rate {summary['false_alarm_scenario_rate']} "
                     f"> {args.max_false_alarm}")
    return fails


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", nargs="*", help="scenario ids (default: all)")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between LLM calls")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--min-detection", type=float, default=1.0)
    ap.add_argument("--min-root-cause", type=float, default=1.0)
    ap.add_argument("--max-false-alarm", type=float, default=0.0)
    args = ap.parse_args()

    scenarios = load_scenarios(args.scenarios)
    agent = build_agent()
    results = []

    for rep in range(args.repeats):
        for sc in scenarios:
            print(f"\n=== {sc['id']} (run {rep + 1}/{args.repeats}) ===")
            replay(sc["id"])
            cps = []
            for minute in checkpoints(sc):
                cp = run_checkpoint(agent, minute)
                a = cp["answer"]
                label = (f"incident={a.get('incident')} station={a.get('station_id')} "
                         f"cause={a.get('root_cause')}" if a
                         else ("ERROR " + cp["error"] if cp["error"] else "PARSE_FAIL"))
                print(f"  {iso(at(minute))[11:16]}  {label}  "
                      f"tools={len(cp['tools'])} tokens={cp['tokens']} "
                      f"retries={cp['retries']} {cp['latency_s']}s")
                cps.append(cp)
                time.sleep(args.pause)
            r = grade(sc, cps)
            r["run"] = rep + 1
            results.append(r)
    Clock.now = None

    summary = summarize(results)
    print("\n=== SUMMARY ===")
    for k, v in summary.items():
        print(f"{k:30} {v}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{stamp}.json"
    path.write_text(json.dumps({"model": os.environ.get("GROQ_MODEL"),
                                "commit": git_commit(), "summary": summary,
                                "results": results}, indent=2, default=str))
    print(f"\nsaved {path.relative_to(ROOT)}")

    if args.gate:
        fails = gate(summary, args)
        if fails:
            print("GATE FAILED:", *fails, sep="\n  ")
            sys.exit(1)
        print("GATE PASSED")


if __name__ == "__main__":
    main()