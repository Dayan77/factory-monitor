"""Sprint 6 §4.1 — tag CSV import: the s6-tags-csv example imports without error and the resulting gateway map is
accepted by the edge's own parser; the invalid row fails on the right line with the edge parser's message; the
server replica of the edge rules gives the SAME message as edge/src/plant_map.py on a shared test vector;
preview writes nothing; re-import is idempotent; tenant isolation."""
from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path

import pytest

from conftest import _make_tenant
from usage_helpers import api

ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "docs" / "sprint6.md").read_text(encoding="utf-8")
SPEC = json.loads(re.search(r"```json s6-tags-csv\n(.*?)\n```", DOC, re.S).group(1))
SOURCES = {"opcua-plc": "opcua", "modbus-plc": "modbus"}


def edge_parser():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from edge.src import plant_map
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"edge not importable: {exc}")
    return plant_map


# shared test vector: (map-v1 tag entry, expected to be valid?)
def _vec():
    k = "l.m.s"
    base = {"key": k, "tag_id": "x", "source": "modbus-plc", "semantic_role": "cycle_counter", "address": 10}
    opc = {"key": k, "tag_id": "x", "source": "opcua-plc", "semantic_role": "run_state", "address": "ns=2;s=A"}
    return [
        base, opc,
        {**base, "semantic_role": "nope"},
        {**base, "source": "s7-plc"},
        {**base, "address": -1}, {**base, "address": "abc"}, {**base, "address": True},
        {**base, "address": "unit=2;hr=10:uint16"}, {**base, "address": "unit=2;di=3", "semantic_role": "cycle_pulse"},
        {**base, "register_type": "flags"},
        {**base, "semantic_role": "cycle_pulse", "register_type": "holding"},
        {**base, "semantic_role": "cycle_pulse", "register_type": "coil"},
        {**base, "semantic_role": "cycle_pulse", "address": "unit=2;hr=5:uint16"},
        {**opc, "address": "Transportador.Run"}, {**opc, "address": 5},
        {**opc, "semantic_role": "cycle_pulse"},
        {**base, "signal_role": "starved"}, {**base, "signal_role": "x"},
        {**base, "counter_max": 0}, {**base, "counter_max": "65535"}, {**base, "counter_max": 65535},
        {**base, "key": ""},
    ]


def test_server_replica_matches_the_edge_parser_messages():
    pm = edge_parser()
    from src.services.onboarding.edge_rules import SEMANTIC_ROLES, SIGNAL_ROLES, tag_error
    assert SEMANTIC_ROLES == pm.SEMANTIC_ROLES and SIGNAL_ROLES == pm.SIGNAL_ROLES
    sources = [{"id": "opcua-plc", "protocol": "opcua", "endpoint": "opc.tcp://h:4840/"},
               {"id": "modbus-plc", "protocol": "modbus", "host": "h", "port": 502}]
    n_err = 0
    for entry in _vec():
        doc = {"version": 1, "gateway_id": "gw", "sources": sources, "tags": [entry]}
        try:
            pm.parse_plant_map(doc, env={})
            edge_msg = None
        except pm.PlantMapError as e:
            edge_msg = str(e)
        assert tag_error(dict(entry), SOURCES, set()) == edge_msg, entry
        n_err += edge_msg is not None
    assert n_err >= 12
    # duplicate key inside one map
    dup = [_vec()[0], {**_vec()[0], "tag_id": "y"}]
    with pytest.raises(pm.PlantMapError) as ei:
        pm.parse_plant_map({"version": 1, "gateway_id": "gw", "sources": sources, "tags": dup}, env={})
    seen: set = set()
    assert tag_error(dup[0], SOURCES, seen) is None and tag_error(dup[1], SOURCES, seen) == str(ei.value)


