"""InfluxDB retention and downsampling (Sprint 7, docs/performance.md §5).

    <bucket>        raw readings              retention INFLUX_RAW_RETENTION_DAYS (90 d)
    <bucket>_1m     1-minute aggregates       retention INFLUX_1M_RETENTION_DAYS (730 d)
    <bucket>_1h     hourly aggregates         forever

Aggregate points keep the measurement (`sensors`) and the tags of the raw series (tenant_id, tag_id, machine_id,
unit) and carry the fields `sum`, `count`, `min`, `max`, `last`, stamped at the START of their window, so any coarser
window is exact: mean = Σsum / Σcount, min of min, max of max, last of last. A chart of the mean over windows of
≥ 1 min therefore gives the same numbers from the aggregates as from the raw readings.

Who writes them:
  * two Influx tasks (`fm_downsample_<bucket>_1m` every minute over the last 15 complete minutes,
    `fm_downsample_<bucket>_1h` every hour over the last 3 complete hours) — created / updated idempotently by
    `ensure()` (worker process at startup: src/worker.py);
  * `backfill()` — the same Flux over any aligned range: history that existed before the tasks (the worker extends
    the coverage backwards day by day, `run_rollup_maintainer`) and readings that arrive later than the task's
    15-minute look-back (gateway store-and-forward: noted by the ingestion pipeline, `note_late` → `flush_late`).

Readers (`services.influx.query_history`) use the aggregates only for the part of the range that is covered
(`coverage_start` .. the last complete aggregated window) and the raw bucket for the rest, so a chart is never
missing data because a rollup has not run yet.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import structlog

from ..config import settings

log = structlog.get_logger()

UTC = timezone.utc
TASK_LOOKBACK_1M = timedelta(minutes=15)
TASK_LOOKBACK_1H = timedelta(hours=3)
META = "fm_rollup_meta"
FIELDS = ("sum", "count", "min", "max", "last")


def names(raw: Optional[str] = None) -> dict:
    raw = raw or settings.influx_bucket
    return {"raw": raw, "1m": f"{raw}_1m", "1h": f"{raw}_1h",
            "task_1m": f"fm_downsample_{raw}_1m", "task_1h": f"fm_downsample_{raw}_1h"}


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z")


def floor(t: datetime, step: timedelta) -> datetime:
    s = int(step.total_seconds())
    ts = int(t.timestamp())
    return datetime.fromtimestamp(ts - ts % s, UTC)


# ── Flux ──────────────────────────────────────────────────────────────────────

def flux_1m(raw: str, dest: str, org: str, start: str, stop: str, tenant_id: Optional[str] = None) -> str:
    """Raw → 1-minute aggregates over [start, stop) (both minute-aligned Flux time expressions)."""
    flt = f' and r["tenant_id"] == "{tenant_id}"' if tenant_id else ""
    return f'''
data = from(bucket: "{raw}")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r["_measurement"] == "sensors" and r["_field"] == "value"{flt})
agg = (fn, field) => data
  |> aggregateWindow(every: 1m, fn: fn, timeSrc: "_start", createEmpty: false)
  |> toFloat()
  |> set(key: "_field", value: field)
union(tables: [agg(fn: sum, field: "sum"), agg(fn: count, field: "count"), agg(fn: min, field: "min"),
               agg(fn: max, field: "max"), agg(fn: last, field: "last")])
  |> to(bucket: "{dest}", org: "{org}")
'''


def flux_1h(src: str, dest: str, org: str, start: str, stop: str, tenant_id: Optional[str] = None) -> str:
    """1-minute → hourly aggregates over [start, stop) (hour-aligned)."""
    flt = f' and r["tenant_id"] == "{tenant_id}"' if tenant_id else ""
    return f'''
data = from(bucket: "{src}")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r["_measurement"] == "sensors"{flt})
agg = (field, fn) => data
  |> filter(fn: (r) => r["_field"] == field)
  |> aggregateWindow(every: 1h, fn: fn, timeSrc: "_start", createEmpty: false)
union(tables: [agg(field: "sum", fn: sum), agg(field: "count", fn: sum), agg(field: "min", fn: min),
               agg(field: "max", fn: max), agg(field: "last", fn: last)])
  |> to(bucket: "{dest}", org: "{org}")
'''


def task_flux(kind: str, raw: Optional[str] = None) -> str:
    n = names(raw)
    org = settings.influx_org
    if kind == "1m":
        head = f'option task = {{name: "{n["task_1m"]}", every: 1m, offset: 20s}}\n'
        body = flux_1m(n["raw"], n["1m"], org,
                       f"date.truncate(t: experimental.subDuration(d: {int(TASK_LOOKBACK_1M.total_seconds())}s, "
                       "from: now()), unit: 1m)", "date.truncate(t: now(), unit: 1m)")
    else:
        head = f'option task = {{name: "{n["task_1h"]}", every: 1h, offset: 5m}}\n'
        body = flux_1h(n["1m"], n["1h"], org,
                       f"date.truncate(t: experimental.subDuration(d: {int(TASK_LOOKBACK_1H.total_seconds())}s, "
                       "from: now()), unit: 1h)", "date.truncate(t: now(), unit: 1h)")
    return 'import "date"\nimport "experimental"\n' + head + body


# ── management API (idempotent) ──────────────────────────────────────────────

class _Api:
    def __init__(self) -> None:
        self.c = httpx.AsyncClient(base_url=settings.influx_url, timeout=60,
                                   headers={"Authorization": f"Token {settings.influx_token}"})
        self._org: Optional[str] = None

    async def close(self) -> None:
        await self.c.aclose()

    async def org_id(self) -> str:
        if self._org is None:
            r = await self.c.get("/api/v2/orgs", params={"org": settings.influx_org})
            r.raise_for_status()
            self._org = r.json()["orgs"][0]["id"]
        return self._org

    async def bucket(self, name: str) -> Optional[dict]:
        r = await self.c.get("/api/v2/buckets", params={"orgID": await self.org_id(), "name": name})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        bs = r.json().get("buckets") or []
        return bs[0] if bs else None

    async def ensure_bucket(self, name: str, retention_s: int, manage: bool = True) -> tuple[dict, bool]:
        """(bucket, created). An existing bucket's retention is set to `retention_s` when `manage`."""
        rules = [{"type": "expire", "everySeconds": int(retention_s)}] if retention_s > 0 else []
        b = await self.bucket(name)
        if b is None:
            r = await self.c.post("/api/v2/buckets", json={"orgID": await self.org_id(), "name": name,
                                                            "retentionRules": rules})
            r.raise_for_status()
            log.info("influx.rollup.bucket_created", bucket=name, retention_s=retention_s)
            return r.json(), True
        cur = next((x.get("everySeconds", 0) for x in b.get("retentionRules") or [] if x.get("type") == "expire"), 0)
        if manage and int(cur or 0) != int(retention_s):
            r = await self.c.patch(f"/api/v2/buckets/{b['id']}", json={"retentionRules": rules})
            r.raise_for_status()
            log.warning("influx.rollup.retention_changed", bucket=name, from_s=cur, to_s=retention_s)
        return b, False

    async def ensure_task(self, name: str, flux: str) -> str:
        """'created' | 'updated' | 'unchanged'."""
        r = await self.c.get("/api/v2/tasks", params={"orgID": await self.org_id(), "name": name})
        r.raise_for_status()
        tasks = [t for t in r.json().get("tasks") or [] if t.get("name") == name]
        if not tasks:
            r = await self.c.post("/api/v2/tasks", json={"orgID": await self.org_id(), "flux": flux,
                                                          "status": "active",
                                                          "description": "FactoryConnect downsampling (Sprint 7)"})
            r.raise_for_status()
            return "created"
        t = tasks[0]
        if (t.get("flux") or "").strip() != flux.strip() or t.get("status") != "active":
            r = await self.c.patch(f"/api/v2/tasks/{t['id']}", json={"flux": flux, "status": "active"})
            r.raise_for_status()
            return "updated"
        return "unchanged"

    async def query(self, flux: str) -> str:
        r = await self.c.post("/api/v2/query", params={"orgID": await self.org_id()},
                              json={"query": flux, "type": "flux", "dialect": {"header": True, "annotations": []}})
        r.raise_for_status()
        return r.text


