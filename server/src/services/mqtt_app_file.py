"""Broker "app file": the MQTT credentials of the enrolled gateways, written by the server for the production
Mosquitto (infra/mosquitto/prod/render.sh reads it next to the operator's gateways.conf and reloads on change).

    # GERADO pelo fm_server (MQTT_APP_GATEWAYS_FILE) — não edite; refeito a cada mudança de gateway
    <mqtt_username> <tenant_id> <mqtt_password_hash>          (one line per gateway, sorted by user)

Every line becomes `user <mqtt_username>` + `topic readwrite factory/<tenant_id>/#` in the broker ACL, so the file is
cross-tenant BY DESIGN (it is the broker's file): it lists every gateway of an active tenant with a non-null
mqtt_username and mqtt_password_hash (revoked → hash null → line gone). Nothing else from the database goes in it.

Written atomically (temporary file in the same directory + os.replace, mode 0644) and only when the content
changes. Called after the commit of enroll / revoke / delete, at startup and every MQTT_APP_SYNC_S; a failure is
logged and never fails the request (the periodic sync retries). MQTT_APP_GATEWAYS_FILE empty = disabled.
"""
from __future__ import annotations

import asyncio
import os
import re
import tempfile
import weakref
from pathlib import Path
from typing import Iterable, Optional

import structlog
from sqlalchemy import select

from ..config import settings

log = structlog.get_logger()

HEADER = "# GERADO pelo fm_server (MQTT_APP_GATEWAYS_FILE) — não edite; refeito a cada mudança de gateway"
# the same checks render.sh applies to every line (a line it would refuse is never written)
USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$")
TENANT_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
# = APP_HASH_RE of infra/mosquitto/prod/render.sh (the broker's check for this file)
HASH_RE = re.compile(r"^\$(7\$[0-9]{1,7}|6)\$[A-Za-z0-9+/]+={0,2}\$[A-Za-z0-9+/]+={0,2}$")
RESERVED_USERS = {"fm-health"}

_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()


def _lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lk = _locks.get(loop)
    if lk is None:
        lk = _locks[loop] = asyncio.Lock()
    return lk


# result of the last sync attempt in this process (GET /api/gateways/{id}/mqtt → sync_ok / sync_at / sync_error)
_status: dict = {"ok": None, "at": None, "error": None}


def status() -> dict:
    return dict(_status)


def _record(ok: bool, error: Optional[str] = None) -> None:
    from datetime import datetime, timezone

    from .metrics import MQTT_APP_SYNC_AT, MQTT_APP_SYNC_FAILURES, MQTT_APP_SYNC_OK
    now = datetime.now(timezone.utc)
    _status.update(ok=ok, at=now, error=error)
    MQTT_APP_SYNC_OK.set(1 if ok else 0)
    MQTT_APP_SYNC_AT.set(now.timestamp())
    if not ok:
        MQTT_APP_SYNC_FAILURES.inc()


def short_error(e: BaseException) -> str:
    """Error for the API / UI without secrets: the exception class + the OS reason (no SQL, no DSN, no content)."""
    if isinstance(e, OSError) and e.strerror:
        return f"{type(e).__name__}: {e.strerror}"[:160]
    return type(e).__name__


def enabled() -> bool:
    return bool((settings.mqtt_app_gateways_file or "").strip())


def render(rows: Iterable[tuple[str, str, str]]) -> str:
    """File content for (username, tenant_id, hash) rows: header + one line per valid gateway, sorted by user.
    Invalid / duplicated / reserved users are skipped with a log line (the broker would refuse them)."""
    lines, seen = [], set(RESERVED_USERS)
    if settings.mqtt_username:
        seen.add(settings.mqtt_username)
    for user, tenant, hsh in sorted(rows, key=lambda r: r[0]):
        if not (USER_RE.match(user or "") and TENANT_RE.match(tenant or "") and HASH_RE.match(hsh or "")):
            log.warning("mqtt_app_file.line_skipped", user=user, reason="formato inválido")
            continue
        if user in seen:
            log.warning("mqtt_app_file.line_skipped", user=user, reason="usuário repetido ou reservado")
            continue
        seen.add(user)
        lines.append(f"{user} {tenant} {hsh}")
    return "\n".join([HEADER, *lines]) + "\n"


async def gateway_rows() -> list[tuple[str, str, str]]:
    """(mqtt_username, tenant_id, mqtt_password_hash) of every gateway with a live MQTT credential of an active
    tenant — intentionally across tenants (the broker isolates them by ACL prefix)."""
    from ..db import AsyncSessionLocal
    from ..models import Gateway, Tenant
    async with AsyncSessionLocal() as s:
        res = await s.execute(
            select(Gateway.mqtt_username, Gateway.tenant_id, Gateway.mqtt_password_hash)
            .join(Tenant, Tenant.id == Gateway.tenant_id)
            .where(Gateway.mqtt_username.is_not(None), Gateway.mqtt_password_hash.is_not(None),
                   Tenant.is_active.is_(True)))
        return [(u, str(t), h) for u, t, h in res.all()]


def write_if_changed(path: str | Path, content: str) -> bool:
    """Atomic write (tmp in the same directory + fsync + os.replace, mode 0644). False when the content is equal."""
    p = Path(path)
    try:
        if p.read_text(encoding="utf-8") == content:
            return False
    except FileNotFoundError:
        pass
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


async def sync() -> Optional[bool]:
    """Renders and writes the app file. None = disabled; True = rewritten; False = unchanged. Raises on error."""
    if not enabled():
        return None
    path = settings.mqtt_app_gateways_file.strip()
    try:
        async with _lock():            # one writer per process: the last DB read is the last write
            content = render(await gateway_rows())
            changed = await asyncio.to_thread(write_if_changed, path, content)
    except Exception as e:
        _record(False, short_error(e))
        raise
    _record(True)
    if changed:
        log.info("mqtt_app_file.written", path=path, gateways=content.count("\n") - 1)
    return changed


async def sync_quietly(reason: str = "") -> Optional[bool]:
    """sync() for request handlers: a failure is logged, never raised (the periodic sync retries)."""
    try:
        return await sync()
    except Exception as e:  # noqa: BLE001
        log.error("mqtt_app_file.write_failed", reason=reason, path=settings.mqtt_app_gateways_file,
                  error=str(e)[:300])
        return None


async def run_mqtt_app_sync() -> None:
    """Background loop of the API process: at startup and every MQTT_APP_SYNC_S."""
    if not enabled():
        return
    interval = max(1.0, float(settings.mqtt_app_sync_s or 60.0))
    while True:
        await sync_quietly("periodic")
        await asyncio.sleep(interval)
