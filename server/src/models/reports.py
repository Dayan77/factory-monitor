"""Relatórios exportáveis e agendados (docs/reports.md, Sprint 6 / Fase 5).

* report_schedules — agendamento (§4): tipo, escopo, horário local, dia da semana/mês, destinatários.
* report_dispatches — um período de um agendamento: idempotência (schedule, período) — nunca envia duas vezes.
* report_runs — instantâneo reprodutível de cada geração (§0.4): período, parâmetros, data_until, versão, hash.
* report_files — o arquivo gerado (PDF/XLSX), separado das linhas de run (listagens leves).
* report_deliveries — registro de cada envio (§5): destinatário, horário, id do provedor, resultado.
* report_unsubscribes — descadastro com um clique (LGPD).
* report_settings / report_external_recipients — destinatários externos (só com o admin habilitando).
* line_targets — metas por linha (§1.1): sem meta, a coluna não aparece.

Enumerações como strings com CHECK. Tudo tem `tenant_id`.
"""
from uuid import uuid4
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, LargeBinary,
                        String, Text, UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin

_uuid = lambda: str(uuid4())  # noqa: E731

REPORT_TYPES = ("daily_production", "shift", "weekly_oee", "monthly_maintenance", "reliability_life")
REPORT_FORMATS = ("pdf", "xlsx")
SCOPE_TYPES = ("tenant", "plant", "line")
RUN_STATUSES = ("generating", "done", "failed")
DISPATCH_STATUSES = ("pending", "sent", "no_channel", "failed")
DELIVERY_STATUSES = ("sent", "failed", "unsubscribed", "no_channel", "bounced")


