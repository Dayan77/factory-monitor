"""
Test harness for server/.

* Creates an isolated PostgreSQL database (default ``fm_test_server``) on the
  running Postgres, applies the Alembic migrations and drops it at the end
  (set ``FM_TEST_KEEP_DB=1`` to keep it for inspection).
* The app's session factory is rebound to a NullPool engine and the Influx
  client is created lazily per event loop, so the suite does not depend on how
  pytest-asyncio scopes its loops.
* ``client`` is an httpx.AsyncClient wired to the ASGI app (no lifespan, so no
  MQTT subscriber / sweeper runs during tests).
* ``tenants`` gives two tenants (A and B), each with one user per role.

Environment (all optional): POSTGRES_HOST/PORT/USER/PASSWORD, FM_TEST_DB,
INFLUX_URL/INFLUX_TOKEN/INFLUX_ORG/INFLUX_BUCKET.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
TEST_DB = os.environ.get("FM_TEST_DB", "fm_test_server")

# ── Environment must be set BEFORE importing anything from src ─────────────
os.environ["POSTGRES_DB"] = TEST_DB
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("POSTGRES_USER", "fm_user")
os.environ.setdefault("POSTGRES_PASSWORD", "fm_dev_password")   # docker dev default, not a secret
os.environ.setdefault("INFLUX_URL", "http://localhost:8086")
os.environ.setdefault("INFLUX_TOKEN", "dev-token-change-me")      # docker dev default, not a secret
os.environ.setdefault("INFLUX_ORG", "factory-monitor")
os.environ.setdefault("INFLUX_BUCKET", "sensors")
os.environ["APP_ENV"] = "test"
# No persistent MQTT session from test servers on the shared dev broker (the broker would keep queueing every
# reading for a client id that never comes back); tests of the persistent session use their own ids and clean up.
os.environ.setdefault("MQTT_PERSISTENT_SESSION", "false")
os.environ["APP_SECRET_KEY"] = "test-secret-key-not-used-anywhere-else"
# Sprint 7: production access tokens last 15 min; the session-scoped fixtures mint their tokens once and the suite
# runs longer than that. Tests of the token lifetime read settings.jwt_access_token_expire_minutes.
os.environ.setdefault("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "480")
os.environ["SENDGRID_API_KEY"] = ""
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["MEDIAMTX_API_URL"] = "http://127.0.0.1:9"   # refused instantly
os.environ["MEDIAMTX_HLS_URL"] = "http://127.0.0.1:9"

sys.path.insert(0, str(SERVER_DIR))

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import pytest_asyncio  # noqa: E402


def _pg_dsn(db: str) -> str:
    return (
        f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{db}"
    )


async def _recreate_db(name: str) -> None:
    conn = await asyncpg.connect(_pg_dsn("postgres"))
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


async def _drop_db(name: str) -> None:
    conn = await asyncpg.connect(_pg_dsn("postgres"))
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await conn.close()


def run_sync(coro):
    """Runs a coroutine in a private loop without touching the current loop
    (asyncio.run() would reset it and break the session loop)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def run_alembic(*args: str, db: str = TEST_DB) -> subprocess.CompletedProcess:
    env = {**os.environ, "POSTGRES_DB": db}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=SERVER_DIR, env=env, capture_output=True, text=True, check=True,
    )


@pytest.fixture(scope="session", autouse=True)
def _database():
    run_sync(_recreate_db(TEST_DB))
    run_alembic("upgrade", "head")
    yield TEST_DB
    if os.environ.get("FM_TEST_KEEP_DB") != "1":
        run_sync(_drop_db(TEST_DB))


@pytest_asyncio.fixture(scope="session")
async def app(_database):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from src.config import settings
    from src.db import AsyncSessionLocal
    from src.main import app as fastapi_app
    from src.services.influx import influx

    # NullPool: no connection outlives the coroutine that opened it, so nothing
    # gets bound to a particular event loop across fixtures/tests.
    test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
    AsyncSessionLocal.configure(bind=test_engine)

    yield fastapi_app
    await influx.close()
    await test_engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def db(app):
    from src.db import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session


