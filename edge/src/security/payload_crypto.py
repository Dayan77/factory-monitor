"""
Optional AES-256-GCM encryption of MQTT payloads (edge → server).

Enabled only when ENCRYPTION_KEY is set. Disabled by default (plaintext JSON).

Key (ENCRYPTION_KEY): exactly 32 bytes, given as 64 hex chars or base64
(standard or URL-safe, padding optional). Anything else is rejected at startup.

Envelope (UTF-8 JSON published as the MQTT payload), version 1:

    {"v": 1, "alg": "AES-256-GCM", "nonce": "<b64 12 bytes>", "ct": "<b64 ciphertext||tag>"}

    - nonce: 12 random bytes (os.urandom), new for every message, standard base64.
    - ct:    AES-256-GCM output = ciphertext followed by the 16-byte auth tag, standard base64.
    - AAD:   the MQTT topic encoded as UTF-8 (e.g. b"factory/<tenant>/sensors/<tag>"),
             so a payload cannot be replayed on another tenant/tag topic.
    - plaintext: the compact UTF-8 JSON of the normal payload
             ({"v":1,"value":..,"unit":..,"ts":..}).

Server-side decryption (Python, `cryptography`):

    env = json.loads(raw)
    if env.get("alg") == "AES-256-GCM" and "ct" in env:
        pt = AESGCM(key).decrypt(b64decode(env["nonce"]), b64decode(env["ct"]), topic.encode())
        data = json.loads(pt)
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENVELOPE_VERSION = 1
ALG = "AES-256-GCM"
NONCE_SIZE = 12
KEY_SIZE = 32


def load_key(value: str) -> bytes:
    """Parses ENCRYPTION_KEY (64 hex chars or base64 of 32 bytes) into raw key bytes."""
    s = (value or "").strip()
    if not s:
        raise ValueError("ENCRYPTION_KEY vazio")
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
        raise ValueError(f"ENCRYPTION_KEY deve ter {KEY_SIZE} bytes (AES-256); recebido {len(key)}")
    return key


def is_envelope(data: Any) -> bool:
    return isinstance(data, dict) and data.get("alg") == ALG and "ct" in data and "nonce" in data


class PayloadCipher:
    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_SIZE:
            raise ValueError(f"chave AES-256 precisa de {KEY_SIZE} bytes")
        self._aead = AESGCM(key)

    @classmethod
    def from_setting(cls, value: str) -> "PayloadCipher | None":
        """None when the setting is empty (encryption disabled)."""
        if not (value or "").strip():
            return None
        return cls(load_key(value))

    def encrypt(self, topic: str, plaintext: bytes) -> dict:
        nonce = os.urandom(NONCE_SIZE)
        ct = self._aead.encrypt(nonce, plaintext, topic.encode("utf-8"))
        return {
            "v": ENVELOPE_VERSION,
            "alg": ALG,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ct": base64.b64encode(ct).decode("ascii"),
        }

    def decrypt(self, topic: str, envelope: dict) -> bytes:
        """Reference implementation of what the server must do (raises on tamper)."""
        if envelope.get("v") != ENVELOPE_VERSION or envelope.get("alg") != ALG:
            raise ValueError(f"envelope não suportado: v={envelope.get('v')} alg={envelope.get('alg')}")
        nonce = base64.b64decode(envelope["nonce"])
        ct = base64.b64decode(envelope["ct"])
        return self._aead.decrypt(nonce, ct, topic.encode("utf-8"))

    def encrypt_json(self, topic: str, payload: dict) -> dict:
        return self.encrypt(topic, json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))

    def decrypt_json(self, topic: str, envelope: dict) -> dict:
        return json.loads(self.decrypt(topic, envelope))
