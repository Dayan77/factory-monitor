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
echo "docker $* [FM_VERSION=${FM_VERSION:-}]" >> "$CALLS"
case "$*" in
  *"config --format json"*)
    def='{"name":"infra","services":{"server":{"container_name":"fm_server"},"postgres":{"container_name":"fm_postgres"}}}'
    printf '%s' "${FAKE_COMPOSE_JSON:-$def}"
    exit 0 ;;
  "ps -a --format"*) printf '%b' "${FAKE_PS:-}"; exit 0 ;;
esac
if [ "$1" = "inspect" ] && [ "${FAKE_PG_RUNNING:-0}" = 1 ]; then echo true; exit 0; fi
case "$*" in
  *"alembic upgrade head"*)
    case "${FAKE_MIGRATION:-ok}" in fail) exit 1 ;; hang) exec /bin/sleep 30 ;; esac ;;
  *" pull "*) [ "${FAKE_PULL:-ok}" = ok ] || exit 1 ;;
esac
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
    # primeiro deploy versionado: o anterior rodava as imagens :latest, que o deploy novo não sobrescreveu
    assert f" build server web site [FM_VERSION=sha-{env['v2'][:12]}]" in calls
    assert "up -d --no-build server web site [FM_VERSION=latest]" in calls
    assert "rollback saudável" in proc.stderr
    assert not (env["app"] / ".deploy/production.current").exists()


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


# ── Sprint 7: releases por tag, versões de imagem, staging, migrations com timeout ─────────────────────────────


def _tag(e, name, rev):
    work = e["app"].parent / "work"
    _git(work, "tag", "-a", name, rev, "-m", name)
    _git(work, "push", "-q", "origin", name)


def _deploy(e, ref, health="ok", **extra):
    return subprocess.run(["bash", str(SCRIPT), ref, str(e["app"])],
                          env={**e["env"], "FAKE_HEALTH": health, **extra},
                          capture_output=True, text=True, timeout=90)


def test_successful_deploy_records_version_and_tags_images_with_commit(env):
    proc = _run(env, "ok")
    assert proc.returncode == 0, proc.stderr
    ver = f"sha-{env['v2'][:12]}"
    assert (env["app"] / ".deploy/production.current").read_text().split() == [ver, env["v2"]]
    assert f"production {ver} {env['v2']}" in (env["app"] / ".deploy/history.log").read_text()
    calls = env["calls"].read_text()
    assert f"up -d --remove-orphans [FM_VERSION={ver}]" in calls
    assert "[FM_VERSION=latest]" not in calls


def test_release_tag_deploys_semver_images_and_older_tag_rolls_back(env):
    _tag(env, "v1.2.0", env["v1"])
    _tag(env, "v1.3.0", env["v2"])
    proc = _deploy(env, "v1.3.0")
    assert proc.returncode == 0, proc.stderr
    assert _rev(env["app"]) == env["v2"]
    assert "up -d --remove-orphans [FM_VERSION=1.3.0]" in env["calls"].read_text()
    # voltar para uma release anterior = deploy da tag antiga (não é fast-forward, e precisa funcionar)
    env["calls"].write_text("")
    proc = _deploy(env, "v1.2.0")
    assert proc.returncode == 0, proc.stderr
    assert _rev(env["app"]) == env["v1"]
    assert "up -d --remove-orphans [FM_VERSION=1.2.0]" in env["calls"].read_text()
    assert (env["app"] / ".deploy/production.current").read_text().split()[0] == "1.2.0"
    # e se a release nova falhar o healthcheck, o rollback volta para a versão registrada (1.2.0), não :latest
    env["calls"].write_text("")
    proc = _deploy(env, "v1.3.0", health="rollback_ok")
    assert proc.returncode == 1
    assert "up -d --no-build server web site [FM_VERSION=1.2.0]" in env["calls"].read_text()
    assert _rev(env["app"]) == env["v1"]


def test_unknown_tag_and_invalid_ref_are_refused(env):
    proc = _deploy(env, "v9.9.9")
    assert proc.returncode == 1 and "não existe" in proc.stderr
    proc = _deploy(env, "main; rm -rf /")
    assert proc.returncode == 2 and "ref inválida" in proc.stderr
    assert env["calls"].read_text() == ""
    assert _rev(env["app"]) == env["v1"]


def test_production_refuses_a_tag_outside_main(env):
    work = env["app"].parent / "work"
    _git(work, "checkout", "-q", "-b", "feature")
    (work / "a.txt").write_text("feature")
    _git(work, "commit", "-qam", "feature")
    _git(work, "push", "-q", "origin", "feature")
    _tag(env, "v2.0.0-rc.1", "HEAD")
    proc = _deploy(env, "v2.0.0-rc.1")
    assert proc.returncode == 1 and "não está na main" in proc.stderr
    assert env["calls"].read_text() == "" and _rev(env["app"]) == env["v1"]
    proc = _deploy(env, "v2.0.0-rc.1", ALLOW_NON_MAIN="1")
    assert proc.returncode == 0, proc.stderr
    assert "[FM_VERSION=2.0.0-rc.1]" in env["calls"].read_text()


