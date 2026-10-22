"""Sprint 6 §4 — onboarding wizard: resumable progress with validated steps, asset CSV (preview / confirm,
idempotent), readiness checks of block s6-readiness-checks (15 min of continuous data in InfluxDB), OPC UA browse
as a command pulled by the gateway heartbeat (§4.2: result with the gateway token, 5-min expiry, no process value
kept), RBAC and tenant isolation."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import update

from conftest import _make_tenant
from usage_helpers import api

ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "docs" / "sprint6.md").read_text(encoding="utf-8")
CHECKS = json.loads(re.search(r"```json s6-readiness-checks\n(.*?)\n```", DOC, re.S).group(1))
SPEC_CSV = json.loads(re.search(r"```json s6-tags-csv\n(.*?)\n```", DOC, re.S).group(1))
DEVICES = [{"id": "opcua-plc", "protocol": "opcua", "endpoint": "opc.tcp://plc.local:4840/fm/"},
           {"id": "modbus-plc", "protocol": "modbus", "host": "10.0.0.5", "port": 502}]
ASSETS_CSV = """linha;maquina;modelo;serie;saida;posicao;tipo;fabricante;modelo_componente;serie_componente;vida_ciclos;vida_horas;vida_partidas;ciclos_por_peca
Linha de Montagem;Estação de Fixação;Dispositivo;VP-FIX;não;Cilindro A1;pneumatic_cylinder;Festo;DSBC;SN-A1-{s};8000000;;;1
Linha de Montagem;Estação de Fixação;Dispositivo;VP-FIX;não;Cilindro B3;cilindro;Festo;DSBC;;;;;1
Linha de Montagem;Transportador de Saída;Esteira;VP-TR;sim;;;;;;;;;
"""


async def _tenant(label):
    from src.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, label)
        await s.commit()
    return t


def test_server_checks_match_the_spec_block():
    from src.services.onboarding.readiness import CHECKS as SERVER
    assert [(c["id"], c["required"]) for c in CHECKS] == SERVER


async def test_wizard_end_to_end_until_ready(client):
    """A new tenant follows the wizard: plant/tz → asset CSV → shifts → PRI → gateway online → tag CSV → reasons →
    users → step 9 green only with 15 min of continuous data."""
    from src.services.influx import influx
    t = await _tenant("ONB")
    h = t.headers("admin")
    prog = await api(client, "GET", "/api/onboarding", h)
    assert prog["current_step"] == 1 and [s["state"] for s in prog["steps"]] == ["pending"] * 9
    await api(client, "GET", "/api/onboarding", t.headers("manager"), 403)

    # step 1: timezone must be IANA and explicitly confirmed
    r = await client.put("/api/onboarding", headers=h, json={"step": 1, "state": "done",
                                                             "data": {"name": "Planta Nova", "timezone": "Mars/Base"}})
    assert r.status_code == 422 and "fuso inválido" in r.text
    r = await client.put("/api/onboarding", headers=h, json={"step": 1, "state": "done",
                                                             "data": {"name": "Planta Nova", "timezone": "America/Sao_Paulo"}})
    assert r.status_code == 422 and "confirme o fuso" in r.text
    prog = await api(client, "PUT", "/api/onboarding", h, json={"step": 1, "state": "done", "data": {
        "name": "Planta Nova", "timezone": "America/Sao_Paulo", "timezone_confirmed": True, "address": "SP"}})
    plant_id = prog["plant_id"]
    assert prog["steps"][0]["state"] == "done" and prog["current_step"] == 2
    plants = await api(client, "GET", "/api/plants/", h)
    assert [p["id"] for p in plants] == [plant_id] and plants[0]["timezone"] == "America/Sao_Paulo"

    # step 2 blocked until every line has an output machine; asset CSV preview / confirm / idempotent
    r = await client.put("/api/onboarding", headers=h, json={"step": 2, "state": "done"})
    assert r.status_code == 422
    csv_assets = ASSETS_CSV.format(s=uuid.uuid4().hex[:6])
    pv = await api(client, "POST", "/api/onboarding/assets/import", h, json={"plant_id": plant_id, "csv": csv_assets})
    assert pv["summary"] == {"ok": 3, "update": 0, "error": 0}
    assert await api(client, "GET", "/api/lines/", h) == []                        # preview writes nothing
    out = await api(client, "POST", "/api/onboarding/assets/import", h,
                    json={"plant_id": plant_id, "csv": csv_assets, "confirm": True})
    assert out["created"] == {"lines": 1, "machines": 2, "positions": 2, "components": 1}
    again = await api(client, "POST", "/api/onboarding/assets/import", h,
                      json={"plant_id": plant_id, "csv": csv_assets, "confirm": True})
    assert again["created"] == {"lines": 0, "machines": 0, "positions": 0, "components": 0}
    lines = await api(client, "GET", "/api/lines/", h)
    assert len(lines) == 1 and lines[0]["output_machine_id"]
    machines = {m["name"]: m for m in await api(client, "GET", "/api/machines/", h)}
    assert lines[0]["output_machine_id"] == machines["Transportador de Saída"]["id"]
    bad = "linha;maquina;saida\nL1;Estação de Fixação;sim\nL1;X;talvez\n"
    pv = await api(client, "POST", "/api/onboarding/assets/import", h, json={"plant_id": plant_id, "csv": bad})
    assert [r_["status"] for r_ in pv["rows"]] == ["error", "error"]
    assert "nomes únicos por planta" in pv["rows"][0]["message"] and "saida" in pv["rows"][1]["message"]
    await api(client, "PUT", "/api/onboarding", h, json={"step": 2, "state": "done"})

    # step 3: needs a shift or explicit 24 h operation
    r = await client.put("/api/onboarding", headers=h, json={"step": 3, "state": "done"})
    assert r.status_code == 422 and "operação 24 h" in r.text
    await api(client, "PUT", "/api/onboarding", h, json={"step": 3, "state": "done", "data": {"operation_24h": True}})

    rd = await api(client, "GET", "/api/onboarding/readiness", h)
    st = {c["id"]: c for c in rd["checks"]}
    assert [c["id"] for c in rd["checks"]] == [c["id"] for c in CHECKS]
    assert rd["ready"] is False
    assert st["timezone_confirmed"]["ok"] and st["output_machine_per_line"]["ok"] and st["calendar"]["ok"]
    assert not st["ideal_cycle_time_output"]["ok"] and not st["gateway_online"]["ok"]
    assert st["second_admin"]["status"] == "warn" or st["second_admin"]["ok"]

    # step 4: no PRI → warning, not a block
    prog = await api(client, "PUT", "/api/onboarding", h, json={"step": 4, "state": "done"})
    assert prog["steps"][3]["state"] == "done" and "OEE ficará indefinido" in prog["steps"][3]["warnings"][0]
    out_m = machines["Transportador de Saída"]["id"]
    prod = await api(client, "POST", "/api/products/", h, 201, json={"code": "P1", "name": "Peça 1"})
    await api(client, "POST", f"/api/machines/{out_m}/products", h, 201,
              json={"product_id": prod["id"], "ideal_cycle_time_s": 30, "is_default": True})

    # step 5: gateway online (enrolled + heartbeat)
    r = await client.put("/api/onboarding", headers=h, json={"step": 5, "state": "done"})
    assert r.status_code == 422
    gw = await api(client, "POST", "/api/gateways/", h, 201, json={"name": "gw onb", "plant_id": plant_id,
                                                                     "devices": DEVICES})
    code = (await api(client, "POST", f"/api/gateways/{gw['id']}/enroll-token", h))["code"]
    enr = await api(client, "POST", "/api/gateways/enroll", {}, json={"code": code})
    gwh = {"Authorization": f"Gateway {enr['token']}"}
    await api(client, "POST", f"/api/gateways/{gw['id']}/heartbeat", gwh, json={"buffer_depth": 0})
    await api(client, "PUT", "/api/onboarding", h, json={"step": 5, "state": "done"})

    # step 6: tags (spec CSV) — config accepted by the edge rules
    r = await client.put("/api/onboarding", headers=h, json={"step": 6, "state": "done"})
    assert r.status_code == 422 and "nenhuma tag" in r.text
    await api(client, "POST", "/api/onboarding/tags/import", h,
              json={"gateway_id": gw["id"], "csv": "\n".join(SPEC_CSV["csv"]), "confirm": True})
    await api(client, "PUT", "/api/onboarding", h, json={"step": 6, "state": "done"})
    prog = await api(client, "PUT", "/api/onboarding", h, json={"step": 7, "state": "done"})
    assert prog["steps"][6]["state"] == "done"
    assert any("fault_code_map" in w or "EQP-01" in w for w in prog["steps"][6]["warnings"])
    prog = await api(client, "PUT", "/api/onboarding", h, json={"step": 8, "state": "done"})
    assert prog["steps"][7]["state"] == "done"

    # step 9: everything except data → not ready (no reading yet)
    rd = await api(client, "GET", "/api/onboarding/readiness", h)
    st = {c["id"]: c for c in rd["checks"]}
    req_bad = [c["id"] for c in rd["checks"] if c["required"] and not c["ok"]]
    assert req_bad == ["data_flowing"], rd
    r = await client.put("/api/onboarding", headers=h, json={"step": 9, "state": "done"})
    assert r.status_code == 422 and "data_flowing" in r.text
    assert st["counter_max_set"]["ok"] and st["counter_max_set"]["required"] is False

    # 15 min of readings, but with a 3-min hole → still not continuous
    from src.config import settings
    tags = await api(client, "GET", "/api/tags/", h)
    req = [x for x in tags if x["semantic_role"] in ("run_state", "cycle_counter", "cycle_pulse", "part_counter_good")]
    now = datetime.now(timezone.utc)
    step = settings.machine_offline_timeout_s
    for x in req:
        k = 0
        while k * 20 <= settings.readiness_window_min * 60 + 30:
            at = now - timedelta(seconds=k * 20)
            hole = x is req[0] and 300 <= k * 20 <= 480
            if not hole:
                await influx.write(tenant_id=t.id, tag_id=x["id"], value=float(k), ts=at, machine_id=x["machine_id"])
            k += 1
    rd = await api(client, "GET", "/api/onboarding/readiness", h)
    flow = {c["id"]: c for c in rd["checks"]}["data_flowing"]
    assert flow["ok"] is False and len(flow["items"]) == 1, flow
    # fill the hole → ready; step 9 done, wizard completed
    for k in range(15, 25):
        await influx.write(tenant_id=t.id, tag_id=req[0]["id"], value=1.0, ts=now - timedelta(seconds=k * 20),
                           machine_id=req[0]["machine_id"])
    rd = await api(client, "GET", "/api/onboarding/readiness", h)
    assert rd["ready"] is True, [c for c in rd["checks"] if not c["ok"]]
    prog = await api(client, "PUT", "/api/onboarding", h, json={"step": 9, "state": "done"})
    assert prog["completed"] and prog["ready_at"]
    # reopening a step reopens the final check
    prog = await api(client, "PUT", "/api/onboarding", h, json={"step": 3, "state": "in_progress"})
    assert prog["steps"][8]["state"] == "in_progress" and prog["ready_at"] is None
    assert step > 0


async def test_opcua_browse_is_a_pulled_command(client, db):
    from src.models import GatewayCommand
    t = await _tenant("BRW")
    h = t.headers("admin")
    plant = await api(client, "POST", "/api/plants/", h, 201, json={"name": "Planta BRW"})
    gw = await api(client, "POST", "/api/gateways/", h, 201, json={"name": "gw brw", "plant_id": plant["id"],
                                                                     "devices": DEVICES})
    # not enrolled → 409; unknown / Modbus source → 422; manager → 403
    await api(client, "POST", f"/api/gateways/{gw['id']}/browse", h, 409, json={"source_id": "opcua-plc"})
    code = (await api(client, "POST", f"/api/gateways/{gw['id']}/enroll-token", h))["code"]
    enr = await api(client, "POST", "/api/gateways/enroll", {}, json={"code": code})
    gwh = {"Authorization": f"Gateway {enr['token']}"}
    await api(client, "POST", f"/api/gateways/{gw['id']}/browse", h, 422, json={"source_id": "nope"})
    await api(client, "POST", f"/api/gateways/{gw['id']}/browse", h, 422, json={"source_id": "modbus-plc"})
    await api(client, "POST", f"/api/gateways/{gw['id']}/browse", t.headers("manager"), 403,
              json={"source_id": "opcua-plc"})
    await api(client, "POST", f"/api/gateways/{gw['id']}/browse", h, 422,
              json={"source_id": "opcua-plc", "max_depth": 7})
    req = await api(client, "POST", f"/api/gateways/{gw['id']}/browse", h, 202,
                    json={"source_id": "opcua-plc", "root_node_id": "ns=2;s=Root", "max_depth": 3, "max_nodes": 3})
    rid = req["request_id"]
    st = await api(client, "GET", f"/api/gateways/{gw['id']}/commands/{rid}", h)
    assert st["status"] == "pending" and st["result"] is None
    # the next heartbeat carries the command; the following one (within 60 s) does not repeat it
    hb = await api(client, "POST", f"/api/gateways/{gw['id']}/heartbeat", gwh, json={"buffer_depth": 0})
    assert [c["id"] for c in hb["commands"]] == [rid]
    cmd = hb["commands"][0]
    assert cmd["type"] == "opcua_browse" and cmd["source_id"] == "opcua-plc" and cmd["max_nodes"] == 3
    assert "endpoint" not in cmd                                          # the gateway uses its own config
    hb = await api(client, "POST", f"/api/gateways/{gw['id']}/heartbeat", gwh, json={"buffer_depth": 0})
    assert hb["commands"] == []
    # result: only with the gateway token; structure only (no Value), capped at max_nodes
    nodes = [{"node_id": "ns=2;s=T.Run", "browse_name": "Run", "display_name": "Transportador rodando",
              "node_class": "Variable", "data_type": "Boolean", "access_level": 1, "parent_id": "ns=2;s=T",
              "value": True, "password": "x"},
             {"node_id": "ns=2;s=T.Good", "browse_name": "Good", "display_name": "Peças boas", "node_class": "Variable",
              "data_type": "UInt32", "parent_id": "ns=2;s=T"},
             {"node_id": "ns=2;s=T", "browse_name": "T", "node_class": "Object"},
             {"node_id": "ns=2;s=T.Temp", "browse_name": "Temp", "node_class": "Variable", "data_type": "Double"}]
    url = f"/api/gateways/{gw['id']}/commands/{rid}/result"
    await api(client, "POST", url, h, 401, json={"nodes": nodes})        # a user JWT is not the gateway
    await api(client, "POST", url, gwh, json={"nodes": nodes, "truncated": False})
    await api(client, "POST", url, gwh, 409, json={"nodes": nodes})
    st = await api(client, "GET", f"/api/gateways/{gw['id']}/commands/{rid}", h)
    assert st["status"] == "done" and st["result"]["truncated"] is True and st["result"]["count"] == 3
    res = st["result"]["nodes"]
    assert all("value" not in n and "password" not in n for n in res)
    assert [n["suggested_role"] for n in res] == ["run_state", "part_counter_good", None]
    # expiry: 5 min without an answer → expired with the message
    req2 = await api(client, "POST", f"/api/gateways/{gw['id']}/browse", h, 202, json={"source_id": "opcua-plc"})
    await db.execute(update(GatewayCommand).where(GatewayCommand.id == req2["request_id"]).values(
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    await db.commit()
    st = await api(client, "GET", f"/api/gateways/{gw['id']}/commands/{req2['request_id']}", h)
    assert st["status"] == "expired" and st["error"] == "gateway não respondeu — está online?"
    await api(client, "POST", f"/api/gateways/{gw['id']}/commands/{req2['request_id']}/result", gwh, 409,
              json={"nodes": []})
    # isolation: another tenant cannot browse / read / answer
    b = await _tenant("BRWB")
    await api(client, "POST", f"/api/gateways/{gw['id']}/browse", b.headers("admin"), 404,
              json={"source_id": "opcua-plc"})
    await api(client, "GET", f"/api/gateways/{gw['id']}/commands/{rid}", b.headers("admin"), 404)
    # another gateway's token cannot answer this gateway's command
    plant_b = await api(client, "POST", "/api/plants/", b.headers("admin"), 201, json={"name": "Planta B"})
    gwb = await api(client, "POST", "/api/gateways/", b.headers("admin"), 201,
                    json={"name": "gw b", "plant_id": plant_b["id"], "devices": DEVICES})
    code = (await api(client, "POST", f"/api/gateways/{gwb['id']}/enroll-token", b.headers("admin")))["code"]
    enr_b = await api(client, "POST", "/api/gateways/enroll", {}, json={"code": code})
    req3 = await api(client, "POST", f"/api/gateways/{gw['id']}/browse", h, 202, json={"source_id": "opcua-plc"})
    await api(client, "POST", f"/api/gateways/{gwb['id']}/commands/{req3['request_id']}/result",
              {"Authorization": f"Gateway {enr_b['token']}"}, 404, json={"nodes": []})
    await api(client, "POST", f"/api/gateways/{gw['id']}/commands/{req3['request_id']}/result",
              {"Authorization": f"Gateway {enr_b['token']}"}, 401, json={"nodes": []})
    # failure answer
    await api(client, "POST", f"/api/gateways/{gw['id']}/commands/{req3['request_id']}/result", gwh,
              json={"error": "BadUserAccessDenied"})
    st = await api(client, "GET", f"/api/gateways/{gw['id']}/commands/{req3['request_id']}", h)
    assert st["status"] == "failed" and st["error"] == "BadUserAccessDenied"


async def test_onboarding_isolation(client):
    a = await _tenant("OIA")
    b = await _tenant("OIB")
    prog = await api(client, "PUT", "/api/onboarding", a.headers("admin"), json={"step": 1, "state": "done", "data": {
        "name": "Planta A", "timezone": "America/Manaus", "timezone_confirmed": True}})
    pa = prog["plant_id"]
    # B: own (empty) progress; A's plant is invisible
    pb = await api(client, "GET", "/api/onboarding", b.headers("admin"))
    assert pb["plant_id"] is None and pb["steps"][0]["state"] == "pending"
    await api(client, "GET", "/api/onboarding/readiness", b.headers("admin"), 404, params={"plant_id": pa})
    await api(client, "POST", "/api/onboarding/assets/import", b.headers("admin"), 404,
              json={"plant_id": pa, "csv": "linha;maquina\nL;M\n"})
    r = await client.put("/api/onboarding", headers=b.headers("admin"), json={"step": 1, "state": "done", "data": {
        "plant_id": pa, "timezone": "America/Manaus", "timezone_confirmed": True}})
    assert r.status_code == 422 and "planta não encontrada" in r.text
    rd = await api(client, "GET", "/api/onboarding/readiness", a.headers("admin"))
    assert rd["plant_id"] == pa
