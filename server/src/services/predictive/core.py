"""Pure, deterministic core of the predictive module (docs/predictive.md P-2 … P-8).

Everything here is a function of its inputs (no database, no clock): the condition-feature series of one
(position, signal) since a baseline reset, the reset itself, the human/workflow events on its alerts and the usage
rate. `fold_segment` is the fold of P-4 ("o estado é uma dobra sobre a série"): recomputing it from the same inputs
gives the same points, baseline and alerts (P-12 recompute).

Conventions:
* Windows carry `x = None` when invalid (P-1): they produce a point `{"valid": False}` and touch no state.
* Direction `down` is handled by working on `y = −x` (z, limits, slope and RUL then all read "bigger = worse"); the
  points report `x` and the slope in the signal's own units.
* Alerts opened in a segment are keyed `w<opened_window>`; an alert still not closed when the segment starts is
  passed in as a carry-in with its own key (the database id).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Callable, Iterable, Optional
from zoneinfo import ZoneInfo

from .defaults import DEFAULTS, SEV_RANK

EPS = 1e-9
ROBUST_K = 1.4826


# ── inputs ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RuleCfg:
    code: str
    type: str                 # threshold | pct_of_baseline | rate_of_change
    value: float
    sustained_for: int = 2
    severity: str = "warning"
    enabled: bool = True


@dataclass
class MonitorCfg:
    feature: str
    direction: str = "up"                 # up | down
    warmup_windows: int = 1
    failure_limit: Optional[float] = None
    failure_limit_relative: bool = False  # True: failure_limit × m0
    sensor_resolution: float = 0.0
    rules: list[RuleCfg] = field(default_factory=list)
    params: dict = field(default_factory=dict)   # overrides of DEFAULTS
    label: str = ""                       # position name, for the explanation text
    unit: str = ""

    def p(self, key: str):
        return self.params.get(key, DEFAULTS[key])

    @property
    def sign(self) -> float:
        return -1.0 if self.direction == "down" else 1.0


@dataclass(frozen=True)
class WindowIn:
    i: int
    end: datetime
    x: Optional[float]                  # None = invalid window
    u: float = 0.0                      # usage of the position at the window end (cycles | operating hours)
    start: Optional[datetime] = None


@dataclass(frozen=True)
class ExtEvent:
    """Lifecycle event not produced by the fold (user, work order, failure, condition plan). Applied after every
    window whose end ≤ `at`."""
    at: datetime
    alert_key: str
    action: str                          # acknowledge | convert | unconvert | close
    resolution: Optional[str] = None
    source: Optional[str] = None         # user | work_order | failure | plan (who closed: compared by recompute)


@dataclass
class CarryAlert:
    key: str
    state: str
    severity: str
    opened_at: datetime
    opened_window: Optional[int]
    acknowledged: bool = False
    triggers_seen: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    escalations: dict = field(default_factory=dict)
    escalation_windows: dict = field(default_factory=dict)
    first_triggers: list = field(default_factory=list)


RateFn = Callable[[datetime], Optional[tuple[float, float, float]]]
PlanFn = Callable[[date, float, Optional[float]], Optional[date]]   # (today, remaining, 14-day rate)


# ── statistics ────────────────────────────────────────────────────────────────

def mad(values: list[float]) -> float:
    m = median(values)
    return median([abs(v - m) for v in values])


def ols(us: list[float], xs: list[float]) -> Optional[tuple[float, float, float]]:
    """Least squares x = a + b·u → (a, b, SE(b)) (P-5). None when u does not vary."""
    n = len(us)
    mu, mx = sum(us) / n, sum(xs) / n
    suu = sum((u - mu) ** 2 for u in us)
    if suu <= 0 or n < 3:
        return None
    b = sum((u - mu) * (x - mx) for u, x in zip(us, xs)) / suu
    a = mx - b * mu
    sse = sum((x - (a + b * u)) ** 2 for u, x in zip(us, xs))
    se = math.sqrt(sse / (n - 2) / suu)
    return a, b, se


def ewma_limit(lam: float, L: float) -> float:
    return L * math.sqrt(lam / (2 - lam))


def compute_baseline(values: list[float], frac: float, resolution: float = 0.0) -> dict:
    """P-2 step 3: m0 = median, MAD, σ = max(1.4826·MAD, σ_floor), σ_floor = max(frac·|m0|, resolution)."""
    m0 = median(values)
    d = mad(values)
    floor = max(frac * abs(m0), resolution or 0.0)
    return {"m0": m0, "mad": d, "sigma": max(ROBUST_K * d, floor), "sigma_floor": floor}


# ── P-6/P-7: condition RUL ────────────────────────────────────────────────────

def project_with_plan(today: date, remaining: float, plan: dict[date, float], horizon_end: Optional[date],
                      fallback_rate: Optional[float]) -> Optional[date]:
    """P-7: first day whose cumulative planned usage ≥ remaining (accumulation starts the day after today); after
    the plan's horizon the 14-day rate continues."""
    if remaining <= EPS:
        return today
    acc, d = 0.0, today
    last = horizon_end or (max(plan) if plan else today)
    while d < last:
        d += timedelta(days=1)
        acc += float(plan.get(d, 0.0))
        if acc >= remaining - EPS:
            return d
    if not fallback_rate or fallback_rate <= 0:
        return None
    return d + timedelta(days=math.ceil((remaining - acc) / fallback_rate - EPS))


