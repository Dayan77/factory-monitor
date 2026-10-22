"""Detector lag (Sprint 7): how far behind its data each detector is, per machine / monitor.

The detectors run in the worker process (src/worker.py), which has no /metrics endpoint; their cursors live in
Postgres, so the app process samples them every `LAG_SAMPLE_S` and exports:

  * fm_detector_lag_seconds{detector, tenant_id, entity_id}
      - downtime:   latest reading of the machine's signals seen by the detector − its checkpoint;
      - predictive: (now − grace) − end of the last stored window − one window (time-window monitors);
  * fm_detector_lag_max_seconds{detector} and fm_detector_lagging{detector} (entities above
    settings.detector_lag_alert_s) — the Prometheus alert FmDetectorLagHigh fires on the latter
    (infra/monitoring/prometheus/alerts.yml); a log warning `detector.lag_high` is written when an entity crosses it.

An offline machine (no new readings) has no lag: the latest reading seen does not move either.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from prometheus_client import Gauge
from sqlalchemy import func, select

from ...config import settings

log = structlog.get_logger()

LAG_SAMPLE_S = 30.0
DETECTORS = ("downtime", "predictive")

DETECTOR_LAG = Gauge("fm_detector_lag_seconds", "Detector lag behind its data, per machine/monitor",
                     ["detector", "tenant_id", "entity_id"])
DETECTOR_LAG_MAX = Gauge("fm_detector_lag_max_seconds", "Largest detector lag", ["detector"])
DETECTOR_LAGGING = Gauge("fm_detector_lagging", "Machines/monitors whose lag is above DETECTOR_LAG_ALERT_S",
                         ["detector"])
for _d in DETECTORS:
    DETECTOR_LAG_MAX.labels(_d).set(0)
    DETECTOR_LAGGING.labels(_d).set(0)

_alerted: set[tuple[str, str]] = set()


def _dt(s: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(s) if s else None


def downtime_lag(checkpoint: Optional[datetime], state: Optional[dict]) -> float:
    """latest reading seen (state.seen[*][1]) − checkpoint, ≥ 0."""
    seen = (state or {}).get("seen") or []
    latest = max((_dt(ts) for _, ts in seen if ts), default=None)
    if latest is None or checkpoint is None:
        return 0.0
    return max(0.0, (latest - checkpoint).total_seconds())


async def sample(db, now: Optional[datetime] = None) -> dict[str, list[tuple[str, str, float]]]:
    """Reads every cursor and updates the gauges. Returns {detector: [(tenant_id, entity_id, lag_s)]}."""
    from ...models import ConditionFeature, DowntimeDetectorState, PredictiveMonitor
    now = now or datetime.now(timezone.utc)
    out: dict[str, list[tuple[str, str, float]]] = {d: [] for d in DETECTORS}
    for tid, mid, cp, st in (await db.execute(select(
            DowntimeDetectorState.tenant_id, DowntimeDetectorState.machine_id, DowntimeDetectorState.checkpoint,
            DowntimeDetectorState.state))).all():
        out["downtime"].append((str(tid), str(mid), downtime_lag(cp, st)))
    grace = timedelta(seconds=float(getattr(settings, "predictive_grace_s", 120.0)))
    last = {m: e for m, e in (await db.execute(select(ConditionFeature.monitor_id, func.max(ConditionFeature.end_at))
                                                .group_by(ConditionFeature.monitor_id))).all()}
    for mid, tid, wh in (await db.execute(select(PredictiveMonitor.id, PredictiveMonitor.tenant_id,
                                                 PredictiveMonitor.window_hours).where(
            PredictiveMonitor.enabled.is_(True), PredictiveMonitor.window_hours.is_not(None)))).all():
        end = last.get(mid)
        if end is None or not wh:
            continue
        lag = (now - grace - end).total_seconds() - float(wh) * 3600.0
        out["predictive"].append((str(tid), str(mid), max(0.0, lag)))
    DETECTOR_LAG.clear()
    threshold = settings.detector_lag_alert_s
    for det, rows in out.items():
        over = 0
        for tid, eid, lag in rows:
            DETECTOR_LAG.labels(det, tid, eid).set(lag)
            key = (det, eid)
            if lag > threshold:
                over += 1
                if key not in _alerted:
                    _alerted.add(key)
                    log.warning("detector.lag_high", detector=det, tenant_id=tid[:8], entity_id=eid[:8],
                                lag_s=round(lag), threshold_s=threshold)
            elif key in _alerted:
                _alerted.discard(key)
                log.info("detector.lag_recovered", detector=det, tenant_id=tid[:8], entity_id=eid[:8],
                         lag_s=round(lag))
        DETECTOR_LAG_MAX.labels(det).set(max((r[2] for r in rows), default=0.0))
        DETECTOR_LAGGING.labels(det).set(over)
    return out


async def run_lag_sampler() -> None:
    """Long-running task (app lifespan)."""
    from ...db import AsyncSessionLocal
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await sample(db)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("detector.lag_sample_error", error=str(e)[:300])
        await asyncio.sleep(LAG_SAMPLE_S)
