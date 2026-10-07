"""Profile `demo` do compose de produção (docs/sprint6.md §5, docs/operations.md §10): planta virtual da demo +
gateway da demo, isolados numa rede interna, com versão fixa, restart, healthcheck e falha fechada sem segredos.

    .venv/bin/python -m pytest -q infra/tests/test_compose_demo.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "infra/docker-compose.prod.yml"
pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="precisa de docker compose")


def _config(profile: str | None = "demo", **env) -> dict:
    cmd = ["docker", "compose", "--env-file", str(ROOT / ".env.prod.example"), "-f", str(COMPOSE)]
    if profile:
        cmd += ["--profile", profile]
    out = subprocess.run(cmd + ["config", "--format", "json"], capture_output=True, text=True, check=True,
                         env={**os.environ, **env}).stdout
    return json.loads(out)


def test_demo_services_only_with_the_profile():
    assert not {"demo-plant", "demo-gateway"} & set(_config(None)["services"])
    assert {"demo-plant", "demo-gateway"} <= set(_config()["services"])


def test_demo_services_are_pinned_restarted_and_healthchecked():
    svc = _config()["services"]
    for name in ("demo-plant", "demo-gateway"):
        s = svc[name]
        assert s["image"] == "factory-monitor-edge:0.6.0" and not s["image"].endswith(":latest")
        assert s["restart"] == "always"
        assert "ports" not in s                                  # nada publicado no VPS
    hc = svc["demo-plant"]["healthcheck"]["test"]
    assert "http://127.0.0.1:8091/health" in " ".join(hc)
    assert svc["demo-gateway"]["depends_on"]["demo-plant"]["condition"] == "service_healthy"
    assert 'ARG PYTHON_IMAGE=python:3.12.15-slim' in (ROOT / "edge/Dockerfile").read_text()   # base fixada


def test_demo_network_is_internal_and_isolates_the_plant():
    cfg = _config()
    svc, nets = cfg["services"], cfg["networks"]
    assert nets["fm-demo"]["internal"] is True
    assert set(svc["demo-plant"]["networks"]) == {"fm-demo"}               # não alcança banco nem internet
    assert set(svc["demo-gateway"]["networks"]) == {"fm-net", "fm-demo"}    # broker + servidor + planta
    assert "fm-demo" in svc["server"]["networks"]                           # demo-bot → POST /replace


def test_demo_gateway_uses_own_credentials_and_internal_broker():
    env = _config(DEMO_GATEWAY_ENROLL_CODE="ABCD-EFGH-IJKL", DEMO_GATEWAY_MQTT_USERNAME="gw-demo",
                  DEMO_GATEWAY_MQTT_PASSWORD="x" * 32)["services"]["demo-gateway"]["environment"]
    assert env["ENROLL_CODE"] == "ABCD-EFGH-IJKL"
    assert (env["MQTT_USERNAME"], env["MQTT_BROKER_HOST"], env["MQTT_BROKER_PORT"]) == ("gw-demo", "mosquitto", "1883")
    assert env["SERVER_URL"] == "http://server:8000" and env["VIRTUAL_PLANT_HOST"] == "demo-plant"


def _run(service: str, tmp_path: Path, env: dict) -> tuple[int, list[str], str]:
    """Roda o comando de partida do serviço com `python` trocado por um stub (como o test_compose_sim)."""
    cmd = _config()["services"][service]["command"][0].replace("$$", "$")
    calls = tmp_path / "calls"
    stub = tmp_path / "python"
    stub.write_text(f'#!/bin/sh\necho "$*" >> {calls}\n')
    stub.chmod(0o755)
    r = subprocess.run(["/bin/sh", "-c", cmd.replace("/data/", f"{tmp_path}/")], capture_output=True, text=True,
                       env={"PATH": f"{tmp_path}:{os.environ['PATH']}", **env})
    return r.returncode, calls.read_text().splitlines() if calls.exists() else [], r.stderr


def test_demo_plant_fails_closed_without_control_token(tmp_path):
    code, calls, err = _run("demo-plant", tmp_path, {})
    assert code == 2 and not calls and "DEMO_VIRTUAL_PLANT_CONTROL_TOKEN" in err
    code, calls, _ = _run("demo-plant", tmp_path, {"VP_CONTROL_TOKEN": "t" * 48})
    assert code == 0 and calls == [f"-m virtual_plant --demo --host 0.0.0.0 --control-host 0.0.0.0 "
                                   f"--state-file {tmp_path}/demo_state.json"]


def test_demo_gateway_fails_closed_without_credentials(tmp_path):
    code, calls, err = _run("demo-gateway", tmp_path, {})
    assert code == 2 and not calls and "mqtt-gateway-add" in err
    mqtt = {"MQTT_USERNAME": "gw-demo", "MQTT_PASSWORD": "p" * 32}
    code, calls, err = _run("demo-gateway", tmp_path, mqtt)
    assert code == 2 and "DEMO_GATEWAY_ENROLL_CODE" in err
    code, calls, _ = _run("demo-gateway", tmp_path, {**mqtt, "ENROLL_CODE": "ABCD-EFGH-IJKL"})
    assert code == 0 and calls == ["-m src.gateway"]
    # já cadastrado (credencial no volume): sobe sem o código
    (tmp_path / "gateway_credentials.json").write_text("{}")
    code, calls, _ = _run("demo-gateway", tmp_path, mqtt)
    assert code == 0 and calls[-1] == "-m src.gateway"


def test_operations_documents_the_demo():
    doc = (ROOT / "docs/operations.md").read_text(encoding="utf-8")
    for needle in ("## 10. Demo", "python -m src.demo bootstrap", "make mqtt-gateway-add GATEWAY=gw-demo",
                   "DEMO_GATEWAY_ENROLL_CODE", "DEMO_VIRTUAL_PLANT_CONTROL_URL", "demo-status"):
        assert needle in doc, needle
