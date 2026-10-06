"""Provisionamento idempotente contra uma API REST falsa (aiohttp em processo, estado em memória)."""

import base64
import json
import uuid
from pathlib import Path

import pytest
from aiohttp import web

from edge.src import provision as prov
from edge.src.plant_map import parse_plant_map
from edge.tests.conftest import free_port

EDGE = Path(__file__).resolve().parents[1]
TENANT = "22222222-2222-2222-2222-222222222222"


def _token() -> str:
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'HS256'})}.{b64({'tenant_id': TENANT, 'sub': 'u1'})}.sig"


class FakeApi:
    """Implementa o contrato REST do sprint 2 o suficiente para o provisionamento."""

    PARENT = {"lines": "plant_id", "machines": "line_id", "positions": "machine_id", "tags": "machine_id",
              "shifts": "plant_id"}
    SPRINT2_ROLES = {"analog", "cycle_counter", "cycle_pulse", "run_state", "part_counter_good",
                     "part_counter_scrap", "part_counter_total", "fault_state", "stroke_time", "speed", "current"}

    def __init__(self, honor_filters: bool = True, support_patch: bool = True, sprint3: bool = True) -> None:
        colls = ["plants", "lines", "machines", "positions", "components", "tags"]
        if sprint3:
            colls += ["shifts", "products"]
        self.db: dict[str, dict[str, dict]] = {c: {} for c in colls}
        self.machine_products: dict[str, dict[str, dict]] = {}      # machine_id → product_id → item
        self.calls: list[tuple[str, str]] = []
        self.honor_filters = honor_filters
        self.support_patch = support_patch
        self.sprint3 = sprint3
        app = web.Application(middlewares=[self._auth])
        app.router.add_post("/api/auth/login", self.login)
        for c in self.db:
            app.router.add_get(f"/api/{c}/", self.lister(c))
            app.router.add_post(f"/api/{c}/", self.creator(c))
            app.router.add_patch(f"/api/{c}/{{id}}", self.patcher(c))
        app.router.add_post("/api/positions/{id}/install", self.install)
        if sprint3:
            app.router.add_get("/api/machines/{id}/products", self.mp_list)
            app.router.add_post("/api/machines/{id}/products", self.mp_create)
            app.router.add_patch("/api/machines/{id}/products/{pid}", self.mp_patch)
        self.app = app

    # Sprint 3 — produtos por máquina (o servidor guarda horários como HH:MM:SS)
    async def mp_list(self, request):
        mid = request.match_info["id"]
        if mid not in self.db["machines"]:
            return web.json_response({"detail": "not found"}, status=404)
        return web.json_response(list(self.machine_products.get(mid, {}).values()))

    async def mp_create(self, request):
        mid = request.match_info["id"]
        body = await request.json()
        assert set(body) == {"product_id", "ideal_cycle_time_s", "parts_per_cycle", "is_default"}
        assert body["product_id"] in self.db["products"] and body["ideal_cycle_time_s"] > 0
        items = self.machine_products.setdefault(mid, {})
        if body["product_id"] in items:
            return web.json_response({"detail": "duplicado"}, status=409)
        if body["is_default"]:
            for it in items.values():
                it["is_default"] = False
        item = {"id": str(uuid.uuid4()), "machine_id": mid,
                "product_code": self.db["products"][body["product_id"]]["code"], **body}
        items[body["product_id"]] = item
        return web.json_response(item, status=201)

    async def mp_patch(self, request):
        item = next((x for x in self.machine_products.get(request.match_info["id"], {}).values()
                     if x["id"] == request.match_info["pid"]), None)
        if item is None:
            return web.json_response({"detail": "not found"}, status=404)
        item.update(await request.json())
        return web.json_response(item)

    @web.middleware
    async def _auth(self, request, handler):
        self.calls.append((request.method, request.path))
        if request.path != "/api/auth/login" and request.headers.get("Authorization") != f"Bearer {_token()}":
            return web.json_response({"detail": "unauthorized"}, status=401)
        return await handler(request)

    async def login(self, request):
        body = await request.json()
        if body["password"] != "secret":
            return web.json_response({"detail": "bad"}, status=401)
        return web.json_response({"access_token": _token(), "token_type": "bearer"})

    def lister(self, c):
        async def h(request):
            items = list(self.db[c].values())
            parent = self.PARENT.get(c)
            if self.honor_filters and parent and parent in request.query:
                items = [i for i in items if i.get(parent) == request.query[parent]]
            if self.honor_filters and c == "components" and "serial_number" in request.query:
                items = [i for i in items if i["serial_number"] == request.query["serial_number"]]
            return web.json_response(items if c != "components" else {"items": items, "total": len(items)})
        return h

    def creator(self, c):
        async def h(request):
            body = await request.json()
            if c == "tags" and not self.sprint3 and body["semantic_role"] not in self.SPRINT2_ROLES:
                return web.json_response({"detail": [{"loc": ["body", "semantic_role"],
                                                      "msg": "Input should be 'analog', ..."}]}, status=422)
            if c == "shifts":
                assert set(body) == {"plant_id", "name", "start_local", "end_local", "weekdays", "breaks"}
                body = {**body, "start_local": body["start_local"] + ":00", "end_local": body["end_local"] + ":00",
                        "breaks": [{**b, "start_local": b["start_local"] + ":00", "end_local": b["end_local"] + ":00"}
                                   for b in body["breaks"]]}
            if c == "products":
                assert set(body) == {"code", "name"}
                if any(p["code"] == body["code"] for p in self.db["products"].values()):
                    return web.json_response({"detail": "code duplicado"}, status=409)
            if c == "tags":
                assert body["semantic_role"] and body["protocol"] in ("opc_ua", "modbus_tcp")
                if body["position_id"]:
                    assert body["position_id"] in self.db["positions"]
            for parent in ("plant_id", "line_id", "machine_id"):
                if parent in body:
                    coll = parent.replace("_id", "s")
                    assert body[parent] in self.db[coll], f"{parent} inexistente"
            item = {"id": str(uuid.uuid4()), "tenant_id": TENANT, **body}
            if c == "components":
                item.update(position_id=None, removed_at=None)
            self.db[c][item["id"]] = item
            return web.json_response(item, status=201)
        return h

    def patcher(self, c):
        async def h(request):
            if not self.support_patch:
                return web.json_response({"detail": "Method Not Allowed"}, status=405)
            item = self.db[c].get(request.match_info["id"])
            if item is None:
                return web.json_response({"detail": "not found"}, status=404)
            body = await request.json()
            if c == "tags" and not self.sprint3 and body.get("semantic_role", "analog") not in self.SPRINT2_ROLES:
                return web.json_response({"detail": [{"loc": ["body", "semantic_role"]}]}, status=422)
            item.update(body)
            return web.json_response(item)
        return h

    async def install(self, request):
        pid = request.match_info["id"]
        body = await request.json()
        comp = self.db["components"][body["component_id"]]
        if any(x["position_id"] == pid and not x["removed_at"] for x in self.db["components"].values()):
            return web.json_response({"detail": "posição já tem componente instalado"}, status=409)
        comp["position_id"] = pid
        comp["installed_at"] = body.get("installed_at")
        return web.json_response(comp, status=201)


