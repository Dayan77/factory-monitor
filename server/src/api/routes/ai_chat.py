"""
AI assistant routes (Sprint 5 — docs/ai_assistant.md). Answers only with the tenant's real data, fetched by
role-scoped, read-only tools that call this API as the user (services/ai).

  POST   /api/ai/chat                       {message, conversation_id?, context?} → {reply, citations, tool_calls,
                                            warnings, verified, refused, refusal_reason, conversation_id, …}
  POST   /api/ai/chat/stream                same body; Server-Sent Events: start, tool_call, tool_result, verifying,
                                            delta (the verified reply, in chunks), done (full response), error
  GET    /api/ai/conversations              the caller's conversations
  GET    /api/ai/conversations/{id}         messages of one of the caller's conversations
  DELETE /api/ai/conversations/{id}
  GET    /api/ai/audit                      tool-call audit trail of the tenant (admin)
  GET    /api/ai/status                     provider/model configured, daily budget use

The answer text is streamed only after the verifier (IA-2) accepted it — unverified numbers never reach the screen.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user, require_roles
from ...auth.deps import oauth2_scheme
from ...config import settings
from ...db import AsyncSessionLocal, get_db
from ...models import AiConversation, AiMessage, AiToolCall, User, UserRole
from ...services.ai import assistant
from ...services.ai.providers import configured_provider
from ...services.validation import is_uuid

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]
Bearer = Annotated[str, Depends(oauth2_scheme)]
DB = Annotated[AsyncSession, Depends(get_db)]


class ChatContext(BaseModel):
    route: Optional[str] = Field(default=None, max_length=300)
    plant_id: Optional[str] = None
    line_id: Optional[str] = None
    machine_id: Optional[str] = None
    position_id: Optional[str] = None
    period: Optional[str] = Field(default=None, max_length=40)


class AiChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: Optional[str] = None
    context: Optional[ChatContext] = None


class Citation(BaseModel):
    id: int
    label: str
    url: str


class ToolCallOut(BaseModel):
    name: str
    args: dict
    status: str
    rows: Optional[int] = None
    duration_ms: int = 0
    summary: str = ""


class AiChatResponse(BaseModel):
    reply: str
    citations: list[Citation] = Field(default_factory=list)
    tool_calls: list[ToolCallOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    verified: bool = False
    refused: bool = False
    refusal_reason: Optional[str] = None
    refusal_reasons: list[str] = Field(default_factory=list)
    answer_source: str = "model"
    conversation_id: Optional[str] = None
    message_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None


def _ctx(body: AiChatRequest) -> dict:
    ctx = body.context.model_dump(exclude_none=True) if body.context else {}
    for k in ("plant_id", "line_id", "machine_id", "position_id"):
        if k in ctx and not is_uuid(ctx[k]):
            ctx.pop(k)
    return ctx


def _rate(user: User) -> None:
    try:
        assistant.check_rate(user.id)
    except assistant.RateLimited:
        raise HTTPException(status_code=429, detail=f"Limite de {settings.ai_rate_limit_per_min} mensagens por minuto "
                                                    "do assistente atingido. Aguarde um pouco.")


@router.post("/chat", response_model=AiChatResponse)
async def chat(body: AiChatRequest, user: CurrentUser, token: Bearer, db: DB):
    _rate(user)
    try:
        res = await assistant.chat(db, user, token, body.message, _ctx(body), body.conversation_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Conversa não encontrada.")
    return AiChatResponse(**res.to_dict())


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode()


@router.post("/chat/stream")
async def chat_stream(body: AiChatRequest, user: CurrentUser, token: Bearer):
    _rate(user)
    queue: asyncio.Queue = asyncio.Queue()
    user_id, ctx = user.id, _ctx(body)

    sent = {"done": False}

    async def emit(kind: str, data: dict) -> None:
        if kind == "done":
            sent["done"] = True
            reply = data.get("reply") or ""
            for i in range(0, len(reply), 48):            # the verified answer, in chunks
                await queue.put(("delta", {"text": reply[i:i + 48]}))
            data = AiChatResponse(**data).model_dump()
        await queue.put((kind, data))

    async def run() -> None:
        try:
            async with AsyncSessionLocal() as db:
                u = await db.get(User, user_id)
                res = await assistant.chat(db, u, token, body.message, ctx, body.conversation_id, emit=emit)
                if not sent["done"]:
                    # answered without a model turn (assistant disabled, no provider configured): the client still
                    # gets the system reply as delta + done, like any other answer
                    await emit("done", res.to_dict())
        except LookupError:
            await queue.put(("error", {"status": 404, "detail": "Conversa não encontrada."}))
        except Exception as e:  # noqa: BLE001
            await queue.put(("error", {"status": 500, "detail": f"Falha no assistente: {type(e).__name__}"}))
        finally:
            await queue.put(None)

    async def gen():
        task = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield _sse(*item)
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/status")
async def status(user: CurrentUser, db: DB):
    from ...services.ai.providers import ANTHROPIC_DEFAULT_MODEL, OPENAI_DEFAULT_MODEL
    which = configured_provider()
    used = await assistant.tokens_used_today(db, user.tenant_id, datetime.now(timezone.utc).date())
    model = settings.ai_model or (ANTHROPIC_DEFAULT_MODEL if which == "anthropic" else
                                  OPENAI_DEFAULT_MODEL if which == "openai" else None)
    return {"enabled": assistant.ai_enabled_for(user.tenant_id), "configured": which is not None, "provider": which,
            "model": model, "daily_token_budget": settings.ai_daily_token_budget, "tokens_used_today": used,
            "rate_limit_per_min": settings.ai_rate_limit_per_min, "max_tool_calls": settings.ai_max_tool_calls}


@router.get("/conversations")
async def list_conversations(user: CurrentUser, db: DB, limit: int = Query(50, ge=1, le=200)):
    rows = (await db.execute(select(AiConversation).where(
        AiConversation.tenant_id == user.tenant_id, AiConversation.user_id == user.id)
        .order_by(AiConversation.updated_at.desc()).limit(limit))).scalars().all()
    counts = dict((await db.execute(select(AiMessage.conversation_id, func.count()).where(
        AiMessage.tenant_id == user.tenant_id, AiMessage.conversation_id.in_([c.id for c in rows]))
        .group_by(AiMessage.conversation_id))).all()) if rows else {}
    return [{"id": c.id, "title": c.title, "created_at": c.created_at, "updated_at": c.updated_at,
             "messages": counts.get(c.id, 0)} for c in rows]


async def _own(db: AsyncSession, user: User, conversation_id: str) -> AiConversation:
    conv = await assistant.get_conversation(db, user, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversa não encontrada.")
    return conv


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, user: CurrentUser, db: DB):
    conv = await _own(db, user, conversation_id)
    msgs = (await db.execute(select(AiMessage).where(
        AiMessage.tenant_id == user.tenant_id, AiMessage.conversation_id == conv.id)
        .order_by(AiMessage.created_at, AiMessage.role.desc()))).scalars().all()
    return {"id": conv.id, "title": conv.title, "created_at": conv.created_at, "updated_at": conv.updated_at,
            "messages": [{"id": m.id, "role": m.role, "content": m.content, "citations": m.citations or [],
                          "tool_calls": m.tool_calls or [], "warnings": m.warnings or [], "verified": m.verified,
                          "refused": m.refused, "refusal_reason": m.refusal_reason, "created_at": m.created_at}
                         for m in msgs]}


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, user: CurrentUser, db: DB):
    conv = await _own(db, user, conversation_id)
    await db.delete(conv)
    await db.commit()


@router.get("/audit", dependencies=[Depends(require_roles(UserRole.admin))])
async def audit(user: CurrentUser, db: DB, limit: int = Query(200, ge=1, le=1000)):
    """Tool calls of the tenant (name, arguments, status, rows — never the content), newest first."""
    rows = (await db.execute(select(AiToolCall, User.name).outerjoin(User, User.id == AiToolCall.user_id).where(
        AiToolCall.tenant_id == user.tenant_id).order_by(AiToolCall.created_at.desc()).limit(limit))).all()
    return [{"id": r.id, "user_id": r.user_id, "user_name": n, "conversation_id": r.conversation_id, "name": r.name,
             "args": r.args, "status": r.status, "http_status": r.http_status, "rows": r.rows,
             "duration_ms": r.duration_ms, "created_at": r.created_at} for r, n in rows]
