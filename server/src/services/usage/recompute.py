"""
Rebuild usage accumulators from history (domain.md §3.5: "DEVEM ser reconstruíveis a partir do
histórico (recalcular = mesmo resultado)").

History = the raw readings stored in InfluxDB (measurement `sensors`). They are replayed per tag,
in timestamp order, through the same pure rules (core.py) and attribution (topology.py) as the
incremental pipeline, using the *current* asset configuration (tag → position links and component
install windows). Result: usage_totals + usage_buckets (+ the final tag_states).

Bounded memory: history is streamed from Influx one tag × one window (USAGE_RECOMPUTE_WINDOW_H,
default 24 h) at a time, in chunks of at most CHUNK_ROWS readings, and each chunk is folded into the
accumulators immediately; only the totals and the hourly buckets are kept (they grow with
hours × series, not with the number of readings).

`recompute_usage(..., apply=False)` only reports the differences (live processing keeps running;
a reading processed during the dry run can show up as a transient difference). `apply=True`
replaces the tenant's accumulators, buckets and counter/run states by the replayed ones:
  1. replay the whole history without blocking the live pipeline;
  2. catch-up passes (still unlocked) re-read, per tag, only what arrived after that tag's last
     replayed reading — including late writes and edge backlog replays with old timestamps;
  3. take the tenant's usage lock EXCLUSIVE (usage/locks.py; the live processor takes it shared),
     run a last catch-up pass, replace the accumulators and commit. Live readings of the tenant
     wait during step 3 (they are not lost) and then continue from the replayed tag_states:
     what the replay already covered is a duplicate/out of order, anything newer is counted.
Do it only when the Influx retention covers the whole history (buckets older than the retained
readings would be lost).

Long histories: run it as a background job (`start_recompute_job`, POST /api/usage/recompute
?background=true); one recompute per tenant at a time.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Iterable, Optional

import structlog
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models import TagState, UsageBucket, UsageTotal
from ...models.tag import COUNTER_ROLES, SemanticRole
from ..influx import influx
from ..validation import ensure_uuid
from .core import CounterTrack, RunTrack, counter_step, run_step
from .locks import lock_tenant_usage_exclusive
from .topology import Topology, load_topology

log = structlog.get_logger()

TOL = 1e-6
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
# Unlocked catch-up passes before taking the exclusive lock, and when a pass is short enough.
MAX_UNLOCKED_CATCHUPS = 3
CATCHUP_TARGET_S = 2.0


@dataclass
class ReplayResult:
    totals: dict = field(default_factory=dict)      # (scope, entity, measure) -> value
    buckets: dict = field(default_factory=dict)     # (scope, entity, measure, bucket) -> value
    states: dict = field(default_factory=dict)      # tag_id -> CounterTrack | RunTrack | (value, ts)


class Replayer:
    """Incremental replay: feed each tag's readings in timestamp order, in as many chunks as
    needed (chunks of one tag must come in order). Contributions are folded into totals/buckets
    right away, so nothing proportional to the number of readings is retained."""

    def __init__(self, topo: Topology) -> None:
        self.topo = topo
        self.totals: dict = defaultdict(float)
        self.buckets: dict = defaultdict(float)
        self.states: dict = {}
        self.last_ts: dict[str, datetime] = {}      # last fed reading per tag (catch-up cursor)
        self.readings = 0

    def _add(self, contribs) -> None:
        for c in contribs:
            self.totals[(c.scope, c.entity_id, c.measure)] += c.amount
            self.buckets[(c.scope, c.entity_id, c.measure, c.bucket_start)] += c.amount

    def feed(self, tag_id: str, rows: list[tuple[datetime, float]]) -> None:
        tag = self.topo.tags.get(tag_id)
        if tag is None or not rows:
            return
        self.readings += len(rows)
        self.last_ts[tag_id] = max(self.last_ts.get(tag_id, rows[-1][0]), rows[-1][0])
        if tag.role in COUNTER_ROLES:
            rate = self.topo.max_rate(tag, settings.usage_default_max_rate_per_s)
            track: Optional[CounterTrack] = self.states.get(tag_id)
            for ts, v in rows:
                res = counter_step(track, v, ts, tag.counter_max, rate)
                if track is None and res.ignored == "invalid":
                    continue
                if not res.ignored:
                    track = res.track
                self._add(self.topo.counter_contributions(tag, res.delta, ts))
            self.states[tag_id] = track
        elif tag.role == SemanticRole.run_state:
            rtrack: Optional[RunTrack] = self.states.get(tag_id)
            for ts, v in rows:
                res = run_step(rtrack, v, ts, settings.machine_offline_timeout_s, settings.run_state_debounce_s)
                if not res.ignored:
                    rtrack = res.track
                self._add(self.topo.run_contributions(tag, res.intervals, res.starts, ts))
            self.states[tag_id] = rtrack
        elif tag.role == SemanticRole.fault_state:
            prev = self.states.get(tag_id)
            ts, v = rows[-1]
            if prev is None or ts >= prev[1]:
                self.states[tag_id] = (v, ts)

    def result(self) -> ReplayResult:
        return ReplayResult(dict(self.totals), dict(self.buckets), dict(self.states))


def _by_tag(readings: Iterable[tuple[str, datetime, float]]) -> dict[str, list[tuple[datetime, float]]]:
    per_tag: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for tag_id, ts, value in readings:
        per_tag[tag_id].append((ts, value))
    for rows in per_tag.values():
        rows.sort(key=lambda r: r[0])
    return per_tag


def replay(topo: Topology, readings: Iterable[tuple[str, datetime, float]]) -> ReplayResult:
    """readings: (tag_id, ts, value). Tags unknown to the topology / non-semantic are skipped."""
    r = Replayer(topo)
    for tag_id, rows in _by_tag(readings).items():
        r.feed(tag_id, rows)
    return r.result()


# ── Influx access (streamed) ────────────────────────────────────────────────

def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat()


def _base(tenant_id: str, start: datetime, stop: datetime) -> str:
    return f"""
    from(bucket: "{settings.influx_bucket}")
      |> range(start: {_iso(start)}, stop: {_iso(stop)})
      |> filter(fn: (r) => r["_measurement"] == "sensors")
      |> filter(fn: (r) => r["tenant_id"] == "{tenant_id}")"""


async def first_timestamps(tenant_id: str, tag_ids: list[str], stop: datetime) -> dict[str, datetime]:
    """Earliest reading per tag (one small query), so windows start where the data starts."""
    tenant_id = ensure_uuid(tenant_id, "tenant_id")
    tag_ids = [ensure_uuid(t, "tag_id") for t in tag_ids]
    if not tag_ids:
        return {}
    ids = ", ".join(f'"{t}"' for t in tag_ids)
    flux = _base(tenant_id, EPOCH, stop) + f"""
      |> filter(fn: (r) => contains(value: r["tag_id"], set: [{ids}]))
      |> filter(fn: (r) => r["_field"] == "value")
      |> group(columns: ["tag_id"])
      |> first()
      |> keep(columns: ["_time", "tag_id"])
    """
    out: dict[str, datetime] = {}
    async for rec in await influx._client.query_api().query_stream(flux, org=settings.influx_org):
        tid, ts = rec.values.get("tag_id"), rec.get_time()
        if tid and (tid not in out or ts < out[tid]):
            out[tid] = ts
    return out


CHUNK_ROWS = 5000


async def iter_tag_rows(tenant_id: str, tag_id: str, start: datetime, stop: datetime,
                        chunk_rows: int = CHUNK_ROWS) -> AsyncIterator[list[tuple[datetime, float]]]:
    """Readings of one tag in [start, stop), streamed from Influx in time order and yielded in chunks
    of at most `chunk_rows`: memory is bounded by the chunk, whatever the tag's sampling rate.
    (A tag may span several Influx series — unit/machine_id tags changed — so they are merged into
    one table and sorted by Influx, not in Python.)"""
    tenant_id = ensure_uuid(tenant_id, "tenant_id")
    tag_id = ensure_uuid(tag_id, "tag_id")
    flux = _base(tenant_id, start, stop) + f"""
      |> filter(fn: (r) => r["tag_id"] == "{tag_id}")
      |> filter(fn: (r) => r["_field"] == "value")
      |> group(columns: ["tag_id"])
      |> sort(columns: ["_time"])
      |> keep(columns: ["_time", "_value"])
    """
    chunk: list[tuple[datetime, float]] = []
    async for rec in await influx._client.query_api().query_stream(flux, org=settings.influx_org):
        chunk.append((rec.get_time(), float(rec.get_value())))
        if len(chunk) >= chunk_rows:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


async def tag_readings(tenant_id: str, tag_id: str, start: datetime, stop: datetime
                       ) -> list[tuple[datetime, float]]:
    """All readings of one tag in [start, stop) as a list — small ranges only (tests, tools)."""
    out: list[tuple[datetime, float]] = []
    async for rows in iter_tag_rows(tenant_id, tag_id, start, stop):
        out += rows
    return out


async def iter_tag_windows(tenant_id: str, tag_id: str, start: datetime, stop: datetime,
                           window: Optional[timedelta] = None,
                           after: Optional[datetime] = None,
                           chunk_rows: int = CHUNK_ROWS) -> AsyncIterator[list[tuple[datetime, float]]]:
    """Yields the tag's readings in time order, one Influx query per time window
    (USAGE_RECOMPUTE_WINDOW_H) and at most `chunk_rows` readings per chunk. `after`: only ts > after."""
    window = window or timedelta(hours=settings.usage_recompute_window_h)
    cur = start
    while cur < stop:
        end = min(cur + window, stop)
        async for rows in iter_tag_rows(tenant_id, tag_id, cur, end, chunk_rows):
            if after is not None:
                rows = [r for r in rows if r[0] > after]
            if rows:
                yield rows
        cur = end


async def load_readings(tenant_id: str, tag_ids: list[str], start: Optional[datetime] = None,
                        stop: Optional[datetime] = None) -> list[tuple[str, datetime, float]]:
    """All readings of the tags in [start, stop) as one list — only for small, bounded ranges
    (tests, diagnostics). recompute_usage streams instead."""
    start = start or EPOCH
    stop = stop or datetime.now(timezone.utc)
    firsts = await first_timestamps(tenant_id, tag_ids, stop)
    out = []
    for tag_id in tag_ids:
        if tag_id not in firsts:
            continue
        async for rows in iter_tag_windows(tenant_id, tag_id, max(start, firsts[tag_id]), stop):
            out += [(tag_id, ts, v) for ts, v in rows]
    return out


async def replay_pass(r: Replayer, tenant_id: str, tag_ids: list[str], stop: datetime) -> int:
    """Feeds, per tag, every reading after that tag's replay cursor (or from its first reading)
    up to `stop`. Returns how many readings were fed."""
    before = r.readings
    missing = [t for t in tag_ids if t not in r.last_ts]
    firsts = await first_timestamps(tenant_id, missing, stop) if missing else {}
    for tag_id in tag_ids:
        after = r.last_ts.get(tag_id)
        start = after if after is not None else firsts.get(tag_id)
        if start is None:
            continue
        async for rows in iter_tag_windows(tenant_id, tag_id, start, stop, after=after):
            r.feed(tag_id, rows)
    return r.readings - before


# ── recompute ───────────────────────────────────────────────────────────────

@dataclass
class RecomputeReport:
    readings: int
    totals: dict
    differences: list = field(default_factory=list)   # [{scope, entity_id, measure, stored, recomputed}]
    applied: bool = False


def _semantic(topo: Topology) -> list[str]:
    return [t.id for t in topo.tags.values()
            if t.role in COUNTER_ROLES or t.role in (SemanticRole.run_state, SemanticRole.fault_state)]


async def _diff(db: AsyncSession, tenant_id: str, totals: dict) -> list:
    stored = {(row.scope, row.entity_id, row.measure): row.value for row in (await db.execute(
        select(UsageTotal).where(UsageTotal.tenant_id == tenant_id))).scalars()}
    diffs = []
    for key in sorted(set(stored) | set(totals)):
        a, b = stored.get(key, 0.0), totals.get(key, 0.0)
        if abs(a - b) > TOL * max(1.0, abs(b)):
            diffs.append({"scope": key[0], "entity_id": key[1], "measure": key[2],
                          "stored": a, "recomputed": b})
    return diffs


async def recompute_usage(db: AsyncSession, tenant_id: str,
                          readings: Optional[list[tuple[str, datetime, float]]] = None,
                          apply: bool = False) -> RecomputeReport:
    topo = await load_topology(db, tenant_id)
    semantic = _semantic(topo)
    r = Replayer(topo)
    if readings is not None:                       # explicit history (tests / tools)
        for tag_id, rows in _by_tag(readings).items():
            r.feed(tag_id, rows)
        if apply:
            await lock_tenant_usage_exclusive(db, tenant_id)
    else:
        t0 = time.monotonic()
        await replay_pass(r, tenant_id, semantic, datetime.now(timezone.utc))
        if apply:
            took = time.monotonic() - t0
            for _ in range(MAX_UNLOCKED_CATCHUPS):
                if took <= CATCHUP_TARGET_S:
                    break
                t0 = time.monotonic()
                await replay_pass(r, tenant_id, semantic, datetime.now(timezone.utc))
                took = time.monotonic() - t0
            # Live processing of this tenant now waits; the last pass sees everything it wrote.
            await lock_tenant_usage_exclusive(db, tenant_id)
            await replay_pass(r, tenant_id, semantic, datetime.now(timezone.utc) + timedelta(seconds=1))
    result = r.result()
    report = RecomputeReport(r.readings, result.totals, await _diff(db, tenant_id, result.totals))
    if apply:
        await _apply(db, tenant_id, result)        # commits → releases the tenant lock
        report.applied = True
    else:
        await db.rollback()
    return report


# ── background jobs ─────────────────────────────────────────────────────────

class RecomputeBusy(Exception):
    """A recompute of this tenant is already running."""


@dataclass
class RecomputeJob:
    id: str
    tenant_id: str
    apply: bool
    status: str = "running"            # running | done | failed
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    report: Optional[RecomputeReport] = None
    error: Optional[str] = None


_jobs: dict[str, RecomputeJob] = {}
_running: dict[str, str] = {}          # tenant_id -> job id (sync runs use a placeholder id)
_tasks: set[asyncio.Task] = set()
_MAX_JOBS = 200


def get_job(job_id: str, tenant_id: str) -> Optional[RecomputeJob]:
    job = _jobs.get(job_id)
    return job if job is not None and job.tenant_id == str(tenant_id) else None


class tenant_slot:
    """One recompute per tenant at a time (in this process)."""

    def __init__(self, tenant_id: str, job_id: str = "sync") -> None:
        self.tenant_id, self.job_id = str(tenant_id), job_id

    def __enter__(self):
        if self.tenant_id in _running:
            raise RecomputeBusy(_running[self.tenant_id])
        _running[self.tenant_id] = self.job_id
        return self

    def __exit__(self, *exc):
        _running.pop(self.tenant_id, None)
        return False


def start_recompute_job(tenant_id: str, apply: bool) -> RecomputeJob:
    from ...db import AsyncSessionLocal

    tenant_id = str(tenant_id)
    job = RecomputeJob(id=str(uuid.uuid4()), tenant_id=tenant_id, apply=apply)
    slot = tenant_slot(tenant_id, job.id)
    slot.__enter__()                   # raises RecomputeBusy before anything is scheduled
    if len(_jobs) >= _MAX_JOBS:        # forget the oldest finished jobs
        for jid in [j.id for j in sorted(_jobs.values(), key=lambda j: j.started_at)
                    if j.status != "running"][: len(_jobs) - _MAX_JOBS + 1]:
            _jobs.pop(jid, None)
    _jobs[job.id] = job

    async def run() -> None:
        try:
            async with AsyncSessionLocal() as db:
                job.report = await recompute_usage(db, tenant_id, apply=apply)
            job.status = "done"
        except Exception as e:  # noqa: BLE001
            job.status, job.error = "failed", str(e)[:500]
            log.exception("usage.recompute_failed", tenant_id=tenant_id[:8], job_id=job.id)
        finally:
            job.finished_at = datetime.now(timezone.utc)
            slot.__exit__(None, None, None)

    task = asyncio.create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job


async def _apply(db: AsyncSession, tenant_id: str, result: ReplayResult) -> None:
    await db.execute(delete(UsageTotal).where(UsageTotal.tenant_id == tenant_id))
    await db.execute(delete(UsageBucket).where(UsageBucket.tenant_id == tenant_id))
    totals = [dict(scope=scope, entity_id=eid, measure=measure, tenant_id=tenant_id, value=v)
              for (scope, eid, measure), v in result.totals.items()]
    buckets = [dict(scope=scope, entity_id=eid, measure=measure, bucket_start=b, tenant_id=tenant_id, value=v)
               for (scope, eid, measure, b), v in result.buckets.items()]
    # Bulk executemany in chunks: the tenant lock is held meanwhile, keep it short.
    for model, rows in ((UsageTotal, totals), (UsageBucket, buckets)):
        for i in range(0, len(rows), 5000):
            await db.execute(insert(model), rows[i:i + 5000])
    for tag_id, st in result.states.items():
        if st is None:
            continue
        row = await db.get(TagState, tag_id, with_for_update=True)
        if isinstance(st, CounterTrack):
            vals = dict(last_value=st.last_value, last_ts=st.last_ts,
                        pending_value=st.pending_value, pending_ts=st.pending_ts, off_since=None)
        elif isinstance(st, RunTrack):
            vals = dict(last_value=1.0 if st.state else 0.0, last_ts=st.last_ts, off_since=st.off_since,
                        pending_value=None, pending_ts=None)
        else:
            vals = dict(last_value=float(st[0]), last_ts=st[1], pending_value=None, pending_ts=None,
                        off_since=None)
        if row is None:
            db.add(TagState(tag_id=tag_id, tenant_id=tenant_id, **vals))
        else:
            for k, v in vals.items():
                setattr(row, k, v)
    await db.commit()
