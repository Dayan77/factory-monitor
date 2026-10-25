"""Gateway na frota (docs/gateways.md): cadastro → configuração remota → heartbeat HTTP → recarga a quente do mapa
quando o servidor muda o config_version, sem perder os contadores de borda; partida offline pela cópia local."""

import asyncio
import copy
import json
import logging

import pytest
from aiohttp import web

from edge.src import gateway as gw_mod
from edge.src.fleet import FleetClient, HeartbeatError, heartbeat_body
from edge.tests.conftest import free_port
from edge.tests.test_plant_map import provisioned_doc
from edge.virtual_plant.model import PlantModel
from edge.virtual_plant.runtime import VirtualPlantRuntime

logging.getLogger("asyncua").setLevel(logging.ERROR)
GW = "0f0e0d0c-0000-4000-8000-000000000001"
TENANT = "11111111-1111-1111-1111-111111111111"
CODE = "QWER-TYUI-OPAS"
TOKEN = "fcg_fleet-token"
PULSE = "montagem.fixacao.a1.eos"


def fixacao_map(keys: set[str]) -> dict:
    doc = provisioned_doc()
    doc["gateway_id"] = GW
    doc["tags"] = [t for t in doc["tags"] if t["key"] in keys]
    return doc


class FleetServer:
    def __init__(self) -> None:
        self.version = 1
        self.maps = {1: fixacao_map({PULSE, "montagem.fixacao.run_state"}),
                     2: fixacao_map({PULSE, "montagem.fixacao.run_state", "montagem.fixacao.a2.cycles"})}
        self.heartbeats: list[dict] = []
        self.config_calls: list[dict] = []
        self.up = True
        app = web.Application()
        app.router.add_post("/api/gateways/enroll", self.enroll)
        app.router.add_get("/api/gateways/{gid}/config", self.config)
        app.router.add_post("/api/gateways/{gid}/heartbeat", self.heartbeat)
        self.app = app

    def _auth(self, request) -> bool:
        return request.headers.get("Authorization") == f"Gateway {TOKEN}" and request.match_info["gid"] == GW

    async def enroll(self, request):
        if (await request.json()).get("code") != CODE:
            return web.json_response({"detail": "inválido"}, status=401)
        return web.json_response({"gateway_id": GW, "tenant_id": TENANT, "token": TOKEN,
                                  "mqtt": {"username": "gw-0f0e0d0c-0001", "password": "pw",
                                           "topic_prefix": f"factory/{TENANT}/"}})

    async def config(self, request):
        if not self._auth(request):
            return web.json_response({}, status=401)
        etag = f'"{self.version}-abc"'
        self.config_calls.append({"inm": request.headers.get("If-None-Match"), "version": self.version})
        if request.headers.get("If-None-Match") == etag:
            return web.Response(status=304, headers={"ETag": etag})
        return web.json_response({"gateway_id": GW, "revision": str(self.version), "config_version": self.version,
                                  "plant_map": self.maps[self.version]}, headers={"ETag": etag})

    async def heartbeat(self, request):
        if not self._auth(request):
            return web.json_response({}, status=401)
        body = await request.json()
        self.heartbeats.append(body)
        return web.json_response({"status": "online", "config_version": self.version,
                                  "config_changed": body.get("config_version") != self.version})


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def publish(self, topic, payload, qos=0):
        self.sent.append((topic, json.loads(payload)))


@pytest.fixture
async def fleet():
    fake = FleetServer()
    runner = web.AppRunner(fake.app)
    await runner.setup()
    port = free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    fake.url = f"http://127.0.0.1:{port}"
    yield fake
    await runner.cleanup()


def _settings(monkeypatch, tmp_path, fleet, **extra):
    s = gw_mod.settings
    values = {"buffer_db_path": str(tmp_path / "buf.db"), "counter_db_path": str(tmp_path / "cnt.db"),
              "tenant_id": "", "gateway_id": "", "encryption_key": "", "mqtt_username": "", "mqtt_password": "",
              "server_url": fleet.url, "enroll_code": CODE, "gateway_credentials_path": str(tmp_path / "cred.json"),
              "gateway_config_cache": str(tmp_path / "cfg.json"), "gateway_config_url": "", "gateway_token": "",
              "plant_map_path": "", "gateway_heartbeat_s": 1.0, "gateway_config_poll_s": 3600.0}
    values.update(extra)
    for k, v in values.items():
        monkeypatch.setattr(s, k, v)


def _attach_fake_broker(g):
    client = FakeClient()
    g.publisher._client, g.publisher.is_connected = client, True

    async def noop():
        g.publisher.is_connected = True
    g.publisher.connect = noop
    g.client = client
    return client


