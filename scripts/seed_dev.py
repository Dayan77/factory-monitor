"""
Seed script — creates a dev tenant + admin user for first-time setup.

Usage:
    cd server
    python ../scripts/seed_dev.py

Production / VPS (APP_ENV=production, or SEED_ENFORCE_PROD_CREDENTIALS=1 which
scripts/seed_remote.py always sets): refuses to run unless SEED_ADMIN_PASSWORD
and SEED_SUPERADMIN_PASS are set, are not the public dev defaults below and have
at least MIN_PROD_PASSWORD_LEN characters. Passwords are never printed there.
"""

import asyncio
import sys
import os
from typing import Mapping

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/server")


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

# Public dev defaults (README / .env.example / CLAUDE.md): never valid in production.
DEV_DEFAULT_PASSWORDS = frozenset({"Admin@1234", "Super@1234"})
MIN_PROD_PASSWORD_LEN = 12
PASSWORD_VARS = ("SEED_ADMIN_PASSWORD", "SEED_SUPERADMIN_PASS")


def production_mode(env: Mapping[str, str] = os.environ) -> bool:
    return (
        env.get("APP_ENV", "").strip().lower() in {"production", "prod"}
        or env.get("SEED_ENFORCE_PROD_CREDENTIALS", "").strip() == "1"
    )


def production_credential_errors(env: Mapping[str, str] = os.environ) -> list[str]:
    """Problems with the seed passwords for a production run (names only, never values)."""
    errors = []
    for var in PASSWORD_VARS:
        value = env.get(var, "")
        if not value.strip():
            errors.append(f"{var} não definido no ambiente do container")
        elif value in DEV_DEFAULT_PASSWORDS:
            errors.append(f"{var} usa a senha padrão de desenvolvimento (pública)")
        elif len(value) < MIN_PROD_PASSWORD_LEN:
            errors.append(f"{var} precisa de pelo menos {MIN_PROD_PASSWORD_LEN} caracteres")
    return errors


def ensure_safe_credentials(env: Mapping[str, str] = os.environ) -> None:
    """Raises SystemExit(3) in production mode when the seed passwords are unsafe."""
    if not production_mode(env):
        return
    errors = production_credential_errors(env)
    if errors:
        msg = "\n".join(f"  - {e}" for e in errors)
        raise SystemExit(f"✗ Seed recusado em produção:\n{msg}")


def _shown(password: str) -> str:
    return "********" if production_mode() else password


async def seed() -> None:
    ensure_safe_credentials()

    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from src.models import Tenant, User, UserRole
    from src.auth.security import hash_password

    engine = create_async_engine(DATABASE_URL, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        from sqlalchemy import select

        # ── Tenant demo ───────────────────────────────────────────
        existing = await db.execute(select(Tenant).where(Tenant.slug == TENANT_SLUG))
        if existing.scalar_one_or_none():
            print(f"⚠  Tenant '{TENANT_SLUG}' já existe. Pulando criação do tenant demo.")
        else:
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
            print(f"✓  Tenant demo: {TENANT_NAME} (slug: {TENANT_SLUG})")
            print(f"   Admin: {ADMIN_EMAIL} / {_shown(ADMIN_PASSWORD)}")

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

    print(f"✓  Superadmin: {SUPERADMIN_EMAIL} / {_shown(SUPERADMIN_PASS)}")
    print()
    print("Acesse /admin após login com o superadmin.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
