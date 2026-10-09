"""    uv run python agents/run_supervisor.py --as-of 2026-10-04T10:26:00Z"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.types import Command  # noqa: E402

from agents.supervisor import build_graph  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", required=True)
    args = ap.parse_args()

    graph = build_graph()
    config = {"configurable": {"thread_id": f"run-{args.as_of}"}}
    result = graph.invoke({"as_of": args.as_of}, config)

    if "__interrupt__" in result:
        info = result["__interrupt__"][0].value
        print("\n*** HUMAN APPROVAL NEEDED ***")
        print(f"incident {info['incident_id']}: {info['question']}")
        print(f"station {info['station_id']} | already NOK: {info['n_nok']} | "
              f"window: {info['window'][0]} -> {info['window'][1]}")
        ok = input("Approve? [y/n]: ").strip().lower() == "y"
        result = graph.invoke(Command(resume={"approved": ok, "by": "abhishek"}), config)
        print("decision:", result.get("decision"))
        print("report:", result.get("report_path") or result.get("note"))
    else:
        print("no approval needed.", result.get("note") or "no new incident")


if __name__ == "__main__":
    main()