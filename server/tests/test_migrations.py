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


async def _fetch(sql: str):
    conn = await asyncpg.connect(_pg_dsn(DB))
    try:
        if ";" in sql:
            await conn.execute(sql)
            return []
        return await conn.fetch(sql)
    finally:
        await conn.close()


def test_assets_migration_backfills_default_plant_and_is_reversible():
    """e6f7a8b9c0d1: every tenant gets a default plant, existing lines point to it (plant_id NOT NULL)."""
    run_sync(_recreate_db(DB))
    try:
        run_alembic("upgrade", "d5e6f7a8b9c0", db=DB)
        run_sync(_fetch(
            "insert into tenants (id, name, slug, is_active, plan, max_users, max_machines) values "
            "('11111111-1111-1111-1111-111111111111', 'A', 'a', true, 'trial', 5, 10), "
            "('22222222-2222-2222-2222-222222222222', 'B', 'b', true, 'trial', 5, 10);"
            "insert into lines (id, tenant_id, name, is_active) values "
            "('33333333-3333-3333-3333-333333333333', '11111111-1111-1111-1111-111111111111', 'L1', true)"))
        run_alembic("upgrade", "head", db=DB)
        rows = run_sync(_fetch("select l.tenant_id::text t, p.tenant_id::text pt, p.name, p.timezone "
                               "from lines l join plants p on p.id = l.plant_id"))
        assert [(r["t"], r["pt"], r["name"], r["timezone"]) for r in rows] == [
            ("11111111-1111-1111-1111-111111111111", "11111111-1111-1111-1111-111111111111",
             "Planta principal", "America/Sao_Paulo")]
        assert len(run_sync(_fetch("select 1 from plants"))) == 2
        for table in ("positions", "components", "usage_totals", "usage_buckets", "tag_states", "leads"):
            assert run_sync(_columns(table)), table
        assert {"semantic_role", "position_id", "counter_max"} <= run_sync(_columns("tags"))
        run_alembic("downgrade", "d5e6f7a8b9c0", db=DB)
        assert "plant_id" not in run_sync(_columns("lines"))
        assert run_sync(_columns("plants")) == set()
        run_alembic("upgrade", "head", db=DB)
    finally:
        run_sync(_drop_db(DB))
