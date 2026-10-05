"""Configuração remota do gateway (edge/src/remote_config.py) contra um servidor falso em processo."""

import json
import os
from pathlib import Path

import pytest
from aiohttp import web

from edge.src import remote_config as rc
from edge.tests.conftest import free_port

EDGE = Path(__file__).resolve().parents[1]
GW = "gw-planta-manaus-01"
TOKEN = "gw-secret-token"


def plant_map(n_tags: int = 2, gateway_id: str = GW) -> dict:
    doc = json.loads((EDGE / "config" / "plant.json").read_text(encoding="utf-8"))
    doc["gateway_id"] = gateway_id
    doc["tags"] = doc["tags"][:n_tags]
    for i, t in enumerate(doc["tags"]):
        t["tag_id"] = f"00000000-0000-0000-0000-00000000000{i}"
    return doc


class FakeServer:
    def __init__(self) -> None:
        self.mode = "ok"
        self.calls: list[dict] = []
        self.body: dict = {"gateway_id": GW, "revision": "r1", "plant_map": plant_map(2)}
        self.etag = '"r1"'
        app = web.Application()
        app.router.add_get("/api/gateways/{gid}/config", self.config)
        self.app = app

    async def config(self, request):
        self.calls.append({"gid": request.match_info["gid"], "auth": request.headers.get("Authorization"),
                           "inm": request.headers.get("If-None-Match")})
        if request.headers.get("Authorization") != f"Gateway {TOKEN}":
            return web.json_response({"detail": "token inválido"}, status=401)
        if request.match_info["gid"] != GW:
            return web.json_response({"detail": "gateway desconhecido"}, status=404)
        if self.mode == "500":
            return web.json_response({"detail": "boom"}, status=500)
        if self.mode == "garbage":
            return web.Response(text="<html>proxy</html>", content_type="text/html")
        if self.mode == "invalid":
            return web.json_response({"plant_map": {"version": 99}})
        if request.headers.get("If-None-Match") == self.etag:
            return web.Response(status=304)
        return web.json_response(self.body, headers={"ETag": self.etag})


@pytest.fixture
async def server():
    fake = FakeServer()
    runner = web.AppRunner(fake.app)
    await runner.setup()
    port = free_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    fake.url = f"http://127.0.0.1:{port}"
    yield fake
    await runner.cleanup()


def test_config_url_forms():
    assert rc.config_url("https://srv", GW) == f"https://srv/api/gateways/{GW}/config"
    assert rc.config_url("https://srv/", GW) == f"https://srv/api/gateways/{GW}/config"
    assert rc.config_url("https://srv/x/{gateway_id}/cfg", GW) == f"https://srv/x/{GW}/cfg"
    assert rc.config_url("https://srv/api/gateways/a/config", GW) == "https://srv/api/gateways/a/config"


async def test_fetch_uses_gateway_auth_header_and_writes_cache(server, tmp_path):
    cache = tmp_path / "gw.json"
    out = await rc.load_gateway_config(server.url, GW, TOKEN, cache)
    assert out.origin == "remote" and out.revision == "r1" and len(out.plant_map.tags) == 2
    assert server.calls == [{"gid": GW, "auth": f"Gateway {TOKEN}", "inm": None}]
    saved = json.loads(cache.read_text())
    assert saved["gateway_id"] == GW and saved["etag"] == '"r1"' and saved["plant_map"]["tags"][0]["tag_id"]
    assert TOKEN not in cache.read_text()                  # o token nunca vai para a cópia local
    assert oct(os.stat(cache).st_mode & 0o777) == "0o600"


async def test_not_modified_uses_cache_with_if_none_match(server, tmp_path):
    cache = tmp_path / "gw.json"
    await rc.load_gateway_config(server.url, GW, TOKEN, cache)
    out = await rc.load_gateway_config(server.url, GW, TOKEN, cache)
    assert out.origin == "cache" and out.detail == "304"
    assert server.calls[-1]["inm"] == '"r1"'


async def test_new_revision_replaces_cache(server, tmp_path):
    cache = tmp_path / "gw.json"
    await rc.load_gateway_config(server.url, GW, TOKEN, cache)
    server.body = plant_map(3)                            # mapa v1 direto no corpo (sem envelope)
    server.etag = '"r2"'
    out = await rc.load_gateway_config(server.url, GW, TOKEN, cache)
    assert out.origin == "remote" and len(out.plant_map.tags) == 3
    assert json.loads(cache.read_text())["etag"] == '"r2"'


@pytest.mark.parametrize("mode", ["500", "garbage", "invalid"])
async def test_server_error_or_invalid_map_falls_back_to_cache_and_keeps_it(server, tmp_path, mode):
    cache = tmp_path / "gw.json"
    await rc.load_gateway_config(server.url, GW, TOKEN, cache)
    before = cache.read_text()
    server.mode = mode
    server.etag = '"other"'
    out = await rc.load_gateway_config(server.url, GW, TOKEN, cache)
    assert out.origin == "cache" and len(out.plant_map.tags) == 2 and out.detail
    assert cache.read_text() == before                    # mapa inválido nunca substitui a cópia boa


