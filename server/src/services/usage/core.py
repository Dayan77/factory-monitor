"""
Pure usage-processing rules (no I/O) — domain.md §3.3 (counter delta), §3.4 (operating time and
starts) and kpis.md §4.1–4.2 (life consumed, RUL). The incremental MQTT pipeline
(`processor.py`) and the rebuild-from-history (`recompute.py`) both run readings through
these same functions, which is what makes "recompute == incremental" hold.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Mapping, Optional

# ── §3.3 counter delta ──────────────────────────────────────────────────────

ROLLOVER = "rollover"
RESET = "counter_reset"
GLITCH = "counter_glitch"
REBASELINE = "counter_rebaseline"   # extension: see counter_step()
ROLLOVER_THRESHOLD = 0.9


def counter_delta(prev: int, cur: int, counter_max: Optional[int],
                  dt_s: Optional[float] = None, max_rate_per_s: Optional[float] = None
                  ) -> tuple[int, Optional[str]]:
    """Rules 1–4 of domain.md §3.3. Returns (delta, event) with event in
    {None, "rollover", "counter_reset", "counter_glitch"}.

    The plausibility filter (rule 4) runs only when both dt_s and max_rate_per_s are known.
    """
    if cur >= prev:
        delta, event = cur - prev, None
    elif counter_max is not None and prev >= ROLLOVER_THRESHOLD * counter_max:
        delta, event = (counter_max - prev) + cur + 1, ROLLOVER
    else:
        delta, event = cur, RESET
    if dt_s is not None and max_rate_per_s is not None:
        if delta > plausible_bound(max_rate_per_s, dt_s):
            return 0, GLITCH
    return delta, event


def plausible_bound(max_rate_per_s: float, dt_s: float) -> float:
    return max_rate_per_s * max(dt_s, 0.0) * 1.5 + 10


@dataclass(frozen=True)
class CounterTrack:
    """Last accepted reading of a counter tag (persisted in tag_states)."""
    last_value: int
    last_ts: datetime
    pending_value: Optional[int] = None   # value rejected as glitch, awaiting confirmation
    pending_ts: Optional[datetime] = None


@dataclass(frozen=True)
class CounterResult:
    delta: int
    track: CounterTrack
    event: Optional[str] = None      # rollover | counter_reset | counter_glitch | counter_rebaseline
    ignored: Optional[str] = None    # duplicate | out_of_order | invalid
    prev_value: Optional[int] = None


def counter_step(track: Optional[CounterTrack], value: float, ts: datetime,
                 counter_max: Optional[int], max_rate_per_s: Optional[float]) -> CounterResult:
    """Applies one counter reading to the tracked state.

    * First reading: baseline only (delta 0) — what was counted before is unknown.
    * Rule 5: same timestamp and value → duplicate, delta 0 (idempotent). A reading older than
      the last accepted one is ignored (the counter is cumulative: the next reading carries
      the counts, nothing is lost).
    * Rules 1–4 via counter_delta(). A glitch keeps `prev` (rule 4).
    * Extension (not in the spec table): a glitched value is remembered as `pending`. If the
      next reading is plausible relative to *that* value (cur ≥ pending and within the
      plausibility bound) the counter really jumped (PLC swapped/preset): we re-baseline on it,
      counting only cur − pending (the jump itself is never counted) and record
      `counter_rebaseline`. Without this a legit jump would freeze counting forever.
    """
    if value is None or not math.isfinite(value) or value < 0:
        return CounterResult(0, track, ignored="invalid") if track else CounterResult(
            0, CounterTrack(0, ts), ignored="invalid")
    cur = int(round(value))
    if track is None:
        return CounterResult(0, CounterTrack(cur, ts))
    if ts < track.last_ts:
        return CounterResult(0, track, ignored="out_of_order")
    if ts == track.last_ts and cur == track.last_value:
        return CounterResult(0, track, ignored="duplicate")

    prev = track.last_value
    dt_s = (ts - track.last_ts).total_seconds()
    delta, event = counter_delta(prev, cur, counter_max, dt_s, max_rate_per_s)
    if event != GLITCH:
        return CounterResult(delta, CounterTrack(cur, ts), event=event, prev_value=prev)

    # Glitch: confirm a pending jump, or remember this value as pending.
    if (track.pending_value is not None and track.pending_ts is not None
            and ts > track.pending_ts and cur >= track.pending_value
            and max_rate_per_s is not None
            and cur - track.pending_value <= plausible_bound(
                max_rate_per_s, (ts - track.pending_ts).total_seconds())):
        return CounterResult(cur - track.pending_value, CounterTrack(cur, ts),
                             event=REBASELINE, prev_value=prev)
    return CounterResult(0, replace(track, pending_value=cur, pending_ts=ts),
                         event=GLITCH, prev_value=prev)


def resolve_max_rate(tag_max_rate: Optional[float], ideal_cycle_time_s: Optional[float],
                     counts_per_cycle: float, default_rate: Optional[float]) -> Optional[float]:
    """max_rate (counts/s) for the plausibility filter: tag override, else 2 / ideal_cycle_time
    (× counts per machine cycle, e.g. cycles_per_part for a position), else the server default."""
    if tag_max_rate is not None and tag_max_rate > 0:
        return float(tag_max_rate)
    if ideal_cycle_time_s is not None and ideal_cycle_time_s > 0:
        return 2.0 / ideal_cycle_time_s * max(counts_per_cycle, 1)
    return default_rate


# ── §3.4 operating time and starts ──────────────────────────────────────────

@dataclass(frozen=True)
class RunTrack:
    state: bool
    last_ts: datetime
    off_since: Optional[datetime] = None   # start of the current 0 (debounce)


@dataclass(frozen=True)
class RunResult:
    track: RunTrack
    intervals: tuple[tuple[datetime, datetime], ...] = ()
    starts: int = 0
    ignored: Optional[str] = None
    changed: bool = False


def run_step(track: Optional[RunTrack], value: float, ts: datetime,
             offline_timeout_s: float, debounce_s: float) -> RunResult:
    """Applies one run_state reading.

    * Operating time = intervals with run_state = 1. The interval since the previous reading is
      credited when the state was 1, but never beyond last_seen + offline_timeout (no
      extrapolation over a data gap).
    * Starts = rising edges 0→1. A 0 shorter than debounce_s does not create a new start.
      The first reading is a baseline (no start); a 1 after a data gap that started at 1 is not
      a new start (the state during the gap is unknown).
    """
    on = bool(value) and value == value  # NaN → False
    if track is None:
        return RunResult(RunTrack(on, ts, None if on else ts), changed=True)
    if ts < track.last_ts:
        return RunResult(track, ignored="out_of_order")
    if ts == track.last_ts and on == track.state:
        return RunResult(track, ignored="duplicate")

    intervals: tuple = ()
    if track.state:
        end = min(ts, track.last_ts + timedelta(seconds=offline_timeout_s))
        if end > track.last_ts:
            intervals = ((track.last_ts, end),)

    starts = 0
    off_since = track.off_since
    if on and not track.state:
        short_off = off_since is not None and (ts - off_since).total_seconds() < debounce_s
        starts = 0 if short_off else 1
        off_since = None
    elif not on and track.state:
        off_since = ts
    elif on:
        off_since = None
    return RunResult(RunTrack(on, ts, off_since), intervals, starts, changed=on != track.state)


def split_hourly(start: datetime, end: datetime) -> list[tuple[datetime, float]]:
    """Splits [start, end) into (hour_bucket_start_utc, seconds) pieces."""
    out = []
    cur = start
    while cur < end:
        b = hour_floor(cur)
        nxt = min(end, b + timedelta(hours=1))
        out.append((b, (nxt - cur).total_seconds()))
        cur = nxt
    return out


def hour_floor(ts: datetime) -> datetime:
    ts = ts.astimezone(timezone.utc)
    return ts.replace(minute=0, second=0, microsecond=0)


# ── kpis.md §4.1 life consumed / §4.2 RUL ───────────────────────────────────

# nominal_life keys (API) → usage measure keys used in usage_since_install dicts
LIFE_MEASURES = ("cycles", "hours", "starts")


def life_consumed(nominal: Mapping[str, float], usage: Mapping[str, float]
                  ) -> tuple[Optional[float], Optional[str]]:
    """max over measures with nominal life of usage/L. (None, None) when no nominal life
    (reason no_nominal_life). Not truncated above 1."""
    best: tuple[Optional[float], Optional[str]] = (None, None)
    for m, L in (nominal or {}).items():
        if L is None or not isinstance(L, (int, float)) or L <= 0:
            continue
        life = float(usage.get(m, 0.0) or 0.0) / float(L)
        if best[0] is None or life > best[0]:
            best = (life, m)
    return best


@dataclass(frozen=True)
class Rul:
    date: Optional[date]
    days: Optional[int]
    measure: Optional[str]
    remaining: Optional[float] = None
    rate_per_day: Optional[float] = None
    reason: Optional[str] = None       # no_nominal_life | no_recent_usage | insufficient_data
    low_confidence: bool = False
    overdue: bool = False


RUL_WINDOW_DAYS = 14
RUL_MIN_DAYS = 3


def rul(today: date, nominal: Mapping[str, float], usage: Mapping[str, float],
        usage_window: Mapping[str, float], history_days: float = RUL_WINDOW_DAYS) -> Rul:
    """kpis.md §4.2: rate = usage over the last min(14, history_days) calendar days / days;
    days = ceil(remaining / rate); date = today + days; RUL = measure with the earliest date."""
    measures = [m for m, L in (nominal or {}).items()
                if isinstance(L, (int, float)) and L is not None and L > 0]
    if not measures:
        return Rul(None, None, None, reason="no_nominal_life")
    window = min(float(RUL_WINDOW_DAYS), float(history_days))
    if window < RUL_MIN_DAYS:
        return Rul(None, None, None, reason="insufficient_data", low_confidence=True)
    low_conf = window < RUL_WINDOW_DAYS
    best: Optional[Rul] = None
    for m in measures:
        remaining = float(nominal[m]) - float(usage.get(m, 0.0) or 0.0)
        rate = float(usage_window.get(m, 0.0) or 0.0) / window
        if remaining <= 0:
            cand = Rul(today, 0, m, remaining, rate, low_confidence=low_conf, overdue=True)
        elif rate <= 0:
            continue
        else:
            days = math.ceil(remaining / rate - 1e-9)
            cand = Rul(today + timedelta(days=days), days, m, remaining, rate, low_confidence=low_conf)
        # C18 / S4-12: fewest days; tie → the measure with the larger % of life
        if best is None or cand.days < best.days or (
                cand.days == best.days
                and float(usage.get(m, 0.0) or 0.0) / float(nominal[m])
                > float(usage.get(best.measure, 0.0) or 0.0) / float(nominal[best.measure])):
            best = cand
    if best is None:
        return Rul(None, None, None, reason="no_recent_usage", low_confidence=low_conf)
    return best
