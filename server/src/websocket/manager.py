"""
WebSocket connection manager.
Broadcasts real-time sensor readings / machine status to dashboard clients of a tenant.

Authentication (/ws/sensors/{tenant_id}):
  The client sends its *access* JWT either as the subprotocol pair
  ``Sec-WebSocket-Protocol: bearer, <jwt>`` (browsers: ``new WebSocket(url,
  ["bearer", jwt])`` — keeps the token out of URLs/access logs) or as
  ``?token=<jwt>`` (scripts). The token must belong to an active user of that
  tenant (superadmin may watch any tenant). Otherwise the socket is closed with:
    4401  missing / invalid / expired / refresh token, or user inactive
    4403  token valid but for another tenant (or malformed tenant id)
  The connection is closed with 4401 when the token expires, so the client
  refreshes it and reconnects.
"""

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError
from sqlalchemy import select
import structlog

from ..auth.security import decode_token
from ..services.validation import ensure_uuid, is_uuid

log = structlog.get_logger()

ws_router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        # tenant_id → set of WebSocket connections
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        # WebSocket → user id (per-user messages: {type: 'notification'})
        self._users: dict[WebSocket, str] = {}
        # WebSocket → line scope of a pure operator (services/operator_scope.py): only messages about machines,
        # tags and positions of his lines are sent to that socket (§3.2 "nunca outras linhas")
        self._scopes: dict[WebSocket, "WsScope"] = {}
        # Set in a process without the sockets (the predictive worker, src/worker.py): messages are relayed to the
        # app process through Postgres NOTIFY (websocket/relay.py) instead of sent here.
        self.relay = None

    async def connect(self, ws: WebSocket, tenant_id: str, accept: bool = True, user_id: Optional[str] = None,
                      scope: Optional["WsScope"] = None) -> None:
        if accept:
            await ws.accept()
        self._connections[tenant_id].add(ws)
        if user_id:
            self._users[ws] = str(user_id)
        if scope is not None:
            self._scopes[ws] = scope
        log.info("ws.connected", tenant_id=tenant_id, total=len(self._connections[tenant_id]))

    def disconnect(self, ws: WebSocket, tenant_id: str) -> None:
        self._connections[tenant_id].discard(ws)
        self._users.pop(ws, None)
        self._scopes.pop(ws, None)

    async def send_to_user(self, tenant_id: str, user_id: str, message: dict) -> int:
        """Sends to the sockets of one user of the tenant (tenant isolation: only that tenant's
        connections are considered). Returns how many sockets got it."""
        if self.relay is not None:
            await self.relay.publish("user", str(tenant_id), str(user_id), message)
            return 0
        sent, dead = 0, set()
        for ws in list(self._connections.get(tenant_id, ())):
            if self._users.get(ws) != str(user_id):
                continue
            try:
                await ws.send_json(message)
                sent += 1
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.disconnect(ws, tenant_id)
        return sent

    async def broadcast(self, tenant_id: str, message: dict) -> None:
        if self.relay is not None:
            await self.relay.publish("tenant", str(tenant_id), None, message)
            return
        dead = set()
        # Snapshot: clients connect/disconnect while we await send_json. Iterating the live set
        # raised "Set changed size during iteration", which aborted the whole MQTT reading
        # (no Influx write, no usage) — seen on the live stack with browsers reconnecting.
        for ws in list(self._connections.get(tenant_id, ())):
            scope = self._scopes.get(ws)
            if scope is not None and not scope.allows(message):
                continue
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._connections[tenant_id].discard(ws)
            self._scopes.pop(ws, None)


class WsScope:
    """What an operator restricted to some lines may receive: messages about other machines/tags/positions are
    dropped; messages that name none of them (e.g. tenant notices) pass."""

    def __init__(self, machine_ids: set[str], tag_ids: set[str], position_ids: set[str]):
        self.machine_ids, self.tag_ids, self.position_ids = machine_ids, tag_ids, position_ids

    def allows(self, message: dict) -> bool:
        ev = message.get("event") if isinstance(message.get("event"), dict) else {}
        mid = message.get("machine_id") or ev.get("machine_id")
        if mid and str(mid) not in self.machine_ids:
            return False
        tid = message.get("tag_id")
        if tid and str(tid) not in self.tag_ids:
            return False
        pid = message.get("position_id") or ev.get("position_id")
        if pid and str(pid) not in self.position_ids:
            return False
        return True


