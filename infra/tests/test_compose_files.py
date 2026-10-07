"""Volume de arquivos do servidor em produção (layouts do mapa, FILES_DIR) e variáveis do demo-bot.

    .venv/bin/python -m pytest -q infra/tests/test_compose_files.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "infra/docker-compose.prod.yml"


def _config(**env) -> dict:
    cmd = ["docker", "compose", "--env-file", str(ROOT / ".env.prod.example"), "-f", str(COMPOSE),
           "config", "--format", "json"]
    return json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True,
                                     env={**os.environ, **env}).stdout)


@pytest.mark.skipif(shutil.which("docker") is None, reason="precisa de docker compose")
def test_server_mounts_files_dir_on_a_named_volume():
    cfg = _config()
    srv = cfg["services"]["server"]
    files_dir = srv["environment"]["FILES_DIR"]
    assert files_dir.startswith("/")                         # absoluto: não depende do WORKDIR
    mounts = {v["target"]: v for v in srv.get("volumes", [])}
    assert files_dir in mounts, "FILES_DIR sem volume: as plantas baixas somem a cada redeploy"
    assert mounts[files_dir]["type"] == "volume" and mounts[files_dir]["source"] == "server-files"
    assert "server-files" in cfg["volumes"]


@pytest.mark.skipif(shutil.which("docker") is None, reason="precisa de docker compose")
def test_server_receives_the_virtual_plant_control_token():
    env = _config(DEMO_VIRTUAL_PLANT_CONTROL_URL="http://demo-plant:7070",
                  DEMO_VIRTUAL_PLANT_CONTROL_TOKEN="t" * 48)["services"]["server"]["environment"]
    assert env["DEMO_VIRTUAL_PLANT_CONTROL_URL"] == "http://demo-plant:7070"
    assert env["DEMO_VIRTUAL_PLANT_CONTROL_TOKEN"] == "t" * 48


def test_image_creates_files_dir_owned_by_app_and_never_ships_local_files():
    df = (ROOT / "server/Dockerfile").read_text()
    assert re.search(r"mkdir -p /app/data/files .*chown -R app /app", df)
    assert "data/" in (ROOT / "server/.dockerignore").read_text().split()


def test_env_examples_document_the_sprint6_settings():
    need = ["FILES_DIR", "GATEWAY_COMMAND_TTL_S", "READINESS_WINDOW_MIN", "DEMO_TENANT_SLUG", "DEMO_SESSION_HOURS",
            "DEMO_RATE_LIMIT_PER_HOUR", "DEMO_AI_QUESTIONS_PER_SESSION", "DEMO_PLANT_STOPPED_AFTER_S",
            "DEMO_BOT_ENABLED", "DEMO_BOT_TICK_S", "DEMO_BOT_REPLACE_AFTER_S", "DEMO_BOT_FAILURE_BACKOFF_S",
            "DEMO_VIRTUAL_PLANT_CONTROL_URL", "DEMO_VIRTUAL_PLANT_CONTROL_TOKEN", "REPORTS_ENABLED", "REPORTS_TICK_S",
            "REPORTS_ATTACHMENT_MAX_BYTES", "REPORTS_CATCHUP_DAYS", "LAYOUT_IMAGE_MAX_BYTES"]
    prod_need = need + ["DEMO_GATEWAY_MQTT_USERNAME", "DEMO_GATEWAY_MQTT_PASSWORD", "DEMO_GATEWAY_ENROLL_CODE",
                        "DEMO_GATEWAY_ID", "DEMO_GATEWAY_TOKEN"]
    for name, keys in ((".env.example", need), (".env.prod.example", prod_need)):
        text = (ROOT / name).read_text(encoding="utf-8")
        missing = [k for k in keys if not re.search(rf"^#?\s*{k}=", text, re.M)]
        assert not missing, f"{name}: {missing}"
