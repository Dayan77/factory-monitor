"""P-10 — how to measure whether the predictive module works (pure functions; the service gathers the inputs).

Ground truth: real failures (`FailureRecord` critical/degraded) at the same position with an ISO 14224 mode compatible
with the signal (a failure whose mode is still unknown counts as compatible: it is pending classification).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median
from typing import Iterable, Optional

from .defaults import COMPATIBLE_MODES, DEFAULTS, FALSE_ALARM_RESOLUTIONS, SEV_RANK


@dataclass
class EvAlert:
    id: str
    position_id: str
    feature: str
    opened_at: datetime
    escalations: dict[str, datetime]           # severity -> instant it reached it
    state: str
    resolution: Optional[str] = None
    closed_at: Optional[datetime] = None
    finding: Optional[str] = None              # finding of the linked predictive WO


@dataclass
class EvFailure:
    id: str
    position_id: Optional[str]
    failed_at: datetime
    failure_mode: Optional[str]
    severity: str = "critical"


@dataclass
class EvSegment:
    """A baseline segment of a monitor: monitored from `established_at` until the next reset / failure / data end."""
    position_id: str
    feature: str
    established_at: Optional[datetime]
    end: Optional[datetime]                    # next reset or last data of the segment


@dataclass
class EvCheckpoint:
    position_id: str
    feature: str
    at: datetime
    low: Optional[date]
    high: Optional[date]
    tz: Optional[object] = None


def compatible(feature: str, f: EvFailure) -> bool:
    return f.severity in ("critical", "degraded") and (f.failure_mode is None
                                                       or f.failure_mode in COMPATIBLE_MODES.get(feature, ()))


def predictive_no_defect_hours(wos: Iterable[tuple[str, str, Optional[str], float]]) -> float:
    """P-9: labor hours of completed/closed predictive WOs that found no defect (cost of false alarms)."""
    return float(sum(h for t, st, finding, h in wos
                     if t == "predictive" and st in ("completed", "closed") and finding == "no_defect"))


def evaluate(alerts: list[EvAlert], failures: list[EvFailure], segments: list[EvSegment],
             checkpoints: list[EvCheckpoint], start: datetime, stop: datetime, now: datetime,
             params: Optional[dict] = None) -> dict:
    P = {**DEFAULTS, **(params or {})}
    H = timedelta(days=P["evaluation_horizon_days"])
    min_lead = timedelta(hours=P["min_useful_lead_h"])
    eval_end = min(stop, now)

    def fails_for(a_pos: str, feature: str) -> list[EvFailure]:
        return [f for f in failures if f.position_id == a_pos and compatible(feature, f)]

    def outcome(a: EvAlert, since: datetime) -> tuple[str, Optional[EvFailure]]:
        if a.resolution == "duplicate":
            # repeats another alert's detection (closed by a person, or superseded by recompute): counting its
            # failure again would give two true positives for one failure
            return "excluded", None
        hits = [f for f in fails_for(a.position_id, a.feature) if since < f.failed_at <= since + H]
        if hits:
            return "tp", min(hits, key=lambda f: f.failed_at)
        if a.resolution == "confirmed" or a.finding == "defect_confirmed":
            return "tp", None
        if a.resolution == "known_cause":
            return "excluded", None
        if a.state == "closed" and (a.resolution in FALSE_ALARM_RESOLUTIONS or a.finding == "no_defect"):
            return "fa", None
        if since + H <= eval_end:
            return "fa", None
        return "pending", None

    in_period = [a for a in alerts if start <= a.opened_at < stop]
    rows = []
    stats = {sev: {"tp": 0, "fa": 0, "pending": 0, "excluded": 0, "leads_h": []} for sev in ("watch", "warning",
                                                                                          "critical")}
    for a in in_period:
        row = {"alert_id": a.id, "position_id": a.position_id, "feature": a.feature,
               "opened_at": a.opened_at, "resolution": a.resolution, "by_severity": {}}
        for sev in ("watch", "warning", "critical"):
            since = a.opened_at if sev == "watch" else a.escalations.get(sev)
            if since is None and sev != "watch":
                # reached a higher level directly: the level's instant is the first escalation at or above it
                ups = [t for s_, t in a.escalations.items() if SEV_RANK[s_] >= SEV_RANK[sev]]
                since = min(ups) if ups else None
            if since is None:
                continue
            oc, f = outcome(a, since)
            stats[sev][oc] += 1
            lead = (f.failed_at - since).total_seconds() / 3600 if f is not None else None
            if lead is not None:
                stats[sev]["leads_h"].append(lead)
            row["by_severity"][sev] = {"outcome": oc, "since": since, "failure_id": f.id if f else None,
                                       "lead_time_h": lead}
        rows.append(row)

    # monitored time (position-days), excluding re-learning
    monitored_s = 0.0
    by_pos: dict[str, float] = {}
    for sg in segments:
        if sg.established_at is None:
            continue
        a = max(sg.established_at, start)
        b = min(x for x in (sg.end, eval_end) if x is not None)
        fl = [f.failed_at for f in fails_for(sg.position_id, sg.feature) if f.failed_at > sg.established_at]
        if fl:
            b = min(b, min(fl))
        if b > a:
            d = (b - a).total_seconds()
            monitored_s += d
            by_pos[f"{sg.position_id}:{sg.feature}"] = by_pos.get(f"{sg.position_id}:{sg.feature}", 0.0) + d / 86400
    monitored_days = monitored_s / 86400

    # detection (recall): compatible failures at monitored (position, signal) with an alert ≥ min_lead before
    monitored_keys = {(sg.position_id, sg.feature) for sg in segments if sg.established_at is not None}
    fail_rows = []
    det = total = 0
    for f in failures:
        if not (start <= f.failed_at < stop) or f.severity not in ("critical", "degraded"):
            continue
        feats = [ft for (p, ft) in monitored_keys if p == f.position_id and compatible(ft, f)]
        if not feats:
            continue
        total += 1
        cands = [a for a in alerts if a.position_id == f.position_id and a.feature in feats
                 and f.failed_at - H <= a.opened_at <= f.failed_at - min_lead]
        hit = min(cands, key=lambda a: a.opened_at) if cands else None
        det += hit is not None
        fail_rows.append({"failure_id": f.id, "position_id": f.position_id, "failed_at": f.failed_at,
                          "detected": hit is not None, "alert_id": hit.id if hit else None,
                          "lead_time_h": (f.failed_at - hit.opened_at).total_seconds() / 3600 if hit else None})

    # RUL band coverage
    cov_n = cov_in = 0
    for f in failures:
        if not (start <= f.failed_at < stop):
            continue
        for cp in checkpoints:
            if cp.position_id != f.position_id or not (f.failed_at - H < cp.at <= f.failed_at):
                continue
            if cp.low is None or cp.high is None:
                continue
            cov_n += 1
            day = _local_day(f.failed_at, cp)
            cov_in += cp.low <= day <= cp.high

    def prec(s):
        d = s["tp"] + s["fa"]
        return s["tp"] / d if d else None

    def far(s):
        return s["fa"] / monitored_days * 365 if monitored_days > 0 else None

    w = stats["warning"]
    out = {
        "period_start": start, "period_end": stop,
        "alerts": len(in_period),
        "true_positives": stats["watch"]["tp"], "false_alarms": stats["watch"]["fa"],
        "pending": stats["watch"]["pending"], "excluded": stats["watch"]["excluded"],
        "true_positives_warning": w["tp"], "false_alarms_warning": w["fa"],
        "true_positives_critical": stats["critical"]["tp"], "false_alarms_critical": stats["critical"]["fa"],
        "precision": prec(stats["watch"]), "precision_warning": prec(w), "precision_critical": prec(stats["critical"]),
        "precision_reason": None if prec(stats["watch"]) is not None else "no_decided_alerts",
        "failures_total": total, "failures_detected": det, "detection_rate": det / total if total else None,
        "detection_reason": None if total else "no_failures",
        "monitored_days": monitored_days, "monitored_days_by_monitor": by_pos,
        "false_alarms_per_position_year": far(stats["watch"]),
        "false_alarms_per_position_year_warning": far(w),
        "false_alarm_rate_reason": None if monitored_days > 0 else "no_monitored_time",
        "lead_time_h_median": median(stats["watch"]["leads_h"]) if stats["watch"]["leads_h"] else None,
        "lead_time_h_median_warning": median(w["leads_h"]) if w["leads_h"] else None,
        "lead_time_h_median_critical": median(stats["critical"]["leads_h"]) if stats["critical"]["leads_h"] else None,
        "lead_time_h_first_alert": (min((r["lead_time_h"] for r in fail_rows if r["lead_time_h"] is not None),
                                        default=None)),
        "rul_band_coverage": cov_in / cov_n if cov_n else None, "rul_band_checkpoints": cov_n,
        "alert_outcomes": rows, "failures": fail_rows,
        "horizon_days": P["evaluation_horizon_days"], "min_useful_lead_h": P["min_useful_lead_h"],
    }
    return out


def _local_day(t: datetime, cp: EvCheckpoint) -> date:
    return t.astimezone(cp.tz).date() if cp.tz else t.date()


# ── gathering from the database (tenant-scoped) ───────────────────────────────

async def evaluate_tenant(db, tenant_id: str, start: datetime, stop: datetime, now: datetime,
                          line_id: Optional[str] = None) -> dict:
    from sqlalchemy import select
    from ...models import (ConditionBaseline, ConditionFeature, FailureRecord, Machine, PredictiveAlert,
                           PredictiveMonitor, WorkOrder)
    from .. import clock
    from .service import aware, plant_tz

    def _iso(d):
        return clock.iso(d) if isinstance(d, datetime) else d

    mq = select(PredictiveMonitor).where(PredictiveMonitor.tenant_id == tenant_id)
    if line_id:
        mq = mq.join(Machine, Machine.id == PredictiveMonitor.machine_id).where(Machine.line_id == line_id,
                                                                                Machine.tenant_id == tenant_id)
    monitors = list((await db.execute(mq)).scalars().all())
    mids = [m.id for m in monitors]
    positions = {m.position_id for m in monitors}
    if not mids:
        out = evaluate([], [], [], [], start, stop, now)
        out["predictive_no_defect_hours"] = 0.0
        return _jsonable(out)
    alerts = list((await db.execute(select(PredictiveAlert).where(
        PredictiveAlert.tenant_id == tenant_id, PredictiveAlert.monitor_id.in_(mids)))).scalars().all())
    wo_ids = [a.work_order_id for a in alerts if a.work_order_id]
    findings = {}
    if wo_ids:
        findings = {i: f for i, f in (await db.execute(select(WorkOrder.id, WorkOrder.finding).where(
            WorkOrder.tenant_id == tenant_id, WorkOrder.id.in_(wo_ids)))).all()}
    ev_alerts = [EvAlert(id=a.id, position_id=a.position_id, feature=a.feature, opened_at=aware(a.opened_at),
                         escalations={s: datetime.fromisoformat(t.replace("Z", "+00:00"))
                                      for s, t in (a.escalations or {}).items()},
                         state=a.state, resolution=a.resolution, closed_at=aware(a.closed_at),
                         finding=findings.get(a.work_order_id)) for a in alerts]
    fails = list((await db.execute(select(FailureRecord).where(
        FailureRecord.tenant_id == tenant_id, FailureRecord.position_id.in_(list(positions))))).scalars().all())
    ev_fails = [EvFailure(id=f.id, position_id=f.position_id, failed_at=aware(f.failed_at),
                          failure_mode=f.failure_mode, severity=f.severity) for f in fails
                if f.detection_method != "continuous_condition_monitoring"]
    segs: list[EvSegment] = []
    cps: list[EvCheckpoint] = []
    by_m = {m.id: m for m in monitors}
    resets = list((await db.execute(select(ConditionBaseline).where(
        ConditionBaseline.tenant_id == tenant_id, ConditionBaseline.monitor_id.in_(mids))
        .order_by(ConditionBaseline.monitor_id, ConditionBaseline.reset_at))).scalars().all())
    last_end = {}
    for bid, mx in (await db.execute(select(ConditionFeature.baseline_id, func_max(ConditionFeature.end_at)).where(
            ConditionFeature.tenant_id == tenant_id, ConditionFeature.monitor_id.in_(mids))
            .group_by(ConditionFeature.baseline_id))).all():
        last_end[bid] = aware(mx)
    for k, b in enumerate(resets):
        nxt = resets[k + 1] if k + 1 < len(resets) and resets[k + 1].monitor_id == b.monitor_id else None
        end = aware(nxt.reset_at) if nxt is not None else last_end.get(b.id)
        m = by_m[b.monitor_id]
        segs.append(EvSegment(position_id=m.position_id, feature=m.feature, established_at=aware(b.established_at),
                              end=end))
    tzs = {}
    for f in (await db.execute(select(ConditionFeature).where(
            ConditionFeature.tenant_id == tenant_id, ConditionFeature.monitor_id.in_(mids),
            ConditionFeature.phase == "monitoring"))).scalars():
        r = f.rul or {}
        if r.get("projected_date_low") and r.get("projected_date_high"):
            m = by_m[f.monitor_id]
            if m.machine_id not in tzs:
                tzs[m.machine_id] = await plant_tz(db, tenant_id, m.machine_id)
            cps.append(EvCheckpoint(position_id=m.position_id, feature=m.feature, at=aware(f.end_at),
                                    low=date.fromisoformat(r["projected_date_low"]),
                                    high=date.fromisoformat(r["projected_date_high"]), tz=tzs[m.machine_id]))
    out = evaluate(ev_alerts, ev_fails, segs, cps, start, stop, now)
    wos = (await db.execute(select(WorkOrder.type, WorkOrder.state, WorkOrder.finding, WorkOrder.labor_hours).where(
        WorkOrder.tenant_id == tenant_id, WorkOrder.type == "predictive", WorkOrder.completed_at >= start,
        WorkOrder.completed_at < stop))).all()
    out["predictive_no_defect_hours"] = predictive_no_defect_hours([tuple(r) for r in wos])
    return _jsonable(out)


def func_max(col):
    from sqlalchemy import func
    return func.max(col)


def _jsonable(v):
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, datetime):
        from .. import clock
        return clock.iso(v)
    if isinstance(v, date):
        return v.isoformat()
    return v
