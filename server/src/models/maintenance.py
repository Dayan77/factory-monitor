"""Manutenção (domain.md §5, Sprint 4): planos, ordens de serviço (OS) com trilha de auditoria,
apontamentos, trocas de componente, configuração e ausências da equipe.

Enumerações como strings com CHECK (estender sem migration de enum do Postgres).
"""
from uuid import uuid4
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin

_uuid = lambda: str(uuid4())  # noqa: E731

PLAN_WO_TYPES = ("preventive", "predictive")
PLAN_STATES = ("ok", "due_soon", "due", "overdue")
WO_TYPES = ("preventive", "predictive", "corrective", "improvement")
WO_URGENCIES = ("emergency", "deferred")
WO_STATES = ("draft", "open", "approved", "scheduled", "in_progress", "on_hold", "completed", "closed", "cancelled")
CANCEL_REASONS = ("superseded", "production_conflict", "duplicate", "not_needed", "other")


class MaintenancePlan(Base, TimestampMixin):
    """Plano de manutenção (domain.md §5.1). Ancorado numa posição (ou na máquina inteira).
    Gatilhos: interval_cycles / interval_hours / interval_starts / interval_days — o que vier primeiro.
    `last_reset_at` / `last_reset_usage` = referência do uso desde a manutenção (S4-1, S4-2):
    uso desde a manutenção = usage_total da âncora agora − last_reset_usage."""
    __tablename__ = "maintenance_plans"
    __table_args__ = (
        CheckConstraint(f"wo_type IN {PLAN_WO_TYPES}", name="ck_maintenance_plans_wo_type"),
        CheckConstraint(f"state IN {PLAN_STATES}", name="ck_maintenance_plans_state"),
        CheckConstraint("position_id IS NOT NULL OR machine_id IS NOT NULL", name="ck_maintenance_plans_anchor"),
        Index("ix_maintenance_plans_tenant", "tenant_id", "is_active"),
    )

    id:                   Mapped[str]             = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:            Mapped[str]             = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    position_id:          Mapped[Optional[str]]   = mapped_column(UUID(as_uuid=False), ForeignKey("positions.id", ondelete="CASCADE"), nullable=True, index=True)
    machine_id:           Mapped[str]             = mapped_column(UUID(as_uuid=False), ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True)
    name:                 Mapped[str]             = mapped_column(String(255), nullable=False)
    description:          Mapped[Optional[str]]   = mapped_column(Text, nullable=True)
    task_list:            Mapped[list]            = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    estimated_hours:      Mapped[float]           = mapped_column(Float, nullable=False, default=0.0)
    wo_type:              Mapped[str]             = mapped_column(String(16), nullable=False, default="preventive")
    interval_cycles:      Mapped[Optional[float]] = mapped_column(Float(precision=53), nullable=True)
    interval_hours:       Mapped[Optional[float]] = mapped_column(Float(precision=53), nullable=True)
    interval_starts:      Mapped[Optional[float]] = mapped_column(Float(precision=53), nullable=True)
    interval_days:        Mapped[Optional[int]]   = mapped_column(Integer, nullable=True)
    tolerance_pct:        Mapped[float]           = mapped_column(Float, nullable=False, default=10.0, server_default=text("10"))
    reset_on_replacement: Mapped[bool]            = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    stops_machine:        Mapped[bool]            = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    is_active:            Mapped[bool]            = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    last_reset_at:        Mapped[datetime]        = mapped_column(DateTime(timezone=True), nullable=False)
    # {"cycles": float, "hours": float, "starts": float} — usage_total da âncora em last_reset_at
    last_reset_usage:     Mapped[dict]            = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    last_reset_by:        Mapped[Optional[str]]   = mapped_column(String(255), nullable=True)
    # cache do último recálculo (notificações na mudança de estado)
    state:                Mapped[str]             = mapped_column(String(16), nullable=False, default="ok", server_default=text("'ok'"))
    consumed:             Mapped[Optional[float]] = mapped_column(Float(precision=53), nullable=True)
    evaluated_at:         Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Sprint 5 (predictive.md P-9): gatilho de condição {feature, min_severity}; vencimento por condição
    condition_trigger:    Mapped[Optional[dict]]  = mapped_column(JSONB, nullable=True)
    condition_due_date:   Mapped[Optional[date]]  = mapped_column(Date, nullable=True)
    condition_alert_id:   Mapped[Optional[str]]   = mapped_column(UUID(as_uuid=False), nullable=True)
    created_by:           Mapped[Optional[str]]   = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class WorkOrder(Base, TimestampMixin):
    """Ordem de serviço (domain.md §5.2–5.4, S4-5). `number` é sequencial por tenant (OS-###)."""
    __tablename__ = "work_orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number", name="uq_work_orders_tenant_number"),
        CheckConstraint(f"type IN {WO_TYPES}", name="ck_work_orders_type"),
        CheckConstraint(f"state IN {WO_STATES}", name="ck_work_orders_state"),
        CheckConstraint(f"urgency IS NULL OR urgency IN {WO_URGENCIES}", name="ck_work_orders_urgency"),
        CheckConstraint(f"cancel_reason IS NULL OR cancel_reason IN {CANCEL_REASONS}", name="ck_work_orders_cancel_reason"),
        Index("ix_work_orders_tenant_state", "tenant_id", "state"),
        # S4-4: no máximo uma OS aberta (draft … on_hold) por plano
        Index("uq_work_orders_one_open_per_plan", "plan_id", unique=True,
              postgresql_where=text("plan_id IS NOT NULL AND state IN ('draft','open','approved','scheduled','in_progress','on_hold')")),
    )

    id:                Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:         Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    number:            Mapped[int]                = mapped_column(Integer, nullable=False)
    type:              Mapped[str]                = mapped_column(String(16), nullable=False)
    urgency:           Mapped[Optional[str]]      = mapped_column(String(16), nullable=True)
    state:             Mapped[str]                = mapped_column(String(16), nullable=False)
    plan_id:           Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("maintenance_plans.id", ondelete="SET NULL"), nullable=True, index=True)
    position_id:       Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("positions.id", ondelete="SET NULL"), nullable=True, index=True)
    machine_id:        Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True)
    downtime_event_id: Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("downtime_events.id", ondelete="SET NULL"), nullable=True)
    failure_id:        Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("failure_records.id", ondelete="SET NULL"), nullable=True)
    parent_id:         Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("work_orders.id", ondelete="SET NULL"), nullable=True)
    title:             Mapped[str]                = mapped_column(String(255), nullable=False)
    description:       Mapped[Optional[str]]      = mapped_column(Text, nullable=True)
    # [{"id": str, "text": str, "done": bool, "result": "ok"|"not_ok"|null, "note": str|null}]
    task_list:         Mapped[list]               = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    estimated_hours:   Mapped[float]              = mapped_column(Float, nullable=False, default=0.0)
    labor_hours:       Mapped[float]              = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    remaining_hours:   Mapped[Optional[float]]    = mapped_column(Float, nullable=True)
    stops_machine:     Mapped[bool]               = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    scheduled_for:     Mapped[Optional[date]]     = mapped_column(Date, nullable=True)
    scheduled_at:      Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    assignee_id:       Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by:        Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    requested_at:      Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at:       Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at:        Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at:      Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at:         Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at:      Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason:     Mapped[Optional[str]]      = mapped_column(String(32), nullable=True)
    hold_reason:       Mapped[Optional[str]]      = mapped_column(String(500), nullable=True)
    # instante da última transição de estado (validação de `at` retroativo)
    state_changed_at:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    overdue_notified_on: Mapped[Optional[date]]   = mapped_column(Date, nullable=True)
    # Sprint 5 (predictive.md P-9): OS preditiva gerada de um alerta; achado na conclusão; reinício da linha de base
    source_alert_id:   Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("predictive_alerts.id", ondelete="SET NULL"), nullable=True, index=True)
    finding:           Mapped[Optional[str]]      = mapped_column(String(24), nullable=True)
    resets_baseline:   Mapped[Optional[bool]]     = mapped_column(Boolean, nullable=True)
    due_by:            Mapped[Optional[date]]     = mapped_column(Date, nullable=True)


