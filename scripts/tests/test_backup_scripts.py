"""
Testa scripts/backup.sh e scripts/restore.sh sem containers: `docker` é um stub que imita
pg_dump / pg_restore / psql / influx dentro dos containers e registra as chamadas.
O round trip REAL (containers novos, contagem de linhas) é scripts/backup_restore_test.sh
(make backup-restore-test, job `backup` do CI).
"""

import json
import os
import shutil
import stat
import time
import subprocess
import tarfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
BACKUP = SCRIPTS / "backup.sh"
RESTORE = SCRIPTS / "restore.sh"

DOCKER_STUB = r"""#!/usr/bin/env bash
echo "docker $*" >> "$CALLS"
args="$*"
case "$1" in
  inspect)
    name="${@: -1}"
    if [ "$name" = "fm_server" ]; then [ "${FAKE_APP_RUNNING:-0}" = 1 ] && echo true || echo false
    else echo true; fi
    exit 0 ;;
  kill) exit 0 ;;
esac
case "$args" in
  *"pg_dump"*)
    [ "${FAKE_PG_DUMP_FAIL:-0}" = 1 ] && { echo "pg_dump: connection refused" >&2; exit 1; }
    printf 'PGDMP-fake-dump'; exit 0 ;;
  *"pg_restore --data-only"*)
    cat >/dev/null
    printf 'SET x;\nCOPY public.users (id, name) FROM stdin;\n1\ta\n2\tb\n3\tc\n\\.\n\nCOPY public.empty (id) FROM stdin;\n\\.\n'
    exit 0 ;;
  *"pg_restore -U"*) cat > "$STATE/restored.dump"; exit 0 ;;
  *"printf %s"*) printf 'factory_monitor'; exit 0 ;;
  *"show server_version"*) echo "16.15"; exit 0 ;;
  *"DROP DATABASE"*) echo dropped >> "$STATE/pg"; exit 0 ;;
  *"select count(*) from"*)
    case "$args" in *"T=public.users"*) echo "${FAKE_USERS_COUNT:-3}" ;; *) echo 0 ;; esac
    exit 0 ;;
  *"influxd version"*) echo "InfluxDB v2.7.12 (git: x) build_date: y"; exit 0 ;;
  *"buckets()"*)
    printf '#group,false,false,false\r\n#datatype,string,long,string\r\n#default,_result,,\r\n,result,table,name\r\n,,0,sensors\r\n\r\n'
    exit 0 ;;
  *"|> count()"*)
    printf '#group,false,false,false\r\n#datatype,string,long,long\r\n#default,_result,,\r\n,result,table,_value\r\n,,0,%s\r\n\r\n' "${FAKE_POINTS:-42}"
    exit 0 ;;
  *"influx backup"*|*"influx restore"*|*"influx bucket list"*|*"rm -rf"*|*"mkdir -p"*) exit 0 ;;
  *"tar -C"*"-cf -"*)
    d="$(mktemp -d)"; echo shard > "$d/20261005.tar.gz"; echo meta > "$d/20261005.manifest"
    tar -C "$d" -cf - .; rm -rf "$d"; exit 0 ;;
  *"tar -C"*"-xf -"*) cat >/dev/null; exit 0 ;;
esac
echo "stub docker: comando inesperado: $args" >&2
exit 97
"""


@pytest.fixture
def env(tmp_path):
    for tool in ("bash", "tar", "sha256sum", "awk"):
        if not shutil.which(tool):
            pytest.skip(f"{tool} indisponível")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    p = bin_dir / "docker"
    p.write_text(DOCKER_STUB)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    state = tmp_path / "state"
    state.mkdir()
    calls = tmp_path / "calls.log"
    calls.write_text("")
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("BACKUP_", "RESTORE_", "INFLUX_", "PG_"))}
    base.update(PATH=f"{bin_dir}:{os.environ['PATH']}", CALLS=str(calls), STATE=str(state),
                BACKUP_DIR=str(tmp_path / "backups"), TMPDIR=str(tmp_path))
    return dict(env=base, dir=tmp_path / "backups", calls=calls, state=state, tmp=tmp_path)


