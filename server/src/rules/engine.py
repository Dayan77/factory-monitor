"""
Rules Engine — evaluates AlertRules against incoming sensor readings.
Triggers AlertEvents and dispatches notifications.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AlertRule, AlertEvent, AlertCondition, Tag
from ..services.influx import influx
from ..notifications.dispatcher import dispatch

log = structlog.get_logger()

_OPERATORS = {
    AlertCondition.gt:  lambda v, t: v >  t,
    AlertCondition.gte: lambda v, t: v >= t,
    AlertCondition.lt:  lambda v, t: v <  t,
    AlertCondition.lte: lambda v, t: v <= t,
    AlertCondition.eq:  lambda v, t: v == t,
    AlertCondition.neq: lambda v, t: v != t,
}

# in-memory cache: rule_id → last triggered timestamp (fast path; the DB is the
# source of truth, so cooldown survives restarts and multiple workers)
_last_triggered: dict[str, datetime] = {}

# last value seen per (tenant_id, tag_id) — used by the 'change' condition
_last_value: dict[tuple[str, str], float] = {}


async def _last_trigger_time(db: AsyncSession, rule_id: str) -> Optional[datetime]:
    cached = _last_triggered.get(rule_id)
    if cached is not None:
        return cached
    result = await db.execute(
        select(func.max(AlertEvent.triggered_at)).where(AlertEvent.rule_id == rule_id)
    )
    last = result.scalar_one_or_none()
    if last is not None:
        _last_triggered[rule_id] = last
    return last


async def evaluate(
    db: AsyncSession,
    tag_id: str,
    tenant_id: str,
    value: float,
) -> None:
    """Called for each incoming sensor reading."""
    key = (tenant_id, tag_id)
    previous = _last_value.get(key)
    _last_value[key] = value

    result = await db.execute(
        select(AlertRule)
        .where(
            AlertRule.tag_id    == tag_id,
            AlertRule.tenant_id == tenant_id,
            AlertRule.is_active.is_(True),
        )
    )
    rules = result.scalars().all()

    for rule in rules:
        triggered = _check_condition(rule, value, previous)
        if not triggered:
            continue

        # Respect cooldown
        now = datetime.now(timezone.utc)
        last = await _last_trigger_time(db, rule.id)
        cooldown = timedelta(minutes=rule.cooldown_minutes)
        if last and (now - last) < cooldown:
            continue

        _last_triggered[rule.id] = now

        event = AlertEvent(
            tenant_id=tenant_id,
            rule_id=rule.id,
            triggered_at=now,
            value_at_trigger=value,
        )
        db.add(event)
        await db.commit()
        await db.refresh(event)

        log.warning("alert.triggered", rule=rule.name, value=value, severity=rule.severity.value)

        await dispatch(rule=rule, event=event, value=value)


def _check_condition(rule: AlertRule, value: float, previous: Optional[float] = None) -> bool:
    if rule.condition == AlertCondition.change:
        # Triggers when the value differs from the previous reading of the same tag.
        # The first reading after start-up has no reference and never triggers.
        return previous is not None and value != previous
    op = _OPERATORS.get(rule.condition)
    if op and rule.threshold is not None:
        return op(value, rule.threshold)
    return False
