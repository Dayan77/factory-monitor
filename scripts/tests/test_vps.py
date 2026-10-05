"""Testes da configuração de VPS dos scripts de deploy (sem rede, sem paramiko)."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from vps import DEFAULT_APP_DIR, DEFAULT_USER, VpsConfigError, load_config  # noqa: E402


def test_missing_host_is_an_error():
    with pytest.raises(VpsConfigError, match="VPS_HOST"):
        load_config({})


def test_defaults_use_dedicated_user_and_agent_auth():
    cfg = load_config({"VPS_HOST": "vps.example.com"})
    assert cfg.host == "vps.example.com"
    assert cfg.user == DEFAULT_USER != "root"
    assert cfg.port == 22
    assert cfg.auth_method == "agent"
    assert cfg.app_dir == DEFAULT_APP_DIR
    assert cfg.accept_new_host_key is False
    assert f"--env-file {DEFAULT_APP_DIR}/.env" in cfg.compose
    assert cfg.compose.endswith("infra/docker-compose.prod.yml")


def test_key_takes_precedence_over_password(tmp_path):
    key = tmp_path / "id_ed25519"
    key.write_text("dummy")
    cfg = load_config({"VPS_HOST": "h", "VPS_SSH_KEY": str(key), "VPS_PASS": "x"})
    assert cfg.auth_method == "key"
    assert cfg.key_path == str(key)


def test_missing_key_file_is_an_error(tmp_path):
    with pytest.raises(VpsConfigError, match="VPS_SSH_KEY"):
        load_config({"VPS_HOST": "h", "VPS_SSH_KEY": str(tmp_path / "nope")})


def test_password_auth_and_repr_never_leaks_password():
    cfg = load_config({"VPS_HOST": "h", "VPS_USER": "ops", "VPS_PASS": "s3cr3t-value"})
    assert cfg.auth_method == "password"
    assert cfg.user == "ops"
    assert "s3cr3t-value" not in repr(cfg)


def test_invalid_port():
    with pytest.raises(VpsConfigError, match="VPS_PORT"):
        load_config({"VPS_HOST": "h", "VPS_PORT": "abc"})


def test_app_dir_trailing_slash_and_overrides():
    cfg = load_config({
        "VPS_HOST": "h", "VPS_PORT": "2222", "VPS_APP_DIR": "/srv/fm/",
        "VPS_HEALTH_URL": "http://localhost:9000/api/health", "VPS_ACCEPT_NEW_HOST_KEY": "1",
    })
    assert cfg.port == 2222
    assert cfg.app_dir == "/srv/fm"
    assert cfg.health_url == "http://localhost:9000/api/health"
    assert cfg.accept_new_host_key is True


@pytest.mark.parametrize("script", ["deploy_remote.py", "seed_remote.py"])
def test_scripts_exit_cleanly_without_host(script):
    env = {k: v for k, v in os.environ.items() if not k.startswith("VPS_")}
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / script)], env=env, capture_output=True, text=True, timeout=30,
    )
    # paramiko pode não estar instalado no venv de teste: o import é tardio,
    # então a falta de VPS_HOST precisa ser detectada antes.
    assert proc.returncode == 2, proc.stderr
    assert "VPS_HOST" in proc.stderr


# IPv4 público + palavras de credencial em scripts/infra/workflows — impede regressão
_IPV4 = re.compile(r"\b(?!(?:10|127|0)\.)(?!192\.168\.)(?!172\.(?:1[6-9]|2\d|3[01])\.)\d{1,3}(?:\.\d{1,3}){3}\b")
_PASS_ASSIGN = re.compile(r"""(?i)\b(?:VPS_PASS|SSH_PASS|ROOT_PASS)\s*=\s*['"][^'"$]+['"]""")


def _tracked_files():
    for base in ("scripts", "infra", ".github", "Makefile"):
        path = REPO / base
        if path.is_file():
            yield path
        elif path.is_dir():
            for f in path.rglob("*"):
                if f.is_file() and "tests" not in f.parts and f.suffix not in {".pyc"}:
                    yield f


def find_offenders(text: str) -> list[str]:
    found = [m.group(0) for m in _IPV4.finditer(text) if not m.group(0).startswith("255.")]
    found += [m.group(0) for m in _PASS_ASSIGN.finditer(text)]
    return found


def test_detector_catches_the_old_leak_pattern():
    assert find_offenders("VPS_PASS='hunter2' python deploy.py")
    assert find_offenders('HOST    = "203.0.113.7"')
    assert not find_offenders('VPS_PASS="$VPS_PASS" ips: ["10.0.0.0/8", "172.16.0.0/12", "127.0.0.1"]')


def test_no_hardcoded_public_ip_or_password_in_ops_files():
    offenders = [
        f"{f.relative_to(REPO)}: {hit}"
        for f in _tracked_files()
        for hit in find_offenders(f.read_text(errors="ignore"))
    ]
    assert not offenders, "possível segredo/IP hardcoded:\n" + "\n".join(offenders)