def run(script, e, *args, **extra):
    return subprocess.run(["bash", str(script), *args], env={**e["env"], **extra},
                          capture_output=True, text=True, timeout=60)


def archives(d: Path) -> list[str]:
    return sorted(p.name for p in d.glob("fm-backup-*.tar*") if not p.name.endswith(".sha256"))


def test_backup_creates_archive_with_manifest_and_counts(env):
    proc = run(BACKUP, env)
    assert proc.returncode == 0, proc.stderr
    out = Path(proc.stdout.strip().splitlines()[-1])
    assert out.exists() and out.name.startswith("fm-backup-") and out.suffix == ".tar"
    assert (out.parent / (out.name + ".sha256")).exists()
    with tarfile.open(out) as t:
        names = set(t.getnames())
        manifest = json.load(t.extractfile("./manifest.json"))
        pg_counts = t.extractfile("./pg_counts.tsv").read().decode()
    assert {"./postgres.dump", "./manifest.json", "./files.tsv", "./pg_counts.tsv",
            "./influx_counts.tsv", "./influx/20261005.tar.gz"} <= names
    # linhas contadas no próprio dump (COPY … \.), inclusive tabela vazia
    assert manifest["postgres_rows"] == {"public.users": 3, "public.empty": 0}
    assert pg_counts == "public.users\t3\npublic.empty\t0\n"
    assert manifest["influx_points"] == {"sensors": 42}
    assert manifest["influx"]["included"] is True and manifest["influx"]["count_stop"].endswith("Z")
    assert manifest["postgres"] == {"database": "factory_monitor", "server_version": "16.15"}
    assert set(manifest["files"]) == {"postgres.dump", "influx/20261005.tar.gz", "influx/20261005.manifest"}
    assert not list(env["dir"].glob(".work-*")) and not (env["dir"] / ".lock").exists()


def test_rotation_keeps_only_the_newest(env):
    env["dir"].mkdir()
    for stamp in ("20200101T000000Z", "20200102T000000Z", "20200103T000000Z"):
        (env["dir"] / f"fm-backup-{stamp}.tar").write_text("old")
        (env["dir"] / f"fm-backup-{stamp}.tar.sha256").write_text("x")
    (env["dir"] / "unrelated.txt").write_text("keep me")
    proc = run(BACKUP, env, BACKUP_KEEP="2")
    assert proc.returncode == 0, proc.stderr
    left = archives(env["dir"])
    assert len(left) == 2
    assert left[0] == "fm-backup-20200103T000000Z.tar"            # o mais novo dos antigos
    assert left[1].startswith("fm-backup-2026") or left[1] > left[0]  # o recém-criado
    assert not (env["dir"] / "fm-backup-20200101T000000Z.tar.sha256").exists()
    assert (env["dir"] / "unrelated.txt").exists()


def test_failure_does_not_rotate_and_pings_fail(env):
    env["dir"].mkdir()
    for stamp in ("20200101T000000Z", "20200102T000000Z"):
        (env["dir"] / f"fm-backup-{stamp}.tar").write_text("old")
    ping_log = env["tmp"] / "ping.log"
    curl = Path(env["env"]["PATH"].split(":")[0]) / "curl"
    curl.write_text(f'#!/bin/sh\necho "$@" >> {ping_log}\n')
    curl.chmod(0o755)
    proc = run(BACKUP, env, BACKUP_KEEP="1", FAKE_PG_DUMP_FAIL="1", BACKUP_PING_URL="https://hc.example/abc")
    assert proc.returncode != 0
    assert archives(env["dir"]) == ["fm-backup-20200101T000000Z.tar", "fm-backup-20200102T000000Z.tar"]
    assert not list(env["dir"].glob(".work-*")) and not (env["dir"] / ".lock").exists()
    assert "https://hc.example/abc/fail" in ping_log.read_text()