async def test_enroll_config_heartbeat_and_hot_reload_keep_edge_counters(fleet, tmp_path, monkeypatch):
    rt = VirtualPlantRuntime(PlantModel(seed=42, faults=False), speed=100, opcua_port=free_port(),
                             modbus_port=free_port(), status_port=None, update_ms=20)
    for k, v in {"VIRTUAL_PLANT_HOST": "127.0.0.1", "VIRTUAL_PLANT_OPCUA_PORT": str(rt.opcua_port),
                 "VIRTUAL_PLANT_MODBUS_PORT": str(rt.modbus_port)}.items():
        monkeypatch.setenv(k, v)
    _settings(monkeypatch, tmp_path, fleet)
    g = gw_mod.EdgeGateway()
    await g.buffer.initialize()
    async with rt:
        await g.configure()
        assert (g.gateway_id, g.tenant_id, g.config_version, g.config_origin) == (GW, TENANT, 1, "remote")
        assert g.publisher._username == "gw-0f0e0d0c-0001"           # usuário MQTT do cadastro
        client = _attach_fake_broker(g)
        g._running = True
        g._start_drivers()
        fleet_task = asyncio.create_task(g._fleet_loop())
        try:
            for _ in range(100):
                if g.counter_store and g.counter_store.get(PULSE) >= 3:
                    break
                await asyncio.sleep(0.1)
            store = g.counter_store
            before = store.get(PULSE)
            assert before >= 3 and fleet.heartbeats and fleet.heartbeats[0]["config_version"] == 1
            # o servidor muda o mapa (nova tag): o heartbeat avisa e o gateway recarrega a quente
            fleet.version = 2
            for _ in range(60):
                if g.config_version == 2:
                    break
                await asyncio.sleep(0.1)
            assert g.config_version == 2 and g.reloads == 1
            assert g.counter_store is store                           # mesmo contador persistido (SQLite)
            for _ in range(100):
                if store.get(PULSE) >= before + 3:
                    break
                await asyncio.sleep(0.1)
            await asyncio.sleep(1.2)                                  # 1 heartbeat com a versão nova
        finally:
            g._running = False
            fleet_task.cancel()
            await asyncio.gather(fleet_task, return_exceptions=True)
            await g._stop_drivers()
    key_of = {t["tag_id"]: t["key"] for t in fleet.maps[2]["tags"]}
    pulses = [p["value"] for t, p in client.sent if "/sensors/" in t and key_of.get(t.rsplit("/", 1)[1]) == PULSE]
    assert pulses == sorted(pulses) and pulses[-1] >= before + 3      # acumulado nunca volta (sem reset no reload)
    assert {key_of.get(t.rsplit("/", 1)[1]) for t, _ in client.sent if "/sensors/" in t} >= \
        {"montagem.fixacao.a2.cycles"}                                # tag nova lida depois do reload
    hb = fleet.heartbeats[-1]
    assert hb["config_version"] == 2 and hb["version"] == gw_mod.__version__ and hb["buffer_depth"] == 0
    assert hb["uptime_s"] >= 0 and isinstance(hb["errors"], list)
    assert [c["inm"] for c in fleet.config_calls][:2] == [None, '"1-abc"']   # ETag da cópia local
    cache = json.loads((tmp_path / "cfg.json").read_text())
    assert cache["config_version"] == 2 and TOKEN not in json.dumps(cache)
    g.counter_store.close()
    await g.fleet.close()
    await g.buffer.close()


async def test_offline_restart_uses_saved_credentials_and_cached_config(fleet, tmp_path, monkeypatch):
    _settings(monkeypatch, tmp_path, fleet)
    g = gw_mod.EdgeGateway()
    await g.configure()                                              # 1ª partida: cadastra e baixa a config
    assert g.config_version == 1
    # servidor fora do ar, código já usado (removido do ambiente): sobe com a credencial e a cópia local
    _settings(monkeypatch, tmp_path, fleet, server_url=f"http://127.0.0.1:{free_port()}", enroll_code="")
    g2 = gw_mod.EdgeGateway()
    await g2.configure()
    assert (g2.gateway_id, g2.config_origin, g2.config_version) == (GW, "cache", 1)
    assert len(g2.plant_map.tags) == len(fleet.maps[1]["tags"])
    # heartbeat falha sem derrubar nada e vira erro reportado no próximo heartbeat
    assert await g2.fleet_heartbeat_once() is False
    assert any("inacessível" in e for e in g2.current_errors())
    await g2.fleet.close()


async def test_bad_map_on_reload_keeps_running_config(fleet, tmp_path, monkeypatch):
    _settings(monkeypatch, tmp_path, fleet)
    g = gw_mod.EdgeGateway()
    await g.configure()
    _attach_fake_broker(g)
    broken = copy.deepcopy(fleet.maps[1])
    broken["sources"][0]["protocol"] = "profibus"
    fleet.maps[3] = broken
    fleet.version = 3
    assert await g.fleet_heartbeat_once() is False                   # servidor avisou; mapa inválido recusado
    assert g.config_version == 1 and g.reloads == 0 and len(g.plant_map.tags) == 2
    assert any("configuração" in e for e in g.current_errors())
    fleet.version = 2
    assert await g.fleet_heartbeat_once() is True and g.config_version == 2
    await g.fleet.close()


def test_heartbeat_body_shape():
    b = heartbeat_body("0.5.0", -3.0, 7, ["x" * 500] * 30, 4)
    assert b["uptime_s"] == 0.0 and b["buffer_depth"] == 7 and len(b["errors"]) == 20 and len(b["errors"][0]) == 200
    assert b["config_version"] == 4 and b["version"] == "0.5.0"


async def test_heartbeat_revoked_token(fleet):
    client = FleetClient(fleet.url, GW, "fcg_revoked")
    with pytest.raises(HeartbeatError) as e:
        await client.heartbeat(heartbeat_body("0.5.0", 1, 0, [], 1))
    assert e.value.status == 401 and "revogado" in str(e.value)
    await client.close()
