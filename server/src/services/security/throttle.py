"""Brute-force protection and rate limits, stored in Postgres (shared by every worker; docs/security.md §1.3).

* `failure(key)`: counts a failure in the LOGIN_WINDOW_S window; at `max_failures` the key is locked for
  LOGIN_LOCK_BASE_S × 2^level (capped at LOGIN_LOCK_MAX_S), level+1 on every lock → exponential backoff.
  A success resets the account key (`reset`), never the IP key.
* `locked(key)`: seconds left of the lock (0 = free).
* `hit(key, limit, window_s)`: fixed-window counter; True while within the limit.
Keys never hold raw personal data: e-mails and IPs are keyed hashes (`hkey`).
"""
from __future__ import annotations

import hashlib
import hmac
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings


def hkey(kind: str, value: str) -> str:
    d = hmac.new(settings.app_secret_key.encode(), f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()[:40]
    return f"{kind}:{d}"


def account_key(email: str) -> str:
    return hkey("acct", (email or "").strip().lower())


def ip_key(ip: str) -> str:
    return hkey("ip", ip or "?")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def locked(db: AsyncSession, key: str, now: Optional[datetime] = None) -> float:
    now = now or _now()
    row = (await db.execute(text("SELECT locked_until FROM auth_throttle WHERE key = :k"), {"k": key})).first()
    if row is None or row[0] is None or row[0] <= now:
        return 0.0
    return (row[0] - now).total_seconds()


async def failure(db: AsyncSession, key: str, max_failures: int, now: Optional[datetime] = None) -> float:
    """Records one failure. Returns the lock (s) it caused, 0 when not locked by it."""
    now = now or _now()
    window = timedelta(seconds=settings.login_window_s)
    await db.execute(text(
        "INSERT INTO auth_throttle (key, failures, window_start, lock_level, updated_at) "
        "VALUES (:k, 0, :now, 0, :now) ON CONFLICT (key) DO NOTHING"), {"k": key, "now": now})
    row = (await db.execute(text(
        "SELECT failures, window_start, locked_until, lock_level FROM auth_throttle WHERE key = :k FOR UPDATE"),
        {"k": key})).first()
    failures, start, until, level = row
    if start + window <= now:
        failures, start = 0, now
    if until is not None and until <= now - timedelta(days=1):
        level = 0                            # backoff forgotten a day after the last lock ended
    failures += 1
    lock_s = 0.0
    if failures >= max_failures:
        lock_s = min(settings.login_lock_max_s, settings.login_lock_base_s * math.pow(2, level))
        until = now + timedelta(seconds=lock_s)
        level += 1
        failures = 0
        start = now
    await db.execute(text(
        "UPDATE auth_throttle SET failures = :f, window_start = :s, locked_until = :u, lock_level = :l, "
        "updated_at = :now WHERE key = :k"),
        {"f": failures, "s": start, "u": until, "l": level, "now": now, "k": key})
    return lock_s


async def reset(db: AsyncSession, key: str) -> None:
    await db.execute(text("DELETE FROM auth_throttle WHERE key = :k"), {"k": key})


async def hit(db: AsyncSession, key: str, limit: int, window_s: float, now: Optional[datetime] = None) -> bool:
    """Counts one request in the current fixed window; False when the limit is exceeded."""
    if limit <= 0:
        return True
    now = now or _now()
    epoch = int(now.timestamp() // window_s * window_s)
    start = datetime.fromtimestamp(epoch, tz=timezone.utc)
    n = (await db.execute(text(
        "INSERT INTO rate_counters (key, window_start, count) VALUES (:k, :w, 1) "
        "ON CONFLICT (key, window_start) DO UPDATE SET count = rate_counters.count + 1 RETURNING count"),
        {"k": key, "w": start})).scalar_one()
    return n <= limit


def retry_after(seconds: float) -> dict:
    return {"Retry-After": str(max(1, int(math.ceil(seconds))))}


async def purge(db: AsyncSession, now: Optional[datetime] = None) -> int:
    now = now or _now()
    n1 = (await db.execute(text("DELETE FROM rate_counters WHERE window_start < :t"),
                           {"t": now - timedelta(days=1)})).rowcount
    n2 = (await db.execute(text(
        "DELETE FROM auth_throttle WHERE (locked_until IS NULL OR locked_until < :t) AND updated_at < :t"),
        {"t": now - timedelta(days=1)})).rowcount
    return (n1 or 0) + (n2 or 0)
