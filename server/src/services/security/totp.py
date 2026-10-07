"""TOTP (RFC 6238: SHA-1, 6 digits, 30 s) for the optional MFA, plus encryption of the secret at rest.

The secret is stored AES-256-GCM encrypted (key = MFA_ENCRYPTION_KEY, or derived from APP_SECRET_KEY); a code is
accepted within ±1 step (clock skew) and never twice (users.mfa_last_step: replay protection).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote, urlencode

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ...config import settings

STEP_S = 30
DIGITS = 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key() -> bytes:
    raw = settings.mfa_encryption_key or ("mfa-at-rest:" + settings.app_secret_key)
    return hashlib.sha256(raw.encode()).digest()


def encrypt(secret: str) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(_key()).encrypt(nonce, secret.encode(), b"mfa")
    return "v1:" + base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt(blob: Optional[str]) -> Optional[str]:
    if not blob or not blob.startswith("v1:"):
        return None
    raw = base64.urlsafe_b64decode(blob[3:].encode())
    try:
        return AESGCM(_key()).decrypt(raw[:12], raw[12:], b"mfa").decode()
    except Exception:  # noqa: BLE001 — wrong key / tampered: no secret
        return None


def _hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(code).zfill(DIGITS)


def code_at(secret: str, t: Optional[float] = None) -> str:
    return _hotp(secret, int((time.time() if t is None else t) // STEP_S))


def verify(secret: str, code: str, last_step: Optional[int] = None, t: Optional[float] = None,
           window: int = 1) -> Optional[int]:
    """The matching time step (to be stored as last_step), or None. Steps <= last_step are refused (replay)."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return None
    now_step = int((time.time() if t is None else t) // STEP_S)
    for step in range(now_step - window, now_step + window + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(_hotp(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, account: str, issuer: Optional[str] = None) -> str:
    issuer = issuer or settings.mfa_issuer
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?" + urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1",
                                                   "digits": DIGITS, "period": STEP_S})


def new_recovery_codes(n: int = 10) -> list[str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    out = []
    for _ in range(n):
        s = "".join(secrets.choice(alphabet) for _ in range(10))
        out.append(f"{s[:5]}-{s[5:]}")
    return out


def hash_recovery(code: str) -> str:
    norm = (code or "").strip().lower().replace("-", "").replace(" ", "")
    return hashlib.sha256(("mfa-recovery:" + norm).encode()).hexdigest()