class ReportSchedule(Base, TimestampMixin):
    __tablename__ = "report_schedules"
    __table_args__ = (
        CheckConstraint(f"report_type IN {REPORT_TYPES}", name="ck_report_schedules_type"),
        CheckConstraint(f"scope_type IN {SCOPE_TYPES}", name="ck_report_schedules_scope"),
        CheckConstraint("weekday IS NULL OR (weekday BETWEEN 0 AND 6)", name="ck_report_schedules_weekday"),
        CheckConstraint("month_day IS NULL OR (month_day BETWEEN 1 AND 28)", name="ck_report_schedules_month_day"),
        Index("ix_report_schedules_tenant", "tenant_id", "enabled"),
    )

    id:               Mapped[str]            = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:        Mapped[str]            = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name:             Mapped[Optional[str]]  = mapped_column(String(255), nullable=True)
    report_type:      Mapped[str]            = mapped_column(String(32), nullable=False)
    scope_type:       Mapped[str]            = mapped_column(String(16), nullable=False)
    scope_id:         Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), nullable=True)
    params:           Mapped[dict]           = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    formats:          Mapped[list[str]]      = mapped_column(ARRAY(String(8)), nullable=False, default=lambda: ["pdf"], server_default=text("'{pdf}'::varchar[]"))
    deliver_at_local: Mapped[Optional[str]]  = mapped_column(String(5), nullable=True)
    weekday:          Mapped[Optional[int]]  = mapped_column(Integer, nullable=True)
    month_day:        Mapped[Optional[int]]  = mapped_column(Integer, nullable=True)
    enabled:          Mapped[bool]           = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    # {"roles": [...], "user_ids": [...], "external_ids": [...]}
    recipients:       Mapped[dict]           = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    created_by:       Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    paused_at:        Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ReportDispatch(Base, TimestampMixin):
    """(schedule, period): created once (unique) when the period is due; the engine claims it with
    SELECT … FOR UPDATE SKIP LOCKED, so two engine passes in the same minute never send twice."""
    __tablename__ = "report_dispatches"
    __table_args__ = (
        UniqueConstraint("schedule_id", "period_key", name="uq_report_dispatches_schedule_period"),
        CheckConstraint(f"status IN {DISPATCH_STATUSES}", name="ck_report_dispatches_status"),
        Index("ix_report_dispatches_due", "status", "next_attempt_at"),
        Index("ix_report_dispatches_tenant", "tenant_id", "created_at"),
    )

    id:              Mapped[str]            = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:       Mapped[str]            = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    schedule_id:     Mapped[str]            = mapped_column(UUID(as_uuid=False), ForeignKey("report_schedules.id", ondelete="CASCADE"), nullable=False)
    period_key:      Mapped[str]            = mapped_column(String(128), nullable=False)
    period:          Mapped[dict]           = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    ready_at:        Mapped[datetime]       = mapped_column(DateTime(timezone=True), nullable=False)
    send_at:         Mapped[datetime]       = mapped_column(DateTime(timezone=True), nullable=False)
    status:          Mapped[str]            = mapped_column(String(16), nullable=False, default="pending", server_default=text("'pending'"))
    attempts:        Mapped[int]            = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error:      Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    manual:          Mapped[bool]           = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    requested_by:    Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), nullable=True)
    completed_at:    Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ReportRun(Base):
    __tablename__ = "report_runs"
    __table_args__ = (
        CheckConstraint(f"report_type IN {REPORT_TYPES}", name="ck_report_runs_type"),
        CheckConstraint(f"format IN {REPORT_FORMATS}", name="ck_report_runs_format"),
        CheckConstraint(f"status IN {RUN_STATUSES}", name="ck_report_runs_status"),
        Index("ix_report_runs_tenant_created", "tenant_id", "created_at"),
        Index("ix_report_runs_version", "tenant_id", "report_type", "scope_id", "period_key", "format", "profile_key"),
        Index("ix_report_runs_schedule", "schedule_id", "created_at"),
    )

    id:                Mapped[str]            = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:         Mapped[str]            = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    schedule_id:       Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), ForeignKey("report_schedules.id", ondelete="SET NULL"), nullable=True)
    dispatch_id:       Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), ForeignKey("report_dispatches.id", ondelete="SET NULL"), nullable=True)
    report_type:       Mapped[str]            = mapped_column(String(32), nullable=False)
    scope_type:        Mapped[str]            = mapped_column(String(16), nullable=False)
    scope_id:          Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), nullable=True)
    scope_name:        Mapped[Optional[str]]  = mapped_column(String(255), nullable=True)
    period_key:        Mapped[str]            = mapped_column(String(128), nullable=False)
    period_start:      Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end:        Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    period_label:      Mapped[Optional[str]]  = mapped_column(String(128), nullable=True)
    params:            Mapped[dict]           = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    format:            Mapped[str]            = mapped_column(String(8), nullable=False)
    version:           Mapped[int]            = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    supersedes_id:     Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), nullable=True)
    # permission profile the content was generated with (roles + lines of the viewer, §0.5)
    profile_key:       Mapped[str]            = mapped_column(String(255), nullable=False, default="", server_default=text("''"))
    viewer_user_id:    Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), nullable=True)
    requested_by:      Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), nullable=True)
    status:            Mapped[str]            = mapped_column(String(16), nullable=False, default="generating", server_default=text("'generating'"))
    error:             Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    generated_at:      Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    data_until:        Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    generator_version: Mapped[str]            = mapped_column(String(32), nullable=False, default="", server_default=text("''"))
    file_sha256:       Mapped[Optional[str]]  = mapped_column(String(64), nullable=True)
    file_size:         Mapped[Optional[int]]  = mapped_column(Integer, nullable=True)
    filename:          Mapped[Optional[str]]  = mapped_column(String(255), nullable=True)
    content_type:      Mapped[Optional[str]]  = mapped_column(String(128), nullable=True)
    observations:      Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    # headline numbers (e-mail body, monthly "versão revisada" comparison)
    summary:           Mapped[dict]           = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    created_at:        Mapped[datetime]       = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class ReportFile(Base):
    __tablename__ = "report_files"

    run_id:    Mapped[str]   = mapped_column(UUID(as_uuid=False), ForeignKey("report_runs.id", ondelete="CASCADE"), primary_key=True)
    tenant_id: Mapped[str]   = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    content:   Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class ReportDelivery(Base):
    __tablename__ = "report_deliveries"
    __table_args__ = (
        CheckConstraint(f"status IN {DELIVERY_STATUSES}", name="ck_report_deliveries_status"),
        Index("ix_report_deliveries_tenant", "tenant_id", "created_at"),
        Index("ix_report_deliveries_dispatch", "dispatch_id"),
    )

    id:                    Mapped[str]            = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:             Mapped[str]            = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    dispatch_id:           Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), ForeignKey("report_dispatches.id", ondelete="CASCADE"), nullable=True)
    schedule_id:           Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), ForeignKey("report_schedules.id", ondelete="SET NULL"), nullable=True)
    run_ids:               Mapped[list[str]]      = mapped_column(ARRAY(UUID(as_uuid=False)), nullable=False, default=list, server_default=text("'{}'::uuid[]"))
    recipient_user_id:     Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), nullable=True)
    external_recipient_id: Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), nullable=True)
    email:                 Mapped[str]            = mapped_column(String(255), nullable=False)
    status:                Mapped[str]            = mapped_column(String(16), nullable=False)
    channel:               Mapped[Optional[str]]  = mapped_column(String(16), nullable=True)
    provider_id:           Mapped[Optional[str]]  = mapped_column(String(255), nullable=True)
    link_only:             Mapped[bool]           = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    error:                 Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    sent_at:               Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at:            Mapped[datetime]       = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class ReportUnsubscribe(Base):
    """schedule_id NULL = every scheduled report of the tenant."""
    __tablename__ = "report_unsubscribes"
    __table_args__ = (
        Index("uq_report_unsubscribes", "tenant_id", "email", "schedule_id", unique=True,
              postgresql_nulls_not_distinct=True),
    )

    id:          Mapped[str]           = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:   Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    email:       Mapped[str]           = mapped_column(String(255), nullable=False)
    schedule_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("report_schedules.id", ondelete="CASCADE"), nullable=True)
    created_at:  Mapped[datetime]      = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class ReportSettings(Base, TimestampMixin):
    __tablename__ = "report_settings"

    tenant_id:                   Mapped[str]  = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    external_recipients_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    # admin warned that no mail channel is configured (once per day, never silent)
    no_channel_warned_on:        Mapped[Optional[date]] = mapped_column(Date, nullable=True)


