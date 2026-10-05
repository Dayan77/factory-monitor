"""Migrations apply from scratch and the latest ones are reversible."""
import asyncpg

from conftest import TEST_DB, _drop_db, _pg_dsn, _recreate_db, run_alembic, run_sync

DB = f"{TEST_DB}_migrations"


async def _columns(table: str) -> set[str]:
    conn = await asyncpg.connect(_pg_dsn(DB))
    try:
        rows = await conn.fetch(
            "select column_name from information_schema.columns where table_name = $1", table)
        return {r["column_name"] for r in rows}
    finally:
        await conn.close()


def test_upgrade_downgrade_upgrade():
    run_sync(_recreate_db(DB))
    try:
        run_alembic("upgrade", "head", db=DB)
        assert "last_seen_at" in run_sync(_columns("machines"))
        run_alembic("downgrade", "c4d5e6f7a8b9", db=DB)
        assert "last_seen_at" not in run_sync(_columns("machines"))
        run_alembic("upgrade", "head", db=DB)
        assert "last_seen_at" in run_sync(_columns("machines"))
        run_alembic("downgrade", "base", db=DB)
        assert run_sync(_columns("machines")) == set()
    finally:
        run_sync(_drop_db(DB))