class WorkOrderHistory(Base):
    """Trilha de auditoria: toda transição de estado (quem, quando, de → para, nota)."""
    __tablename__ = "work_order_history"
    __table_args__ = (Index("ix_work_order_history_wo_at", "work_order_id", "at"),)

    id:            Mapped[str]           = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:     Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    work_order_id: Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False)
    at:            Mapped[datetime]      = mapped_column(DateTime(timezone=True), nullable=False)
    by:            Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    from_state:    Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    to_state:      Mapped[str]           = mapped_column(String(16), nullable=False)
    note:          Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # set when the action came from an API token (integration): `by` is then the token's creator
    api_token_id:  Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("api_tokens.id", ondelete="SET NULL"), nullable=True)
    recorded_at:   Mapped[datetime]      = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class WorkOrderLabor(Base):
    """Apontamento de horas de um técnico (S4-5: labor_hours = soma; tempo em on_hold não conta)."""
    __tablename__ = "work_order_labor"
    __table_args__ = (UniqueConstraint("work_order_id", "client_action_id", name="uq_work_order_labor_client_action"),)

    id:              Mapped[str]             = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:       Mapped[str]             = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    work_order_id:   Mapped[str]             = mapped_column(UUID(as_uuid=False), ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id:         Mapped[Optional[str]]   = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    hours:           Mapped[float]           = mapped_column(Float, nullable=False)
    remaining_hours: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    note:            Mapped[Optional[str]]   = mapped_column(Text, nullable=True)
    at:              Mapped[datetime]        = mapped_column(DateTime(timezone=True), nullable=False)
    # X-Client-Action-Id of an offline-queued action (the app replays it): a replay is not counted twice
    client_action_id: Mapped[Optional[str]]  = mapped_column(String(64), nullable=True)


class WorkOrderReplacement(Base):
    """Troca de componente registrada na OS (domain.md §5.4, S4-2). Aplicada na conclusão:
    removido com removed_at = completed_at; novo com installed_at = completed_at."""
    __tablename__ = "work_order_replacements"

    id:                     Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:              Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    work_order_id:          Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("work_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    position_id:            Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("positions.id", ondelete="CASCADE"), nullable=False)
    removed_component_id:   Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("components.id", ondelete="SET NULL"), nullable=True)
    installed_component_id: Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("components.id", ondelete="CASCADE"), nullable=False)
    removal_reason:         Mapped[str]                = mapped_column(String(32), nullable=False)
    applied_at:             Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by:             Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at:             Mapped[datetime]           = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class MaintenanceSettings(Base, TimestampMixin):
    """Parâmetros de manutenção por tenant (kpis.md §3.2 tolerância; §3.3 capacidade)."""
    __tablename__ = "maintenance_settings"

    tenant_id:                  Mapped[str]   = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    schedule_tolerance_days:    Mapped[int]   = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    technician_hours_per_week:  Mapped[float] = mapped_column(Float, nullable=False, default=44.0, server_default=text("44"))
    due_soon_at:                Mapped[float] = mapped_column(Float, nullable=False, default=0.8, server_default=text("0.8"))
    # chamado emergencial: parada EQP-* aberta há pelo menos isto (s) notifica o plantão
    emergency_call_after_s:     Mapped[int]   = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    # MTTR padrão (min) para o escalonamento quando a posição não tem histórico
    default_mttr_min:           Mapped[float] = mapped_column(Float, nullable=False, default=60.0, server_default=text("60"))
    digest_hour_local:          Mapped[int]   = mapped_column(Integer, nullable=False, default=7, server_default=text("7"))


class MaintenanceAbsence(Base, TimestampMixin):
    """Ausência prevista de um técnico (desconta da capacidade semanal, kpis.md §3.3)."""
    __tablename__ = "maintenance_absences"

    id:        Mapped[str]           = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id: Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id:   Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    date:      Mapped[date]          = mapped_column(Date, nullable=False)
    hours:     Mapped[float]         = mapped_column(Float, nullable=False)
    note:      Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
