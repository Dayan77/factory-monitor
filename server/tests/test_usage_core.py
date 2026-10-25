"""Pure usage rules: domain.md §3.3 (counter delta table), §3.4 (hours/starts), kpis.md §4.1–4.2."""
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.services.usage.core import (
    GLITCH, REBASELINE, RESET, ROLLOVER, CounterTrack, RunTrack, counter_delta, counter_step,
    life_consumed, resolve_max_rate, rul, run_step, split_hourly,
)

T0 = datetime(2026, 10, 5, 10, 0, 0, tzinfo=timezone.utc)


def s(sec: float) -> datetime:
    return T0 + timedelta(seconds=sec)


# ── §3.3 example table (binding) ────────────────────────────────────────────

@pytest.mark.parametrize("prev,cur,cmax,dt,rate,delta,event", [
    (1000, 1250, 65535, None, None, 250, None),
    (65530, 4, 65535, None, None, 10, ROLLOVER),
    (48000, 120, 65535, None, None, 120, RESET),          # 48 000 < 0.9 × 65 535 = 58 981.5
    (48000, 120, None, None, None, 120, RESET),
    (1000, 900000, 65535, 1, 2, 0, GLITCH),               # 899 000 > 2 × 1 × 1.5 + 10 = 13
])
def test_domain_counter_table(prev, cur, cmax, dt, rate, delta, event):
    assert counter_delta(prev, cur, cmax, dt, rate) == (delta, event)


def test_rollover_threshold_is_inclusive_at_90pct():
    cmax = 1000
    assert counter_delta(900, 5, cmax) == ((1000 - 900) + 5 + 1, ROLLOVER)
    assert counter_delta(899, 5, cmax) == (5, RESET)


def test_32bit_rollover():
    assert counter_delta(4_294_967_290, 3, 4_294_967_295) == (9, ROLLOVER)


def test_kpis_md_counter_delta_case_through_engine():
    """kpis.md `counter_delta` case (the same as the §3.3 table) run through the real engine."""
    text = (Path(__file__).resolve().parents[2] / "docs" / "kpis.md").read_text(encoding="utf-8")
    cases = json.loads(re.search(r"```json kpi-test-cases\n(.*?)\n```", text, re.S).group(1))
    by_id = {c["id"]: c for c in cases}
    c = by_id["counter_delta"]
    res = [counter_delta(x["prev"], x["cur"], x["counter_max"], x.get("dt_s"), x.get("max_rate_per_s"))
           for x in c["input"]["cases"]]
    assert [r[0] for r in res] == c["expected"]["deltas"]
    assert [r[1] for r in res] == c["expected"]["events"]

    for cid in ("life_consumed", "life_consumed_multi_measure"):
        i, e = by_id[cid]["input"], by_id[cid]["expected"]
        life, measure = life_consumed(i["nominal"], i["usage_since_install"])
        assert life == pytest.approx(e["life"], abs=1e-9) and measure == e["measure"]

    i, e = by_id["rul_projection"]["input"], by_id["rul_projection"]["expected"]
    r = rul(date.fromisoformat(i["today"]), i["nominal"], i["usage_since_install"], i["usage_last_14_days"])
    assert (r.remaining, r.rate_per_day, r.days, r.date.isoformat(), r.measure) == (
        e["remaining"], e["rate_per_day"], e["days"], e["date"], e["measure"])
    assert r.low_confidence is False


# ── counter_step: state machine around the table ────────────────────────────

def test_first_reading_is_baseline():
    r = counter_step(None, 5000, s(0), 65535, 2)
    assert r.delta == 0 and r.track == CounterTrack(5000, s(0))


def test_duplicate_reading_is_idempotent():
    tr = CounterTrack(1000, s(0))
    r = counter_step(tr, 1000, s(0), 65535, 2)
    assert r.delta == 0 and r.ignored == "duplicate" and r.track == tr


def test_out_of_order_reading_is_ignored():
    tr = CounterTrack(1000, s(10))
    r = counter_step(tr, 990, s(5), 65535, 2)
    assert r.delta == 0 and r.ignored == "out_of_order" and r.track == tr


def test_table_through_counter_step_with_enough_dt():
    # row 1: 250 counts need dt ≥ (250−10)/(2×1.5) = 80 s at 2/s
    r = counter_step(CounterTrack(1000, s(0)), 1250, s(100), 65535, 2)
    assert (r.delta, r.event) == (250, None)
    r = counter_step(CounterTrack(65530, s(0)), 4, s(10), 65535, 2)
    assert (r.delta, r.event) == (10, ROLLOVER)
    r = counter_step(CounterTrack(48000, s(0)), 120, s(60), 65535, 2)
    assert (r.delta, r.event) == (120, RESET)


def test_glitch_keeps_prev_then_normal_reading_counts_from_prev():
    tr = CounterTrack(1000, s(0))
    g = counter_step(tr, 900000, s(1), 65535, 2)
    assert (g.delta, g.event) == (0, GLITCH)
    assert g.track.last_value == 1000 and g.track.last_ts == s(0)     # prev kept
    n = counter_step(g.track, 1004, s(2), 65535, 2)
    assert (n.delta, n.event) == (4, None)
    assert n.track.pending_value is None


def test_confirmed_jump_rebaselines_without_counting_the_jump():
    tr = CounterTrack(1000, s(0))
    g = counter_step(tr, 900000, s(1), None, 2)
    r = counter_step(g.track, 900003, s(2), None, 2)
    assert (r.delta, r.event) == (3, REBASELINE)
    assert r.track == CounterTrack(900003, s(2))


def test_invalid_values_are_ignored():
    tr = CounterTrack(10, s(0))
    assert counter_step(tr, -5, s(1), None, 2).ignored == "invalid"
    assert counter_step(tr, float("nan"), s(1), None, 2).ignored == "invalid"


