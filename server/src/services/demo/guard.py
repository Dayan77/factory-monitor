"""Read-only enforcement of the public demo (docs/sprint6.md §5), for every route of the app.

A demo session JWT carries `role: demo_viewer` (and `demo_session: <id>`). For such a request:
* any method other than GET/HEAD/OPTIONS → 403 "Modo demonstração: alterações desativadas" — except the AI
  assistant questions (POST /api/ai/chat, /api/ai/chat/stream), allowed within the per-session quota
  (settings.demo_ai_questions_per_session; exhausted → 429 with a clear message, never an invented answer);
* administration, users, gateways, settings, cameras, integration tokens/webhooks and onboarding are blocked
  even for GET (403);
* every demo visitor shares one read-only account, so the AI conversations are not shared: the history routes are
  blocked and a question never continues an earlier conversation (`conversation_id` is dropped from the body);
* JSON responses are scrubbed: any `email` field and any e-mail-looking string become null / "(oculto)", so no
  user e-mail is exposed anywhere.
`get_current_user` applies the same write rule from the user's roles (defence in depth: a demo_viewer account is
read-only whatever token it presents).
"""
from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import parse_qs

from jose import JWTError

from ...auth.security import decode_token

DEMO_ROLE = "demo_viewer"
WRITE_MSG = "Modo demonstração: alterações desativadas"
AREA_MSG = "Modo demonstração: área indisponível (administração, usuários, gateways, configurações e câmeras)"
QUOTA_MSG = ("Modo demonstração: a cota de perguntas ao assistente desta sessão acabou. Abra uma nova demonstração "
             "ou fale com a equipe FactoryConnect.")
SAFE = {"GET", "HEAD", "OPTIONS"}
AI_WRITE = ("/api/ai/chat", "/api/ai/chat/stream")
BLOCKED_PREFIXES = (
    "/api/admin", "/api/users", "/api/gateways", "/api/cameras", "/api/api-tokens", "/api/webhooks",
    "/api/maintenance/settings", "/api/maintenance/absences", "/api/notifications/preferences", "/api/push",
    "/api/ai/audit", "/api/ai/conversations", "/api/onboarding", "/api/usage/recompute", "/api/predictive/recompute", "/api/auth/register",
    "/api/report-schedules", "/api/reports/settings", "/api/reports/external-recipients", "/api/settings",
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HIDDEN = "(oculto)"


def _norm_path(path: str) -> str:
    p = re.sub(r"/{2,}", "/", path or "/")
    return p.rstrip("/") or "/"


def is_blocked_area(path: str) -> bool:
    p = _norm_path(path)
    return any(p == b or p.startswith(b + "/") for b in BLOCKED_PREFIXES)


def is_ai_question(method: str, path: str) -> bool:
    return method.upper() == "POST" and _norm_path(path) in AI_WRITE


def block_reason(method: str, path: str) -> Optional[str]:
    """Message of the 403 for a demo_viewer request, or None when allowed."""
    if is_blocked_area(path):
        return AREA_MSG
    if method.upper() not in SAFE and not is_ai_question(method, path):
        return WRITE_MSG
    return None


def scrub(obj):
    """Removes e-mails from a JSON value (keys named *email* → None; e-mail-looking strings → "(oculto)")."""
    if isinstance(obj, dict):
        return {k: (None if isinstance(k, str) and "email" in k.lower() and not isinstance(v, (dict, list))
                    else scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    if isinstance(obj, str) and "@" in obj:
        return _EMAIL.sub(HIDDEN, obj)
    return obj


def demo_claims(scope) -> Optional[dict]:
    """Claims of a demo JWT in the request (Authorization: Bearer, or ?token=), else None."""
    token = None
    for k, v in scope.get("headers") or []:
        if k == b"authorization":
            scheme, _, val = v.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer":
                token = val.strip()
            break
    if token is None:
        qs = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
        token = (qs.get("token") or [None])[0]
    if not token or token.startswith("fct_"):
        return None
    try:
        claims = decode_token(token)
    except JWTError:
        return None
    return claims if claims.get("role") == DEMO_ROLE else None


async def _json(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


async def consume_ai_question(session_id: Optional[str]) -> Optional[int]:
    """Atomically takes one question of the session quota. Returns the questions used, or None (exhausted)."""
    from sqlalchemy import text

    from ...config import settings
    from ...db import AsyncSessionLocal
    from ..validation import is_uuid
    if not session_id or not is_uuid(str(session_id)):
        return None
    async with AsyncSessionLocal() as db:
        row = (await db.execute(text(
            "UPDATE demo_sessions SET ai_questions_used = ai_questions_used + 1 "
            "WHERE id = :id AND ai_questions_used < :q AND expires_at > now() RETURNING ai_questions_used"),
            {"id": str(session_id), "q": settings.demo_ai_questions_per_session})).first()
        await db.commit()
    return row[0] if row else None


async def _without_conversation(receive):
    """Reads the request body and drops `conversation_id` (demo visitors never continue another's conversation)."""
    chunks = []
    while True:
        msg = await receive()
        if msg["type"] != "http.request":
            break
        chunks.append(msg.get("body", b""))
        if not msg.get("more_body"):
            break
    raw = b"".join(chunks)
    try:
        doc = json.loads(raw) if raw else None
        if isinstance(doc, dict) and "conversation_id" in doc:
            doc.pop("conversation_id", None)
            raw = json.dumps(doc, ensure_ascii=False).encode()
    except ValueError:
        pass
    sent = {"done": False}

    async def new_receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        return await receive()
    return new_receive


class DemoGuardMiddleware:
    """Pure ASGI middleware (streaming responses keep working; only JSON bodies are buffered and scrubbed)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        claims = demo_claims(scope)
        if claims is None:
            return await self.app(scope, receive, send)
        method, path = scope.get("method", "GET"), scope.get("path", "/")
        reason = block_reason(method, path)
        if reason:
            return await _json(send, 403, reason)
        if is_ai_question(method, path):
            if await consume_ai_question(claims.get("demo_session")) is None:
                return await _json(send, 429, QUOTA_MSG)
            receive = await _without_conversation(receive)
        state = {"json": False, "start": None, "chunks": []}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                ctype = b""
                for k, v in message.get("headers") or []:
                    if k.lower() == b"content-type":
                        ctype = v.lower()
                if ctype.startswith(b"application/json"):
                    state["json"], state["start"] = True, message
                    return
                return await send(message)
            if message["type"] == "http.response.body" and state["json"]:
                state["chunks"].append(message.get("body", b""))
                if message.get("more_body"):
                    return
                raw = b"".join(state["chunks"])
                try:
                    body = json.dumps(scrub(json.loads(raw)), ensure_ascii=False, default=str).encode() if raw else raw
                except ValueError:
                    body = raw
                start = state["start"]
                headers = [(k, v) for k, v in start.get("headers") or [] if k.lower() != b"content-length"]
                headers.append((b"content-length", str(len(body)).encode()))
                await send({**start, "headers": headers})
                return await send({"type": "http.response.body", "body": body})
            return await send(message)

        return await self.app(scope, receive, send_wrapper)
