"""POST /api/public/leads: store, honeypot, consent, per-IP rate limit, e-mail notification, CORS."""
import uuid

import httpx
import pytest
from sqlalchemy import select


def lead(**kw):
    body = {"name": "Maria Silva", "email": f"maria.{uuid.uuid4().hex[:6]}@example.com", "company": "Metal SA",
            "phone": "+55 92 99999-0000", "message": "Quero uma demo", "consent": True, "website": ""}
    body.update(kw)
    return body


def client_from(app, ip: str) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(ip, 12345))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def sent(monkeypatch):
    calls = []

    async def fake_send(to, subject, body, reply_to=None):
        calls.append({"to": to, "subject": subject, "body": body, "reply_to": reply_to})
        return "smtp"

    from src.services import mailer
    monkeypatch.setattr(mailer, "send_email", fake_send)
    monkeypatch.setattr(mailer.settings, "leads_to_email", "vendas@factoryconnect.tech")
    return calls


async def _lead_by_email(db, email):
    from src.models import Lead
    db.expire_all()
    return (await db.execute(select(Lead).where(Lead.email == email))).scalar_one_or_none()


async def test_lead_is_stored_and_notified_without_auth(app, db, sent):
    body = lead()
    async with client_from(app, "203.0.113.10") as c:
        r = await c.post("/api/public/leads", json=body)
    assert r.status_code == 202 and r.json() == {"status": "accepted"}
    row = await _lead_by_email(db, body["email"])
    assert row is not None and row.consent is True and row.company == "Metal SA"
    assert row.ip_hash and "203.0.113.10" not in row.ip_hash        # IP only as a keyed hash
    assert row.notified_at is not None and row.notify_error is None
    assert len(sent) == 1 and sent[0]["to"] == "vendas@factoryconnect.tech"
    assert sent[0]["reply_to"] == body["email"] and "Quero uma demo" in sent[0]["body"]


async def test_honeypot_returns_202_but_stores_nothing(app, db, sent):
    body = lead(website="http://spam.example")
    async with client_from(app, "203.0.113.11") as c:
        r = await c.post("/api/public/leads", json=body)
    assert r.status_code == 202
    assert await _lead_by_email(db, body["email"]) is None
    assert sent == []


@pytest.mark.parametrize("override", [{"consent": False}, {"email": "not-an-email"}, {"name": ""},
                                      {"message": "x" * 5001}])
async def test_invalid_leads_are_422(app, override, sent):
    body = lead(**override)
    if override.get("consent") is False:
        body["consent"] = False
    async with client_from(app, "203.0.113.12") as c:
        r = await c.post("/api/public/leads", json=body)
    assert r.status_code == 422


async def test_missing_consent_is_422(app):
    body = lead()
    del body["consent"]
    async with client_from(app, "203.0.113.13") as c:
        assert (await c.post("/api/public/leads", json=body)).status_code == 422


async def test_rate_limit_per_ip(app, db, sent):
    from src.config import settings
    ip = f"198.51.100.{uuid.uuid4().int % 250}"
    async with client_from(app, ip) as c:
        for _ in range(settings.leads_rate_limit_per_hour):
            assert (await c.post("/api/public/leads", json=lead())).status_code == 202
        blocked = lead()
        r = await c.post("/api/public/leads", json=blocked)
        assert r.status_code == 429
    assert await _lead_by_email(db, blocked["email"]) is None
    async with client_from(app, "192.0.2.77") as other:           # other IPs unaffected
        assert (await other.post("/api/public/leads", json=lead())).status_code == 202


