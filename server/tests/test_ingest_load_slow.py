"""Sprint 5b — load test of the MQTT ingestion through the real path (slow: FM_SLOW=1).

Runs e2e/sprint5b/ingest_load.py (own broker in docker with infra/mosquitto/mosquitto.conf, own server process
and database): golden F1 readings published at maximum speed vs a slow replay → Influx count == published,
identical predictive blocks (== F1) and usage, no drops / dead letters, live latency p95 < 1 s while the
predictive worker runs. FM_LOAD_W blocks of 10 000 cycles (default 5 → 100 001 readings per replay).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("FM_SLOW") != "1", reason="load test: set FM_SLOW=1 (takes ~15-30 min)")
def test_f1_at_maximum_speed_loses_nothing_and_matches_a_slow_replay():
    env = {**os.environ, "W": os.environ.get("FM_LOAD_W", "5"), "SLOW_RATE": os.environ.get("FM_LOAD_SLOW_RATE", "500"),
           "LIVE_S": "60"}
    r = subprocess.run([sys.executable, "-u", str(ROOT / "e2e/sprint5b/ingest_load.py")], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=5400)
    print(r.stdout[-6000:])
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
