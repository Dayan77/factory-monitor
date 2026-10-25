"""Alembic refuses to run when DATABASE_URL points at another DB than POSTGRES_* (QA lost the dev
database once: DATABASE_URL was set to a scratch DB, but the URL is built from POSTGRES_DB)."""
import os
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent


def _alembic(env_extra: dict) -> subprocess.CompletedProcess:
    env = {**os.environ, **env_extra}
    return subprocess.run([sys.executable, "-m", "alembic", "current"], cwd=SERVER, env=env,
                          capture_output=True, text=True, timeout=60)


def test_conflicting_database_url_aborts_before_connecting():
    r = _alembic({"DATABASE_URL": "postgresql://u:p@localhost:5432/fm_scratch_db",
                  "POSTGRES_DB": "factory_monitor_guard_test", "POSTGRES_HOST": "localhost",
                  "POSTGRES_PORT": "5432"})
    assert r.returncode != 0
    assert "DATABASE_URL" in r.stderr and "POSTGRES_DB" in r.stderr


def test_matching_database_url_is_accepted(_database):
    db = os.environ["POSTGRES_DB"]
    r = _alembic({"DATABASE_URL": f"postgresql+asyncpg://x:y@{os.environ['POSTGRES_HOST']}:"
                                  f"{os.environ['POSTGRES_PORT']}/{db}"})
    assert "DATABASE_URL aponta" not in r.stderr
    assert r.returncode == 0, r.stderr[-500:]
