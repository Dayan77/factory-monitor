"""Drill-down period context (docs/sprint6.md §2.2 rule 1): `?period=current_shift|today|yesterday|last_7d|custom`
(+ `start`/`stop` for custom) resolves to the same window at every level (plant → line → machine → position).

"today"/"yesterday" are productive days (C19, S3-8): from the start of the first shift instance of the day to the
end of the last one — never 00:00–00:00 (a day without any shift falls back to the local calendar day).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import HTTPException

from .oee.calendar import Calendar
from .oee.service import productive_today
from .usage import queries as uq

PERIODS = ("current_shift", "today", "yesterday", "last_7d", "custom")


def _day_bounds(cal: Calendar, day: date) -> tuple[datetime, datetime]:
    insts = cal.day_instances(day)
    if insts:
        return min(i.start for i in insts), max(i.end for i in insts)
    start = uq.local_midnight_utc(datetime.combine(day, datetime.min.time(), tzinfo=cal.tz), cal.tz)
    return start, start + timedelta(days=1)


def resolve_period(cal: Calendar, period: Optional[str], start: Optional[datetime], stop: Optional[datetime],
                   now: datetime) -> tuple[Optional[datetime], Optional[datetime], Optional[dict]]:
    """(start, stop, info) for the period; period None → (start, stop, None) unchanged."""
    if period is None:
        return start, stop, None
    if period not in PERIODS:
        raise HTTPException(status_code=422, detail=f"period inválido: use {' | '.join(PERIODS)}")
    if period == "custom":
        if start is None or stop is None:
            raise HTTPException(status_code=422, detail="period=custom exige start e stop.")
        return start, stop, {"period": period}
    today = productive_today(cal, now)
    if period == "current_shift":
        inst = cal.instance_at(now)
        if inst is None:
            raise HTTPException(status_code=422, detail={"message": "Fora do turno: nenhum turno agora.",
                                                          "reason": "no_shift"})
        return inst.start, inst.end, {"period": period, "shift_name": inst.name,
                                      "productive_day": inst.productive_day.isoformat()}
    if period == "today":
        s, e = _day_bounds(cal, today)
        return s, e, {"period": period, "productive_day": today.isoformat()}
    if period == "yesterday":
        d = today - timedelta(days=1)
        s, e = _day_bounds(cal, d)
        return s, e, {"period": period, "productive_day": d.isoformat()}
    first = today - timedelta(days=6)
    s, _ = _day_bounds(cal, first)
    _, e = _day_bounds(cal, today)
    return s, e, {"period": period, "first_day": first.isoformat(), "last_day": today.isoformat()}
