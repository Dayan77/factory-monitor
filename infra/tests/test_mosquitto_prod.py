"""Mosquitto de PRODUÇÃO de verdade (docker): autenticação, ACL por tenant, TLS e reload.

Sobe o serviço `mosquitto` exatamente como definido em infra/docker-compose.prod.yml (entrypoint,
command, env, volumes e healthcheck lidos do `docker compose config`), numa rede docker própria, com
certificados autoassinados descartáveis e gateways criados por scripts/mqtt_gateway.sh. Os clientes
são mosquitto_pub/mosquitto_sub em containers na mesma rede.

    .venv/bin/python -m pytest -q infra/tests/test_mosquitto_prod.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "eclipse-mosquitto:2.0.22"
TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"
SERVER_USER, SERVER_PW = "fm-backend", "server-password-0123456789"
HOST = "mqtt.test"

pytestmark = pytest.mark.skipif(shutil.which("docker") is None or shutil.which("openssl") is None,
                                reason="precisa de docker e openssl")


def sh(*args, check=True, **kw):
    return subprocess.run(args, check=check, capture_output=True, text=True, **kw)


def make_certs(d: Path) -> None:
    d.mkdir(parents=True)
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=fm-test-ca",
       "-keyout", str(d / "ca.key"), "-out", str(d / "ca.pem"))
    sh("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={HOST}",
       "-keyout", str(d / "privkey.pem"), "-out", str(d / "server.csr"))
    (d / "ext.cnf").write_text(f"subjectAltName=DNS:{HOST},DNS:localhost\n")
    sh("openssl", "x509", "-req", "-in", str(d / "server.csr"), "-CA", str(d / "ca.pem"), "-CAkey", str(d / "ca.key"),
       "-CAcreateserial", "-days", "2", "-extfile", str(d / "ext.cnf"), "-out", str(d / "fullchain.pem"))
    # outra CA (para provar que o cliente valida o certificado)
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=evil-ca",
       "-keyout", str(d / "evil.key"), "-out", str(d / "evil-ca.pem"))
    for f in d.iterdir():
        f.chmod(0o644)   # o container lê como root e copia para o usuário mosquitto


def add_gateway(gw_file: Path, user: str, tenant: str, **env) -> str:
    out = sh("bash", str(ROOT / "scripts/mqtt_gateway.sh"), "add", user, tenant,
             env={**os.environ, "MQTT_GATEWAYS_FILE": str(gw_file), "MQTT_NO_RELOAD": "1", **env}).stdout
    m = re.search(r"MQTT_PASSWORD=(\S+)", out)
    assert m, out
    return m.group(1)


class Broker:
    def __init__(self, tmp: Path):
        self.id = f"fmmqtt{uuid.uuid4().hex[:8]}"
        self.net = f"{self.id}_net"
        self.name = f"{self.id}_broker"
        self.tmp = tmp
        self.certs = tmp / "certs"
        self.gw_dir = tmp / "gw"
        self.gw_dir.mkdir()
        self.gw_file = self.gw_dir / "gateways.conf"
        self.env_file = tmp / "prod.env"
        self.tls = "true"

    def service(self) -> dict:
        self.env_file.write_text(
            "POSTGRES_DB=x\nPOSTGRES_USER=x\nPOSTGRES_PASSWORD=x\nINFLUX_ADMIN_PASSWORD=x\nINFLUX_ORG=x\n"
            "INFLUX_BUCKET=x\nINFLUX_TOKEN=x\n"
            f"MQTT_USERNAME={SERVER_USER}\nMQTT_PASSWORD={SERVER_PW}\n"
            f"MQTT_GATEWAYS_DIR={self.gw_dir}\nMQTT_TLS_CERT_DIR={self.certs}\n"
            f"MQTT_TLS_ENABLED={self.tls}\n")
        cfg = json.loads(sh("docker", "compose", "--env-file", str(self.env_file), "-f",
                            str(ROOT / "infra/docker-compose.prod.yml"), "config", "--format", "json").stdout)
        return cfg["services"]["mosquitto"]

    def run_args(self, svc: dict, name: str, extra_env: dict | None = None, publish: tuple[str, ...] = ()
                 ) -> list[str]:
        args = ["docker", "run", "-d", "--name", name, "--network", self.net, "--network-alias", HOST]
        for p in publish:
            args += ["-p", p]
        env = {**svc["environment"], **(extra_env or {})}
        for k, v in env.items():
            args += ["-e", f"{k}={v}"]
        for v in svc["volumes"]:
            if v["type"] == "bind":
                args += ["-v", f"{v['source']}:{v['target']}" + (":ro" if v.get("read_only") else "")]
            # volumes nomeados (data) ficam efêmeros no teste
        args += ["--entrypoint", svc["entrypoint"][0], IMAGE, *svc["entrypoint"][1:], *svc["command"]]
        return args

    def start(self) -> dict:
        svc = self.service()
        assert svc["image"] == IMAGE
        sh("docker", "network", "create", self.net)
        sh(*self.run_args(svc, self.name))
        health = svc["healthcheck"]["test"]
        assert health[0] == "CMD-SHELL"
        cmd = health[1].replace("$$", "$")   # `config --format json` mantém o "$$" de escape do compose
        for _ in range(40):
            if sh("docker", "exec", self.name, "sh", "-c", cmd, check=False).returncode == 0:
                return svc
            time.sleep(0.5)
        raise AssertionError("broker não ficou saudável:\n" + "".join(sh("docker", "logs", self.name, check=False).__getattribute__(a) for a in ("stdout", "stderr")))

    def stop(self) -> None:
        sh("docker", "rm", "-f", self.name, check=False)
        sh("docker", "network", "rm", self.net, check=False)

    # clientes ------------------------------------------------------
    def client(self, tool: str, *args: str, tls: bool = True, cafile: str = "ca.pem", timeout=20):
        conn = ["-h", HOST, "-p", "8883", "--cafile", f"/certs/{cafile}"] if tls else ["-h", HOST, "-p", "1883"]
        return sh("docker", "run", "--rm", "--network", self.net, "-v", f"{self.certs}:/certs:ro",
                  "--entrypoint", tool, IMAGE, *conn, *args, check=False, timeout=timeout)

    def pub(self, user: str | None, pw: str | None, topic: str, msg: str = "x", **kw):
        auth = ["-u", user, "-P", pw] if user else []
        return self.client("mosquitto_pub", *auth, "-t", topic, "-m", msg, "-q", "1", **kw)

    def subscribe(self, user: str, pw: str, topic: str, count: int, tls: bool = False, wait_s: int = 20):
        """Sai ao receber `count` mensagens (ou após wait_s). Mensagem que vazasse entraria na contagem."""
        conn = ["-h", HOST, "-p", "8883", "--cafile", "/certs/ca.pem"] if tls else ["-h", HOST, "-p", "1883"]
        p = subprocess.Popen(["docker", "run", "--rm", "--network", self.net, "-v", f"{self.certs}:/certs:ro",
                              "--entrypoint", "mosquitto_sub", IMAGE, *conn, "-u", user, "-P", pw,
                              "-t", topic, "-v", "-C", str(count), "-W", str(wait_s)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(1.5)   # tempo de conectar e assinar
        return p


@pytest.fixture(scope="module")
def broker(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mqtt")
    b = Broker(tmp)
    make_certs(b.certs)
    b.pw_a = add_gateway(b.gw_file, "gw-a", TENANT_A)
    b.pw_b = add_gateway(b.gw_file, "gw-b", TENANT_B)
    try:
        b.svc = b.start()
        yield b
    finally:
        b.stop()


def test_gateways_file_holds_only_hashes(broker):
    text = broker.gw_file.read_text()
    assert broker.pw_a not in text and broker.pw_b not in text
    lines = [line.split() for line in text.splitlines() if line and not line.startswith("#")]
    assert [(u, t) for u, t, _ in lines] == [("gw-a", TENANT_A), ("gw-b", TENANT_B)]
    assert all(h.startswith("$7$") for _, _, h in lines)
    assert oct(broker.gw_file.stat().st_mode)[-3:] == "600"


def test_generated_acl_restricts_each_gateway_to_its_tenant(broker):
    acl = sh("docker", "exec", broker.name, "cat", "/mosquitto/runtime/acl").stdout
    assert f"user gw-a\ntopic readwrite factory/{TENANT_A}/#" in acl
    assert f"user gw-b\ntopic readwrite factory/{TENANT_B}/#" in acl
    assert f"user {SERVER_USER}\ntopic read factory/#" in acl
    passwd = sh("docker", "exec", broker.name, "cat", "/mosquitto/runtime/passwd").stdout
    assert SERVER_PW not in passwd and broker.pw_a not in passwd
    st = sh("docker", "exec", broker.name, "stat", "-c", "%U %a", "/mosquitto/runtime/passwd").stdout.split()
    assert st == ["mosquitto", "700"]


def test_anonymous_is_rejected_on_both_listeners(broker):
    plain = broker.pub(None, None, f"factory/{TENANT_A}/sensors/t1", tls=False)
    assert plain.returncode != 0 and "not authori" in (plain.stderr + plain.stdout).lower()
    tls = broker.pub(None, None, f"factory/{TENANT_A}/sensors/t1")
    assert tls.returncode != 0


def test_wrong_password_is_rejected(broker):
    r = broker.pub("gw-a", broker.pw_b, f"factory/{TENANT_A}/sensors/t1")
    assert r.returncode != 0


def test_tls_listener_requires_tls_and_a_trusted_cert(broker):
    # texto puro na porta TLS
    r = sh("docker", "run", "--rm", "--network", broker.net, "--entrypoint", "mosquitto_pub", IMAGE,
           "-h", HOST, "-p", "8883", "-u", "gw-a", "-P", broker.pw_a, "-t", f"factory/{TENANT_A}/x", "-m", "x",
           check=False, timeout=20)
    assert r.returncode != 0
    # CA errada → o cliente recusa o certificado do broker
    r = broker.pub("gw-a", broker.pw_a, f"factory/{TENANT_A}/x", cafile="evil-ca.pem")
    assert r.returncode != 0


def test_gateway_reaches_backend_only_inside_its_tenant(broker):
    sub = broker.subscribe(SERVER_USER, SERVER_PW, "factory/#", count=2)
    ok = broker.pub("gw-a", broker.pw_a, f"factory/{TENANT_A}/sensors/tag-1", "own-tenant")
    other = broker.pub("gw-a", broker.pw_a, f"factory/{TENANT_B}/sensors/tag-1", "cross-tenant")
    outside = broker.pub("gw-a", broker.pw_a, "other/topic", "outside")
    b_ok = broker.pub("gw-b", broker.pw_b, f"factory/{TENANT_B}/gateways/gw-b/heartbeat", "b-own")
    out, err = sub.communicate(timeout=30)
    assert ok.returncode == 0 and b_ok.returncode == 0, ok.stderr + b_ok.stderr
    # publicação negada não derruba o cliente (MQTT 3.1.1), mas a mensagem não chega a ninguém
    assert other.returncode == 0 and outside.returncode == 0
    assert f"factory/{TENANT_A}/sensors/tag-1 own-tenant" in out
    assert f"factory/{TENANT_B}/gateways/gw-b/heartbeat b-own" in out
    assert "cross-tenant" not in out and "outside" not in out


def test_gateway_cannot_read_other_tenant_and_backend_cannot_publish(broker):
    sub_a = broker.subscribe("gw-a", broker.pw_a, "factory/#", count=1, tls=True)
    broker.pub("gw-b", broker.pw_b, f"factory/{TENANT_B}/sensors/secret", "b-data")
    broker.pub(SERVER_USER, SERVER_PW, f"factory/{TENANT_A}/sensors/forged", "from-backend", tls=False)
    broker.pub("gw-a", broker.pw_a, f"factory/{TENANT_A}/sensors/mine", "a-data")
    out, _ = sub_a.communicate(timeout=30)
    assert "a-data" in out
    assert "b-data" not in out and "from-backend" not in out


def test_add_and_revoke_gateway_with_hot_reload(broker):
    env = {"MQTT_CONTAINER": broker.name, "MQTT_NO_RELOAD": "0"}
    pw_c = add_gateway(broker.gw_file, "gw-c", TENANT_A, **env)
    assert broker.pub("gw-c", pw_c, f"factory/{TENANT_A}/sensors/t").returncode == 0

    # troca de senha: a antiga deixa de valer
    pw_c2 = add_gateway(broker.gw_file, "gw-c", TENANT_A, **env)
    assert pw_c2 != pw_c
    assert broker.pub("gw-c", pw_c, f"factory/{TENANT_A}/sensors/t").returncode != 0
    assert broker.pub("gw-c", pw_c2, f"factory/{TENANT_A}/sensors/t").returncode == 0

    r = sh("bash", str(ROOT / "scripts/mqtt_gateway.sh"), "remove", "gw-c",
           env={**os.environ, "MQTT_GATEWAYS_FILE": str(broker.gw_file), **env})
    assert "revogado" in r.stdout
    assert broker.pub("gw-c", pw_c2, f"factory/{TENANT_A}/sensors/t").returncode != 0
    # os outros continuam funcionando
    assert broker.pub("gw-a", broker.pw_a, f"factory/{TENANT_A}/sensors/t").returncode == 0
    listed = sh("bash", str(ROOT / "scripts/mqtt_gateway.sh"), "list",
                env={**os.environ, "MQTT_GATEWAYS_FILE": str(broker.gw_file)}).stdout
    assert "gw-a" in listed and "gw-c" not in listed and "$7$" not in listed


@pytest.mark.parametrize("user,tenant", [
    ("gw a", TENANT_A), ("fm-health", TENANT_A), ("gw-x", "not-a-uuid"), ("g", TENANT_A),
    ("gw-x", f"{TENANT_A}/#"), ("gw\nuser evil", TENANT_A),
])
def test_gateway_script_rejects_bad_input(tmp_path, user, tenant):
    r = sh("bash", str(ROOT / "scripts/mqtt_gateway.sh"), "add", user, tenant, check=False,
           env={**os.environ, "MQTT_GATEWAYS_FILE": str(tmp_path / "g.conf"), "MQTT_NO_RELOAD": "1"})
    assert r.returncode != 0
    assert not (tmp_path / "g.conf").exists() or "evil" not in (tmp_path / "g.conf").read_text()


def _render_fails(broker, tmp_path, env: dict, gateways: str | None = None, certs: bool = True):
    """Roda o entrypoint de produção num container avulso e espera que ele recuse subir."""
    gw = tmp_path / "gw"
    gw.mkdir(exist_ok=True)
    if gateways is not None:
        (gw / "gateways.conf").write_text(gateways)
    certs_dir = broker.certs if certs else tmp_path / "nocerts"
    certs_dir.mkdir(exist_ok=True)
    base = {"MQTT_SERVER_USERNAME": SERVER_USER, "MQTT_SERVER_PASSWORD": SERVER_PW, "MQTT_TLS_ENABLED": "true"}
    args = ["docker", "run", "--rm", "-v", f"{ROOT}/infra/mosquitto/prod:/fm:ro",
            "-v", f"{ROOT}/infra/mosquitto/prod/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro",
            "-v", f"{gw}:/mosquitto/gateways:ro", "-v", f"{certs_dir}:/mosquitto/certs:ro"]
    for k, v in {**base, **env}.items():
        args += ["-e", f"{k}={v}"]
    args += ["--entrypoint", "/bin/sh", IMAGE, "/fm/entrypoint.sh", "true"]
    return sh(*args, check=False, timeout=30)


def test_entrypoint_fails_closed(broker, tmp_path):
    r = _render_fails(broker, tmp_path, {"MQTT_SERVER_PASSWORD": "short"})
    assert r.returncode != 0 and "16 caracteres" in r.stderr
    r = _render_fails(broker, tmp_path, {"MQTT_SERVER_PASSWORD": ""})
    assert r.returncode != 0
    r = _render_fails(broker, tmp_path, {}, gateways=f"gw-x {TENANT_A}/# $7$101$abc$def\n")
    assert r.returncode != 0 and "não é UUID" in r.stderr
    r = _render_fails(broker, tmp_path, {}, gateways=f"gw-x {TENANT_A} plaintextpassword\n")
    assert r.returncode != 0 and "hash inválido" in r.stderr
    r = _render_fails(broker, tmp_path, {}, gateways=f"gw-x {TENANT_A} $7$1$a$b\ngw-x {TENANT_B} $7$1$a$b\n")
    assert r.returncode != 0 and "repetido" in r.stderr
    r = _render_fails(broker, tmp_path, {}, gateways="", certs=False)
    assert r.returncode != 0 and "fullchain.pem" in r.stderr
    # sem TLS e sem gateways ainda: sobe (só o backend conecta)
    ok = _render_fails(broker, tmp_path, {"MQTT_TLS_ENABLED": "false"}, gateways=None, certs=False)
    assert ok.returncode == 0, ok.stderr


def test_compose_default_has_tls_off_and_broker_starts_without_certificates(tmp_path):
    """Backward compatible deploy (Sprint 3 review): without MQTT_TLS_ENABLED in the .env the broker comes
    up with no certificate (authenticated 1883), instead of refusing to start."""
    b = Broker(tmp_path)
    b.tls = ""                                   # not set in the .env
    b.certs.mkdir()                              # empty: no fullchain.pem / privkey.pem
    svc = b.service()
    assert svc["environment"]["MQTT_TLS_ENABLED"] == "false"
    try:
        b.start()
        logs = sh("docker", "logs", b.name, check=False)
        assert "TLS=false" in logs.stdout + logs.stderr
    finally:
        b.stop()


def test_edge_publisher_over_tls_reaches_the_backend(broker):
    """The edge MqttPublisher (MQTT_TLS=true + MQTT_CA_CERT) connects to the production listener 8883 with
    a gateway credential, verifies the certificate, and the backend user receives the reading."""
    edge_py = ROOT / ".venv-edge/bin/python"
    if not edge_py.exists():
        pytest.skip("precisa do venv do edge (.venv-edge)")

    name = f"{broker.id}_tls"
    sh(*broker.run_args(broker.svc, name, publish=("127.0.0.1::8883",)))
    try:
        port = int(sh("docker", "port", name, "8883/tcp").stdout.strip().splitlines()[0].rsplit(":", 1)[1])
        for _ in range(40):
            if sh("docker", "exec", name, "sh", "-c", broker.svc["healthcheck"]["test"][1].replace("$$", "$"),
                  check=False).returncode == 0:
                break
            time.sleep(0.5)
        sub = subprocess.Popen(["docker", "run", "--rm", "--network", broker.net, "--entrypoint", "mosquitto_sub",
                                IMAGE, "-h", name, "-p", "1883", "-u", SERVER_USER, "-P", SERVER_PW,
                                "-t", f"factory/{TENANT_A}/#", "-v", "-C", "1", "-W", "20"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(1.5)

        def go(ca: str, tls: bool = True) -> dict:
            code = (
                "import asyncio, json, sys\n"
                "from edge.src.protocols.mqtt_publisher import MqttPublisher\n"
                "async def main():\n"
                "    try:\n"
                f"        pub = MqttPublisher('localhost', {port}, username='gw-a', password={broker.pw_a!r},\n"
                f"                            tls={tls!r}, ca_cert={ca!r})\n"
                "    except Exception as e:\n"
                "        return {'error': type(e).__name__}\n"
                "    await pub.connect()\n"
                f"    sent = pub.is_connected and await pub.publish({TENANT_A!r}, 'tag-tls', 7.0, 'C')\n"
                "    out = {'connected': pub.is_connected, 'sent': bool(sent)}\n"
                "    await pub.disconnect()\n"
                "    return out\n"
                "print(json.dumps(asyncio.run(main())))\n")
            r = sh(str(edge_py), "-c", code, cwd=str(ROOT), timeout=60)
            return json.loads(r.stdout.strip().splitlines()[-1])

        # untrusted CA: refused (never falls back to plaintext); missing CA file: fails at startup
        assert go(str(broker.certs / "evil-ca.pem")) == {"connected": False, "sent": False}
        assert go(str(broker.certs / "missing.pem")) == {"error": "FileNotFoundError"}
        assert go(str(broker.certs / "ca.pem")) == {"connected": True, "sent": True}
        out, err = sub.communicate(timeout=30)
        assert f"factory/{TENANT_A}/sensors/tag-tls" in out, err
    finally:
        sh("docker", "rm", "-f", name, check=False)


def test_dev_broker_config_is_still_anonymous():
    conf = (ROOT / "infra/mosquitto/mosquitto.conf").read_text()
    assert "allow_anonymous true" in conf
    prod = (ROOT / "infra/mosquitto/prod/mosquitto.conf").read_text()
    assert "allow_anonymous false" in prod and "per_listener_settings false" in prod