async def operator_ws_scope(user_id: str, tenant_id: str) -> Optional[WsScope]:
    """WsScope of a pure operator with lines assigned; None for everybody else."""
    from ..db import AsyncSessionLocal
    from ..models import Machine, Position, Tag, User
    from ..services.operator_scope import operator_lines
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        lines = operator_lines(user) if user is not None else None
        if lines is None:
            return None
        mids = {str(x) for x in (await db.execute(select(Machine.id).where(
            Machine.tenant_id == tenant_id, Machine.line_id.in_(list(lines))))).scalars()}
        tags = {str(x) for x in (await db.execute(select(Tag.id).where(
            Tag.tenant_id == tenant_id, Tag.machine_id.in_(list(mids))))).scalars()} if mids else set()
        poss = {str(x) for x in (await db.execute(select(Position.id).where(
            Position.tenant_id == tenant_id, Position.machine_id.in_(list(mids))))).scalars()} if mids else set()
    return WsScope(mids, tags, poss)


manager = ConnectionManager()


WS_UNAUTHORIZED = 4401
WS_FORBIDDEN = 4403
BEARER_SUBPROTOCOL = "bearer"


def _extract_token(ws: WebSocket) -> tuple[Optional[str], Optional[str]]:
    """Returns (token, subprotocol_to_echo)."""
    offered = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    if len(offered) >= 2 and offered[0].lower() == BEARER_SUBPROTOCOL:
        return offered[1], offered[0]
    token = ws.query_params.get("token")
    return (token or None), (offered[0] if offered else None)


async def authorize_ws(token: Optional[str], tenant_id: str) -> tuple[Optional[int], Optional[datetime]]:
    """Checks the access token for tenant_id. Returns (close_code | None, token_expiry)."""
    code, exp, _ = await authorize_ws_user(token, tenant_id)
    return code, exp


async def authorize_ws_user(token: Optional[str], tenant_id: str
                            ) -> tuple[Optional[int], Optional[datetime], Optional[str]]:
    """Like authorize_ws, also returning the user id (per-user notification messages)."""
    from ..db import AsyncSessionLocal       # local import: keeps module importable in tests
    from ..models import User, UserRole

    if not token:
        return WS_UNAUTHORIZED, None, None
    try:
        payload = decode_token(token)
    except JWTError:
        return WS_UNAUTHORIZED, None, None
    user_id = payload.get("sub")
    if not user_id or payload.get("refresh") or not is_uuid(str(user_id)):
        return WS_UNAUTHORIZED, None, None
    if not is_uuid(tenant_id):
        return WS_FORBIDDEN, None, None

    async with AsyncSessionLocal() as db:
        user = (await db.execute(
            select(User).where(User.id == user_id, User.is_active.is_(True))
        )).scalar_one_or_none()
    if user is None:
        return WS_UNAUTHORIZED, None, None
    if user.role != UserRole.superadmin and str(user.tenant_id) != ensure_uuid(tenant_id):
        return WS_FORBIDDEN, None, None
    exp = payload.get("exp")
    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc) if isinstance(exp, (int, float)) else None
    return None, expires_at, str(user.id)


@ws_router.websocket("/sensors/{tenant_id}")
async def sensor_stream(ws: WebSocket, tenant_id: str):
    token, subprotocol = _extract_token(ws)
    # Accept first so we can close with an application code the client can read
    # (a pre-accept close is just an HTTP 403 with no reason in browsers).
    await ws.accept(subprotocol=subprotocol)
    try:
        code, expires_at, ws_user_id = await authorize_ws_user(token, tenant_id)
    except Exception as e:  # noqa: BLE001 — DB down etc.: fail closed
        log.warning("ws.auth_error", error=str(e))
        code, expires_at, ws_user_id = 1011, None, None
    if code is not None:
        log.info("ws.rejected", tenant_id=tenant_id[:36], code=code)
        await ws.close(code=code)
        return

    tenant_id = ensure_uuid(tenant_id)   # same canonical form the MQTT subscriber broadcasts to
    try:
        scope = await operator_ws_scope(ws_user_id, tenant_id) if ws_user_id else None
    except Exception as e:  # noqa: BLE001 — fail closed: no scope could be computed for a possible operator
        log.warning("ws.scope_error", error=str(e)[:200])
        await ws.close(code=1011)
        return
    await manager.connect(ws, tenant_id, accept=False, user_id=ws_user_id, scope=scope)
    try:
        while True:
            # Keep connection alive; server pushes via manager.broadcast().
            # Wake up when the token expires and make the client re-authenticate.
            timeout = None
            if expires_at is not None:
                timeout = (expires_at - datetime.now(timezone.utc)).total_seconds()
                if timeout <= 0:
                    await ws.close(code=WS_UNAUTHORIZED)
                    break
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=timeout)
            except asyncio.TimeoutError:
                await ws.close(code=WS_UNAUTHORIZED)
                break
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        # Starlette: receive after the socket was closed by a failed send (client already gone).
        pass
    finally:
        manager.disconnect(ws, tenant_id)
        log.info("ws.disconnected", tenant_id=tenant_id)
