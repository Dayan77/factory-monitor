"""E-mail distribution of reports (docs/reports.md §5).

* recipients: users of the tenant by role (§5.1 defaults; the admin adjusts) and area (User.line_ids), plus
  external e-mails only when the admin enabled `external_recipients_enabled` (LGPD: legal basis, one-click
  unsubscribe, "não quero mais receber" link in every e-mail);
* content per recipient permissions: recipients are grouped by permission profile (roles + lines) and each
  group gets its own generation with a representative user's token (an operator never receives WO hours);
* subject `[FactoryConnect] <Relatório> — <escopo> — <período>`; body = 3–5 headline numbers + incomplete-data
  warning + link; attachments PDF (and XLSX when selected); above 10 MB → signed link only (7 days, login);
* every send is recorded (recipient, time, provider id, result); 3 bounces disable an external recipient.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html import escape
from typing import Optional

from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models import (ReportExternalRecipient, ReportSchedule, ReportSettings, ReportUnsubscribe, User)
from .catalog import BY_CODE

UNSUB_PURPOSE = "report_unsubscribe"
LINK_PURPOSE = "report_link"


# ── signed tokens ─────────────────────────────────────────────────────────

def unsubscribe_token(tenant_id: str, email: str, schedule_id: Optional[str]) -> str:
    return jwt.encode({"purpose": UNSUB_PURPOSE, "t": tenant_id, "e": email.lower(), "s": schedule_id},
                      settings.app_secret_key, algorithm=settings.jwt_algorithm)


def read_unsubscribe_token(token: str) -> Optional[dict]:
    try:
        p = jwt.decode(token, settings.app_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
    if p.get("purpose") != UNSUB_PURPOSE or not p.get("t") or not p.get("e"):
        return None
    return p


def unsubscribe_url(tenant_id: str, email: str, schedule_id: Optional[str]) -> str:
    return (f"{settings.app_public_url.rstrip('/')}/api/reports/unsubscribe?token="
            f"{unsubscribe_token(tenant_id, email, schedule_id)}")


def download_link_token(tenant_id: str, run_id: str, now: datetime, days: int = 7) -> str:
    return jwt.encode({"purpose": LINK_PURPOSE, "t": tenant_id, "r": run_id,
                       "exp": int((now + timedelta(days=days)).timestamp())},
                      settings.app_secret_key, algorithm=settings.jwt_algorithm)


def read_link_token(token: str) -> Optional[dict]:
    try:
        p = jwt.decode(token, settings.app_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
    return p if p.get("purpose") == LINK_PURPOSE else None


# ── recipients ────────────────────────────────────────────────────────────

@dataclass
class Recipient:
    email: str
    user: Optional[User] = None            # tenant user (content generated with their permissions)
    external: Optional[ReportExternalRecipient] = None
    profile: str = ""
    viewer: Optional[User] = None           # whose permissions generate the content
    skipped: Optional[str] = None           # unsubscribed | no_lines_in_scope


@dataclass
class RecipientPlan:
    recipients: list[Recipient] = field(default_factory=list)
    profiles: dict[str, User] = field(default_factory=dict)    # profile key → representative viewer


def default_recipients(report_type: str) -> dict:
    return {"roles": list(BY_CODE[report_type]["recipients"]), "user_ids": [], "external_ids": []}


async def settings_for(db: AsyncSession, tenant_id: str) -> ReportSettings:
    s = await db.get(ReportSettings, tenant_id)
    if s is None:
        s = ReportSettings(tenant_id=tenant_id, external_recipients_enabled=False)
        db.add(s)
        await db.flush()
    return s


def _in_scope(user: User, schedule: ReportSchedule, scope_line_ids: set[str]) -> bool:
    """Area filter (§5.1): user lines empty = every line; otherwise they must intersect the scope's lines."""
    area = BY_CODE[schedule.report_type]["area_filter"]
    if area in ("tenant", "plant"):
        return True
    mine = {str(x) for x in (user.line_ids or [])}
    return not mine or bool(mine & scope_line_ids)


