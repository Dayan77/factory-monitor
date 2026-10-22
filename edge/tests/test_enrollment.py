"""Cadastro do gateway (edge/src/enrollment.py): troca do código por credenciais, arquivo 0600, reuso, erros."""

import json
import os
import stat

import pytest
from aiohttp import web

from edge.src import enrollment as en
from edge.src import gateway as gw_mod
from edge.tests.conftest import free_port

GW_ID = "5b0c3f0e-1111-4222-8333-944445555666"
TENANT = "11111111-1111-1111-1111-111111111111"
CODE = "ABCD-EFGH-JKLM"


class FakeServer:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail_first = 0          # quantas respostas 503 antes de aceitar
        self.used = False
        app = web.Application()
        app.router.add_post("/api/gateways/enroll", self.enroll)
        self.app = app

    async def enroll(self, request):
        body = await request.json()
        self.calls.append(body)
        if self.fail_first:
            self.fail_first -= 1
            return web.json_response({"detail": "indisponível"}, status=503)
        if body.get("code") != CODE or self.used:
            return web.json_response({"detail": "Código de cadastro inválido ou expirado."}, status=401)
        self.used = True
        return web.json_response({
            "gateway_id": GW_ID, "tenant_id": TENANT, "token": "fcg_secret-token-value",
            "config_url": f"/api/gateways/{GW_ID}/config", "heartbeat_url": f"/api/gateways/{GW_ID}/heartbeat",
            "mqtt": {"username": "gw-5b0c3f0e-5666", "password": "mqtt-pass", "topic_prefix": f"factory/{TENANT}/"}})


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


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


async def test_enroll_stores_credentials_0600_and_reuses_them(server, tmp_path):
    path = tmp_path / "secrets" / "gateway_credentials.json"
    creds = await en.ensure_enrolled(str(path), server.url, CODE)
    assert (creds.gateway_id, creds.tenant_id, creds.token) == (GW_ID, TENANT, "fcg_secret-token-value")
    assert (creds.mqtt_username, creds.mqtt_password) == ("gw-5b0c3f0e-5666", "mqtt-pass")
    assert server.calls == [{"code": CODE}]
    assert _mode(path) == 0o600 and _mode(path.parent) == 0o700
    saved = json.loads(path.read_text())
    assert saved["token"] == "fcg_secret-token-value" and saved["server_url"] == server.url
    assert not [p for p in path.parent.iterdir() if p.name.startswith(".gwcred-")]     # sem temporário
    # 2ª partida: usa o arquivo, não chama o servidor (o código é de uso único)
    again = await en.ensure_enrolled(str(path), server.url, CODE)
    assert again == creds and len(server.calls) == 1
    assert "fcg_secret" not in json.dumps(creds.redacted()) and creds.redacted()["mqtt_password"] == "***"


async def test_invalid_code_is_final_and_writes_nothing(server, tmp_path):
    path = tmp_path / "cred.json"
    with pytest.raises(en.EnrollmentError) as e:
        await en.ensure_enrolled(str(path), server.url, "WRONG-CODE-0000", retry_delays=(0,))
    assert not e.value.retry and "inválido" in str(e.value)
    assert not path.exists() and len(server.calls) == 1             # não insiste com código errado


async def test_transient_server_error_is_retried(server, tmp_path):
    server.fail_first = 2
    creds = await en.ensure_enrolled(str(tmp_path / "c.json"), server.url, CODE, retry_delays=(0,))
    assert creds.gateway_id == GW_ID and len(server.calls) == 3


async def test_server_unreachable_is_retryable(tmp_path):
    with pytest.raises(en.EnrollmentError) as e:
        await en.ensure_enrolled(str(tmp_path / "c.json"), f"http://127.0.0.1:{free_port()}", CODE,
                                 retry_delays=(0,), max_attempts=2)
    assert e.value.retry and "inacessível" in str(e.value)


def test_plain_http_only_for_localhost():
    assert en.check_server_url("https://app.factoryconnect.tech/") == "https://app.factoryconnect.tech"
    assert en.check_server_url("http://localhost:8000") == "http://localhost:8000"
    assert en.check_server_url("https://x.example/api/gateways/abc/config") == "https://x.example"
    with pytest.raises(en.EnrollmentError, match="https"):
        en.check_server_url("http://10.0.0.5:8000")
    assert en.check_server_url("http://10.0.0.5:8000", allow_insecure=True) == "http://10.0.0.5:8000"
    with pytest.raises(en.EnrollmentError):
        en.check_server_url("ftp://x")


async def test_no_code_no_credentials_means_legacy_mode(tmp_path):
    assert await en.ensure_enrolled(str(tmp_path / "none.json"), "", "") is None
    with pytest.raises(en.EnrollmentError, match="SERVER_URL"):
        await en.ensure_enrolled(str(tmp_path / "none.json"), "", CODE)


def test_world_readable_credentials_are_fixed(tmp_path):
    path = tmp_path / "c.json"
    en.save_credentials(path, en.Credentials(GW_ID, "fcg_x", "https://s"))
    os.chmod(path, 0o644)
    creds = en.load_credentials(path)
    assert creds.gateway_id == GW_ID and _mode(path) == 0o600
    path.write_text("{}")
    with pytest.raises(en.EnrollmentError, match="inválido"):
        en.load_credentials(path)


async def test_gateway_bootstrap_uses_enrolled_identity_and_mqtt_user(server, tmp_path, monkeypatch):
    s = gw_mod.settings
    for k, v in {"buffer_db_path": str(tmp_path / "b.db"), "tenant_id": "", "gateway_id": "", "encryption_key": "",
                 "mqtt_username": "", "mqtt_password": "", "server_url": server.url, "enroll_code": CODE,
                 "gateway_credentials_path": str(tmp_path / "cred.json")}.items():
        monkeypatch.setattr(s, k, v)
    g = gw_mod.EdgeGateway()
    creds = await g.bootstrap_identity()
    assert creds.gateway_id == GW_ID and g.gateway_id == GW_ID and g.tenant_id == TENANT
    assert (g.publisher._username, g.publisher._password) == ("gw-5b0c3f0e-5666", "mqtt-pass")
    assert g._remote_settings() == (server.url, server.url, "fcg_secret-token-value")
    # credencial MQTT do ambiente tem precedência
    monkeypatch.setattr(s, "mqtt_username", "env-user")
    monkeypatch.setattr(s, "mqtt_password", "env-pass")
    g2 = gw_mod.EdgeGateway()
    await g2.bootstrap_identity()
    assert g2.publisher._username == "env-user" and len(server.calls) == 1


def test_cli_enroll(server, tmp_path, capsys):
    # o servidor fake roda no loop do pytest-asyncio; o CLI usa asyncio.run → testado só o caminho de erro
    assert en.main(["--server", "http://10.1.1.1", "--code", CODE, "--credentials", str(tmp_path / "c.json")]) == 2
    assert "https" in capsys.readouterr().err
