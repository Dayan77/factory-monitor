"""Comandos puxados (docs/sprint6.md §4.2): navegação OPC UA pelo gateway contra o servidor OPC UA da planta virtual.

O servidor nunca conecta na fábrica: o heartbeat traz `commands`, o gateway navega a fonte configurada no SEU mapa
(credenciais locais via ${VAR}) e devolve só a estrutura dos nós com o próprio token.
"""

import asyncio
import json
import logging

import pytest
from aiohttp import web

from edge.src import gateway as gw_mod
from edge.src.commands import (MAX_DEPTH, MAX_NODES, CommandError, CommandRunner, browse_opcua, parse_command,
                               redact)
from edge.tests.conftest import free_port
from edge.tests.test_plant_map import provisioned_doc
from edge.virtual_plant.model import PlantModel
from edge.virtual_plant.runtime import VirtualPlantRuntime

logging.getLogger("asyncua").setLevel(logging.ERROR)
GW = "0f0e0d0c-0000-4000-8000-0000000000c1"
TENANT = "11111111-1111-1111-1111-111111111111"
TOKEN = "fcg_commands-token"
SECRET = "s3cr3t-Opc-Pass"
CMD1 = "8a0c7e4e-1111-4c1d-9a49-0000000000b1"
NODE_FIELDS = {"node_id", "browse_name", "display_name", "node_class", "data_type", "access_level", "parent_id"}


@pytest.fixture
async def plant():
    rt = VirtualPlantRuntime(PlantModel(seed=42, faults=False), speed=1, opcua_port=free_port(),
                             modbus_port=free_port(), status_port=None, update_ms=50)
    async with rt:
        yield rt


# ── contrato do comando ─────────────────────────────────────────────────────

def test_parse_command_validates_and_caps_limits():
    cid, cmd = parse_command({"id": CMD1, "type": "opcua_browse", "source_id": "opcua-plc", "max_depth": 99,
                              "max_nodes": 10 ** 6, "root_node_id": None})
    assert cid == CMD1 and cmd.max_depth == MAX_DEPTH == 6 and cmd.max_nodes == MAX_NODES == 5000
    assert cmd.root_node_id is None
    assert parse_command({"id": CMD1, "type": "reboot"})[1].startswith("tipo de comando não suportado")
    assert parse_command({"id": CMD1, "type": "opcua_browse"})[1] == "comando sem source_id"
    assert parse_command({"id": CMD1, "type": "opcua_browse", "source_id": "x", "max_depth": "3"})[1] == \
        "max_depth inválido"
    assert parse_command({"id": "../etc", "type": "opcua_browse"}) == (None, None)      # id vai na URL
    assert parse_command("x") == (None, None)


def test_redact_removes_url_credentials():
    assert redact("falhou opc.tcp://op:s3cr3t@10.0.0.5:4840/fm/") == "falhou opc.tcp://***@10.0.0.5:4840/fm/"
    assert redact("sem credencial opc.tcp://10.0.0.5:4840/") == "sem credencial opc.tcp://10.0.0.5:4840/"


# ── navegação de verdade (servidor OPC UA da planta virtual) ────────────────

async def test_browse_lists_the_virtual_plant_tree_with_structure_only(plant, monkeypatch):
    from asyncua.client.ua_client import UaClient
    requested = []
    orig = UaClient.read_attributes

    async def spy(self, nodeids, attr):
        requested.append(attr)
        return await orig(self, nodeids, attr)
    monkeypatch.setattr(UaClient, "read_attributes", spy)

    out = await browse_opcua(plant.opcua_endpoint, None, max_depth=6, max_nodes=5000, timeout_s=30)
    nodes = out["nodes"]
    assert out["truncated"] is False and nodes
    by_id = {n["node_id"]: n for n in nodes}
    assert all(set(n) == NODE_FIELDS for n in nodes)
    # árvore Planta Manaus → linha → máquina → variáveis; nada do namespace 0 no topo (Server, Aliases…)
    top = [n for n in nodes if n["parent_id"] == "i=85"]
    assert [n["display_name"] for n in top] == ["Planta Manaus"]
    run = by_id["ns=2;s=usinagem.torno.run_state"]
    assert run["node_class"] == "Variable" and run["data_type"] == "Boolean" and run["access_level"] & 1
    machine = by_id[run["parent_id"]]
    line = by_id[machine["parent_id"]]
    assert (machine["display_name"], line["display_name"]) == ("Torno CNC", "Linha de Usinagem")
    assert by_id["ns=2;s=usinagem.torno.parts_good"]["data_type"] == "UInt32"
    # todas as variáveis OPC UA da planta virtual aparecem
    expected = {f"ns=2;s={a.key}" for a in plant.addresses if a.protocol == "opcua"}
    assert expected <= set(by_id)
    # nunca lê valor de processo
    from asyncua import ua
    assert ua.AttributeIds.Value not in requested
    assert "value" not in json.dumps(out).lower().replace("value_", "")


