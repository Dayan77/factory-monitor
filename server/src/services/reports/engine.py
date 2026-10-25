"""Report scheduling engine (docs/reports.md §4): runs every minute (like the maintenance engine).

1. For every enabled schedule, the candidate periods whose send_at (§4.1) is due and inside the catch-up window
   (§4.2: last 7 days; older periods stay download-only) get a `report_dispatches` row — unique per
   (schedule, period): inserted with ON CONFLICT DO NOTHING, so the same period is never dispatched twice.
   A period whose send_at precedes the schedule's creation is not back-filled.
2. Due dispatches are claimed with SELECT … FOR UPDATE SKIP LOCKED and a lease (next_attempt_at moved ahead), so two
   engine passes in the same minute never process the same dispatch; every recipient's delivery is recorded and
   committed as it happens, and a retry skips recipients already sent (never twice).
3. Every format × permission profile is generated **before** anything is sent (nothing is sent partially).
   Generation failure → retries after 5, 15 and 60 min; then the dispatch is `failed`, a `failed` report_run is
   recorded and the tenant admins are notified. No mail channel → the reports are generated and kept for
   download, the dispatch is `no_channel` and the admins are warned (never silent).
4. Monthly: within 7 days after sending, once a day the KPIs of section 1 are recomputed; a change > 0.5 pp
   (fractions) or > 1 % (others) generates a new version sent as "versão revisada".
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models import (Plant, ReportDelivery, ReportDispatch, ReportExternalRecipient, ReportRun, ReportSchedule,
                       Tenant, User)
from ...notifications import service as notif
from .. import clock
from ..mailer import MailBounced, MailNotConfigured, mail_channel, send_email_message
from ..oee.calendar import Calendar
from ..oee.engine import load_calendar
from ..usage import queries as uq
from . import delivery as dl, periods as P
from .catalog import BY_CODE
from .service import ScopeError, build, store_run

log = structlog.get_logger()

RETRY_MIN = (5, 15, 60)
LEASE_MIN = 15
REVISION_PP = 0.005
REVISION_REL = 0.01
REVISION_KEYS_FRACTION = ("pmp", "schedule_compliance", "ao")
REVISION_KEYS_OTHER = ("backlog_weeks", "mtbf_h", "mttr_min")


async def schedule_calendar(db: AsyncSession, s: ReportSchedule) -> tuple[Calendar, Optional[str], list[str]]:
    """Calendar of the schedule's plant (tenant scope: the first plant's time zone, no shifts)."""
    from ...models import Line
    plant_id = None
    if s.scope_type == "plant":
        plant_id = s.scope_id
    elif s.scope_type == "line":
        plant_id = (await db.execute(select(Line.plant_id).where(Line.id == s.scope_id,
                                                                 Line.tenant_id == s.tenant_id))).scalar_one_or_none()
    if plant_id:
        plant = (await db.execute(select(Plant).where(Plant.id == plant_id, Plant.tenant_id == s.tenant_id))
                 ).scalar_one_or_none()
        if plant is None:
            raise ScopeError(404, "escopo do agendamento não encontrado")
        tz = uq.tzinfo(plant.timezone)
        cal = await load_calendar(db, s.tenant_id, plant.id, tz)
        line_ids = list((await db.execute(select(Line.id).where(Line.tenant_id == s.tenant_id,
                                                                Line.plant_id == plant.id))).scalars().all())
        if s.scope_type == "line":
            line_ids = [s.scope_id]
        return cal, plant.id, line_ids
    tzname = (await db.execute(select(Plant.timezone).where(Plant.tenant_id == s.tenant_id)
                               .order_by(Plant.created_at).limit(1))).scalar_one_or_none()
    from ...models import Line as _L
    line_ids = list((await db.execute(select(_L.id).where(_L.tenant_id == s.tenant_id))).scalars().all())
    return Calendar(uq.tzinfo(tzname), []), None, line_ids


def _period_matches_schedule(s: ReportSchedule, per: P.Period) -> bool:
    if s.report_type == "shift":
        sid = (s.params or {}).get("shift_id")
        return sid is None or per.shift_id == sid
    return True


def due_periods(s: ReportSchedule, cal: Calendar, now: datetime, catchup_days: int) -> list[tuple[P.Period, datetime, datetime]]:
    out = []
    created = s.created_at or now
    for per in P.candidate_periods(s.report_type, cal, now, lookback_days=catchup_days + 1,
                                   shift_id=(s.params or {}).get("shift_id")):
        if not _period_matches_schedule(s, per):
            continue
        ready = P.ready_at(per, s.report_type)
        send = P.send_at(per, s.report_type, cal.tz, s.deliver_at_local or BY_CODE[s.report_type]["deliver_at_local"])
        if send <= now and send >= now - timedelta(days=catchup_days) and send >= created:
            out.append((per, ready, send))
    return out


async def plan_dispatches(db: AsyncSession, tenant_id: str, now: datetime) -> int:
    scheds = (await db.execute(select(ReportSchedule).where(ReportSchedule.tenant_id == tenant_id,
                                                            ReportSchedule.enabled.is_(True)))).scalars().all()
    n = 0
    for s in scheds:
        try:
            cal, _, _ = await schedule_calendar(db, s)
        except ScopeError:
            continue
        for per, ready, send in due_periods(s, cal, now, settings.reports_catchup_days):
            res = await db.execute(pg_insert(ReportDispatch).values(
                tenant_id=tenant_id, schedule_id=s.id, period_key=per.key, period=per.as_dict(), ready_at=ready,
                send_at=send, status="pending", attempts=0, next_attempt_at=send)
                .on_conflict_do_nothing(index_elements=["schedule_id", "period_key"]))
            n += res.rowcount or 0
    await db.commit()
    return n


def _period_from(d: dict, report_type: str) -> P.Period:
    def ts(x):
        return datetime.fromisoformat(x) if x else None

    def dd(x):
        return date.fromisoformat(x) if x else None
    return P.Period(report_type, d["key"], dd(d.get("first_day")), dd(d.get("last_day")), ts(d.get("start")),
                    ts(d.get("end")), d.get("label") or d["key"], shift_id=d.get("shift_id"),
                    shift_name=d.get("shift_name"))


async def claim(db: AsyncSession, now: datetime, limit: int = 5, tenant_id: Optional[str] = None) -> list[str]:
    q = (select(ReportDispatch).where(ReportDispatch.status == "pending", ReportDispatch.next_attempt_at <= now)
         .order_by(ReportDispatch.next_attempt_at).limit(limit).with_for_update(skip_locked=True))
    if tenant_id:
        q = q.where(ReportDispatch.tenant_id == tenant_id)
    rows = (await db.execute(q)).scalars().all()
    ids = []
    for d in rows:
        d.attempts += 1
        d.next_attempt_at = now + timedelta(minutes=LEASE_MIN)          # lease
        ids.append(d.id)
    await db.commit()
    return ids


async def _admins(db: AsyncSession, tenant_id: str) -> list[User]:
    return await notif.users_with_roles(db, tenant_id, ("admin",))


async def warn_no_channel(db: AsyncSession, tenant_id: str, now: datetime) -> None:
    cfg = await dl.settings_for(db, tenant_id)
    today = now.date()
    if cfg.no_channel_warned_on == today:
        return
    cfg.no_channel_warned_on = today
    await notif.notify(db, tenant_id, await _admins(db, tenant_id), "report_no_mail_channel",
                       "E-mail não configurado: relatórios agendados ficam só para download",
                       body="Configure SMTP_HOST ou SENDGRID_API_KEY para que os relatórios agendados sejam enviados.",
                       link="/reports/schedules", group_key="report_no_mail_channel")


async def process(db: AsyncSession, dispatch_id: str, now: datetime, app=None) -> str:
    d = (await db.execute(select(ReportDispatch).where(ReportDispatch.id == dispatch_id))).scalar_one()
    s = (await db.execute(select(ReportSchedule).where(ReportSchedule.id == d.schedule_id,
                                                       ReportSchedule.tenant_id == d.tenant_id))).scalar_one()
    tid = s.tenant_id
    try:
        cal, plant_id, line_ids = await schedule_calendar(db, s)
        per = _period_from(d.period, s.report_type)
        plan = await dl.resolve_recipients(db, s, set(line_ids))
        formats = [f for f in (s.formats or ["pdf"]) if f in BY_CODE[s.report_type]["formats"]] or ["pdf"]
        runs: dict[str, list[ReportRun]] = {}
        profiles = dict(plan.profiles)
        if not profiles:
            # no recipient (or all unsubscribed): the period is still generated and kept for download,
            # with the permissions of whoever created the schedule
            creator = await db.get(User, s.created_by) if s.created_by else None
            if creator is not None and creator.tenant_id == tid and creator.is_active:
                from .service import profile_key
                profiles[profile_key(creator)] = creator
        # every profile × format generated before anything is sent (nothing partial)
        for prof, viewer in profiles.items():
            doc, _ = await build(db, tid, s.report_type, s.scope_type, s.scope_id, s.params or {}, viewer, now,
                                 period=per, app=app, observations=(s.params or {}).get("observations"))
            runs[prof] = [await store_run(db, tid, doc, f, prof, viewer, requested_by=d.requested_by,
                                          schedule_id=s.id, dispatch_id=d.id, params=s.params or {}) for f in formats]
        await db.commit()
    except Exception as e:  # noqa: BLE001 — retry policy
        await db.rollback()
        return await _failed_attempt(db, dispatch_id, now, f"{type(e).__name__}: {e}"[:1000])

    channel = mail_channel()
    done_emails = set((await db.execute(select(ReportDelivery.email).where(
        ReportDelivery.dispatch_id == d.id, ReportDelivery.status.in_(("sent", "unsubscribed"))))).scalars().all())
    scope_label = next((r[0].scope_name for r in runs.values() if r), None) or s.scope_type
    link = f"{settings.app_public_url.rstrip('/')}/reports/schedules/{s.id}"
    for rcp in plan.recipients:
        if rcp.email in done_emails:
            continue
        base = dict(tenant_id=tid, dispatch_id=d.id, schedule_id=s.id, email=rcp.email,
                    recipient_user_id=rcp.user.id if rcp.user else None,
                    external_recipient_id=rcp.external.id if rcp.external else None)
        if rcp.skipped == "unsubscribed":
            db.add(ReportDelivery(**base, status="unsubscribed"))
            await db.commit()
            continue
        if rcp.skipped:
            continue
        rs = runs.get(rcp.profile, [])
        if channel is None:
            db.add(ReportDelivery(**base, run_ids=[r.id for r in rs], status="no_channel",
                                  error="e-mail não configurado"))
            await db.commit()
            continue
        status, err, prov, link_only = await send_report_email(db, s, rs, rcp, scope_label, link, now)
        db.add(ReportDelivery(**base, run_ids=[r.id for r in rs], status=status, channel=channel, provider_id=prov,
                              error=err, link_only=link_only, sent_at=now if status == "sent" else None))
        if status == "bounced" and rcp.external is not None:
            await _bounce(db, rcp.external, now)
        await db.commit()
    d = (await db.execute(select(ReportDispatch).where(ReportDispatch.id == dispatch_id))).scalar_one()
    d.status = "no_channel" if channel is None else "sent"
    d.completed_at = now
    d.next_attempt_at = None
    d.last_error = None if channel else "e-mail não configurado"
    if channel is None:
        await warn_no_channel(db, tid, now)
    await db.commit()
    await notif.deliver_pending(db)
    return d.status


async def send_report_email(db: AsyncSession, s: ReportSchedule, runs: list[ReportRun], rcp: dl.Recipient,
                            scope_label: str, link: str, now: datetime, revised: bool = False
                            ) -> tuple[str, Optional[str], Optional[str], bool]:
    from ...models import ReportFile
    files = []
    total = 0
    for r in runs:
        f = await db.get(ReportFile, r.id)
        if f is None or f.tenant_id != s.tenant_id:
            continue
        files.append((r.filename, r.content_type, f.content))
        total += len(f.content)
    link_only = total > settings.reports_attachment_max_bytes
    if link_only:
        urls = [f"{settings.app_public_url.rstrip('/')}/api/report-runs/{r.id}/download?link="
                f"{dl.download_link_token(s.tenant_id, r.id, now)}" for r in runs]
        note = "Anexos acima de 10 MB: baixe pelo link (válido por 7 dias, exige login): " + " ".join(urls)
        files = []
    else:
        note = "Relatório em anexo (" + ", ".join(r.format.upper() for r in runs) + ")."
    unsub = dl.unsubscribe_url(s.tenant_id, rcp.email, s.id)
    summary = runs[0].summary if runs else {}
    per_label = runs[0].period_label if runs else ""
    text, html = dl.compose(summary, s.report_type, scope_label, per_label, link, unsub, note, revised=revised)
    subj = dl.subject(s.report_type, scope_label, per_label, revised=revised)
    try:
        _, prov = await send_email_message(rcp.email, subj, text, html, files,
                                           headers={"List-Unsubscribe": f"<{unsub}>",
                                                    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
        return "sent", None, prov, link_only
    except MailBounced as e:
        return "bounced", str(e)[:500], None, link_only
    except MailNotConfigured as e:
        return "no_channel", str(e)[:500], None, link_only
    except Exception as e:  # noqa: BLE001 — recorded, never logged with production data
        return "failed", f"{type(e).__name__}: {e}"[:500], None, link_only


async def _bounce(db: AsyncSession, ext: ReportExternalRecipient, now: datetime) -> None:
    ext.bounce_count = (ext.bounce_count or 0) + 1
    if ext.bounce_count >= 3 and ext.disabled_at is None:
        ext.disabled_at = now
        await notif.notify(db, ext.tenant_id, await _admins(db, ext.tenant_id), "report_recipient_disabled",
                           f"Destinatário externo desativado após 3 devoluções: {ext.name}",
                           link="/reports/schedules", group_key=f"report_bounce:{ext.id}")


async def _failed_attempt(db: AsyncSession, dispatch_id: str, now: datetime, error: str) -> str:
    d = (await db.execute(select(ReportDispatch).where(ReportDispatch.id == dispatch_id).with_for_update())).scalar_one()
    d.last_error = error
    log.warning("reports.generation_failed", tenant_id=d.tenant_id[:8], dispatch_id=d.id, attempt=d.attempts,
                error=error[:200])
    if d.attempts <= len(RETRY_MIN):
        d.next_attempt_at = now + timedelta(minutes=RETRY_MIN[d.attempts - 1])
        await db.commit()
        return "retry"
    d.status = "failed"
    d.completed_at = now
    d.next_attempt_at = None
    s = (await db.execute(select(ReportSchedule).where(ReportSchedule.id == d.schedule_id))).scalar_one()
    db.add(ReportRun(tenant_id=d.tenant_id, schedule_id=s.id, dispatch_id=d.id, report_type=s.report_type,
                     scope_type=s.scope_type, scope_id=s.scope_id, period_key=d.period_key,
                     period_label=(d.period or {}).get("label"), format=(s.formats or ["pdf"])[0], status="failed",
                     error=error, params=s.params or {}))
    await notif.notify(db, d.tenant_id, await _admins(db, d.tenant_id), "report_failed",
                       f"Falha ao gerar o relatório {BY_CODE[s.report_type]['name']} ({(d.period or {}).get('label')})",
                       body="Foram feitas 3 novas tentativas. O relatório não foi enviado.",
                       link=f"/reports/schedules/{s.id}", group_key=f"report_failed:{s.id}")
    await db.commit()
    await notif.deliver_pending(db)
    return "failed"


# ── monthly "versão revisada" (§4.2) ───────────────────────────────────────

def revision_needed(old: dict, new: dict) -> bool:
    for k in REVISION_KEYS_FRACTION:
        a, b = old.get(k), new.get(k)
        if (a is None) != (b is None):
            return True
        if a is not None and abs(a - b) > REVISION_PP:
            return True
    for k in REVISION_KEYS_OTHER:
        a, b = old.get(k), new.get(k)
        if (a is None) != (b is None):
            return True
        if a is not None and abs(a - b) > REVISION_REL * max(abs(a), 1e-12):
            return True
    return False


async def check_monthly_revisions(db: AsyncSession, tenant_id: str, now: datetime, app=None) -> int:
    since = now - timedelta(days=7)
    rows = (await db.execute(select(ReportDispatch, ReportSchedule).join(
        ReportSchedule, ReportSchedule.id == ReportDispatch.schedule_id).where(
        ReportDispatch.tenant_id == tenant_id, ReportSchedule.report_type == "monthly_maintenance",
        ReportDispatch.status == "sent", ReportDispatch.completed_at >= since, ReportDispatch.manual.is_(False))
    )).all()
    n = 0
    for d, s in rows:
        per = dict(d.period or {})
        if per.get("revision_checked_on") == now.date().isoformat():
            continue
        per["revision_checked_on"] = now.date().isoformat()
        d.period = per
        await db.commit()
        last = (await db.execute(select(ReportRun).where(
            ReportRun.tenant_id == tenant_id, ReportRun.schedule_id == s.id, ReportRun.period_key == d.period_key,
            ReportRun.status == "done").order_by(ReportRun.created_at.desc()).limit(1))).scalar_one_or_none()
        if last is None:
            continue
        viewer = await db.get(User, last.viewer_user_id) if last.viewer_user_id else None
        if viewer is None or not viewer.is_active:
            continue
        try:
            doc, _ = await build(db, tenant_id, s.report_type, s.scope_type, s.scope_id, s.params or {}, viewer, now,
                                 period=_period_from(d.period, s.report_type), app=app)
        except Exception as e:  # noqa: BLE001
            log.warning("reports.revision_check_failed", dispatch_id=d.id, error=str(e)[:200])
            continue
        old = (last.summary or {}).get("numbers", {})
        new = {k: v for k, v in doc.summary.items() if not k.startswith("_")}
        if not revision_needed(old, new):
            continue
        n += 1
        await send_revision(db, s, d, now, app=app)
    return n


async def send_revision(db: AsyncSession, s: ReportSchedule, d: ReportDispatch, now: datetime, app=None) -> None:
    cal, _, line_ids = await schedule_calendar(db, s)
    plan = await dl.resolve_recipients(db, s, set(line_ids))
    per = _period_from(d.period, s.report_type)
    runs: dict[str, list[ReportRun]] = {}
    for prof, viewer in plan.profiles.items():
        doc, _ = await build(db, s.tenant_id, s.report_type, s.scope_type, s.scope_id, s.params or {}, viewer, now,
                             period=per, app=app)
        runs[prof] = [await store_run(db, s.tenant_id, doc, f, prof, viewer, requested_by=None, schedule_id=s.id,
                                      dispatch_id=d.id, params=s.params or {}) for f in (s.formats or ["pdf"])]
    await db.commit()
    if mail_channel() is None:
        await warn_no_channel(db, s.tenant_id, now)
        await db.commit()
        return
    link = f"{settings.app_public_url.rstrip('/')}/reports/schedules/{s.id}"
    for rcp in plan.recipients:
        if rcp.skipped:
            continue
        rs = runs.get(rcp.profile, [])
        status, err, prov, link_only = await send_report_email(db, s, rs, rcp, rs[0].scope_name if rs else "",
                                                               link, now, revised=True)
        db.add(ReportDelivery(tenant_id=s.tenant_id, dispatch_id=d.id, schedule_id=s.id, email=rcp.email,
                              recipient_user_id=rcp.user.id if rcp.user else None,
                              external_recipient_id=rcp.external.id if rcp.external else None,
                              run_ids=[r.id for r in rs], status=status, channel=mail_channel(), provider_id=prov,
                              error=err, link_only=link_only, sent_at=now if status == "sent" else None))
        await db.commit()


# ── loop ───────────────────────────────────────────────────────────────────

async def tick(db_factory, now: Optional[datetime] = None, app=None, tenant_id: Optional[str] = None) -> dict:
    now = now or clock.now()
    out = {"planned": 0, "processed": 0, "revisions": 0}
    async with db_factory() as db:
        q = select(Tenant.id).where(Tenant.is_active.is_(True))
        if tenant_id:
            q = q.where(Tenant.id == tenant_id)
        tenants = (await db.execute(q)).scalars().all()
    for tid in tenants:
        async with db_factory() as db:
            try:
                out["planned"] += await plan_dispatches(db, tid, now)
            except Exception as e:  # noqa: BLE001
                log.error("reports.plan_error", tenant_id=tid[:8], error=f"{type(e).__name__}: {e}"[:300])
    while True:
        async with db_factory() as db:
            ids = await claim(db, now, tenant_id=tenant_id)
        if not ids:
            break
        for did in ids:
            async with db_factory() as db:
                try:
                    await process(db, did, now, app=app)
                    out["processed"] += 1
                except Exception as e:  # noqa: BLE001
                    log.error("reports.process_error", dispatch_id=did, error=f"{type(e).__name__}: {e}"[:300])
    for tid in tenants:
        async with db_factory() as db:
            try:
                out["revisions"] += await check_monthly_revisions(db, tid, now, app=app)
            except Exception as e:  # noqa: BLE001
                log.error("reports.revision_error", tenant_id=tid[:8], error=f"{type(e).__name__}: {e}"[:300])
    return out


async def run_report_engine() -> None:
    from ...db import AsyncSessionLocal
    while True:
        try:
            if settings.reports_enabled:
                await tick(AsyncSessionLocal)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("reports.engine.error", error=str(e)[:300])
        await asyncio.sleep(settings.reports_tick_s)
