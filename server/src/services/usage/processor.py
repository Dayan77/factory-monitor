"""
Incremental usage processing of one reading (called by the MQTT subscriber).

For semantic tags only (analog etc. are ignored here):
  * counters (cycle_counter, cycle_pulse, part_counter_*): delta per domain.md §3.3 against the
    last accepted value persisted in `tag_states` (row lock → serialised per tag; duplicates and
    out-of-order readings give delta 0, so re-delivery is idempotent). counter_reset /
    counter_rollover / counter_glitch / counter_rebaseline go to `usage_events`.
  * run_state: operating seconds and starts per §3.4.
  * fault_state: last value kept for the machine status derivation (§1.1).
Deltas are attributed (topology.py) and added to `usage_totals` and the hourly `usage_buckets`
in the same transaction as the state update.

Machine status (§1.1) for machines with a machine-level run_state / fault_state tag:
maintenance (operator) > error (fault_state ≠ 0) > running (run_state = 1) > stopped. A machine
without a fault_state tag keeps an operator-declared 'error'. Only fresh readings change status.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models import TagState, UsageBucket, UsageEvent, UsageTotal
from ...models.tag import COUNTER_ROLES, SemanticRole
from ..machine_status import StatusChange
from .core import CounterTrack, RunTrack, counter_step, run_step
from .cache import topology_cache
from .locks import lock_tenant_usage_shared
from .topology import Contribution, TagInfo, Topology

# measure stored → measure exposed in API / WebSocket
API_MEASURE = {"operating_seconds": "hours"}


def api_value(measure: str, value: float) -> float:
    return value / 3600.0 if measure == "operating_seconds" else value


@dataclass(frozen=True)
class UsageUpdate:
    tenant_id: str
    position_id: Optional[str]
    machine_id: Optional[str]
    measure: str      # API name: cycles | hours | starts | good | scrap | total
    delta: float
    total: float


@dataclass
class ProcessOutcome:
    updates: list[UsageUpdate] = field(default_factory=list)
    status_change: Optional[StatusChange] = None
    event: Optional[str] = None
    ignored: Optional[str] = None
    delta: float = 0.0


async def _lock_state(db: AsyncSession, tag_id: str) -> Optional[TagState]:
    return (await db.execute(
        select(TagState).where(TagState.tag_id == tag_id).with_for_update()
    )).scalar_one_or_none()


async def _insert_state(db: AsyncSession, tag: TagInfo, value: float, ts: datetime,
                        off_since: Optional[datetime] = None) -> bool:
    """Baseline row. False if another transaction created it first."""
    res = await db.execute(
        pg_insert(TagState).values(tag_id=tag.id, tenant_id=tag.tenant_id, last_value=value,
                                   last_ts=ts, off_since=off_since)
        .on_conflict_do_nothing(index_elements=["tag_id"])
    )
    return res.rowcount == 1


def aggregate(contribs: list[Contribution]) -> tuple[dict, dict]:
    totals: dict[tuple[str, str, str], float] = defaultdict(float)
    buckets: dict[tuple[str, str, str, datetime], float] = defaultdict(float)
    for c in contribs:
        totals[(c.scope, c.entity_id, c.measure)] += c.amount
        buckets[(c.scope, c.entity_id, c.measure, c.bucket_start)] += c.amount
    return totals, buckets


async def apply_contributions(db: AsyncSession, tenant_id: str, contribs: list[Contribution]
                              ) -> dict[tuple[str, str, str], tuple[float, float]]:
    """Adds the contributions to usage_totals/usage_buckets. Returns {key: (delta, new_total)}."""
    if not contribs:
        return {}
    totals, buckets = aggregate(contribs)
    # One multi-row upsert per table (rows in key order → consistent lock order, no deadlocks):
    # a part counter can touch many positions; one statement per key cost a round trip each.
    t_rows = [dict(scope=sc, entity_id=eid, measure=m, tenant_id=tenant_id, value=amount)
              for (sc, eid, m), amount in sorted(totals.items())]
    stmt = pg_insert(UsageTotal).values(t_rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["scope", "entity_id", "measure"],
        set_={"value": UsageTotal.value + stmt.excluded.value, "updated_at": text("now()")},
    ).returning(UsageTotal.scope, UsageTotal.entity_id, UsageTotal.measure, UsageTotal.value)
    new_totals = {(sc, eid, m): v for sc, eid, m, v in (await db.execute(stmt)).all()}
    out = {key: (amount, new_totals[key]) for key, amount in sorted(totals.items())}
    b_rows = [dict(scope=sc, entity_id=eid, measure=m, bucket_start=b, tenant_id=tenant_id, value=amount)
              for (sc, eid, m, b), amount in sorted(buckets.items())]
    stmt = pg_insert(UsageBucket).values(b_rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["scope", "entity_id", "measure", "bucket_start"],
        set_={"value": UsageBucket.value + stmt.excluded.value},
    )
    await db.execute(stmt)
    return out


def _updates(tenant_id: str, topo: Topology, applied: dict) -> list[UsageUpdate]:
    ups = []
    for (scope, eid, measure), (delta, total) in applied.items():
        if scope == "position":
            p = topo.positions.get(eid)
            ups.append(UsageUpdate(tenant_id, eid, p.machine_id if p else None,
                                   API_MEASURE.get(measure, measure),
                                   api_value(measure, delta), api_value(measure, total)))
        elif scope == "machine":
            ups.append(UsageUpdate(tenant_id, None, eid, API_MEASURE.get(measure, measure),
                                   api_value(measure, delta), api_value(measure, total)))
    return ups


async def process_reading(db: AsyncSession, tag: TagInfo, value: float, ts: datetime,
                          fresh: bool = True, topo: Optional[Topology] = None) -> ProcessOutcome:
    """Processes one reading of a tag already verified to belong to tag.tenant_id. Commits."""
    return (await process_readings(db, tag, [(value, ts, fresh)], topo))[0]


async def process_readings(db: AsyncSession, tag: TagInfo, readings: list[tuple[float, datetime, bool]],
                           topo: Optional[Topology] = None) -> list[ProcessOutcome]:
    """Processes consecutive readings of ONE tag (arrival order) in one transaction: the same result as calling
    process_reading for each, with one state lock, one upsert of the summed contributions and one commit
    (Sprint 5b: the per-tag ordered MQTT pipeline hands over what queued up for the tag — a gateway buffer flush
    is no longer one transaction per reading). The machine status is derived once, after the last reading. Commits."""
    role = tag.role
    if role not in COUNTER_ROLES and role not in (SemanticRole.run_state, SemanticRole.fault_state):
        return [ProcessOutcome(ignored="not_semantic") for _ in readings]
    if topo is None:
        topo = await topology_cache.topology(db, tag.tenant_id, [tag.machine_id] if tag.machine_id else [])

    # Waits while a recompute(apply) of this tenant replaces the accumulators (usage/locks.py).
    await lock_tenant_usage_shared(db, tag.tenant_id)
    state = await _lock_state(db, tag.id)
    contribs: list[Contribution] = []
    outcomes: list[ProcessOutcome] = []
    derive_at: Optional[ProcessOutcome] = None
    max_rate = topo.max_rate(tag, settings.usage_default_max_rate_per_s) if role in COUNTER_ROLES else None

    for value, ts, fresh in readings:
        outcome = ProcessOutcome()
        outcomes.append(outcome)
        if role in COUNTER_ROLES:
            track = None if state is None else CounterTrack(
                int(round(state.last_value)), state.last_ts,
                None if state.pending_value is None else int(round(state.pending_value)), state.pending_ts)
            res = counter_step(track, value, ts, tag.counter_max, max_rate)
            outcome.ignored, outcome.event, outcome.delta = res.ignored, res.event, res.delta
            if state is None:
                if res.ignored == "invalid":
                    continue
                created = await _insert_state(db, tag, res.track.last_value, ts)
                state = await _lock_state(db, tag.id)
                if not created:
                    continue                 # another transaction created the baseline first
            elif not res.ignored:
                state.last_value, state.last_ts = res.track.last_value, res.track.last_ts
                state.pending_value, state.pending_ts = res.track.pending_value, res.track.pending_ts
            if res.event:
                db.add(UsageEvent(tenant_id=tag.tenant_id, tag_id=tag.id, event=_event_name(res.event),
                                  ts=ts, prev_value=res.prev_value, value=value, delta=res.delta))
            contribs += topo.counter_contributions(tag, res.delta, ts)

        elif role == SemanticRole.run_state:
            track = None if state is None else RunTrack(bool(state.last_value), state.last_ts, state.off_since)
            res = run_step(track, value, ts, settings.machine_offline_timeout_s, settings.run_state_debounce_s)
            outcome.ignored = res.ignored
            if state is None:
                created = await _insert_state(db, tag, 1.0 if res.track.state else 0.0, ts, res.track.off_since)
                state = await _lock_state(db, tag.id)
                if not created:
                    continue
            elif not res.ignored:
                state.last_value = 1.0 if res.track.state else 0.0
                state.last_ts, state.off_since = res.track.last_ts, res.track.off_since
            contribs += topo.run_contributions(tag, res.intervals, res.starts, ts)
            outcome.delta = res.starts

        else:  # fault_state: keep the latest value
            if state is None:
                created = await _insert_state(db, tag, float(value), ts)
                state = await _lock_state(db, tag.id)
                if not created:
                    continue
            elif ts >= state.last_ts:
                state.last_value, state.last_ts = float(value), ts
            else:
                outcome.ignored = "out_of_order"

        if (fresh and not outcome.ignored and tag.machine_id and
                (role == SemanticRole.fault_state or (role == SemanticRole.run_state and not tag.position_id))):
            derive_at = outcome

    await db.flush()   # state row must be visible to the raw SQL below
    applied = await apply_contributions(db, tag.tenant_id, contribs)
    if outcomes:
        outcomes[-1].updates = _updates(tag.tenant_id, topo, applied)
    if derive_at is not None:
        derive_at.status_change = await derive_machine_status(db, tag.tenant_id, tag.machine_id)
    await db.commit()
    return outcomes


def _event_name(ev: str) -> str:
    return "counter_rollover" if ev == "rollover" else ev


_DERIVE_SQL = text("""
SELECT m.status::text AS status, m.name, m.last_seen_at,
       (SELECT bool_or(s.last_value <> 0) FROM tags t JOIN tag_states s ON s.tag_id = t.id
         WHERE t.machine_id = m.id AND t.semantic_role = 'fault_state' AND t.is_active) AS fault_on,
       (SELECT count(*) FROM tags t
         WHERE t.machine_id = m.id AND t.semantic_role = 'fault_state' AND t.is_active) AS fault_tags,
       (SELECT s.last_value FROM tags t JOIN tag_states s ON s.tag_id = t.id
         WHERE t.machine_id = m.id AND t.semantic_role = 'run_state' AND t.position_id IS NULL
           AND t.is_active ORDER BY s.last_ts DESC LIMIT 1) AS run_value
  FROM machines m
 WHERE m.id = CAST(:mid AS uuid) AND m.tenant_id = CAST(:tid AS uuid)
   FOR UPDATE OF m
""")


async def derive_machine_status(db: AsyncSession, tenant_id: str, machine_id: str) -> Optional[StatusChange]:
    row = (await db.execute(_DERIVE_SQL, {"mid": machine_id, "tid": tenant_id})).mappings().first()
    if row is None or row["status"] == "maintenance":
        return None
    current = row["status"]
    if row["fault_on"]:
        new = "error"
    elif current == "error" and not row["fault_tags"]:
        return None                      # operator-declared error, no fault signal to clear it
    elif row["run_value"] is not None:
        new = "running" if row["run_value"] else "stopped"
    else:
        new = "running"                  # fault cleared, no run_state: data is arriving
    if new == current:
        return None
    await db.execute(text("UPDATE machines SET status = CAST(:s AS machinestatus) WHERE id = CAST(:mid AS uuid)"),
                     {"s": new, "mid": machine_id})
    return StatusChange(tenant_id=tenant_id, machine_id=machine_id, machine_name=row["name"],
                        status=new, previous_status=current, last_seen_at=row["last_seen_at"])
