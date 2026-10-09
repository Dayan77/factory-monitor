"""Sprint 7 gate (e) — security flows through the REAL HTTP API of a running server (no ASGI shortcut, no mocks).

On throwaway users invited into the admin's tenant, so the run is repeatable and never touches the seed users:
  1. invitation: admin invites → preview → accept (policy applies) → signed in; the link cannot be reused
  2. refresh rotation: R1 → R2; replaying R1 after the grace window revokes the whole family (R2 and the access
     token of that session stop working)
  3. password reset without enumeration: same status and body for an existing and an unknown e-mail
  4. MFA: enroll → confirm (TOTP) → recovery codes; login asks for MFA; TOTP and a recovery code both sign in;
     a used recovery code is refused; a wrong code is refused
  5. brute force: 5 wrong passwords lock the account (429, Retry-After) — even the right password is refused
  6. LGPD: export (JSON attachment, no secrets) and self-service delete → login refused afterwards

    API=http://localhost:8000 .venv/bin/python e2e/sprint7b/security_api_real.py
Exit code 0 = every check passed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import sys
import time

import httpx

API = os.getenv("API", "http://localhost:8000").rstrip("/")
ADMIN = (os.getenv("E2E_EMAIL", "admin@factory-monitor.com"), os.getenv("E2E_PASSWORD", "Admin@1234"))
failures = 0


def check(cond: bool, msg: str, got=None) -> None:
    global failures
    print(f"  {'✓' if cond else '✗'} {msg}" + ("" if cond or got is None else f"   got={str(got)[:300]}"))
    if not cond:
        failures += 1


def totp(secret: str, at: float | None = None) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    step = int((at or time.time()) // 30)
    h = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def wait_next_step() -> None:
    """TOTP codes are single-use (replay protection): wait for a fresh 30-s step."""
    time.sleep(30 - time.time() % 30 + 1)


def login(c: httpx.Client, email: str, password: str) -> httpx.Response:
    return c.post(f"{API}/api/auth/login", json={"email": email, "password": password})


def bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def invite_user(c: httpx.Client, admin_tok: str, tag: str) -> tuple[str, str, dict]:
    email = f"qa7b-{tag}-{secrets.token_hex(4)}@example.com"
    r = c.post(f"{API}/api/users/invitations", headers=bearer(admin_tok),
               json={"email": email, "name": f"QA 7b {tag}", "role": "operator"})
    assert r.status_code == 201, r.text
    token = r.json()["invite_url"].split("token=", 1)[1]
    pw = "Qg7b-" + secrets.token_urlsafe(12) + "!9"
    r = c.post(f"{API}/api/auth/invitations/accept", json={"token": token, "password": pw})
    assert r.status_code == 201, r.text
    return email, pw, r.json()


def main() -> int:
    c = httpx.Client(timeout=30)
    r = login(c, *ADMIN)
    assert r.status_code == 200, r.text
    admin_tok = r.json()["access_token"]

    print("1. invitation")
    email = f"qa7b-inv-{secrets.token_hex(4)}@example.com"
    r = c.post(f"{API}/api/users/invitations", headers=bearer(admin_tok),
               json={"email": email, "name": "QA 7b convite", "role": "operator"})
    check(r.status_code == 201 and "invite_url" in r.json(), "admin creates an invitation with a one-time link",
          r.text)
    token = r.json()["invite_url"].split("token=", 1)[1]
    r = c.get(f"{API}/api/auth/invitations/{token}")
    check(r.status_code == 200 and r.json().get("email") == email and r.json().get("role") == "operator",
          "preview shows e-mail and role", r.text)
    r = c.post(f"{API}/api/auth/invitations/accept", json={"token": token, "password": "123456"})
    check(r.status_code == 422, "weak password refused by the policy", r.status_code)
    pw = "Qg7b-" + secrets.token_urlsafe(12) + "!9"
    r = c.post(f"{API}/api/auth/invitations/accept", json={"token": token, "password": pw})
    check(r.status_code == 201 and r.json().get("access_token"), "accept → signed in", r.text)
    me = c.get(f"{API}/api/auth/me", headers=bearer(r.json()["access_token"]))
    check(me.status_code == 200 and me.json().get("email") == email, "the new session is the invitee", me.text)
    r = c.post(f"{API}/api/auth/invitations/accept", json={"token": token, "password": pw + "x"})
    check(r.status_code == 404, "the invitation link cannot be reused", r.status_code)
    r = c.get(f"{API}/api/auth/invitations/{token}")
    check(r.status_code == 404, "preview of a used link → 404", r.status_code)

    print("2. refresh-token rotation and reuse")
    r = login(c, email, pw)
    t1 = r.json()
    r = c.post(f"{API}/api/auth/refresh", json={"refresh_token": t1["refresh_token"]})
    check(r.status_code == 200 and r.json()["refresh_token"] != t1["refresh_token"], "R1 → R2 (rotated)", r.text)
    t2 = r.json()
    check(c.get(f"{API}/api/auth/me", headers=bearer(t2["access_token"])).status_code == 200, "A2 works")
    print("    (waiting past the 10 s reuse grace)")
    time.sleep(11.5)
    r = c.post(f"{API}/api/auth/refresh", json={"refresh_token": t1["refresh_token"]})
    check(r.status_code == 401, "replaying R1 → 401", r.status_code)
    r = c.post(f"{API}/api/auth/refresh", json={"refresh_token": t2["refresh_token"]})
    check(r.status_code == 401, "after the reuse R2 is dead too (family revoked)", r.status_code)
    r = c.get(f"{API}/api/auth/me", headers=bearer(t2["access_token"]))
    check(r.status_code == 401, "and the access token of that session is refused", r.status_code)
    r = c.get(f"{API}/api/auth/me", headers=bearer(admin_tok))
    check(r.status_code == 200, "other users' sessions are untouched", r.status_code)

    print("3. password reset without enumeration")
    a = c.post(f"{API}/api/auth/password-reset/request", json={"email": email})
    b = c.post(f"{API}/api/auth/password-reset/request",
               json={"email": f"nobody-{secrets.token_hex(4)}@example.com"})
    check(a.status_code == b.status_code == 202 and a.json() == b.json(),
          "same 202 + body for existing and unknown e-mail", (a.status_code, a.text, b.status_code, b.text))
    r = c.post(f"{API}/api/auth/password-reset/confirm", json={"token": "garbage", "new_password": "Xx-1234567890!"})
    check(r.status_code == 400, "a forged reset token is refused", r.status_code)

    print("4. MFA enroll / login / recovery")
    email_m, pw_m, tok = invite_user(c, admin_tok, "mfa")
    h = bearer(tok["access_token"])
    r = c.post(f"{API}/api/account/mfa/enroll", headers=h, json={"password": "wrong-password"})
    check(r.status_code in (401, 403, 422), "enroll needs the password", r.status_code)
    r = c.post(f"{API}/api/account/mfa/enroll", headers=h, json={"password": pw_m})
    check(r.status_code == 200 and r.json().get("otpauth_uri", "").startswith("otpauth://totp/"),
          "enroll returns secret + otpauth URI", r.text)
    secret = r.json()["secret"]
    r = c.post(f"{API}/api/account/mfa/confirm", headers=h, json={"code": "000000" if totp(secret) != "000000"
                                                                   else "111111"})
    check(r.status_code == 422, "wrong confirmation code refused", r.status_code)
    r = c.post(f"{API}/api/account/mfa/confirm", headers=h, json={"code": totp(secret)})
    codes = r.json().get("recovery_codes", []) if r.status_code == 200 else []
    check(r.status_code == 200 and len(codes) >= 8, "confirm → MFA on + recovery codes", r.text)
    r = login(c, email_m, pw_m)
    check(r.status_code == 200 and r.json().get("mfa_required") and "access_token" not in r.json(),
          "login now asks for the second factor (no tokens yet)", r.text)
    mt = r.json().get("mfa_token")
    r = c.post(f"{API}/api/auth/mfa/verify", json={"mfa_token": mt, "code": "000000" if totp(secret) != "000000"
                                                    else "111111"})
    check(r.status_code == 401, "wrong TOTP refused", r.status_code)
    wait_next_step()
    r = c.post(f"{API}/api/auth/mfa/verify", json={"mfa_token": mt, "code": totp(secret)})
    check(r.status_code == 200 and r.json().get("access_token"), "TOTP → signed in", r.text)
    r = c.post(f"{API}/api/auth/mfa/verify", json={"mfa_token": mt, "code": totp(secret)})
    check(r.status_code == 401, "the MFA challenge cannot be reused", r.status_code)
    mt2 = login(c, email_m, pw_m).json().get("mfa_token")
    r = c.post(f"{API}/api/auth/mfa/verify", json={"mfa_token": mt2, "recovery_code": codes[0]})
    check(r.status_code == 200 and r.json().get("access_token"), "recovery code → signed in", r.text)
    mt3 = login(c, email_m, pw_m).json().get("mfa_token")
    r = c.post(f"{API}/api/auth/mfa/verify", json={"mfa_token": mt3, "recovery_code": codes[0]})
    check(r.status_code == 401, "a used recovery code is refused", r.status_code)
    st = c.get(f"{API}/api/account/mfa", headers=h)
    check(st.status_code == 200 and st.json().get("recovery_codes_left") == len(codes) - 1,
          "recovery_codes_left decremented", st.text)

    print("5. brute-force lockout")
    email_l, pw_l, _ = invite_user(c, admin_tok, "lock")
    codes_seen = [login(c, email_l, "wrong-" + str(i)).status_code for i in range(5)]
    check(codes_seen[:4] == [401] * 4, "first wrong passwords → 401", codes_seen)
    r = login(c, email_l, pw_l)
    check(r.status_code == 429 and r.headers.get("retry-after"), "locked: even the right password → 429 + Retry-After",
          (r.status_code, dict(r.headers)))
    r = login(c, f"ghost-{secrets.token_hex(3)}@example.com", "whatever")
    check(r.status_code == 401, "unknown e-mail → same 401 (no enumeration)", r.status_code)

    print("6. LGPD export / delete")
    email_d, pw_d, tok = invite_user(c, admin_tok, "lgpd")
    h = bearer(tok["access_token"])
    r = c.get(f"{API}/api/account/export", headers=h)
    body = r.text
    check(r.status_code == 200 and "attachment" in r.headers.get("content-disposition", "")
          and email_d in body and r.headers.get("cache-control") == "no-store",
          "export: JSON attachment with the user's data, no-store", (r.status_code, dict(r.headers)))
    check("password_hash" not in body and "$argon2" not in body and "$2b$" not in body and "mfa_secret" not in body,
          "export carries no secret (hash, MFA secret)")
    r = c.request("DELETE", f"{API}/api/account", headers=h, json={"password": pw_d, "confirm": "nao"})
    check(r.status_code == 422, "delete requires the literal EXCLUIR", r.status_code)
    r = c.request("DELETE", f"{API}/api/account", headers=h, json={"password": "wrong", "confirm": "EXCLUIR"})
    check(r.status_code in (401, 403, 422), "delete requires the password", r.status_code)
    r = c.request("DELETE", f"{API}/api/account", headers=h, json={"password": pw_d, "confirm": "EXCLUIR"})
    check(r.status_code == 204, "self-service delete → 204", (r.status_code, r.text))
    r = login(c, email_d, pw_d)
    check(r.status_code == 401, "login refused after deletion", r.status_code)
    check(c.get(f"{API}/api/auth/me", headers=h).status_code == 401, "the old access token is refused")

    print(f"\n{'all checks passed' if failures == 0 else f'{failures} check(s) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
