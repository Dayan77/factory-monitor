"""Planos de manutenção da planta virtual (edge/src/provision_maintenance.py) contra uma API REST falsa."""

import json
import uuid
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from edge.src import provision_maintenance as pm
from edge.src.provision import Api, ProvisionError
from edge.tests.conftest import free_port

EDGE = Path(__file__).resolve().parents[1]
PLAN_KEYS = {"position_id", "machine_id", "name", "task_list", "estimated_hours", "wo_type", "interval_cycles",
             "interval_hours", "interval_starts", "interval_days", "stops_machine", "last_done_at"}


class FakeMaintenanceApi:
    """Ativos da Planta Manaus (do mapa) + /api/maintenance-plans/ com a validação do servidor (PlanCreate)."""

    def __init__(self, with_assets: bool = True) -> None:
        self.db = {c: {} for c in ("plants", "lines", "machines", "positions", "maintenance-plans")}
        self.calls: list[tuple[str, str]] = []
        if with_assets:
            self._seed()
        app = web.Application(middlewares=[self._mw])
        app.router.add_post("/api/auth/login", self.login)
        for c in self.db:
            app.router.add_get(f"/api/{c}/", self.lister(c))
        app.router.add_post("/api/maintenance-plans/", self.create_plan)
        app.router.add_patch("/api/maintenance-plans/{id}", self.patch_plan)
        self.app = app

    def _add(self, c, **kw):
        item = {"id": str(uuid.uuid4()), **kw}
        self.db[c][item["id"]] = item
        return item

    def _seed(self):
        doc = json.loads((EDGE / "config" / "plant.json").read_text(encoding="utf-8"))
        plant = self._add("plants", name=doc["assets"]["plant"]["name"])
        self._add("plants", name="Outra planta")
        for ln in doc["assets"]["lines"]:
            line = self._add("lines", plant_id=plant["id"], name=ln["name"])
            for m in ln["machines"]:
                mach = self._add("machines", line_id=line["id"], name=m["name"])
                for p in m["positions"]:
                    self._add("positions", machine_id=mach["id"], name=p["name"])

    @web.middleware
    async def _mw(self, request, handler):
        self.calls.append((request.method, request.path))
        if request.path != "/api/auth/login" and request.headers.get("Authorization") != "Bearer tok":
            return web.json_response({"detail": "unauthorized"}, status=401)
        return await handler(request)

    async def login(self, request):
        return web.json_response({"access_token": "tok"})

    def lister(self, c):
        async def h(request):
            items = list(self.db[c].values())
            for k, v in request.query.items():
                items = [x for x in items if x.get(k) == v]
            return web.json_response(items)
        return h

    async def create_plan(self, request):
        body = await request.json()
        assert set(body) <= PLAN_KEYS, set(body) - PLAN_KEYS
        if not (body.get("position_id") or body.get("machine_id")):
            return web.json_response({"detail": "Informe position_id ou machine_id."}, status=422)
        if not any(body.get(k) for k in ("interval_cycles", "interval_hours", "interval_starts", "interval_days")):
            return web.json_response({"detail": "gatilho"}, status=422)
        if body.get("position_id") and body["position_id"] not in self.db["positions"]:
            return web.json_response({"detail": "Posição"}, status=404)
        assert body["wo_type"] in ("preventive", "predictive")
        assert all(set(t) == {"id", "text"} and t["text"] for t in body["task_list"])
        assert all(body.get(k) is None or body[k] > 0 for k in ("interval_cycles", "interval_hours",
                                                                 "interval_starts", "interval_days"))
        pos = self.db["positions"].get(body.get("position_id"))
        plan = {**{k: v for k, v in body.items() if k != "last_done_at"},
                "machine_id": body.get("machine_id") or (pos or {}).get("machine_id"),
                "last_reset_at": body.get("last_done_at") or "now", "state": "ok", "consumed": 0.0}
        plan["id"] = str(uuid.uuid4())
        self.db["maintenance-plans"][plan["id"]] = plan
        return web.json_response(plan, status=201)

    async def patch_plan(self, request):
        plan = self.db["maintenance-plans"].get(request.match_info["id"])
        if plan is None:
            return web.json_response({"detail": "not found"}, status=404)
        body = await request.json()
        assert "last_done_at" not in body and "position_id" not in body
        plan.update(body)
        return web.json_response(plan)


@pytest.fixture
async def fake():
    servers = []

    async def start(**kw):
        f = FakeMaintenanceApi(**kw)
        runner = web.AppRunner(f.app)
        await runner.setup()
        port = free_port()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        servers.append(runner)
        f.url = f"http://127.0.0.1:{port}"
        return f
    yield start
    for r in servers:
        await r.cleanup()


async def _run(url, **kw):
    async with aiohttp.ClientSession() as s:
        api = Api(url, s)
        await api.login("a", "b")
        p = pm.MaintenanceProvisioner(api, log=lambda *_: None, **kw)
        ids = await p.run()
        return ids, p.stats