async def test_browse_bounds_depth_nodes_and_root(plant):
    out = await browse_opcua(plant.opcua_endpoint, None, max_depth=1, max_nodes=5000, timeout_s=30)
    assert [n["display_name"] for n in out["nodes"]] == ["Planta Manaus"] and out["truncated"] is False
    out = await browse_opcua(plant.opcua_endpoint, None, max_depth=6, max_nodes=4, timeout_s=30)
    assert len(out["nodes"]) == 4 and out["truncated"] is True
    root = next(n for n in (await browse_opcua(plant.opcua_endpoint, None, 3, 100, 30))["nodes"]
                if n["display_name"] == "Torno CNC")["node_id"]
    out = await browse_opcua(plant.opcua_endpoint, root, max_depth=1, max_nodes=100, timeout_s=30)
    assert out["nodes"] and all(n["parent_id"] == root and n["node_class"] == "Variable" for n in out["nodes"])
    with pytest.raises(CommandError, match="nó raiz não encontrado"):
        await browse_opcua(plant.opcua_endpoint, "ns=2;s=nao.existe", 2, 10, 10)
    with pytest.raises(CommandError, match="root_node_id inválido"):
        await browse_opcua(plant.opcua_endpoint, "isto não é nodeid", 2, 10, 10)


async def test_browse_with_local_credentials_never_leaks_them(plant):
    ep = plant.opcua_endpoint.replace("opc.tcp://", f"opc.tcp://operador:{SECRET}@")
    out = await browse_opcua(ep, None, 6, 5000, 30)
    assert out["nodes"] and SECRET not in json.dumps(out)
    dead = f"opc.tcp://operador:{SECRET}@127.0.0.1:{free_port()}/fm/"
    with pytest.raises(CommandError) as e:
        await browse_opcua(dead, None, 2, 10, 5)
    assert SECRET not in str(e.value) and "não foi possível conectar" in str(e.value)


# ── gateway de ponta a ponta: heartbeat → navegação → POST result ───────────

class CommandServer:
    """Servidor fake: entrega os comandos no heartbeat (re-entrega enquanto sem resposta, como o real)."""

    def __init__(self, endpoint_env: str) -> None:
        doc = provisioned_doc()
        doc["gateway_id"] = GW
        doc["tags"] = [t for t in doc["tags"] if t["key"] == "usinagem.torno.run_state"]
        for s in doc["sources"]:
            if s["id"] == "opcua-plc":
                s["endpoint"] = endpoint_env           # ${VAR}: credencial só no ambiente do gateway
        self.doc = doc
        self.pending: dict[str, dict] = {}
        self.results: dict[str, list[dict]] = {}
        self.result_auth: list[str] = []
        self.fail_results = 0
        self.heartbeats = 0
        app = web.Application()
        app.router.add_get("/api/gateways/{gid}/config", self.config)
        app.router.add_post("/api/gateways/{gid}/heartbeat", self.heartbeat)
        app.router.add_post("/api/gateways/{gid}/commands/{cid}/result", self.result)
        self.app = app

    async def config(self, request):
        return web.json_response({"gateway_id": GW, "revision": "1", "config_version": 1, "plant_map": self.doc})

    async def heartbeat(self, request):
        assert request.headers["Authorization"] == f"Gateway {TOKEN}"
        self.heartbeats += 1
        return web.json_response({"status": "online", "config_version": 1, "config_changed": False,
                                  "commands": list(self.pending.values())})

    async def result(self, request):
        self.result_auth.append(request.headers.get("Authorization", ""))
        if self.fail_results > 0:
            self.fail_results -= 1
            return web.json_response({"detail": "indisponível"}, status=503)
        cid = request.match_info["cid"]
        if cid not in self.pending:
            return web.json_response({"detail": "Comando já encerrado (done)."}, status=409)
        self.results.setdefault(cid, []).append(await request.json())
        self.pending.pop(cid)
        return web.json_response({"status": "done"})


@pytest.fixture
async def cmd_server():
    fake = CommandServer("opc.tcp://${OPC_USER}:${OPC_PASS}@127.0.0.1:${VIRTUAL_PLANT_OPCUA_PORT}"
                         "/factoryconnect/virtual-plant/")
    runner = web.AppRunner(fake.app)
    await runner.setup()
    port = free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    fake.url = f"http://127.0.0.1:{port}"
    yield fake
    await runner.cleanup()


