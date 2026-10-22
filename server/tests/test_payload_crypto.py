"""Server-side decryption of the edge AES-256-GCM envelope (edge/src/security/payload_crypto.py)."""
import base64
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.services.payload_crypto import PayloadError, decode_payload, key_error
from usage_helpers import add_position, add_tag, build_plant, iso, totals_of

KEY = os.urandom(32)
KEY_HEX = KEY.hex()


def envelope(topic: str, payload: dict, key: bytes = KEY) -> bytes:
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, json.dumps(payload).encode(), topic.encode())
    return json.dumps({"v": 1, "alg": "AES-256-GCM", "nonce": base64.b64encode(nonce).decode(),
                       "ct": base64.b64encode(ct).decode()}).encode()


def test_decode_plain_and_envelope():
    t = "factory/a/sensors/b"
    assert decode_payload(t, b'{"value": 1}', "") == {"value": 1}
    assert decode_payload(t, envelope(t, {"value": 2}), KEY_HEX) == {"value": 2}
    assert decode_payload(t, envelope(t, {"value": 3}), base64.b64encode(KEY).decode()) == {"value": 3}


@pytest.mark.parametrize("case", ["other_topic", "wrong_key", "no_key", "plain_required", "garbage"])
def test_decode_rejections(case):
    t = "factory/a/sensors/b"
    with pytest.raises(PayloadError):
        if case == "other_topic":
            decode_payload(t, envelope("factory/x/sensors/b", {"value": 1}), KEY_HEX)
        elif case == "wrong_key":
            decode_payload(t, envelope(t, {"value": 1}, os.urandom(32)), KEY_HEX)
        elif case == "no_key":
            decode_payload(t, envelope(t, {"value": 1}), "")
        elif case == "plain_required":
            decode_payload(t, b'{"value": 1}', KEY_HEX, require_encryption=True)
        else:
            decode_payload(t, b"\xff\x00", KEY_HEX)


def test_key_validation():
    assert key_error("") is None and key_error(KEY_HEX) is None
    assert key_error("short") is not None


async def test_encrypted_readings_flow_through_pipeline(client, tenants, db, monkeypatch):
    from src.config import settings
    from src.services.mqtt_subscriber import _handle_message
    monkeypatch.setattr(settings, "encryption_key", KEY_HEX)
    t = tenants.a
    p = await build_plant(client, t)
    pos = await add_position(client, t, p, "Cilindro cifrado")
    tag = await add_tag(client, t, p, "enc.ciclos", "cycle_counter", pos, max_rate_per_s=100)
    topic = f"factory/{t.id}/sensors/{tag['id']}"
    T = datetime.now(timezone.utc) - timedelta(hours=2)
    for sec, v in [(0, 10), (10, 25)]:
        ts = T + timedelta(seconds=sec)
        await _handle_message(topic, envelope(topic, {"v": 1, "value": v, "unit": "", "ts": iso(ts)}),
                              received_at=ts + timedelta(seconds=1))
    # replayed on another tag's topic → rejected (AAD mismatch)
    other = await add_tag(client, t, p, "enc.outra", "cycle_counter", pos, max_rate_per_s=100)
    await _handle_message(f"factory/{t.id}/sensors/{other['id']}",
                          envelope(topic, {"v": 1, "value": 999, "ts": iso(T)}))
    assert (await totals_of(db, "position", pos["id"]))["cycles"] == 15
