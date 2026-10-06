"""edge/deploy/install.sh (alternativa systemd ao Docker) num DESTDIR de teste: arquivos, permissões, idempotência."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

EDGE = Path(__file__).resolve().parents[1]
SCRIPT = EDGE / "deploy" / "install.sh"
UNIT = EDGE / "deploy" / "systemd" / "factoryconnect-gateway.service"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="precisa de bash")


def _run(dest, *args):
    return subprocess.run(["bash", str(SCRIPT), "--no-systemd", "--no-venv", *args], capture_output=True, text=True,
                          env={**os.environ, "DESTDIR": str(dest)}, timeout=60)


def _mode(p) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


def test_install_into_destdir_is_idempotent_and_keeps_config(tmp_path):
    r = _run(tmp_path, "--server", "https://app.example.com", "--code", "AAAA-BBBB-CCCC", "--mqtt-host", "mqtt.ex")
    assert r.returncode == 0, r.stderr
    edge = tmp_path / "opt/factoryconnect/edge"
    assert (edge / "src/gateway.py").exists() and (edge / "src/enrollment.py").exists()
    assert not list(edge.rglob("__pycache__")) and not (edge / "tests").exists()
    env = tmp_path / "etc/factoryconnect/gateway.env"
    txt = env.read_text()
    assert "SERVER_URL=https://app.example.com" in txt and "ENROLL_CODE=AAAA-BBBB-CCCC" in txt
    assert "MQTT_BROKER_HOST=mqtt.ex" in txt and "MQTT_TLS=true" in txt
    assert _mode(env) == 0o640 and _mode(env.parent) == 0o750
    unit = tmp_path / "etc/systemd/system/factoryconnect-gateway.service"
    assert unit.read_text() == UNIT.read_text()
    # 2ª execução (atualização): mantém a configuração editada, só troca o código de cadastro
    env.write_text(txt.replace("MQTT_BROKER_PORT=8883", "MQTT_BROKER_PORT=18883"))
    r = _run(tmp_path, "--code", "DDDD-EEEE-FFFF")
    assert r.returncode == 0, r.stderr
    txt2 = env.read_text()
    assert "MQTT_BROKER_PORT=18883" in txt2 and "ENROLL_CODE=DDDD-EEEE-FFFF" in txt2
    assert "SERVER_URL=https://app.example.com" in txt2


def test_custom_prefix_rewrites_unit_paths(tmp_path):
    assert _run(tmp_path, "--prefix", "/srv/fc").returncode == 0
    unit = (tmp_path / "etc/systemd/system/factoryconnect-gateway.service").read_text()
    assert "ExecStart=/srv/fc/venv/bin/python -m src.gateway" in unit and "/opt/factoryconnect" not in unit
    assert (tmp_path / "srv/fc/edge/src/gateway.py").exists()


def test_refuses_plain_http_server_and_uninstalls(tmp_path):
    r = _run(tmp_path, "--server", "http://10.0.0.5:8000")
    assert r.returncode == 2 and "https" in r.stderr
    assert _run(tmp_path).returncode == 0
    r = _run(tmp_path, "--uninstall")
    assert r.returncode == 0 and not (tmp_path / "opt/factoryconnect").exists()
    assert (tmp_path / "etc/factoryconnect/gateway.env").exists()          # configuração mantida


def test_unit_file_hardening_and_state_dir():
    u = UNIT.read_text()
    for line in ("User=factoryconnect", "StateDirectory=factoryconnect", "StateDirectoryMode=0700", "UMask=0077",
                 "NoNewPrivileges=yes", "ProtectSystem=strict", "RestartPreventExitStatus=2 3",
                 "Environment=GATEWAY_CREDENTIALS_PATH=/var/lib/factoryconnect/gateway_credentials.json",
                 "EnvironmentFile=/etc/factoryconnect/gateway.env"):
        assert line in u, line
    if shutil.which("systemd-analyze"):
        out = subprocess.run(["systemd-analyze", "verify", "--man=no", str(UNIT)], capture_output=True, text=True)
        # fora de um host instalado só pode faltar o executável do venv
        problems = [ln for ln in (out.stdout + out.stderr).splitlines()
                    if ln.strip() and "is not executable" not in ln and "No such file" not in ln]
        assert not problems, problems