async def _setup(client, label):
    from src.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, label)
        await s.commit()
    h = t.headers("admin")
    plant = await api(client, "POST", "/api/plants/", h, 201, json={"name": f"Planta {label}"})
    line = await api(client, "POST", "/api/lines/", h, 201, json={"name": "Linha de Montagem", "plant_id": plant["id"]})
    fix = await api(client, "POST", "/api/machines/", h, 201, json={"line_id": line["id"], "name": "Estação de Fixação"})
    conv = await api(client, "POST", "/api/machines/", h, 201,
                     json={"line_id": line["id"], "name": "Transportador de Saída"})
    for name in ("Cilindro A1", "Cilindro B3"):
        await api(client, "POST", "/api/positions/", h, 201, json={
            "machine_id": fix["id"], "name": name, "component_type": "pneumatic_cylinder"})
    gw = await api(client, "POST", "/api/gateways/", h, 201, json={
        "name": f"gw {label}", "plant_id": plant["id"],
        "devices": [{"id": "opcua-plc", "protocol": "opcua", "endpoint": "opc.tcp://plc.local:4840/fm/"},
                    {"id": "modbus-plc", "protocol": "modbus", "host": "10.0.0.5", "port": 502}]})
    return t, plant, line, fix, conv, gw


async def test_spec_csv_imports_and_edge_accepts_the_map(client):
    t, plant, line, fix, conv, gw = await _setup(client, "TCS")
    h = t.headers("admin")
    csv_text = "\n".join(SPEC["csv"]) + "\n"
    pv = await api(client, "POST", "/api/onboarding/tags/import", h, json={"gateway_id": gw["id"], "csv": csv_text})
    assert pv["delimiter"] == ";" and pv["summary"] == {"ok": 6, "update": 0, "error": 0} and pv["can_confirm"]
    assert [r["line"] for r in pv["rows"]] == [2, 3, 4, 5, 6, 7]
    opc = [r for r in pv["rows"] if r["chave"] == "montagem.transportador.rodando"][0]
    assert opc["tag"]["address"] == "ns=2;s=Transportador.Run"            # quoted NodeId with ';' kept whole
    # preview wrote nothing
    assert await api(client, "GET", "/api/tags/", h, params={"machine_id": fix["id"]}) == []
    out = await api(client, "POST", "/api/onboarding/tags/import", h,
                    json={"gateway_id": gw["id"], "csv": "﻿" + csv_text, "confirm": True})   # Excel BOM
    assert out["confirmed"] and out["created"] == 6 and out["updated"] == 0 and out["config_warnings"] == []
    v1 = out["config_version"]
    tags = await api(client, "GET", "/api/tags/", h, params={"machine_id": fix["id"]})
    assert {t_["semantic_role"] for t_ in tags} == {"cycle_counter", "stroke_time", "cycle_pulse"}
    cyc = next(t_ for t_ in tags if t_["semantic_role"] == "cycle_counter")
    assert cyc["counter_max"] == 65535 and cyc["protocol"] == "modbus_tcp"
    pos = await api(client, "GET", f"/api/positions/{cyc['position_id']}", h)
    assert pos["counter_tag_id"] == cyc["id"]                             # the position's counter
    # the gateway map is accepted by the edge parser
    pm = edge_parser()
    cfg = await api(client, "GET", f"/api/gateways/{gw['id']}/config-preview", h)
    parsed = pm.parse_plant_map(cfg["plant_map"], env={})
    assert len(parsed.tags) == 6 and parsed.skipped == []
    by_key = {t_.key: t_ for t_ in parsed.tags}
    assert by_key["montagem.fixacao.pulso_b3"].register_type == "discrete"
    assert by_key["montagem.fixacao.ciclos_a1"].unit_id == 2 and by_key["montagem.fixacao.ciclos_a1"].address == 10
    assert by_key["montagem.transportador.rodando"].address == "ns=2;s=Transportador.Run"
    assert by_key["montagem.transportador.rodando"].poll_interval_ms == 500
    # re-import: idempotent (all "already exists", same map version, no duplicate tag)
    again = await api(client, "POST", "/api/onboarding/tags/import", h,
                      json={"gateway_id": gw["id"], "csv": csv_text, "confirm": True})
    assert again["summary"] == {"ok": 0, "update": 6, "error": 0} and again["created"] == 0
    assert all(r["message"] == "já existe — será atualizada" for r in again["rows"])
    assert again["config_version"] == v1
    assert len(await api(client, "GET", "/api/tags/", h, params={"machine_id": fix["id"]})) == 3
    # comma-separated file (detected from the header) works too
    import csv as _csv
    import io
    buf = io.StringIO()
    w = _csv.writer(buf, delimiter=",")
    for row in _csv.reader(io.StringIO("\n".join(SPEC["csv"])), delimiter=";"):
        w.writerow(row)
    pv2 = await api(client, "POST", "/api/onboarding/tags/import", h, json={"gateway_id": gw["id"], "csv": buf.getvalue()})
    assert pv2["delimiter"] == "," and pv2["summary"]["error"] == 0


