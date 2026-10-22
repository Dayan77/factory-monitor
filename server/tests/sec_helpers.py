"""Helpers for the Sprint 7 security tests: a fresh tenant whose users have a policy-compliant password, logins
from distinct client IPs (X-Forwarded-For through the trusted loopback proxy, so throttling of one test never
leaks into another), captured security e-mails."""
from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field

STRONG = "Ponte-Vermelha#4821"
STRONG2 = "Girassol!Turbina-9034"
STRONG3 = "Cascata_Bussola*5172"

_ip_counter = itertools.count(1)


def pin_throttle_clock(monkeypatch) -> None:
    """Rate limits count in fixed windows (a minute, an hour). A test that sends N requests and expects the N+1-th
    to be refused fails if a window boundary falls between them (slow CI: every login hashes a password). Pin the
    throttle clock to the middle of the current hour, so every request of the test lands in the same window."""
    from datetime import datetime, timedelta, timezone

    from src.services.security import throttle
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(minutes=30)
    monkeypatch.setattr(throttle, "_now", lambda: now)


def new_ip() -> str:
    n = next(_ip_counter) + uuid.uuid4().int % 50_000
    return f"198.18.{(n // 250) % 250}.{n % 250 + 1}"


@dataclass
class SecTenant:
    id: str
    name: str
    users: dict = field(default_factory=dict)       # role -> {"id", "email"}

    def email(self, role: str) -> str:
        return self.users[role]["email"]


_HASH: dict = {}


async def make_sec_tenant(label: str = "S", roles=("admin", "manager", "operator"), password: str = STRONG,
                          max_users: int = 50) -> SecTenant:
    from src.auth.security import hash_password
    from src.db import AsyncSessionLocal
    from src.models import Tenant, User, UserRole
    if password not in _HASH:
        _HASH[password] = hash_password(password)
    async with AsyncSessionLocal() as s:
        sfx = uuid.uuid4().hex[:8]
        t = Tenant(name=f"Empresa {label} {sfx}", slug=f"sec-{label.lower()}-{sfx}", max_users=max_users)
        s.add(t)
        await s.flush()
        ctx = SecTenant(id=t.id, name=t.name)
        for role in roles:
            email = f"{role}.{label.lower()}.{sfx}@example.com"
            u = User(tenant_id=t.id, email=email, password_hash=_HASH[password], name=f"{role} {label}",
                     role=UserRole(role), roles=[role])
            s.add(u)
            await s.flush()
            ctx.users[role] = {"id": u.id, "email": email}
        await s.commit()
    return ctx


async def login(client, email: str, password: str = STRONG, ip: str | None = None, expect: int | None = 200):
    r = await client.post("/api/auth/login", json={"email": email, "password": password},
                          headers={"X-Forwarded-For": ip or new_ip(), "User-Agent": "pytest-sec"})
    if expect is not None:
        assert r.status_code == expect, r.text
    return r


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def audit_actions(user_id: str) -> list[str]:
    from sqlalchemy import select

    from src.db import AsyncSessionLocal
    from src.models import AuditEvent
    async with AsyncSessionLocal() as s:
        return list((await s.execute(select(AuditEvent.action).where(AuditEvent.user_id == user_id)
                                     .order_by(AuditEvent.created_at))).scalars().all())