class ReportExternalRecipient(Base, TimestampMixin):
    __tablename__ = "report_external_recipients"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_report_external_recipients_email"),)

    id:           Mapped[str]            = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:    Mapped[str]            = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    email:        Mapped[str]            = mapped_column(String(255), nullable=False)
    name:         Mapped[str]            = mapped_column(String(255), nullable=False)
    purpose:      Mapped[str]            = mapped_column(String(500), nullable=False)
    legal_basis:  Mapped[str]            = mapped_column(String(64), nullable=False)
    bounce_count: Mapped[int]            = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    disabled_at:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by:   Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), nullable=True)


class LineTarget(Base, TimestampMixin):
    """Meta por linha (reports.md §1.1), vigente a partir de `valid_from` (dia produtivo)."""
    __tablename__ = "line_targets"
    __table_args__ = (
        UniqueConstraint("line_id", "valid_from", name="uq_line_targets_line_valid_from"),
        CheckConstraint("oee IS NULL OR (oee > 0 AND oee <= 1)", name="ck_line_targets_oee"),
        CheckConstraint("parts_per_shift IS NULL OR parts_per_shift > 0", name="ck_line_targets_parts"),
        Index("ix_line_targets_tenant", "tenant_id", "line_id"),
    )

    id:              Mapped[str]             = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:       Mapped[str]             = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    line_id:         Mapped[str]             = mapped_column(UUID(as_uuid=False), ForeignKey("lines.id", ondelete="CASCADE"), nullable=False)
    oee:             Mapped[Optional[float]] = mapped_column(Float(precision=53), nullable=True)
    parts_per_shift: Mapped[Optional[float]] = mapped_column(Float(precision=53), nullable=True)
    valid_from:      Mapped[date]            = mapped_column(Date, nullable=False)
    created_by:      Mapped[Optional[str]]   = mapped_column(UUID(as_uuid=False), nullable=True)
