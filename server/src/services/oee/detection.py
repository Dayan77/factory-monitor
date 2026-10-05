"""
Detecção de paradas (domain.md §4.3, kpis.md §1.8, Esclarecimentos C10/C11/C22–C24) — funções puras.

Entrada: o estado dos sinais da máquina num instante (checkpoint) + as leituras depois dele, em ordem:
  * `run`   — valor de run_state (da máquina; ou de uma posição quando a máquina não tem o seu);
  * `fault` — valor de fault_state (código; 0 = sem falha);
  * `cycle` — n ciclos revelados por um contador (modo sem run_state: intervalo entre ciclos);
  * `data`  — leitura sem mudança de ciclo (heartbeat de contador): só prova que há dados.

Regras:
  1. "Parado" = run_state = 0 **ou** fault_state ≠ 0; sem run_state, intervalo entre ciclos
     > 2 × PRI → parada de `gap − PRI` (começa em último ciclo + PRI).
  2. Sem dados: uma leitura vale até `última + machine_offline_timeout_s` (C11); depois disso o
     intervalo é "sem dados" (não é parada nem operação). Uma parada é cortada no início do buraco.
  3. Quedas < `run_state_debounce_s` não são paradas (C22). Paradas separadas por menos de
     `merge_gap_s` (10 s) de operação são fundidas (C24a): a duração vai do início da primeira ao
     fim da última e o limiar é aplicado a ela.
  4. Duração ≥ micro_stop_threshold_s → DowntimeEvent; menor → micro-parada (C22, inclusive falha
     com código: `short_fault`). A parte dentro de pausa planejada é PDOT; parada inteiramente na
     pausa não gera evento (C24b/d). Dividida por turno (regra 5).
  5. Falha (fault ≠ 0) em qualquer momento da parada → motivo sugerido EQP-* (mapa do código, regra 6),
     `fault_since` = instante em que fault_state ficou ≠ 0 (= failed_at do FailureRecord). Uma parada
     que começa `stopped` e vira `error` é **um** evento (Decisões Sprint 3 em kpis.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from .calendar import Calendar
from .intervals import Interval, intersect, normalize, subtract, total_s
from ..usage.core import split_hourly

DEFAULT_EQP_REASON = "EQP-01"


@dataclass(frozen=True)
class SignalState:
    """Estado dos sinais após a última leitura aplicada (persistido no checkpoint).

    Quando o checkpoint cai **dentro** de uma parada (máquina parada há horas, à noite), `down_since`
    é o início dessa parada e `stop_fault`/`stop_fault_since` a primeira falha vista nela até `at`:
    a próxima passada continua a mesma parada sem reler tudo desde o seu início."""
    run: Optional[float] = None
    fault: float = 0.0
    fault_since: Optional[datetime] = None
    last_cycle: Optional[datetime] = None
    last_data: Optional[datetime] = None
    at: Optional[datetime] = None          # ts of the last applied reading
    down_since: Optional[datetime] = None
    stop_fault: Optional[int] = None
    stop_fault_since: Optional[datetime] = None

    def to_json(self) -> dict:
        iso = lambda d: d.isoformat() if d else None  # noqa: E731
        return {"run": self.run, "fault": self.fault, "fault_since": iso(self.fault_since),
                "last_cycle": iso(self.last_cycle), "last_data": iso(self.last_data), "at": iso(self.at),
                "down_since": iso(self.down_since), "stop_fault": self.stop_fault,
                "stop_fault_since": iso(self.stop_fault_since)}

    @classmethod
    def from_json(cls, d: Optional[dict]) -> "SignalState":
        d = d or {}
        p = lambda k: datetime.fromisoformat(d[k]) if d.get(k) else None  # noqa: E731
        sf = d.get("stop_fault")
        return cls(run=d.get("run"), fault=float(d.get("fault") or 0.0), fault_since=p("fault_since"),
                   last_cycle=p("last_cycle"), last_data=p("last_data"), at=p("at"),
                   down_since=p("down_since"), stop_fault=int(sf) if sf is not None else None,
                   stop_fault_since=p("stop_fault_since"))


@dataclass(frozen=True)
class Reading:
    ts: datetime
    kind: str            # run | fault | cycle | data
    value: float = 0.0


@dataclass(frozen=True)
class Params:
    offline_timeout_s: float = 60.0
    debounce_s: float = 1.0
    merge_gap_s: float = 10.0
    finalize_delay_s: float = 5.0
    mode: str = "run"                     # run | cycle
    ideal_cycle_s: Optional[float] = None  # cycle mode
    stale_after_s: float = 86400.0         # a stop cut by a data gap is final after this much silence


@dataclass
class Stop:
    start: datetime
    end: Optional[datetime]               # None = still stopped at `now`
    seen_end: datetime                    # end, or `now` while open
    fault_code: Optional[int] = None
    fault_since: Optional[datetime] = None
    ended_by_nodata: bool = False
    final: bool = False
    source: str = "auto_run_state"

    @property
    def duration_s(self) -> float:
        return (self.seen_end - self.start).total_seconds()


@dataclass
class Detection:
    stops: list[Stop]
    spans: list[Interval]                 # intervals with data, up to `now`
    checkpoint: Optional[datetime]        # resume point (None = no reading yet: stay where we are)
    checkpoint_state: SignalState
    last_state: SignalState


def _step_down(points: list[tuple[datetime, bool]], end: datetime) -> list[Interval]:
    """points: (ts, is_down) change list → intervals where down, up to end."""
    out: list[Interval] = []
    cur_start: Optional[datetime] = None
    for ts, down in points:
        if ts > end:
            break
        if down and cur_start is None:
            cur_start = ts
        elif not down and cur_start is not None:
            out.append((cur_start, ts))
            cur_start = None
    if cur_start is not None and end > cur_start:
        out.append((cur_start, end))
    return out


def _attribute(fault_pts: list[tuple[datetime, float, Optional[datetime]]], start: datetime, end: datetime,
               inclusive: bool = False) -> tuple[Optional[int], Optional[datetime]]:
    """First fault (≠ 0) active at some point of [start, end) — or [start, end] — and since when."""
    for i, (ts, v, fsince) in enumerate(fault_pts):
        nxt = fault_pts[i + 1][0] if i + 1 < len(fault_pts) else None
        if v != 0 and (ts < end or (inclusive and ts <= end)) and (nxt is None or nxt > start):
            return int(v), max(fsince or ts, start)
    return None, None


def detect(initial: SignalState, readings: Iterable[Reading], now: datetime, p: Params,
           cap: Optional[datetime] = None) -> Detection:
    """Stops after `initial` (+ the stop in progress at it, `initial.down_since`).

    `cap`: the checkpoint may not move past it — readings up to `cap` are known to be complete
    (none still in flight). None = no limit (all readings given are complete)."""
    timeout = timedelta(seconds=p.offline_timeout_s)
    st = initial
    readings = sorted(readings, key=lambda r: r.ts)
    snapshots: list[SignalState] = []

    # data spans
    spans: list[Interval] = []
    span_start: Optional[datetime] = st.last_data

    def data_at(ts: datetime) -> None:
        nonlocal span_start
        if st.last_data is None or span_start is None:
            span_start = ts
        elif ts - st.last_data > timeout:
            spans.append((span_start, st.last_data + timeout))
            span_start = ts

    # change points
    t0 = st.at or (readings[0].ts if readings else now)
    run_down0 = st.run is not None and not st.run
    down_pts: list[tuple[datetime, bool]] = [(t0, run_down0 or st.fault != 0)] if p.mode == "run" else []
    # (ts, value, since): since = when the fault became non-zero (a code change keeps it)
    fault_pts: list[tuple[datetime, float, Optional[datetime]]] = [(t0, st.fault, st.fault_since)]
    cycles: list[datetime] = [st.last_cycle] if st.last_cycle else []

    for r in readings:
        data_at(r.ts)
        run, fault, fault_since, last_cycle = st.run, st.fault, st.fault_since, st.last_cycle
        if r.kind == "run":
            run = 1.0 if r.value else 0.0
        elif r.kind == "fault":
            v = float(r.value or 0.0)
            if v != 0 and fault == 0:
                fault_since = r.ts
            elif v == 0:
                fault_since = None
            fault = v
            fault_pts.append((r.ts, v, fault_since))
        elif r.kind == "cycle" and r.value > 0:
            last_cycle = r.ts
            cycles.append(r.ts)
        st = SignalState(run, fault, fault_since, last_cycle, r.ts, r.ts)
        if p.mode == "run":
            down_pts.append((r.ts, (run is not None and not run) or fault != 0))
        snapshots.append(st)

    data_end = min(now, st.last_data + timeout) if st.last_data else None
    if span_start is not None and data_end is not None and data_end > span_start:
        spans.append((span_start, data_end))
    spans = normalize(spans)
    data_now = bool(spans) and spans[-1][1] >= now

    # raw down intervals
    if p.mode == "run":
        down = _step_down(down_pts, now)
    else:
        down = _step_down([(ts, v != 0) for ts, v, _ in fault_pts], now)
        pri = timedelta(seconds=p.ideal_cycle_s or 0)
        if pri.total_seconds() > 0:
            ref = cycles[:]
            if not ref and spans:
                ref = [spans[0][0]]
            for a, b in zip(ref, ref[1:]):
                if b - a > 2 * pri:
                    down.append((a + pri, b))
            if ref and now - ref[-1] > 2 * pri:
                down.append((ref[-1] + pri, now))
    down = intersect(normalize(down), spans)

    # stops (+ debounce, + merge); a down interval starting at the checkpoint continues the stop
    # in progress there (initial.down_since)
    stops: list[Stop] = []
    resumed: Optional[Stop] = None
    for a, b in down:
        cont = initial.down_since is not None and initial.at is not None and a == t0 and not stops
        open_ = data_now and b >= now
        by_gap = not open_ and any(s_end == b for _, s_end in spans) and b < now
        s = Stop(initial.down_since if cont else a, None if open_ else b, b, ended_by_nodata=by_gap)
        if not open_ and s.duration_s < p.debounce_s:
            continue
        if stops and not stops[-1].ended_by_nodata and stops[-1].end is not None \
                and (a - stops[-1].end).total_seconds() < p.merge_gap_s:
            prev = stops[-1]
            prev.end, prev.seen_end, prev.ended_by_nodata = s.end, s.seen_end, s.ended_by_nodata
            continue
        stops.append(s)
        if cont:
            resumed = s

    # fault attribution, source, finality
    for s in stops:
        if s is resumed and initial.stop_fault is not None:
            code, since = initial.stop_fault, initial.stop_fault_since
        else:
            code, since = _attribute(fault_pts, s.start, s.seen_end)
        s.fault_code, s.fault_since = code, since
        s.source = "auto_fault" if code is not None else ("auto_run_state" if p.mode == "run" else "auto_cycle_gap")
        if s.end is None:
            s.final = False
        elif s.ended_by_nodata:
            resumed_data = any(a > s.end for a, _ in spans)
            s.final = resumed_data or (now - s.end).total_seconds() >= p.stale_after_s
        else:
            s.final = (now - s.end).total_seconds() >= p.merge_gap_s + p.finalize_delay_s

    # checkpoint: the latest reading (≤ cap) from which a later pass reproduces exactly the same stops
    cp_state = initial
    merge_gap = timedelta(seconds=p.merge_gap_s)
    pri = timedelta(seconds=p.ideal_cycle_s or 0)
    for i, snap in enumerate(snapshots):
        at = snap.at
        if cap is not None and at > cap:
            break
        if i + 1 < len(snapshots) and snapshots[i + 1].at == at:
            continue                      # resume after the last reading of an instant
        inside = next((s for s in stops if s.start <= at and (s.end is None or at < s.seen_end)), None)
        # a later down period within merge_gap of a stop that ended before `at` would merge with it
        if any(s.end is not None and s.seen_end <= at and not s.ended_by_nodata and at - s.end < merge_gap
               for s in stops):
            continue
        if inside is not None:
            raw_down = (snap.run is not None and not snap.run) or snap.fault != 0 if p.mode == "run" else True
            if not raw_down:
                continue                  # running inside a merged stop: resuming here would split it
            if inside is resumed and initial.stop_fault is not None:
                code, since = initial.stop_fault, initial.stop_fault_since
            else:
                code, since = _attribute(fault_pts, inside.start, at, inclusive=True)
            cp_state = replace(snap, down_since=inside.start, stop_fault=code, stop_fault_since=since)
        else:
            if p.mode == "cycle" and (snap.last_cycle is None or at - snap.last_cycle > pri):
                continue                  # a cycle gap starting before `at` may still turn into a stop
            cp_state = snap
    return Detection(stops, spans, cp_state.at, cp_state, st)


# ── classification against the calendar ──────────────────────────────────

@dataclass
class EventSegment:
    start: datetime
    end: Optional[datetime]
    stop_start: datetime
    shift_id: Optional[str]
    productive_day: date
    adot_s: float                         # part outside planned breaks (seen so far)
    pdot_s: float


@dataclass
class MicroPiece:
    bucket_start: datetime
    shift_slot: str
    shift_id: Optional[str]
    productive_day: Optional[date]
    loss_s: float
    count: int = 0
    short_faults: int = 0


def suggested_reason(fault_code: Optional[int], fault_map: Optional[dict]) -> Optional[str]:
    if fault_code is None:
        return None
    m = fault_map or {}
    return m.get(str(fault_code)) or m.get(fault_code) or DEFAULT_EQP_REASON


def segments(stop: Stop, cal: Calendar, now: datetime) -> list[EventSegment]:
    """Event pieces of a stop ≥ threshold: one per shift instance it overlaps, dropping pieces that
    lie entirely inside planned breaks (C24d) or outside scheduled time."""
    end = stop.seen_end
    out = []
    for inst in cal.instances(stop.start, end if end > stop.start else stop.start + timedelta(seconds=1)):
        a, b = max(stop.start, inst.start), min(end, inst.end)
        if b <= a:
            continue
        brk = inst.break_intervals()
        unplanned = subtract([(a, b)], brk)
        adot = total_s(unplanned)
        if adot <= 0:
            continue
        open_seg = stop.end is None and inst.end > end
        out.append(EventSegment(a, None if open_seg else b, stop.start, inst.shift_id, inst.productive_day,
                                adot, total_s(intersect([(a, b)], brk))))
    return out


def micro_pieces(stop: Stop, cal: Calendar) -> list[MicroPiece]:
    """Loss of a micro-stop inside scheduled, non-break time, by (hour bucket, shift instance).
    The occurrence (and short_fault) is counted on the first piece."""
    out: list[MicroPiece] = []
    for inst in cal.instances(stop.start, stop.seen_end):
        a, b = max(stop.start, inst.start), min(stop.seen_end, inst.end)
        if b <= a:
            continue
        for s, e in subtract([(a, b)], inst.break_intervals()):
            for bucket, secs in split_hourly(s, e):
                out.append(MicroPiece(bucket, inst.slot, inst.shift_id, inst.productive_day, secs))
    if out:
        out[0].count = 1
        out[0].short_faults = 1 if stop.fault_code is not None else 0
    return out


# ── kpis.md §1.8 cycle-gap classification (exemplo E4) ───────────────────

@dataclass
class GapClassification:
    speed_loss_s: float = 0.0
    micro_stop_count: int = 0
    micro_stop_s: float = 0.0
    downtime_events_s: list[float] = field(default_factory=list)


def classify_cycle_gaps(ideal_cycle_s: float, threshold_s: float, gaps_s: Iterable[float]) -> GapClassification:
    """PRI < gap ≤ 2·PRI → speed loss gap − PRI; gap > 2·PRI → stop of gap − PRI (micro if < threshold)."""
    out = GapClassification()
    for g in gaps_s:
        if g <= ideal_cycle_s:
            continue
        loss = g - ideal_cycle_s
        if g <= 2 * ideal_cycle_s:
            out.speed_loss_s += loss
        elif loss < threshold_s:
            out.micro_stop_count += 1
            out.micro_stop_s += loss
        else:
            out.downtime_events_s.append(loss)
    return out


def with_end(s: Stop, end: datetime) -> Stop:
    return replace(s, end=end, seen_end=end)
