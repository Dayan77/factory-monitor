"""Profile `monitoring`: Prometheus + Grafana provisionados (docker de verdade).

- painel versionado == gerado por infra/monitoring/tools/build_dashboard.py;
- toda métrica usada no painel/alertas existe no backend (instrumentator + métricas fm_* do contrato);
- promtool valida prometheus.yml (prod e dev) e alerts.yml;
- sobe Prometheus + Grafana com as montagens do compose de dev e confere: datasource saudável,
  painel `fm-ops` provisionado, regras carregadas. Se houver backend em localhost:8000, confere a coleta.

    .venv/bin/python -m pytest -q infra/tests/test_monitoring.py
Portas: FM_TEST_PROM_PORT (8190) e FM_TEST_GRAFANA_PORT (8191), só em 127.0.0.1.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MON = ROOT / "infra/monitoring"
PROM_IMAGE = "prom/prometheus:v3.5.0"
GRAFANA_IMAGE = "grafana/grafana:12.1.1"
PROM_PORT = int(os.environ.get("FM_TEST_PROM_PORT", "8190"))
GRAFANA_PORT = int(os.environ.get("FM_TEST_GRAFANA_PORT", "8191"))

# Métricas que o backend expõe em /metrics. As fm_* novas são contrato com o backend
# (docs/operations.md §Observabilidade); fm_mqtt_inflight_messages já existe.
KNOWN_METRICS = {
    "up", "http_requests_total", "http_request_duration_seconds_bucket",
    "http_request_duration_highr_seconds_bucket", "process_resident_memory_bytes", "process_cpu_seconds_total",
    "fm_mqtt_inflight_messages", "fm_mqtt_messages_total", "fm_mqtt_processing_lag_seconds_bucket",
    "fm_downtime_events_total",
    # Sprint 5b/7 — pipeline de ingestão e detectores (server/src/services/metrics.py, services/oee/lag.py)
    "fm_ingest_queue_depth", "fm_ingest_queue_capacity", "fm_ingest_unacked", "fm_ingest_latency_seconds_bucket",
    "fm_ingest_dead_letters_total", "fm_ingest_dropped_total", "fm_ingest_retries_total",
    "fm_detector_lag_max_seconds", "fm_detector_lagging",
}
# Métricas da PLATAFORMA (exporters do profile monitoring de produção, não do backend) e onde nascem
PLATFORM_METRICS = {
    "node_filesystem_avail_bytes": "node-exporter", "node_filesystem_size_bytes": "node-exporter",
    "fm_backup_last_success_timestamp_seconds": "scripts/backup.sh",
    "fm_backup_last_failure_timestamp_seconds": "scripts/backup.sh",
    "fm_gateway_seconds_since_seen": "sql-exporter", "fm_gateway_heartbeat_seconds": "sql-exporter",
    "fm_ingest_dead_letters_pending": "sql-exporter", "fm_demo_last_data_age_seconds": "sql-exporter",
    "probe_success": "blackbox", "probe_ssl_earliest_cert_expiry": "blackbox", "probe_duration_seconds": "blackbox",
    "ALERTS": "prometheus",
}
PROMQL_FUNCS = {"sum", "rate", "max", "min", "histogram_quantile", "clamp_min", "topk", "increase", "absent",
                "by", "le", "or", "and", "count", "on", "time", "vector", "predict_linear"}

docker_only = pytest.mark.skipif(shutil.which("docker") is None, reason="precisa de docker")


def sh(*args, check=True, **kw):
    return subprocess.run(args, check=check, capture_output=True, text=True, **kw)


def load_builder():
    spec = importlib.util.spec_from_file_location("build_dashboard", MON / "tools/build_dashboard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def all_exprs() -> list[str]:
    dash = json.loads((MON / "grafana/dashboards/factoryconnect-ops.json").read_text())
    exprs = [t["expr"] for p in dash["panels"] for t in p.get("targets", [])]
    for f in ("alerts.yml", "alerts.platform.yml"):
        alerts = (MON / "prometheus" / f).read_text()
        exprs += re.findall(r"expr:\s*\|?\s*\n?((?:.+\n?)+?)\s+for:", alerts)
    return exprs


def backend_exprs() -> list[str]:
    """Expressões que só usam métricas do backend (sem as da plataforma)."""
    return [e for e in all_exprs() if not any(m in e for m in PLATFORM_METRICS)]


def test_dashboard_json_is_generated():
    assert (MON / "grafana/dashboards/factoryconnect-ops.json").read_text() == load_builder().render(), \
        "rode: python3 infra/monitoring/tools/build_dashboard.py"


def test_dashboard_panels_have_unique_ids_and_known_datasource():
    dash = json.loads(load_builder().render())
    ids = [p["id"] for p in dash["panels"]]
    assert len(ids) == len(set(ids))
    assert dash["uid"] == "fm-ops"
    for p in dash["panels"]:
        if p["type"] != "row":
            assert p["datasource"]["uid"] == "fm-prometheus"
            assert p["targets"], p["title"]


def test_every_metric_used_is_known():
    used = set()
    for e in all_exprs():
        e = re.sub(r"\{[^}]*\}", "", e)          # tira os seletores de label
        e = re.sub(r"\[[^\]]*\]", "", e)         # e as janelas
        e = re.sub(r"\b(by|on|ignoring|without)\s*\([^)]*\)", "", e)   # e os agrupamentos/casamentos
        e = re.sub(r'"[^"]*"', "", e)
        used |= set(re.findall(r"\b[a-zA-Z_:][a-zA-Z0-9_:]*\b", e))
    used = {u for u in used if not re.fullmatch(r"e\d*|\d.*", u)} - PROMQL_FUNCS
    assert used <= KNOWN_METRICS | set(PLATFORM_METRICS), used - KNOWN_METRICS - set(PLATFORM_METRICS)
    # e o painel cobre o que foi pedido: MQTT processado, atraso, paradas, latência HTTP
    for m in ("fm_mqtt_messages_total", "fm_mqtt_processing_lag_seconds_bucket", "fm_downtime_events_total",
              "http_request_duration_highr_seconds_bucket", "fm_mqtt_inflight_messages",
              "fm_ingest_queue_depth", "fm_ingest_dead_letters_total", "fm_detector_lag_max_seconds",
              "fm_gateway_seconds_since_seen", "fm_backup_last_success_timestamp_seconds", "probe_success"):
        assert m in used


def test_metrics_are_not_published_through_traefik():
    prod = (ROOT / "infra/docker-compose.prod.yml").read_text()
    rule = next(line for line in prod.splitlines() if "routers.fm-api.rule" in line)
    assert "/metrics" not in rule


@docker_only
@pytest.mark.parametrize("cfg", ["prometheus.yml", "prometheus.dev.yml"])
def test_promtool_check_config(cfg):
    r = sh("docker", "run", "--rm", "--entrypoint", "promtool",
           "-v", f"{MON}/prometheus/{cfg}:/etc/prometheus/prometheus.yml:ro",
           "-v", f"{MON}/prometheus/alerts.yml:/etc/prometheus/alerts.yml:ro",
           "-v", f"{MON}/prometheus/alerts.platform.yml:/etc/prometheus/alerts.platform.yml:ro",
           PROM_IMAGE, "check", "config", "/etc/prometheus/prometheus.yml", check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SUCCESS" in r.stdout + r.stderr


def _get(url: str, auth: tuple[str, str] | None = None) -> dict:
    req = urllib.request.Request(url)
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode())
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def _wait(fn, what: str, tries: int = 60):
    last = None
    for _ in range(tries):
        try:
            v = fn()
            if v:
                return v
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(1)
    raise AssertionError(f"{what}: {last}")


@pytest.fixture(scope="module")
def monitoring():
    if shutil.which("docker") is None:
        pytest.skip("precisa de docker")
    cfg = json.loads(sh("docker", "compose", "--env-file", str(ROOT / ".env.example"), "-f",
                        str(ROOT / "infra/docker-compose.yml"), "--profile", "monitoring",
                        "config", "--format", "json").stdout)
    tag = f"fmmon{uuid.uuid4().hex[:8]}"
    net = f"{tag}_net"
    names = {"prometheus": f"{tag}_prom", "grafana": f"{tag}_grafana"}
    ports = {"prometheus": PROM_PORT, "grafana": GRAFANA_PORT}
    sh("docker", "network", "create", net)
    try:
        for svc_name in ("prometheus", "grafana"):
            svc = cfg["services"][svc_name]
            args = ["docker", "run", "-d", "--name", names[svc_name], "--network", net,
                    "--network-alias", svc_name, "-p", f"127.0.0.1:{ports[svc_name]}:{svc['ports'][0]['target']}"]
            for h in svc.get("extra_hosts", []) if isinstance(svc.get("extra_hosts"), list) else \
                    [f"{k}:{v}" if isinstance(v, str) else f"{k}:{v[0]}" for k, v in svc.get("extra_hosts", {}).items()]:
                args += ["--add-host", h.replace("=", ":", 1)]
            for k, v in (svc.get("environment") or {}).items():
                args += ["-e", f"{k}={v}"]
            for v in svc["volumes"]:
                if v["type"] == "bind":
                    args += ["-v", f"{v['source']}:{v['target']}:ro"]
            args += [svc["image"], *(svc.get("command") or [])]
            sh(*args)
        yield {"prom": f"http://127.0.0.1:{PROM_PORT}", "grafana": f"http://127.0.0.1:{GRAFANA_PORT}",
               "auth": ("admin", cfg["services"]["grafana"]["environment"]["GF_SECURITY_ADMIN_PASSWORD"])}
    finally:
        for n in names.values():
            sh("docker", "rm", "-f", n, check=False)
        sh("docker", "network", "rm", net, check=False)


def test_prometheus_loads_rules_and_targets(monitoring):
    rules = _wait(lambda: _get(f"{monitoring['prom']}/api/v1/rules")["data"]["groups"], "regras")
    names = {r["name"] for g in rules for r in g["rules"]}
    assert {"FmServerDown", "FmMqttBacklog", "FmMqttProcessingLagHigh", "FmHttpLatencyP95High"} <= names
    targets = _wait(lambda: _get(f"{monitoring['prom']}/api/v1/targets")["data"]["activeTargets"], "alvos")
    assert {t["labels"]["instance"] for t in targets if t["labels"]["job"] == "fm-server"} == \
        {"server:8000", "host.docker.internal:8000"}


def test_prometheus_scrapes_local_backend_when_running(monitoring):
    """Prometheus (in docker) scrapes host.docker.internal:8000. A dev backend bound to 127.0.0.1 (the plain
    `uvicorn --port 8000`) answers the host but not a container: skip unless Prometheus itself can reach it
    (`uvicorn ... --host 0.0.0.0`, docs/operations.md §8.1)."""
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/metrics", timeout=2).read(100)
    except Exception:  # noqa: BLE001
        pytest.skip("sem backend em localhost:8000")

    def host_target():
        ts = _get(f"{monitoring['prom']}/api/v1/targets")["data"]["activeTargets"]
        t = next((t for t in ts if t["labels"].get("instance") == "host.docker.internal:8000"), None)
        return t if t and t.get("health") in ("up", "down") else None

    target = _wait(host_target, "primeira coleta de host.docker.internal:8000", tries=45)
    if target["health"] != "up":
        pytest.skip("backend em localhost:8000 não alcançável de dentro do docker "
                    f"({target.get('lastError')}); suba com --host 0.0.0.0 para este teste")

    def scraped():
        res = _get(f"{monitoring['prom']}/api/v1/query?query=up%7Bjob%3D%22fm-server%22%7D")["data"]["result"]
        up = {r["metric"]["instance"]: r["value"][1] for r in res}
        return up if up.get("host.docker.internal:8000") == "1" else None

    _wait(scraped, "coleta de host.docker.internal:8000", tries=45)
    q = "http_requests_total%7Bjob%3D%22fm-server%22%7D"
    assert _wait(lambda: _get(f"{monitoring['prom']}/api/v1/query?query={q}")["data"]["result"], "http_requests_total")


def test_backend_exports_every_fm_metric_the_rules_and_dashboard_use():
    """Every fm_* metric referenced by alerts.yml / the dashboard is registered by the server (labels
    pre-created, so the series exist from the first scrape) — no alert silently watching nothing."""
    py = ROOT / ".venv/bin/python"
    if not py.exists():
        pytest.skip("precisa do venv do server (.venv)")
    code = ("import sys; sys.path.insert(0, '.'); import src.main  # noqa\n"
            "from prometheus_client import generate_latest; print(generate_latest().decode())")
    out = subprocess.run([str(py), "-c", code], cwd=ROOT / "server", capture_output=True, text=True,
                         timeout=120, check=True).stdout
    exported = set(re.findall(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)", out, re.M))
    used = {m for e in backend_exprs() for m in re.findall(r"\b(fm_[a-z0-9_]+)", e)}
    assert used, "nenhuma métrica fm_* encontrada nas regras/painel"
    assert used <= exported, f"não exportadas pelo backend: {sorted(used - exported)}"
    assert 'fm_mqtt_messages_total{result="ok"} 0.0' in out


def test_grafana_provisions_datasource_and_dashboard(monitoring):
    g, auth = monitoring["grafana"], monitoring["auth"]
    _wait(lambda: _get(f"{g}/api/health").get("database") == "ok", "grafana /api/health")
    dash = _wait(lambda: _get(f"{g}/api/dashboards/uid/fm-ops", auth), "painel fm-ops")
    assert dash["dashboard"]["title"] == "FactoryConnect — Operação"
    assert dash["meta"]["provisioned"] is True and dash["meta"]["folderTitle"] == "FactoryConnect"
    health = _wait(lambda: _get(f"{g}/api/datasources/uid/fm-prometheus/health", auth), "datasource")
    assert health["status"] == "OK", health


# ── Sprint 7: alertas de produção, Alertmanager e exporters da plataforma ─────────────────────────────────────

AM_IMAGE = "prom/alertmanager:v0.34.1"
NODE_IMAGE = "prom/node-exporter:v1.12.1"
BLACKBOX_IMAGE = "prom/blackbox-exporter:v0.29.0"
SQL_IMAGE = "burningalchemist/sql_exporter:0.24.9"


def test_platform_metrics_have_a_source():
    """Cada métrica da plataforma usada nos alertas/painel é produzida por quem diz produzir."""
    collector = (MON / "sql-exporter/fm_platform.collector.yml").read_text()
    backup = (ROOT / "scripts/backup.sh").read_text()
    for metric, src in PLATFORM_METRICS.items():
        if src == "sql-exporter":
            assert f"metric_name: {metric}" in collector, metric
        elif src == "scripts/backup.sh":
            assert f"echo \"{metric} " in backup, metric


def test_required_production_alerts_exist():
    names = set()
    for f in ("alerts.yml", "alerts.platform.yml"):
        names |= set(re.findall(r"- alert: (\w+)", (MON / "prometheus" / f).read_text()))
    need = {"FmIngestionLagHigh", "FmIngestDeadLetters", "FmDeadLettersPending", "FmDetectorLagHigh",
            "FmGatewayOffline", "FmHttp5xxRate", "FmHttp5xxRateCritical", "FmDiskSpaceLow", "FmDiskSpaceCritical",
            "FmBackupTooOld", "FmDemoPlantDown", "FmPublicEndpointDown", "FmTlsCertificateExpiring",
            "FmAlertmanagerDown", "FmServerDown"}
    assert need <= names, need - names
    for f in ("alerts.yml", "alerts.platform.yml"):
        for block in re.split(r"\n\s+- alert: ", (MON / "prometheus" / f).read_text())[1:]:
            sev = re.search(r"severity: (\w+)", block)
            assert sev and sev.group(1) in ("warning", "critical"), block[:60]
            assert "summary:" in block, block[:60]


def test_production_prometheus_sends_alerts_and_scrapes_every_exporter():
    prod = (MON / "prometheus/prometheus.yml").read_text()
    assert 'targets: ["alertmanager:9093"]' in prod and "alerts.platform.yml" in prod
    for job in ("fm-server", "node", "fm-postgres", "blackbox-public", "blackbox-demo", "alertmanager"):
        assert f"job_name: {job}" in prod, job
    cfg = json.loads(sh("docker", "compose", "--env-file", str(ROOT / ".env.prod.example"), "-f",
                        str(ROOT / "infra/docker-compose.prod.yml"), "--profile", "monitoring", "--profile", "demo",
                        "config", "--format", "json", env={**os.environ, "ALERT_EMAIL_TO": "ops@example.com"}).stdout) \
        if shutil.which("docker") else None
    if cfg:
        svc = cfg["services"]
        for name in ("prometheus", "alertmanager", "node-exporter", "sql-exporter", "blackbox-exporter", "grafana"):
            assert name in svc and svc[name]["logging"]["driver"] == "json-file", name
            assert "ports" not in svc[name] or all(p.get("host_ip") == "127.0.0.1" for p in svc[name]["ports"]), \
                f"{name} publicado fora do loopback"
        assert "fm-demo" in svc["blackbox-exporter"]["networks"]          # sonda a planta da demo
        mounts = {v["target"] for v in svc["prometheus"]["volumes"]}
        assert "/etc/prometheus/alerts.platform.yml" in mounts


@docker_only
def test_alertmanager_renders_config_and_refuses_without_recipient():
    base = ["docker", "run", "--rm", "--entrypoint", "sh", "-v", f"{MON}/alertmanager:/etc/alertmanager:ro"]
    ok = sh(*base, "-e", "ALERT_EMAIL_TO=ops@example.com", "-e", "SMTP_HOST=smtp.example.com", "-e", "SMTP_USER=u",
            "-e", "SMTP_PASSWORD=s3cr3t", "-e", "SMTP_FROM=alerts@example.com", "-e", "ALERTMANAGER_CHECK_ONLY=1",
            AM_IMAGE, "-c", "sh /etc/alertmanager/entrypoint.sh && grep -c s3cr3t /alertmanager/runtime/alertmanager.yml"
            " ; grep -q 'smtp.example.com:587' /alertmanager/runtime/alertmanager.yml && echo SMARTHOST",
            check=False)
    assert "configuração OK" in ok.stdout, ok.stdout + ok.stderr
    assert "\n0\n" in ok.stdout, "a senha SMTP fica em arquivo próprio, nunca na config renderizada"
    assert "SMARTHOST" in ok.stdout
    bad = sh(*base, AM_IMAGE, "/etc/alertmanager/entrypoint.sh", check=False)
    assert bad.returncode != 0 and "ALERT_EMAIL_TO" in bad.stderr
    inj = sh(*base, "-e", 'ALERT_EMAIL_TO=a@b.c" | x', "-e", "SMTP_HOST=h", "-e", "SMTP_FROM=f@b.c", AM_IMAGE,
             "/etc/alertmanager/entrypoint.sh", check=False)
    assert inj.returncode != 0 and "inválido" in inj.stderr


@docker_only
def test_blackbox_config_is_valid():
    r = sh("docker", "run", "--rm", "-v", f"{MON}/blackbox/blackbox.yml:/etc/blackbox/blackbox.yml:ro", BLACKBOX_IMAGE,
           "--config.file=/etc/blackbox/blackbox.yml", "--config.check", check=False)
    assert r.returncode == 0, r.stdout + r.stderr


def _wait_metrics(url: str, needle: str, tries: int = 40) -> str:
    last = ""
    for _ in range(tries):
        try:
            last = urllib.request.urlopen(url, timeout=3).read().decode()
            if needle in last:
                return last
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    raise AssertionError(f"{needle} não apareceu em {url}:\n{last[-2000:]}")


@docker_only
def test_node_exporter_exposes_backup_metrics_written_by_backup_sh(tmp_path):
    """O arquivo que scripts/backup.sh grava (BACKUP_METRICS_FILE) é lido pelo textfile collector."""
    d = tmp_path / "metrics"
    env = {**os.environ, "BACKUP_METRICS_FILE": str(d / "fm_backup.prom")}
    # só a função write_metrics (o backup completo tem teste próprio em scripts/tests)
    script = (ROOT / "scripts/backup.sh").read_text()
    fn = script[script.index("T0="):script.index("\n}\n", script.index("write_metrics()")) + 3]
    (tmp_path / "w.sh").write_text("set -eu\nlog() { :; }\n" + fn + 'touch "$1"; write_metrics success "$1"\n')
    sh("bash", str(tmp_path / "w.sh"), str(tmp_path / "x.tar"), env=env)
    port = int(os.environ.get("FM_TEST_NODE_PORT", "8192"))
    name = f"fmnode{uuid.uuid4().hex[:8]}"
    sh("docker", "run", "-d", "--name", name, "-p", f"127.0.0.1:{port}:9100", "-v", f"{d}:/textfile:ro", NODE_IMAGE,
       "--collector.textfile.directory=/textfile", "--collector.disable-defaults", "--collector.textfile")
    try:
        out = _wait_metrics(f"http://127.0.0.1:{port}/metrics", "fm_backup_last_success_timestamp_seconds")
        assert re.search(r"^fm_backup_last_success_timestamp_seconds \d", out, re.M)
        assert 'node_textfile_scrape_error 0' in out
    finally:
        sh("docker", "rm", "-f", name, check=False)


@pytest.fixture
def mon_db():
    """Banco efêmero com o schema real (alembic upgrade head) no Postgres local — FM_TEST_MON_DB."""
    py = Path(os.environ.get("FM_SERVER_PYTHON") or ROOT / ".venv/bin/python")   # CI: FM_SERVER_PYTHON=python
    if not (py.exists() or shutil.which(str(py))) or shutil.which("docker") is None:
        pytest.skip("precisa do Python do server (FM_SERVER_PYTHON ou .venv) e de docker")
    db = os.environ.get("FM_TEST_MON_DB", "fm_test_devops_mon")
    pg = {"POSTGRES_HOST": os.environ.get("POSTGRES_HOST", "localhost"), "POSTGRES_PORT": "5432",
          "POSTGRES_USER": os.environ.get("POSTGRES_USER", "fm_user"),
          "POSTGRES_PASSWORD": os.environ.get("POSTGRES_PASSWORD", "fm_dev_password")}   # dev local, não é segredo
    admin = ("import asyncio, asyncpg, os, sys\n"
             "async def m():\n"
             "    c = await asyncpg.connect(host=os.environ['POSTGRES_HOST'], user=os.environ['POSTGRES_USER'],"
             " password=os.environ['POSTGRES_PASSWORD'], database='postgres')\n"
             f"    await c.execute('DROP DATABASE IF EXISTS {db} WITH (FORCE)')\n"
             "    if sys.argv[1] == 'create':\n"
             f"        await c.execute('CREATE DATABASE {db}')\n"
             "    await c.close()\n"
             "asyncio.run(m())\n")
    env = {**os.environ, **pg, "POSTGRES_DB": db, "APP_ENV": "test", "FM_PY": str(py)}
    try:
        sh(str(py), "-c", admin, "create", env=env, timeout=60)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres local indisponível: {e}")
    try:
        sh(str(py), "-m", "alembic", "upgrade", "head", cwd=ROOT / "server", env=env, timeout=300)
        yield db, env
    finally:
        sh(str(py), "-c", admin, "drop", env=env, check=False, timeout=60)


SEED = """
import asyncio, uuid
from datetime import datetime, timedelta, timezone
from src.db import AsyncSessionLocal
from src.models import Tenant, Plant, Line, Machine, Gateway
from src.models.ingest import IngestDeadLetter
async def main():
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        t = Tenant(name="Demo", slug="demo-publica"); db.add(t); await db.flush()
        p = Plant(tenant_id=t.id, name="P"); db.add(p); await db.flush()
        ln = Line(tenant_id=t.id, plant_id=p.id, name="L"); db.add(ln); await db.flush()
        db.add(Machine(tenant_id=t.id, line_id=ln.id, name="M", last_seen_at=now - timedelta(seconds=900)))
        db.add(Gateway(tenant_id=t.id, plant_id=p.id, name="gw-a", status="offline", heartbeat_s=10,
                       last_seen_at=now - timedelta(seconds=600)))
        db.add(Gateway(tenant_id=t.id, plant_id=p.id, name="gw-nunca", status="pending"))
        db.add(IngestDeadLetter(topic="factory/x", payload=b"{}", received_at=now, stage="influx", error="e"))
        db.add(IngestDeadLetter(topic="factory/x", payload=b"{}", received_at=now, stage="influx", error="e",
                                replayed_at=now))
        await db.commit()