def condition_rul(cfg: MonitorCfg, last: list[WindowIn], ys: list[float], m0: float, end: datetime, tz: ZoneInfo,
                  rate_fn: Optional[RateFn], plan_fn: Optional[Callable] = None) -> dict:
    """P-5/P-6/P-7 on the last K valid windows since the reset (y = sign·x). Values are reported in signal units."""
    s = cfg.sign
    K = cfg.p("trend_windows")
    lf = None
    if cfg.failure_limit is not None:
        lf = cfg.failure_limit * (m0 if cfg.failure_limit_relative else 1.0)
    out: dict = {"failure_limit": lf}
    if len(last) < K:
        return {**out, "rul_days": None, "reason": "insufficient_data"}
    fit = ols([w.u for w in last], ys)
    if fit is None:
        return {**out, "rul_days": None, "reason": "insufficient_data"}
    a, b, se = fit
    u_now = last[-1].u
    yhat = a + b * u_now
    out.update(slope=s * b, slope_se=se, x_fit=s * yhat)
    if lf is None:
        return {**out, "rul_days": None, "reason": "no_failure_limit"}
    ly = s * lf
    today = end.astimezone(tz).date()
    out["today"] = today.isoformat()
    if yhat >= ly:
        return {**out, "rul_usage": 0.0, "rul_usage_low": 0.0, "rul_usage_high": 0.0, "rul_days": 0,
                "rul_days_low": 0, "rul_days_high": 0, "projected_date": today.isoformat(),
                "projected_date_low": today.isoformat(), "projected_date_high": today.isoformat(),
                "limit_reached": True}
    if b <= 0 or (se > 0 and b / se <= cfg.p("trend_t_min")):
        return {**out, "rul_days": None, "reason": "no_significant_trend"}
    rem = ly - yhat
    ru, rl = rem / b, rem / (b + 2 * se)
    rh = rem / (b - 2 * se) if b - 2 * se > 0 else None
    out.update(rul_usage=ru, rul_usage_low=rl, rul_usage_high=rh)
    rates = rate_fn(end) if rate_fn else None
    if rate_fn is not None and rates is None:
        return {**out, "rul_days": None, "reason": "insufficient_data", "low_confidence": True}
    if not rates or rates[0] <= 0:
        return {**out, "rul_days": None, "reason": "no_usage_rate"}
    r, r_lo, r_hi = rates
    wdays = float(getattr(rates, "days", 14.0))
    out.update(rate_per_day=r, rate_low=r_lo, rate_high=r_hi, rate_source="history_14d", rate_window_days=wdays,
               low_confidence=wdays < 14)
    d = math.ceil(ru / r - EPS)
    d_lo = math.floor(rl / r_hi + EPS) if r_hi > 0 else None
    d_hi = math.ceil(rh / r_lo - EPS) if (rh is not None and r_lo > 0) else None
    pd_ = today + timedelta(days=d)
    pd_lo = today + timedelta(days=d_lo) if d_lo is not None else None
    pd_hi = today + timedelta(days=d_hi) if d_hi is not None else None
    if plan_fn is not None:
        planned = plan_fn(today, ru, r)
        if planned is not None:
            out["rate_source"] = "production_plan"
            out["projected_date_history_rate"] = pd_.isoformat()
            pd_ = planned
            d = (pd_ - today).days
            lo = plan_fn(today, rl, r)
            if lo is not None:
                pd_lo, d_lo = lo, (lo - today).days
            hi = plan_fn(today, rh, r) if rh is not None else None
            if hi is not None:
                pd_hi, d_hi = hi, (hi - today).days
    out.update(rul_days=d, rul_days_low=d_lo, rul_days_high=d_hi, projected_date=pd_.isoformat(),
               projected_date_low=pd_lo.isoformat() if pd_lo else None,
               projected_date_high=pd_hi.isoformat() if pd_hi else None)
    return out


