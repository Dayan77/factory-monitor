"""Sessões da demonstração pública (Sprint 6 — docs/sprint6.md §5).

Cada "Ver demonstração" cria uma linha: limite por IP (hash com chave, nunca o IP) e cota de perguntas ao
assistente de IA por sessão. O JWT de 2 h carrega o id da sessão (`demo_session`).
"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class DemoSession(Base):
    __tablename__ = "demo_sessions"
    __table_args__ = (Index("ix_demo_sessions_ip_created", "ip_hash", "created_at"),)

    id:                Mapped[str]      = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    ip_hash:           Mapped[str]      = mapped_column(String(64), nullable=False)
    created_at:        Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at:        Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ai_questions_used: Mapped[int]      = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
