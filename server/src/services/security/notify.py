"""Security e-mails (password reset, invitation) and the security audit helper.

E-mails are sent after the response (BackgroundTasks) so the timing of "request reset" does not reveal whether the
account exists; without a mail channel the event is logged (never the link: it is a credential).
"""
from __future__ import annotations

from typing import Optional

import structlog
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings

log = structlog.get_logger()


def client_ip(request: Optional[Request]) -> str:
    # ProxyHeadersMiddleware (main.py, FORWARDED_ALLOW_IPS) already rewrote request.client to the real client
    return request.client.host if request is not None and request.client else "?"


async def send_mail(to: str, subject: str, body: str) -> None:
    from ..mailer import MailNotConfigured, send_email
    try:
        await send_email(to, subject, body)
    except MailNotConfigured:
        log.warning("security.mail_not_configured", subject=subject)
    except Exception as e:  # noqa: BLE001 — never surfaces to the requester (no enumeration)
        log.error("security.mail_failed", subject=subject, error=str(e)[:200])


def reset_link(token: str) -> str:
    return f"{settings.app_public_url.rstrip('/')}/redefinir-senha?token={token}"


def invite_link(token: str) -> str:
    return f"{settings.app_public_url.rstrip('/')}/convite?token={token}"


async def send_reset_email(to: str, name: str, token: str) -> None:
    body = (f"Olá, {name}.\n\nRecebemos um pedido para redefinir a sua senha no FactoryConnect.\n"
            f"Use o link abaixo em até {settings.password_reset_ttl_min} minutos (vale uma única vez):\n\n"
            f"{reset_link(token)}\n\nSe não foi você, ignore este e-mail: sua senha continua a mesma.\n")
    await send_mail(to, "FactoryConnect — redefinição de senha", body)


async def send_invite_email(to: str, tenant_name: str, inviter: str, token: str) -> None:
    body = (f"Olá!\n\n{inviter} convidou você para usar o FactoryConnect em {tenant_name}.\n"
            f"Crie sua senha pelo link abaixo (válido por {int(settings.invite_ttl_hours // 24)} dias):\n\n"
            f"{invite_link(token)}\n")
    await send_mail(to, f"Convite para o FactoryConnect — {tenant_name}", body)


async def audit(db: AsyncSession, tenant_id: Optional[str], user_id: Optional[str], action: str,
                entity_type: str = "user", entity_id: Optional[str] = None, data: Optional[dict] = None,
                request: Optional[Request] = None) -> None:
    """Security audit event (audit_events). Never a password, token or full IP — only the /24 prefix."""
    if not tenant_id:
        log.info("security.audit_no_tenant", action=action)
        return
    from ..audit import record
    from .sessions import ip_prefix
    d = dict(data or {})
    if request is not None:
        d.setdefault("ip", ip_prefix(client_ip(request)))
    await record(db, tenant_id, user_id, action, entity_type, entity_id, d)
