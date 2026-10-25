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


# Sprint 7 (scale): one constant statement per table (arrays + unnest, rows in key order = consistent lock order):
# the multi-row VALUES form was compiled anew by SQLAlchemy for every distinct row count — a CPU hot spot of the
# ingestion process at 2 400 readings/s.
_TOTALS_SQL = text("""
INSERT INTO usage_totals (scope, entity_id, measure, tenant_id, value, updated_at)
SELECT r.sc, r.eid, r.m, CAST(:tid AS uuid), r.v, now()
  FROM unnest(CAST(:sc AS varchar[]), CAST(:eid AS uuid[]), CAST(:m AS varchar[]), CAST(:v AS float8[]))
       WITH ORDINALITY AS r(sc, eid, m, v, ord)
 ORDER BY r.ord
ON CONFLICT (scope, entity_id, measure)
DO UPDATE SET value = usage_totals.value + excluded.value, updated_at = now()
RETURNING scope, entity_id::text, measure, value
""")
_BUCKETS_SQL = text("""
INSERT INTO usage_buckets (scope, entity_id, measure, bucket_start, tenant_id, value)
SELECT r.sc, r.eid, r.m, r.b, CAST(:tid AS uuid), r.v
  FROM unnest(CAST(:sc AS varchar[]), CAST(:eid AS uuid[]), CAST(:m AS varchar[]), CAST(:b AS timestamptz[]),
              CAST(:v AS float8[])) WITH ORDINALITY AS r(sc, eid, m, b, v, ord)
 ORDER BY r.ord
ON CONFLICT (scope, entity_id, measure, bucket_start)
DO UPDATE SET value = usage_buckets.value + excluded.value
""")


async def apply_contributions(db: AsyncSession, tenant_id: str, contribs: list[Contribution]
                              ) -> dict[tuple[str, str, str], tuple[float, float]]:
    """Adds the contributions to usage_totals/usage_buckets. Returns {key: (delta, new_total)}."""
    if not contribs:
        return {}
    totals, buckets = aggregate(contribs)
    # One multi-row upsert per table (rows in key order → consistent lock order, no deadlocks):
    # a part counter can touch many positions; one statement per key cost a round trip each.
    t_keys = sorted(totals)
    rows = (await db.execute(_TOTALS_SQL, {
        "tid": tenant_id, "sc": [k[0] for k in t_keys], "eid": [str(k[1]) for k in t_keys],
        "m": [k[2] for k in t_keys], "v": [float(totals[k]) for k in t_keys]})).all()
    new_totals = {(sc, str(eid), m): v for sc, eid, m, v in rows}
    out = {key: (totals[key], new_totals[(key[0], str(key[1]), key[2])]) for key in t_keys}
    b_keys = sorted(buckets)
    await db.execute(_BUCKETS_SQL, {
        "tid": tenant_id, "sc": [k[0] for k in b_keys], "eid": [str(k[1]) for k in b_keys],
        "m": [k[2] for k in b_keys], "b": [k[3] for k in b_keys], "v": [float(buckets[k]) for k in b_keys]})
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
    outcomes, derive_at = await _step_tag(db, tag, state, readings, topo, contribs)
    await db.flush()   # state row must be visible to the raw SQL below
    applied = await apply_contributions(db, tag.tenant_id, contribs)
    if outcomes:
        outcomes[-1].updates = _updates(tag.tenant_id, topo, applied)
    if derive_at is not None:
        derive_at.status_change = await derive_machine_status(db, tag.tenant_id, tag.machine_id)
    await db.commit()
    return outcomes


async def process_many(db: AsyncSession, tenant_id: str,
                       groups: list[tuple[TagInfo, list[tuple[float, datetime, bool]]]],
                       topo: Optional[Topology] = None) -> list[list[ProcessOutcome]]:
    """Sprint 7 (scale, docs/performance.md): the readings of SEVERAL tags of one tenant (each group = one tag, its
    readings in arrival order; one group per tag) in ONE transaction — the same result as process_readings per
    group, with one tenant lock, one locking SELECT of every tag state, one upsert of the summed contributions and
    one status derivation per machine. At 200 machines × 3 usage tags at 1 Hz, one transaction per reading was the
    ingestion bottleneck (≈ 8 round trips per reading). Lock order is deterministic (tag states by id, totals and
    buckets by key, machines by id), so concurrent batches of the same tenant cannot deadlock. All usage updates
    go to the last outcome of the last group; status changes to the last outcome of the group that triggered them.
    Commits. Raises on any error (the caller falls back to process_readings per group, to isolate a bad one)."""
    sem = [(t, r) for t, r in groups if t.role in COUNTER_ROLES
           or t.role in (SemanticRole.run_state, SemanticRole.fault_state)]
    results: dict[str, list[ProcessOutcome]] = {}
    if sem:
        # the same (cached) per-machine topology process_readings uses for each tag
        topos: dict[Optional[str], Topology] = {}
        for t, _ in sem:
            if t.machine_id not in topos:
                topos[t.machine_id] = topo if topo is not None else await topology_cache.topology(
                    db, tenant_id, [t.machine_id] if t.machine_id else [])
        merged = Topology()
        for tp in topos.values():
            merged.positions.update(tp.positions)
        await lock_tenant_usage_shared(db, tenant_id)
        ids = sorted({t.id for t, _ in sem})
        states = {s.tag_id: s for s in (await db.execute(
            select(TagState).where(TagState.tag_id.in_(ids)).order_by(TagState.tag_id).with_for_update()
        )).scalars().all()}
        contribs: list[Contribution] = []
        derive: dict[str, ProcessOutcome] = {}
        last: Optional[ProcessOutcome] = None
        for tag, readings in sorted(sem, key=lambda g: g[0].id):
            outs, derive_at = await _step_tag(db, tag, states.get(tag.id), readings, topos[tag.machine_id], contribs)
            results[tag.id] = outs
            if outs:
                last = outs[-1]
            if derive_at is not None:
                derive[tag.machine_id] = derive_at
        await db.flush()
        applied = await apply_contributions(db, tenant_id, contribs)
        if last is not None:
            last.updates = _updates(tenant_id, merged, applied)
        for mid, change in (await derive_machine_statuses(db, tenant_id, list(derive))).items():
            derive[mid].status_change = change
        await db.commit()
    return [results.get(t.id) or [ProcessOutcome(ignored="not_semantic") for _ in r] for t, r in groups]


