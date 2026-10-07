"""Report periods and scheduling instants (docs/reports.md §0.3, §4.1).

* Period = productive day / shift instance of the plant calendar (C19, S3-8), in the plant time zone; week =
  Monday–Sunday productive days; month = productive days of the civil month; reliability_life = the whole history
  until 00:00 local of the first day of the quarter.
* ready_at = end of the period by the calendar (end of the last shift instance whose productive day belongs to the
  period; without shifts — D6 — 24:00 local of the last day) + settle.
* send_at = ready_at for `shift`; otherwise max(ready_at, D at deliver_at_local) with D = first civil day after the
  last day of the period. A non-existent local time (DST gap) → first valid minute after; ambiguous → first one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from ..oee.calendar import Calendar, ShiftInstance, parse_hhmm

SETTLE_MIN = {"shift": 15}
DEFAULT_SETTLE_MIN = 30


def settle_min(report_type: str) -> int:
    return SETTLE_MIN.get(report_type, DEFAULT_SETTLE_MIN)


def local_instant(d: date, hhmm: str | time, tz: ZoneInfo) -> datetime:
    """Local wall time → UTC. Gap (non-existent) → first valid minute after; fold (repeated) → first occurrence."""
    t = parse_hhmm(hhmm) if isinstance(hhmm, str) else hhmm
    naive = datetime.combine(d, t)
    cand = naive.replace(tzinfo=tz, fold=0)
    if cand.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) == naive:
        return cand.astimezone(timezone.utc)
    # non-existent: walk forward minute by minute to the first wall time that exists
    probe = naive
    for _ in range(24 * 60):
        probe += timedelta(minutes=1)
        c = probe.replace(tzinfo=tz, fold=0)
        if c.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) == probe:
            return c.astimezone(timezone.utc)
    return cand.astimezone(timezone.utc)   # pragma: no cover


def midnight(d: date, tz: ZoneInfo) -> datetime:
    return local_instant(d, time(0), tz)


@dataclass
class Period:
    report_type: str
    key: str                        # idempotency key of (schedule, period)
    first_day: Optional[date]       # productive days [first_day, last_day]
    last_day: Optional[date]
    start: Optional[datetime]       # data window (UTC): first instance start … period end
    end: datetime
    label: str                      # "05/10/2026", "Turno A 05/10/2026", "28/09–04/10/2026", "setembro/2026"
    shift_id: Optional[str] = None
    shift_name: Optional[str] = None
    instances: tuple = ()

    def as_dict(self) -> dict:
        return {"key": self.key, "first_day": self.first_day.isoformat() if self.first_day else None,
                "last_day": self.last_day.isoformat() if self.last_day else None,
                "start": self.start.isoformat() if self.start else None, "end": self.end.isoformat(),
                "label": self.label, "shift_id": self.shift_id, "shift_name": self.shift_name}


MONTHS_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
             "novembro", "dezembro"]


def _days_instances(cal: Calendar, first: date, last: date) -> list[ShiftInstance]:
    out: list[ShiftInstance] = []
    d = first
    while d <= last:
        out += cal.day_instances(d)
        d += timedelta(days=1)
    return out


def calendar_bounds(cal: Calendar, first: date, last: date) -> tuple[datetime, datetime, list[ShiftInstance]]:
    """(start, end, instances) of the productive days [first, last]: end = end of the last shift instance whose
    productive day is in the period; no instance (no shifts, D6) → [00:00 first, 24:00 last) local."""
    insts = _days_instances(cal, first, last)
    if not insts or getattr(cal, "default", False):
        return midnight(first, cal.tz), midnight(last + timedelta(days=1), cal.tz), insts
    return min(i.start for i in insts), max(i.end for i in insts), insts


def day_period(cal: Calendar, day: date, report_type: str = "daily_production") -> Period:
    s, e, insts = calendar_bounds(cal, day, day)
    return Period(report_type, day.isoformat(), day, day, s, e, day.strftime("%d/%m/%Y"), instances=tuple(insts))


def week_period(cal: Calendar, any_day: date, report_type: str = "weekly_oee") -> Period:
    monday = any_day - timedelta(days=any_day.weekday())
    sunday = monday + timedelta(days=6)
    s, e, insts = calendar_bounds(cal, monday, sunday)
    iso = monday.isocalendar()
    return Period(report_type, f"{iso[0]}-W{iso[1]:02d}", monday, sunday, s, e,
                  f"{monday:%d/%m}–{sunday:%d/%m/%Y}", instances=tuple(insts))


def month_period(cal: Calendar, year: int, month: int, report_type: str = "monthly_maintenance") -> Period:
    first = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    s, e, insts = calendar_bounds(cal, first, last)
    return Period(report_type, f"{year}-{month:02d}", first, last, s, e, f"{MONTHS_PT[month - 1]}/{year}",
                  instances=tuple(insts))


def quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def quarter_period(tz: ZoneInfo, qstart: date, report_type: str = "reliability_life") -> Period:
    """History until 00:00 local of the quarter's first day (§4.1)."""
    q = (qstart.month - 1) // 3 + 1
    cut = midnight(qstart, tz)
    prev_last = qstart - timedelta(days=1)
    return Period(report_type, f"{qstart.year}-Q{q}", None, prev_last, None, cut,
                  f"histórico até {qstart:%d/%m/%Y}")


