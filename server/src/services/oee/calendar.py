"""Calendário de produção (domain.md §4.1): turnos padrão + exceções → instâncias de turno em UTC.

* Horários locais no fuso da planta. `end_local <= start_local` = turno que cruza a meia-noite;
  pertence ao **dia produtivo em que começa** (C19). `weekdays`: 0 = segunda … 6 = domingo,
  avaliado no dia produtivo.
* Pausas (`breaks`) e exceções `planned_stop` são PDOT dentro da instância.
* Exceções: `holiday` remove as instâncias do dia (todas, ou só `shift_id`); `extra_shift` acrescenta
  uma instância `start_local`–`end_local` (com as pausas do `shift_id`, se informado); `planned_stop`
  marca `start_local`–`end_local` (ou o turno inteiro / o dia inteiro sem horário) como PDOT.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from .intervals import Interval, clip, normalize

HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
DEFAULT_BREAK_REASON = "PLN-01"
DEFAULT_PLANNED_STOP_REASON = "PLN-04"


def parse_hhmm(s: str) -> time:
    m = HHMM.match(s or "")
    if not m:
        raise ValueError(f"horário inválido (HH:MM): {s!r}")
    return time(int(m.group(1)), int(m.group(2)))


def _minutes(s: str) -> int:
    t = parse_hhmm(s)
    return t.hour * 60 + t.minute


def span_minutes(start_local: str, end_local: str) -> int:
    """Duração em minutos; fim ≤ início cruza a meia-noite (24 h quando iguais)."""
    d = (_minutes(end_local) - _minutes(start_local)) % (24 * 60)
    return d or 24 * 60


@dataclass(frozen=True)
class ShiftDef:
    id: Optional[str]
    name: str
    start_local: str
    end_local: str
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    breaks: tuple[dict, ...] = ()
    valid_from: Optional[date] = None      # version in effect for productive days [valid_from, valid_to]
    valid_to: Optional[date] = None

    def in_effect(self, day: date) -> bool:
        return (self.valid_from is None or day >= self.valid_from) and (self.valid_to is None or day <= self.valid_to)


@dataclass(frozen=True)
class ExceptionDef:
    date: date
    kind: str                         # holiday | extra_shift | planned_stop
    shift_id: Optional[str] = None
    start_local: Optional[str] = None
    end_local: Optional[str] = None
    reason_code: Optional[str] = None
    note: Optional[str] = None


@dataclass(frozen=True)
class Break:
    start: datetime
    end: datetime
    reason_code: str


@dataclass
class ShiftInstance:
    shift_id: Optional[str]
    name: str
    productive_day: date
    start: datetime
    end: datetime
    breaks: list[Break] = field(default_factory=list)
    extra: bool = False

    @property
    def interval(self) -> Interval:
        return (self.start, self.end)

    @property
    def slot(self) -> str:
        return self.shift_id or ""

    def break_intervals(self) -> list[Interval]:
        return normalize((b.start, b.end) for b in self.breaks)


def _local(day: date, hhmm: str, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, parse_hhmm(hhmm), tzinfo=tz).astimezone(timezone.utc)


def _window(day: date, start_local: str, end_local: str, tz: ZoneInfo) -> Interval:
    start = _local(day, start_local, tz)
    end_day = day + timedelta(days=1) if _minutes(end_local) <= _minutes(start_local) else day
    return start, _local(end_day, end_local, tz)


def _place(inst_start: datetime, inst_end: datetime, day: date, start_local: str, end_local: str,
           tz: ZoneInfo) -> Optional[Interval]:
    """An HH:MM–HH:MM span placed inside the instance (it may fall on the next calendar day)."""
    dur = timedelta(minutes=span_minutes(start_local, end_local))
    for d in (day, day + timedelta(days=1)):
        s = _local(d, start_local, tz)
        if inst_start <= s < inst_end:
            return s, min(s + dur, inst_end)
    return None


class Calendar:
    def __init__(self, tz: ZoneInfo, shifts: Iterable[ShiftDef], exceptions: Iterable[ExceptionDef] = (),
                 default: bool = False):
        self.tz = tz
        self.default = default          # implicit 24 h calendar (plant without shifts)
        self.shifts = list(shifts)
        self.by_id = {s.id: s for s in self.shifts if s.id}
        self.exceptions: dict[date, list[ExceptionDef]] = {}
        for e in exceptions:
            self.exceptions.setdefault(e.date, []).append(e)

    @property
    def empty(self) -> bool:
        return not self.shifts and not any(
            e.kind == "extra_shift" for es in self.exceptions.values() for e in es)

    def _breaks(self, sdef: Optional[ShiftDef], day: date, start: datetime, end: datetime) -> list[Break]:
        out = []
        for b in (sdef.breaks if sdef else ()):
            try:
                iv = _place(start, end, day, b["start_local"], b["end_local"], self.tz)
            except (KeyError, ValueError):
                continue
            if iv:
                out.append(Break(iv[0], iv[1], b.get("reason_code") or DEFAULT_BREAK_REASON))
        return out

    def day_instances(self, day: date) -> list[ShiftInstance]:
        exc = self.exceptions.get(day, [])
        holiday_all = any(e.kind == "holiday" and not e.shift_id for e in exc)
        holiday_shifts = {e.shift_id for e in exc if e.kind == "holiday" and e.shift_id}
        out: list[ShiftInstance] = []
        if not holiday_all:
            for s in self.shifts:
                if day.weekday() not in s.weekdays or (s.id and s.id in holiday_shifts) or not s.in_effect(day):
                    continue
                start, end = _window(day, s.start_local, s.end_local, self.tz)
                out.append(ShiftInstance(s.id, s.name, day, start, end, self._breaks(s, day, start, end)))
        for e in exc:
            if e.kind != "extra_shift" or not e.start_local or not e.end_local:
                continue
            sdef = self.by_id.get(e.shift_id) if e.shift_id else None
            start, end = _window(day, e.start_local, e.end_local, self.tz)
            out.append(ShiftInstance(e.shift_id, (sdef.name if sdef else "Turno extra"), day, start, end,
                                     self._breaks(sdef, day, start, end), extra=True))
        for e in exc:
            if e.kind != "planned_stop":
                continue
            reason = e.reason_code or DEFAULT_PLANNED_STOP_REASON
            for inst in out:
                if e.shift_id and inst.shift_id != e.shift_id:
                    continue
                if e.start_local and e.end_local:
                    iv = _place(inst.start, inst.end, day, e.start_local, e.end_local, self.tz)
                    if iv is None:
                        continue
                else:
                    iv = inst.interval
                inst.breaks.append(Break(iv[0], iv[1], reason))
        out.sort(key=lambda i: i.start)
        return out

    def instances(self, start: datetime, end: datetime) -> list[ShiftInstance]:
        """Instances overlapping [start, end), sorted by start (not clipped)."""
        if end <= start:
            return []
        d0 = start.astimezone(self.tz).date() - timedelta(days=1)
        d1 = end.astimezone(self.tz).date()
        out: list[ShiftInstance] = []
        d = d0
        while d <= d1:
            out += [i for i in self.day_instances(d) if i.end > start and i.start < end]
            d += timedelta(days=1)
        out.sort(key=lambda i: i.start)
        return out

    def instance_at(self, ts: datetime) -> Optional[ShiftInstance]:
        for i in self.instances(ts - timedelta(days=2), ts + timedelta(seconds=1)):
            if i.start <= ts < i.end:
                return i
        return None

    def pot(self, start: datetime, end: datetime) -> list[Interval]:
        return clip(normalize(i.interval for i in self.instances(start, end)), start, end)

    def breaks(self, start: datetime, end: datetime) -> list[Interval]:
        return clip(normalize(iv for i in self.instances(start, end) for iv in i.break_intervals()), start, end)


def productive_day_of(cal: Calendar, ts: datetime) -> date:
    """Dia produtivo: o da instância de turno que contém ts; fora de turno, o dia local (C19)."""
    inst = cal.instance_at(ts)
    return inst.productive_day if inst else ts.astimezone(cal.tz).date()


def shifts_overlap(a: ShiftDef, b: ShiftDef, tz: ZoneInfo, ref: date = date(2026, 1, 5)) -> bool:
    """True if the two shift definitions overlap on some day of a reference fortnight."""
    from dataclasses import replace
    cal = Calendar(tz, [replace(a, id="__a__"), replace(b, id="__b__")])
    insts: list[ShiftInstance] = []
    for k in range(15):
        insts += cal.day_instances(ref + timedelta(days=k))
    ia = [i for i in insts if i.shift_id == "__a__"]
    ib = [i for i in insts if i.shift_id == "__b__"]
    return any(x.start < y.end and y.start < x.end for x in ia for y in ib)