async def test_creates_one_plan_per_catalog_entry_anchored_correctly(fake):
    f = await fake()
    ids, stats = await _run(f.url, profile="demo", speed=60, demo_minutes=60)
    assert stats == {"created": len(pm.PLANS), "updated": 0, "unchanged": 0, "skipped": 0}
    plans = list(f.db["maintenance-plans"].values())
    by_name = {p["name"]: p for p in plans}
    assert set(by_name) == {p["name"] + pm.PLAN_SUFFIX for p in pm.PLANS} == set(ids)
    a1 = by_name["Troca de vedações — Cilindro A1" + pm.PLAN_SUFFIX]
    pos = f.db["positions"][a1["position_id"]]
    assert pos["name"] == "Cilindro A1" and f.db["machines"][pos["machine_id"]]["name"] == "Estação de Fixação"
    nr12 = by_name["Inspeção NR-12 da célula" + pm.PLAN_SUFFIX]
    assert nr12.get("position_id") is None and nr12["interval_days"] == 30
    assert nr12["last_reset_at"] != "now"                 # plano de calendário já existia no papel


async def test_idempotent_rerun_creates_nothing(fake):
    f = await fake()
    ids1, _ = await _run(f.url, profile="demo", speed=60, demo_minutes=60)
    n_posts = sum(1 for m, _ in f.calls if m == "POST" and "maintenance" in _)
    ids2, stats = await _run(f.url, profile="demo", speed=60, demo_minutes=60)
    assert ids1 == ids2 and stats == {"created": 0, "updated": 0, "unchanged": len(pm.PLANS), "skipped": 0}
    assert len(f.db["maintenance-plans"]) == len(pm.PLANS)
    assert sum(1 for m, _ in f.calls if m == "POST" and "maintenance" in _) == n_posts
    assert not any(m == "PATCH" for m, _ in f.calls)


async def test_profile_change_patches_intervals_only(fake):
    f = await fake()
    await _run(f.url, profile="demo", speed=60, demo_minutes=60)
    _, stats = await _run(f.url, profile="realistic")
    assert stats["created"] == 0 and stats["updated"] == sum(1 for p in pm.PLANS if p.get("demo_trigger"))
    a1 = next(p for p in f.db["maintenance-plans"].values() if p["name"].startswith("Troca de vedações — Cilindro A1"))
    assert a1["interval_cycles"] == 2_000_000 and a1["interval_days"] == 180
    _, stats = await _run(f.url, profile="realistic")
    assert stats["updated"] == 0


def test_demo_intervals_reach_due_within_the_window():
    """Uso esperado na janela ÷ intervalo = demo_target (≥ 1 → due; ≥ 0,8 → due_soon)."""
    for plan in pm.PLANS:
        trig = plan.get("demo_trigger")
        if not trig:
            continue
        iv = pm.plan_intervals(plan, "demo", speed=60, demo_minutes=60)[pm._FIELD[trig]]
        rate = pm.RATES[(plan["machine"], plan["position"])][trig]
        hours = 1.0 if trig == "hours" else 60.0      # horímetro do servidor anda no relógio real
        assert rate * hours / iv == pytest.approx(plan["demo_target"], rel=0.02), plan["name"]
    targets = [p["demo_target"] for p in pm.PLANS if p.get("demo_trigger")]
    assert sum(t >= 1.0 for t in targets) >= 3 and any(t < 0.8 for t in targets)
    a1 = next(p for p in pm.PLANS if p["position"] == "Cilindro A1")
    assert pm.plan_intervals(a1, "realistic", 60, 60)["interval_cycles"] == 2_000_000
    # escala com a velocidade: o dobro da aceleração, o dobro do intervalo
    d60 = pm.plan_intervals(a1, "demo", 60, 60)["interval_cycles"]
    assert pm.plan_intervals(a1, "demo", 120, 60)["interval_cycles"] == pytest.approx(2 * d60, abs=1)
    mot = next(p for p in pm.PLANS if p["position"] == "Motor do transportador")
    assert pm.plan_intervals(mot, "demo", 120, 60)["interval_hours"] == pm.plan_intervals(mot, "demo", 60, 60)[
        "interval_hours"] == pytest.approx(0.7, abs=0.01)


def test_demo_rates_match_virtual_plant():
    """Ritmo estimado (provisionamento) ≈ ritmo real da planta virtual (24 h com calendário padrão)."""
    from edge.virtual_plant.model import PlantModel
    m = PlantModel(seed=42)
    m.run_for(24 * 3600)
    snap = m.snapshot()
    a1 = snap["truth"]["cylinders"]["A1"]["strokes"] / 24
    assert a1 == pytest.approx(pm.RATES[("Estação de Fixação", "Cilindro A1")]["cycles"], rel=0.12)
    hours = snap["truth"]["run_state"]["montagem.transportador.motor.run"]["on_s"] / 3600 / 24
    assert hours == pytest.approx(pm.RATES[("Transportador de Saída", "Motor do transportador")]["hours"], rel=0.12)
    starts = snap["truth"]["run_state"]["usinagem.centro.coolant.run"]["starts"] / 24
    assert starts == pytest.approx(pm.RATES[("Centro de Usinagem", "Motor da bomba de refrigeração")]["starts"],
                                   rel=0.12)


async def test_missing_position_is_skipped_and_missing_plant_errors(fake):
    f = await fake()
    pid = next(k for k, v in f.db["positions"].items() if v["name"] == "Eixo 2")
    del f.db["positions"][pid]
    _, stats = await _run(f.url, profile="realistic")
    assert stats["skipped"] == 1 and stats["created"] == len(pm.PLANS) - 1
    empty = await fake(with_assets=False)
    with pytest.raises(ProvisionError):
        await _run(empty.url, profile="realistic")


def test_cli_rejects_bad_profile():
    with pytest.raises(SystemExit):
        pm.build_parser().parse_args(["--profile", "x"])
    with pytest.raises(ValueError):
        pm.MaintenanceProvisioner(None, profile="demo", speed=0)
