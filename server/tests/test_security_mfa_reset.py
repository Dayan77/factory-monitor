"""Sprint 7 — TOTP MFA (enrol, login challenge, replay, recovery codes, enforcement for admins), password reset by
e-mail without user enumeration, invitation-only signup, WebSocket session re-validation (docs/security.md §1)."""
from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import update

from sec_helpers import STRONG, STRONG2, audit_actions, bearer, login, make_sec_tenant, new_ip


@pytest.fixture
async def st(app):
    return await make_sec_tenant("Mfa", roles=("admin", "manager", "operator"))


async def _enable_mfa(client, h) -> tuple[str, list[str]]:
    from src.services.security import totp
    r = await client.post("/api/account/mfa/enroll", json={"password": STRONG}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    uri = urlparse(body["otpauth_uri"])
    assert uri.scheme == "otpauth" and uri.netloc == "totp"
    assert parse_qs(uri.query)["secret"] == [body["secret"]] and parse_qs(uri.query)["issuer"] == ["FactoryConnect"]
    r = await client.post("/api/account/mfa/confirm", json={"code": "000000"}, headers=h)
    assert r.status_code == 422
    r = await client.post("/api/account/mfa/confirm", json={"code": totp.code_at(body["secret"])}, headers=h)
    assert r.status_code == 200, r.text
    codes = r.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    return body["secret"], codes


def _next_step(monkeypatch):
    """Moves the TOTP clock one step ahead (the enrolment code's step is already used)."""
    import types

    from src.services.security import totp
    real = time.time
    monkeypatch.setattr(totp, "time", types.SimpleNamespace(time=lambda: real() + totp.STEP_S))


async def test_totp_rfc6238_vectors():
    import base64

    from src.services.security import totp
    secret = base64.b32encode(b"12345678901234567890").decode()
    # RFC 6238 appendix B (SHA-1), last 6 digits
    for t, code in ((59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037")):
        assert totp.code_at(secret, t) == code
        assert totp.verify(secret, code, t=t) is not None
    assert totp.verify(secret, "287082", last_step=int(59 // 30), t=59) is None     # replay refused
    enc = totp.encrypt(secret)
    assert secret not in enc and totp.decrypt(enc) == secret


async def test_mfa_enrol_login_replay_recovery_and_disable(client, st, monkeypatch):
    from sqlalchemy import select

    from src.db import AsyncSessionLocal
    from src.models import MfaRecoveryCode, User
    from src.services.security import totp
    t = (await login(client, st.email("manager"))).json()
    h = bearer(t["access_token"])
    secret, codes = await _enable_mfa(client, h)
    async with AsyncSessionLocal() as s:
        u = await s.get(User, st.users["manager"]["id"])
        assert secret not in (u.mfa_secret_enc or "")
        hashes = (await s.execute(select(MfaRecoveryCode.code_hash).where(MfaRecoveryCode.user_id == u.id))).scalars().all()
        assert len(hashes) == 10 and not set(codes) & set(hashes)
    st_ = (await client.get("/api/account/mfa", headers=h)).json()
    assert st_["enabled"] and st_["recovery_codes_left"] == 10

    # login now asks for the second factor
    r = await login(client, st.email("manager"))
    ch = r.json()
    assert ch["mfa_required"] is True and "access_token" not in ch
    # the challenge token is not an access token
    assert (await client.get("/api/auth/me", headers=bearer(ch["mfa_token"]))).status_code == 401
    # the code used at enrolment cannot be replayed: wait for the next step
    _next_step(monkeypatch)
    code = totp.code_at(secret, time.time() + totp.STEP_S)
    r = await client.post("/api/auth/mfa/verify", json={"mfa_token": ch["mfa_token"], "code": code})
    assert r.status_code == 200, r.text
    tok = r.json()
    assert (await client.get("/api/auth/me", headers=bearer(tok["access_token"]))).status_code == 200
    # same challenge twice → refused; same code in another challenge → replay refused
    r = await client.post("/api/auth/mfa/verify", json={"mfa_token": ch["mfa_token"], "code": code})
    assert r.status_code == 401
    ch2 = (await login(client, st.email("manager"))).json()
    r = await client.post("/api/auth/mfa/verify", json={"mfa_token": ch2["mfa_token"], "code": code})
    assert r.status_code == 401
    # recovery code: works once
    r = await client.post("/api/auth/mfa/verify", json={"mfa_token": ch2["mfa_token"], "recovery_code": codes[0]})
    assert r.status_code == 200
    ch3 = (await login(client, st.email("manager"))).json()
    r = await client.post("/api/auth/mfa/verify", json={"mfa_token": ch3["mfa_token"], "recovery_code": codes[0]})
    assert r.status_code == 401
    h2 = bearer(tok["access_token"])
    assert (await client.get("/api/account/mfa", headers=h2)).json()["recovery_codes_left"] == 9
    # disable needs password + code
    r = await client.post("/api/account/mfa/disable", json={"password": "wrong-wrong-wrong", "code": codes[1]},
                          headers=h2)
    assert r.status_code == 403
    r = await client.post("/api/account/mfa/disable", json={"password": STRONG, "code": codes[1]}, headers=h2)
    assert r.status_code == 204
    r = await login(client, st.email("manager"))
    assert "access_token" in r.json()
    acts = await audit_actions(st.users["manager"]["id"])
    for a in ("account.mfa_enroll_started", "account.mfa_enabled", "auth.mfa_challenge", "auth.mfa_failed",
              "auth.mfa_recovery_used", "account.mfa_disabled"):
        assert a in acts, a


async def test_mfa_code_brute_force_locks(client, st):
    t = (await login(client, st.email("operator"))).json()
    await _enable_mfa(client, bearer(t["access_token"]))
    ch = (await login(client, st.email("operator"))).json()
    for _ in range(5):
        r = await client.post("/api/auth/mfa/verify", json={"mfa_token": ch["mfa_token"], "code": "123456"})
        assert r.status_code == 401
    r = await client.post("/api/auth/mfa/verify", json={"mfa_token": ch["mfa_token"], "code": "654321"})
    assert r.status_code == 429


async def test_require_mfa_for_admins(client, st):
    adm = (await login(client, st.email("admin"))).json()
    h = bearer(adm["access_token"])
    # cannot require it before having it
    r = await client.put("/api/account/tenant-security", json={"require_mfa_admins": True}, headers=h)
    assert r.status_code == 422
    # managers cannot change the policy
    mgr = (await login(client, st.email("manager"))).json()
    r = await client.put("/api/account/tenant-security", json={"require_mfa_admins": True},
                         headers=bearer(mgr["access_token"]))
    assert r.status_code == 403
    await _enable_mfa(client, h)
    r = await client.put("/api/account/tenant-security", json={"require_mfa_admins": True}, headers=h)
    assert r.status_code == 200 and r.json()["require_mfa_admins"] is True
    # a second admin without MFA: only enrolment / sign-out allowed
    from src.auth.security import hash_password
    from src.db import AsyncSessionLocal
    from src.models import User, UserRole
    async with AsyncSessionLocal() as s:
        u = User(tenant_id=st.id, email=f"adm2.{time.time_ns()}@example.com", password_hash=hash_password(STRONG),
                 name="Adm2", role=UserRole.admin, roles=["admin"])
        s.add(u)
        await s.commit()
        email2 = u.email
    t2 = (await login(client, email2)).json()
    h2 = bearer(t2["access_token"])
    r = await client.get("/api/lines/", headers=h2)
    assert r.status_code == 403 and r.headers.get("X-Error-Code") == "mfa_enrollment_required"
    assert (await client.get("/api/auth/me", headers=h2)).status_code == 200
    assert (await client.get("/api/account/mfa", headers=h2)).json()["required"] is True
    _, codes2 = await _enable_mfa(client, h2)
    assert (await client.get("/api/lines/", headers=h2)).status_code == 200
    # non-admins are not affected
    assert (await client.get("/api/lines/", headers=bearer(mgr["access_token"]))).status_code == 200
    # disabling MFA is refused while required
    r = await client.post("/api/account/mfa/disable", json={"password": STRONG, "code": codes2[0]}, headers=h2)
    assert r.status_code == 422 and "exige MFA" in r.json()["detail"]
    assert "tenant.security_settings_changed" in await audit_actions(st.users["admin"]["id"])


async def test_admin_resets_mfa_of_user(client, st):
    op = (await login(client, st.email("operator"))).json()
    await _enable_mfa(client, bearer(op["access_token"]))
    adm = (await login(client, st.email("admin"))).json()
    r = await client.post(f"/api/users/{st.users['operator']['id']}/mfa/reset", headers=bearer(adm["access_token"]))
    assert r.status_code == 204
    assert (await client.get("/api/auth/me", headers=bearer(op["access_token"]))).status_code == 401
    assert "access_token" in (await login(client, st.email("operator"))).json()
    other = await make_sec_tenant("MfaB", roles=("admin",))
    otok = (await login(client, other.email("admin"))).json()
    r = await client.post(f"/api/users/{st.users['operator']['id']}/mfa/reset", headers=bearer(otok["access_token"]))
    assert r.status_code == 404


# ── password reset ─────────────────────────────────────────────────

@pytest.fixture
def mails(monkeypatch):
    sent = []

    async def fake_reset(to, name, token):
        sent.append(("reset", to, token))

    async def fake_invite(to, tenant_name, inviter, token):
        sent.append(("invite", to, token))
    from src.services.security import notify
    monkeypatch.setattr(notify, "send_reset_email", fake_reset)
    monkeypatch.setattr(notify, "send_invite_email", fake_invite)
    return sent


async def test_password_reset_no_enumeration_single_use(client, st, mails):
    ip = new_ip()
    known = await client.post("/api/auth/password-reset/request", json={"email": st.email("operator")},
                              headers={"X-Forwarded-For": ip})
    unknown = await client.post("/api/auth/password-reset/request", json={"email": f"x{time.time_ns()}@example.com"},
                                headers={"X-Forwarded-For": ip})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert len(mails) == 1 and mails[0][1] == st.email("operator")
    token = mails[0][2]
    old = (await login(client, st.email("operator"))).json()
    r = await client.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": "curta"})
    assert r.status_code == 422
    r = await client.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": STRONG2})
    assert r.status_code == 204
    r = await client.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": STRONG2 + "x"})
    assert r.status_code == 400                                         # single use
    assert (await client.get("/api/auth/me", headers=bearer(old["access_token"]))).status_code == 401
    await login(client, st.email("operator"), STRONG, expect=401)
    await login(client, st.email("operator"), STRONG2)
    assert {"auth.password_reset_requested", "auth.password_reset"} <= set(await audit_actions(st.users["operator"]["id"]))


async def test_password_reset_token_expiry_and_invalidated_by_password_change(client, st, mails, monkeypatch):
    from datetime import timedelta

    from src.auth.security import TYP_RESET, create_token
    from src.db import AsyncSessionLocal
    from src.models import PasswordResetToken
    await client.post("/api/auth/password-reset/request", json={"email": st.email("manager")},
                      headers={"X-Forwarded-For": new_ip()})
    token = mails[-1][2]
    # expired row
    async with AsyncSessionLocal() as s:
        from datetime import datetime, timezone
        await s.execute(update(PasswordResetToken).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
                        .where(PasswordResetToken.user_id == st.users["manager"]["id"]))
        await s.commit()
    r = await client.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": STRONG2})
    assert r.status_code == 400
    # a fresh one dies when the password changes by another way
    await client.post("/api/auth/password-reset/request", json={"email": st.email("manager")},
                      headers={"X-Forwarded-For": new_ip()})
    token2 = mails[-1][2]
    t = (await login(client, st.email("manager"))).json()
    r = await client.post("/api/account/password", json={"current_password": STRONG, "new_password": STRONG2},
                          headers=bearer(t["access_token"]))
    assert r.status_code == 204
    r = await client.post("/api/auth/password-reset/confirm", json={"token": token2, "new_password": STRONG})
    assert r.status_code == 400
    # forged / wrong kind
    forged = create_token({"sub": st.users["manager"]["id"], "rid": "x"}, timedelta(minutes=5), TYP_RESET)
    assert (await client.post("/api/auth/password-reset/confirm",
                              json={"token": forged, "new_password": STRONG})).status_code == 400
    assert (await client.post("/api/auth/password-reset/confirm",
                              json={"token": t["access_token"], "new_password": STRONG})).status_code == 400


async def test_password_reset_rate_limits(client, st, mails, monkeypatch):
    from src.config import settings
    ip = new_ip()
    for _ in range(settings.password_reset_per_email_per_hour + 2):
        r = await client.post("/api/auth/password-reset/request", json={"email": st.email("admin")},
                              headers={"X-Forwarded-For": ip})
        assert r.status_code == 202
    assert len([m for m in mails if m[1] == st.email("admin")]) == settings.password_reset_per_email_per_hour
    monkeypatch.setattr(settings, "password_reset_per_ip_per_hour", 2)
    ip2 = new_ip()
    codes = [(await client.post("/api/auth/password-reset/request", json={"email": f"n{i}@example.com"},
                                headers={"X-Forwarded-For": ip2})).status_code for i in range(3)]
    assert codes == [202, 202, 429]


# ── invitations ────────────────────────────────────────────────────

async def test_invitation_flow(client, st, mails):
    adm = (await login(client, st.email("admin"))).json()
    h = bearer(adm["access_token"])
    email = f"convidado.{time.time_ns()}@example.com"
    r = await client.post("/api/users/invitations", json={"email": email, "name": "Técnico Novo", "role": "technician"},
                          headers=h)
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["state"] == "pending" and "token" not in inv
    token = parse_qs(urlparse(inv["invite_url"]).query)["token"][0]
    assert mails[-1] == ("invite", email, token)
    # operator / manager cannot invite; superadmin role cannot be invited
    mgr = (await login(client, st.email("manager"))).json()
    assert (await client.post("/api/users/invitations", json={"email": "z@example.com"},
                              headers=bearer(mgr["access_token"]))).status_code == 403
    assert (await client.post("/api/users/invitations", json={"email": "z@example.com", "role": "superadmin"},
                              headers=h)).status_code == 403
    # preview + accept
    r = await client.get(f"/api/auth/invitations/{token}")
    assert r.status_code == 200 and r.json()["email"] == email and r.json()["tenant_name"] == st.name
    r = await client.post("/api/auth/invitations/accept", json={"token": token, "password": "Senha@2026!!"})
    assert r.status_code == 422
    r = await client.post("/api/auth/invitations/accept", json={"token": token, "password": STRONG2})
    assert r.status_code == 201, r.text
    me = (await client.get("/api/auth/me", headers=bearer(r.json()["access_token"]))).json()
    assert me["email"] == email and me["tenant_id"] == st.id and me["role"] == "technician"
    # single use
    assert (await client.post("/api/auth/invitations/accept", json={"token": token, "password": STRONG2})
            ).status_code == 404
    assert (await client.get(f"/api/auth/invitations/{token}")).status_code == 404
    rows = (await client.get("/api/users/invitations", headers=h)).json()
    assert any(i["id"] == inv["id"] and i["state"] == "accepted" for i in rows)
    assert "user.invitation_created" in await audit_actions(st.users["admin"]["id"])


async def test_invitation_revoke_and_isolation(client, st, mails):
    adm = (await login(client, st.email("admin"))).json()
    h = bearer(adm["access_token"])
    r = await client.post("/api/users/invitations", json={"email": f"rev.{time.time_ns()}@example.com"}, headers=h)
    inv = r.json()
    token = parse_qs(urlparse(inv["invite_url"]).query)["token"][0]
    other = await make_sec_tenant("InvB", roles=("admin",))
    ob = bearer((await login(client, other.email("admin"))).json()["access_token"])
    assert (await client.delete(f"/api/users/invitations/{inv['id']}", headers=ob)).status_code == 404
    assert all(i["id"] != inv["id"] for i in (await client.get("/api/users/invitations", headers=ob)).json())
    assert (await client.delete(f"/api/users/invitations/{inv['id']}", headers=h)).status_code == 204
    assert (await client.post("/api/auth/invitations/accept", json={"token": token, "password": STRONG2})
            ).status_code == 404
    # line ids of another tenant are refused
    from src.db import AsyncSessionLocal
    from src.models import Line, Plant
    async with AsyncSessionLocal() as s:
        p = Plant(tenant_id=other.id, name="P")
        s.add(p)
        await s.flush()
        ln = Line(tenant_id=other.id, plant_id=p.id, name="L")
        s.add(ln)
        await s.commit()
        lid = ln.id
    r = await client.post("/api/users/invitations", json={"email": "x@example.com", "line_ids": [lid]}, headers=h)
    assert r.status_code == 404


# ── WebSocket re-validation ────────────────────────────────────────

def test_ws_closed_when_session_revoked_and_reauth(app, monkeypatch):
    import asyncio

    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from conftest import run_sync
    from src.config import settings
    monkeypatch.setattr(settings, "ws_revalidate_s", 0.3)
    st = run_sync(make_sec_tenant("Ws", roles=("manager",)))
    wsc = TestClient(app)

    def do_login():
        r = wsc.post("/api/auth/login", json={"email": st.email("manager"), "password": STRONG},
                     headers={"X-Forwarded-For": new_ip()})
        assert r.status_code == 200, r.text
        return r.json()

    t1, t2 = do_login(), do_login()
    with wsc.websocket_connect(f"/ws/sensors/{st.id}", subprotocols=["bearer", t1["access_token"]]) as ws:
        # renew with another valid token of the same user → reauth_ok
        ws.send_json({"type": "reauth", "token": t2["access_token"]})
        assert ws.receive_json()["type"] == "reauth_ok"
        # revoke the session of the token in use (t2) → closed 4401 at the next re-validation
        r = wsc.post("/api/auth/logout", headers=bearer(t2["access_token"]))
        assert r.status_code == 204
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
        assert exc.value.code == 4401
    # reauth with garbage closes 4401
    t3 = do_login()
    with wsc.websocket_connect(f"/ws/sensors/{st.id}", subprotocols=["bearer", t3["access_token"]]) as ws:
        ws.send_json({"type": "reauth", "token": "garbage"})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
        assert exc.value.code == 4401
    _ = asyncio