@pytest.fixture
async def api_server():
    servers = []

    async def _start(**kw):
        fake = FakeApi(**kw)
        runner = web.AppRunner(fake.app)
        await runner.setup()
        port = free_port()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        servers.append(runner)
        return fake, f"http://127.0.0.1:{port}"

    yield _start
    for r in servers:
        await r.cleanup()


def plant_doc() -> dict:
    return json.loads((EDGE / "config" / "plant.json").read_text(encoding="utf-8"))


def _quiet(*_a, **_k):
    pass


async def test_provision_creates_hierarchy_and_writes_tag_ids(api_server):
    fake, url = await api_server()
    doc = plant_doc()
    out, stats = await prov.provision(url, "admin@x", "secret", doc, log=_quiet)

    assert len(fake.db["plants"]) == 1 and len(fake.db["lines"]) == 2
    assert len(fake.db["machines"]) == 5
    n_positions = sum(len(m["positions"]) for line in doc["assets"]["lines"] for m in line["machines"])
    assert len(fake.db["positions"]) == n_positions == len(fake.db["components"])
    assert len(fake.db["tags"]) == len(doc["tags"])
    # todo componente instalado na sua posição
    assert all(c["position_id"] for c in fake.db["components"].values())
    # tags com semantic_role, position_id e counter_max
    by_name = {(fake.db["machines"][t["machine_id"]]["name"], t["name"]): t for t in fake.db["tags"].values()}
    a3 = by_name[("Estação de Fixação", "Cilindro A3 — ciclos (CLP 16 bits)")]
    assert a3["semantic_role"] == "cycle_counter" and a3["counter_max"] == 65535
    assert fake.db["positions"][a3["position_id"]]["name"] == "Cilindro A3"
    assert a3["address"] == "unit=2;hr=5:uint16" and a3["protocol"] == "modbus_tcp"
    pulse = by_name[("Estação de Fixação", "Cilindro A1 — fim de curso (pulsos)")]
    assert pulse["semantic_role"] == "cycle_pulse" and pulse["counter_max"] is None
    assert by_name[("Torno CNC", "Em operação")]["address"] == "ns=2;s=usinagem.torno.run_state"
    # posição → tag contadora; linha → máquina de saída
    a1_pos = next(p for p in fake.db["positions"].values() if p["name"] == "Cilindro A1")
    assert a1_pos["counter_tag_id"] == pulse["id"]
    montagem = next(line for line in fake.db["lines"].values() if line["name"] == "Linha de Montagem")
    assert fake.db["machines"][montagem["output_machine_id"]]["name"] == "Transportador de Saída"

    # mapa de saída: ids preenchidos e carregável pelo gateway
    assert out["tenant_id"] == TENANT
    assert all(t["tag_id"] in fake.db["tags"] for t in out["tags"])
    pm = parse_plant_map(out, env={})
    assert not pm.skipped and len(pm.tags) == len(doc["tags"])
    assert doc["tags"][0]["tag_id"] is None    # entrada não é alterada
    assert stats["warnings"] == 0

    # Sprint 3: turnos com pausas, produtos, ciclo ideal por (máquina, produto), tags de produto/fluxo
    plant_id = next(iter(fake.db["plants"]))
    shifts = {x["name"]: x for x in fake.db["shifts"].values()}
    assert set(shifts) == {"Turno A", "Turno B", "Turno C"}
    c = shifts["Turno C"]
    assert c["plant_id"] == plant_id and c["start_local"] == "22:00:00" and c["end_local"] == "06:00:00"
    assert c["weekdays"] == [0, 1, 2, 3, 4, 5, 6]
    assert sorted((b["start_local"], b["end_local"], b["reason_code"]) for b in c["breaks"]) == [
        ("00:30:00", "00:40:00", "PLN-01"), ("02:00:00", "03:00:00", "PLN-01"), ("04:30:00", "04:40:00", "PLN-01")]
    products = {p["code"]: p["id"] for p in fake.db["products"].values()}
    assert set(products) == {"EIXO-20", "EIXO-32", "FLG-A", "FLG-B", "SUP-100", "SUP-200"}
    mid = {m["name"]: m["id"] for m in fake.db["machines"].values()}
    centro = fake.machine_products[mid["Centro de Usinagem"]]
    assert centro[products["FLG-A"]]["ideal_cycle_time_s"] == 90.0 and centro[products["FLG-A"]]["is_default"]
    assert centro[products["FLG-B"]]["parts_per_cycle"] == 2 and not centro[products["FLG-B"]]["is_default"]
    assert len(fake.machine_products) == 5 and all(len(v) == 2 for v in fake.machine_products.values())
    assert out["asset_ids"]["products"] == products and set(out["asset_ids"]["shifts"]) == set(shifts)
    prod_tag = by_name[("Torno CNC", "Produto atual (código no CLP)")]
    assert prod_tag["semantic_role"] == "analog" and prod_tag["data_type"] == "int"   # papel de borda: signal_role
    assert prod_tag["address"] == "ns=2;s=usinagem.torno.product_code"
    assert by_name[("Robô de Solda", "Aguardando material (starved)")]["data_type"] == "bool"
    assert by_name[("Transportador de Saída", "Saída bloqueada (blocked)")]["address"] == "unit=3;di=2"
    assert by_name[("Torno CNC", "Código de falha")]["semantic_role"] == "fault_state"


