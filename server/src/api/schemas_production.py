"""Pydantic contracts of Fase 2 (produção / OEE). Ratios are fractions 0–1, null when undefined."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from ..services.oee.calendar import parse_hhmm, span_minutes

REASON_CODE_PATTERN = r"^[A-Z]{3}-\d{2}(-[A-Z0-9]{1,6})?$"


def _hhmm(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    parse_hhmm(v)
    return v


# ── shifts ────────────────────────────────────────────────────────────────

class BreakIn(BaseModel):
    start_local: str
    end_local: str
    reason_code: str = "PLN-01"

    @field_validator("start_local", "end_local")
    @classmethod
    def _hhmm_fields(cls, v):
        return _hhmm(v)


class ShiftBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    start_local: str
    end_local: str
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4],
                                description="0 = segunda … 6 = domingo (dia produtivo do início)")
    breaks: list[BreakIn] = Field(default_factory=list)
    is_active: bool = True

    @field_validator("start_local", "end_local")
    @classmethod
    def _hhmm_fields(cls, v):
        return _hhmm(v)

    @field_validator("weekdays")
    @classmethod
    def _wd(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("weekdays: valores de 0 (segunda) a 6 (domingo)")
        return sorted(set(v))

    @model_validator(mode="after")
    def _breaks_inside(self):
        _check_breaks(self.start_local, self.end_local, self.breaks)
        return self


def _check_breaks(start_local: str, end_local: str, breaks: list[BreakIn]) -> None:
    def off(hhmm: str) -> int:
        return span_minutes(start_local, hhmm) % (24 * 60) if hhmm != start_local else 0
    length = span_minutes(start_local, end_local)
    for b in breaks:
        s = off(b.start_local)
        d = span_minutes(b.start_local, b.end_local)
        if s + d > length:
            raise ValueError(f"pausa {b.start_local}–{b.end_local} fora do turno {start_local}–{end_local}")


class ShiftCreate(ShiftBase):
    plant_id: str


class ShiftUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    start_local: Optional[str] = None
    end_local: Optional[str] = None
    weekdays: Optional[list[int]] = None
    breaks: Optional[list[BreakIn]] = None
    is_active: Optional[bool] = None
    # Changing hours/days/breaks or deactivating a shift that already ran closes the current version
    # the day before this productive day (default: today in the plant) and opens a new one — the
    # past keeps the schedule it had (kpis.md D11).
    effective_from: Optional[date] = None

    @field_validator("start_local", "end_local")
    @classmethod
    def _hhmm_fields(cls, v):
        return _hhmm(v)

    @field_validator("weekdays")
    @classmethod
    def _wd(cls, v):
        if v is None:
            return v
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("weekdays: valores de 0 (segunda) a 6 (domingo)")
        return sorted(set(v))


class ShiftOut(BaseModel):
    id: str
    tenant_id: str
    plant_id: str
    name: str
    start_local: str
    end_local: str
    weekdays: list[int]
    breaks: list[dict]
    is_active: bool
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── calendar exceptions ───────────────────────────────────────────────────

class CalendarExceptionBase(BaseModel):
    date: date
    kind: Literal["holiday", "extra_shift", "planned_stop"]
    shift_id: Optional[str] = None
    start_local: Optional[str] = None
    end_local: Optional[str] = None
    reason_code: Optional[str] = Field(default=None, pattern=REASON_CODE_PATTERN)
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("start_local", "end_local")
    @classmethod
    def _hhmm_fields(cls, v):
        return _hhmm(v)

    @model_validator(mode="after")
    def _times(self):
        if (self.start_local is None) != (self.end_local is None):
            raise ValueError("informe start_local e end_local juntos")
        if self.kind == "extra_shift" and self.start_local is None:
            raise ValueError("turno extra exige start_local e end_local")
        return self


class CalendarExceptionCreate(CalendarExceptionBase):
    plant_id: str


class CalendarExceptionUpdate(BaseModel):
    date: Optional[date] = None
    kind: Optional[Literal["holiday", "extra_shift", "planned_stop"]] = None
    shift_id: Optional[str] = None
    start_local: Optional[str] = None
    end_local: Optional[str] = None
    reason_code: Optional[str] = Field(default=None, pattern=REASON_CODE_PATTERN)
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("start_local", "end_local")
    @classmethod
    def _hhmm_fields(cls, v):
        return _hhmm(v)


class CalendarExceptionOut(BaseModel):
    id: str
    tenant_id: str
    plant_id: str
    date: date
    kind: str
    shift_id: Optional[str]
    start_local: Optional[str]
    end_local: Optional[str]
    reason_code: Optional[str]
    note: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── products / PRI / runs ──────────────────────────────────────────────────

class ProductCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)

    @field_validator("code", "name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("não pode ser vazio")
        return v


class ProductUpdate(BaseModel):
    code: Optional[str] = Field(default=None, min_length=1, max_length=64)
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)


class ProductOut(BaseModel):
    id: str
    tenant_id: str
    code: str
    name: str
    created_at: datetime

    model_config = {"from_attributes": True}


class MachineProductIn(BaseModel):
    product_id: str
    ideal_cycle_time_s: float = Field(gt=0)
    parts_per_cycle: int = Field(default=1, ge=1)
    is_default: bool = False


class MachineProductUpdate(BaseModel):
    ideal_cycle_time_s: Optional[float] = Field(default=None, gt=0)
    parts_per_cycle: Optional[int] = Field(default=None, ge=1)
    is_default: Optional[bool] = None


class MachineProductOut(BaseModel):
    id: str
    machine_id: str
    product_id: str
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    ideal_cycle_time_s: float
    parts_per_cycle: int
    is_default: bool
    ideal_time_per_part_s: float = 0.0

    model_config = {"from_attributes": True}


class ProductionRunCreate(BaseModel):
    machine_id: str
    product_id: str
    started_at: Optional[datetime] = None      # default: now; closes the machine's open run
    ended_at: Optional[datetime] = None


class ProductionRunStop(BaseModel):
    ended_at: Optional[datetime] = None


class ProductionRunOut(BaseModel):
    id: str
    machine_id: str
    product_id: str
    product_code: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime]

    model_config = {"from_attributes": True}


# ── reason codes ──────────────────────────────────────────────────────────

class ReasonCodeCreate(BaseModel):
    code: str = Field(pattern=REASON_CODE_PATTERN)
    category: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=255)
    planned: bool = False
    oee_loss: Literal["none", "availability", "performance", "quality"] = "availability"


class ReasonCodeUpdate(BaseModel):
    category: Optional[str] = Field(default=None, min_length=1, max_length=64)
    label: Optional[str] = Field(default=None, min_length=1, max_length=255)
    planned: Optional[bool] = None
    oee_loss: Optional[Literal["none", "availability", "performance", "quality"]] = None
    is_active: Optional[bool] = None


class ReasonCodeOut(BaseModel):
    id: str
    tenant_id: Optional[str]
    code: str
    category: str
    label: str
    planned: bool
    oee_loss: str
    is_active: bool
    is_default: bool = False

    model_config = {"from_attributes": True}


# ── downtime ──────────────────────────────────────────────────────────────

class DowntimeOut(BaseModel):
    id: str
    machine_id: str
    machine_name: Optional[str] = None
    line_id: Optional[str] = None
    start: datetime
    end: Optional[datetime]
    duration_s: Optional[float]
    stop_start: Optional[datetime] = None
    source: str
    reason_code: Optional[str]
    suggested_reason_code: Optional[str]
    effective_reason_code: str
    planned: bool
    fault_code: Optional[int]
    fault_since: Optional[datetime] = None
    failure_id: Optional[str]
    comment: Optional[str]
    shift_id: Optional[str]
    productive_day: Optional[date]
    justified_by: Optional[str] = None
    justified_at: Optional[datetime] = None
    unjustified: bool = False


class DowntimePatch(BaseModel):
    reason_code: Optional[str] = Field(default=None, pattern=REASON_CODE_PATTERN)
    comment: Optional[str] = Field(default=None, max_length=2000)


class DowntimeCreate(BaseModel):
    machine_id: str
    start: datetime
    end: Optional[datetime] = None
    reason_code: str = Field(pattern=REASON_CODE_PATTERN)
    comment: Optional[str] = Field(default=None, max_length=2000)


# ── OEE / Pareto / reliability ────────────────────────────────────────────

class OeePoint(BaseModel):
    t: datetime
    end: datetime
    label: str
    availability: Optional[float]
    performance: Optional[float]
    quality: Optional[float]
    oee: Optional[float]
    teep: Optional[float] = None
    pbt_s: float = 0.0
    good: float = 0.0
    scrap: float = 0.0
    instances: Optional[int] = None          # group_by=shift_name: shift instances summed in the point


class ProducedOutside(BaseModel):
    total: float = 0.0
    good: float = 0.0
    scrap: float = 0.0


class OeeOut(BaseModel):
    scope: str
    id: str
    period_start: datetime
    period_end: datetime
    granularity: str
    availability: Optional[float]
    performance: Optional[float]
    quality: Optional[float]
    oee: Optional[float]
    teep: Optional[float]
    loading: Optional[float] = None
    reason: Optional[str] = None
    ideal_cycle_time_suspect: bool = False
    pot_s: float
    pdot_s: float
    pbt_s: float
    adot_s: float
    apt_s: float
    nodata_s: float = 0.0
    micro_stop_count: int
    micro_stop_loss_s: float
    micro_stops_per_apt_hour: Optional[float] = None
    speed_loss_s: float
    quality_loss_s: float = 0.0
    ideal_produced_s: float = 0.0
    net_productive_s: float = 0.0
    produced: float = 0.0
    good: float
    scrap: float
    cadence_planned_per_hour: Optional[float] = None
    cadence_running_per_hour: Optional[float] = None
    availability_operational: Optional[float] = None
    data_coverage: Optional[float]
    product_unknown: bool
    # Sprint 7 (audit A-13): line PRI of some product taken from the output machine (none at the bottleneck)
    line_pri_fallback: bool = False
    # S3-7: parts counted outside every shift instance (not in P/Q/OEE/TEEP)
    produced_outside_schedule: ProducedOutside = Field(default_factory=lambda: ProducedOutside())
    # D7: some hourly count bucket was only partly inside the window/schedule (counted pro rata)
    counts_prorated: bool = False
    calendar_default: bool = False
    points: list[OeePoint] = Field(default_factory=list)
    # Sprint 5 — compare_to=previous_period: the previous period and the server-computed differences
    previous: Optional[dict] = None
    delta: Optional[dict] = None
    # Sprint 6 — drill-down ?period=…: the resolved period (productive day / shift)
    period: Optional[dict] = None


class ParetoRow(BaseModel):
    reason_code: Optional[str]
    category: str
    label: str
    planned: bool
    duration_s: float
    events: int
    pct: Optional[float]
    cumulative_pct: Optional[float]


class ReliabilityOut(BaseModel):
    scope: str
    id: str
    period_start: datetime
    period_end: datetime
    failures: int
    incipient: int = 0
    short_faults: int
    operating_h: float
    mtbf_h: Optional[float]
    mttr_h: Optional[float]
    mttr_s: Optional[float] = None
    availability_inherent: Optional[float]
    failure_rate_per_h: Optional[float] = None
    reason: Optional[str] = None
    # Sprint 7: kpis S5-1c item 6 (audit A-6) and MART, §2.3 (audit A-20)
    failures_outside_production: int = 0
    mart_s: Optional[float] = None
    mart_reason: Optional[str] = None
    repairs_timed: int = 0


class MachineProduction(BaseModel):
    machine_id: str
    machine_name: str
    good: float
    scrap: float
    total: float
    availability: Optional[float] = None
    performance: Optional[float] = None
    quality: Optional[float] = None
    oee: Optional[float] = None


class ShiftReportOut(BaseModel):
    line_id: str
    productive_day: date
    shift_id: Optional[str]
    shift_name: Optional[str]
    period_start: Optional[datetime]
    period_end: Optional[datetime]
    oee: Optional[OeeOut]
    pareto: list[ParetoRow]
    downtime: list[DowntimeOut]
    production: list[MachineProduction]
    reason: Optional[str] = None


class OverviewLine(BaseModel):
    line_id: str
    line_name: str
    output_machine_id: Optional[str]
    oee_today: Optional[float]
    oee_current_shift: Optional[float]
    current_shift_name: Optional[str] = None
    availability: Optional[float]
    performance: Optional[float]
    quality: Optional[float]
    good: float
    scrap: float
    cadence_per_hour: Optional[float]
    data_coverage: Optional[float] = None
    open_downtime_count: int
    unjustified_count: int
    worst_machine: Optional[dict] = None


class OverviewOut(BaseModel):
    plant_id: str
    plant_name: str
    productive_day: date
    generated_at: datetime
    lines: list[OverviewLine]
    top_losses: list[ParetoRow]
    machines_in_error: list[dict]
    components_near_end: list[dict]
    # Sprint 5: field gateways of the plant with their effective status (online | degraded | offline | pending)
    gateways: list[dict] = Field(default_factory=list)
