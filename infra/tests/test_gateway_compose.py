"""infra/gateway (gateway de campo num host da fábrica): o compose de exemplo é válido com o gateway.env.example,
não publica portas, roda endurecido e guarda o estado no volume.

    .venv/bin/python -m pytest -q infra/tests/test_gateway_compose.py
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GW = ROOT / "infra" / "gateway"

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="precisa de docker compose")


def _config(tmp_path) -> dict:
    d = tmp_path / "infra" / "gateway"
    d.mkdir(parents=True)
    shutil.copy(GW / "docker-compose.yml", d / "docker-compose.yml")
    shutil.copy(GW / "gateway.env.example", d / "gateway.env")
    (tmp_path / "edge").mkdir()
    out = subprocess.run(["docker", "compose", "-f", str(d / "docker-compose.yml"), "config", "--format", "json"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_gateway_compose_is_valid_and_hardened(tmp_path):
    svc = _config(tmp_path)["services"]["gateway"]
    assert not svc.get("ports")                                   # só conexões de saída
    assert svc["read_only"] is True and svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]
    env = svc["environment"]
    assert env["GATEWAY_CREDENTIALS_PATH"] == "/data/gateway_credentials.json"
    assert env["MQTT_TLS"] == "true" and env["MQTT_BROKER_PORT"] == "8883"
    assert env["SERVER_URL"].startswith("https://") and env["ENROLL_CODE"] == ""
    vols = {v["target"]: v for v in svc["volumes"]}
    assert vols["/data"]["type"] == "volume" and vols["/certs"]["read_only"] is True
    assert svc["build"]["context"].endswith("/edge")


def test_env_example_has_no_secrets():
    txt = (GW / "gateway.env.example").read_text(encoding="utf-8")
    for key in ("ENROLL_CODE", "MQTT_PASSWORD", "ENCRYPTION_KEY", "MQTT_USERNAME"):
        line = next(ln for ln in txt.splitlines() if ln.startswith(f"{key}="))
        assert line == f"{key}=", line
