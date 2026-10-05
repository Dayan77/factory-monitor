"""Produção (domain.md §4): calendário de turnos, produtos, motivos de parada, paradas, falhas.

Enumerações ficam como strings com CHECK (estender sem migration de enum do Postgres).
"""
from uuid import uuid4
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin

_uuid = lambda: str(uuid4())  # noqa: E731

CALENDAR_KINDS = ("holiday", "extra_shift", "planned_stop")
DOWNTIME_SOURCES = ("auto_fault", "auto_run_state", "auto_cycle_gap", "manual")
FAILURE_STATUSES = ("draft", "complete")
FAILURE_SEVERITIES = ("critical", "degraded", "incipient")
OEE_LOSSES = ("none", "availability", "performance", "quality")


class Shift(Base, TimestampMixin):
    """Turno padrão da planta. Horários locais (fuso da planta); `end_local <= start_local` cruza a
    meia-noite e pertence ao dia produtivo em que começa. `weekdays`: 0 = segunda … 6 = domingo
    (dia produtivo do início). `breaks`: [{start_local, end_local, reason_code}]."""
    __tablename__ = "shifts"

    id:          Mapped[str]       = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:   Mapped[str]       = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    plant_id:    Mapped[str]       = mapped_column(UUID(as_uuid=False), ForeignKey("plants.id", ondelete="CASCADE"), nullable=False, index=True)
    name:        Mapped[str]       = mapped_column(String(100), nullable=False)
    start_local: Mapped[str]       = mapped_column(String(5), nullable=False)
    end_local:   Mapped[str]       = mapped_column(String(5), nullable=False)
    weekdays:    Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False, default=lambda: [0, 1, 2, 3, 4])
    breaks:      Mapped[list]      = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    is_active:   Mapped[bool]      = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    # Vigência desta versão do turno (dias produtivos, inclusivos; nulo = aberto). Uma versão
    # encerrada (is_active falso + valid_to) continua valendo para os dias até valid_to.
    valid_from:  Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    valid_to:    Mapped[Optional[date]] = mapped_column(Date, nullable=True)


class CalendarException(Base, TimestampMixin):
    """Exceção ao calendário padrão num dia produtivo: feriado (sem turno, ou só `shift_id`),
    turno extra (`start_local`–`end_local`) ou parada planejada (PDOT; sem horário = turno inteiro)."""
    __tablename__ = "calendar_exceptions"
    __table_args__ = (CheckConstraint(f"kind IN {CALENDAR_KINDS}", name="ck_calendar_exceptions_kind"),)

    id:          Mapped[str]           = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:   Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    plant_id:    Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("plants.id", ondelete="CASCADE"), nullable=False, index=True)
    date:        Mapped[date]          = mapped_column(Date, nullable=False)
    kind:        Mapped[str]           = mapped_column(String(16), nullable=False)
    shift_id:    Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("shifts.id", ondelete="CASCADE"), nullable=True)
    start_local: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    end_local:   Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    reason_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    note:        Mapped[Optional[str]] = mapped_column(String(500), nullable=True)


class Product(Base, TimestampMixin):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_products_tenant_code"),)

    id:        Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    code:      Mapped[str] = mapped_column(String(64), nullable=False)
    name:      Mapped[str] = mapped_column(String(255), nullable=False)


class MachineProduct(Base, TimestampMixin):
    """Tempo de ciclo ideal (PRI) por (máquina, produto) — domain.md §4.2. PRI por peça =
    ideal_cycle_time_s / parts_per_cycle. No máximo um `is_default` por máquina."""
    __tablename__ = "machine_products"
    __table_args__ = (
        UniqueConstraint("machine_id", "product_id", name="uq_machine_products_machine_product"),
        Index("uq_machine_products_one_default", "machine_id", unique=True, postgresql_where=text("is_default")),
        CheckConstraint("ideal_cycle_time_s > 0", name="ck_machine_products_ict"),
        CheckConstraint("parts_per_cycle >= 1", name="ck_machine_products_ppc"),
    )

    id:                 Mapped[str]   = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:          Mapped[str]   = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    machine_id:         Mapped[str]   = mapped_column(UUID(as_uuid=False), ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id:         Mapped[str]   = mapped_column(UUID(as_uuid=False), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    ideal_cycle_time_s: Mapped[float] = mapped_column(Float, nullable=False)
    parts_per_cycle:    Mapped[int]   = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    is_default:         Mapped[bool]  = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))


