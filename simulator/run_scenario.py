"""Reset the MES data, then replay one scenario. Run ingest.py in another terminal first."""
import os
import subprocess
import sys
import time

import psycopg2
from dotenv import load_dotenv

load_dotenv()
name = sys.argv[1]
speed = sys.argv[2] if len(sys.argv) > 2 else "600"

conn = psycopg2.connect(host="localhost", port=5432,
                        user=os.environ["POSTGRES_USER"],
                        password=os.environ["POSTGRES_PASSWORD"],
                        dbname=os.environ["POSTGRES_DB"])
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute("TRUNCATE station_passes, vehicles, alarms, downtime_events, "
                "tool_changes, incidents, agent_actions RESTART IDENTITY CASCADE;")
print("database reset")

subprocess.run([sys.executable, "simulator/simulator.py",
                "--scenario", name, "--speed", speed], check=True)
time.sleep(2)  # let the ingestion worker finish
print("scenario", name, "replayed")