async def ensure(raw: Optional[str] = None) -> dict:
    """Creates/updates the buckets, the raw retention and the two tasks. Idempotent; safe to call at every start.
    A new 1-minute bucket starts its coverage now (the history is then backfilled by run_rollup_maintainer)."""
    n = names(raw)
    api = _Api()
    try:
        _, _ = await api.ensure_bucket(n["raw"], settings.influx_raw_retention_days * 86400,
                                       manage=settings.influx_manage_retention)
        _, created_1m = await api.ensure_bucket(n["1m"], settings.influx_1m_retention_days * 86400)
        await api.ensure_bucket(n["1h"], 0)
        out = {"task_1m": await api.ensure_task(n["task_1m"], task_flux("1m", raw)),
               "task_1h": await api.ensure_task(n["task_1h"], task_flux("1h", raw)), "created_1m": created_1m}
        if created_1m or await read_coverage(raw, api=api) is None:
            # the tasks only look back 15 min: aggregate the current hour now, so coverage starts complete
            cov = floor(datetime.now(UTC), timedelta(hours=1))
            await backfill(cov, datetime.now(UTC), raw)
            await write_coverage(cov, raw)
        log.info("influx.rollup.ensured", bucket=n["raw"], **out)
        return out
    finally:
        await api.close()


