"""Diretórios de estado do gateway (BUFFER_DB_PATH, COUNTER_DB_PATH, GATEWAY_CONFIG_CACHE, GATEWAY_CREDENTIALS_PATH):
diretório pai ausente é criado (0700) antes de abrir o SQLite; impossível criar → uma linha de erro clara e código 2,
sem traceback e sem tocar a rede (achado do E2E de QA: sqlite3.OperationalError + "Event loop is closed")."""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from edge.src import gateway as gw_mod
from edge.tests.conftest import free_port

ROOT = Path(__file__).resolve().parents[2]


def _paths(monkeypatch, base: Path, **extra) -> dict[str, Path]:
    p = {"buffer_db_path": base / "a" / "buf" / "edge_buffer.db",
         "counter_db_path": base / "b" / "cnt" / "edge_counters.db",
         "gateway_config_cache": base / "c" / "cfg" / "gateway_config.json",
         "gateway_credentials_path": base / "d" / "cred" / "gateway_credentials.json"}
    p.update(extra)
    for k, v in p.items():
        monkeypatch.setattr(gw_mod.settings, k, str(v))
    return p


def test_missing_parent_dirs_are_created_0700(tmp_path, monkeypatch):
    p = _paths(monkeypatch, tmp_path)
    created = gw_mod.prepare_state_dirs()
    assert sorted(created) == sorted(str(v.parent) for v in p.values())
    for v in p.values():
        assert v.parent.is_dir() and stat.S_IMODE(v.parent.stat().st_mode) & 0o077 == 0
    assert gw_mod.prepare_state_dirs() == []                      # idempotente; existentes ficam como estão


async def test_gateway_start_path_opens_sqlite_in_new_dirs(tmp_path, monkeypatch):
    p = _paths(monkeypatch, tmp_path)
    monkeypatch.setattr(gw_mod.settings, "encryption_key", "")
    gw_mod.prepare_state_dirs()
    g = gw_mod.EdgeGateway()
    await g.buffer.initialize()
    store = gw_mod.EdgeCounterStore(str(p["counter_db_path"]))
    assert p["buffer_db_path"].exists() and p["counter_db_path"].exists()
    store.close()
    await g.buffer.close()


def test_unusable_dir_is_one_clear_error(tmp_path, monkeypatch):
    blocker = tmp_path / "um-arquivo"
    blocker.write_text("x")
    _paths(monkeypatch, tmp_path, counter_db_path=blocker / "sub" / "edge_counters.db")
    with pytest.raises(gw_mod.StateDirError, match=r"COUNTER_DB_PATH=.*um-arquivo"):
        gw_mod.prepare_state_dirs()


def test_main_exits_2_with_one_log_line_and_no_traceback(tmp_path):
    blocker = tmp_path / "arquivo"
    blocker.write_text("x")
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "PYTHONPATH")}
    env.update({"BUFFER_DB_PATH": str(blocker / "data" / "edge_buffer.db"),
                "COUNTER_DB_PATH": str(tmp_path / "ok" / "edge_counters.db"),
                "GATEWAY_CONFIG_CACHE": str(tmp_path / "ok" / "gateway_config.json"),
                "GATEWAY_CREDENTIALS_PATH": str(tmp_path / "ok" / "cred.json"),
                # servidor e broker inexistentes: se a rede fosse tocada, o processo ficaria tentando de novo
                "SERVER_URL": f"http://127.0.0.1:{free_port()}", "ENROLL_CODE": "AAAA-BBBB-CCCC",
                "MQTT_BROKER_HOST": "127.0.0.1", "MQTT_BROKER_PORT": str(free_port()),
                "PLANT_MAP_PATH": "", "GATEWAY_CONFIG_URL": "", "ENCRYPTION_KEY": ""})
    r = subprocess.run([sys.executable, "-m", "edge.src.gateway"], cwd=ROOT, env=env, capture_output=True,
                       text=True, timeout=60)
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "state_dir_unusable" in out and "BUFFER_DB_PATH" in out
    assert "Traceback" not in out and "Event loop is closed" not in out
    assert "enroll" not in out.lower().replace("enroll_code", "")      # nem tentou o cadastro