def _settings(monkeypatch, tmp_path, url):
    values = {"buffer_db_path": str(tmp_path / "buf.db"), "counter_db_path": str(tmp_path / "cnt.db"),
              "tenant_id": TENANT, "gateway_id": GW, "encryption_key": "", "mqtt_username": "", "mqtt_password": "",
              "server_url": url, "enroll_code": "", "gateway_credentials_path": str(tmp_path / "cred.json"),
              "gateway_config_cache": str(tmp_path / "cfg.json"), "gateway_config_url": "", "gateway_token": TOKEN,
              "plant_map_path": "", "gateway_heartbeat_s": 1.0, "gateway_config_poll_s": 3600.0,
              "gateway_browse_timeout_s": 20.0}
    for k, v in values.items():
        monkeypatch.setattr(gw_mod.settings, k, v)


async def test_gateway_runs_browse_command_from_heartbeat_and_posts_result(plant, cmd_server, tmp_path,
                                                                        monkeypatch, capsys):
    monkeypatch.setenv("OPC_USER", "operador")
    monkeypatch.setenv("OPC_PASS", SECRET)
    monkeypatch.setenv("VIRTUAL_PLANT_OPCUA_PORT", str(plant.opcua_port))
    monkeypatch.setenv("VIRTUAL_PLANT_HOST", "127.0.0.1")
    monkeypatch.setenv("VIRTUAL_PLANT_MODBUS_PORT", str(plant.modbus_port))
    _settings(monkeypatch, tmp_path, cmd_server.url)
    g = gw_mod.EdgeGateway()
    await g.configure()
    assert g.fleet is not None and g.commands is not None
    cmd_server.pending = {
        CMD1: {"id": CMD1, "type": "opcua_browse", "source_id": "opcua-plc", "root_node_id": None, "max_depth": 6,
               "max_nodes": 5000, "expires_at": "2099-01-01T00:00:00+00:00"},
        "c2": {"id": "c2", "type": "opcua_browse", "source_id": "modbus-plc", "max_depth": 2, "max_nodes": 10},
        "c3": {"id": "c3", "type": "opcua_browse", "source_id": "plc-que-nao-existe", "max_depth": 2,
               "max_nodes": 10},
    }
    cmd_server.fail_results = 1                      # 1º POST falha: o resultado guardado é reenviado
    try:
        for _ in range(12):
            await g.fleet_heartbeat_once()           # o servidor re-entrega o que ainda não foi respondido
            await asyncio.sleep(0.3)
            await g.commands.drain(30)
            if not cmd_server.pending:
                break
        assert not cmd_server.pending, cmd_server.pending
    finally:
        await g.commands.close()
        await g.fleet.close()
    body = cmd_server.results[CMD1][0]
    assert set(body) == {"nodes", "truncated"} and body["truncated"] is False
    assert {"ns=2;s=usinagem.torno.run_state", "ns=2;s=usinagem.centro.run_state"} <= \
        {n["node_id"] for n in body["nodes"]}
    assert all(set(n) == NODE_FIELDS for n in body["nodes"])
    assert cmd_server.results["c2"][0]["error"].startswith("a fonte 'modbus-plc' não é OPC UA")
    assert "não está configurada neste gateway" in cmd_server.results["c3"][0]["error"]
    assert all(len(v) == 1 for v in cmd_server.results.values())        # cada comando respondido uma vez
    assert g.commands.executed == 1                                       # navegou uma vez só (reenvio = cache)
    assert set(cmd_server.result_auth) == {f"Gateway {TOKEN}"}
    # a credencial OPC UA (expandida do ambiente) nunca sai do gateway: nem no resultado nem no log
    assert SECRET not in json.dumps(cmd_server.results) and SECRET not in capsys.readouterr().out


async def test_runner_dedupes_redelivery_and_survives_browse_errors():
    calls = []

    async def fake_browse(endpoint, root, depth, nodes, timeout):
        calls.append(endpoint)
        if "boom" in endpoint:
            raise RuntimeError(f"falhou {endpoint}")
        return {"nodes": [{"node_id": "ns=2;s=x"}], "truncated": False}

    sent: list[tuple[str, dict]] = []
    runner = CommandRunner("http://127.0.0.1:1", GW, TOKEN,
                           lambda sid: ("opcua", {"a": "opc.tcp://ok/", "b": f"opc.tcp://u:{SECRET}@boom/"}[sid]),
                           browse=fake_browse)

    async def deliver(cid, body):
        sent.append((cid, body))
        runner._known[cid] = "done"
        return True
    runner._deliver = deliver
    cmd = {"id": "a1", "type": "opcua_browse", "source_id": "a"}
    assert runner.submit([cmd, cmd]) == 1                                # mesmo id no mesmo heartbeat
    await runner.drain()
    assert runner.submit([cmd]) == 0                                     # re-entrega depois de respondido
    assert runner.submit([{"id": "b1", "type": "opcua_browse", "source_id": "b"}]) == 1
    await runner.drain()
    assert [c for c, _ in sent] == ["a1", "b1"] and len(calls) == 2
    err = sent[1][1]["error"]
    assert "RuntimeError" in err and SECRET not in err
