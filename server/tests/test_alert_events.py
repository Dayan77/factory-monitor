"""GET /api/alerts/events enrichment: rule_name, severity, machine_name, message."""
from datetime import datetime, timedelta, timezone

from conftest import create_assets


async def test_events_are_enriched(client, tenants, db):
    from src.models import AlertEvent

    assets = await create_assets(client, tenants.a, "enrich")
    now = datetime.now(timezone.utc)
    ev = AlertEvent(tenant_id=tenants.a.id, rule_id=assets.rule["id"],
                    triggered_at=now, value_at_trigger=85.25)
    db.add(ev)
    await db.commit()

    r = await client.get("/api/alerts/events", headers=tenants.a.headers("operator"))
    assert r.status_code == 200
    out = next(e for e in r.json() if e["id"] == ev.id)
    assert out["rule_id"] == assets.rule["id"]
    assert out["rule_name"] == "Temp alta enrich"
    assert out["severity"] == "critical"
    assert out["machine_name"] == "Máquina enrich"
    assert out["message"] == "Temperatura enrich: 85.25 °C"
    assert out["value_at_trigger"] == 85.25
    assert out["resolved_at"] is None
    assert out["notified"] is False


async def test_event_without_machine_and_value(client, tenants, db):
    from src.models import AlertEvent

    h = tenants.a.headers("admin")
    tag = (await client.post("/api/tags/", json={"name": "Sem máquina", "protocol": "mqtt",
                                                 "address": "x"}, headers=h)).json()
    rule = (await client.post("/api/alerts/rules", json={"tag_id": tag["id"], "name": "Solta",
                                                         "condition": "change", "severity": "info"},
                              headers=h)).json()
    ev = AlertEvent(tenant_id=tenants.a.id, rule_id=rule["id"],
                    triggered_at=datetime.now(timezone.utc), value_at_trigger=None)
    db.add(ev)
    await db.commit()
    out = next(e for e in (await client.get("/api/alerts/events", headers=h)).json() if e["id"] == ev.id)
    assert out["machine_name"] is None
    assert out["message"] is None
    assert out["severity"] == "info"


async def test_active_only_and_ordering(client, tenants, db):
    from src.models import AlertEvent

    assets = await create_assets(client, tenants.a, "order")
    base = datetime.now(timezone.utc) + timedelta(days=1)   # newest of the suite
    old = AlertEvent(tenant_id=tenants.a.id, rule_id=assets.rule["id"], triggered_at=base,
                     value_at_trigger=81)
    new = AlertEvent(tenant_id=tenants.a.id, rule_id=assets.rule["id"],
                     triggered_at=base + timedelta(minutes=1), value_at_trigger=82,
                     resolved_at=base + timedelta(minutes=2))
    db.add_all([old, new])
    await db.commit()
    h = tenants.a.headers("admin")
    events = (await client.get("/api/alerts/events", params={"limit": 2}, headers=h)).json()
    assert [e["id"] for e in events] == [new.id, old.id]
    active = (await client.get("/api/alerts/events", params={"active_only": True}, headers=h)).json()
    ids = {e["id"] for e in active}
    assert old.id in ids and new.id not in ids
