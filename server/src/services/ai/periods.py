"""IA-6 — period words resolved by the server with the plant calendar (productive day, C17/C19), never by the
model. A productive day runs from its first shift instance start to its last instance end; a day without shifts
uses local midnight to midnight."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from ..oee.calendar import Calendar
from ..oee.service import productive_today
from ..usage import queries as uq

PERIODS = ("today", "yesterday", "this_week", "last_week", "last_7_days", "this_month", "last_month",
           "last_12_months")


class PeriodError(ValueError):
    pass


def _midnight(d: date, cal: Calendar) -> datetime:
    return uq.local_midnight_utc(datetime.combine(d, datetime.min.time(), tzinfo=cal.tz), cal.tz)


def day_start(cal: Calendar, d: date) -> datetime:
    insts = cal.day_instances(d)
    return min((i.start for i in insts), default=_midnight(d, cal))


def day_end(cal: Calendar, d: date) -> datetime:
    insts = cal.day_instances(d)
    return max((i.end for i in insts), default=_midnight(d + timedelta(days=1), cal))


def _month_start(d: date) -> date:
    return d.replace(day=1)


def resolve(period: str, cal: Calendar, now: datetime) -> tuple[datetime, datetime]:
    """(start, stop) in UTC for a period word. Current periods stop at `now`."""
    today = productive_today(cal, now)
    if period == "today":
        return day_start(cal, today), now
    if period == "yesterday":
        y = today - timedelta(days=1)
        return day_start(cal, y), day_end(cal, y)
    if period == "this_week":
        return day_start(cal, today - timedelta(days=today.weekday())), now
    if period == "last_week":
        mon = today - timedelta(days=today.weekday() + 7)
        return day_start(cal, mon), day_end(cal, mon + timedelta(days=6))
    if period == "last_7_days":
        return day_start(cal, today - timedelta(days=6)), now
    if period == "this_month":
        return day_start(cal, _month_start(today)), now
    if period == "last_month":
        last = _month_start(today) - timedelta(days=1)
        return day_start(cal, _month_start(last)), day_end(cal, last)
    if period == "last_12_months":
        try:
            first = today.replace(year=today.year - 1)
        except ValueError:                       # 29/02
            first = today.replace(year=today.year - 1, day=28)
        return _midnight(first, cal), _midnight(today, cal)
    raise PeriodError(f"período desconhecido: {period!r} (use {', '.join(PERIODS)} ou start/stop)")


def today_of(cal: Optional[Calendar], now: datetime) -> date:
    return productive_today(cal, now) if cal is not None else now.date()