# ── coverage: aggregates are complete from coverage_start on ─────────────────

_coverage_cache: dict[str, tuple[float, Optional[datetime]]] = {}
COVERAGE_TTL_S = 300.0


async def write_coverage(start: datetime, raw: Optional[str] = None) -> None:
    from .influx import influx
    n = names(raw)
    ns = int(start.timestamp()) * 1_000_000_000
    line = f"{META},kind=coverage start_ns={ns}i {ns}"
    await influx._client.write_api().write(bucket=n["1m"], record=line)
    _coverage_cache[n["raw"]] = (time.monotonic() + COVERAGE_TTL_S, start)


async def read_coverage(raw: Optional[str] = None, api: Optional[_Api] = None, cached: bool = False
                        ) -> Optional[datetime]:
    """Earliest instant from which the aggregates are complete (None: no aggregates)."""
    n = names(raw)
    hit = _coverage_cache.get(n["raw"])
    if cached and hit is not None and hit[0] > time.monotonic():
        return hit[1]
    own = api is None
    api = api or _Api()
    try:
        txt = await api.query(f'''
from(bucket: "{n["1m"]}") |> range(start: 0)
  |> filter(fn: (r) => r["_measurement"] == "{META}" and r["_field"] == "start_ns")
  |> min() |> keep(columns: ["_value"])''')
    except httpx.HTTPError:
        return hit[1] if hit else None
    finally:
        if own:
            await api.close()
    val = None
    for line in txt.splitlines()[1:]:
        cells = line.strip().split(",")
        if cells and cells[-1].strip().lstrip("-").isdigit():
            val = datetime.fromtimestamp(int(cells[-1]) / 1e9, UTC)
    _coverage_cache[n["raw"]] = (time.monotonic() + COVERAGE_TTL_S, val)
    return val


# ── backfill / late data ──────────────────────────────────────────────────────

async def backfill(start: datetime, stop: datetime, raw: Optional[str] = None,
                   tenant_id: Optional[str] = None) -> None:
    """Recomputes the 1-minute and hourly aggregates of [start, stop) (widened to whole hours) from the raw
    readings — idempotent (same points overwritten). `tenant_id` narrows it (late data of one tenant)."""
    from .validation import ensure_uuid
    n = names(raw)
    if tenant_id:
        tenant_id = ensure_uuid(tenant_id, "tenant_id")
    s = floor(start, timedelta(hours=1))
    e = floor(stop, timedelta(hours=1))
    if e < stop:
        e += timedelta(hours=1)
    api = _Api()
    try:
        await api.query(flux_1m(n["raw"], n["1m"], settings.influx_org, _iso(s), _iso(e), tenant_id))
        await api.query(flux_1h(n["1m"], n["1h"], settings.influx_org, _iso(s), _iso(e), tenant_id))
    finally:
        await api.close()


# late readings noted by the ingestion pipeline: tenant → [min ts, max ts]
_late: dict[str, list[datetime]] = {}


def note_late(tenant_id: str, ts: datetime) -> None:
    """A reading stored after the 1-minute task's look-back passed it (store-and-forward flush)."""
    r = _late.get(tenant_id)
    if r is None:
        _late[tenant_id] = [ts, ts]
    else:
        r[0], r[1] = min(r[0], ts), max(r[1], ts)


