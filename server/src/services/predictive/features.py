"""P-1 — condition features per window, from the raw readings in InfluxDB.

Pure helpers (`time_window_feature`, `cycle_blocks`) do the arithmetic and are unit-tested; `influx_points` /
`presence` read Influx (measurement `sensors`, filtered by tenant and tag/machine, every id validated).

* Analog signals (current, vibration, temperature): time-weighted (sample-and-hold) mean over the intervals with
  `run_state = 1`; each value holds until the next reading of the same tag, but never over a data hole (no reading
  from any tag of the machine for more than `machine_offline_timeout_s`, domain.md §3.4). Valid window: running
  ≥ 25 % of the window and data coverage ≥ 80 % of the running time.
* stroke_time: median of the stroke times of each block of N cycles (cycles from the position's cycle counter, or
  one cycle per stroke reading when the position has no counter). Valid block: readings in ≥ 80 % of its cycles.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Iterable, Optional

from ...config import settings
from ..influx import influx
from ..validation import ensure_uuid

MIN_RUNNING_FRACTION = 0.25
MIN_COVERAGE = 0.80
MIN_BLOCK_READINGS = 0.80

Interval = tuple[datetime, datetime]


def _utc(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _rfc(t: datetime) -> str:
    return _utc(t).astimezone(timezone.utc).isoformat()


# ── interval arithmetic ───────────────────────────────────────────────────────

def merge(intervals: Iterable[Interval]) -> list[Interval]:
    out: list[list[datetime]] = []
    for a, b in sorted(i for i in intervals if i[1] > i[0]):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def intersect(xs: list[Interval], ys: list[Interval]) -> list[Interval]:
    out, i, j = [], 0, 0
    while i < len(xs) and j < len(ys):
        a, b = max(xs[i][0], ys[j][0]), min(xs[i][1], ys[j][1])
        if b > a:
            out.append((a, b))
        if xs[i][1] < ys[j][1]:
            i += 1
        else:
            j += 1
    return out


def total_s(xs: Iterable[Interval]) -> float:
    return sum((b - a).total_seconds() for a, b in xs)


def held(samples: list[tuple[datetime, float]], start: datetime, end: datetime,
         presence: Optional[list[Interval]]) -> list[tuple[datetime, datetime, float]]:
    """Sample-and-hold segments within [start, end): each value until the next reading, clipped to presence."""
    segs: list[tuple[datetime, datetime, float]] = []
    s = sorted(samples, key=lambda p: p[0])
    for k, (t, v) in enumerate(s):
        nxt = s[k + 1][0] if k + 1 < len(s) else end
        a, b = max(t, start), min(nxt, end)
        if b > a:
            segs.append((a, b, v))
    if presence is None:
        return segs
    out = []
    for a, b, v in segs:
        for pa, pb in intersect([(a, b)], presence):
            out.append((pa, pb, v))
    return out


@dataclass
class WindowFeature:
    x: Optional[float]
    valid: bool
    n: int
    running_h: Optional[float] = None
    coverage: Optional[float] = None
    reason: Optional[str] = None


def time_window_feature(signal: list[tuple[datetime, float]], run_state: Optional[list[tuple[datetime, float]]],
                        start: datetime, end: datetime, presence: Optional[list[Interval]] = None,
                        ambient: Optional[list[tuple[datetime, float]]] = None) -> WindowFeature:
    """Time-weighted mean of `signal` while running in [start, end) (P-1). `run_state=None`: the position has no
    run_state tag — running = whenever the machine has data. With `ambient`, the feature is ΔT = T − T_amb."""
    if run_state is None:
        running = merge(presence) if presence is not None else [(start, end)]
        running = intersect(running, [(start, end)])
    else:
        running = merge((a, b) for a, b, v in held(run_state, start, end, presence) if v >= 0.5)
    run_s = total_s(running)
    dur = (end - start).total_seconds()
    n = sum(1 for t, _ in signal if start <= t < end)
    if run_s <= 0 or run_s < MIN_RUNNING_FRACTION * dur:
        return WindowFeature(None, False, n, run_s / 3600, None, "not_running")
    segs = [(a, b, v) for a, b, v in held(signal, start, end, presence)]
    on = []
    for a, b, v in segs:
        for ia, ib in intersect([(a, b)], running):
            on.append((ia, ib, v))
    cov_s = total_s((a, b) for a, b, _ in on)
    coverage = cov_s / run_s
    if coverage < MIN_COVERAGE or cov_s <= 0:
        return WindowFeature(None, False, n, run_s / 3600, coverage, "low_coverage")
    mean = sum((b - a).total_seconds() * v for a, b, v in on) / cov_s
    if ambient is not None:
        amb = []
        for a, b, v in held(ambient, start, end, presence):
            for ia, ib in intersect([(a, b)], running):
                amb.append((ia, ib, v))
        amb_s = total_s((a, b) for a, b, _ in amb)
        if amb_s < MIN_COVERAGE * run_s:
            return WindowFeature(None, False, n, run_s / 3600, coverage, "ambient_missing")
        mean -= sum((b - a).total_seconds() * v for a, b, v in amb) / amb_s
    return WindowFeature(mean, True, n, run_s / 3600, coverage)


@dataclass
class Block:
    k: int                    # block number since the reset (1-based)
    start: datetime
    end: datetime
    x: Optional[float]
    n: int
    valid: bool


def cumulative_cycles(counter: list[tuple[datetime, float]], counter_max: Optional[int] = None,
                      prev_value: Optional[float] = None) -> list[tuple[datetime, float]]:
    """Cumulative cycles from raw counter readings (a drop is a rollover with `counter_max`, else a reset)."""
    out, acc, last = [], 0.0, prev_value
    for t, v in sorted(counter, key=lambda p: p[0]):
        if last is not None:
            d = v - last
            if d < 0:
                d = (counter_max + 1 - last + v) if counter_max else v
            acc += d
        last = v
        out.append((t, acc))
    return out


def cycle_blocks(strokes: list[tuple[datetime, float]], block: int, start_after: datetime, first_k: int = 1,
                 cum: Optional[list[tuple[datetime, float]]] = None, carry: float = 0.0
                 ) -> tuple[list[Block], Optional[dict]]:
    """Blocks of `block` cycles after `start_after` (P-1). With `cum` (cumulative cycles since `start_after`, already
    including `carry`) a block ends at the first counter reading reaching k·block; without it, every stroke
    reading is one cycle. Returns the closed blocks and the cursor to resume from."""
    st = sorted((p for p in strokes if p[0] > start_after), key=lambda p: p[0])
    blocks: list[Block] = []
    k = first_k
    if cum is None:
        prev_end = start_after
        for j in range(0, len(st) - block + 1, block):
            chunk = st[j:j + block]
            vals = [v for _, v in chunk]
            blocks.append(Block(k, prev_end, chunk[-1][0], median(vals), len(vals), True))
            prev_end = chunk[-1][0]
            k += 1
        return blocks, ({"end_at": prev_end, "carry": 0.0} if blocks else None)
    prev_end, target = start_after, float(block)
    cursor = None
    for t, c in cum:
        total = c + carry                       # cycles since the last block boundary before `start_after`
        while total >= target:
            vals = [v for ts_, v in st if prev_end < ts_ <= t]
            valid = bool(vals) and len(vals) >= MIN_BLOCK_READINGS * block
            blocks.append(Block(k, prev_end, t, median(vals) if vals else None, len(vals), valid))
            prev_end = t
            k += 1
            target += block
            cursor = {"end_at": t, "carry": total - (target - block)}
    return blocks, cursor


# ── Influx ────────────────────────────────────────────────────────────────────

async def influx_points(tenant_id: str, tag_ids: list[str], start: datetime, stop: datetime
                        ) -> dict[str, list[tuple[datetime, float]]]:
    tenant_id = ensure_uuid(tenant_id, "tenant_id")
    ids = [ensure_uuid(t, "tag_id") for t in tag_ids if t]
    out: dict[str, list[tuple[datetime, float]]] = {t: [] for t in ids}
    if not ids:
        return out
    flt = " or ".join(f'r["tag_id"] == "{t}"' for t in ids)
    flux = f"""
    from(bucket: "{settings.influx_bucket}")
      |> range(start: {_rfc(start)}, stop: {_rfc(stop)})
      |> filter(fn: (r) => r["_measurement"] == "sensors")
      |> filter(fn: (r) => r["tenant_id"] == "{tenant_id}")
      |> filter(fn: (r) => {flt})
      |> filter(fn: (r) => r["_field"] == "value")
      |> keep(columns: ["_time", "_value", "tag_id"])
    """
    tables = await influx._client.query_api().query(flux, org=settings.influx_org)
    for table in tables:
        for rec in table.records:
            tid = rec.values.get("tag_id")
            v = rec.get_value()
            if tid in out and v is not None:
                out[tid].append((rec.get_time(), float(v)))
    for v in out.values():
        v.sort(key=lambda p: p[0])
    return out


async def last_before(tenant_id: str, tag_ids: list[str], before: datetime, lookback: timedelta
                      ) -> dict[str, tuple[datetime, float]]:
    """Last reading of each tag in [before − lookback, before) (the value held at the window start)."""
    tenant_id = ensure_uuid(tenant_id, "tenant_id")
    ids = [ensure_uuid(t, "tag_id") for t in tag_ids if t]
    if not ids:
        return {}
    flt = " or ".join(f'r["tag_id"] == "{t}"' for t in ids)
    flux = f"""
    from(bucket: "{settings.influx_bucket}")
      |> range(start: {_rfc(before - lookback)}, stop: {_rfc(before)})
      |> filter(fn: (r) => r["_measurement"] == "sensors")
      |> filter(fn: (r) => r["tenant_id"] == "{tenant_id}")
      |> filter(fn: (r) => {flt})
      |> filter(fn: (r) => r["_field"] == "value")
      |> last()
    """
    tables = await influx._client.query_api().query(flux, org=settings.influx_org)
    out = {}
    for table in tables:
        for rec in table.records:
            if rec.get_value() is not None:
                out[rec.values.get("tag_id")] = (rec.get_time(), float(rec.get_value()))
    return out


async def presence(tenant_id: str, machine_id: str, start: datetime, stop: datetime, gap_s: float
                   ) -> list[Interval]:
    """Intervals in which the machine had data (any tag): each reading covers up to `gap_s` after it
    (approximated by buckets of `gap_s`: a bucket with readings covers [bucket, bucket + 2·gap_s))."""
    tenant_id = ensure_uuid(tenant_id, "tenant_id")
    machine_id = ensure_uuid(machine_id, "machine_id")
    every = max(1, int(gap_s))
    flux = f"""
    from(bucket: "{settings.influx_bucket}")
      |> range(start: {_rfc(start - timedelta(seconds=every))}, stop: {_rfc(stop)})
      |> filter(fn: (r) => r["_measurement"] == "sensors")
      |> filter(fn: (r) => r["tenant_id"] == "{tenant_id}")
      |> filter(fn: (r) => r["machine_id"] == "{machine_id}")
      |> filter(fn: (r) => r["_field"] == "value")
      |> group()
      |> aggregateWindow(every: {every}s, fn: count, createEmpty: false, timeSrc: "_start")
    """
    tables = await influx._client.query_api().query(flux, org=settings.influx_org)
    iv = []
    for table in tables:
        for rec in table.records:
            if (rec.get_value() or 0) > 0:
                t = rec.get_time()
                iv.append((t, t + timedelta(seconds=2 * every)))
    return intersect(merge(iv), [(start, stop)])
