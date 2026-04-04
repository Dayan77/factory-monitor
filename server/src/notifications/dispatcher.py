"""
Notification dispatcher — routes alert events to the configured channels.
"""

import structlog
from ..models import AlertRule, AlertEvent

log = structlog.get_logger()


async def dispatch(rule: AlertRule, event: AlertEvent, value: float) -> None:
    subject = f"[{rule.severity.value.upper()}] {rule.name}"
    body = (
        f"Regra '{rule.name}' disparada.\n"
        f"Valor medido: {value}\n"
        f"Horário: {event.triggered_at.strftime('%d/%m/%Y %H:%M:%S UTC')}"
    )

    if rule.notify_email:
        await _send_email(subject, body)
    if rule.notify_telegram:
        await _send_telegram(f"{subject}\n{body}")
    if rule.notify_whatsapp:
        await _send_whatsapp(f"{subject}\n{body}")


async def _send_email(subject: str, body: str) -> None:
    from ..config import settings
    if not settings.sendgrid_api_key:
        log.warning("notifications.email.skipped", reason="SENDGRID_API_KEY not set")
        return

    import httpx
    payload = {
        "personalizations": [{"to": [{"email": settings.sendgrid_from_email}]}],
        "from": {"email": settings.sendgrid_from_email},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.sendgrid.com/v3/mail/send",
            json=payload,
            headers={"Authorization": f"Bearer {settings.sendgrid_api_key}"},
            timeout=10,
        )
    if resp.status_code >= 400:
        log.error("notifications.email.failed", status=resp.status_code)
    else:
        log.info("notifications.email.sent")


async def _send_telegram(text: str) -> None:
    from ..config import settings
    if not settings.telegram_bot_token:
        log.warning("notifications.telegram.skipped", reason="TELEGRAM_BOT_TOKEN not set")
        return
    # TODO: store chat_id per tenant/user
    log.info("notifications.telegram.stub", text=text[:80])


async def _send_whatsapp(text: str) -> None:
    log.info("notifications.whatsapp.stub", text=text[:80])
