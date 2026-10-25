"""QA harness: run the API with the maintenance clock frozen at an instant (default: scenario E `evaluate_at`).

Used by the Sprint 4 acceptance e2e (scenario_e_ui.verify.mjs): the DB is seeded by
`FM_TEST_DB=fm_test_qa_e FM_TEST_KEEP_DB=1 pytest server/tests/test_maintenance_scenario_e.py`, then

    cd server && set -a && . ../.env && set +a && POSTGRES_DB=fm_test_qa_e \
        ../.venv/bin/python ../e2e/sprint4/fixed_clock_server.py --port 8110

Only `src.services.clock.now` is patched (exactly what the scenario E test does); nothing in production code changes.
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.getcwd())

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8110)
ap.add_argument("--at", default="2026-12-01T12:00:00+00:00")
args = ap.parse_args()

from src.services import clock  # noqa: E402

FIXED = datetime.fromisoformat(args.at)
clock.now = lambda: FIXED

import uvicorn  # noqa: E402
from src.main import app  # noqa: E402

uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