class ProductionRun(Base, TimestampMixin):
    """Ordem/lote em produção numa máquina. No máximo uma aberta (ended_at nulo) por máquina."""
    __tablename__ = "production_runs"
    __table_args__ = (
        Index("uq_production_runs_one_open", "machine_id", unique=True, postgresql_where=text("ended_at IS NULL")),
        Index("ix_production_runs_machine_started", "machine_id", "started_at"),
    )

    id:         Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:  Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    machine_id: Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("machines.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    started_at: Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at:   Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ReasonCode(Base, TimestampMixin):
    """Motivo de parada (domain.md §4.4). `tenant_id` nulo = padrão do sistema (visível a todos);
    o tenant estende com códigos próprios (ex. `EQP-01-1`)."""
    __tablename__ = "reason_codes"
    __table_args__ = (
        Index("uq_reason_codes_default_code", "code", unique=True, postgresql_where=text("tenant_id IS NULL")),
        Index("uq_reason_codes_tenant_code", "tenant_id", "code", unique=True,
              postgresql_where=text("tenant_id IS NOT NULL")),
        CheckConstraint(f"oee_loss IN {OEE_LOSSES}", name="ck_reason_codes_oee_loss"),
    )

    id:        Mapped[str]           = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    code:      Mapped[str]           = mapped_column(String(16), nullable=False)
    category:  Mapped[str]           = mapped_column(String(64), nullable=False)
    label:     Mapped[str]           = mapped_column(String(255), nullable=False)
    planned:   Mapped[bool]          = mapped_column(Boolean, nullable=False, default=False)
    oee_loss:  Mapped[str]           = mapped_column(String(16), nullable=False, default="availability")
    is_active: Mapped[bool]          = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))


class DowntimeEvent(Base, TimestampMixin):
    """Parada ≥ micro_stop_threshold_s (domain.md §4.3). Uma parada que cruza a fronteira de turno
    vira um evento por turno (regra 5); `stop_start` é o início da parada contígua inteira."""
    __tablename__ = "downtime_events"
    __table_args__ = (
        CheckConstraint(f"source IN {DOWNTIME_SOURCES}", name="ck_downtime_events_source"),
        Index("ix_downtime_events_tenant_machine_start", "tenant_id", "machine_id", "start"),
        Index("uq_downtime_events_auto_start", "machine_id", "start", unique=True,
              postgresql_where=text("source <> 'manual'")),
    )

    id:                    Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:             Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    machine_id:            Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("machines.id", ondelete="CASCADE"), nullable=False)
    start:                 Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    end:                   Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_s:            Mapped[Optional[float]]    = mapped_column(Float, nullable=True)
    stop_start:            Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    source:                Mapped[str]                = mapped_column(String(16), nullable=False)
    reason_code:           Mapped[Optional[str]]      = mapped_column(String(16), nullable=True)
    suggested_reason_code: Mapped[Optional[str]]      = mapped_column(String(16), nullable=True)
    planned:               Mapped[bool]               = mapped_column(Boolean, nullable=False, default=False)
    fault_code:            Mapped[Optional[int]]      = mapped_column(Integer, nullable=True)
    fault_since:           Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_id:            Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("failure_records.id", ondelete="SET NULL", use_alter=True), nullable=True)
    comment:               Mapped[Optional[str]]      = mapped_column(Text, nullable=True)
    shift_id:              Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("shifts.id", ondelete="SET NULL"), nullable=True)
    productive_day:        Mapped[Optional[date]]     = mapped_column(Date, nullable=True)
    justified_by:          Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    justified_at:          Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class FailureRecord(Base, TimestampMixin):
    """Registro de falha ISO 14224 (domain.md §5.5) — rascunho criado pelo servidor para toda parada
    `EQP-*`; o técnico completa modo/causa/mecanismo (Fase 3)."""
    __tablename__ = "failure_records"
    __table_args__ = (
        CheckConstraint(f"status IN {FAILURE_STATUSES}", name="ck_failure_records_status"),
        CheckConstraint(f"severity IN {FAILURE_SEVERITIES}", name="ck_failure_records_severity"),
        Index("ix_failure_records_tenant_machine_failed", "tenant_id", "machine_id", "failed_at"),
    )

    id:                Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:         Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    machine_id:        Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("machines.id", ondelete="CASCADE"), nullable=False)
    position_id:       Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("positions.id", ondelete="SET NULL"), nullable=True)
    downtime_event_id: Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("downtime_events.id", ondelete="SET NULL"), nullable=True)
    failed_at:         Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    restored_at:       Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    severity:          Mapped[str]                = mapped_column(String(16), nullable=False, default="critical")
    failure_mode:      Mapped[Optional[str]]      = mapped_column(String(8), nullable=True)
    cause:             Mapped[Optional[str]]      = mapped_column(String(16), nullable=True)
    mechanism:         Mapped[Optional[str]]      = mapped_column(String(16), nullable=True)
    detection_method:  Mapped[Optional[str]]      = mapped_column(String(64), nullable=True)
    status:            Mapped[str]                = mapped_column(String(16), nullable=False, default="draft")


