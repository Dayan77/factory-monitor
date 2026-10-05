"""
Schemas for the asset hierarchy (Plant > Line > Machine > Position > Component), usage,
production and public leads (Sprint 2 / Fase 1). Conventions (docs/domain.md):
fractions (life_consumed_pct) are 0..1, unrounded; times in hours in the API; UTC timestamps.
"""

from datetime import date, datetime
from typing import Annotated, Literal, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field, field_validator

from ..models.asset import ComponentType, RemovalReason
from ..models.machine import MachineStatus
from ..models.plant import DEFAULT_TIMEZONE


# Names used in docs/domain.md §2 accepted as input aliases of the stored enum.
COMPONENT_TYPE_ALIASES = {"servo_motor": "servo", "contactor": "other"}


def _component_type_alias(v):
    return COMPONENT_TYPE_ALIASES.get(v, v) if isinstance(v, str) else v


ComponentTypeIn = Annotated[ComponentType, BeforeValidator(_component_type_alias)]


def _valid_tz(v: str) -> str:
    try:
        ZoneInfo(v)
    except Exception:  # noqa: BLE001
        raise ValueError("timezone deve ser um fuso IANA, ex. America/Sao_Paulo.")
    return v


# ── Plant ─────────────────────────────────────────────────────────

class PlantOut(BaseModel):
    id: str
    tenant_id: str
    name: str
    timezone: str
    address: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PlantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    timezone: str = DEFAULT_TIMEZONE
    address: Optional[str] = Field(default=None, max_length=500)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        return _valid_tz(v)


class PlantUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    timezone: Optional[str] = None
    address: Optional[str] = Field(default=None, max_length=500)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        return None if v is None else _valid_tz(v)


class StatusSummary(BaseModel):
    running: int = 0
    stopped: int = 0
    error: int = 0
    unknown: int = 0
    maintenance: int = 0


class TreeMachine(BaseModel):
    id: str
    name: str
    status: MachineStatus
    last_seen_at: Optional[datetime]
    positions_count: int


class TreeLine(BaseModel):
    id: str
    name: str
    output_machine_id: Optional[str] = None
    bottleneck_machine_id: Optional[str] = None
    status_summary: StatusSummary
    machines: list[TreeMachine]


class TreePlant(BaseModel):
    id: str
    name: str
    timezone: str
    lines: list[TreeLine]


# ── Component ─────────────────────────────────────────────────────

class NominalLife(BaseModel):
    """Vida nominal por medida (datasheet). Medida ausente → % de vida null para ela."""
    model_config = ConfigDict(extra="forbid")
    cycles: Optional[int] = Field(default=None, gt=0)
    hours: Optional[float] = Field(default=None, gt=0)
    starts: Optional[int] = Field(default=None, gt=0)


class UsageValues(BaseModel):
    cycles: float = 0
    hours: float = 0
    starts: float = 0


class ComponentOut(BaseModel):
    id: str
    tenant_id: str
    position_id: Optional[str]
    last_position_id: Optional[str] = None
    component_type: ComponentType
    manufacturer: Optional[str]
    model: Optional[str]
    serial_number: Optional[str]
    installed_at: Optional[datetime]
    removed_at: Optional[datetime]
    removal_reason: Optional[RemovalReason]
    nominal_life: NominalLife
    # derived: installed | in_stock | removed
    state: Literal["installed", "in_stock", "removed"] = "in_stock"
    usage_since_install: UsageValues = UsageValues()
    life_consumed_pct: Optional[float] = None
    life_measure: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ComponentCreate(BaseModel):
    component_type: ComponentTypeIn
    manufacturer: Optional[str] = Field(default=None, max_length=255)
    model: Optional[str] = Field(default=None, max_length=255)
    serial_number: Optional[str] = Field(default=None, max_length=255)
    nominal_life: NominalLife = NominalLife()
    position_id: Optional[str] = None       # install right away
    installed_at: Optional[datetime] = None  # default: now (only with position_id)


class ComponentUpdate(BaseModel):
    component_type: Optional[ComponentTypeIn] = None
    manufacturer: Optional[str] = Field(default=None, max_length=255)
    model: Optional[str] = Field(default=None, max_length=255)
    serial_number: Optional[str] = Field(default=None, max_length=255)
    nominal_life: Optional[NominalLife] = None


class InstallRequest(BaseModel):
    component_id: str
    installed_at: Optional[datetime] = None


class RemoveRequest(BaseModel):
    reason: RemovalReason
    removed_at: Optional[datetime] = None