async def test_invalid_rows_show_the_edge_message_on_the_right_line(client):
    t, plant, line, fix, conv, gw = await _setup(client, "TCE")
    h = t.headers("admin")
    rows = [SPEC["csv"][0], SPEC["csv"][1], SPEC["invalid_csv_row"],
            "x.y.z;Linha Inexistente;Estação de Fixação;;run_state;modbus-plc;1;1;coil;;;;;;;",
            "x.y.w;linha de montagem;ESTACAO DE FIXACAO;cilindro a1;analog;modbus-plc;7;1;holding;uint16;big;;;0,5;;",
            "x.y.v;Linha de Montagem;Estação de Fixação;;speed;s7-plc;1;;;;;;;;;"]
    pv = await api(client, "POST", "/api/onboarding/tags/import", h, json={"gateway_id": gw["id"], "csv": "\n".join(rows)})
    by_line = {r["line"]: r for r in pv["rows"]}
    assert by_line[2]["status"] == "ok"
    assert by_line[3]["status"] == "error" and SPEC["invalid_error_contains"] in by_line[3]["message"]
    assert by_line[3]["message"] == "erro: tag montagem.fixacao.pulso_errado: cycle_pulse exige register_type coil/discrete"
    assert by_line[4]["status"] == "error" and "Linha Inexistente" in by_line[4]["message"]
    assert by_line[5]["status"] == "ok" and by_line[5]["tag"]["scale"] == 0.5    # accent/case-insensitive names
    assert by_line[6]["message"] == "erro: tag x.y.v: source desconhecida 's7-plc'"
    assert pv["can_confirm"] is False
    r = await client.post("/api/onboarding/tags/import", headers=h,
                          json={"gateway_id": gw["id"], "csv": "\n".join(rows), "confirm": True})
    assert r.status_code == 422 and r.json()["detail"]["confirmed"] is False
    assert await api(client, "GET", "/api/tags/", h, params={"machine_id": fix["id"]}) == []     # nothing written
    # missing required column → 422 (file level)
    r = await client.post("/api/onboarding/tags/import", headers=h, json={"gateway_id": gw["id"], "csv": "chave;linha\na;b"})
    assert r.status_code == 422 and "Colunas obrigatórias" in r.text


async def test_tags_import_isolation_and_rbac(client):
    a = await _setup(client, "TIA")
    b = await _setup(client, "TIB")
    ta, gwa = a[0], a[5]
    tb = b[0]
    body = {"gateway_id": gwa["id"], "csv": "\n".join(SPEC["csv"])}
    await api(client, "POST", "/api/onboarding/tags/import", tb.headers("admin"), 404, json=body)
    await api(client, "POST", "/api/onboarding/tags/import", ta.headers("manager"), 403, json=body)
    await api(client, "POST", "/api/onboarding/tags/import", ta.headers("operator"), 403, json=body)
    await api(client, "POST", "/api/onboarding/tags/import", tb.headers("admin"), 404,
              json={**body, "gateway_id": str(uuid.uuid4())})
    # B's gateway, A's asset names: B's plant has its own assets with the same names → ok only within B
    pv = await api(client, "POST", "/api/onboarding/tags/import", tb.headers("admin"),
                   json={**body, "gateway_id": b[5]["id"], "confirm": True})
    assert pv["created"] == 6
    tags_a = await api(client, "GET", "/api/tags/", ta.headers("admin"), params={"machine_id": a[3]["id"]})
    assert tags_a == []
