"""
Web Push (RFC 8030) with message encryption (RFC 8291, aes128gcm / RFC 8188) and VAPID (RFC 8292).

Keys come from the environment: VAPID_PUBLIC_KEY (base64url, uncompressed P-256 point, 65 bytes —
the `applicationServerKey` the browser subscribes with), VAPID_PRIVATE_KEY (base64url raw 32-byte
scalar, or PEM) and VAPID_SUBJECT (`mailto:` or https URL). Without a private key push is disabled
(notifications still go in-app / e-mail).

Generate a key pair:  python -m src.notifications.webpush   (prints the two env lines)
"""
from __future__ import annotations

import base64
import json
import os
import struct
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

RECORD_SIZE = 4096


def b64u_decode(s: str) -> bytes:
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _hmac(key: bytes, data: bytes) -> bytes:
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def _public_bytes(pub: ec.EllipticCurvePublicKey) -> bytes:
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def encrypt(plaintext: bytes, ua_public_b64: str, auth_secret_b64: str,
            as_private: Optional[ec.EllipticCurvePrivateKey] = None, salt: Optional[bytes] = None) -> bytes:
    """RFC 8291 aes128gcm body (single record). `as_private`/`salt` are for tests only."""
    ua_public = b64u_decode(ua_public_b64)
    auth_secret = b64u_decode(auth_secret_b64)
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(as_private.public_key())
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh_secret = as_private.exchange(ec.ECDH(), ua_key)
    prk_key = _hmac(auth_secret, ecdh_secret)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    salt = salt or os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    if len(plaintext) > RECORD_SIZE - 17 - 86:
        raise ValueError("payload grande demais para um registro Web Push")
    ct = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    header = salt + struct.pack("!I", RECORD_SIZE) + bytes([len(as_public)]) + as_public
    return header + ct


def decrypt(body: bytes, ua_private: ec.EllipticCurvePrivateKey, auth_secret_b64: str) -> bytes:
    """Inverse of encrypt() (user-agent side) — used by the tests to verify the encryption."""
    salt, rs, idlen = body[:16], struct.unpack("!I", body[16:20])[0], body[20]
    as_public = body[21:21 + idlen]
    ct = body[21 + idlen:]
    assert rs == RECORD_SIZE
    ua_public = _public_bytes(ua_private.public_key())
    as_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
    ecdh_secret = ua_private.exchange(ec.ECDH(), as_key)
    prk_key = _hmac(b64u_decode(auth_secret_b64), ecdh_secret)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    padded = AESGCM(cek).decrypt(nonce, ct, None)
    return padded.rstrip(b"\x00")[:-1]


@dataclass
class Vapid:
    private_key: ec.EllipticCurvePrivateKey
    public_key_b64: str
    subject: str

    @classmethod
    def from_env(cls, private: str, public: str = "", subject: str = "") -> Optional["Vapid"]:
        private = (private or "").strip()
        if not private:
            return None
        if "BEGIN" in private:
            key = serialization.load_pem_private_key(private.replace("\\n", "\n").encode(), password=None)
        else:
            key = ec.derive_private_key(int.from_bytes(b64u_decode(private), "big"), ec.SECP256R1())
        pub = b64u(_public_bytes(key.public_key()))
        if public and public.strip() and public.strip() != pub:
            raise ValueError("VAPID_PUBLIC_KEY não corresponde a VAPID_PRIVATE_KEY")
        return cls(key, pub, subject or "mailto:suporte@factoryconnect.tech")

    def authorization(self, endpoint: str, ttl_s: int = 12 * 3600) -> str:
        p = urlsplit(endpoint)
        header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
        claims = b64u(json.dumps({"aud": f"{p.scheme}://{p.netloc}", "exp": int(time.time()) + ttl_s,
                                  "sub": self.subject}, separators=(",", ":")).encode())
        signing_input = f"{header}.{claims}".encode()
        der = self.private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        sig = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return f"vapid t={header}.{claims}.{sig}, k={self.public_key_b64}"


def vapid_from_settings() -> Optional[Vapid]:
    from ..config import settings
    try:
        return Vapid.from_env(settings.vapid_private_key, settings.vapid_public_key, settings.vapid_subject)
    except Exception:  # noqa: BLE001 — misconfigured keys: push disabled, logged by caller
        return None


async def send(endpoint: str, p256dh: str, auth: str, payload: dict, vapid: Vapid, urgency: str = "normal",
               ttl_s: int = 3600, client: Optional[httpx.AsyncClient] = None) -> int:
    """Returns the push service HTTP status (201 = accepted; 404/410 = subscription gone)."""
    body = encrypt(json.dumps(payload, ensure_ascii=False).encode(), p256dh, auth)
    headers = {"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(ttl_s),
               "Urgency": urgency, "Authorization": vapid.authorization(endpoint)}
    if client is not None:
        r = await client.post(endpoint, content=body, headers=headers, timeout=10)
        return r.status_code
    async with httpx.AsyncClient() as c:
        r = await c.post(endpoint, content=body, headers=headers, timeout=10)
        return r.status_code


if __name__ == "__main__":
    k = ec.generate_private_key(ec.SECP256R1())
    priv = b64u(k.private_numbers().private_value.to_bytes(32, "big"))
    print(f"VAPID_PUBLIC_KEY={b64u(_public_bytes(k.public_key()))}")
    print(f"VAPID_PRIVATE_KEY={priv}")
    print("VAPID_SUBJECT=mailto:suporte@factoryconnect.tech")