# ── Position ──────────────────────────────────────────────────────

class PositionOut(BaseModel):
    id: str
    tenant_id: str
    machine_id: str
    name: str
    component_type: ComponentType
    cycles_per_part: int
    counter_tag_id: Optional[str]
    installed_component_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PositionCreate(BaseModel):
    machine_id: str
    name: str = Field(min_length=1, max_length=255)
    component_type: ComponentTypeIn
    cycles_per_part: int = Field(default=1, ge=1)
    counter_tag_id: Optional[str] = None


class PositionUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    component_type: Optional[ComponentTypeIn] = None
    cycles_per_part: Optional[int] = Field(default=None, ge=1)
    counter_tag_id: Optional[str] = None     # explicit null unlinks


class PositionUsage(BaseModel):
    """usage_total da posição (nunca zera). cycles_estimated: sem tag de ciclo própria, ciclos
    = peças da máquina × cycles_per_part (domain.md §2)."""
    cycles_total: float = 0
    hours_total: float = 0
    starts_total: float = 0
    cycles_estimated: bool = False


class ComponentUsage(BaseModel):
    """usage_since_install do componente instalado + vida (kpis.md §4.1–4.2)."""
    cycles: float = 0
    hours: float = 0
    starts: float = 0
    life_consumed_pct: Optional[float] = None   # fraction 0..1 (not %), max over measures; null = no nominal life
    life_measure: Optional[str] = None
    life_reason: Optional[str] = None
    projected_end_date: Optional[date] = None
    rul_days: Optional[int] = None
    rul_measure: Optional[str] = None
    rul_reason: Optional[str] = None            # no_nominal_life | no_recent_usage | insufficient_data
    rate_per_day: Optional[float] = None
    low_confidence: bool = False
    overdue: bool = False


class PositionDetail(PositionOut):
    installed_component: Optional[ComponentOut] = None
    usage: PositionUsage
    component_usage: Optional[ComponentUsage] = None


# ── Usage / production ────────────────────────────────────────────

class MachineUsagePosition(BaseModel):
    position_id: str
    name: str
    component_type: ComponentType
    cycles_total: float
    hours_total: float
    starts_total: float
    cycles_estimated: bool = False
    component_id: Optional[str] = None
    life_consumed_pct: Optional[float] = None
    life_measure: Optional[str] = None


class ProductionCounts(BaseModel):
    good: float = 0
    scrap: float = 0
    total: float = 0
    start: Optional[datetime] = None     # start of the productive day (plant local midnight, UTC)
    timezone: Optional[str] = None


class MachineUsageOut(BaseModel):
    machine_id: str
    positions: list[MachineUsagePosition]
    production: ProductionCounts
    hours_total: float = 0               # machine-level run_state, when it has one
    starts_total: float = 0


class UsagePoint(BaseModel):
    t: datetime
    value: float


class UsageHistoryOut(BaseModel):
    measure: str
    bucket: str
    start: datetime
    stop: datetime
    points: list[UsagePoint]


class ProductionPoint(BaseModel):
    t: datetime
    good: float
    scrap: float


class LineProductionOut(BaseModel):
    line_id: str
    output_machine_id: Optional[str]
    start: datetime
    stop: datetime
    good: float = 0
    scrap: float = 0
    total: float = 0
    # total / hours of the window (no shift calendar yet — becomes PQ/PBT in Fase 2)
    cadence_per_hour: Optional[float] = None
    # total / operating hours of the output machine (PQ/APT), when it has a run_state tag
    cadence_running_per_hour: Optional[float] = None
    reason: Optional[str] = None         # no_output_machine
    points: list[ProductionPoint] = []


class RecomputeOut(BaseModel):
    readings: int
    applied: bool
    differences: list[dict]


class RecomputeJobOut(BaseModel):
    job_id: str
    status: Literal["running", "done", "failed"]
    apply: bool
    started_at: datetime
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    result: Optional[RecomputeOut] = None


# ── Public leads ──────────────────────────────────────────────────

class LeadCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    company: Optional[str] = Field(default=None, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=64)
    message: Optional[str] = Field(default=None, max_length=5000)
    consent: bool
    website: Optional[str] = Field(default="", max_length=500)   # honeypot: must stay empty
    source: Optional[str] = Field(default=None, max_length=255)   # page/campaign

    @field_validator("consent")
    @classmethod
    def _consent(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("É necessário consentir com o tratamento dos dados (LGPD).")
        return v
