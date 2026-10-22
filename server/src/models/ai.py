"""Assistente de IA (Sprint 5 — docs/ai_assistant.md): conversas por usuário, auditoria das chamadas de
ferramenta e consumo de tokens por tenant/dia. Toda linha leva tenant_id; conversas pertencem a um usuário."""
from datetime import date, datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _uuid() -> str:
    return str(uuid4())


class AiConversation(Base):
    __tablename__ = "ai_conversations"
    __table_args__ = (Index("ix_ai_conversations_tenant_user", "tenant_id", "user_id", "updated_at"),)

    id:         Mapped[str]      = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:  Mapped[str]      = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id:    Mapped[str]      = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title:      Mapped[str]      = mapped_column(String(200), nullable=False, default="Conversa")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AiMessage(Base):
    """Uma mensagem da conversa. Assistente: resposta final (já verificada) + citações, chamadas e avisos."""
    __tablename__ = "ai_messages"
    __table_args__ = (Index("ix_ai_messages_conversation", "conversation_id", "created_at"),)

    id:              Mapped[str]            = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:       Mapped[str]            = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[str]            = mapped_column(UUID(as_uuid=False), ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False)
    role:            Mapped[str]            = mapped_column(String(16), nullable=False)       # user | assistant
    content:         Mapped[str]            = mapped_column(Text, nullable=False)
    citations:       Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    tool_calls:      Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    warnings:        Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    verified:        Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    refused:         Mapped[bool]           = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    refusal_reason:  Mapped[Optional[str]]  = mapped_column(String(32), nullable=True)
    context:         Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at:      Mapped[datetime]       = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AiToolCall(Base):
    """Trilha de auditoria (ai_assistant.md §3): nome, argumentos, status, nº de linhas — nunca o conteúdo."""
    __tablename__ = "ai_tool_calls"
    __table_args__ = (Index("ix_ai_tool_calls_tenant_created", "tenant_id", "created_at"),)

    id:              Mapped[str]            = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:       Mapped[str]            = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id:         Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    conversation_id: Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), ForeignKey("ai_conversations.id", ondelete="SET NULL"), nullable=True)
    name:            Mapped[str]            = mapped_column(String(64), nullable=False)
    args:            Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    status:          Mapped[str]            = mapped_column(String(32), nullable=False)
    http_status:     Mapped[Optional[int]]  = mapped_column(Integer, nullable=True)
    rows:            Mapped[Optional[int]]  = mapped_column(Integer, nullable=True)
    duration_ms:     Mapped[Optional[int]]  = mapped_column(Integer, nullable=True)
    created_at:      Mapped[datetime]       = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AiUsage(Base):
    """Tokens consumidos por tenant e dia (UTC) — orçamento diário do assistente."""
    __tablename__ = "ai_usage"

    tenant_id:     Mapped[str]  = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    day:           Mapped[date] = mapped_column(Date, primary_key=True)
    input_tokens:  Mapped[int]  = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    output_tokens: Mapped[int]  = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    requests:      Mapped[int]  = mapped_column(Integer, nullable=False, default=0, server_default="0")