asyncio.run(main())
"""


def test_sql_exporter_queries_run_against_the_real_schema(mon_db):
    db, env = mon_db
    sh(env["FM_PY"], "-c", SEED, cwd=ROOT / "server", env=env, timeout=120)
    port = int(os.environ.get("FM_TEST_SQL_PORT", "8193"))
    name = f"fmsql{uuid.uuid4().hex[:8]}"
    dsn = f"postgres://{env['POSTGRES_USER']}:{env['POSTGRES_PASSWORD']}@127.0.0.1:5432/{db}?sslmode=disable"
    sh("docker", "run", "-d", "--name", name, "--network", "host", "-v", f"{MON}/sql-exporter:/etc/sql_exporter:ro",
       SQL_IMAGE, "-config.file=/etc/sql_exporter/sql_exporter.yml", f"-config.data-source-name={dsn}",
       f"-web.listen-address=127.0.0.1:{port}")
    try:
        out = _wait_metrics(f"http://127.0.0.1:{port}/metrics", "fm_gateway_seconds_since_seen")
        logs = sh("docker", "logs", name, check=False)
        assert "level=ERROR" not in logs.stdout + logs.stderr, logs.stderr[-2000:]
        m = re.search(r'^fm_gateway_seconds_since_seen\{[^}]*gateway="gw-a \([0-9a-f]{8}\)"[^}]*tenant="demo-publica"'
                      r'[^}]*\} ([\d.e+]+)', out, re.M)
        assert m and 590 <= float(m.group(1)) <= 900, out[-3000:]
        assert "gw-nunca" not in out, "gateway que nunca se cadastrou não é 'offline'"
        assert re.search(r'^fm_gateway_heartbeat_seconds\{.*gateway="gw-a.*\} 10$', out, re.M)
        assert re.search(r"^fm_ingest_dead_letters_pending\{.*\} 1$", out, re.M)
        age = re.search(r"^fm_demo_last_data_age_seconds\{.*\} ([\d.e+]+)$", out, re.M)
        assert age and 890 <= float(age.group(1)) <= 1200
    finally:
        sh("docker", "rm", "-f", name, check=False)


@docker_only
def test_alert_rules_unit_tests():
    """promtool test rules: cada alerta crítico de produção dispara na condição e não fora dela
    (infra/monitoring/prometheus/tests/alerts_test.yml)."""
    r = sh("docker", "run", "--rm", "--entrypoint", "promtool", "-v", f"{MON}/prometheus:/r:ro", "-w", "/r/tests",
           PROM_IMAGE, "test", "rules", "alerts_test.yml", check=False)
    assert r.returncode == 0 and "SUCCESS" in r.stdout + r.stderr, r.stdout + r.stderr