def test_success_pings_and_uploads_archive_and_checksum(env):
    up = env["tmp"] / "uploads"
    up.mkdir()
    ping_log = env["tmp"] / "ping.log"
    curl = Path(env["env"]["PATH"].split(":")[0]) / "curl"
    curl.write_text(f'#!/bin/sh\necho "$@" >> {ping_log}\n')
    curl.chmod(0o755)
    proc = run(BACKUP, env, BACKUP_UPLOAD_CMD=f'cp "$1" {up}/', BACKUP_PING_URL="https://hc.example/abc")
    assert proc.returncode == 0, proc.stderr
    names = sorted(p.name for p in up.iterdir())
    assert len(names) == 2 and names[0].endswith(".tar") and names[1].endswith(".tar.sha256")
    assert ping_log.read_text().split()[-1] == "https://hc.example/abc"


def _prom(path: Path) -> dict[str, float]:
    out = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#"):
            k, v = line.split()
            out[k] = float(v)
    return out


def test_success_writes_textfile_metrics_for_the_backup_age_alert(env):
    m = env["tmp"] / "metrics" / "fm_backup.prom"
    before = int(time.time())
    proc = run(BACKUP, env, BACKUP_METRICS_FILE=str(m))
    assert proc.returncode == 0, proc.stderr
    vals = _prom(m)
    assert before <= vals["fm_backup_last_success_timestamp_seconds"] <= time.time() + 1
    out = Path(proc.stdout.strip().splitlines()[-1])
    assert vals["fm_backup_last_size_bytes"] == out.stat().st_size > 0
    assert vals["fm_backup_last_duration_seconds"] >= 0
    assert oct(m.stat().st_mode & 0o777) == "0o644" and oct(m.parent.stat().st_mode & 0o777) == "0o755", \
        "node-exporter (nobody) precisa ler o arquivo"
    assert not list(m.parent.glob("*.tmp"))


def test_failure_writes_failure_metric_and_keeps_last_success(env):
    m = env["tmp"] / "metrics" / "fm_backup.prom"
    assert run(BACKUP, env, BACKUP_METRICS_FILE=str(m)).returncode == 0
    ok_ts = _prom(m)["fm_backup_last_success_timestamp_seconds"]
    proc = run(BACKUP, env, BACKUP_METRICS_FILE=str(m), BACKUP_UPLOAD_CMD="exit 3")
    assert proc.returncode != 0
    assert _prom(m)["fm_backup_last_success_timestamp_seconds"] == ok_ts, "falha não pode parecer backup novo"
    assert _prom(m.parent / "fm_backup_failure.prom")["fm_backup_last_failure_timestamp_seconds"] >= ok_ts


def test_upload_failure_fails_the_run_but_keeps_local_copy(env):
    proc = run(BACKUP, env, BACKUP_UPLOAD_CMD="exit 3")
    assert proc.returncode != 0
    assert "upload falhou" in proc.stderr
    assert len(archives(env["dir"])) == 1


def test_concurrent_run_is_refused(env):
    env["dir"].mkdir()
    (env["dir"] / ".lock").mkdir()
    proc = run(BACKUP, env)
    assert proc.returncode != 0 and "outro backup em andamento" in proc.stderr
    assert (env["dir"] / ".lock").exists()          # não remove o lock de outra execução


@pytest.mark.parametrize("keep", ["0", "abc", "-1"])
def test_invalid_keep_is_rejected(env, keep):
    proc = run(BACKUP, env, BACKUP_KEEP=keep)
    assert proc.returncode != 0 and "BACKUP_KEEP" in proc.stderr


def test_skip_influx(env):
    proc = run(BACKUP, env, BACKUP_SKIP_INFLUX="1")
    assert proc.returncode == 0, proc.stderr
    out = Path(proc.stdout.strip().splitlines()[-1])
    with tarfile.open(out) as t:
        manifest = json.load(t.extractfile("./manifest.json"))
        assert not any(n.startswith("./influx/") for n in t.getnames())
    assert manifest["influx"]["included"] is False and manifest["influx_points"] == {}
    assert "influx" not in env["calls"].read_text()


