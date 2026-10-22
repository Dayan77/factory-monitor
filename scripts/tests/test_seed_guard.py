"""seed_dev/seed_remote must never create production accounts with the public dev passwords."""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import seed_dev  # noqa: E402  (stdlib-only at import time)
import seed_remote  # noqa: E402

STRONG = {"SEED_ADMIN_PASSWORD": "x9!Strong-admin-pw", "SEED_SUPERADMIN_PASS": "y7#Strong-super-pw"}


def test_dev_mode_allows_defaults():
    assert seed_dev.production_mode({"APP_ENV": "development"}) is False
    seed_dev.ensure_safe_credentials({"APP_ENV": "development"})  # no raise


@pytest.mark.parametrize("env", [
    {"APP_ENV": "production"},
    {"APP_ENV": "development", "SEED_ENFORCE_PROD_CREDENTIALS": "1"},
])
def test_production_without_seed_passwords_is_refused(env):
    with pytest.raises(SystemExit) as exc:
        seed_dev.ensure_safe_credentials(env)
    msg = str(exc.value)
    assert "SEED_ADMIN_PASSWORD" in msg and "SEED_SUPERADMIN_PASS" in msg


@pytest.mark.parametrize("override", [
    {"SEED_SUPERADMIN_PASS": "Super@1234"},
    {"SEED_ADMIN_PASSWORD": "Admin@1234"},
    {"SEED_ADMIN_PASSWORD": "short1!"},
    {"SEED_SUPERADMIN_PASS": "   "},
])
def test_production_with_dev_default_or_weak_password_is_refused(override):
    env = {"APP_ENV": "production", **STRONG, **override}
    with pytest.raises(SystemExit) as exc:
        seed_dev.ensure_safe_credentials(env)
    # never echo the secret values
    for value in env.values():
        if value.strip() and value not in ("production",):
            assert value not in str(exc.value)


def test_production_with_strong_passwords_is_allowed():
    seed_dev.ensure_safe_credentials({"APP_ENV": "production", **STRONG})


def test_seed_refuses_before_touching_db(monkeypatch):
    """seed() must exit before importing server deps / connecting."""
    import asyncio
    monkeypatch.setenv("SEED_ENFORCE_PROD_CREDENTIALS", "1")
    monkeypatch.delenv("SEED_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("SEED_SUPERADMIN_PASS", raising=False)
    with pytest.raises(SystemExit):
        asyncio.run(seed_dev.seed())


def test_seed_remote_always_enforces_production_credentials():
    cmd = seed_remote.seed_command()
    assert cmd.startswith("docker exec -e SEED_ENFORCE_PROD_CREDENTIALS=1 fm_server ")
    assert "m.seed()" in cmd


class _Chan:
    def __init__(self, status):
        self.status = status

    def recv_exit_status(self):
        return self.status


class _Stream:
    def __init__(self, data, status):
        self._data = data.encode()
        self.channel = _Chan(status)

    def read(self):
        return self._data


class _Sftp:
    def put(self, *a):
        pass

    def close(self):
        pass


class FakeClient:
    def __init__(self, seed_status):
        self.seed_status = seed_status
        self.commands = []

    def open_sftp(self):
        return _Sftp()

    def exec_command(self, cmd, timeout=None):
        self.commands.append(cmd)
        status = self.seed_status if "m.seed()" in cmd else 0
        return None, _Stream("", status), _Stream("", 0)


def test_run_seed_reports_refusal_and_cleans_up():
    c = FakeClient(seed_status=1)
    assert seed_remote.run_seed(c) is False
    assert any("rm -f /app/seed_dev.py" in x for x in c.commands)


def test_run_seed_success():
    c = FakeClient(seed_status=0)
    assert seed_remote.run_seed(c) is True
