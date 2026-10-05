"""
Testa scripts/remote_deploy.sh sem VPS: repositório git local como "origin",
`docker` e `sleep` substituídos por stubs que registram as chamadas.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "remote_deploy.sh"

DOCKER_STUB = """#!/usr/bin/env bash
echo "docker $*" >> "$CALLS"
if [ "$1" = "inspect" ] && [ "${FAKE_PG_RUNNING:-0}" = 1 ]; then echo true; exit 0; fi
if [ "$1" = "exec" ]; then
  # healthcheck: FAKE_HEALTH=ok | fail | rollback_ok (falha até o rollback)
  case "$FAKE_HEALTH" in
    ok) exit 0 ;;
    fail) exit 1 ;;
    rollback_ok) grep -q -- "--no-build" "$CALLS" && exit 0 || exit 1 ;;
  esac
fi
exit 0
"""


GOOD_ENV = ("X=1\nMQTT_USERNAME=fm-backend   # backend\nMQTT_PASSWORD=0123456789abcdef0123\n"
            "MQTT_TLS_ENABLED=false\n")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def _rev(cwd):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def env(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git indisponível")
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    app = tmp_path / "app"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    (work / "a.txt").write_text("v1")
    _git(work, "add", "."); _git(work, "commit", "-qm", "v1"); _git(work, "push", "-q", "origin", "HEAD:main")
    _git(tmp_path, "clone", "-q", "-b", "main", str(origin), str(app))
    (app / ".env").write_text(GOOD_ENV)
    (app / "mqtt").mkdir()
    (app / "mqtt" / "gateways.conf").write_text(
        "# gateways\ngw-a 11111111-1111-4111-8111-111111111111 $7$101$abc\n")
    v1 = _rev(app)
    (work / "a.txt").write_text("v2")
    _git(work, "commit", "-qam", "v2"); _git(work, "push", "-q", "origin", "HEAD:main")
    v2 = _rev(work)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", DOCKER_STUB), ("sleep", "#!/bin/sh\nexit 0\n")):
        p = bin_dir / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    calls = tmp_path / "calls.log"
    calls.write_text("")
    base_env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CALLS": str(calls),
                "HEALTH_RETRIES": "2"}
    return dict(app=app, v1=v1, v2=v2, calls=calls, env=base_env)


def _run(e, health):
    return subprocess.run(["bash", str(SCRIPT), e["v2"], str(e["app"])],
                          env={**e["env"], "FAKE_HEALTH": health},
                          capture_output=True, text=True, timeout=60)


def test_successful_deploy_runs_steps_in_order(env):
    proc = _run(env, "ok")
    assert proc.returncode == 0, proc.stderr
    assert _rev(env["app"]) == env["v2"]
    calls = env["calls"].read_text().splitlines()
    joined = "\n".join(calls)
    i_build = joined.index(" build server web")
    i_migr = joined.index("run --rm -T server alembic upgrade head")
    i_up = joined.index("up -d --remove-orphans")
    assert i_build < i_migr < i_up, "ordem esperada: build → migrations → up"
    assert f"--env-file {env['app']}/.env" in joined
    assert "--no-build" not in joined


def test_failed_healthcheck_rolls_back_code_and_images(env):
    proc = _run(env, "rollback_ok")
    assert proc.returncode == 1
    assert _rev(env["app"]) == env["v1"], "código deve voltar ao commit anterior"
    calls = env["calls"].read_text()
    assert "tag factory-monitor-server:rollback factory-monitor-server:latest" in calls
    assert "up -d --no-build server web" in calls
    assert "rollback saudável" in proc.stderr


def test_missing_env_file_aborts_before_touching_anything(env):
    (env["app"] / ".env").unlink()
    proc = _run(env, "ok")
    assert proc.returncode == 1
    assert ".env" in proc.stderr
    assert env["calls"].read_text() == ""
    assert _rev(env["app"]) == env["v1"]


def _assert_aborted_before_build(env, proc, *msgs):
    assert proc.returncode == 1
    for m in msgs:
        assert m in proc.stderr, proc.stderr
    calls = env["calls"].read_text()
    assert " build " not in calls and "alembic" not in calls and " up -d" not in calls
    assert _rev(env["app"]) == env["v1"], "código deve voltar ao commit anterior"


def test_missing_mqtt_credentials_abort_before_build(env):
    (env["app"] / ".env").write_text("X=1\nMQTT_USERNAME=\nMQTT_PASSWORD=short\n")
    proc = _run(env, "ok")
    _assert_aborted_before_build(env, proc, "MQTT_USERNAME ausente", "MQTT_PASSWORD", "§6.4")


def test_no_gateway_credentials_abort_unless_explicitly_allowed(env):
    (env["app"] / "mqtt" / "gateways.conf").write_text("# vazio\n\n")
    proc = _run(env, "ok")
    _assert_aborted_before_build(env, proc, "nenhum gateway")
    with (env["app"] / ".env").open("a") as f:
        f.write("MQTT_ALLOW_NO_GATEWAYS=1\n")
    env["calls"].write_text("")
    proc = _run(env, "ok")
    assert proc.returncode == 0, proc.stderr


def test_tls_enabled_without_certificate_aborts(env):
    with (env["app"] / ".env").open("a") as f:
        f.write('MQTT_TLS_ENABLED="true"\n')
    proc = _run(env, "ok")
    _assert_aborted_before_build(env, proc, "fullchain.pem", "privkey.pem")
    certs = env["app"] / "mqtt" / "certs"
    certs.mkdir()
    (certs / "fullchain.pem").write_text("x")
    (certs / "privkey.pem").write_text("x")
    env["calls"].write_text("")
    assert _run(env, "ok").returncode == 0


def test_preflight_validates_the_compose_file(env):
    proc = _run(env, "ok")
    assert proc.returncode == 0, proc.stderr
    joined = env["calls"].read_text()
    assert joined.index("config -q") < joined.index(" build server web")


def test_non_fast_forward_aborts(env):
    # alteração local commitada no VPS que diverge do origin → não deploya
    (env["app"] / "a.txt").write_text("local")
    _git(env["app"], "commit", "-qam", "local")
    proc = _run(env, "ok")
    assert proc.returncode != 0
    assert "build" not in env["calls"].read_text()


def _add_backup_script(e, body):
    """Commita um scripts/backup.sh falso na v2 (o deploy roda o da versão nova)."""
    work = e["app"].parent / "work"
    (work / "scripts").mkdir(exist_ok=True)
    f = work / "scripts" / "backup.sh"
    f.write_text(body)
    f.chmod(0o755)
    _git(work, "add", ".")
    _git(work, "commit", "-qm", "backup")
    _git(work, "push", "-q", "origin", "HEAD:main")
    e["v2"] = _rev(work)


def test_pre_deploy_backup_runs_before_migrations(env):
    marker = env["app"].parent / "backup.called"
    _add_backup_script(env, f'#!/bin/sh\necho "$BACKUP_DIR $BACKUP_SKIP_INFLUX" > {marker}\necho "docker BACKUP" >> "$CALLS"\n')
    proc = subprocess.run(["bash", str(SCRIPT), env["v2"], str(env["app"])],
                          env={**env["env"], "FAKE_HEALTH": "ok", "FAKE_PG_RUNNING": "1"},
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert marker.read_text().split() == [f"{env['app']}/backups/pre-deploy", "1"]
    calls = env["calls"].read_text()
    assert calls.index("docker BACKUP") < calls.index("alembic upgrade head")


def test_failed_pre_deploy_backup_aborts_before_migrations(env):
    _add_backup_script(env, "#!/bin/sh\nexit 1\n")
    proc = subprocess.run(["bash", str(SCRIPT), env["v2"], str(env["app"])],
                          env={**env["env"], "FAKE_HEALTH": "ok", "FAKE_PG_RUNNING": "1"},
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 1
    assert "backup pré-deploy falhou" in proc.stderr
    assert "alembic" not in env["calls"].read_text()
    assert _rev(env["app"]) == env["v1"], "código deve voltar ao commit anterior"


def test_pre_deploy_backup_can_be_skipped(env):
    marker = env["app"].parent / "backup.called"
    _add_backup_script(env, f"#!/bin/sh\ntouch {marker}\n")
    proc = subprocess.run(["bash", str(SCRIPT), env["v2"], str(env["app"])],
                          env={**env["env"], "FAKE_HEALTH": "ok", "FAKE_PG_RUNNING": "1", "PRE_DEPLOY_BACKUP": "0"},
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert not marker.exists()