async def _step_tag(db: AsyncSession, tag: TagInfo, state: Optional[TagState],
                    readings: list[tuple[float, datetime, bool]], topo: Topology,
                    contribs: list[Contribution]) -> tuple[list[ProcessOutcome], Optional[ProcessOutcome]]:
    """The readings of one tag against its (locked) state: updates the state, appends the contributions.
    Returns (outcome per reading, the outcome after which the machine status must be derived, or None)."""
    role = tag.role
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
    return outcomes, derive_at


def _event_name(ev: str) -> str:
    return "counter_rollover" if ev == "rollover" else ev


_DERIVE_BODY = """
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
"""
_DERIVE_SQL = text(_DERIVE_BODY + "   FOR UPDATE OF m")
_DERIVE_PEEK_SQL = text(_DERIVE_BODY)
_DERIVE_PEEK_MANY_SQL = text(_DERIVE_BODY.replace("m.id = CAST(:mid AS uuid)", "m.id = ANY(CAST(:mids AS uuid[]))")
                             .replace("SELECT m.status::text AS status", "SELECT m.id::text AS id, m.status::text AS status"))


def _derived(row) -> Optional[str]:
    """The status the signals give (None: nothing to change — maintenance, or an operator-declared error)."""
    if row is None or row["status"] == "maintenance":
        return None
    current = row["status"]
    if row["fault_on"]:
        return "error"
    if current == "error" and not row["fault_tags"]:
        return None                      # operator-declared error, no fault signal to clear it
    if row["run_value"] is not None:
        return "running" if row["run_value"] else "stopped"
    return "running"                     # fault cleared, no run_state: data is arriving


async def derive_machine_statuses(db: AsyncSession, tenant_id: str, machine_ids: list[str]
                                  ) -> dict[str, Optional[StatusChange]]:
    """derive_machine_status for several machines: one unlocked look at all of them, then the locked path only
    for the (rare) machines whose status would change, in machine-id order."""
    mids = sorted(set(machine_ids))
    if not mids:
        return {}
    rows = {r["id"]: r for r in (await db.execute(_DERIVE_PEEK_MANY_SQL, {"mids": mids, "tid": tenant_id})
                                 ).mappings().all()}
    out: dict[str, Optional[StatusChange]] = {}
    for mid in mids:
        peek = rows.get(mid)
        new = _derived(peek)
        out[mid] = None if new is None or new == peek["status"] else await derive_machine_status(
            db, tenant_id, mid, peeked=True)
    return out


async def derive_machine_status(db: AsyncSession, tenant_id: str, machine_id: str,
                                peeked: bool = False) -> Optional[StatusChange]:
    # Sprint 7 (scale): almost every call changes nothing (1 Hz run/fault readings of a steady machine). Look first
    # without locking the machine row (it was the hot row: mark_seen and the usage transactions of the machine's
    # other tags queued on it); lock and decide again only when the status would change.
    if not peeked:
        peek = (await db.execute(_DERIVE_PEEK_SQL, {"mid": machine_id, "tid": tenant_id})).mappings().first()
        new = _derived(peek)
        if new is None or new == peek["status"]:
            return None
    row = (await db.execute(_DERIVE_SQL, {"mid": machine_id, "tid": tenant_id})).mappings().first()
    new = _derived(row)
    if new is None:
        return None
    current = row["status"]
    if new == current:
        return None
    await db.execute(text("UPDATE machines SET status = CAST(:s AS machinestatus) WHERE id = CAST(:mid AS uuid)"),
                     {"s": new, "mid": machine_id})
    return StatusChange(tenant_id=tenant_id, machine_id=machine_id, machine_name=row["name"],
                        status=new, previous_status=current, last_seen_at=row["last_seen_at"])
