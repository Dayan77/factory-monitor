"""
Seed script — creates a dev tenant + admin user for first-time setup.

Usage:
    cd server
    python ../scripts/seed_dev.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)) + "/server")

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.models import Base, Tenant, User, UserRole
from src.auth.security import hash_password


DATABASE_URL = (
    f"postgresql+asyncpg://"
    f"{os.getenv('POSTGRES_USER', 'fm_user')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'fm_dev_password')}@"
    f"{os.getenv('POSTGRES_HOST', 'localhost')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/"
    f"{os.getenv('POSTGRES_DB', 'factory_monitor')}"
)

ADMIN_EMAIL      = os.getenv("SEED_ADMIN_EMAIL",      "admin@factory-monitor.com")
ADMIN_PASSWORD   = os.getenv("SEED_ADMIN_PASSWORD",   "Admin@1234")
ADMIN_NAME       = os.getenv("SEED_ADMIN_NAME",       "Administrador")
TENANT_NAME      = os.getenv("SEED_TENANT_NAME",      "Empresa Demo")
TENANT_SLUG      = os.getenv("SEED_TENANT_SLUG",      "demo")
SUPERADMIN_EMAIL = os.getenv("SEED_SUPERADMIN_EMAIL", "superadmin@factory-monitor.com")
SUPERADMIN_PASS  = os.getenv("SEED_SUPERADMIN_PASS",  "Super@1234")
SUPERADMIN_NAME  = os.getenv("SEED_SUPERADMIN_NAME",  "Super Admin")


async def seed() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        from sqlalchemy import select

        # ── Tenant demo ───────────────────────────────────────────
        existing = await db.execute(select(Tenant).where(Tenant.slug == TENANT_SLUG))
        if existing.scalar_one_or_none():
            print(f"⚠  Tenant '{TENANT_SLUG}' já existe. Seed ignorado.")
            return

        tenant = Tenant(name=TENANT_NAME, slug=TENANT_SLUG)
        db.add(tenant)
        await db.flush()

        admin = User(
            tenant_id=tenant.id,
            email=ADMIN_EMAIL,
            password_hash=hash_password(ADMIN_PASSWORD),
            name=ADMIN_NAME,
            role=UserRole.admin,
        )
        db.add(admin)

        # ── Tenant "platform" para o superadmin ───────────────────
        platform_existing = await db.execute(select(Tenant).where(Tenant.slug == "platform"))
        if not platform_existing.scalar_one_or_none():
            platform_tenant = Tenant(name="Factory Monitor Platform", slug="platform")
            db.add(platform_tenant)
            await db.flush()

            superadmin = User(
                tenant_id=platform_tenant.id,
                email=SUPERADMIN_EMAIL,
                password_hash=hash_password(SUPERADMIN_PASS),
                name=SUPERADMIN_NAME,
                role=UserRole.superadmin,
            )
            db.add(superadmin)

        await db.commit()

    print(f"✓  Tenant demo: {TENANT_NAME} (slug: {TENANT_SLUG})")
    print(f"   tenant_id: {tenant.id}  ← copie para TENANT_ID no .env do edge")
    print(f"✓  Admin:      {ADMIN_EMAIL} / {ADMIN_PASSWORD}")
    print()
    print(f"✓  Superadmin: {SUPERADMIN_EMAIL} / {SUPERADMIN_PASS}")
    print()
    print("Acesse http://localhost e faça login com as credenciais acima.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
