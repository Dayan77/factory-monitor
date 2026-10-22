"""Assistente de implantação (Sprint 6 — docs/sprint6.md §4) e comandos puxados pelo gateway (§4.2).

* onboarding_progress: um registro por tenant com o estado de cada passo (1–9) — o assistente é retomável;
* gateway_commands: pedido da UI que o gateway busca na resposta do heartbeat (o servidor nunca abre conexão
  para dentro da fábrica). Expira em 5 min sem resposta. O resultado (árvore OPC UA) nunca contém valor de
  processo nem credencial.
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin

COMMAND_TYPES = ("opcua_browse",)
COMMAND_STATUSES = ("pending", "sent", "done", "failed", "expired")

_uuid = lambda: str(uuid4())  # noqa: E731


class OnboardingProgress(Base, TimestampMixin):
    __tablename__ = "onboarding_progress"

    tenant_id:   Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    plant_id:    Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("plants.id", ondelete="SET NULL"), nullable=True)
    # {"1": {"state": "done", "data": {...}, "warnings": [...], "at": iso, "by": user_id}, ...}
    steps:       Mapped[dict]          = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    updated_by:  Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # momento em que todos os itens obrigatórios do passo 9 ficaram verdes (15 min contínuos de dados)
    ready_at:    Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class GatewayCommand(Base, TimestampMixin):
    __tablename__ = "gateway_commands"
    __table_args__ = (
        CheckConstraint(f"type IN {COMMAND_TYPES}", name="ck_gateway_commands_type"),
        CheckConstraint(f"status IN {COMMAND_STATUSES}", name="ck_gateway_commands_status"),
        Index("ix_gateway_commands_gateway_status", "gateway_id", "status"),
    )

    id:           Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:    Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    gateway_id:   Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("gateways.id", ondelete="CASCADE"), nullable=False)
    type:         Mapped[str]                = mapped_column(String(32), nullable=False)
    params:       Mapped[dict]               = mapped_column(JSONB, nullable=False, default=dict)
    status:       Mapped[str]                = mapped_column(String(16), nullable=False, default="pending")
    result:       Mapped[Optional[dict]]     = mapped_column(JSONB, nullable=True)
    error:        Mapped[Optional[str]]      = mapped_column(Text, nullable=True)
    attempts:     Mapped[int]                = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    created_by:   Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    expires_at:   Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at:      Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
