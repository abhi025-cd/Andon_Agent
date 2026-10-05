"""Monitor -> Diagnosis at a given simulated time.
    uv run python agents/run_pipeline.py --as-of 2026-10-04T10:26:00Z
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.diagnosis import diagnose  # noqa: E402
from agents.monitor import scan  # noqa: E402
from agents.tools import _connect  # noqa: E402


def undiagnosed() -> list[int]:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM incidents WHERE status = 'open' AND diagnosis IS NULL "
                        "ORDER BY id")
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", required=True)
    args = ap.parse_args()

    out = scan(args.as_of)
    print("Monitor verdicts:")
    for k, v in out["verdicts"].items():
        print(f"  {k:28} {v}")
    print("new incidents:", out["new_incidents"] or "none")
    print("sensor suspects (no incident):", out["sensor_suspects"] or "none")

    for iid in undiagnosed():
        print(f"\nDiagnosing incident {iid} ...")
        r = diagnose(iid)
        print("tools used:", r["tools_used"], "| retries:", r["retries"])
        if r["diagnosis"]:
            for k, v in r["diagnosis"].items():
                print(f"  {k}: {v}")
        else:
            print("  PARSE_FAIL. Raw:", r["raw"].replace("\n", " | "))


if __name__ == "__main__":
    main()