# ── the fold ──────────────────────────────────────────────────────────────────

def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class FoldResult:
    points: list[dict]
    baseline: Optional[dict]
    alerts: list[dict]
    state: dict
    learning: dict
    unmatched_events: list[ExtEvent]


class _Alert(dict):
    """Mutable alert record of the fold."""


def severity_of(cfg: MonitorCfg, trig: Iterable[str]) -> str:
    """P-8: statistical detectors alone → watch; engineering rules → their severity; RUL ≤ 30 d → warning;
    days_low ≤ 7 → critical."""
    rules = {r.code: r for r in cfg.rules}
    sev = "watch"
    for t in trig:
        if t == "RUL_CRITICAL":
            return "critical"
        if t == "RUL_WARNING":
            sev = max(sev, "warning", key=SEV_RANK.get)
        r = rules.get(t)
        if r is not None:
            if r.severity == "critical":
                return "critical"
            sev = max(sev, r.severity, key=SEV_RANK.get)
    return sev


def fold_segment(cfg: MonitorCfg, windows: list[WindowIn], *, warmup: bool, tz: ZoneInfo,
                 rate_fn: Optional[RateFn] = None, plan_fn: Optional[Callable] = None,
                 events: Iterable[ExtEvent] = (), carry: Optional[CarryAlert] = None,
                 snooze: Optional[tuple[datetime, str]] = None) -> FoldResult:
    """One baseline segment: warm-up → learning → monitoring, with detectors, rules, RUL and alert lifecycle."""
    P = cfg.p
    lam, L, k, h, clip = P("ewma_lambda"), P("ewma_L"), P("cusum_k"), P("cusum_h"), P("z_clip")
    e_lim = ewma_limit(lam, L)
    nbase, span_h = P("baseline_min_windows"), P("baseline_min_span_h")
    W, rz_lim = P("rolling_window"), P("rolling_z_limit")
    an_min, an_of = P("anomaly_min"), P("anomaly_of")
    K = P("trend_windows")
    frac = P("sigma_floor_frac")
    auto_close = P("auto_close_after_windows")
    snooze_days = P("false_alarm_snooze_days")
    s = cfg.sign
    rules = [r for r in cfg.rules if r.enabled]

    evs = sorted(events, key=lambda e: e.at)
    ev_pos = 0
    unmatched: list[ExtEvent] = []

    skip = cfg.warmup_windows if warmup else 0
    state = "learning"
    learn: list[WindowIn] = []
    since: list[WindowIn] = []       # valid windows since the reset (after warm-up)
    ys_since: list[float] = []
    base: Optional[dict] = None
    m0y = sig = None
    E = S = 0.0
    arm = {r.code: {"armed": False, "hit": 0, "miss": 0} for r in rules}
    points: list[dict] = []
    alerts: list[_Alert] = []
    by_key: dict[str, _Alert] = {}
    open_alert: Optional[_Alert] = None
    snooze_until, snooze_rank = (snooze[0], SEV_RANK[snooze[1]]) if snooze else (None, -1)
    anomalies: list[bool] = []       # per monitoring window with rolling z

    if carry is not None and carry.state != "closed":
        open_alert = _Alert(key=carry.key, opened_at=_iso(carry.opened_at), opened_window=carry.opened_window,
                            first_triggers=list(carry.first_triggers), severity=carry.severity,
                            escalations=dict(carry.escalations), escalation_windows=dict(carry.escalation_windows),
                            state=carry.state, triggers_seen=dict(carry.triggers_seen),
                            evidence={k_: dict(v) for k_, v in carry.evidence.items()},
                            acknowledged=carry.acknowledged, quiet=0, carried=True, auto=[], rul=None,
                            point=None, closed_at=None, resolution=None, closed_by=None)
        alerts.append(open_alert)
        by_key[carry.key] = open_alert

    def apply_event(ev: ExtEvent) -> None:
        nonlocal open_alert, snooze_until, snooze_rank
        a = by_key.get(ev.alert_key)
        if a is None or a["state"] == "closed":
            unmatched.append(ev)
            return
        if ev.action == "acknowledge" and a["state"] == "open":
            a["state"], a["acknowledged"] = "acknowledged", True
        elif ev.action == "convert" and a["state"] in ("open", "acknowledged"):
            a["state"] = "converted"
            a["acknowledged"] = True
        elif ev.action == "unconvert" and a["state"] == "converted":
            a["state"] = "acknowledged"
        elif ev.action == "close":
            a["state"], a["resolution"], a["closed_at"] = "closed", ev.resolution, _iso(ev.at)
            a["closed_by"] = ev.source
            if ev.resolution == "false_alarm":
                snooze_until, snooze_rank = ev.at + timedelta(days=snooze_days), SEV_RANK[a["severity"]]
            if open_alert is a:
                open_alert = None
        else:
            unmatched.append(ev)

    def flush_events(before: Optional[datetime]) -> None:
        nonlocal ev_pos
        while ev_pos < len(evs) and (before is None or evs[ev_pos].at < before):
            apply_event(evs[ev_pos])
            ev_pos += 1

    def evidence(a: _Alert, code: str, w: WindowIn, value, limit, sev) -> None:
        ev = a["evidence"].get(code)
        if ev is None:
            a["evidence"][code] = {"source": code, "first_i": w.i, "last_i": w.i, "first_at": _iso(w.end),
                                   "last_at": _iso(w.end), "value": value, "limit": limit, "severity": sev}
        else:
            ev.update(last_i=w.i, last_at=_iso(w.end), value=value, limit=limit)
        a["triggers_seen"].setdefault(code, w.i)

    for w in windows:
        flush_events(w.end)
        if w.x is None:
            points.append({"i": w.i, "end": _iso(w.end), "valid": False})
            continue
        if skip > 0:
            skip -= 1
            points.append({"i": w.i, "end": _iso(w.end), "valid": True, "x": w.x, "u": w.u, "phase": "warmup"})
            continue
        x = w.x
        y = s * x
        pt: dict = {"i": w.i, "end": _iso(w.end), "valid": True, "x": x, "u": w.u}
        trig: list[str] = []
        info: dict[str, tuple] = {}            # code -> (value, limit)

        def rule_step(r: RuleCfg, hit: bool) -> bool:
            st = arm[r.code]
            if not st["armed"]:
                st["hit"] = st["hit"] + 1 if hit else 0
                if st["hit"] >= r.sustained_for:
                    st["armed"], st["miss"] = True, 0
            else:
                st["miss"] = 0 if hit else st["miss"] + 1
                if st["miss"] >= r.sustained_for:          # P-3: disarm after n valid windows without the condition
                    st["armed"], st["hit"] = False, 0
            return st["armed"]

        for r in rules:                                    # absolute limits are active even while learning (P-3)
            if r.type == "threshold":
                if rule_step(r, y > s * r.value):
                    trig.append(r.code)
                    info[r.code] = (x, r.value)

        if state == "learning":
            learn.append(w)
            since.append(w)
            ys_since.append(y)
            pt["phase"] = "baseline"
            first_start = learn[0].start or learn[0].end
            if len(learn) >= nbase and (w.end - first_start).total_seconds() / 3600 >= span_h - EPS:
                vals = [q.x for q in learn]
                b = compute_baseline(vals, frac, cfg.sensor_resolution)
                base = {"from_i": learn[0].i, "to_i": learn[-1].i, **b, "n": len(vals),
                        "established_at": _iso(w.end), "established_at_dt": w.end}
                m0y, sig = s * b["m0"], b["sigma"]
                state = "monitoring"
                E = S = 0.0
        else:
            z = max(-clip, min(clip, (y - m0y) / sig))
            E = lam * z + (1 - lam) * E
            S = max(0.0, S + z - k)
            pt.update(z=z, ewma=E, cusum=S)
            if E > e_lim + EPS:
                trig.append("EWMA")
                info["EWMA"] = (E, e_lim)
            if S > h + EPS:
                trig.append("CUSUM")
                info["CUSUM"] = (S, h)
            prev = ys_since[-W:]
            if len(prev) == W:
                rs = max(ROBUST_K * mad(prev), frac * abs(m0y), cfg.sensor_resolution or 0.0)
                rz = (y - median(prev)) / rs
                pt["rolling_z"] = rz
                pt["anomaly"] = rz > rz_lim
                recent = anomalies[-(an_of - 1):] if an_of > 1 else []
                anomalies.append(pt["anomaly"])
                n_an = sum(recent) + (1 if pt["anomaly"] else 0)
                if pt["anomaly"] and n_an >= an_min:
                    trig.append("ROLLING_Z")
                    info["ROLLING_Z"] = (n_an, an_min)
            since.append(w)
            ys_since.append(y)
            last, last_y = since[-K:], ys_since[-K:]
            slope_y = None
            if len(last) == K:
                fit = ols([q.u for q in last], last_y)
                if fit is not None:
                    slope_y = fit[1]
                    pt["slope"] = s * slope_y
            for r in rules:
                if r.type == "pct_of_baseline":
                    hit = y > r.value * m0y
                    lim = r.value * base["m0"]
                    val = x
                elif r.type == "rate_of_change":
                    hit = slope_y is not None and slope_y > r.value
                    lim, val = r.value, (s * slope_y if slope_y is not None else None)
                else:
                    continue
                if rule_step(r, hit):
                    trig.append(r.code)
                    info[r.code] = (val, lim)
            rul = condition_rul(cfg, last, last_y, base["m0"], w.end, tz, rate_fn, plan_fn)
            pt["rul"] = rul
            if rul.get("rul_days") is not None and rul["rul_days"] <= P("rul_warning_days"):
                trig.append("RUL_WARNING")
                info["RUL_WARNING"] = (rul["rul_days"], P("rul_warning_days"))
            if rul.get("rul_days_low") is not None and rul["rul_days_low"] <= P("rul_critical_days"):
                trig.append("RUL_CRITICAL")
                info["RUL_CRITICAL"] = (rul["rul_days_low"], P("rul_critical_days"))
            pt["phase"] = "monitoring"

        trig = sorted(set(trig))
        if trig:
            pt["triggers"] = trig
            sev = severity_of(cfg, trig)
            pt["severity"] = sev
            if open_alert is None:
                snoozed = snooze_until is not None and w.end < snooze_until and SEV_RANK[sev] <= snooze_rank
                if not snoozed:
                    open_alert = _Alert(key=f"w{w.i}", opened_at=_iso(w.end), opened_window=w.i,
                                        first_triggers=list(trig), severity=sev, escalations={sev: _iso(w.end)},
                                        escalation_windows={sev: w.i}, state="open", triggers_seen={},
                                        evidence={}, acknowledged=False, quiet=0, carried=False,
                                        auto=[{"at": _iso(w.end), "i": w.i, "kind": "open", "severity": sev}],
                                        rul=None, point=None, closed_at=None, resolution=None, closed_by=None)
                    alerts.append(open_alert)
                    by_key[open_alert["key"]] = open_alert
                else:
                    pt["snoozed"] = True
            elif SEV_RANK[sev] > SEV_RANK[open_alert["severity"]]:   # P-8: only goes up automatically
                open_alert["severity"] = sev
                open_alert["escalations"][sev] = _iso(w.end)
                open_alert["escalation_windows"][sev] = w.i
                open_alert["auto"].append({"at": _iso(w.end), "i": w.i, "kind": "escalate", "severity": sev})
            if open_alert is not None:
                for code in trig:
                    v, lim = info.get(code, (None, None))
                    evidence(open_alert, code, w, v, lim, severity_of(cfg, [code]))
                open_alert["quiet"] = 0
                open_alert["point"] = {**pt, "baseline": _public_base(base)}
                if pt.get("rul") is not None:
                    open_alert["rul"] = pt["rul"]
        elif open_alert is not None:
            open_alert["quiet"] += 1
            if open_alert["state"] == "open" and not open_alert["acknowledged"] and open_alert["quiet"] >= auto_close:
                open_alert["state"], open_alert["resolution"] = "closed", "auto_cleared"
                open_alert["closed_by"] = "engine"
                open_alert["closed_at"] = _iso(w.end)
                open_alert["auto"].append({"at": _iso(w.end), "i": w.i, "kind": "auto_cleared"})
                open_alert = None
        if open_alert is not None and pt.get("rul") is not None:
            open_alert["rul"] = pt["rul"]
        points.append(pt)
    flush_events(None)

    learning = {"windows": len(learn) if state == "learning" else (base or {}).get("n", 0),
                "required": nbase, "warmup_remaining": skip}
    return FoldResult(points=points, baseline=base, alerts=[dict(a) for a in alerts],
                      state={"phase": state if skip == 0 or state != "learning" else "warmup", "ewma": E,
                             "cusum": S, "ewma_limit": e_lim, "cusum_h": h,
                             "open_alert_key": open_alert["key"] if open_alert else None},
                      learning=learning, unmatched_events=unmatched)