async def test_provision_is_idempotent(api_server):
    fake, url = await api_server()
    out1, _ = await prov.provision(url, "admin@x", "secret", plant_doc(), log=_quiet)
    counts = {c: len(v) for c, v in fake.db.items()}
    mark = len(fake.calls)
    out2, stats2 = await prov.provision(url, "admin@x", "secret", plant_doc(), log=_quiet)
    assert {c: len(v) for c, v in fake.db.items()} == counts
    assert [t["tag_id"] for t in out2["tags"]] == [t["tag_id"] for t in out1["tags"]]
    assert out2["asset_ids"] == out1["asset_ids"]
    assert stats2["created"] == 0 and stats2["updated"] == 0 and stats2["warnings"] == 0
    writes = [(m, p) for m, p in fake.calls[mark:] if m != "GET"]
    assert writes == [("POST", "/api/auth/login")]     # 2ª rodada: nenhuma escrita além do login


async def test_provision_updates_changed_fields(api_server):
    fake, url = await api_server()
    await prov.provision(url, "admin@x", "secret", plant_doc(), log=_quiet)
    doc = plant_doc()
    t = next(t for t in doc["tags"] if t["key"] == "montagem.fixacao.a2.cycles")
    t["counter_max"] = 4294967295
    doc["assets"]["lines"][1]["machines"][1]["positions"][1]["cycles_per_part"] = 3
    _, stats = await prov.provision(url, "admin@x", "secret", doc, log=_quiet)
    assert stats["created"] == 0 and stats["updated"] == 2 and stats["warnings"] == 0
    tag = next(x for x in fake.db["tags"].values() if x["name"] == "Cilindro A2 — ciclos (CLP 16 bits)")
    assert tag["counter_max"] == 4294967295
    assert next(p for p in fake.db["positions"].values() if p["name"] == "Cilindro A2")["cycles_per_part"] == 3