@pytest.mark.skipif(not shutil.which("openssl"), reason="openssl indisponível")
def test_encrypted_backup_restores_with_passphrase_only(env):
    pw = env["tmp"] / "pass"
    pw.write_text("correct horse battery staple\n")
    proc = run(BACKUP, env, BACKUP_PASSPHRASE_FILE=str(pw))
    assert proc.returncode == 0, proc.stderr
    out = Path(proc.stdout.strip().splitlines()[-1])
    assert out.name.endswith(".tar.enc")
    assert not list(env["dir"].glob("*.tar"))          # nada em claro fica no disco
    assert b"PGDMP" not in out.read_bytes()

    ok = run(RESTORE, env, str(out), RESTORE_CONFIRM="yes", BACKUP_PASSPHRASE_FILE=str(pw),
             INFLUX_TOKEN="t")
    assert ok.returncode == 0, ok.stderr
    assert (env["state"] / "restored.dump").read_text() == "PGDMP-fake-dump"

    bad = env["tmp"] / "wrong"
    bad.write_text("wrong\n")
    fail = run(RESTORE, env, str(out), RESTORE_CONFIRM="yes", BACKUP_PASSPHRASE_FILE=str(bad))
    assert fail.returncode != 0


# ── restore.sh ────────────────────────────────────────────────────

@pytest.fixture
def archive(env):
    proc = run(BACKUP, env)
    assert proc.returncode == 0, proc.stderr
    return Path(proc.stdout.strip().splitlines()[-1])


def test_restore_requires_confirmation(env, archive):
    proc = run(RESTORE, env, str(archive))
    assert proc.returncode != 0 and "RESTORE_CONFIRM=yes" in proc.stderr
    assert "DROP DATABASE" not in env["calls"].read_text()


def test_restore_refuses_while_app_is_running(env, archive):
    proc = run(RESTORE, env, str(archive), RESTORE_CONFIRM="yes", FAKE_APP_RUNNING="1")
    assert proc.returncode != 0 and "fm_server está rodando" in proc.stderr
    assert "DROP DATABASE" not in env["calls"].read_text()


def test_restore_verifies_counts(env, archive):
    proc = run(RESTORE, env, str(archive), RESTORE_CONFIRM="yes", INFLUX_TOKEN="t")
    assert proc.returncode == 0, proc.stderr
    assert "2 tabelas com a mesma contagem do dump" in proc.stderr
    assert "bucket sensors: 42 pontos" in proc.stderr
    calls = env["calls"].read_text()
    assert "influx restore --full" in calls and "DROP DATABASE" in calls


def test_restore_fails_when_row_count_differs(env, archive):
    proc = run(RESTORE, env, str(archive), RESTORE_CONFIRM="yes", FAKE_USERS_COUNT="2")
    assert proc.returncode != 0
    assert "public.users: esperado 3, restaurado 2" in proc.stderr


def test_restore_fails_when_influx_lost_points(env, archive):
    proc = run(RESTORE, env, str(archive), RESTORE_CONFIRM="yes", INFLUX_TOKEN="t", FAKE_POINTS="41")
    assert proc.returncode != 0 and "origem tinha 42 pontos, restaurado 41" in proc.stderr


def test_restore_detects_tampered_archive(env, archive):
    data = bytearray(archive.read_bytes())
    data[600] ^= 0xFF
    archive.write_bytes(bytes(data))
    proc = run(RESTORE, env, str(archive), RESTORE_CONFIRM="yes")
    assert proc.returncode != 0 and "sha256" in proc.stderr
    assert "DROP DATABASE" not in env["calls"].read_text()


def test_restore_detects_tampered_member_without_sidecar(env, archive, tmp_path):
    (archive.parent / (archive.name + ".sha256")).unlink()
    x = tmp_path / "x"
    x.mkdir()
    with tarfile.open(archive) as t:
        t.extractall(x, filter="data")
    (x / "postgres.dump").write_text("PGDMP-evil")
    with tarfile.open(archive, "w") as t:
        t.add(x, arcname=".")
    proc = run(RESTORE, env, str(archive), RESTORE_CONFIRM="yes")
    assert proc.returncode != 0 and "sha256 não confere: postgres.dump" in proc.stderr
    assert "DROP DATABASE" not in env["calls"].read_text()
