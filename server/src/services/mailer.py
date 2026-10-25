"""
Transactional e-mail (lead notifications).

Channel: SendGrid when SENDGRID_API_KEY is set, else SMTP when SMTP_HOST is set, else none (the
message is logged without personal data and the caller decides what to do).
SMTP: SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_TLS (true → STARTTLS,
or implicit TLS on port 465).
"""

import asyncio
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Optional

import httpx
import structlog

from ..config import settings

log = structlog.get_logger()


class MailNotConfigured(Exception):
    pass


def _clean_header(v: str) -> str:
    return " ".join((v or "").split())[:200]


def _smtp_send(to: str, subject: str, body: str, reply_to: Optional[str]) -> None:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to
    msg["Subject"] = _clean_header(subject)
    if reply_to:
        msg["Reply-To"] = _clean_header(reply_to)
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if settings.smtp_tls and settings.smtp_port == 465:
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15, context=ctx)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
    try:
        if settings.smtp_tls and settings.smtp_port != 465:
            server.starttls(context=ctx)
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass


async def _sendgrid_send(to: str, subject: str, body: str, reply_to: Optional[str]) -> None:
    payload = {
        "personalizations": [{"to": [{"email": to}]}],
        "from": {"email": settings.sendgrid_from_email or settings.smtp_from},
        "subject": _clean_header(subject),
        "content": [{"type": "text/plain", "value": body}],
    }
    if reply_to:
        payload["reply_to"] = {"email": reply_to}
    async with httpx.AsyncClient() as client:
        resp = await client.post("https://api.sendgrid.com/v3/mail/send", json=payload,
                                 headers={"Authorization": f"Bearer {settings.sendgrid_api_key}"}, timeout=15)
    if resp.status_code >= 400:
        raise RuntimeError(f"sendgrid status {resp.status_code}")


async def send_email(to: str, subject: str, body: str, reply_to: Optional[str] = None) -> str:
    """Returns the channel used ('sendgrid' | 'smtp'). Raises MailNotConfigured / on failure."""
    if not to:
        raise MailNotConfigured("LEADS_TO_EMAIL não configurado")
    if settings.sendgrid_api_key:
        await _sendgrid_send(to, subject, body, reply_to)
        return "sendgrid"
    if settings.smtp_host:
        await asyncio.to_thread(_smtp_send, to, subject, body, reply_to)
        return "smtp"
    raise MailNotConfigured("nem SENDGRID_API_KEY nem SMTP_HOST configurados")


# ── messages with HTML, attachments and extra headers (Sprint 6 — reports.md §5) ──────────────────────────

def mail_channel() -> Optional[str]:
    """'sendgrid' | 'smtp' | None (no channel configured — callers must warn, never fail silently)."""
    if settings.sendgrid_api_key:
        return "sendgrid"
    if settings.smtp_host:
        return "smtp"
    return None


class MailBounced(Exception):
    """The provider refused the recipient (counts as a bounce)."""


def _smtp_send_message(to: str, subject: str, text: str, html: Optional[str], attachments, headers: dict) -> str:
    from email.utils import make_msgid
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to
    msg["Subject"] = _clean_header(subject)
    msg_id = make_msgid(domain=(settings.smtp_from or "factoryconnect.local").split("@")[-1] or None)
    msg["Message-ID"] = msg_id
    for k, v in (headers or {}).items():
        msg[k] = _clean_header(v) if k.lower() != "list-unsubscribe" else v
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    for filename, ctype, content in attachments or ():
        maintype, _, subtype = ctype.partition("/")
        msg.add_attachment(content, maintype=maintype, subtype=subtype or "octet-stream", filename=filename)
    ctx = ssl.create_default_context()
    if settings.smtp_tls and settings.smtp_port == 465:
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30, context=ctx)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
    try:
        if settings.smtp_tls and settings.smtp_port != 465:
            server.starttls(context=ctx)
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        try:
            refused = server.send_message(msg)
        except smtplib.SMTPRecipientsRefused as e:
            raise MailBounced(str(e)[:300]) from None
        if refused:
            raise MailBounced(str(refused)[:300])
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass
    return msg_id


