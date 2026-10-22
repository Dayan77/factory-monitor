"""
Server side of the optional AES-256-GCM MQTT payload envelope (edge/src/security/payload_crypto.py).

    {"v": 1, "alg": "AES-256-GCM", "nonce": "<b64 12 bytes>", "ct": "<b64 ciphertext||tag>"}
    AAD = MQTT topic (UTF-8); plaintext = the normal JSON payload.

ENCRYPTION_KEY (64 hex chars or base64 of 32 bytes) is shared with the edge. With a key, envelopes
are decrypted (tampered/other-topic payloads are rejected); plaintext payloads are still accepted
unless MQTT_REQUIRE_ENCRYPTION=true. Without a key, envelopes cannot be read and are rejected.
"""

from __future__ import annotations

import base64
import binascii
import json
from functools import lru_cache
from typing import Any, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ALG = "AES-256-GCM"
KEY_SIZE = 32


class PayloadError(ValueError):
    pass


def load_key(value: str) -> bytes:
    s = (value or "").strip()
    if len(s) == 2 * KEY_SIZE:
        try:
            return bytes.fromhex(s)
        except ValueError:
            pass
    try:
        padded = s + "=" * (-len(s) % 4)
        alt = b"-_" if ("-" in s or "_" in s) else None
        key = base64.b64decode(padded, altchars=alt, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError("ENCRYPTION_KEY deve ser 64 caracteres hex ou base64 de 32 bytes") from e
    if len(key) != KEY_SIZE:
        raise ValueError(f"ENCRYPTION_KEY deve ter {KEY_SIZE} bytes")
    return key


@lru_cache(maxsize=4)
def _aead(key_setting: str) -> AESGCM:
    return AESGCM(load_key(key_setting))


def is_envelope(data: Any) -> bool:
    return isinstance(data, dict) and data.get("alg") == ALG and "ct" in data and "nonce" in data


def decode_payload(topic: str, raw: bytes, key_setting: str, require_encryption: bool = False) -> dict:
    """Returns the plaintext payload dict. Raises PayloadError when it cannot be accepted."""
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as e:
        raise PayloadError("payload não é JSON") from e
    if not is_envelope(data):
        if require_encryption and (key_setting or "").strip():
            raise PayloadError("payload em claro recusado (MQTT_REQUIRE_ENCRYPTION)")
        if not isinstance(data, dict):
            raise PayloadError("payload não é um objeto JSON")
        return data
    if not (key_setting or "").strip():
        raise PayloadError("envelope cifrado recebido mas ENCRYPTION_KEY não configurada no servidor")
    if data.get("v") != 1:
        raise PayloadError(f"versão de envelope não suportada: {data.get('v')!r}")
    try:
        pt = _aead(key_setting.strip()).decrypt(base64.b64decode(data["nonce"]), base64.b64decode(data["ct"]),
                                                topic.encode("utf-8"))
        out = json.loads(pt)
    except Exception as e:  # noqa: BLE001 — InvalidTag, bad base64, bad JSON
        raise PayloadError("envelope inválido ou adulterado") from e
    if not isinstance(out, dict):
        raise PayloadError("payload decifrado não é um objeto JSON")
    return out


def key_error(key_setting: Optional[str]) -> Optional[str]:
    if not (key_setting or "").strip():
        return None
    try:
        load_key(key_setting)
        return None
    except ValueError as e:
        return str(e)
