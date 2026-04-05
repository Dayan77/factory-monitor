"""
Pydantic schemas for request/response serialization.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator

from ..models.user import UserRole
from ..models.tenant import TenantPlan
from ..models.machine import MachineStatus
from ..models.tag import ProtocolType, DataType
from ..models.alert import AlertCondition, AlertSeverity


# ── Tenant (superadmin) ───────────────────────────────────────────

class TenantOut(BaseModel):
    id:               str
    name:             str
    slug:             str
    is_active:        bool
    plan:             TenantPlan
    trial_ends_at:    Optional[datetime]
    suspended_reason: Optional[str]
    max_users:        int
    max_machines:     int
    created_at:       datetime
    updated_at:       datetime

    model_config = {"from_attributes": True}


class TenantWithStats(TenantOut):
    user_count:    int = 0
    machine_count: int = 0


class TenantCreate(BaseModel):
    name:          str
    slug:          str
    plan:          TenantPlan = TenantPlan.trial
    trial_ends_at: Optional[datetime] = None
    max_users:     int = 5
    max_machines:  int = 10
    # Usuário admin inicial
    admin_email:   EmailStr
    admin_password: str
    admin_name:    str

    @field_validator("admin_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Senha deve ter no mínimo 8 caracteres.")
        return v

    @field_validator("slug")
    @classmethod
    def slug_format(cls, v: str) -> str:
        import re
        if not re.match(r'^[a-z0-9-]+$', v):
            raise ValueError("Slug deve conter apenas letras minúsculas, números e hífens.")
        return v


class TenantUpdate(BaseModel):
    name:             Optional[str]        = None
    is_active:        Optional[bool]       = None
    plan:             Optional[TenantPlan] = None
    trial_ends_at:    Optional[datetime]   = None
    suspended_reason: Optional[str]        = None
    max_users:        Optional[int]        = None
    max_machines:     Optional[int]        = None


# ── Auth ──────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


# ── User ─────────────────────────────────────────────────────────

class UserOut(BaseModel):
    id: str
    tenant_id: str
    email: str
    name: str
    role: UserRole
    is_active: bool
    last_login_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str
    role: UserRole = UserRole.operator

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Senha deve ter no mínimo 8 caracteres.")
        return v


class UserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None


# ── Line ─────────────────────────────────────────────────────────

class LineOut(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: Optional[str]
    location: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LineCreate(BaseModel):
    name: str
    description: Optional[str] = None
    location: Optional[str] = None


class LineUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    is_active: Optional[bool] = None


# ── Machine ───────────────────────────────────────────────────────

class MachineOut(BaseModel):
    id: str
    tenant_id: str
    line_id: str
    name: str
    model: Optional[str]
    serial_number: Optional[str]
    status: MachineStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MachineCreate(BaseModel):
    line_id: str
    name: str
    model: Optional[str] = None
    serial_number: Optional[str] = None


class MachineUpdate(BaseModel):
    name: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    status: Optional[MachineStatus] = None
    line_id: Optional[str] = None


# ── Tag ───────────────────────────────────────────────────────────

class TagOut(BaseModel):
    id: str
    tenant_id: str
    machine_id: Optional[str]
    name: str
    description: Optional[str]
    protocol: ProtocolType
    address: str
    data_type: DataType
    unit: Optional[str]
    scale_factor: float
    offset: float
    min_value: Optional[float]
    max_value: Optional[float]
    poll_interval_ms: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TagCreate(BaseModel):
    machine_id: Optional[str] = None
    name: str
    description: Optional[str] = None
    protocol: ProtocolType
    address: str
    data_type: DataType = DataType.float
    unit: Optional[str] = None
    scale_factor: float = 1.0
    offset: float = 0.0
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    poll_interval_ms: int = 1000


class TagUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    address: Optional[str] = None
    unit: Optional[str] = None
    scale_factor: Optional[float] = None
    offset: Optional[float] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    poll_interval_ms: Optional[int] = None
    is_active: Optional[bool] = None


# ── Alert Rule ────────────────────────────────────────────────────

class AlertRuleOut(BaseModel):
    id: str
    tenant_id: str
    tag_id: str
    name: str
    condition: AlertCondition
    threshold: Optional[float]
    severity: AlertSeverity
    cooldown_minutes: int
    notify_email: bool
    notify_telegram: bool
    notify_whatsapp: bool
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AlertRuleCreate(BaseModel):
    tag_id: str
    name: str
    condition: AlertCondition
    threshold: Optional[float] = None
    severity: AlertSeverity = AlertSeverity.warning
    cooldown_minutes: int = 5
    notify_email: bool = True
    notify_telegram: bool = False
    notify_whatsapp: bool = False


class AlertEventOut(BaseModel):
    id: str
    rule_id: str
    triggered_at: datetime
    resolved_at: Optional[datetime]
    value_at_trigger: Optional[float]
    notified: bool

    model_config = {"from_attributes": True}


# ── Sensor readings ───────────────────────────────────────────────

class SensorReading(BaseModel):
    tag_id: str
    value: float
    unit: Optional[str] = None
    timestamp: datetime


class SensorHistoryQuery(BaseModel):
    tag_id: str
    start: Optional[datetime] = None   # defaults to -1h server-side
    stop: Optional[datetime] = None    # defaults to now
    window: str = "1m"                 # InfluxDB aggregation window


# ── AI Chat ───────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
