"""Full round trip: head -> base -> head must work (a fresh `downgrade base` must leave no residue).

Regression (Sprint 1 QA gate): initial_schema downgrade() used to drop the tables but not the
Postgres enum types it created (userrole, ...), so the second `upgrade head` failed with
DuplicateObject: type "userrole" already exists.
"""
import asyncpg

from conftest import TEST_DB, _drop_db, _pg_dsn, _recreate_db, run_alembic, run_sync

DB = f"{TEST_DB}_roundtrip"


async def _user_enum_types() -> set[str]:
    conn = await asyncpg.connect(_pg_dsn(DB))
    try:
        rows = await conn.fetch(
            "select t.typname from pg_type t join pg_namespace n on n.oid = t.typnamespace "
            "where t.typtype = 'e' and n.nspname = 'public'")
        return {r["typname"] for r in rows}
    finally:
        await conn.close()


def test_head_base_head_roundtrip():
    run_sync(_recreate_db(DB))
    try:
        run_alembic("upgrade", "head", db=DB)
        run_alembic("downgrade", "base", db=DB)
        assert run_sync(_user_enum_types()) == set()
        run_alembic("upgrade", "head", db=DB)
    finally:
        run_sync(_drop_db(DB))