async def test_provision_idempotent_when_server_ignores_filters_and_patch(api_server):
    fake, url = await api_server(honor_filters=False, support_patch=False)
    await prov.provision(url, "admin@x", "secret", plant_doc(), log=_quiet)
    counts = {c: len(v) for c, v in fake.db.items()}
    _, stats = await prov.provision(url, "admin@x", "secret", plant_doc(), log=_quiet)
    assert {c: len(v) for c, v in fake.db.items()} == counts
    assert stats["created"] == 0
    # "Eixo-árvore" existe no torno e no centro: casou pelo pai, não só pelo nome
    spindles = [p for p in fake.db["positions"].values() if p["name"] == "Eixo-árvore"]
    assert len(spindles) == 2 and len({p["machine_id"] for p in spindles}) == 2


async def test_speed_scales_counter_plausibility_rate(api_server):
    fake, url = await api_server()
    await prov.provision(url, "admin@x", "secret", plant_doc(), log=_quiet)
    tag = next(t for t in fake.db["tags"].values() if t["name"] == "Cilindro A3 — ciclos (CLP 16 bits)")
    assert tag["max_rate_per_s"] == pytest.approx(2 * 2 / 40.0)   # 2 golpes/peça, ciclo 40 s, margem 2×
    assert next(t for t in fake.db["tags"].values() if t["name"] == "Em operação")["max_rate_per_s"] is None
    machine = next(m for m in fake.db["machines"].values() if m["name"] == "Torno CNC")
    assert machine["ideal_cycle_time_s"] == 45.0
    _, stats = await prov.provision(url, "admin@x", "secret", plant_doc(), log=_quiet, speed=60)
    assert tag["max_rate_per_s"] == pytest.approx(6.0) and stats["created"] == 0
    assert stats["updated"] == sum(1 for t in plant_doc()["tags"] if t["max_rate_per_s"])