async def resolve_recipients(db: AsyncSession, schedule: ReportSchedule, scope_line_ids: set[str]) -> RecipientPlan:
    from .service import profile_key
    tid = schedule.tenant_id
    spec = schedule.recipients or default_recipients(schedule.report_type)
    roles = set(spec.get("roles") or [])
    user_ids = {str(x) for x in (spec.get("user_ids") or [])}
    users = (await db.execute(select(User).where(User.tenant_id == tid, User.is_active.is_(True)))).scalars().all()
    unsub = (await db.execute(select(ReportUnsubscribe.email, ReportUnsubscribe.schedule_id).where(
        ReportUnsubscribe.tenant_id == tid))).all()
    blocked = {e.lower() for e, s in unsub if s is None or s == schedule.id}
    area = BY_CODE[schedule.report_type]["area_filter"] in ("user_lines", "report_line")
    plan = RecipientPlan()
    seen: set[str] = set()
    for u in sorted(users, key=lambda u: u.email):
        if not ((u.role_set & roles) or u.id in user_ids):
            continue
        if u.email.lower() in seen:
            continue
        seen.add(u.email.lower())
        r = Recipient(email=u.email, user=u)
        if u.email.lower() in blocked:
            r.skipped = "unsubscribed"
        elif not _in_scope(u, schedule, scope_line_ids):
            r.skipped = "no_lines_in_scope"
        else:
            r.profile = profile_key(u, area)
            r.viewer = u
            plan.profiles.setdefault(r.profile, u)
        plan.recipients.append(r)
    cfg = await settings_for(db, tid)
    ext_ids = [x for x in (spec.get("external_ids") or [])]
    if ext_ids and cfg.external_recipients_enabled:
        creator = await db.get(User, schedule.created_by) if schedule.created_by else None
        if creator is not None and (creator.tenant_id != tid or not creator.is_active):
            creator = None
        exts = (await db.execute(select(ReportExternalRecipient).where(
            ReportExternalRecipient.tenant_id == tid, ReportExternalRecipient.id.in_(ext_ids),
            ReportExternalRecipient.disabled_at.is_(None)))).scalars().all()
        for e in exts:
            if e.email.lower() in seen or creator is None:
                continue
            seen.add(e.email.lower())
            r = Recipient(email=e.email, external=e)
            if e.email.lower() in blocked:
                r.skipped = "unsubscribed"
            else:
                # external e-mails receive what the schedule's creator (admin/manager) sees, by the admin's choice
                r.profile = "external|" + profile_key(creator, area)
                r.viewer = creator
                plan.profiles.setdefault(r.profile, creator)
            plan.recipients.append(r)
    return plan


# ── message ───────────────────────────────────────────────────────────────

def subject(report_type: str, scope_label: str, period_label: str, revised: bool = False) -> str:
    name = BY_CODE[report_type]["name"]
    s = f"[FactoryConnect] {name} — {scope_label} — {period_label}"
    return s + (" (versão revisada)" if revised else "")


def compose(run_summary: dict, report_type: str, scope_label: str, period_label: str, link: str, unsub: str,
            attachments_note: str, revised: bool = False) -> tuple[str, str]:
    """Plain text + simple HTML: 3–5 headline numbers, incomplete-data warning, link, unsubscribe."""
    head = (run_summary.get("headline") or [])[:5]
    banners = run_summary.get("banners") or []
    omitted = run_summary.get("omitted") or []
    title = BY_CODE[report_type]["title"]
    lines = [f"{title} — {scope_label} — {period_label}", ""]
    if revised:
        lines += ["Versão revisada: dados atrasados ou reclassificados mudaram os indicadores do mês.", ""]
    for b in banners:
        lines.append(f"ATENÇÃO: {b}")
    if banners:
        lines.append("")
    lines += [f"• {k}: {v}" for k, v in head]
    lines += ["", attachments_note, f"Abrir no FactoryConnect: {link}"]
    if omitted:
        lines += ["", "Seções omitidas por permissão: " + "; ".join(omitted)]
    lines += ["", "Não quero mais receber este relatório: " + unsub]
    text = "\n".join(lines)
    h = [f"<h2 style='font-family:sans-serif;color:#1F3A5F'>{escape(title)}</h2>",
         f"<p style='font-family:sans-serif'>{escape(scope_label)} — {escape(period_label)}</p>"]
    if revised:
        h.append("<p style='font-family:sans-serif'><b>Versão revisada</b>: dados atrasados ou reclassificados "
                 "mudaram os indicadores do mês.</p>")
    for b in banners:
        h.append(f"<p style='font-family:sans-serif;background:#FFF4CE;color:#7A4B00;padding:6px'>"
                 f"<b>Atenção:</b> {escape(b)}</p>")
    h.append("<ul style='font-family:sans-serif'>" + "".join(
        f"<li>{escape(k)}: <b>{escape(v)}</b></li>" for k, v in head) + "</ul>")
    h.append(f"<p style='font-family:sans-serif'>{escape(attachments_note)} "
             f"<a href='{escape(link)}'>Abrir no FactoryConnect</a></p>")
    if omitted:
        h.append(f"<p style='font-family:sans-serif;color:#6B7280'>Seções omitidas por permissão: "
                 f"{escape('; '.join(omitted))}</p>")
    h.append(f"<p style='font-family:sans-serif;font-size:12px;color:#6B7280'>"
             f"<a href='{escape(unsub)}'>Não quero mais receber este relatório</a></p>")
    return text, "\n".join(h)
