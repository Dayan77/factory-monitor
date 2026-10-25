"""Pulled gateway commands (docs/sprint6.md §4.2): the server never connects into the plant.

1. the UI asks `POST /api/gateways/{id}/browse` → a `gateway_commands` row (pending), 202 + request_id;
2. the next heartbeat answer carries `commands: [{id, type: "opcua_browse", source_id, root_node_id, max_depth,
   max_nodes, expires_at}]` (re-delivered while unanswered; the gateway de-duplicates by id);
3. the gateway browses only the endpoint of that source (configured on the gateway, credentials stay there) and
   posts `POST /api/gateways/{id}/commands/{cmd_id}/result` with its own token;
4. the UI polls `GET /api/gateways/{id}/commands/{cmd_id}`.
A command expires after settings.gateway_command_ttl_s (5 min) without an answer. Only the node structure is
kept: never a process value nor a credential.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models import GatewayCommand

EXPIRED_MSG = "gateway não respondeu — está online?"
REDELIVER_AFTER_S = 60.0
NODE_FIELDS = ("node_id", "browse_name", "display_name", "node_class", "data_type", "access_level", "parent_id")


def expiry(now: datetime) -> datetime:
    return now + timedelta(seconds=settings.gateway_command_ttl_s)


def expire_if_due(cmd: GatewayCommand, now: datetime) -> bool:
    if cmd.status in ("pending", "sent") and cmd.expires_at <= now:
        cmd.status = "expired"
        cmd.error = EXPIRED_MSG
        cmd.completed_at = now
        return True
    return False


async def deliverable(db: AsyncSession, gateway_id: str, tenant_id: str, now: datetime) -> list[dict]:
    """Commands for the heartbeat answer: pending ones, and sent ones still unanswered after REDELIVER_AFTER_S.
    Marks them sent. Expired ones are closed instead."""
    rows = (await db.execute(select(GatewayCommand).where(
        GatewayCommand.tenant_id == tenant_id, GatewayCommand.gateway_id == gateway_id,
        GatewayCommand.status.in_(("pending", "sent"))).order_by(GatewayCommand.created_at)
        .with_for_update(skip_locked=True))).scalars().all()
    out = []
    for c in rows:
        if expire_if_due(c, now):
            continue
        if c.status == "sent" and c.sent_at and (now - c.sent_at).total_seconds() < REDELIVER_AFTER_S:
            continue
        c.status, c.sent_at, c.attempts = "sent", now, (c.attempts or 0) + 1
        out.append({"id": c.id, "type": c.type, **(c.params or {}), "expires_at": c.expires_at.isoformat()})
    return out


def clean_nodes(nodes: list, max_nodes: int) -> tuple[list[dict], bool]:
    """Keeps only the structural fields of each node (no Value, no credentials); caps at max_nodes."""
    out, truncated = [], False
    for n in nodes:
        if not isinstance(n, dict) or not isinstance(n.get("node_id"), str) or not n["node_id"].strip():
            continue
        if len(out) >= max_nodes:
            truncated = True
            break
        row = {}
        for k in NODE_FIELDS:
            v = n.get(k)
            if v is None:
                row[k] = None
            elif k == "access_level":
                row[k] = v if isinstance(v, (int, str)) else None
            else:
                row[k] = str(v)[:512]
        row["suggested_role"] = suggest_role(row)
        out.append(row)
    return out, truncated


def _norm(s: Optional[str]) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s)


_BOOL = {"boolean", "bool", "i=1"}
_INT = {"int16", "uint16", "int32", "uint32", "int64", "uint64", "byte", "sbyte", "integer", "uinteger"}
_FLOAT = {"float", "double", "number"}


def suggest_role(node: dict) -> Optional[str]:
    """Only a suggestion (§4.2): Boolean with 'run'/'rodando' → run_state, etc. None = no suggestion."""
    if (node.get("node_class") or "Variable").lower() != "variable":
        return None
    name = _norm(f"{node.get('browse_name') or ''} {node.get('display_name') or ''} {node.get('node_id') or ''}")
    words = set(name.split())
    dt = (node.get("data_type") or "").strip().lower()
    has = lambda *ks: any(k in words or any(w.startswith(k) for w in words) for k in ks)  # noqa: E731
    if dt in _BOOL:
        if has("run", "running", "rodando", "ligado", "ligada", "operando", "emoperacao"):
            return "run_state"
        if has("fault", "falha", "alarm", "alarme", "erro", "error"):
            return "fault_state"
        return None
    if dt in _INT or dt in _FLOAT:
        if has("fault", "falha", "alarm", "alarme") and dt in _INT:
            return "fault_state"
        if has("good", "boas", "boa", "ok"):
            return "part_counter_good"
        if has("scrap", "refugo", "ng", "rejeito", "rejeitada", "rejeitadas"):
            return "part_counter_scrap"
        if has("total", "pecas", "parts", "produzidas") and dt in _INT:
            return "part_counter_total"
        if has("ciclo", "ciclos", "cycle", "cycles", "contador", "count") and dt in _INT:
            return "cycle_counter"
        if has("stroke", "curso"):
            return "stroke_time"
        if has("speed", "velocidade", "rpm"):
            return "speed"
        if has("current", "corrente", "amp", "amps"):
            return "current"
        return "analog" if dt in _FLOAT else None
    return None


async def get_command(db: AsyncSession, tenant_id: str, gateway_id: str, cmd_id: str,
                      lock: bool = False) -> Optional[GatewayCommand]:
    q = select(GatewayCommand).where(GatewayCommand.id == cmd_id, GatewayCommand.tenant_id == tenant_id,
                                     GatewayCommand.gateway_id == gateway_id)
    if lock:
        q = q.with_for_update()
    return (await db.execute(q)).scalar_one_or_none()


def command_out(c: GatewayCommand) -> dict:
    return {"request_id": c.id, "id": c.id, "gateway_id": c.gateway_id, "type": c.type, "params": c.params,
            "status": c.status, "error": c.error, "created_at": c.created_at, "expires_at": c.expires_at,
            "sent_at": c.sent_at, "completed_at": c.completed_at, "attempts": c.attempts,
            "result": c.result if c.status == "done" else None}


async def open_count(db: AsyncSession, tenant_id: str, gateway_id: str) -> int:
    from sqlalchemy import func
    return (await db.execute(select(func.count()).select_from(GatewayCommand).where(
        GatewayCommand.tenant_id == tenant_id, GatewayCommand.gateway_id == gateway_id,
        or_(GatewayCommand.status == "pending", GatewayCommand.status == "sent"),
        GatewayCommand.expires_at > datetime.now(timezone.utc)))).scalar_one()
