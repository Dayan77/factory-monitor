"""Fontes `ueapd` (boxes ueaPowerDriver) vindas da configuração REMOTA do gateway (sprint "onboarding sem passos
manuais", C2): gateway cadastrado com ENROLL_CODE e SEM PLANT_MAP_PATH recebe do servidor (GET
/api/gateways/{id}/config) `sources[{protocol: "ueapd", base_url, ...}]` + tags com seletor e roda igual ao mapa
local; recarga (`config_changed`) com base_url nova / tags novas mantém offsets, dedupe e totais (COUNTER_DB_PATH,
estado por id da fonte); seletor inválido vindo do servidor sai do mapa com aviso sem derrubar as outras fontes.

Servidor da frota e boxes falsos em processo (aiohttp; o box é o FakeBox de test_ueapd.py)."""

import asyncio
import copy
import json
import time
import uuid

import pytest
from aiohttp import web
from structlog.testing import capture_logs

from edge.src import gateway as gw_mod
from edge.src.plant_map import PlantMapError, parse_plant_map
from edge.src.protocols.ueapd import UeapdDriver
from edge.src.remote_config import load_gateway_config
from edge.tests.conftest import free_port
from edge.tests.test_ueapd import FakeBox

GW = "0f0e0d0c-0000-4000-8000-0000000000ae"
TENANT = "22222222-2222-2222-2222-222222222222"
CODE = "UEAP-DREM-OTE1"
TOKEN = "fcg_ueapd-remote-token"


