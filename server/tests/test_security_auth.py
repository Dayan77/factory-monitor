"""Sprint 7 — sessions, refresh-token rotation with reuse detection, logout everywhere, JWT kid rotation and clock
skew, brute-force lockout with backoff, password policy (docs/security.md §1)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from sec_helpers import STRONG, STRONG2, STRONG3, audit_actions, bearer, login, make_sec_tenant, new_ip, pin_throttle_clock


@pytest.fixture
async def st(app):
    return await make_sec_tenant("Auth")


# ── sessions and refresh rotation ──────────────────────────────────

async def test_login_creates_session_and_tokens_carry_sid_kid(client, st):
    from src.config import ServerSettings, settings
    assert ServerSettings.model_fields["jwt_access_token_expire_minutes"].default == 15     # production default
    ttl = settings.jwt_access_token_expire_minutes * 60
    r = await login(client, st.email("manager"))
    body = r.json()
    assert body["session_id"] and body["expires_in"] == ttl
    hdr = jwt.get_unverified_header(body["access_token"])
    claims = jwt.get_unverified_claims(body["access_token"])
    assert hdr["kid"] == "k0" and claims["sid"] == body["session_id"] and claims["typ"] == "access"
    assert claims["exp"] - claims["iat"] == ttl
    me = await client.get("/api/auth/me", headers=bearer(body["access_token"]))
    assert me.status_code == 200
    assert "auth.login" in await audit_actions(st.users["manager"]["id"])


async def test_refresh_rotates_and_reuse_revokes_family(client, st, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "refresh_reuse_grace_s", 0.0)
    t1 = (await login(client, st.email("operator"))).json()
    r = await client.post("/api/auth/refresh", json={"refresh_token": t1["refresh_token"]})
    assert r.status_code == 200, r.text
    t2 = r.json()
    assert t2["refresh_token"] != t1["refresh_token"] and t2["session_id"] == t1["session_id"]
    # the rotated (old) refresh token presented again = theft: whole family revoked
    time.sleep(0.01)
    r = await client.post("/api/auth/refresh", json={"refresh_token": t1["refresh_token"]})
    assert r.status_code == 401
    assert (await client.post("/api/auth/refresh", json={"refresh_token": t2["refresh_token"]})).status_code == 401
    assert (await client.get("/api/auth/me", headers=bearer(t2["access_token"]))).status_code == 401
    assert "auth.refresh_reuse" in await audit_actions(st.users["operator"]["id"])


async def test_refresh_reuse_within_grace_is_not_theft(client, st, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "refresh_reuse_grace_s", 30.0)
    t1 = (await login(client, st.email("operator"))).json()
    a = await client.post("/api/auth/refresh", json={"refresh_token": t1["refresh_token"]})
    b = await client.post("/api/auth/refresh", json={"refresh_token": t1["refresh_token"]})   # 2nd tab
    assert a.status_code == 200 and b.status_code == 200
    assert (await client.get("/api/auth/me", headers=bearer(b.json()["access_token"]))).status_code == 200


async def test_access_token_is_not_a_refresh_token_and_legacy_refresh_refused(client, st):
    from src.auth.security import create_refresh_token
    t = (await login(client, st.email("operator"))).json()
    assert (await client.post("/api/auth/refresh", json={"refresh_token": t["access_token"]})).status_code == 401
    legacy = create_refresh_token({"sub": st.users["operator"]["id"], "tenant_id": st.id, "role": "operator"})
    assert (await client.post("/api/auth/refresh", json={"refresh_token": legacy})).status_code == 401
    # a refresh token is never accepted as access token
    assert (await client.get("/api/auth/me", headers=bearer(t["refresh_token"]))).status_code == 401


async def test_logout_revokes_current_session_only(client, st):
    a = (await login(client, st.email("manager"))).json()
    b = (await login(client, st.email("manager"))).json()
    assert (await client.post("/api/auth/logout", headers=bearer(a["access_token"]))).status_code == 204
    assert (await client.get("/api/auth/me", headers=bearer(a["access_token"]))).status_code == 401
    assert (await client.post("/api/auth/refresh", json={"refresh_token": a["refresh_token"]})).status_code == 401
    assert (await client.get("/api/auth/me", headers=bearer(b["access_token"]))).status_code == 200


async def test_sessions_list_and_revoke_and_logout_all(client, st):
    from src.auth.security import create_access_token
    a = (await login(client, st.email("admin"))).json()
    b = (await login(client, st.email("admin"))).json()
    r = await client.get("/api/account/sessions", headers=bearer(a["access_token"]))
    assert r.status_code == 200
    rows = r.json()
    ids = {s["id"] for s in rows}
    assert {a["session_id"], b["session_id"]} <= ids
    cur = [s for s in rows if s["current"]]
    assert len(cur) == 1 and cur[0]["id"] == a["session_id"]
    assert cur[0]["ip_prefix"].endswith(".x") and cur[0]["user_agent"] == "pytest-sec"
    # revoke b from a
    r = await client.delete(f"/api/account/sessions/{b['session_id']}", headers=bearer(a["access_token"]))
    assert r.status_code == 204
    assert (await client.get("/api/auth/me", headers=bearer(b["access_token"]))).status_code == 401
    # another user's session id → 404
    other = (await login(client, st.email("operator"))).json()
    r = await client.delete(f"/api/account/sessions/{other['session_id']}", headers=bearer(a["access_token"]))
    assert r.status_code == 404
    # logout everywhere also kills sid-less tokens issued before
    old_internal = create_access_token({"sub": st.users["admin"]["id"], "tenant_id": st.id, "role": "admin"})
    time.sleep(1.05)
    assert (await client.post("/api/auth/logout-all", headers=bearer(a["access_token"]))).status_code == 204
    assert (await client.get("/api/auth/me", headers=bearer(a["access_token"]))).status_code == 401
    assert (await client.get("/api/auth/me", headers=bearer(old_internal))).status_code == 401
    c = (await login(client, st.email("admin"))).json()
    assert (await client.get("/api/auth/me", headers=bearer(c["access_token"]))).status_code == 200
    assert {"auth.logout_all", "account.session_revoked"} <= set(await audit_actions(st.users["admin"]["id"]))


async def test_deactivated_user_sessions_die(client, st):
    adm = (await login(client, st.email("admin"))).json()
    op = (await login(client, st.email("operator"))).json()
    r = await client.patch(f"/api/users/{st.users['operator']['id']}", json={"is_active": False},
                           headers=bearer(adm["access_token"]))
    assert r.status_code == 200
    assert (await client.get("/api/auth/me", headers=bearer(op["access_token"]))).status_code == 401
    assert (await client.post("/api/auth/refresh", json={"refresh_token": op["refresh_token"]})).status_code == 401
    assert "user.activation_changed" in await audit_actions(st.users["admin"]["id"])


# ── JWT: kid rotation and clock skew ───────────────────────────────

async def test_kid_key_rotation(client, st, monkeypatch):
    from src.auth.security import create_access_token
    from src.config import settings
    sub = {"sub": st.users["operator"]["id"], "tenant_id": st.id, "role": "operator"}
    monkeypatch.setattr(settings, "jwt_keys", "old:old-secret-0123456789,new:new-secret-9876543210")
    monkeypatch.setattr(settings, "jwt_active_kid", "old")
    old_tok = create_access_token(sub)
    monkeypatch.setattr(settings, "jwt_active_kid", "new")
    new_tok = create_access_token(sub)
    assert jwt.get_unverified_header(new_tok)["kid"] == "new"
    for tok in (old_tok, new_tok):
        assert (await client.get("/api/auth/me", headers=bearer(tok))).status_code == 200
    # old key retired → its tokens die; unknown kid refused
    monkeypatch.setattr(settings, "jwt_keys", "new:new-secret-9876543210")
    assert (await client.get("/api/auth/me", headers=bearer(old_tok))).status_code == 401
    forged = jwt.encode({**sub, "exp": int(time.time()) + 60}, "whatever", algorithm="HS256",
                        headers={"kid": "nope"})
    assert (await client.get("/api/auth/me", headers=bearer(forged))).status_code == 401
    # tokens without kid (issued before rotation) still verify with APP_SECRET_KEY
    legacy = jwt.encode({**sub, "exp": int(time.time()) + 60}, settings.app_secret_key, algorithm="HS256")
    assert (await client.get("/api/auth/me", headers=bearer(legacy))).status_code == 200


async def test_clock_skew_leeway(client, st):
    from src.config import settings
    sub = {"sub": st.users["operator"]["id"], "tenant_id": st.id, "role": "operator"}
    now = int(time.time())
    slightly = jwt.encode({**sub, "exp": now - 10}, settings.app_secret_key, algorithm="HS256")
    clearly = jwt.encode({**sub, "exp": now - settings.jwt_leeway_s - 30}, settings.app_secret_key, algorithm="HS256")
    future_nbf = jwt.encode({**sub, "exp": now + 600, "nbf": now + 10}, settings.app_secret_key, algorithm="HS256")
    assert (await client.get("/api/auth/me", headers=bearer(slightly))).status_code == 200
    assert (await client.get("/api/auth/me", headers=bearer(clearly))).status_code == 401
    assert (await client.get("/api/auth/me", headers=bearer(future_nbf))).status_code == 200


async def test_other_token_kinds_are_not_access_tokens(client, st):
    from src.auth.security import TYP_MFA, TYP_RESET, create_token
    sub = {"sub": st.users["operator"]["id"], "tenant_id": st.id}
    for typ in (TYP_MFA, TYP_RESET):
        tok = create_token(sub, timedelta(minutes=5), typ)
        assert (await client.get("/api/auth/me", headers=bearer(tok))).status_code == 401


# ── brute force ────────────────────────────────────────────────────

async def test_account_lockout_with_backoff_and_audit(client, st):
    email = st.email("operator")
    for _ in range(5):
        r = await login(client, email, "Wrong-Password-123", expect=401)
        assert r.json()["detail"] == "Credenciais inválidas."
    r = await login(client, email, STRONG, expect=429)         # locked even with the right password
    assert int(r.headers["Retry-After"]) >= 1
    acts = await audit_actions(st.users["operator"]["id"])
    assert acts.count("auth.login_failed") == 5 and "auth.lockout" in acts and "auth.login_blocked" in acts


async def test_unknown_email_gets_the_same_answers(client):
    email = f"ghost.{time.time_ns()}@example.com"
    for _ in range(5):
        r = await login(client, email, "Wrong-Password-123", expect=401)
        assert r.json()["detail"] == "Credenciais inválidas."
    r = await login(client, email, "Wrong-Password-123", expect=429)
    assert "Retry-After" in r.headers


async def test_ip_lockout_across_accounts(client, st):
    from src.config import settings
    ip = new_ip()
    for i in range(settings.login_max_failures_ip):
        await login(client, f"spray{i}.{time.time_ns()}@example.com", "Wrong-Password-123", ip=ip, expect=401)
    await login(client, st.email("manager"), STRONG, ip=ip, expect=429)
    await login(client, st.email("manager"), STRONG, ip=new_ip(), expect=200)   # the account itself is fine


async def test_throttle_exponential_backoff_unit(db):
    from src.services.security import throttle
    key = throttle.hkey("unit", str(time.time_ns()))
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    locks = []
    t = t0
    for _round in range(4):
        for i in range(3):
            lock = await throttle.failure(db, key, 3, now=t)
        locks.append(lock)
        t = t + timedelta(seconds=lock + 1)
    await db.rollback()
    assert locks == [30.0, 60.0, 120.0, 240.0]


async def test_success_resets_account_counter(client, st):
    email = st.email("manager")
    for _ in range(4):
        await login(client, email, "Wrong-Password-123", expect=401)
    await login(client, email, STRONG, expect=200)
    for _ in range(4):
        await login(client, email, "Wrong-Password-123", expect=401)
    await login(client, email, STRONG, expect=200)


async def test_login_rate_limit_per_ip(client, st, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "login_rate_per_ip_per_min", 3)
    pin_throttle_clock(monkeypatch)
    ip = new_ip()
    for _ in range(3):
        await login(client, st.email("manager"), STRONG, ip=ip, expect=200)
    await login(client, st.email("manager"), STRONG, ip=ip, expect=429)


# ── password policy ────────────────────────────────────────────────

@pytest.mark.parametrize("pw,ok", [
    ("curta1!", False), ("Senha@2026!!", False), ("P4ssw0rd!2026", False), ("aaaaaaaaaaaaaa", False),
    ("abcdefghijklmno", False), ("Admin@12345678", False), ("123456789012", False), ("Qwerty123456!", False),
    (STRONG, True), (STRONG2, True), ("correct horse battery staple", True), ("Xy1!" * 20, False),
    ("Ç" * 40, False),
])
def test_password_policy(pw, ok):
    from src.services.security.passwords import PasswordPolicyError, check_policy
    if ok:
        check_policy(pw)
    else:
        with pytest.raises(PasswordPolicyError):
            check_policy(pw)


def test_password_policy_context():
    from src.services.security.passwords import PasswordPolicyError, check_policy
    with pytest.raises(PasswordPolicyError):
        check_policy("Ricardo-Fonseca-2026", ["ricardo.fonseca@acme.com", "Ricardo"])
    check_policy("Ponte-Vermelha#4821", ["ricardo.fonseca@acme.com", "Ricardo"])


def test_bcrypt_cost_and_rehash():
    from passlib.hash import bcrypt

    from src.auth.security import hash_password, needs_rehash
    h = hash_password(STRONG)
    assert h.startswith("$2b$12$")
    weak = bcrypt.using(rounds=4).hash(STRONG)
    assert needs_rehash(weak) and not needs_rehash(h)


async def test_login_upgrades_weak_hash(client, st):
    from passlib.hash import bcrypt
    from sqlalchemy import update

    from src.db import AsyncSessionLocal
    from src.models import User
    async with AsyncSessionLocal() as s:
        await s.execute(update(User).where(User.id == st.users["operator"]["id"])
                        .values(password_hash=bcrypt.using(rounds=4).hash(STRONG)))
        await s.commit()
    await login(client, st.email("operator"))
    async with AsyncSessionLocal() as s:
        h = (await s.get(User, st.users["operator"]["id"])).password_hash
    assert h.startswith("$2b$12$")


async def test_change_password_history_and_session_revocation(client, st):
    a = (await login(client, st.email("manager"))).json()
    b = (await login(client, st.email("manager"))).json()
    h = bearer(a["access_token"])
    r = await client.post("/api/account/password", json={"current_password": "nope-nope-nope", "new_password": STRONG2},
                          headers=h)
    assert r.status_code == 403
    r = await client.post("/api/account/password", json={"current_password": STRONG, "new_password": "curta"},
                          headers=h)
    assert r.status_code == 422
    r = await client.post("/api/account/password", json={"current_password": STRONG, "new_password": STRONG},
                          headers=h)
    assert r.status_code == 422 and "últimas" in r.json()["detail"]
    r = await client.post("/api/account/password", json={"current_password": STRONG, "new_password": STRONG2},
                          headers=h)
    assert r.status_code == 204
    assert (await client.get("/api/auth/me", headers=h)).status_code == 200            # current session kept
    assert (await client.get("/api/auth/me", headers=bearer(b["access_token"]))).status_code == 401
    # back to the first one: still in the history (last 5)
    r = await client.post("/api/account/password", json={"current_password": STRONG2, "new_password": STRONG},
                          headers=h)
    assert r.status_code == 422
    r = await client.post("/api/account/password", json={"current_password": STRONG2, "new_password": STRONG3},
                          headers=h)
    assert r.status_code == 204
    await login(client, st.email("manager"), STRONG3)
    assert "account.password_changed" in await audit_actions(st.users["manager"]["id"])


async def test_password_history_keeps_last_five(db, st):
    from sqlalchemy import func, select

    from src.models import PasswordHistory, User
    from src.services.security.passwords import PasswordPolicyError, set_password
    u = await db.get(User, st.users["operator"]["id"])
    pws = [f"Historico-Senha-{i}-Xq" for i in range(7)]
    for pw in pws:
        await set_password(db, u, pw)
    n = (await db.execute(select(func.count()).select_from(PasswordHistory).where(
        PasswordHistory.user_id == u.id))).scalar_one()
    assert n == 4                       # + the current one = last 5
    for pw in pws[-5:]:
        with pytest.raises(PasswordPolicyError):
            await set_password(db, u, pw)
    await set_password(db, u, pws[0])   # 7 changes ago: allowed again
    await db.rollback()


async def test_register_enforces_policy_and_forbids_extra_fields(client):
    ip = new_ip()
    body = {"company_name": "Metalúrgica Teste", "admin_name": "Ana", "admin_email": f"ana{time.time_ns()}@ex.com",
            "admin_password": "Senha@2026!!"}
    r = await client.post("/api/auth/register", json=body, headers={"X-Forwarded-For": ip})
    assert r.status_code == 422
    r = await client.post("/api/auth/register", json={**body, "admin_password": STRONG, "role": "superadmin"},
                          headers={"X-Forwarded-For": ip})
    assert r.status_code == 422
    r = await client.post("/api/auth/register", json={**body, "admin_password": STRONG}, headers={"X-Forwarded-For": ip})
    assert r.status_code == 201, r.text


async def test_admin_create_user_enforces_policy_and_audits(client, st):
    adm = (await login(client, st.email("admin"))).json()
    h = bearer(adm["access_token"])
    base = {"email": f"novo{time.time_ns()}@example.com", "name": "Novo", "role": "operator"}
    r = await client.post("/api/users/", json={**base, "password": "Test@12345"}, headers=h)
    assert r.status_code == 422
    r = await client.post("/api/users/", json={**base, "password": STRONG2}, headers=h)
    assert r.status_code == 201
    r = await client.patch(f"/api/users/{r.json()['id']}", json={"roles": ["operator", "technician"]}, headers=h)
    assert r.status_code == 200
    acts = await audit_actions(st.users["admin"]["id"])
    assert "user.created" in acts and "user.roles_changed" in acts
