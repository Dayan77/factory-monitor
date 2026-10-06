"""Ingestão MQTT (Sprint 5b): leituras que não puderam ser processadas (dead letters) e marcas d'água por tag."""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import DateTime, Index, Integer, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class IngestDeadLetter(Base):
    """Leitura válida que falhou depois das repetições (banco/Influx fora além do prazo, erro de programa).
    Nada é descartado em silêncio: fica aqui (com a métrica fm_ingest_dead_letters) para reprocessar."""
    __tablename__ = "ingest_dead_letters"
    __table_args__ = (Index("ix_ingest_dead_letters_tenant_created", "tenant_id", "created_at"),)

    id:          Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    # do tópico (validado como UUID quando possível); sem FK: a leitura pode ter falhado antes da verificação
    tenant_id:   Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), nullable=True)
    tag_id:      Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), nullable=True)
    topic:       Mapped[str]                = mapped_column(String(512), nullable=False)
    payload:     Mapped[bytes]              = mapped_column(LargeBinary, nullable=False)
    received_at: Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    stage:       Mapped[str]                = mapped_column(String(16), nullable=False)   # lookup|influx|usage|process
    error:       Mapped[str]                = mapped_column(Text, nullable=False)
    attempts:    Mapped[int]                = mapped_column(Integer, nullable=False, default=1)
    created_at:  Mapped[datetime]           = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    replayed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class IngestWatermark(Base):
    """Marca d'água por tag gravada pela ingestão: maior ts de origem gravado no Influx e, enquanto o motor
    preditivo não consumir, o intervalo [late_min_ts, late_max_ts] de leituras que chegaram atrasadas (buffer do
    gateway, backlog) — as janelas já extraídas que ele toca são recalculadas (predictive.md P-12)."""
    __tablename__ = "ingest_watermarks"
    __table_args__ = (Index("ix_ingest_watermarks_late", "tenant_id", "machine_id",
                            postgresql_where="late_min_ts IS NOT NULL"),)

    tenant_id:   Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True)
    tag_id:      Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True)
    machine_id:  Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), nullable=True)
    max_ts:      Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    late_min_ts: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    late_max_ts: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    late_count:  Mapped[int]                = mapped_column(Integer, nullable=False, default=0)
    updated_at:  Mapped[datetime]           = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