async def test_spoofed_left_most_x_forwarded_for_does_not_change_rate_limit_key(app, db, sent):
    """Behind a proxy that appends to X-Forwarded-For (nginx $proxy_add_x_forwarded_for, Traefik),
    the client controls the left-most entries. The key must be the right-most untrusted entry."""
    from src.config import settings
    real = f"203.0.113.{uuid.uuid4().int % 250}"
    proxy = "172.18.0.5"                                   # docker network → trusted proxy
    async with client_from(app, proxy) as c:
        for i in range(settings.leads_rate_limit_per_hour):
            r = await c.post("/api/public/leads", json=lead(), headers={"X-Forwarded-For": f"10.9.{i}.1, {real}"})
            assert r.status_code == 202
        r = await c.post("/api/public/leads", json=lead(), headers={"X-Forwarded-For": f"8.8.8.8, 1.1.1.1, {real}"})
        assert r.status_code == 429


async def test_x_forwarded_for_from_an_untrusted_peer_is_ignored(app, db, sent):
    """A client talking to the API directly cannot pick its IP via X-Forwarded-For."""
    from src.config import settings
    ip = f"198.51.100.{uuid.uuid4().int % 250}"
    async with client_from(app, ip) as c:
        for i in range(settings.leads_rate_limit_per_hour):
            r = await c.post("/api/public/leads", json=lead(), headers={"X-Forwarded-For": f"192.0.2.{i}"})
            assert r.status_code == 202
        r = await c.post("/api/public/leads", json=lead(), headers={"X-Forwarded-For": "192.0.2.200"})
        assert r.status_code == 429


def test_wildcard_forwarded_allow_ips_is_rejected():
    import subprocess
    import sys
    env = {**__import__("os").environ, "FORWARDED_ALLOW_IPS": "*"}
    r = subprocess.run([sys.executable, "-c", "import src.main"], cwd=str(__import__("pathlib").Path(__file__).parent.parent),
                       env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "FORWARDED_ALLOW_IPS" in r.stderr


async def test_without_mail_config_lead_is_stored_and_error_recorded(app, db, monkeypatch):
    from src.services import mailer
    monkeypatch.setattr(mailer.settings, "leads_to_email", "vendas@factoryconnect.tech")
    monkeypatch.setattr(mailer.settings, "sendgrid_api_key", "")
    monkeypatch.setattr(mailer.settings, "smtp_host", "")
    body = lead()
    async with client_from(app, "203.0.113.20") as c:
        assert (await c.post("/api/public/leads", json=body)).status_code == 202
    row = await _lead_by_email(db, body["email"])
    assert row is not None and row.notified_at is None and row.notify_error.startswith("not_configured")


async def test_smtp_channel_builds_message(monkeypatch):
    """SMTP path with a fake server: STARTTLS + login + send, headers without injection."""
    from src.services import mailer
    seen = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            seen["host"], seen["port"] = host, port

        def starttls(self, context=None):
            seen["tls"] = True

        def login(self, u, p):
            seen["login"] = u

        def send_message(self, msg):
            seen["msg"] = msg

        def quit(self):
            pass

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    for k, v in {"sendgrid_api_key": "", "smtp_host": "smtp.example", "smtp_port": 587, "smtp_user": "u",
                 "smtp_password": "p", "smtp_from": "site@factoryconnect.tech", "smtp_tls": True}.items():
        monkeypatch.setattr(mailer.settings, k, v)
    channel = await mailer.send_email("vendas@factoryconnect.tech", "Novo\r\nBcc: x@evil", "corpo", "a@b.com")
    assert channel == "smtp" and seen["tls"] and seen["login"] == "u"
    msg = seen["msg"]
    assert msg["To"] == "vendas@factoryconnect.tech" and "\n" not in msg["Subject"]
    assert msg["Bcc"] is None and msg["Reply-To"] == "a@b.com"


@pytest.mark.parametrize("origin", ["https://factoryconnect.tech", "https://app.factoryconnect.tech"])
async def test_cors_allows_public_site_and_app(client, origin):
    r = await client.options("/api/public/leads", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == origin


async def test_cors_rejects_unknown_origin(client):
    r = await client.options("/api/public/leads", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") is None
