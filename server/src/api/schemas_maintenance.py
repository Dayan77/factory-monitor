"""Pydantic contracts of the maintenance module (Sprint 4). Fractions are 0–1 floats; hours in hours;
timestamps ISO-8601 UTC; dates YYYY-MM-DD (plant local)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

WoType = Literal["preventive", "predictive", "corrective", "improvement"]
WoState = Literal["draft", "open", "approved", "scheduled", "in_progress", "on_hold", "completed", "closed", "cancelled"]
CancelReason = Literal["superseded", "production_conflict", "duplicate", "not_needed", "other"]
RemovalReasonT = Literal["failure", "preventive_replacement", "upgrade", "other"]
Severity = Literal["critical", "degraded", "incipient"]


class TaskItem(BaseModel):
    id: Optional[str] = None
    text: str = Field(min_length=1, max_length=500)


class UsageRef(BaseModel):
    cycles: Optional[float] = None
    hours: Optional[float] = None
    starts: Optional[float] = None


class PlanBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    task_list: list[TaskItem] = []
    estimated_hours: float = Field(0.0, ge=0)
    wo_type: Literal["preventive", "predictive"] = "preventive"
    interval_cycles: Optional[float] = Field(None, gt=0)
    interval_hours: Optional[float] = Field(None, gt=0)
    interval_starts: Optional[float] = Field(None, gt=0)
    interval_days: Optional[int] = Field(None, gt=0)
    tolerance_pct: float = Field(10.0, ge=0, le=100)
    reset_on_replacement: bool = True
    stops_machine: bool = False
    is_active: bool = True


class PlanCreate(PlanBase):
    position_id: Optional[str] = None
    machine_id: Optional[str] = None
    # Plan that already existed on paper: date/usage of the last execution (default: now / current usage)
    last_done_at: Optional[datetime] = None
    last_done_usage: Optional[UsageRef] = None

    @model_validator(mode="after")
    def _check(self):
        if not (self.position_id or self.machine_id):
            raise ValueError("Informe position_id ou machine_id.")
        if not any((self.interval_cycles, self.interval_hours, self.interval_starts, self.interval_days)):
            raise ValueError("Informe pelo menos um gatilho (interval_cycles/hours/starts/days).")
        return self


class PlanUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    task_list: Optional[list[TaskItem]] = None
    estimated_hours: Optional[float] = Field(None, ge=0)
    wo_type: Optional[Literal["preventive", "predictive"]] = None
    interval_cycles: Optional[float] = Field(None, gt=0)
    interval_hours: Optional[float] = Field(None, gt=0)
    interval_starts: Optional[float] = Field(None, gt=0)
    interval_days: Optional[int] = Field(None, gt=0)
    tolerance_pct: Optional[float] = Field(None, ge=0, le=100)
    reset_on_replacement: Optional[bool] = None
    stops_machine: Optional[bool] = None
    is_active: Optional[bool] = None
    last_done_at: Optional[datetime] = None
    last_done_usage: Optional[UsageRef] = None


class PlanOut(BaseModel):
    id: str
    position_id: Optional[str]
    machine_id: Optional[str]
    name: str
    description: Optional[str]
    task_list: list[dict]
    estimated_hours: float
    wo_type: str
    interval_cycles: Optional[float]
    interval_hours: Optional[float]
    interval_starts: Optional[float]
    interval_days: Optional[int]
    tolerance_pct: float
    reset_on_replacement: bool
    stops_machine: bool
    is_active: bool
    last_reset_at: datetime
    last_reset_usage: dict
    last_reset_by: Optional[str]
    state: str
    consumed: Optional[float]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkOrderCreate(BaseModel):
    type: WoType = "corrective"
    urgency: Optional[Literal["emergency", "deferred"]] = None
    state: Literal["draft", "open"] = "open"
    plan_id: Optional[str] = None
    position_id: Optional[str] = None
    machine_id: Optional[str] = None
    downtime_event_id: Optional[str] = None
    failure_id: Optional[str] = None
    parent_id: Optional[str] = None
    title: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    task_list: list[TaskItem] = []
    estimated_hours: float = Field(0.0, ge=0)
    stops_machine: bool = False
    assignee_id: Optional[str] = None
    # Registro retroativo (ex.: lançamento de OS do papel); padrão = agora
    at: Optional[datetime] = None

    @model_validator(mode="after")
    def _check(self):
        if self.type == "corrective" and self.urgency is None:
            self.urgency = "deferred"
        if self.type != "corrective" and self.urgency is not None:
            raise ValueError("urgency só se aplica a OS corretiva.")
        return self


class WorkOrderUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    task_list: Optional[list[TaskItem]] = None
    estimated_hours: Optional[float] = Field(None, ge=0)
    stops_machine: Optional[bool] = None
    scheduled_for: Optional[date] = None
    assignee_id: Optional[str] = None
    position_id: Optional[str] = None
    urgency: Optional[Literal["emergency", "deferred"]] = None


class TransitionIn(BaseModel):
    to: WoState
    note: Optional[str] = None
    scheduled_for: Optional[date] = None
    assignee_id: Optional[str] = None
    hold_reason: Optional[str] = None
    cancel_reason: Optional[str] = None
    labor_hours: Optional[float] = Field(None, ge=0, le=24)
    remaining_hours: Optional[float] = Field(None, ge=0)
    at: Optional[datetime] = None


class LaborIn(BaseModel):
    hours: float = Field(gt=0, le=24)
    note: Optional[str] = None
    remaining_hours: Optional[float] = Field(None, ge=0)
    at: Optional[datetime] = None


class TaskPatch(BaseModel):
    task_id: str
    done: bool
    result: Optional[Literal["ok", "not_ok"]] = None
    note: Optional[str] = None


class NewComponent(BaseModel):
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    component_type: Optional[str] = None
    nominal_life: dict = {}


class ReplacementIn(BaseModel):
    position_id: str
    new_component_id: Optional[str] = None
    new_component: Optional[NewComponent] = None
    removal_reason: RemovalReasonT

    @model_validator(mode="after")
    def _check(self):
        if bool(self.new_component_id) == bool(self.new_component):
            raise ValueError("Informe new_component_id ou new_component (um dos dois).")
        return self


class EmergencyCallIn(BaseModel):
    downtime_event_id: Optional[str] = None
    machine_id: Optional[str] = None
    position_id: Optional[str] = None
    title: Optional[str] = None
    note: Optional[str] = None


class FailureCreate(BaseModel):
    machine_id: str
    position_id: Optional[str] = None
    component_id: Optional[str] = None
    failed_at: datetime
    restored_at: Optional[datetime] = None
    detected_at: Optional[datetime] = None
    repair_started_at: Optional[datetime] = None
    repair_finished_at: Optional[datetime] = None
    severity: Severity = "critical"
    failure_mode: Optional[str] = None
    mechanism: Optional[str] = None
    cause: Optional[str] = None
    detection_method: Optional[str] = None
    downtime_event_id: Optional[str] = None
    work_order_id: Optional[str] = None
    note: Optional[str] = None


class FailurePatch(BaseModel):
    position_id: Optional[str] = None
    component_id: Optional[str] = None
    failed_at: Optional[datetime] = None
    restored_at: Optional[datetime] = None
    detected_at: Optional[datetime] = None
    repair_started_at: Optional[datetime] = None
    repair_finished_at: Optional[datetime] = None
    severity: Optional[Severity] = None
    failure_mode: Optional[str] = None
    mechanism: Optional[str] = None
    failure_mechanism: Optional[str] = None
    cause: Optional[str] = None
    failure_cause: Optional[str] = None
    detection_method: Optional[str] = None
    work_order_id: Optional[str] = None
    note: Optional[str] = None


class SettingsIn(BaseModel):
    schedule_tolerance_days: Optional[int] = Field(None, ge=0, le=30)
    technician_hours_per_week: Optional[float] = Field(None, ge=0, le=168)
    due_soon_at: Optional[float] = Field(None, gt=0, lt=1)
    emergency_call_after_s: Optional[int] = Field(None, ge=0, le=86400)
    default_mttr_min: Optional[float] = Field(None, gt=0)
    digest_hour_local: Optional[int] = Field(None, ge=0, le=23)


class AbsenceIn(BaseModel):
    user_id: str
    date: date
    hours: float = Field(gt=0, le=24)
    note: Optional[str] = None


class PreferencesIn(BaseModel):
    channels: dict[str, dict[str, bool]] = {}
    digest_enabled: Optional[bool] = None


class PushSubscribeIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)
    keys: dict[str, str]

    @field_validator("keys")
    @classmethod
    def _keys(cls, v):
        if not v.get("p256dh") or not v.get("auth"):
            raise ValueError("keys.p256dh e keys.auth são obrigatórios.")
        return v

    @field_validator("endpoint")
    @classmethod
    def _https(cls, v):
        from ..notifications.webpush import endpoint_error
        reason = endpoint_error(v)
        if reason:
            raise ValueError(reason)
        return v


class ApiTokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    scopes: list[Literal["read", "write"]] = ["read"]
    expires_at: Optional[datetime] = None


class WebhookIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    secret: str = Field(min_length=16, max_length=255)
    events: list[str] = Field(min_length=1)
    description: Optional[str] = None
    is_active: bool = True


class WebhookPatch(BaseModel):
    url: Optional[str] = Field(None, min_length=8, max_length=2000)
    secret: Optional[str] = Field(None, min_length=16, max_length=255)
    events: Optional[list[str]] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
