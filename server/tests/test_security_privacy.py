"""Sprint 7 — LGPD: personal-data export, self-service and admin anonymization (history kept under a pseudonym),
lead/audit retention (docs/privacy.md)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from sec_helpers import STRONG, audit_actions, bearer, login, make_sec_tenant


@pytest.fixture
async def st(app):
    return await make_sec_tenant("Lgpd", roles=("admin", "manager", "operator", "technician"))


async def _api_token(client, h, scopes=("read", "write")) -> str:
    r = await client.post("/api/api-tokens/", json={"name": "erp", "scopes": list(scopes)}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["token"]


async def test_export_contains_personal_data_only_of_the_user(client, st):
    t = (await login(client, st.email("manager"))).json()
    h = bearer(t["access_token"])
    r = await client.get("/api/account/export", headers=h)
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"] and r.headers["cache-control"] == "no-store"
    d = r.json()
    assert d["format"].startswith("factoryconnect.personal-data-export/")
    assert d["user"]["email"] == st.email("manager") and d["controller"]["tenant_id"] == st.id
    assert any(s["id"] == t["session_id"] for s in d["sessions"])
    assert any(a["action"] == "auth.login" for a in d["audit_events"])
    raw = r.text
    assert "password" not in raw.replace("password_changed_at", "") and "$2b$" not in raw
    assert st.email("operator") not in raw
    assert "account.data_exported" in await audit_actions(st.users["manager"]["id"])


async def test_api_token_and_demo_cannot_use_account_area(client, st):
    adm = bearer((await login(client, st.email("admin"))).json()["access_token"])
    tok = await _api_token(client, adm)
    for method, path, body in (("GET", "/api/account/export", None), ("GET", "/api/account/me", None),
                               ("DELETE", "/api/account", {"password": STRONG, "confirm": "EXCLUIR"}),
                               ("GET", "/api/account/sessions", None)):
        r = await client.request(method, path, json=body, headers=bearer(tok))
        assert r.status_code == 403, (path, r.text)
    assert (await client.post("/api/auth/logout-all", headers=bearer(tok))).status_code == 403
    # the admin who created the token is untouched
    assert (await client.get("/api/auth/me", headers=adm)).status_code == 200


async def test_self_service_deletion_anonymizes_and_keeps_history(client, st):
    from src.db import AsyncSessionLocal
    from src.models import AuditEvent, Notification, User
    uid = st.users["technician"]["id"]
    async with AsyncSessionLocal() as s:
        s.add(Notification(tenant_id=st.id, user_id=uid, kind="x", title="Olá técnico", priority="normal"))
        await s.commit()
    t = (await login(client, st.email("technician"))).json()
    h = bearer(t["access_token"])
    r = await client.request("DELETE", "/api/account", json={"password": STRONG, "confirm": "sim"}, headers=h)
    assert r.status_code == 422
    r = await client.request("DELETE", "/api/account", json={"password": "wrong-wrong-wrong", "confirm": "EXCLUIR"},
                             headers=h)
    assert r.status_code == 403
    r = await client.request("DELETE", "/api/account", json={"password": STRONG, "confirm": "EXCLUIR"}, headers=h)
    assert r.status_code == 204
    assert (await client.get("/api/auth/me", headers=h)).status_code == 401
    await login(client, st.email("technician"), expect=401)
    async with AsyncSessionLocal() as s:
        u = await s.get(User, uid)
        assert u.anonymized_at is not None and not u.is_active and u.password_hash == "!"
        assert u.email.endswith("@anonimizado.invalid") and u.name.startswith("Usuário removido")
        assert "technician" not in u.email
        evs = (await s.execute(select(AuditEvent.action).where(AuditEvent.user_id == uid))).scalars().all()
        assert "auth.login" in evs and "account.deleted" in evs          # history kept, now pseudonymous
        assert not (await s.execute(select(Notification).where(Notification.user_id == uid))).scalars().all()


async def test_last_admin_cannot_self_delete(client, st):
    t = (await login(client, st.email("admin"))).json()
    r = await client.request("DELETE", "/api/account", json={"password": STRONG, "confirm": "EXCLUIR"},
                             headers=bearer(t["access_token"]))
    assert r.status_code == 422


async def test_admin_anonymize_and_isolation(client, st):
    adm = bearer((await login(client, st.email("admin"))).json()["access_token"])
    other = await make_sec_tenant("LgpdB", roles=("admin",))
    ob = bearer((await login(client, other.email("admin"))).json()["access_token"])
    target = st.users["operator"]["id"]
    assert (await client.post(f"/api/users/{target}/anonymize", headers=ob)).status_code == 404
    mgr = bearer((await login(client, st.email("manager"))).json()["access_token"])
    assert (await client.post(f"/api/users/{target}/anonymize", headers=mgr)).status_code == 403
    assert (await client.post(f"/api/users/{st.users['admin']['id']}/anonymize", headers=adm)).status_code == 422
    assert (await client.post(f"/api/users/{target}/anonymize", headers=adm)).status_code == 204
    r = await client.get(f"/api/users/{target}", headers=adm)
    assert r.status_code == 200 and r.json()["email"].endswith("@anonimizado.invalid") and not r.json()["is_active"]
    assert "user.anonymized" in await audit_actions(st.users["admin"]["id"])


async def test_retention_purges_old_leads_and_expired_rows(db):
    from src.models import Lead
    from src.services.security.privacy import purge_expired
    now = datetime.now(timezone.utc)
    old = Lead(name="Velho", email="velho@example.com", consent=True, consent_at=now - timedelta(days=400),
               ip_hash="h" * 64, created_at=now - timedelta(days=400))
    new = Lead(name="Novo", email="novo@example.com", consent=True, consent_at=now, ip_hash="h" * 64)
    db.add_all([old, new])
    await db.commit()
    out = await purge_expired(db)
    await db.commit()
    assert out["leads"] >= 1
    assert await db.get(Lead, old.id) is None and await db.get(Lead, new.id) is not None


async def test_retention_respects_setting(db, monkeypatch):
    from src.config import settings
    from src.models import Lead
    from src.services.security.privacy import purge_expired
    monkeypatch.setattr(settings, "lead_retention_days", 0)            # 0 = keep forever
    now = datetime.now(timezone.utc)
    old = Lead(name="Velho2", email=f"v{time.time_ns()}@example.com", consent=True,
               consent_at=now - timedelta(days=4000), ip_hash="h" * 64, created_at=now - timedelta(days=4000))
    db.add(old)
    await db.commit()
    out = await purge_expired(db)
    await db.commit()
    assert "leads" not in out and await db.get(Lead, old.id) is not None
    await db.delete(old)
    await db.commit()
