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

        row("Processo", 48),
        ts("Memória residente", [(f"process_resident_memory_bytes{{{J}}}", "{{instance}}")], 0, 49, unit="bytes"),
        ts("CPU", [(f"rate(process_cpu_seconds_total{{{J}}}[5m])", "{{instance}}")], 12, 49, unit="percentunit"),
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
