"""Rules engine: conditions, cooldown (memory + DB), tenant scoping, dispatch."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from conftest import create_assets


@pytest.fixture
def dispatched(monkeypatch):
    calls = []

    async def fake_dispatch(rule, event, value):
        calls.append((rule.id, event.id, value))

    import src.rules.engine as engine
    monkeypatch.setattr(engine, "dispatch", fake_dispatch)
    return calls


@pytest.mark.parametrize("cond,threshold,value,expected", [
    ("gt", 10, 10.1, True), ("gt", 10, 10, False),
    ("gte", 10, 10, True), ("gte", 10, 9.9, False),
    ("lt", 10, 9.9, True), ("lt", 10, 10, False),
    ("lte", 10, 10, True), ("lte", 10, 10.1, False),
    ("eq", 1, 1.0, True), ("eq", 1, 0, False),
    ("neq", 1, 0, True), ("neq", 1, 1, False),
    ("gt", None, 999, False),  # threshold missing never triggers
])
def test_check_condition(cond, threshold, value, expected):
    from src.models import AlertCondition
    from src.rules.engine import _check_condition

    rule = SimpleNamespace(condition=AlertCondition(cond), threshold=threshold)
    assert _check_condition(rule, value) is expected


def test_change_condition():
    from src.models import AlertCondition
    from src.rules.engine import _check_condition

    rule = SimpleNamespace(condition=AlertCondition.change, threshold=None)
    assert _check_condition(rule, 5, None) is False      # no reference yet
    assert _check_condition(rule, 5, 5) is False
    assert _check_condition(rule, 6, 5) is True


async def _count_events(db, rule_id):
    from src.models import AlertEvent
    return (await db.execute(select(func.count()).where(AlertEvent.rule_id == rule_id))).scalar_one()


async def test_evaluate_triggers_and_respects_cooldown(client, tenants, db, dispatched):
    from src.rules import engine

    assets = await create_assets(client, tenants.a)     # rule: gt 80, cooldown 5 min
    tag_id, rule_id = assets.tag["id"], assets.rule["id"]

    await engine.evaluate(db=db, tag_id=tag_id, tenant_id=tenants.a.id, value=50)
    assert await _count_events(db, rule_id) == 0

    await engine.evaluate(db=db, tag_id=tag_id, tenant_id=tenants.a.id, value=90)
    assert await _count_events(db, rule_id) == 1
    assert len(dispatched) == 1 and dispatched[0][2] == 90

    await engine.evaluate(db=db, tag_id=tag_id, tenant_id=tenants.a.id, value=95)  # within cooldown
    assert await _count_events(db, rule_id) == 1

    # Cooldown survives a restart: the in-memory cache is gone, the DB still knows.
    engine._last_triggered.clear()
    await engine.evaluate(db=db, tag_id=tag_id, tenant_id=tenants.a.id, value=96)
    assert await _count_events(db, rule_id) == 1

    # After the cooldown has elapsed it triggers again.
    engine._last_triggered[rule_id] = datetime.now(timezone.utc) - timedelta(minutes=6)
    await engine.evaluate(db=db, tag_id=tag_id, tenant_id=tenants.a.id, value=97)
    assert await _count_events(db, rule_id) == 2
    assert len(dispatched) == 2


async def test_zero_cooldown_triggers_every_time(client, tenants, db, dispatched):
    from src.rules import engine

    h = tenants.a.headers("admin")
    assets = await create_assets(client, tenants.a)
    rule = (await client.post("/api/alerts/rules", json={
        "tag_id": assets.tag["id"], "name": "sem cooldown", "condition": "lt",
        "threshold": 0, "cooldown_minutes": 0}, headers=h)).json()
    for v in (-1, -2, -3):
        await engine.evaluate(db=db, tag_id=assets.tag["id"], tenant_id=tenants.a.id, value=v)
    assert await _count_events(db, rule["id"]) == 3


async def test_inactive_rule_does_not_trigger(client, tenants, db, dispatched):
    from src.rules import engine

    assets = await create_assets(client, tenants.a)
    await client.patch(f"/api/alerts/rules/{assets.rule['id']}/toggle", headers=tenants.a.headers("admin"))
    await engine.evaluate(db=db, tag_id=assets.tag["id"], tenant_id=tenants.a.id, value=1000)
    assert await _count_events(db, assets.rule["id"]) == 0


async def test_reading_with_wrong_tenant_does_not_trigger(client, tenants, db, dispatched):
    from src.rules import engine

    assets = await create_assets(client, tenants.a)
    await engine.evaluate(db=db, tag_id=assets.tag["id"], tenant_id=tenants.b.id, value=1000)
    assert await _count_events(db, assets.rule["id"]) == 0


async def test_change_rule_end_to_end(client, tenants, db, dispatched):
    from src.rules import engine

    h = tenants.a.headers("admin")
    assets = await create_assets(client, tenants.a)
    rule = (await client.post("/api/alerts/rules", json={
        "tag_id": assets.tag["id"], "name": "mudou", "condition": "change",
        "cooldown_minutes": 0}, headers=h)).json()
    for v in (1, 1, 2, 2, 3):
        await engine.evaluate(db=db, tag_id=assets.tag["id"], tenant_id=tenants.a.id, value=v)
    assert await _count_events(db, rule["id"]) == 2


async def test_event_tenant_matches_rule(client, tenants, db, dispatched):
    from src.models import AlertEvent
    from src.rules import engine

    assets = await create_assets(client, tenants.b)
    await engine.evaluate(db=db, tag_id=assets.tag["id"], tenant_id=tenants.b.id, value=100)
    ev = (await db.execute(select(AlertEvent).where(AlertEvent.rule_id == assets.rule["id"]))).scalar_one()
    assert ev.tenant_id == tenants.b.id and ev.value_at_trigger == 100