def test_failed_migration_aborts_before_swapping_containers(env):
    proc = _run_migration(env, "fail")
    assert proc.returncode == 1
    assert "migrations falharam" in proc.stderr
    calls = env["calls"].read_text()
    assert "alembic current" in calls and " up -d" not in calls
    assert _rev(env["app"]) == env["v1"], "código volta para a versão no ar"


def test_migration_timeout_aborts(env):
    proc = _run_migration(env, "hang", MIGRATION_TIMEOUT_S="1")
    assert proc.returncode == 1
    assert "passaram de 1s" in proc.stderr
    assert " up -d" not in env["calls"].read_text()


def _run_migration(e, mode, **extra):
    return _deploy(e, e["v2"], FAKE_MIGRATION=mode, **extra)


def test_registry_images_are_pulled_not_built(env):
    with (env["app"] / ".env").open("a") as f:
        f.write("FM_IMAGE_PREFIX=ghcr.io/acme/factoryconnect\n")
    proc = _deploy(env, env["v2"], GHCR_TOKEN="t0k3n", GHCR_USER="bot")
    assert proc.returncode == 0, proc.stderr
    calls = env["calls"].read_text()
    assert "login ghcr.io -u bot --password-stdin" in calls and "t0k3n" not in calls
    assert calls.index(" pull server web site") < calls.index("alembic upgrade head")
    assert " build " not in calls and "logout ghcr.io" in calls


def test_failed_pull_aborts_before_migrations(env):
    with (env["app"] / ".env").open("a") as f:
        f.write("FM_IMAGE_PREFIX=ghcr.io/acme/factoryconnect\n")
    proc = _deploy(env, env["v2"], FAKE_PULL="fail")
    assert proc.returncode == 1 and "pull das imagens" in proc.stderr
    assert "alembic" not in env["calls"].read_text()
    assert _rev(env["app"]) == env["v1"]


def test_staging_target_uses_its_own_compose_containers_and_state(env):
    proc = _deploy(env, env["v2"], DEPLOY_TARGET="staging", FAKE_PG_RUNNING="1")
    assert proc.returncode == 0, proc.stderr
    calls = env["calls"].read_text()
    assert "-f infra/docker-compose.staging.yml" in calls and "docker-compose.prod.yml" not in calls
    assert "exec fm_stg_server" in calls and "exec fm_server" not in calls
    assert " build server web [" in calls, "staging não tem site"
    assert (env["app"] / ".deploy/staging.current").exists()
    assert not (env["app"] / ".deploy/production.current").exists()


def test_invalid_target_is_refused(env):
    proc = _deploy(env, env["v2"], DEPLOY_TARGET="prod")
    assert proc.returncode == 2 and env["calls"].read_text() == ""


def test_demo_profile_pulls_the_edge_image_of_the_same_version(env):
    with (env["app"] / ".env").open("a") as f:
        f.write("FM_IMAGE_PREFIX=ghcr.io/acme/factoryconnect\nCOMPOSE_PROFILES=monitoring,demo\n")
    proc = _deploy(env, env["v2"])
    assert proc.returncode == 0, proc.stderr
    assert " pull server web site demo-plant [" in env["calls"].read_text()


def test_foreign_container_in_the_compose_project_aborts_before_touching_anything(env):
    """VPS compartilhado: um container de outro app no mesmo projeto compose seria removido por --remove-orphans."""
    e = {**env, "env": {**env["env"], "FAKE_PS": "fm_server\\tinfra\\nprojetos_web\\tinfra\\nfm_postgres\\tinfra\\n"}}
    r = _run(e, "ok")
    assert r.returncode == 1
    assert "projetos_web" in r.stderr and "nada foi alterado" in r.stderr
    calls = env["calls"].read_text()
    assert "build" not in calls and " up " not in calls and "alembic" not in calls and " pull " not in calls
    assert _rev(env["app"]) == env["v1"]


def test_container_name_owned_by_another_project_aborts(env):
    e = {**env, "env": {**env["env"], "FAKE_PS": "fm_postgres\\toutroapp\\n"}}
    r = _run(e, "ok")
    assert r.returncode == 1
    assert "fm_postgres" in r.stderr and "outroapp" in r.stderr
    assert " up " not in env["calls"].read_text()


def test_other_apps_in_their_own_projects_do_not_block_the_deploy(env):
    e = {**env, "env": {**env["env"],
                        "FAKE_PS": "fm_server\\tinfra\\nfm_postgres\\tinfra\\nprojetos_web\\tprojetos\\ntraefik\\t\\n"}}
    r = _run(e, "ok")
    assert r.returncode == 0, r.stderr
    assert "projeto=infra" in r.stdout
