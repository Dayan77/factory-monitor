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
}
PROMQL_FUNCS = {"sum", "rate", "max", "min", "histogram_quantile", "clamp_min", "topk", "increase", "absent",
                "by", "le", "or", "and", "count", "on"}

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
    alerts = (MON / "prometheus/alerts.yml").read_text()
    exprs += re.findall(r"expr:\s*\|?\s*\n?((?:.+\n?)+?)\s+for:", alerts)
    return exprs


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
        e = re.sub(r"\bby\s*\([^)]*\)", "", e)   # e os agrupamentos
        e = re.sub(r'"[^"]*"', "", e)
        used |= set(re.findall(r"\b[a-zA-Z_:][a-zA-Z0-9_:]*\b", e))
    used = {u for u in used if not re.fullmatch(r"e\d*|\d.*", u)} - PROMQL_FUNCS
    assert used <= KNOWN_METRICS, used - KNOWN_METRICS
    # e o painel cobre o que foi pedido: MQTT processado, atraso, paradas, latência HTTP
    for m in ("fm_mqtt_messages_total", "fm_mqtt_processing_lag_seconds_bucket", "fm_downtime_events_total",
              "http_request_duration_highr_seconds_bucket", "fm_mqtt_inflight_messages"):
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
    used = {m for e in all_exprs() for m in re.findall(r"\b(fm_[a-z0-9_]+)", e)}
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
