import base64
import json
import os

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from edge.src.security.payload_crypto import PayloadCipher, is_envelope, load_key

KEY = bytes(range(32))
TOPIC = "factory/11111111-1111-1111-1111-111111111111/sensors/22222222-2222-2222-2222-222222222222"


@pytest.mark.parametrize(
    "text",
    [
        KEY.hex(),
        KEY.hex().upper(),
        base64.b64encode(KEY).decode(),
        base64.urlsafe_b64encode(KEY).decode().rstrip("="),
        "  " + KEY.hex() + "\n",
    ],
)
def test_load_key_formats(text):
    assert load_key(text) == KEY


@pytest.mark.parametrize("bad", ["", "abc", "zz" * 32, base64.b64encode(b"x" * 16).decode(), "not base64!!"])
def test_load_key_rejects_bad_keys(bad):
    with pytest.raises(ValueError):
        load_key(bad)


def test_disabled_when_setting_empty():
    assert PayloadCipher.from_setting("") is None
    assert PayloadCipher.from_setting("   ") is None


def test_envelope_shape_and_roundtrip():
    c = PayloadCipher(KEY)
    payload = {"v": 1, "value": 21.5, "unit": "°C", "ts": "2026-01-01T00:00:00.000Z"}
    env = c.encrypt_json(TOPIC, payload)
    assert set(env) == {"v", "alg", "nonce", "ct"}
    assert env["v"] == 1 and env["alg"] == "AES-256-GCM"
    assert len(base64.b64decode(env["nonce"])) == 12
    assert is_envelope(env) and not is_envelope(payload)
    assert "21.5" not in json.dumps(env)
    assert c.decrypt_json(TOPIC, env) == payload


def test_interop_with_plain_cryptography_aesgcm():
    """Exactly what the server will do — no edge code involved in decryption."""
    env = PayloadCipher(KEY).encrypt_json(TOPIC, {"value": 1.0})
    pt = AESGCM(KEY).decrypt(base64.b64decode(env["nonce"]), base64.b64decode(env["ct"]), TOPIC.encode())
    assert json.loads(pt) == {"value": 1.0}


def test_nonce_is_unique_per_message():
    c = PayloadCipher(KEY)
    nonces = {c.encrypt(TOPIC, b"x")["nonce"] for _ in range(200)}
    assert len(nonces) == 200


def test_tampering_wrong_topic_or_key_fails():
    c = PayloadCipher(KEY)
    env = c.encrypt_json(TOPIC, {"value": 1})
    with pytest.raises(InvalidTag):
        c.decrypt(TOPIC.replace("sensors/2", "sensors/3"), env)  # replayed on another tag
    with pytest.raises(InvalidTag):
        PayloadCipher(os.urandom(32)).decrypt(TOPIC, env)
    ct = bytearray(base64.b64decode(env["ct"]))
    ct[0] ^= 1
    with pytest.raises(InvalidTag):
        c.decrypt(TOPIC, {**env, "ct": base64.b64encode(bytes(ct)).decode()})


def test_unsupported_version_rejected():
    c = PayloadCipher(KEY)
    env = c.encrypt_json(TOPIC, {"value": 1})
    with pytest.raises(ValueError):
        c.decrypt(TOPIC, {**env, "v": 2})