async def test_bad_credentials_fail_cleanly(api_server):
    _, url = await api_server()
    with pytest.raises(prov.ProvisionError, match="login"):
        await prov.provision(url, "admin@x", "wrong", plant_doc(), log=_quiet)


async def test_provision_updates_shift_breaks_and_ideal_cycle(api_server):
    fake, url = await api_server()
    await prov.provision(url, "admin@x", "secret", plant_doc(), log=_quiet)
    doc = plant_doc()
    doc["assets"]["plant"]["shifts"][0]["breaks"][1] = {"start_local": "11:00", "end_local": "12:00",
                                                        "reason_code": "PLN-01", "label": "refeição"}
    torno = doc["assets"]["lines"][0]["machines"][0]
    torno["products"][1]["ideal_cycle_time_s"] = 50.0
    _, stats = await prov.provision(url, "admin@x", "secret", doc, log=_quiet)
    assert stats["created"] == 0 and stats["updated"] == 2 and stats["warnings"] == 0
    a = next(x for x in fake.db["shifts"].values() if x["name"] == "Turno A")
    assert {"start_local": "11:00", "end_local": "12:00", "reason_code": "PLN-01"} in a["breaks"]
    eixo32 = next(p["id"] for p in fake.db["products"].values() if p["code"] == "EIXO-32")
    tid = next(m["id"] for m in fake.db["machines"].values() if m["name"] == "Torno CNC")
    assert fake.machine_products[tid][eixo32]["ideal_cycle_time_s"] == 50.0
    # e de novo: nada a fazer
    _, stats = await prov.provision(url, "admin@x", "secret", doc, log=_quiet)
    assert stats["created"] == stats["updated"] == stats["warnings"] == 0


async def test_provision_against_sprint2_server_skips_calendar_with_warning(api_server):
    """Servidor sem /api/shifts e /api/products: ativos e tags são provisionados (todas as tags com papéis que
    o servidor do Sprint 2 aceita), turnos/produtos pulados com aviso; idempotente."""
    fake, url = await api_server(sprint3=False)
    out, stats = await prov.provision(url, "admin@x", "secret", plant_doc(), log=_quiet)
    assert len(fake.db["tags"]) == len(plant_doc()["tags"])
    assert all(t["tag_id"] for t in out["tags"])
    assert stats["warnings"] == 2                       # /api/shifts/ + /api/products/
    counts = {c: len(v) for c, v in fake.db.items()}
    _, stats2 = await prov.provision(url, "admin@x", "secret", plant_doc(), log=_quiet)
    assert {c: len(v) for c, v in fake.db.items()} == counts
    assert stats2["created"] == stats2["updated"] == 0


def test_cli_writes_out_file(tmp_path, api_server):
    # a CLI roda seu próprio loop; aqui só garantimos os padrões seguros
    args = prov.build_parser().parse_args([])
    assert args.server == "http://localhost:8000"
    assert args.out.endswith("plant.local.json") and args.map.endswith("plant.json")