def late_threshold() -> timedelta:
    # the task re-reads the last 15 complete minutes every minute: anything older than ~14 min when stored is missed
    return TASK_LOOKBACK_1M - timedelta(minutes=1)


async def flush_late(raw: Optional[str] = None) -> int:
    """Backfills the noted late ranges (one per tenant). On failure the ranges are kept for the next try."""
    if not _late:
        return 0
    items = list(_late.items())
    _late.clear()
    done = 0
    for tid, (lo, hi) in items:
        try:
            await backfill(lo, hi + timedelta(minutes=1), raw, tenant_id=tid)
            done += 1
        except Exception as e:  # noqa: BLE001
            note_late(tid, lo)
            note_late(tid, hi)
            log.warning("influx.rollup.late_backfill_failed", tenant_id=tid[:8], error=str(e)[:200])
    return done


async def run_late_rollups(interval_s: float = 30.0) -> None:
    """Ingestion process(es): backfill the aggregates of late readings every interval_s."""
    if not settings.influx_downsampling:
        return
    while True:
        await asyncio.sleep(interval_s)
        try:
            await flush_late()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("influx.rollup.late_flush_error", error=str(e)[:200])


async def run_rollup_maintainer(raw: Optional[str] = None) -> None:
    """Worker process: ensure() (retried until InfluxDB answers), then extend the coverage backwards one day per
    step until it reaches the raw retention (history from before the tasks existed); then re-ensure hourly."""
    if not settings.influx_downsampling:
        return
    while True:
        try:
            await ensure(raw)
            break
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("influx.rollup.ensure_failed", error=str(e)[:300])
            await asyncio.sleep(30)
    oldest = datetime.now(UTC) - timedelta(days=settings.influx_raw_retention_days)
    while True:
        try:
            cov = await read_coverage(raw)
            if cov is not None and cov > oldest + timedelta(hours=1):
                start = max(oldest, cov - timedelta(days=1))
                await backfill(start, cov, raw)
                await write_coverage(floor(start, timedelta(hours=1)), raw)
                log.info("influx.rollup.backfilled", start=_iso(start), stop=_iso(cov))
                await asyncio.sleep(1)
                continue
            await asyncio.sleep(3600)
            await ensure(raw)
            oldest = datetime.now(UTC) - timedelta(days=settings.influx_raw_retention_days)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("influx.rollup.maintainer_error", error=str(e)[:300])
            await asyncio.sleep(60)


# ── reading side ──────────────────────────────────────────────────────────────

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def duration_s(window: str) -> Optional[int]:
    """'5m' → 300 (single-unit Flux durations; None for anything else)."""
    w = window.strip()
    if len(w) < 2 or w[-1] not in _UNITS or not w[:-1].isdigit():
        return None
    return int(w[:-1]) * _UNITS[w[-1]]


def plan_history(start: datetime, stop: datetime, window_s: Optional[int], coverage: Optional[datetime],
                 now: Optional[datetime] = None) -> list[tuple[str, datetime, datetime]]:
    """Which source answers which part of a mean-per-window chart: [(source, start, stop)] with source in
    raw | 1m | 1h. Aggregates only for windows that are whole minutes (hours for 1h), spans ≥ 6 h, inside the
    coverage and before the last complete aggregated window (cut aligned to the chart window, so no chart window
    mixes two sources)."""
    now = now or datetime.now(UTC)
    if (not settings.influx_downsampling or window_s is None or window_s < 60 or window_s % 60
            or coverage is None or stop - start < timedelta(hours=6)):
        return [("raw", start, stop)]
    hourly = window_s % 3600 == 0
    src = "1h" if hourly else "1m"
    ready = floor(now - timedelta(minutes=10), timedelta(hours=1)) if hourly else floor(now - timedelta(minutes=3),
                                                                                        timedelta(minutes=1))
    w = timedelta(seconds=window_s)
    a = max(start, coverage)
    a = floor(a, w) + (w if floor(a, w) < a else timedelta(0))     # first whole chart window inside the coverage
    b = floor(min(stop, ready), w)
    if b <= a:
        return [("raw", start, stop)]
    parts = []
    if start < a:
        parts.append(("raw", start, a))
    parts.append((src, a, b))
    if b < stop:
        parts.append(("raw", b, stop))
    return parts
