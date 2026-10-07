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


class RuleTagsCache:
    """Sprint 7 (scale harness): which tags of a tenant have active alert rules. Evaluating a reading used to open a
    session and query alert_rules for EVERY reading (3 round trips; most tags have no rule) — the largest share of
    the per-reading database work at 2 400 readings/s. One query per tenant per `ttl_s`; any AlertRule committed in
    this process clears it at once (session events below); other processes see changes within `ttl_s`."""

    def __init__(self, ttl_s: float = 10.0) -> None:
        self.ttl_s = ttl_s
        self._by_tenant: dict[str, tuple[float, frozenset]] = {}
        self.generation = 0

    def invalidate(self) -> None:
        self.generation += 1
        self._by_tenant.clear()

    async def has_rules(self, tenant_id: str, tag_id: str) -> bool:
        import time
        hit = self._by_tenant.get(tenant_id)
        if hit is not None and hit[0] > time.monotonic():
            return tag_id in hit[1]
        from ..db import AsyncSessionLocal
        gen = self.generation
        async with AsyncSessionLocal() as db:
            tags = frozenset((await db.execute(select(AlertRule.tag_id).where(
                AlertRule.tenant_id == tenant_id, AlertRule.is_active.is_(True)).distinct())).scalars().all())
        if gen == self.generation:
            if len(self._by_tenant) > 10_000:
                self._by_tenant.clear()
            self._by_tenant[tenant_id] = (time.monotonic() + self.ttl_s, tags)
        return tag_id in tags


rule_tags = RuleTagsCache()


def note_value(tenant_id: str, tag_id: str, value: float) -> None:
    """Fast path for a reading of a tag without rules: keeps the 'change' reference up to date."""
    _last_value[(tenant_id, tag_id)] = value


def _install_rule_cache_invalidation() -> None:
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    @event.listens_for(Session, "after_flush")
    def _mark(session, _ctx) -> None:
        if any(isinstance(o, AlertRule) for o in (*session.new, *session.dirty, *session.deleted)):
            session.info["alert_rules_dirty"] = True

    @event.listens_for(Session, "do_orm_execute")
    def _mark_bulk(state) -> None:
        if (state.is_update or state.is_delete) and any(m.class_ is AlertRule for m in state.all_mappers):
            state.session.info["alert_rules_dirty"] = True

    @event.listens_for(Session, "after_commit")
    def _inv(session) -> None:
        if session.info.pop("alert_rules_dirty", False):
            rule_tags.invalidate()
            from ..services.usage.cache import notify_other_processes
            notify_other_processes()              # ingestion processes (src/ingest_worker.py)

    @event.listens_for(Session, "after_rollback")
    def _drop(session) -> None:
        session.info.pop("alert_rules_dirty", None)


_install_rule_cache_invalidation()


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
