#!/usr/bin/env python3
"""Gera infra/monitoring/grafana/dashboards/factoryconnect-ops.json (painel de operação).

Fonte da verdade do painel: edite aqui e rode `python3 infra/monitoring/tools/build_dashboard.py`.
O teste infra/tests/test_monitoring.py confere que o JSON versionado é igual ao gerado.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "grafana" / "dashboards" / "factoryconnect-ops.json"
DS = {"type": "prometheus", "uid": "fm-prometheus"}
J = 'job="fm-server"'

_id = 0


def _next_id() -> int:
    global _id
    _id += 1
    return _id


def target(expr: str, legend: str = "", ref: str = "A") -> dict:
    return {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref, "range": True}


def stat(title, expr, x, y, unit="short", thresholds=None, desc="", decimals=None):
    p = {
        "id": _next_id(), "type": "stat", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": 4, "w": 4, "x": x, "y": y},
        "targets": [target(expr)],
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "background", "graphMode": "area", "textMode": "value"},
        "fieldConfig": {"defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": thresholds or [
            {"color": "green", "value": None}]}}, "overrides": []},
    }
    if decimals is not None:
        p["fieldConfig"]["defaults"]["decimals"] = decimals
    return p


def ts(title, targets, x, y, w=12, h=8, unit="short", desc=""):
    return {
        "id": _next_id(), "type": "timeseries", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target(e, l, chr(65 + i)) for i, (e, l) in enumerate(targets)],
        "fieldConfig": {"defaults": {"unit": unit, "custom": {"lineWidth": 1, "fillOpacity": 10}}, "overrides": []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
    }


def row(title, y):
    return {"id": _next_id(), "type": "row", "title": title, "collapsed": False,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []}


RED = [{"color": "green", "value": None}, {"color": "red", "value": 0.05}]
LAT = [{"color": "green", "value": None}, {"color": "orange", "value": 0.5}, {"color": "red", "value": 1}]
BACKLOG = [{"color": "green", "value": None}, {"color": "orange", "value": 100}, {"color": "red", "value": 1000}]
UP = [{"color": "red", "value": None}, {"color": "green", "value": 1}]
QUEUE = [{"color": "green", "value": None}, {"color": "orange", "value": 0.5}, {"color": "red", "value": 0.8}]
ANY = [{"color": "green", "value": None}, {"color": "red", "value": 1}]
LAG = [{"color": "green", "value": None}, {"color": "orange", "value": 900}, {"color": "red", "value": 1800}]
BACKUP = [{"color": "green", "value": None}, {"color": "orange", "value": 26 * 3600}, {"color": "red", "value": 48 * 3600}]
DISK = [{"color": "red", "value": None}, {"color": "orange", "value": 0.05}, {"color": "green", "value": 0.15}]
CERT = [{"color": "red", "value": None}, {"color": "orange", "value": 7 * 86400}, {"color": "green", "value": 14 * 86400}]


def build() -> dict:
    global _id
    _id = 0
    p = [
        row("Visão geral", 0),
        stat("Backend no ar", f"max(up{{{J}}})", 0, 1, thresholds=UP,
             desc="1 = o Prometheus consegue coletar /metrics do fm_server"),
        stat("Requisições/s", f"sum(rate(http_requests_total{{{J}}}[5m]))", 4, 1, unit="reqps"),
        stat("Erros 5xx", f'sum(rate(http_requests_total{{{J},status=~"5.."}}[5m])) / '
             f"clamp_min(sum(rate(http_requests_total{{{J}}}[5m])), 1e-9)", 8, 1,
             unit="percentunit", thresholds=RED, decimals=2),
        stat("Latência p95", "histogram_quantile(0.95, sum by (le) "
             f"(rate(http_request_duration_highr_seconds_bucket{{{J}}}[5m])))", 12, 1, unit="s", thresholds=LAT),
        stat("MQTT msgs/s", f"sum(rate(fm_mqtt_messages_total{{{J}}}[5m]))", 16, 1, unit="short",
             desc="Leituras MQTT processadas por segundo (todas as origens)"),
        stat("Backlog MQTT", f"max(fm_mqtt_inflight_messages{{{J}}})", 20, 1, thresholds=BACKLOG,
             desc="Leituras recebidas e ainda não processadas"),

        row("HTTP", 5),
        ts("Requisições/s por classe de status",
           [(f'sum by (status) (rate(http_requests_total{{{J}}}[5m]))', "{{status}}")], 0, 6, unit="reqps"),
        ts("Latência HTTP (p50 / p95 / p99)",
           [(f"histogram_quantile({q}, sum by (le) (rate(http_request_duration_highr_seconds_bucket{{{J}}}[5m])))",
             f"p{int(q * 100)}") for q in (0.5, 0.95, 0.99)], 12, 6, unit="s"),
        ts("p95 por rota (top 10)",
           [("topk(10, histogram_quantile(0.95, sum by (le, handler) "
             f"(rate(http_request_duration_seconds_bucket{{{J}}}[5m]))))", "{{handler}}")], 0, 14, w=24, unit="s"),

        row("Ingestão MQTT", 22),
        ts("Mensagens MQTT/s por resultado",
           [(f"sum by (result) (rate(fm_mqtt_messages_total{{{J}}}[5m]))", "{{result}}")], 0, 23,
           desc="result: ok | rejected (tag desconhecida/outro tenant) | invalid (payload) | error"),
        ts("Atraso de processamento (CLP → servidor)",
           [(f"histogram_quantile({q}, sum by (le) (rate(fm_mqtt_processing_lag_seconds_bucket{{{J}}}[5m])))",
             f"p{int(q * 100)}") for q in (0.5, 0.95)], 12, 23, unit="s",
           desc="Diferença entre o horário da leitura (ts do gateway/CLP) e o fim do processamento no servidor"),
        ts("Backlog MQTT (em processamento)",
           [(f"max(fm_mqtt_inflight_messages{{{J}}})", "em voo")], 0, 31),

        row("Produção / paradas", 39),
        ts("Eventos de parada detectados (por hora)",
           [(f"sum by (kind) (increase(fm_downtime_events_total{{{J}}}[1h]))", "{{kind}}")], 0, 40,
           desc="Paradas abertas pela detecção automática (kind: stop | micro_stop | …)"),

        row("Pipeline de ingestão", 48),
        stat("Fila de ingestão", f"max(fm_ingest_queue_depth{{{J}}}) / clamp_min(max(fm_ingest_queue_capacity{{{J}}}), 1)",
             0, 49, unit="percentunit", thresholds=QUEUE, desc="Leituras aceitas e não terminadas / MQTT_MAX_INFLIGHT"),
        stat("Dead letters (24 h)", f"sum(increase(fm_ingest_dead_letters_total{{{J}}}[24h]))", 4, 49,
             thresholds=ANY, desc="Leituras que falharam depois das repetições (tabela ingest_dead_letters)"),
        stat("Dead letters pendentes", 'max(fm_ingest_dead_letters_pending{job="fm-postgres"})', 8, 49,
             thresholds=ANY, desc="Ainda não reprocessadas (sql-exporter)"),
        stat("Leituras perdidas (24 h)", f"sum(increase(fm_ingest_dropped_total{{{J}}}[24h]))", 12, 49,
             thresholds=ANY, desc="Nem processadas nem em dead letter — deve ser sempre 0"),
        stat("Atraso máx. detector de paradas", f'max(fm_detector_lag_max_seconds{{{J},detector="downtime"}})', 16, 49,
             unit="s", thresholds=LAG),
        stat("Atraso máx. preditiva", f'max(fm_detector_lag_max_seconds{{{J},detector="predictive"}})', 20, 49,
             unit="s", thresholds=LAG),
        ts("Latência da ingestão (recebida → gravada)",
           [(f"histogram_quantile({q}, sum by (le) (rate(fm_ingest_latency_seconds_bucket{{{J}}}[5m])))",
             f"p{int(q * 100)}") for q in (0.5, 0.95, 0.99)], 0, 53, unit="s"),
        ts("Fila, não confirmadas e repetições",
           [(f"max(fm_ingest_queue_depth{{{J}}})", "fila"), (f"max(fm_ingest_unacked{{{J}}})", "QoS 1 sem ack"),
            (f"sum by (stage) (rate(fm_ingest_retries_total{{{J}}}[5m]))", "repetições {{stage}}/s")], 12, 53),
        ts("Atraso dos detectores (máximo por detector)",
           [(f"max by (detector) (fm_detector_lag_max_seconds{{{J}}})", "{{detector}}"),
            (f"max by (detector) (fm_detector_lagging{{{J}}})", "acima do limite: {{detector}}")], 0, 61, unit="s",
           desc="Quanto o detector está atrás dos próprios dados (checkpoint) — cresce depois de uma queda longa"),
        ts("Dead letters por etapa (por hora)",
           [(f"sum by (stage) (increase(fm_ingest_dead_letters_total{{{J}}}[1h]))", "{{stage}}")], 12, 61),

        row("Gateways e demo", 69),
        ts("Gateways: segundos desde o último contato",
           [('max by (tenant, gateway) (fm_gateway_seconds_since_seen{job="fm-postgres"})', "{{tenant}} · {{gateway}}")],
           0, 70, unit="s", desc="Alerta FmGatewayOffline: acima de 6 heartbeats (mínimo 3 min)"),
        ts("Demo pública: idade do último dado e /health da planta",
           [('max(fm_demo_last_data_age_seconds{job="fm-postgres"})', "idade do dado (s)"),
            ('max(probe_success{job="blackbox-demo"})', "planta /health (1 = ok)")], 12, 70),

        row("Plataforma (VPS, backup, sondas públicas)", 78),
        stat("Último backup", 'time() - max(fm_backup_last_success_timestamp_seconds)', 0, 79, unit="s",
             thresholds=BACKUP, desc="Idade do último backup completo (scripts/backup.sh → textfile do node-exporter)"),
        stat("Disco livre (mín.)", 'min(node_filesystem_avail_bytes{job="node",fstype!~"tmpfs|overlay|squashfs"} '
             '/ node_filesystem_size_bytes{job="node",fstype!~"tmpfs|overlay|squashfs"})', 4, 79,
             unit="percentunit", thresholds=DISK),
        stat("Endpoints públicos OK", 'min(probe_success{job="blackbox-public"})', 8, 79, thresholds=UP,
             desc="app /api/health, site /healthz e staging, por HTTPS (blackbox-exporter)"),
        stat("Certificado TLS expira em", 'min(probe_ssl_earliest_cert_expiry{job="blackbox-public"}) - time()', 12, 79,
             unit="s", thresholds=CERT),
        stat("Alertas disparando", 'count(ALERTS{alertstate="firing"}) or vector(0)', 16, 79, thresholds=ANY),
        stat("Alertmanager no ar", 'max(up{job="alertmanager"})', 20, 79, thresholds=UP),
        ts("Disco livre por ponto de montagem",
           [('min by (mountpoint) (node_filesystem_avail_bytes{job="node",fstype!~"tmpfs|overlay|squashfs"} '
             '/ node_filesystem_size_bytes{job="node",fstype!~"tmpfs|overlay|squashfs"})', "{{mountpoint}}")],
           0, 83, unit="percentunit"),
        ts("Sondas públicas (duração)",
           [('max by (instance) (probe_duration_seconds{job="blackbox-public"})', "{{instance}}")], 12, 83, unit="s"),

        row("Processo", 91),
        ts("Memória residente", [(f"process_resident_memory_bytes{{{J}}}", "{{instance}}")], 0, 92, unit="bytes"),
        ts("CPU", [(f"rate(process_cpu_seconds_total{{{J}}}[5m])", "{{instance}}")], 12, 92, unit="percentunit"),
    ]
    return {
        "uid": "fm-ops", "title": "FactoryConnect — Operação", "tags": ["factoryconnect", "ops"],
        "timezone": "browser", "schemaVersion": 39, "version": 1, "editable": False,
        "refresh": "30s", "time": {"from": "now-6h", "to": "now"},
        "annotations": {"list": []}, "templating": {"list": []}, "links": [], "panels": p,
    }


def render() -> str:
    return json.dumps(build(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    OUT.write_text(render(), encoding="utf-8")
    print(f"✓ {OUT}")
