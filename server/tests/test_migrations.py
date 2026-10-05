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


def test_production_oee_migration_seeds_reasons_dedupes_counter_tags_and_is_reversible():
    """f7a8b9c0d1e2: Fase 2 tables + §4.4 reason codes; a tag that was the counter of two positions
    keeps only the oldest link (unique index); downgrade removes everything."""
    run_sync(_recreate_db(DB))
    try:
        run_alembic("upgrade", "e6f7a8b9c0d1", db=DB)
        t, pl, ln, m, tg = ("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222",
                            "33333333-3333-3333-3333-333333333333", "44444444-4444-4444-4444-444444444444",
                            "55555555-5555-5555-5555-555555555555")
        run_sync(_fetch(
            f"insert into tenants (id, name, slug, is_active, plan, max_users, max_machines) values ('{t}', 'A', 'a', true, 'trial', 5, 10);"
            f"insert into plants (id, tenant_id, name, timezone) values ('{pl}', '{t}', 'P', 'America/Manaus');"
            f"insert into lines (id, tenant_id, name, is_active, plant_id) values ('{ln}', '{t}', 'L', true, '{pl}');"
            f"insert into machines (id, tenant_id, line_id, name, status) values ('{m}', '{t}', '{ln}', 'M', 'unknown');"
            f"insert into tags (id, tenant_id, machine_id, name, protocol, address, data_type, scale_factor, \"offset\", "
            f"poll_interval_ms, is_active, semantic_role) values ('{tg}', '{t}', '{m}', 'c', 'mqtt', 'a', 'int', 1, 0, 1000, true, 'cycle_counter');"
            f"insert into positions (id, tenant_id, machine_id, name, component_type, cycles_per_part, counter_tag_id, created_at) values "
            f"('66666666-6666-6666-6666-666666666661', '{t}', '{m}', 'old', 'valve', 1, '{tg}', now() - interval '1 day'),"
            f"('66666666-6666-6666-6666-666666666662', '{t}', '{m}', 'new', 'valve', 1, '{tg}', now())"))
        run_alembic("upgrade", "head", db=DB)
        rows = run_sync(_fetch("select name, counter_tag_id::text c from positions order by name"))
        assert [(r["name"], r["c"]) for r in rows] == [("new", None), ("old", tg)]
        codes = run_sync(_fetch("select code, planned from reason_codes where tenant_id is null order by code"))
        assert len(codes) == 19 and {r["code"] for r in codes if r["planned"]} == {f"PLN-0{i}" for i in range(1, 6)}
        for table in ("shifts", "calendar_exceptions", "products", "machine_products", "production_runs",
                      "downtime_events", "failure_records", "micro_stop_buckets", "machine_data_spans",
                      "downtime_detector_states"):
            assert run_sync(_columns(table)), table
        assert {"micro_stop_threshold_s", "fault_code_map"} <= run_sync(_columns("machines"))
        run_alembic("downgrade", "e6f7a8b9c0d1", db=DB)
        assert run_sync(_columns("downtime_events")) == set()
        assert "micro_stop_threshold_s" not in run_sync(_columns("machines"))
        run_alembic("upgrade", "head", db=DB)
    finally:
        run_sync(_drop_db(DB))
