"""Trilha de auditoria genérica (Sprint 6) e chaves de idempotência de ações do cliente.

* audit_events: quem fez o quê (publicação de layout, ações do usuário de sistema `demo-bot`, importações do
  assistente). Nunca guarda segredo nem dado pessoal além do id do usuário.
* client_actions: chave `X-Client-Action-Id` enviada pelo tablet do operador (fila offline, §3.2). Reenviar a
  mesma ação devolve o que foi gravado na primeira vez — a ação chega uma única vez.
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

_uuid = lambda: str(uuid4())  # noqa: E731


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_tenant_created", "tenant_id", "created_at"),)

    id:          Mapped[str]           = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:   Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id:     Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action:      Mapped[str]           = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str]           = mapped_column(String(32), nullable=False)
    entity_id:   Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    data:        Mapped[dict]          = mapped_column(JSONB, nullable=False, default=dict)
    created_at:  Mapped[datetime]      = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ClientAction(Base):
    __tablename__ = "client_actions"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "action_id", name="uq_client_actions_key"),)

    id:          Mapped[str]           = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:   Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id:     Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    action_id:   Mapped[str]           = mapped_column(String(64), nullable=False)
    endpoint:    Mapped[str]           = mapped_column(String(64), nullable=False)   # ex. downtime.justify
    entity_id:   Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status_code: Mapped[int]           = mapped_column(Integer, nullable=False, default=200)
    created_at:  Mapped[datetime]      = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