async def test_wrong_token_falls_back_to_cache(server, tmp_path):
    cache = tmp_path / "gw.json"
    await rc.load_gateway_config(server.url, GW, TOKEN, cache)
    out = await rc.load_gateway_config(server.url, GW, "wrong", cache)
    assert out.origin == "cache" and "401" in out.detail


async def test_offline_start_uses_cache(tmp_path, server):
    cache = tmp_path / "gw.json"
    await rc.load_gateway_config(server.url, GW, TOKEN, cache)
    dead = f"http://127.0.0.1:{free_port()}"
    out = await rc.load_gateway_config(dead, GW, TOKEN, cache, timeout_s=2)
    assert out.origin == "cache" and "inacessível" in out.detail


async def test_no_cache_falls_back_to_file_then_errors(tmp_path):
    dead = f"http://127.0.0.1:{free_port()}"
    f = tmp_path / "plant.local.json"
    f.write_text(json.dumps(plant_map(1)))
    out = await rc.load_gateway_config(dead, GW, TOKEN, tmp_path / "none.json", fallback_path=str(f), timeout_s=2)
    assert out.origin == "file" and len(out.plant_map.tags) == 1
    with pytest.raises(rc.RemoteConfigError):
        await rc.load_gateway_config(dead, GW, TOKEN, tmp_path / "none.json", timeout_s=2)


async def test_map_of_other_gateway_is_rejected(server, tmp_path):
    server.body = {"plant_map": plant_map(2, gateway_id="gw-outro")}
    with pytest.raises(rc.RemoteConfigError):
        await rc.load_gateway_config(server.url, GW, TOKEN, tmp_path / "gw.json")
    assert not (tmp_path / "gw.json").exists()


async def test_cache_of_other_gateway_is_ignored(tmp_path):
    cache = tmp_path / "gw.json"
    rc.write_cache(cache, plant_map(2, "gw-outro"), "gw-outro", "http://x", None, None)
    dead = f"http://127.0.0.1:{free_port()}"
    with pytest.raises(rc.RemoteConfigError):
        await rc.load_gateway_config(dead, GW, TOKEN, cache, timeout_s=2)


async def test_requires_gateway_id_and_token_missing_uses_cache(tmp_path, server):
    with pytest.raises(rc.RemoteConfigError):
        await rc.load_gateway_config(server.url, "", TOKEN, tmp_path / "gw.json")
    cache = tmp_path / "gw.json"
    await rc.load_gateway_config(server.url, GW, TOKEN, cache)
    n = len(server.calls)
    out = await rc.load_gateway_config(server.url, GW, "", cache)
    assert out.origin == "cache" and len(server.calls) == n   # sem token não chama o servidor


def test_local_gateway_id(tmp_path):
    f = tmp_path / "m.json"
    f.write_text(json.dumps({"gateway_id": "gw-x"}))
    assert rc.local_gateway_id(str(f)) == "gw-x"
    assert rc.local_gateway_id("") == "" and rc.local_gateway_id(str(tmp_path / "nope.json")) == ""


async def test_gateway_start_uses_remote_config(server, tmp_path, monkeypatch):
    """O gateway, com GATEWAY_CONFIG_URL, carrega o mapa do servidor (drivers/tags do mapa remoto)."""
    from edge.src import gateway as gwmod

    s = gwmod.settings
    monkeypatch.setattr(s, "gateway_config_url", server.url)
    monkeypatch.setattr(s, "gateway_token", TOKEN)
    monkeypatch.setattr(s, "gateway_id", GW)
    monkeypatch.setattr(s, "gateway_config_cache", str(tmp_path / "gw.json"))
    monkeypatch.setattr(s, "plant_map_path", "")
    monkeypatch.setattr(s, "buffer_db_path", str(tmp_path / "buf.db"))
    monkeypatch.setattr(s, "counter_db_path", str(tmp_path / "cnt.db"))
    monkeypatch.setattr(s, "encryption_key", "")
    g = gwmod.EdgeGateway()
    loaded = []
    monkeypatch.setattr(g, "load_plant_map", lambda pm: loaded.append(pm))

    async def noop(*_a, **_k):
        return None
    monkeypatch.setattr(g.buffer, "initialize", noop)
    monkeypatch.setattr(g.publisher, "connect", noop)
    for loop_name in ("_flush_buffer_loop", "_heartbeat_loop", "_refresh_loop"):
        monkeypatch.setattr(g, loop_name, noop)
    await g.start()
    assert loaded and loaded[0].gateway_id == GW and len(loaded[0].tags) == 2
    assert server.calls[-1]["auth"] == f"Gateway {TOKEN}"