# ── Tenants & users ─────────────────────────────────────────────────────────

PASSWORD = "Test@12345"
ROLES = ("operator", "manager", "admin")


@dataclass
class TenantCtx:
    id: str
    slug: str
    users: dict = field(default_factory=dict)    # role -> {"id", "email", "token"}

    def headers(self, role: str) -> dict:
        return {"Authorization": f"Bearer {self.users[role]['token']}"}


@dataclass
class Tenants:
    a: TenantCtx
    b: TenantCtx
    superadmin: dict


async def _make_tenant(session, label: str) -> TenantCtx:
    from src.auth.security import create_access_token, hash_password
    from src.models import Tenant, User, UserRole

    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(name=f"Tenant {label}", slug=f"t-{label.lower()}-{suffix}")
    session.add(tenant)
    await session.flush()
    ctx = TenantCtx(id=tenant.id, slug=tenant.slug)
    pw_hash = hash_password(PASSWORD)
    for role in ROLES:
        email = f"{role}.{label.lower()}.{suffix}@example.com"
        user = User(tenant_id=tenant.id, email=email, password_hash=pw_hash,
                    name=f"{role} {label}", role=UserRole(role))
        session.add(user)
        await session.flush()
        token = create_access_token({"sub": user.id, "tenant_id": tenant.id, "role": role})
        ctx.users[role] = {"id": user.id, "email": email, "token": token}
    return ctx


@pytest_asyncio.fixture(scope="session")
async def tenants(app) -> Tenants:
    from src.auth.security import create_access_token, hash_password
    from src.db import AsyncSessionLocal
    from src.models import User, UserRole

    async with AsyncSessionLocal() as session:
        a = await _make_tenant(session, "A")
        b = await _make_tenant(session, "B")
        email = f"super.{uuid.uuid4().hex[:8]}@example.com"
        sa_user = User(tenant_id=a.id, email=email, password_hash=hash_password(PASSWORD),
                       name="Super", role=UserRole.superadmin)
        session.add(sa_user)
        await session.flush()
        superadmin = {
            "id": sa_user.id, "email": email,
            "token": create_access_token({"sub": sa_user.id, "tenant_id": a.id, "role": "superadmin"}),
        }
        await session.commit()
    return Tenants(a=a, b=b, superadmin=superadmin)


# ── Asset helpers ───────────────────────────────────────────────────────────

@dataclass
class Assets:
    line: dict
    machine: dict
    tag: dict
    rule: dict
    camera: dict


async def create_assets(client: httpx.AsyncClient, t: TenantCtx, label: str = "") -> Assets:
    """Creates line → machine → tag → rule + camera for tenant t via the API."""
    h = t.headers("admin")
    sfx = label or uuid.uuid4().hex[:6]
    r = await client.post("/api/lines/", json={"name": f"Linha {sfx}"}, headers=h)
    assert r.status_code == 201, r.text
    line = r.json()
    r = await client.post("/api/machines/", json={"line_id": line["id"], "name": f"Máquina {sfx}"}, headers=h)
    assert r.status_code == 201, r.text
    machine = r.json()
    r = await client.post("/api/tags/", json={
        "machine_id": machine["id"], "name": f"Temperatura {sfx}", "protocol": "mqtt",
        "address": f"plc/{sfx}/temp", "unit": "°C",
    }, headers=h)
    assert r.status_code == 201, r.text
    tag = r.json()
    r = await client.post("/api/alerts/rules", json={
        "tag_id": tag["id"], "name": f"Temp alta {sfx}", "condition": "gt",
        "threshold": 80, "severity": "critical", "notify_email": False,
    }, headers=h)
    assert r.status_code == 201, r.text
    rule = r.json()
    r = await client.post("/api/cameras/", json={
        "name": f"Câmera {sfx}", "rtsp_url": "rtsp://127.0.0.1/none", "machine_id": machine["id"],
    }, headers=h)
    assert r.status_code == 201, r.text
    camera = r.json()
    return Assets(line=line, machine=machine, tag=tag, rule=rule, camera=camera)