async def _sendgrid_send_message(to: str, subject: str, text: str, html: Optional[str], attachments,
                                 headers: dict) -> Optional[str]:
    import base64
    content = [{"type": "text/plain", "value": text}]
    if html:
        content.append({"type": "text/html", "value": html})
    payload = {
        "personalizations": [{"to": [{"email": to}]}],
        "from": {"email": settings.sendgrid_from_email or settings.smtp_from},
        "subject": _clean_header(subject),
        "content": content,
    }
    if headers:
        payload["headers"] = {k: v for k, v in headers.items()}
    if attachments:
        payload["attachments"] = [{"content": base64.b64encode(c).decode(), "type": t, "filename": f,
                                   "disposition": "attachment"} for f, t, c in attachments]
    async with httpx.AsyncClient() as client:
        resp = await client.post("https://api.sendgrid.com/v3/mail/send", json=payload,
                                 headers={"Authorization": f"Bearer {settings.sendgrid_api_key}"}, timeout=60)
    if resp.status_code >= 400:
        raise RuntimeError(f"sendgrid status {resp.status_code}")
    return resp.headers.get("X-Message-Id")


async def send_email_message(to: str, subject: str, text: str, html: Optional[str] = None,
                             attachments: Optional[list[tuple[str, str, bytes]]] = None,
                             headers: Optional[dict] = None) -> tuple[str, Optional[str]]:
    """E-mail with optional HTML part, attachments [(filename, content_type, bytes)] and extra headers.
    Returns (channel, provider message id). Raises MailNotConfigured / MailBounced / on failure."""
    if not to:
        raise MailNotConfigured("destinatário vazio")
    channel = mail_channel()
    if channel == "sendgrid":
        return channel, await _sendgrid_send_message(to, subject, text, html, attachments, headers or {})
    if channel == "smtp":
        return channel, await asyncio.to_thread(_smtp_send_message, to, subject, text, html, attachments, headers or {})
    raise MailNotConfigured("nem SENDGRID_API_KEY nem SMTP_HOST configurados")


def lead_email(lead) -> tuple[str, str]:
    subject = f"[FactoryConnect] Novo contato do site: {lead.name}" + (f" ({lead.company})" if lead.company else "")
    body = "\n".join([
        "Novo contato recebido pelo site factoryconnect.tech.",
        "",
        f"Nome:     {lead.name}",
        f"E-mail:   {lead.email}",
        f"Empresa:  {lead.company or '-'}",
        f"Telefone: {lead.phone or '-'}",
        f"Origem:   {lead.source or '-'}",
        f"Recebido: {lead.created_at:%d/%m/%Y %H:%M UTC}" if lead.created_at else "",
        f"Consentimento LGPD: sim ({lead.consent_at:%d/%m/%Y %H:%M UTC})",
        "",
        "Mensagem:",
        lead.message or "-",
        "",
        f"ID do lead: {lead.id}",
    ])
    return subject, body


async def notify_lead(lead_id: str) -> None:
    """Background task: e-mails the lead to LEADS_TO_EMAIL and records the outcome on the row."""
    from ..db import AsyncSessionLocal
    from ..models import Lead

    async with AsyncSessionLocal() as db:
        lead = await db.get(Lead, lead_id)
        if lead is None:
            return
        subject, body = lead_email(lead)
        try:
            channel = await send_email(settings.leads_to_email, subject, body, reply_to=lead.email)
            lead.notified_at = datetime.now(timezone.utc)
            lead.notify_error = None
            log.info("leads.notified", lead_id=lead.id, channel=channel)
        except MailNotConfigured as e:
            lead.notify_error = f"not_configured: {e}"[:500]
            log.warning("leads.notify_skipped", lead_id=lead.id, reason=str(e))
        except Exception as e:  # noqa: BLE001
            lead.notify_error = f"{type(e).__name__}: {e}"[:500]
            log.error("leads.notify_failed", lead_id=lead.id, error=type(e).__name__)
        await db.commit()
