"""Role-based access control: operator < manager < admin < superadmin."""
import uuid

import pytest

from conftest import create_assets


@pytest.fixture
def anyname():
    return uuid.uuid4().hex[:6]


async def test_operator_cannot_create_rules(client, tenants):
    assets = await create_assets(client, tenants.a)
    r = await client.post("/api/alerts/rules", json={
        "tag_id": assets.tag["id"], "name": "x", "condition": "gt", "threshold": 1,
    }, headers=tenants.a.headers("operator"))
    assert r.status_code == 403


async def test_manager_can_create_and_toggle_rules_but_not_delete(client, tenants):
    assets = await create_assets(client, tenants.a)
    h = tenants.a.headers("manager")
    r = await client.post("/api/alerts/rules", json={
        "tag_id": assets.tag["id"], "name": "por gerente", "condition": "lt", "threshold": 5,
    }, headers=h)
    assert r.status_code == 201, r.text
    rule_id = r.json()["id"]
    r = await client.patch(f"/api/alerts/rules/{rule_id}/toggle", headers=h)
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert (await client.delete(f"/api/alerts/rules/{rule_id}", headers=h)).status_code == 403
    r = await client.delete(f"/api/alerts/rules/{rule_id}", headers=tenants.a.headers("admin"))
    assert r.status_code == 204


@pytest.mark.parametrize("role", ["operator", "manager"])
async def test_non_admin_cannot_create_assets(client, tenants, role, anyname):
    h = tenants.a.headers(role)
    assert (await client.post("/api/lines/", json={"name": anyname}, headers=h)).status_code == 403
    assets = await create_assets(client, tenants.a)
    r = await client.post("/api/machines/", json={"line_id": assets.line["id"], "name": anyname}, headers=h)
    assert r.status_code == 403
    r = await client.post("/api/tags/", json={"name": anyname, "protocol": "mqtt", "address": "a"}, headers=h)
    assert r.status_code == 403
    r = await client.post("/api/cameras/", json={"name": anyname, "rtsp_url": "rtsp://x"}, headers=h)
    assert r.status_code == 403
    assert (await client.delete(f"/api/machines/{assets.machine['id']}", headers=h)).status_code == 403


async def test_operator_cannot_update_machine_manager_can(client, tenants):
    assets = await create_assets(client, tenants.a)
    url = f"/api/machines/{assets.machine['id']}"
    r = await client.patch(url, json={"status": "maintenance"}, headers=tenants.a.headers("operator"))
    assert r.status_code == 403
    r = await client.patch(url, json={"status": "maintenance"}, headers=tenants.a.headers("manager"))
    assert r.status_code == 200 and r.json()["status"] == "maintenance"


async def test_operator_can_read_and_resolve_events(client, tenants, db):
    from datetime import datetime, timezone
    from src.models import AlertEvent

    assets = await create_assets(client, tenants.a)
    ev = AlertEvent(tenant_id=tenants.a.id, rule_id=assets.rule["id"],
                    triggered_at=datetime.now(timezone.utc), value_at_trigger=99.0)
    db.add(ev)
    await db.commit()
    h = tenants.a.headers("operator")
    assert (await client.get("/api/alerts/events", headers=h)).status_code == 200
    r = await client.post(f"/api/alerts/events/{ev.id}/resolve", headers=h)
    assert r.status_code == 200 and r.json()["resolved_at"]


async def test_admin_routes_require_superadmin(client, tenants):
    r = await client.get("/api/admin/tenants", headers=tenants.a.headers("admin"))
    assert r.status_code == 403
    r = await client.get("/api/admin/tenants",
                         headers={"Authorization": f"Bearer {tenants.superadmin['token']}"})
    assert r.status_code == 200
    ids = {t["id"] for t in r.json()}
    assert {tenants.a.id, tenants.b.id} <= ids


async def test_unauthenticated_is_rejected(client):
    for url in ("/api/lines/", "/api/machines/", "/api/tags/", "/api/alerts/rules",
                "/api/alerts/events", "/api/cameras/"):
        assert (await client.get(url)).status_code == 401, url
