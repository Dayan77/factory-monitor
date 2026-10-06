"""Manutenção preditiva (docs/predictive.md, Sprint 5): monitores por (posição, sinal), regras de condição,
características por janela, linhas de base, alertas preditivos e sua trilha, plano diário de produção (P-7).

Enumerações como strings com CHECK (estender sem migration de enum do Postgres). Tudo tem `tenant_id`.
"""
from uuid import uuid4
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin

_uuid = lambda: str(uuid4())  # noqa: E731

PRED_FEATURES = ("stroke_time", "current", "vibration", "temperature")
PRED_DIRECTIONS = ("up", "down")
PRED_RULE_TYPES = ("threshold", "pct_of_baseline", "rate_of_change")
PRED_SEVERITIES = ("watch", "warning", "critical")
PRED_ALERT_STATES = ("open", "acknowledged", "converted", "closed")
PRED_RESOLUTIONS = ("confirmed", "false_alarm", "failed_before_action", "rebaseline", "duplicate", "known_cause",
                    "auto_cleared")
PRED_RESET_KINDS = ("initial", "installation", "work_order", "manual", "alert_rebaseline", "config_change")
WO_FINDINGS = ("defect_confirmed", "no_defect")


class PredictiveMonitor(Base, TimestampMixin):
    """Um sinal monitorado numa posição (P-1). Janela por ciclos (`window_cycles`, stroke_time) ou por tempo
    (`window_hours`, analógicos), alinhada a `anchor_at`. `status` é o cache do último processamento (fase,
    EWMA, CUSUM, último ponto, RUL) — reconstruível pela dobra (P-12)."""
    __tablename__ = "predictive_monitors"
    __table_args__ = (
        UniqueConstraint("position_id", "feature", name="uq_predictive_monitors_position_feature"),
        CheckConstraint(f"feature IN {PRED_FEATURES}", name="ck_predictive_monitors_feature"),
        CheckConstraint(f"direction IN {PRED_DIRECTIONS}", name="ck_predictive_monitors_direction"),
        CheckConstraint("(window_cycles IS NOT NULL) <> (window_hours IS NOT NULL)", name="ck_predictive_monitors_window"),
        Index("ix_predictive_monitors_tenant", "tenant_id", "enabled"),
    )

    id:                     Mapped[str]             = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:              Mapped[str]             = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    position_id:            Mapped[str]             = mapped_column(UUID(as_uuid=False), ForeignKey("positions.id", ondelete="CASCADE"), nullable=False, index=True)
    machine_id:             Mapped[str]             = mapped_column(UUID(as_uuid=False), ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True)
    feature:                Mapped[str]             = mapped_column(String(16), nullable=False)
    tag_id:                 Mapped[Optional[str]]   = mapped_column(UUID(as_uuid=False), ForeignKey("tags.id", ondelete="SET NULL"), nullable=True)
    ambient_tag_id:         Mapped[Optional[str]]   = mapped_column(UUID(as_uuid=False), ForeignKey("tags.id", ondelete="SET NULL"), nullable=True)
    direction:              Mapped[str]             = mapped_column(String(8), nullable=False, default="up", server_default=text("'up'"))
    window_cycles:          Mapped[Optional[int]]   = mapped_column(Integer, nullable=True)
    window_hours:           Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    anchor_at:              Mapped[datetime]        = mapped_column(DateTime(timezone=True), nullable=False)
    warmup_windows:         Mapped[int]             = mapped_column(Integer, nullable=False)
    failure_limit:          Mapped[Optional[float]] = mapped_column(Float(precision=53), nullable=True)
    failure_limit_relative: Mapped[bool]            = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    sensor_resolution:      Mapped[float]           = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    unit:                   Mapped[Optional[str]]   = mapped_column(String(32), nullable=True)
    # overrides of the documented defaults (baseline_min_windows, ewma_lambda, …)
    params:                 Mapped[dict]            = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    enabled:                Mapped[bool]            = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    status:                 Mapped[dict]            = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    processed_until:        Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by:             Mapped[Optional[str]]   = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class PredictiveRule(Base, TimestampMixin):
    """Regra de condição (P-3) de um monitor. `code` é o identificador mostrado na evidência do alerta."""
    __tablename__ = "predictive_rules"
    __table_args__ = (
        UniqueConstraint("monitor_id", "code", name="uq_predictive_rules_monitor_code"),
        CheckConstraint(f"type IN {PRED_RULE_TYPES}", name="ck_predictive_rules_type"),
        CheckConstraint(f"severity IN {PRED_SEVERITIES}", name="ck_predictive_rules_severity"),
        CheckConstraint("sustained_for >= 1", name="ck_predictive_rules_sustained"),
    )

    id:            Mapped[str]           = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:     Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    monitor_id:    Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("predictive_monitors.id", ondelete="CASCADE"), nullable=False, index=True)
    code:          Mapped[str]           = mapped_column(String(64), nullable=False)
    type:          Mapped[str]           = mapped_column(String(24), nullable=False)
    value:         Mapped[float]         = mapped_column(Float(precision=53), nullable=False)
    sustained_for: Mapped[int]           = mapped_column(Integer, nullable=False, default=2, server_default=text("2"))
    severity:      Mapped[str]           = mapped_column(String(16), nullable=False, default="warning")
    enabled:       Mapped[bool]          = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    description:   Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class ConditionBaseline(Base):
    """Evento de reinício + a linha de base resultante (P-2). Nunca é apagada. Enquanto aprende, m0/σ são nulos."""
    __tablename__ = "condition_baselines"
    __table_args__ = (
        CheckConstraint(f"reset_kind IN {PRED_RESET_KINDS}", name="ck_condition_baselines_kind"),
        Index("ix_condition_baselines_monitor_reset", "monitor_id", "reset_at"),
    )

    id:             Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:      Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    monitor_id:     Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("predictive_monitors.id", ondelete="CASCADE"), nullable=False)
    position_id:    Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("positions.id", ondelete="CASCADE"), nullable=False)
    feature:        Mapped[str]                = mapped_column(String(16), nullable=False)
    reset_at:       Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    reset_kind:     Mapped[str]                = mapped_column(String(24), nullable=False)
    warmup:         Mapped[bool]               = mapped_column(Boolean, nullable=False)
    reason:         Mapped[Optional[str]]      = mapped_column(Text, nullable=True)
    source_id:      Mapped[Optional[str]]      = mapped_column(String(64), nullable=True)
    user_id:        Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    first_i:        Mapped[Optional[int]]      = mapped_column(Integer, nullable=True)   # first window index of the segment
    m0:             Mapped[Optional[float]]    = mapped_column(Float(precision=53), nullable=True)
    mad:            Mapped[Optional[float]]    = mapped_column(Float(precision=53), nullable=True)
    sigma:          Mapped[Optional[float]]    = mapped_column(Float(precision=53), nullable=True)
    sigma_floor:    Mapped[Optional[float]]    = mapped_column(Float(precision=53), nullable=True)
    from_i:         Mapped[Optional[int]]      = mapped_column(Integer, nullable=True)
    to_i:           Mapped[Optional[int]]      = mapped_column(Integer, nullable=True)
    n:              Mapped[Optional[int]]      = mapped_column(Integer, nullable=True)
    established_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at:     Mapped[datetime]           = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class ConditionFeature(Base):
    """Uma característica por janela (P-1) e o resultado dos detectores nela (P-4…P-6)."""
    __tablename__ = "condition_features"
    __table_args__ = (
        Index("ix_condition_features_tenant_pos_feat_end", "tenant_id", "position_id", "feature", "end_at"),
    )

    monitor_id:  Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("predictive_monitors.id", ondelete="CASCADE"), primary_key=True)
    i:           Mapped[int]                = mapped_column(Integer, primary_key=True)
    tenant_id:   Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    position_id: Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("positions.id", ondelete="CASCADE"), nullable=False)
    feature:     Mapped[str]                = mapped_column(String(16), nullable=False)
    baseline_id: Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("condition_baselines.id", ondelete="SET NULL"), nullable=True)
    start_at:    Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    end_at:      Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    x:           Mapped[Optional[float]]    = mapped_column(Float(precision=53), nullable=True)
    n:           Mapped[int]                = mapped_column(Integer, nullable=False, default=0)
    valid:       Mapped[bool]               = mapped_column(Boolean, nullable=False)
    u:           Mapped[float]              = mapped_column(Float(precision=53), nullable=False, default=0.0)
    running_h:   Mapped[Optional[float]]    = mapped_column(Float, nullable=True)
    coverage:    Mapped[Optional[float]]    = mapped_column(Float, nullable=True)
    phase:       Mapped[Optional[str]]      = mapped_column(String(16), nullable=True)
    z:           Mapped[Optional[float]]    = mapped_column(Float(precision=53), nullable=True)
    ewma:        Mapped[Optional[float]]    = mapped_column(Float(precision=53), nullable=True)
    cusum:       Mapped[Optional[float]]    = mapped_column(Float(precision=53), nullable=True)
    rolling_z:   Mapped[Optional[float]]    = mapped_column(Float(precision=53), nullable=True)
    anomaly:     Mapped[Optional[bool]]     = mapped_column(Boolean, nullable=True)
    slope:       Mapped[Optional[float]]    = mapped_column(Float(precision=53), nullable=True)
    triggers:    Mapped[list]               = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    severity:    Mapped[Optional[str]]      = mapped_column(String(16), nullable=True)
    rul:         Mapped[Optional[dict]]     = mapped_column(JSONB, nullable=True)
    created_at:  Mapped[datetime]           = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class PredictiveAlert(Base, TimestampMixin):
    """Alerta preditivo (P-8): no máximo um não fechado por (posição, sinal). Novos disparos viram evidência."""
    __tablename__ = "predictive_alerts"
    __table_args__ = (
        CheckConstraint(f"state IN {PRED_ALERT_STATES}", name="ck_predictive_alerts_state"),
        CheckConstraint(f"severity IN {PRED_SEVERITIES}", name="ck_predictive_alerts_severity"),
        CheckConstraint(f"resolution IS NULL OR resolution IN {PRED_RESOLUTIONS}", name="ck_predictive_alerts_resolution"),
        Index("uq_predictive_alerts_one_open", "position_id", "feature", unique=True,
              postgresql_where=text("state <> 'closed'")),
        UniqueConstraint("monitor_id", "baseline_id", "opened_window", name="uq_predictive_alerts_segment_window"),
        Index("ix_predictive_alerts_tenant_state", "tenant_id", "state", "severity"),
    )

    id:                 Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:          Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    monitor_id:         Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("predictive_monitors.id", ondelete="CASCADE"), nullable=False, index=True)
    baseline_id:        Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("condition_baselines.id", ondelete="SET NULL"), nullable=True)
    position_id:        Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("positions.id", ondelete="CASCADE"), nullable=False)
    machine_id:         Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("machines.id", ondelete="CASCADE"), nullable=False)
    feature:            Mapped[str]                = mapped_column(String(16), nullable=False)
    state:              Mapped[str]                = mapped_column(String(16), nullable=False, default="open")
    severity:           Mapped[str]                = mapped_column(String(16), nullable=False)
    opened_at:          Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    opened_window:      Mapped[Optional[int]]      = mapped_column(Integer, nullable=True)
    first_triggers:     Mapped[list]               = mapped_column(JSONB, nullable=False, default=list)
    escalations:        Mapped[dict]               = mapped_column(JSONB, nullable=False, default=dict)
    escalation_windows: Mapped[dict]               = mapped_column(JSONB, nullable=False, default=dict)
    triggers_seen:      Mapped[dict]               = mapped_column(JSONB, nullable=False, default=dict)
    evidence:           Mapped[dict]               = mapped_column(JSONB, nullable=False, default=dict)
    explanation:        Mapped[Optional[str]]      = mapped_column(Text, nullable=True)
    rul:                Mapped[Optional[dict]]     = mapped_column(JSONB, nullable=True)
    last_point:         Mapped[Optional[dict]]     = mapped_column(JSONB, nullable=True)
    acknowledged:       Mapped[bool]               = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    work_order_id:      Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("work_orders.id", ondelete="SET NULL", use_alter=True, name="fk_predictive_alerts_work_order"), nullable=True)
    resolution:         Mapped[Optional[str]]      = mapped_column(String(24), nullable=True)
    closed_at:          Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by:          Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class PredictiveAlertEvent(Base):
    """Trilha do alerta: toda abertura, escalonamento e transição (quem/quando/comentário). `source` diz quem
    produziu o evento: `engine` (a dobra), `user`, `work_order`, `failure`, `plan`."""
    __tablename__ = "predictive_alert_events"
    __table_args__ = (Index("ix_predictive_alert_events_alert_at", "alert_id", "at"),)

    id:            Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:     Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    alert_id:      Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("predictive_alerts.id", ondelete="CASCADE"), nullable=False)
    at:            Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    action:        Mapped[str]                = mapped_column(String(24), nullable=False)
    source:        Mapped[str]                = mapped_column(String(16), nullable=False)
    from_state:    Mapped[Optional[str]]      = mapped_column(String(16), nullable=True)
    to_state:      Mapped[Optional[str]]      = mapped_column(String(16), nullable=True)
    severity:      Mapped[Optional[str]]      = mapped_column(String(16), nullable=True)
    resolution:    Mapped[Optional[str]]      = mapped_column(String(24), nullable=True)
    comment:       Mapped[Optional[str]]      = mapped_column(Text, nullable=True)
    by:            Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    work_order_id: Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("work_orders.id", ondelete="SET NULL"), nullable=True)
    window_i:      Mapped[Optional[int]]      = mapped_column(Integer, nullable=True)
    recorded_at:   Mapped[datetime]           = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class ProductionPlanDay(Base, TimestampMixin):
    """Plano diário de produção de uma máquina (P-7): peças planejadas (uso = peças × cycles_per_part) ou horas."""
    __tablename__ = "production_plan_days"

    machine_id:    Mapped[str]             = mapped_column(UUID(as_uuid=False), ForeignKey("machines.id", ondelete="CASCADE"), primary_key=True)
    day:           Mapped[date]            = mapped_column(Date, primary_key=True)
    tenant_id:     Mapped[str]             = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    planned_parts: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    planned_hours: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
