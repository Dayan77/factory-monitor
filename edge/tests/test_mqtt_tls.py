"""TLS até o broker MQTT: leitura/validação das variáveis (MQTT_TLS, MQTT_CA_CERT, MQTT_CLIENT_CERT/KEY, porta) e
handshake real contra um servidor TLS em processo (CA própria, nome do host verificado)."""

import asyncio
import datetime as dt
import ssl

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from edge.src import gateway as gw_mod
from edge.src.protocols.mqtt_publisher import (
    MqttTlsError, effective_port, parse_tls_settings, tls_context,
)
from edge.tests.conftest import free_port


def _key():
    return ec.generate_private_key(ec.SECP256R1())


def _write_key(path, key):
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))


def _cert(subject_cn, key, issuer_cn=None, issuer_key=None, ca=False, san=None):
    now = dt.datetime.now(dt.timezone.utc)
    b = (x509.CertificateBuilder()
         .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_cn)]))
         .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_cn or subject_cn)]))
         .public_key(key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now - dt.timedelta(minutes=1)).not_valid_after(now + dt.timedelta(days=1))
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if san:
        b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in san]), critical=False)
    return b.sign(issuer_key or key, hashes.SHA256())


@pytest.fixture
def pki(tmp_path):
    ca_key = _key()
    ca = _cert("FactoryConnect Test CA", ca_key, ca=True)
    srv_key = _key()
    srv = _cert("localhost", srv_key, "FactoryConnect Test CA", ca_key, san=["localhost"])
    cli_key = _key()
    cli = _cert("gw-test", cli_key, "FactoryConnect Test CA", ca_key)
    files = {}
    for name, obj in (("ca.pem", ca), ("server.pem", srv), ("client.pem", cli)):
        (tmp_path / name).write_bytes(obj.public_bytes(serialization.Encoding.PEM))
        files[name] = tmp_path / name
    for name, k in (("server.key", srv_key), ("client.key", cli_key)):
        _write_key(tmp_path / name, k)
        files[name] = tmp_path / name
    other_key = _key()
    (tmp_path / "other_ca.pem").write_bytes(_cert("Other CA", other_key, ca=True)
                                            .public_bytes(serialization.Encoding.PEM))
    files["other_ca.pem"] = tmp_path / "other_ca.pem"
    return files


def test_parse_tls_settings(pki, tmp_path):
    off = parse_tls_settings(False)
    assert not off.enabled and off.context() is None and off.warnings == ()
    assert parse_tls_settings("false", ca_cert=str(pki["ca.pem"])).warnings        # CA sem TLS: avisa
    on = parse_tls_settings("true", ca_cert=str(pki["ca.pem"]))
    ctx = on.context()
    assert on.enabled and ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2
    assert parse_tls_settings("1").enabled and parse_tls_settings("yes").ca_cert == ""   # vazio = CAs do sistema
    with pytest.raises(MqttTlsError, match="não encontrado"):
        parse_tls_settings(True, ca_cert=str(tmp_path / "nope.pem"))
    with pytest.raises(MqttTlsError, match="juntos"):
        parse_tls_settings(True, client_cert=str(pki["client.pem"]))
    with pytest.raises(MqttTlsError, match="MQTT_TLS inválido"):
        parse_tls_settings("talvez")
    mtls = parse_tls_settings(True, str(pki["ca.pem"]), str(pki["client.pem"]), str(pki["client.key"]))
    assert mtls.client_cert.endswith("client.pem") and mtls.context() is not None


def test_effective_port():
    assert effective_port(None, True) == 8883 and effective_port(None, False) == 1883
    assert effective_port(0, True) == 8883 and effective_port(18883, True) == 18883
    with pytest.raises(MqttTlsError):
        effective_port(70000, False)


def test_gateway_fails_at_startup_on_bad_tls_and_defaults_to_8883(pki, tmp_path, monkeypatch):
    s = gw_mod.settings
    monkeypatch.setattr(s, "buffer_db_path", str(tmp_path / "b.db"))
    monkeypatch.setattr(s, "encryption_key", "")
    monkeypatch.setattr(s, "mqtt_tls", True)
    monkeypatch.setattr(s, "mqtt_broker_port", None)
    monkeypatch.setattr(s, "mqtt_ca_cert", str(tmp_path / "missing.pem"))
    with pytest.raises(ValueError, match="MQTT_CA_CERT"):
        gw_mod.EdgeGateway()                                         # nunca cai para texto puro
    monkeypatch.setattr(s, "mqtt_ca_cert", str(pki["ca.pem"]))
    g = gw_mod.EdgeGateway()
    assert g.publisher._port == 8883 and g.publisher._tls_context is not None
    monkeypatch.setattr(s, "mqtt_tls", False)
    assert gw_mod.EdgeGateway().publisher._port == 1883


async def _tls_server(pki, require_client_cert=False):
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(pki["server.pem"], pki["server.key"])
    if require_client_cert:
        ctx.load_verify_locations(pki["ca.pem"])
        ctx.verify_mode = ssl.CERT_REQUIRED

    async def handle(reader, writer):
        writer.write(b"ok")
        await writer.drain()
        writer.close()

    port = free_port()
    server = await asyncio.start_server(handle, "127.0.0.1", port, ssl=ctx)
    return server, port


async def _hello(port, ctx, host="localhost"):
    reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=ctx, server_hostname=host)
    data = await reader.read(2)
    writer.close()
    return data


async def test_tls_handshake_verifies_ca_and_hostname(pki):
    server, port = await _tls_server(pki)
    async with server:
        assert await _hello(port, tls_context(str(pki["ca.pem"]))) == b"ok"
        with pytest.raises(ssl.SSLCertVerificationError):
            await _hello(port, tls_context(str(pki["other_ca.pem"])))          # CA errada
        with pytest.raises(ssl.SSLCertVerificationError):
            await _hello(port, tls_context(str(pki["ca.pem"])), host="broker.example")   # nome não confere


async def test_mutual_tls_client_certificate(pki):
    server, port = await _tls_server(pki, require_client_cert=True)
    async with server:
        cfg = parse_tls_settings(True, str(pki["ca.pem"]), str(pki["client.pem"]), str(pki["client.key"]))
        assert await _hello(port, cfg.context()) == b"ok"
        with pytest.raises((ssl.SSLError, ConnectionResetError, asyncio.IncompleteReadError, BrokenPipeError)):
            data = await _hello(port, tls_context(str(pki["ca.pem"])))           # sem certificado do cliente
            if not data:
                raise ConnectionResetError("servidor recusou")