def tid(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def ueapd_source(sid: str, base_url: str, line: str = "hipot", **extra) -> dict:
    """Dispositivo ueapd como o servidor o grava em gateways.devices[] (contrato C2)."""
    return {"id": sid, "protocol": "ueapd", "base_url": base_url, "line": line, "poll_interval_s": 0.2,
            "timeout_s": 2, "timestamps": "gateway", "max_clock_skew_s": 2, "backfill": "none",
            "default_fault_code": 301, "fault_codes": {"ERRO_TRANSFERENCIA": 101}, **extra}


def ueapd_tag(key: str, source: str, role: str, selector: str, **extra) -> dict:
    """Tag `protocol = http` atribuída a um dispositivo ueapd (forma do mapa gerado pelo servidor)."""
    return {"key": key, "tag_id": tid(key), "source": source, "semantic_role": role, "address": selector,
            "unit": "", "scale": 1.0, "offset": 0.0, "poll_interval_ms": 2000, "name": key.rsplit(".", 1)[1],
            "machine_id": str(uuid.uuid5(uuid.NAMESPACE_URL, source)), **extra}


M1_TAGS = {
    "run": ("run_state", "state:run"), "fault": ("fault_state", "state:fault"),
    "good": ("part_counter_good", "passes:good"), "scrap": ("part_counter_scrap", "passes:scrap"),
    "total": ("part_counter_total", "passes:total"), "c_hipot1": ("cycle_counter", "cycles:hipot_1"),
}


def m1_tags(names) -> list[dict]:
    return [ueapd_tag(f"ledd.m1.{n}", "ueapd-m1", *M1_TAGS[n]) for n in names]


def plant_map(sources: list[dict], tags: list[dict]) -> dict:
    return {"version": 1, "gateway_id": GW, "tenant_id": TENANT, "heartbeat_s": 10, "pulse_poll_ms": 20,
            "sources": sources, "tags": tags}


class FleetServer:
    """POST /enroll, GET /config (ETag), POST /heartbeat (config_changed) — como server/src/routers/gateways."""

    def __init__(self, maps: dict[int, dict]) -> None:
        self.maps = maps
        self.version = 1
        self.heartbeats: list[dict] = []
        app = web.Application()
        app.router.add_post("/api/gateways/enroll", self.enroll)
        app.router.add_get("/api/gateways/{gid}/config", self.config)
        app.router.add_post("/api/gateways/{gid}/heartbeat", self.heartbeat)
        self.app = app
        self.runner = None
        self.url = ""

    def _auth(self, request) -> bool:
        return request.headers.get("Authorization") == f"Gateway {TOKEN}" and request.match_info["gid"] == GW

    async def enroll(self, request):
        if (await request.json()).get("code") != CODE:
            return web.json_response({"detail": "inválido"}, status=401)
        return web.json_response({"gateway_id": GW, "tenant_id": TENANT, "token": TOKEN,
                                  "mqtt": {"username": "gw-ueapd", "password": "pw"}})

    async def config(self, request):
        if not self._auth(request):
            return web.json_response({}, status=401)
        etag = f'"{self.version}-x"'
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

    async def start(self) -> None:
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        port = free_port()
        await web.TCPSite(self.runner, "127.0.0.1", port).start()
        self.url = f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        await self.runner.cleanup()


class FakeBroker:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def publish(self, topic, payload, qos=0):
        self.sent.append((topic, json.loads(payload)))

    def values(self, key: str) -> list[float]:
        t = tid(key)
        return [p["value"] for topic, p in self.sent if topic == f"factory/{TENANT}/sensors/{t}"]

    def last(self, key: str):
        v = self.values(key)
        return v[-1] if v else None


def _settings(monkeypatch, tmp_path, server_url: str) -> None:
    """Gateway da frota: só SERVER_URL + ENROLL_CODE (o comando de instalação) — nenhum PLANT_MAP_PATH."""
    values = {"buffer_db_path": str(tmp_path / "buf.db"), "counter_db_path": str(tmp_path / "cnt.db"),
              "tenant_id": "", "gateway_id": "", "encryption_key": "", "mqtt_username": "", "mqtt_password": "",
              "server_url": server_url, "enroll_code": CODE,
              "gateway_credentials_path": str(tmp_path / "cred.json"),
              "gateway_config_cache": str(tmp_path / "cfg.json"), "gateway_config_url": "", "gateway_token": "",
              "plant_map_path": "", "gateway_heartbeat_s": 1.0, "gateway_config_poll_s": 3600.0,
              "allow_insecure_http": True}
    for k, v in values.items():
        monkeypatch.setattr(gw_mod.settings, k, v)


async def until(cond, timeout: float = 10.0, msg: str = "") -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        if loop.time() > end:
            raise AssertionError(f"tempo esgotado: {msg}")
        await asyncio.sleep(0.05)


def _ueapd(g, sid: str) -> UeapdDriver:
    return next(d for d in g.drivers if isinstance(d, UeapdDriver) and d.sid == sid)


@pytest.fixture
async def started():
    """Sobe servidor/boxes registrados e derruba tudo no fim (mesmo com falha no meio)."""
    things: list = []
    gws: list = []

    async def add(obj):
        await obj.start()
        things.append(obj)
        return obj

    yield add, gws
    for g in gws:
        g._running = False
        await g._stop_drivers()
        await g._stop_sender()
        if g.fleet is not None:
            await g.fleet.close()
        if g.counter_store is not None:
            g.counter_store.close()
        await g.buffer.close()
    for obj in reversed(things):
        await obj.stop()


async def _boot(g) -> FakeBroker:
    await g.buffer.initialize()
    await g.configure()
    broker = FakeBroker()
    g.publisher._client, g.publisher.is_connected = broker, True
    g._running = True
    g.start_sender()
    g._start_drivers()
    return broker


# ── remoto = local ────────────────────────────────────────────────────────
async def test_enrolled_gateway_runs_ueapd_from_server_and_reload_keeps_counters(started, tmp_path, monkeypatch):
    add, gws = started
    trace = tmp_path / "m1-trace"
    box_old = await add(FakeBox(trace))
    box_new = await add(FakeBox(trace))                 # o MESMO box com outro IP (mesmo trace)
    monkeypatch.setenv("UEAPD_M1_URL", box_old.url)
    v1 = plant_map([ueapd_source("ueapd-m1", "${UEAPD_M1_URL:-http://192.168.1.15:8003}")],
                   m1_tags(["run", "fault", "good", "scrap", "total", "c_hipot1"]))
    # v2: IP novo (literal), `scrap` removida, `cycles:laser_1` nova
    v2 = plant_map([ueapd_source("ueapd-m1", box_new.url + "/")],
                   m1_tags(["run", "fault", "good", "total", "c_hipot1"])
                   + [ueapd_tag("ledd.m1.c_laser1", "ueapd-m1", "cycle_counter", "cycles:laser_1")])
    fleet = await add(FleetServer({1: v1, 2: v2}))
    _settings(monkeypatch, tmp_path, fleet.url)

    g = gw_mod.EdgeGateway()
    gws.append(g)
    broker = await _boot(g)
    assert (g.gateway_id, g.tenant_id, g.config_origin, g.config_version) == (GW, TENANT, "remote", 1)
    src = g.plant_map.sources["ueapd-m1"]
    assert (src.protocol, src.base_url, src.line, src.fault_codes) == \
        ("ueapd", box_old.url, "hipot", {"ERRO_TRANSFERENCIA": 101})       # ${VAR:-padrão} expandido no gateway
    cache = json.loads((tmp_path / "cfg.json").read_text())
    assert cache["plant_map"]["sources"][0]["base_url"].startswith("${UEAPD_M1_URL")   # cópia sem expandir
    drv = _ueapd(g, "ueapd-m1")
    await until(lambda: drv.stats["polls"] >= 1, msg="1º contato com o box")   # backfill none: daqui em diante

    box_old.add_pass(ok=True)
    box_old.add_pass(ok=True)
    box_old.add_pass(ok=False)
    await until(lambda: broker.last("ledd.m1.total") == 3, msg="3 passes publicados")
    await until(lambda: broker.last("ledd.m1.run") == 1, msg="run_state")
    assert broker.last("ledd.m1.good") == 2 and broker.last("ledd.m1.scrap") == 1
    assert broker.last("ledd.m1.c_hipot1") == 3 and broker.last("ledd.m1.fault") == 0
    store = g.counter_store
    offsets = store.kv_prefix("ueapd:ueapd-m1:off:")
    assert offsets and all(int(v) > 0 for v in offsets.values())

    # servidor muda o mapa: o heartbeat avisa e o gateway recarrega a quente
    fleet.version = 2
    old_pulls = box_old.pulls
    assert await g.fleet_heartbeat_once() is True
    assert (g.config_version, g.reloads, g.counter_store) == (2, 1, store)
    drv2 = _ueapd(g, "ueapd-m1")
    assert drv2 is not drv and drv2.base == box_new.url and drv2.source.base_url == box_new.url
    assert store.kv_prefix("ueapd:ueapd-m1:off:") == offsets             # offsets da fonte preservados
    await until(lambda: drv2.stats["polls"] >= 2, msg="driver novo rodando")
    assert box_new.pulls == 0                       # nada a reler: o arquivo não cresceu (sem recontagem)
    assert broker.last("ledd.m1.total") == 3        # re-oferta do acumulado = mesmo valor

    box_new.add_pass(ok=True)
    box_new.add_pass(ok=True)
    await until(lambda: broker.last("ledd.m1.total") == 5, msg="passes depois da recarga")
    await until(lambda: broker.last("ledd.m1.c_laser1") == 2, msg="tag nova")
    assert broker.last("ledd.m1.good") == 4 and broker.last("ledd.m1.c_hipot1") == 5
    assert box_old.pulls == old_pulls               # o IP velho não é mais consultado
    for key in ("ledd.m1.total", "ledd.m1.good", "ledd.m1.c_hipot1"):
        vals = broker.values(key)
        assert vals == sorted(vals), f"{key} voltou: {vals}"              # acumulado nunca volta
    n_scrap = len(broker.values("ledd.m1.scrap"))
    await asyncio.sleep(0.5)
    assert len(broker.values("ledd.m1.scrap")) == n_scrap                # tag removida não publica mais
    assert store.get("ledd.m1.total") == 5 and store.get("ledd.m1.good") == 4      # sem contagem dupla
    assert drv2.stats["duplicates"] == 0
    assert g.current_errors() == []


async def test_reload_back_and_forth_rereads_nothing(started, tmp_path, monkeypatch):
    """Duas recargas seguidas (IP novo e de volta) com o box crescendo entre elas: cada passe conta uma vez."""
    add, gws = started
    trace = tmp_path / "trace"
    a, b = await add(FakeBox(trace)), await add(FakeBox(trace))
    tags = m1_tags(["good", "total"])
    maps = {1: plant_map([ueapd_source("ueapd-m1", a.url)], tags),
            2: plant_map([ueapd_source("ueapd-m1", b.url)], tags),
            3: plant_map([ueapd_source("ueapd-m1", a.url, poll_interval_s=0.3)], tags)}
    fleet = await add(FleetServer(maps))
    _settings(monkeypatch, tmp_path, fleet.url)
    g = gw_mod.EdgeGateway()
    gws.append(g)
    broker = await _boot(g)
    await until(lambda: _ueapd(g, "ueapd-m1").stats["polls"] >= 1)
    expected = 0
    for version in (2, 3):
        for _ in range(3):
            a.add_pass()
        expected += 3
        await until(lambda: broker.last("ledd.m1.total") == expected, msg=f"total {expected}")
        fleet.version = version
        assert await g.fleet_heartbeat_once() is True
    a.add_pass()
    expected += 1
    await until(lambda: broker.last("ledd.m1.total") == expected)
    await asyncio.sleep(0.6)
    assert g.counter_store.get("ledd.m1.total") == expected == 7 and broker.last("ledd.m1.good") == 7


# ── item inválido vindo do servidor ───────────────────────────────────────
async def test_invalid_selector_from_server_is_dropped_and_other_sources_run(started, tmp_path, monkeypatch):
    add, gws = started
    box1 = await add(FakeBox(tmp_path / "m1"))
    box2 = await add(FakeBox(tmp_path / "m2", line="ats2"))
    good_tags = m1_tags(["run", "total"]) + [
        ueapd_tag("ledd.m2.run", "ueapd-m2", "run_state", "state:run"),
        ueapd_tag("ledd.m2.c_ats1_p2", "ueapd-m2", "cycle_counter", "cycles:ats2_1:P2")]
    bad_tags = [ueapd_tag("ledd.m1.bad_sel", "ueapd-m1", "cycle_counter", "cycles:hipot 1"),
                ueapd_tag("ledd.m1.bad_role", "ueapd-m1", "analog", "passes:good")]
    v1 = plant_map([ueapd_source("ueapd-m1", box1.url), ueapd_source("ueapd-m2", box2.url, line="ats2")],
                   good_tags + bad_tags)
    # v2: a fonte m2 fica com ${VAR} sem valor nem padrão → só ela (e as tags dela) sai; m1 continua
    v2 = copy.deepcopy(v1)
    v2["sources"][1]["base_url"] = "${UEAPD_M2_URL_NAO_DEFINIDA}"
    monkeypatch.delenv("UEAPD_M2_URL_NAO_DEFINIDA", raising=False)
    fleet = await add(FleetServer({1: v1, 2: v2}))
    _settings(monkeypatch, tmp_path, fleet.url)

    g = gw_mod.EdgeGateway()
    gws.append(g)
    with capture_logs() as logs:
        broker = await _boot(g)
    assert g.config_origin == "remote"                     # o mapa foi aceito (sem cair na cópia/arquivo)
    assert {t.key for t in g.plant_map.tags} == {t["key"] for t in good_tags}
    rejected = g.plant_map.rejected
    assert len(rejected) == 2
    assert "ledd.m1.bad_sel" in rejected[0] and "estágio inválido" in rejected[0]
    assert "ledd.m1.bad_role" in rejected[1] and "part_counter_good" in rejected[1]
    events = [e for e in logs if e["event"] in ("remote_config.item_rejected", "edge_gateway.config_item_rejected")]
    assert any("cycles:hipot 1" in e["reason"] for e in events)
    assert sum("configuração: tag ledd.m1.bad" in e for e in g.current_errors()) == 2   # vai no heartbeat

    await until(lambda: broker.last("ledd.m1.run") == 1 and broker.last("ledd.m2.run") == 1, msg="duas fontes")
    box2.add_pass(events=[{"stage": "ats2_1", "t": time.time(), "ok": True, "posto": 1}])
    box1.add_pass()
    await until(lambda: broker.last("ledd.m2.c_ats1_p2") == 1 and broker.last("ledd.m1.total") == 1)
    assert await g.fleet_heartbeat_once() is False         # versão 1: nada muda
    assert any("bad_sel" in e for e in fleet.heartbeats[-1]["errors"])

    fleet.version = 2
    with capture_logs() as logs:
        assert await g.fleet_heartbeat_once() is True
    assert g.config_version == 2 and set(g.plant_map.sources) == {"ueapd-m1"}
    assert any("fonte ueapd-m2 recusada" in m and "UEAPD_M2_URL_NAO_DEFINIDA" in m for m in g.plant_map.rejected)
    assert any("ledd.m2.run" in m and "recusada 'ueapd-m2'" in m for m in g.plant_map.rejected)
    assert [d.sid for d in g.drivers] == ["ueapd-m1"]
    box1.add_pass()
    await until(lambda: broker.last("ledd.m1.total") == 2, msg="m1 segue depois de m2 recusada")


# ── contrato do mapa (servidor ↔ edge) ────────────────────────────────────
def test_lenient_parse_vs_strict_local_file():
    doc = plant_map([ueapd_source("ueapd-m1", "http://10.0.0.5:8003")],
                    m1_tags(["run"]) + [ueapd_tag("ledd.m1.x", "ueapd-m1", "analog", "measure:hipot_1:")])
    with pytest.raises(PlantMapError, match="measure"):
        parse_plant_map(doc, env={})                       # PLANT_MAP_PATH: estrito (erro de digitação = não sobe)
    pm = parse_plant_map(doc, env={}, strict=False)
    assert [t.key for t in pm.tags] == ["ledd.m1.run"] and pm.rejected[0].startswith("tag ledd.m1.x recusada")
    # estrutura continua recusando o mapa inteiro, mesmo no modo tolerante
    for broken in ({**doc, "version": 2},
                   {**doc, "sources": doc["sources"] + [{"id": "x", "protocol": "profibus"}]},
                   {**doc, "tags": doc["tags"] + [dict(doc["tags"][0])]}):
        with pytest.raises(PlantMapError):
            parse_plant_map(broken, env={}, strict=False)


@pytest.mark.parametrize("url,ok", [
    ("http://192.168.1.15:8003", True), ("https://box-m1.local", True), ("http://192.168.1.15:8003/", True),
    ("${UEAPD_M1_URL:-http://192.168.1.15:8003}", True),
    ("http://admin:senha@192.168.1.15:8003", False),      # credencial na URL iria para o log/cópia
    ("http://:8003", False), ("ftp://192.168.1.15", False), ("192.168.1.15:8003", False),
    ("http://192.168.1.15:porta", False), ("", False),
])
def test_ueapd_base_url_rules(url, ok):
    doc = plant_map([ueapd_source("ueapd-m1", url)], m1_tags(["run"]))
    if ok:
        pm = parse_plant_map(doc, env={})
        assert pm.sources["ueapd-m1"].base_url.startswith(("http://", "https://"))
        assert not pm.sources["ueapd-m1"].base_url.endswith("/")
    else:
        with pytest.raises(PlantMapError, match="base_url"):
            parse_plant_map(doc, env={})


def test_server_device_fields_map_to_source_config():
    """Campos exatos do dispositivo ueapd do servidor (gateways.devices[], contrato C2) → SourceConfig."""
    dev = {"id": "ueapd-m1", "protocol": "ueapd", "base_url": "http://192.168.1.15:8003", "line": "hipot",
           "poll_interval_s": 2, "timeout_s": 5, "timestamps": "source", "max_clock_skew_s": 3, "backfill": "latest_day",
           "default_fault_code": 302, "fault_codes": {"ERRO_TRANSFERENCIA": 101}}
    src = parse_plant_map(plant_map([dev], []), env={}).sources["ueapd-m1"]
    assert (src.base_url, src.line, src.poll_interval_s, src.timeout_s, src.timestamps, src.max_clock_skew_s,
            src.backfill, src.default_fault_code, src.fault_codes) == \
        ("http://192.168.1.15:8003", "hipot", 2.0, 5.0, "source", 3.0, "latest_day", 302, {"ERRO_TRANSFERENCIA": 101})
    minimal = parse_plant_map(plant_map([{"id": "m", "protocol": "ueapd", "base_url": "http://h:8003"}], []),
                              env={}).sources["m"]
    assert (minimal.poll_interval_s, minimal.timeout_s, minimal.timestamps, minimal.backfill,
            minimal.default_fault_code, minimal.fault_codes, minimal.line) == (2.0, 5.0, "gateway", "none", 301, {}, "")


async def test_remote_loader_without_server_uses_cache_with_ueapd(tmp_path, monkeypatch):
    """Partida offline: a cópia local com fonte ueapd é re-expandida com o ambiente atual."""
    doc = plant_map([ueapd_source("ueapd-m1", "${UEAPD_M1_URL:-http://192.168.1.15:8003}")], m1_tags(["run"]))
    cache = tmp_path / "cfg.json"
    cache.write_text(json.dumps({"cache_version": 1, "gateway_id": GW, "etag": '"1-x"', "revision": "1",
                                 "config_version": 1, "plant_map": doc}))
    loaded = await load_gateway_config(f"http://127.0.0.1:{free_port()}", GW, TOKEN, cache, timeout_s=1,
                                       env={"UEAPD_M1_URL": "http://10.1.1.1:8003"})
    assert loaded.origin == "cache" and loaded.plant_map.sources["ueapd-m1"].base_url == "http://10.1.1.1:8003"