class MicroStopBucket(Base):
    """Acumulador de micro-paradas (kpis.md §1.8) por máquina × hora (UTC) × turno. `short_faults` =
    falhas com código mais curtas que o limiar (C22). `shift_slot` = id do turno, ou '' fora de
    turno nomeado (turno extra sem id)."""
    __tablename__ = "micro_stop_buckets"
    __table_args__ = (Index("ix_micro_stop_buckets_tenant_bucket", "tenant_id", "bucket_start"),)

    machine_id:     Mapped[str]            = mapped_column(UUID(as_uuid=False), ForeignKey("machines.id", ondelete="CASCADE"), primary_key=True)
    bucket_start:   Mapped[datetime]       = mapped_column(DateTime(timezone=True), primary_key=True)
    shift_slot:     Mapped[str]            = mapped_column(String(36), primary_key=True, default="")
    tenant_id:      Mapped[str]            = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    shift_id:       Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), nullable=True)
    productive_day: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # max(bucket_start, início da instância de turno): o bucket entra na janela que contém este instante
    piece_start:    Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    count:          Mapped[int]            = mapped_column(Integer, nullable=False, default=0)
    loss_s:         Mapped[float]          = mapped_column(Float(precision=53), nullable=False, default=0.0)
    short_faults:   Mapped[int]            = mapped_column(Integer, nullable=False, default=0)


class MachineDataSpan(Base):
    """Intervalos em que a máquina tinha dados (C11: até última leitura + machine_offline_timeout_s).
    O complemento dentro do tempo programado é "sem dados" (domain.md §4.1, data_coverage)."""
    __tablename__ = "machine_data_spans"
    __table_args__ = (Index("ix_machine_data_spans_machine_start", "machine_id", "start"),)

    id:         Mapped[str]      = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:  Mapped[str]      = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    machine_id: Mapped[str]      = mapped_column(UUID(as_uuid=False), ForeignKey("machines.id", ondelete="CASCADE"), nullable=False)
    start:      Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end:        Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DowntimeDetectorState(Base):
    """Cursor do detector de paradas por máquina: tudo antes de `checkpoint` está finalizado.
    `state` = estado dos sinais no checkpoint (run, fault, último ciclo, última leitura)."""
    __tablename__ = "downtime_detector_states"

    machine_id:    Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("machines.id", ondelete="CASCADE"), primary_key=True)
    tenant_id:     Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    checkpoint:    Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    covered_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    state:         Mapped[dict]               = mapped_column(JSONB, nullable=False, default=dict)
    # menor ts de leitura gravada depois que o checkpoint já tinha passado por ela → rewind
    late_from:     Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at:    Mapped[datetime]           = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class DowntimeDetectorSnapshot(Base):
    """Checkpoint antigo do detector, para re-detectar a partir dele quando chegam dados atrasados.
    `micro` = linhas de micro_stop_buckets com bucket_start ≥ `micro_from` contendo só as
    micro-paradas já definitivas nesse checkpoint (as que terminaram antes dele)."""
    __tablename__ = "downtime_detector_snapshots"

    machine_id: Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("machines.id", ondelete="CASCADE"), primary_key=True)
    checkpoint: Mapped[datetime]           = mapped_column(DateTime(timezone=True), primary_key=True)
    tenant_id:  Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    state:      Mapped[dict]               = mapped_column(JSONB, nullable=False, default=dict)
    micro_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    micro:      Mapped[list]               = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime]           = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