def _public_base(base: Optional[dict]) -> Optional[dict]:
    if base is None:
        return None
    return {k: v for k, v in base.items() if k != "established_at_dt"}


@dataclass(frozen=True)
class Reset:
    at: datetime
    warmup: bool
    reason: str = ""


def fold_monitor(cfg: MonitorCfg, windows: list[WindowIn], resets: list[Reset], tz: ZoneInfo,
                 rate_fn: Optional[RateFn] = None, plan_fn: Optional[Callable] = None,
                 events: Iterable[ExtEvent] = ()) -> list[tuple[Reset, FoldResult]]:
    """Every segment of a monitor in order (windows assigned to the segment whose reset precedes their start;
    a window straddling a reset belongs to none). Alerts still open at a reset carry into the next segment."""
    resets = sorted(resets, key=lambda r: r.at)
    evs = sorted(events, key=lambda e: e.at)
    out: list[tuple[Reset, FoldResult]] = []
    carry: Optional[CarryAlert] = None
    snooze = None
    for n, r in enumerate(resets):
        nxt = resets[n + 1].at if n + 1 < len(resets) else None
        seg = [w for w in windows if (w.start or w.end) >= r.at and (nxt is None or w.end <= nxt)]
        # an event exactly at a reset instant (close with `rebaseline`) belongs to the segment it ends
        seg_ev = [e for e in evs if (e.at > r.at or n == 0) and (nxt is None or e.at <= nxt)]
        res = fold_segment(cfg, seg, warmup=r.warmup, tz=tz, rate_fn=rate_fn, plan_fn=plan_fn, events=seg_ev,
                           carry=carry, snooze=snooze)
        out.append((r, res))
        carry = None
        for a in res.alerts:
            if a["state"] != "closed":
                carry = CarryAlert(key=a["key"], state=a["state"], severity=a["severity"],
                                   opened_at=datetime.fromisoformat(a["opened_at"].replace("Z", "+00:00")),
                                   opened_window=a["opened_window"], acknowledged=a["acknowledged"],
                                   triggers_seen=a["triggers_seen"], evidence=a["evidence"],
                                   escalations=a["escalations"], escalation_windows=a["escalation_windows"],
                                   first_triggers=a["first_triggers"])
            elif a.get("resolution") == "false_alarm" and a.get("closed_at"):
                snooze = (datetime.fromisoformat(a["closed_at"].replace("Z", "+00:00"))
                          + timedelta(days=cfg.p("false_alarm_snooze_days")), a["severity"])
    return out


