"""Visual state of a machine on the plant map (docs/sprint6.md §1.2, block `s6-status-precedence`).

`visual_state(facts)` is the single rule used by the API (map, automatic layout) and by the tests: the first true
rule of the table wins. `load_facts()` reads the facts of the machines of one plant from the database (status,
last_seen_at, open failures, maintenance WOs in progress that stop the machine, the calendar at `now`, open
downtime events); `describe()` turns facts into the marker payload (state, icon, short text, `since` — durations
are counted by the client from the server clock).

Rules (first true wins):
  1 offline      Machine.status = unknown or no reading for > machine_offline_timeout_s (last_seen_at)
  2 failure      open FailureRecord (no restored_at) or Machine.status = error  (before the break rules: kpis S5-1)
                 S7-23: an `incipient` record never stops the machine, and a record open for longer than
                 FAILURE_STALE_OPEN_H does not make the state (a machine that reports a fault now is still
                 "failure" by its status); it adds the badge/text "falha aberta há muito tempo — verificar"
  3 maintenance  Machine.status = maintenance or a WO in_progress that stops the machine
  4 planned_stop break of the shift, planned stop of the calendar, open PLN-* downtime
  5 off_shift    no shift instance now (D6/D9)
  6 stopped      open unplanned DowntimeEvent, or Machine.status = stopped in productive time
  7 running      Machine.status = running
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models import DowntimeEvent, FailureRecord, Machine, PredictiveAlert, Shift, WorkOrder
from ..oee.engine import UNJUSTIFIED, effective_reason, load_calendar, reason_meta
from ..usage import queries as uq

ORDER = ("offline", "failure", "maintenance", "planned_stop", "off_shift", "stopped", "running")
MACHINE_STATUS_MAP = {"unknown": "offline", "error": "failure", "maintenance": "maintenance",
                      "stopped": "stopped", "running": "running"}
ICONS = {"offline": "cloud-off", "failure": "wrench", "maintenance": "hard-hat", "planned_stop": "clock",
         "off_shift": "moon", "stopped": "pause", "running": "play"}
LABELS = {"offline": "sem dados", "failure": "falha", "maintenance": "manutenção", "planned_stop": "pausa",
          "off_shift": "fora do turno", "stopped": "parada", "running": "rodando"}
OPEN_ALERT_STATES = ("open", "acknowledged")
STALE_FAILURE_TEXT = "falha aberta há muito tempo — verificar"
SEV_RANK = {"watch": 1, "warning": 2, "critical": 3}


def _status(v) -> Optional[str]:
    return getattr(v, "value", v)


def visual_state(facts: Mapping) -> str:
    """The table of §1.2; `facts` uses the keys of the spec block (status, stale, open_failure,
    wo_in_progress_stops_machine, in_break, planned_stop, open_pln, off_shift, open_downtime)."""
    st = _status(facts.get("status"))
    if st == "unknown" or facts.get("stale"):
        return "offline"
    if facts.get("open_failure") or st == "error":
        return "failure"
    if st == "maintenance" or facts.get("wo_in_progress_stops_machine"):
        return "maintenance"
    if facts.get("in_break") or facts.get("planned_stop") or facts.get("open_pln"):
        return "planned_stop"
    if facts.get("off_shift"):
        return "off_shift"
    if facts.get("open_downtime") or st == "stopped":
        return "stopped"
    if st == "running":
        return "running"
    return "offline"           # not reachable for a known MachineStatus (every value is mapped above)


def _iso(ts: Optional[datetime]) -> Optional[str]:
    return ts.isoformat() if ts else None


def describe(facts: Mapping) -> dict:
    """Marker payload for one machine: state + icon + short text (never colour only, §1.2) + `since`."""
    state = visual_state(facts)
    text = LABELS[state]
    since = None
    detail: dict = {}
    if state == "offline":
        since = facts.get("last_seen_at")
    elif state == "failure":
        since = facts.get("failure_since") or facts.get("downtime_since")
        code = facts.get("failure_mode") or facts.get("fault_reason")
        if facts.get("fault_code") is not None:
            detail["fault_code"] = facts["fault_code"]
        if code:
            text = f"falha — {code}"
            detail["failure_code"] = code
    elif state == "maintenance":
        since = facts.get("wo_started_at")
        if facts.get("wo_label"):
            text = f"manutenção — {facts['wo_label']}"
            detail["work_order_id"] = facts.get("wo_id")
    elif state == "planned_stop":
        since = facts.get("break_start") or facts.get("downtime_since")
        label = facts.get("break_label")
        until = facts.get("break_until_local")
        text = f"pausa ({label})" if label else "pausa"
        if until:
            text += f" até {until}"
        detail["until"] = _iso(facts.get("break_until"))
    elif state == "off_shift":
        detail["next_shift_start"] = _iso(facts.get("next_shift_start"))
    elif state == "stopped":
        since = facts.get("downtime_since")
        reason = facts.get("downtime_reason")
        text = f"parada — {facts.get('downtime_reason_label') or reason}" if reason and reason != UNJUSTIFIED \
            else "parada — sem motivo"
        detail["downtime_id"] = facts.get("downtime_id")
        detail["reason_code"] = reason
    badges = []
    if facts.get("stale_failure"):
        # S7-23: explicit, never a normal live failure — someone must check the record (forgotten restore?)
        detail["stale_failure_id"] = facts.get("stale_failure_id")
        detail["stale_failure_since"] = _iso(facts.get("stale_failure_since"))
        badges.append({"kind": "stale_failure", "failure_id": facts.get("stale_failure_id"),
                       "since": _iso(facts.get("stale_failure_since")), "text": STALE_FAILURE_TEXT})
        text = f"{text} · {STALE_FAILURE_TEXT}"
    if facts.get("predictive_severity"):
        badges.append({"kind": "predictive", "severity": facts["predictive_severity"],
                       "alert_id": facts.get("predictive_alert_id")})
    return {"state": state, "icon": ICONS[state], "text": text, "since": _iso(since), "detail": detail,
            "badges": badges, "status": _status(facts.get("status")),
            "last_seen_at": _iso(facts.get("last_seen_at"))}


async def load_facts(db: AsyncSession, tenant_id: str, plant, machines: Iterable[Machine],
                     now: Optional[datetime] = None) -> dict[str, dict]:
    """Facts of §1.2 for `machines` (all of `plant`, tenant `tenant_id`) at `now`."""
    from ..maintenance.service import wo_label

    now = now or datetime.now(timezone.utc)
    machines = list(machines)
    mids = [m.id for m in machines]
    out: dict[str, dict] = {}
    if not mids:
        return out
    timeout = timedelta(seconds=settings.machine_offline_timeout_s)
    tz = uq.tzinfo(plant.timezone)
    cal = await load_calendar(db, tenant_id, plant.id, tz)
    inst = cal.instance_at(now)
    meta = await reason_meta(db, tenant_id)
    brk = None
    if inst is not None:
        brk = next((b for b in inst.breaks if b.start <= now < b.end), None)
    break_label = None
    if brk is not None:
        break_label = await _break_label(db, tenant_id, inst, brk, tz, meta)
    next_start = None
    if inst is None and not cal.default:
        nxt = cal.instances(now, now + timedelta(days=8))
        next_start = next((i.start for i in nxt if i.start > now), None)

    # S7-23: open = no restored_at and not incipient (an incipient failure does not stop the function, ISO 14224);
    # a record open for longer than failure_stale_open_h is "stale" — kept apart from the live ones
    stale_after = timedelta(hours=settings.failure_stale_open_h)
    failures, stale_failures = {}, {}
    for fr in (await db.execute(select(FailureRecord).where(
            FailureRecord.tenant_id == tenant_id, FailureRecord.machine_id.in_(mids),
            FailureRecord.restored_at.is_(None), FailureRecord.severity != "incipient")
            .order_by(FailureRecord.failed_at, FailureRecord.id))).scalars():
        if now - fr.failed_at > stale_after:
            stale_failures.setdefault(fr.machine_id, fr)
        else:
            failures.setdefault(fr.machine_id, fr)
    wos = {}
    for wo in (await db.execute(select(WorkOrder).where(
            WorkOrder.tenant_id == tenant_id, WorkOrder.machine_id.in_(mids), WorkOrder.state == "in_progress",
            WorkOrder.stops_machine.is_(True)).order_by(WorkOrder.started_at))).scalars():
        wos.setdefault(wo.machine_id, wo)
    open_evs: dict[str, list[DowntimeEvent]] = {}
    for ev in (await db.execute(select(DowntimeEvent).where(
            DowntimeEvent.tenant_id == tenant_id, DowntimeEvent.machine_id.in_(mids),
            DowntimeEvent.end.is_(None)).order_by(DowntimeEvent.start))).scalars():
        open_evs.setdefault(ev.machine_id, []).append(ev)
    alerts: dict[str, PredictiveAlert] = {}
    for a in (await db.execute(select(PredictiveAlert).where(
            PredictiveAlert.tenant_id == tenant_id, PredictiveAlert.machine_id.in_(mids),
            PredictiveAlert.state.in_(OPEN_ALERT_STATES)))).scalars():
        cur = alerts.get(a.machine_id)
        if cur is None or SEV_RANK.get(a.severity, 0) > SEV_RANK.get(cur.severity, 0):
            alerts[a.machine_id] = a

    for m in machines:
        st = _status(m.status)
        last = m.last_seen_at
        f: dict = {"status": st, "last_seen_at": last,
                   "stale": last is None or (now - last) > timeout}
        fr = failures.get(m.id)
        evs = open_evs.get(m.id, [])
        planned = [e for e in evs if e.planned or effective_reason(e).startswith("PLN-")]
        unplanned = [e for e in evs if e not in planned]
        if fr is not None:
            f.update(open_failure=True, failure_since=fr.failed_at, failure_mode=fr.failure_mode)
        fault_ev = next((e for e in evs if e.fault_code is not None), None)
        if fault_ev is not None:
            f.update(fault_code=fault_ev.fault_code, fault_reason=effective_reason(fault_ev))
            f.setdefault("failure_since", fault_ev.fault_since or fault_ev.start)
        old_fr = stale_failures.get(m.id)
        if old_fr is not None:
            # never `open_failure`: the state comes from the other rules (a machine that reports a fault now is
            # still "failure" by its status); the old record is flagged for checking
            f.update(stale_failure=True, stale_failure_id=old_fr.id, stale_failure_since=old_fr.failed_at)
        wo = wos.get(m.id)
        if wo is not None:
            f.update(wo_in_progress_stops_machine=True, wo_id=wo.id, wo_label=wo_label(wo.number),
                     wo_started_at=wo.started_at)
        if brk is not None:
            f.update(in_break=True, break_start=brk.start, break_until=brk.end, break_label=break_label,
                     break_until_local=brk.end.astimezone(tz).strftime("%H:%M"))
        if planned:
            ev = planned[0]
            f.update(open_pln=True, downtime_since=ev.stop_start or ev.start)
            if brk is None:
                code = effective_reason(ev)
                f.update(break_label=(meta.get(code, (None, code))[1] or code).lower())
        if inst is None:
            f.update(off_shift=True, next_shift_start=next_start)
        if unplanned:
            ev = unplanned[0]
            code = effective_reason(ev)
            f.update(open_downtime=True, downtime_id=ev.id, downtime_since=ev.stop_start or ev.start,
                     downtime_reason=code, downtime_reason_label=meta.get(code, (None, None))[1])
        a = alerts.get(m.id)
        if a is not None:
            f.update(predictive_severity=a.severity, predictive_alert_id=a.id)
        out[m.id] = f
    return out


async def _break_label(db: AsyncSession, tenant_id: str, inst, brk, tz, meta) -> Optional[str]:
    """Label of the break (`label` of the shift break, e.g. "refeição"), else the reason label."""
    if inst.shift_id:
        sh = (await db.execute(select(Shift).where(Shift.id == inst.shift_id,
                                                   Shift.tenant_id == tenant_id))).scalar_one_or_none()
        hhmm = brk.start.astimezone(tz).strftime("%H:%M")
        for b in (sh.breaks if sh else []) or []:
            if b.get("start_local") == hhmm and b.get("label"):
                return str(b["label"])
    label = meta.get(brk.reason_code, (None, None, False))[1]
    return label.lower() if label else None
