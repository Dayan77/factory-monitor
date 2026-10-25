"""Password hashing and JWT signing/verification (Sprint 7 hardening, docs/security.md §1).

* bcrypt with an explicit cost (BCRYPT_ROUNDS, default 12); `needs_rehash` lets login upgrade old hashes.
* JWTs carry `kid` and `iat`; JWT_KEYS="kid1:secret1,kid2:secret2" + JWT_ACTIVE_KID enable key rotation (the active
  key signs, every listed key verifies). A token without `kid` (issued before rotation, demo sessions, signed links)
  is verified with APP_SECRET_KEY. JWT_LEEWAY_S tolerates clock skew.
* `typ` separates token kinds: access (no typ for legacy tokens), refresh, mfa, reset. Each consumer checks it, so a
  token of one kind is never accepted as another.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from jose import JWTError, jwt
from passlib.context import CryptContext

from ..config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=settings.bcrypt_rounds)

# Verified against when the account does not exist, so a login for an unknown e-mail costs the same time.
_DUMMY_HASH = pwd_context.hash("dummy-password-for-timing-equalization")

TYP_ACCESS, TYP_REFRESH, TYP_MFA, TYP_RESET = "access", "refresh", "mfa", "reset"
DEFAULT_KID = "k0"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: Optional[str]) -> bool:
    if not hashed or not hashed.startswith("$2"):
        pwd_context.verify(plain, _DUMMY_HASH)
        return False
    try:
        return pwd_context.verify(plain, hashed)
    except (ValueError, TypeError):
        return False


def dummy_verify(plain: str) -> None:
    pwd_context.verify(plain, _DUMMY_HASH)


def needs_rehash(hashed: str) -> bool:
    try:
        return pwd_context.needs_update(hashed)
    except (ValueError, TypeError):
        return False


# ── signing keys ────────────────────────────────────────────────────────────

def signing_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    for part in (settings.jwt_keys or "").split(","):
        kid, sep, secret = part.strip().partition(":")
        if sep and kid.strip() and secret.strip():
            keys[kid.strip()] = secret.strip()
    if not keys:
        keys[DEFAULT_KID] = settings.app_secret_key
    return keys


def active_kid() -> str:
    keys = signing_keys()
    kid = settings.jwt_active_kid or ""
    if kid in keys:
        return kid
    return next(iter(keys))


def _encode(claims: dict[str, Any]) -> str:
    kid = active_kid()
    return jwt.encode(claims, signing_keys()[kid], algorithm=settings.jwt_algorithm, headers={"kid": kid})


def create_token(claims: dict[str, Any], ttl: timedelta, typ: Optional[str] = None) -> str:
    now = datetime.now(timezone.utc)
    body = {**claims, "iat": int(now.timestamp()), "exp": now + ttl}
    body.setdefault("jti", uuid4().hex)
    if typ:
        body["typ"] = typ
    return _encode(body)


def create_access_token(subject: dict[str, Any], ttl: Optional[timedelta] = None) -> str:
    return create_token(subject, ttl or timedelta(minutes=settings.jwt_access_token_expire_minutes), TYP_ACCESS)


def create_refresh_token(subject: dict[str, Any], ttl: Optional[timedelta] = None) -> str:
    return create_token({**subject, "refresh": True},
                        ttl or timedelta(days=settings.jwt_refresh_token_expire_days), TYP_REFRESH)


def decode_token(token: str, typ: Optional[str] = None) -> dict[str, Any]:
    """Verifies signature (by `kid`) and expiry (with leeway). `typ` given → the token must be of that kind."""
    try:
        header = jwt.get_unverified_header(token)
    except JWTError:
        raise
    kid = header.get("kid")
    keys = signing_keys()
    if kid is None:
        key = settings.app_secret_key
    elif kid in keys:
        key = keys[kid]
    elif kid == DEFAULT_KID:
        key = settings.app_secret_key
    else:
        raise JWTError("unknown kid")
    claims = jwt.decode(token, key, algorithms=[settings.jwt_algorithm],
                        options={"leeway": settings.jwt_leeway_s})
    if typ is not None:
        kind = claims.get("typ") or (TYP_REFRESH if claims.get("refresh") else TYP_ACCESS)
        if kind != typ:
            raise JWTError("wrong token type")
    return claims


def is_access_claims(claims: dict[str, Any]) -> bool:
    return not claims.get("refresh") and claims.get("typ") in (None, TYP_ACCESS)