# ── P-7 usage rate from hourly usage ──────────────────────────────────────────

class Rates(tuple):
    """(rate, rate_low, rate_high) per day + the days of history behind them (`days`)."""
    days: float = 14.0


def make_rate_fn(hourly: dict[datetime, float], days: int = 14, data_since: Optional[datetime] = None) -> RateFn:
    """Rate = usage of the complete hourly buckets [hour(t) − 14 d, hour(t)) ÷ 14; band = the two 7-day halves (min,
    max). `hourly` maps UTC hour starts to the usage in that hour (usage_buckets).
    Sprint 7 (S7-5, audit A-10): whole buckets, the hour still filling at `t` left out — the live evaluation and a
    recomputation give the same pace (P-12), with the same rule as the nominal RUL. S7-2 (audit A-2): with less than
    14 days of data (`data_since`) the window starts there and is divided by those days (< 3 → None:
    insufficient_data; < 14 → low_confidence)."""
    from ..usage.core import RUL_MIN_DAYS, rate_window
    hours = sorted(hourly)

    def between(a: datetime, b: datetime) -> float:
        return sum(hourly[h] for h in hours if a <= h < b)

    def fn(end: datetime):
        start, stop, d = rate_window(end, data_since, days=days)
        if d < RUL_MIN_DAYS:
            return None
        mid = stop - timedelta(days=d / 2)
        r = between(start, stop) / d
        w1, w2 = between(start, mid) / (d / 2), between(mid, stop) / (d / 2)
        out = Rates((r, min(w1, w2), max(w1, w2)))
        out.days = d
        return out

    return fn


