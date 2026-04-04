"""
Rules Engine — evaluates AlertRules against incoming sensor readings.
Triggers AlertEvents and dispatches notifications.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import select
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

# in-memory cache: rule_id → last triggered timestamp
_last_triggered: dict[str, datetime] = {}


async def evaluate(
    db: AsyncSession,
    tag_id: str,
    tenant_id: str,
    value: float,
) -> None:
    """Called for each incoming sensor reading."""
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
        triggered = _check_condition(rule, value)
        if not triggered:
            continue

        # Respect cooldown
        last = _last_triggered.get(rule.id)
        cooldown = timedelta(minutes=rule.cooldown_minutes)
        now = datetime.now(timezone.utc)
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


def _check_condition(rule: AlertRule, value: float) -> bool:
    if rule.condition == AlertCondition.change:
        return True  # always triggers on any new reading (use cooldown to rate-limit)
    op = _OPERATORS.get(rule.condition)
    if op and rule.threshold is not None:
        return op(value, rule.threshold)
    return False