def test_resolve_max_rate():
    assert resolve_max_rate(5.0, 2.0, 1, 10) == 5.0               # tag override
    assert resolve_max_rate(None, 2.0, 1, 10) == 1.0              # 2 / ideal_cycle_time
    assert resolve_max_rate(None, 2.0, 3, 10) == 3.0              # × cycles_per_part
    assert resolve_max_rate(None, None, 1, 10) == 10              # server default


# ── §3.4 operating time and starts ──────────────────────────────────────────

def _run(seq, timeout=60, debounce=1.0):
    track, secs, starts = None, 0.0, 0
    for t, v in seq:
        r = run_step(track, v, s(t), timeout, debounce)
        if not r.ignored:
            track = r.track
        secs += sum((b - a).total_seconds() for a, b in r.intervals)
        starts += r.starts
    return secs, starts, track


def test_hours_are_sum_of_on_intervals_with_heartbeats():
    # on at 0, heartbeats every 30 s, off at 100, on at 200, off at 260
    secs, starts, _ = _run([(0, 1), (30, 1), (60, 1), (90, 1), (100, 0), (200, 1), (230, 1), (260, 0)])
    assert secs == 100 + 60
    assert starts == 1          # the first reading is a baseline; 0→1 at 200 is a start


def test_starts_count_rising_edges_after_baseline_off():
    secs, starts, _ = _run([(0, 0), (10, 1), (20, 0), (30, 1), (40, 0)])
    assert starts == 2 and secs == 20


def test_debounce_short_off_is_not_a_new_start():
    # 0 lasting 0.5 s (< 1 s debounce) → no new start; the 0.5 s are not operating time
    secs, starts, _ = _run([(0, 0), (10, 1), (20, 0), (20.5, 1), (30, 0)])
    assert starts == 1
    assert secs == pytest.approx(10 + 9.5)
    # a 0 of exactly 1 s is a real stop → new start
    _, starts2, _ = _run([(0, 0), (10, 1), (20, 0), (21, 1), (30, 0)])
    assert starts2 == 2


def test_debounce_configurable():
    _, starts, _ = _run([(0, 0), (10, 1), (20, 0), (23, 1)], debounce=5)
    assert starts == 1


def test_data_gap_is_not_extrapolated():
    # on at 0, then silence until 1000 s (timeout 60 s): only 60 s credited; no new start
    secs, starts, _ = _run([(0, 0), (1, 1), (1000, 1), (1010, 0)])
    assert secs == 60 + 10
    assert starts == 1


def test_run_duplicate_and_out_of_order_ignored():
    tr = RunTrack(True, s(10))
    assert run_step(tr, 1, s(10), 60, 1).ignored == "duplicate"
    assert run_step(tr, 0, s(5), 60, 1).ignored == "out_of_order"


def test_split_hourly():
    a = datetime(2026, 10, 5, 9, 59, 30, tzinfo=timezone.utc)
    b = datetime(2026, 10, 5, 11, 0, 30, tzinfo=timezone.utc)
    pieces = split_hourly(a, b)
    assert [p[1] for p in pieces] == [30, 3600, 30]
    assert pieces[1][0] == datetime(2026, 10, 5, 10, tzinfo=timezone.utc)


# ── kpis.md §4.1 / §4.2 edges ───────────────────────────────────────────────

def test_life_without_nominal_is_none_not_zero():
    assert life_consumed({}, {"cycles": 10}) == (None, None)
    assert life_consumed({"cycles": 100}, {}) == (0.0, "cycles")


def test_life_not_truncated_above_one():
    assert life_consumed({"cycles": 100}, {"cycles": 150}) == (1.5, "cycles")


def test_rul_edges():
    today = date(2026, 10, 5)
    assert rul(today, {}, {}, {}).reason == "no_nominal_life"
    assert rul(today, {"cycles": 100}, {"cycles": 10}, {"cycles": 0}).reason == "no_recent_usage"
    r = rul(today, {"cycles": 100}, {"cycles": 10}, {"cycles": 50}, history_days=2)
    assert r.reason == "insufficient_data" and r.date is None
    r = rul(today, {"cycles": 100}, {"cycles": 10}, {"cycles": 30}, history_days=5)
    assert r.low_confidence and r.rate_per_day == 6 and r.days == 15
    r = rul(today, {"cycles": 100}, {"cycles": 120}, {"cycles": 30})
    assert r.overdue and r.days == 0 and r.date == today
    # earliest measure wins
    r = rul(today, {"cycles": 1000, "hours": 100}, {"cycles": 0, "hours": 0}, {"cycles": 140, "hours": 140})
    assert r.measure == "hours" and r.days == 10


def test_estimated_cycles_example():
    """domain.md §2: 700 parts, cycles_per_part = 2 → 1 400 estimated cycles (via attribution)."""
    from src.models.tag import SemanticRole
    from src.services.usage.topology import MachineInfo, PositionInfo, TagInfo, Topology

    topo = Topology()
    topo.machines["m"] = MachineInfo(id="m", tenant_id="t", ideal_cycle_time_s=None, has_total_tag=True,
                                     position_ids=["grip", "cyl"])
    topo.positions["grip"] = PositionInfo("grip", "m", 2, has_cycle_tag=False, has_run_tag=False)
    topo.positions["cyl"] = PositionInfo("cyl", "m", 1, has_cycle_tag=True, has_run_tag=False)
    tag = TagInfo("tg", "t", "m", None, SemanticRole.part_counter_total, None, None)
    out = topo.counter_contributions(tag, 700, T0)
    got = {(c.scope, c.entity_id, c.measure): c.amount for c in out}
    assert got == {("machine", "m", "total"): 700, ("position", "grip", "cycles"): 1400}
