"""
Demo session info for the app (Sprint 6 — docs/sprint6.md §5). The session itself is opened by
`POST /api/public/demo-session` (no auth).

  GET /api/demo/session    expiry and AI assistant quota left of the caller's demo session (demo_viewer only)
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import get_current_user
from ...db import get_db
from ...models import User
from ...services.demo.guard import demo_claims
from ...services.demo.session import BANNER, session_info

router = APIRouter()


@router.get("/session")
async def get_session(request: Request, user: Annotated[User, Depends(get_current_user)],
                      db: Annotated[AsyncSession, Depends(get_db)]):
    claims = demo_claims(request.scope)
    if claims is None or "demo_viewer" not in user.role_set:
        raise HTTPException(status_code=404, detail="Não é uma sessão de demonstração.")
    info = await session_info(db, claims.get("demo_session"))
    if info is None:
        raise HTTPException(status_code=404, detail="Sessão de demonstração não encontrada.")
    return {**info, "banner": BANNER, "read_only_message": "Modo demonstração: alterações desativadas"}