def shift_period(inst: ShiftInstance) -> Period:
    return Period("shift", f"{inst.productive_day.isoformat()}/{inst.shift_id or inst.name}", inst.productive_day,
                  inst.productive_day, inst.start, inst.end, f"{inst.name} {inst.productive_day:%d/%m/%Y}",
                  shift_id=inst.shift_id, shift_name=inst.name, instances=(inst,))


def ready_at(period: Period, report_type: str, settle: Optional[int] = None) -> datetime:
    return period.end + timedelta(minutes=settle_min(report_type) if settle is None else settle)


def send_at(period: Period, report_type: str, tz: ZoneInfo, deliver_at_local: Optional[str],
            settle: Optional[int] = None) -> datetime:
    ready = ready_at(period, report_type, settle)
    if report_type == "shift" or not deliver_at_local:
        return ready
    d = period.last_day + timedelta(days=1)    # first civil day after the period (quarterly: the quarter's 1st day)
    return max(ready, local_instant(d, deliver_at_local, tz))


def schedule_due_day(report_type: str, d: date, weekday: Optional[int], month_day: Optional[int]) -> bool:
    """Is civil day d a delivery day of the schedule? (weekly: weekday; monthly: month_day; quarterly: 1st of
    Jan/Apr/Jul/Oct). Used only to enumerate candidate periods."""
    if report_type == "weekly_oee":
        return d.weekday() == (weekday if weekday is not None else 0)
    if report_type == "monthly_maintenance":
        return d.day == (month_day or 1)
    if report_type == "reliability_life":
        return d.day == 1 and d.month in (1, 4, 7, 10)
    return True


def candidate_periods(report_type: str, cal: Calendar, now: datetime, lookback_days: int = 8,
                      shift_id: Optional[str] = None) -> list[Period]:
    """Periods that may be due around `now` (most recent ones first). The engine keeps those whose send_at is in
    [now − 7 days, now] (§4.2: pending periods of the last 7 days are sent once; older ones are download-only)."""
    tz = cal.tz
    today = now.astimezone(tz).date()
    out: list[Period] = []
    if report_type == "daily_production":
        for k in range(1, lookback_days + 2):
            out.append(day_period(cal, today - timedelta(days=k)))
    elif report_type == "shift":
        for inst in reversed(cal.instances(now - timedelta(days=lookback_days + 1), now)):
            if inst.end <= now and (shift_id is None or inst.shift_id == shift_id):
                out.append(shift_period(inst))
    elif report_type == "weekly_oee":
        monday = today - timedelta(days=today.weekday())
        for k in range(1, 3):
            out.append(week_period(cal, monday - timedelta(days=7 * k)))
    elif report_type == "monthly_maintenance":
        y, m = today.year, today.month
        for _ in range(2):
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
            out.append(month_period(cal, y, m))
    elif report_type == "reliability_life":
        q = quarter_start(today)
        out.append(quarter_period(tz, q))
        prev = quarter_start(q - timedelta(days=1))
        out.append(quarter_period(tz, prev))
    return out


def productive_day_at(cal: Calendar, now: datetime) -> date:
    """Productive day running at `now` (C19/S3-8): a night shift belongs to the day it started."""
    inst = cal.instance_at(now)
    return inst.productive_day if inst else now.astimezone(cal.tz).date()


def _day_param(v, cal: Calendar, now: datetime) -> Optional[date]:
    """productive_day param: ISO date, or "today" = the productive day running now (never the civil date: at
    02:00 in a 22:00–06:00 shift "today" is the day the shift started)."""
    if isinstance(v, str) and v.strip().lower() == "today":
        return productive_day_at(cal, now)
    return _date(v)


def resolve_period(report_type: str, cal: Calendar, params: dict, now: datetime) -> Period:
    """On-demand generation: period parameters → Period (defaults: the last closed period)."""
    tz = cal.tz
    today = now.astimezone(tz).date()
    if report_type == "daily_production":
        d = _day_param(params.get("productive_day"), cal, now) or (today - timedelta(days=1))
        return day_period(cal, d)
    if report_type == "weekly_oee":
        d = _date(params.get("week_start")) or _day_param(params.get("productive_day"), cal, now) or \
            (today - timedelta(days=today.weekday() + 7))
        return week_period(cal, d)
    if report_type == "monthly_maintenance":
        m = params.get("month")
        if m:
            y, mm = (int(x) for x in str(m).split("-")[:2])
        else:
            first = today.replace(day=1) - timedelta(days=1)
            y, mm = first.year, first.month
        return month_period(cal, y, mm)
    if report_type == "reliability_life":
        cut = _date(params.get("cutoff_date"))
        if cut is None:
            # on demand: history until now (today 00:00 local would drop today's data)
            return Period(report_type, f"now-{now:%Y%m%dT%H%M}", None, today, None, now,
                          f"histórico até {now.astimezone(tz):%d/%m/%Y %H:%M}")
        return Period(report_type, f"cut-{cut.isoformat()}", None, cut - timedelta(days=1), None, midnight(cut, tz),
                      f"histórico até {cut:%d/%m/%Y}")
    if report_type == "shift":
        d = _day_param(params.get("productive_day"), cal, now) or productive_day_at(cal, now)
        sid = params.get("shift_id")
        insts = [i for i in cal.day_instances(d) if sid is None or i.shift_id == sid]
        if not insts:
            raise ValueError("no_shift")
        if sid is None:
            done = [i for i in insts if i.end <= now] or insts
            inst = done[-1]
        else:
            inst = insts[0]
        return shift_period(inst)
    raise ValueError(f"unknown report_type {report_type}")


def _date(v) -> Optional[date]:
    if v in (None, ""):
        return None
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])