# ── P-8: explainable text ─────────────────────────────────────────────────────

def fmt_num(v: Optional[float], decimals: int) -> str:
    if v is None:
        return "—"
    s = f"{v:,.{decimals}f}"
    return s.replace(",", " ").replace(".", ",").replace(" ", " ")


def _decimals_for(sigma: float) -> int:
    if not sigma or sigma <= 0:
        return 2
    return max(1, min(4, 1 - int(math.floor(math.log10(abs(sigma))))))


def _g(v: float) -> str:
    if v is None:
        return "—"
    if abs(v - round(v)) < 1e-9:
        return fmt_num(v, 0)
    return fmt_num(v, 2).rstrip("0").rstrip(",")


def explain(cfg: MonitorCfg, alert: dict, point: Optional[dict], base: Optional[dict], tz: ZoneInfo,
            feature_label: str) -> str:
    """'Cilindro B3 — tempo de curso 206,5 ms (referência 200,0 ± 2,0 ms desde 14/01): +3,25 σ. EWMA 1,34 > 1,00;
    CUSUM 6,0 > 5,0. Falha projetada em 26 dias (17–50) ao atingir 240 ms.'"""
    u = f" {cfg.unit}" if cfg.unit else ""
    head = f"{cfg.label} — {feature_label}" if cfg.label else feature_label.capitalize()
    if point is None:
        return head
    dec = _decimals_for(base["sigma"]) if base else 2
    parts = [f"{head} {fmt_num(point.get('x'), dec)}{u}"]
    if base:
        since = datetime.fromisoformat(base["established_at"].replace("Z", "+00:00")).astimezone(tz)
        ref = f" (referência {fmt_num(base['m0'], dec)} ± {fmt_num(base['sigma'], dec)}{u} desde {since:%d/%m})"
        z = point.get("z")
        parts[0] += ref + (f": {'+' if z >= 0 else '−'}{fmt_num(abs(z), 2)} σ." if z is not None else ".")
    else:
        parts[0] += " (linha de base ainda em aprendizado)."
    trig = point.get("triggers") or []
    rules = {r.code: r for r in cfg.rules}
    det = []
    if point.get("ewma") is not None and "EWMA" in trig:
        det.append(f"EWMA {fmt_num(point['ewma'], 2)} > {fmt_num(ewma_limit(cfg.p('ewma_lambda'), cfg.p('ewma_L')), 2)}")
    if point.get("cusum") is not None and "CUSUM" in trig:
        det.append(f"CUSUM {fmt_num(point['cusum'], 1)} > {fmt_num(cfg.p('cusum_h'), 1)}")
    if "ROLLING_Z" in trig:
        det.append(f"{cfg.p('anomaly_min')}+ anomalias (z móvel > {fmt_num(cfg.p('rolling_z_limit'), 1)}) "
                   f"nas últimas {cfg.p('anomaly_of')} janelas")
    for code in trig:
        r = rules.get(code)
        if r is None:
            continue
        cmp_ = "<" if cfg.direction == "down" else ">"
        if r.type == "threshold":
            det.append(f"{code}: {feature_label} {cmp_} {_g(r.value)}{u} por {r.sustained_for} janelas")
        elif r.type == "pct_of_baseline" and base:
            det.append(f"{code}: {feature_label} {cmp_} {fmt_num(r.value * 100, 0)} % da referência "
                       f"({fmt_num(r.value * base['m0'], dec)}{u})")
        elif r.type == "rate_of_change":
            per = 10000 if cfg.feature == "stroke_time" else 1
            unit_u = "10 000 ciclos" if cfg.feature == "stroke_time" else "hora de operação"
            det.append(f"{code}: variação de {fmt_num((point.get('slope') or 0) * per, 2)}{u} a cada {unit_u} "
                       f"(limite {fmt_num(r.value * per, 2)})")
    text = parts[0]
    if det:
        text += " " + "; ".join(det) + "."
    rul = point.get("rul") or {}
    if rul.get("limit_reached"):
        text += f" Limite de falha atingido ({_g(rul.get('failure_limit'))}{u})."
    elif rul.get("rul_days") is not None:
        lo, hi = rul.get("rul_days_low"), rul.get("rul_days_high")
        hi_s = "> 1 ano" if hi is not None and hi > 365 else (str(hi) if hi is not None else "?")
        text += (f" Falha projetada em {rul['rul_days']} dias ({lo if lo is not None else '?'}–{hi_s}) "
                 f"ao atingir {_g(rul.get('failure_limit'))}{u}.")
    elif rul.get("reason") == "no_significant_trend":
        text += " Sem tendência significativa: sem projeção de falha."
    return text
