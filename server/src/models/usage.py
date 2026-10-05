"""Processamento de uso (domain.md §3.3–3.5): estado por tag, acumuladores, histórico e eventos."""
from uuid import uuid4
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Float, ForeignKey, DateTime, func, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

# Escopos e medidas armazenados (strings para poder estender sem migration de enum).
SCOPES = ("position", "component", "machine")
# operating_seconds: tempos ficam em segundos no servidor (domain.md, convenções); a API expõe horas.
MEASURES = ("cycles", "operating_seconds", "starts", "good", "scrap", "total")


class TagState(Base):
    """Última leitura aceita por tag (contadores, run_state, fault_state). Base da idempotência."""
    __tablename__ = "tag_states"

    tag_id:        Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    tenant_id:     Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    last_value:    Mapped[float]              = mapped_column(Float(precision=53), nullable=False)
    last_ts:       Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    # contador: leitura rejeitada pelo filtro de plausibilidade aguardando confirmação
    pending_value: Mapped[Optional[float]]    = mapped_column(Float(precision=53), nullable=True)
    pending_ts:    Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # run_state: início do 0 corrente (debounce de partidas)
    off_since:     Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at:    Mapped[datetime]           = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class UsageTotal(Base):
    """Acumulador: usage_total (posição), usage_since_install (componente), totais da máquina."""
    __tablename__ = "usage_totals"

    scope:      Mapped[str]      = mapped_column(String(16), primary_key=True)
    entity_id:  Mapped[str]      = mapped_column(UUID(as_uuid=False), primary_key=True)
    measure:    Mapped[str]      = mapped_column(String(32), primary_key=True)
    tenant_id:  Mapped[str]      = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    value:      Mapped[float]    = mapped_column(Float(precision=53), nullable=False, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class UsageBucket(Base):
    """Histórico horário (UTC) dos deltas — gráficos, ritmo de uso (RUL) e recálculo."""
    __tablename__ = "usage_buckets"
    __table_args__ = (
        Index("ix_usage_buckets_tenant_bucket", "tenant_id", "bucket_start"),
    )

    scope:        Mapped[str]      = mapped_column(String(16), primary_key=True)
    entity_id:    Mapped[str]      = mapped_column(UUID(as_uuid=False), primary_key=True)
    measure:      Mapped[str]      = mapped_column(String(32), primary_key=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    tenant_id:    Mapped[str]      = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    value:        Mapped[float]    = mapped_column(Float(precision=53), nullable=False, default=0.0)


class UsageEvent(Base):
    """Auditoria de contadores: counter_reset, counter_rollover, counter_glitch, counter_rebaseline."""
    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_events_tenant_ts", "tenant_id", "ts"),
    )

    id:        Mapped[str]           = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    tag_id:    Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("tags.id", ondelete="CASCADE"), nullable=False, index=True)
    event:     Mapped[str]           = mapped_column(String(32), nullable=False)
    ts:        Mapped[datetime]      = mapped_column(DateTime(timezone=True), nullable=False)
    prev_value: Mapped[Optional[float]] = mapped_column(Float(precision=53), nullable=True)
    value:     Mapped[Optional[float]] = mapped_column(Float(precision=53), nullable=True)
    delta:     Mapped[Optional[float]] = mapped_column(Float(precision=53), nullable=True)
    details:   Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime]     = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
