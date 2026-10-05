"""Tenant B must never see, change or reference tenant A's data."""
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from conftest import create_assets


@pytest_asyncio.fixture(scope="session")
async def a_assets(client, tenants):
    return await create_assets(client, tenants.a, "iso-A")


@pytest_asyncio.fixture(scope="session")
async def b_assets(client, tenants):
    return await create_assets(client, tenants.b, "iso-B")


RESOURCES = [
    ("/api/lines/", "line"),
    ("/api/machines/", "machine"),
    ("/api/tags/", "tag"),
    ("/api/cameras/", "camera"),
]


@pytest.mark.parametrize("url,attr", RESOURCES)
async def test_list_only_shows_own_tenant(client, tenants, a_assets, b_assets, url, attr):
    r = await client.get(url, headers=tenants.b.headers("admin"))
    assert r.status_code == 200
    items = r.json()
    ids = {i["id"] for i in items}
    assert getattr(b_assets, attr)["id"] in ids
    assert getattr(a_assets, attr)["id"] not in ids
    assert all(i["tenant_id"] == tenants.b.id for i in items)


@pytest.mark.parametrize("url,attr", RESOURCES)
async def test_get_patch_delete_other_tenant_is_404(client, tenants, a_assets, url, attr):
    h = tenants.b.headers("admin")
    item_url = f"{url}{getattr(a_assets, attr)['id']}"
    assert (await client.get(item_url, headers=h)).status_code == 404
    assert (await client.patch(item_url, json={"name": "hacked"}, headers=h)).status_code == 404
    assert (await client.delete(item_url, headers=h)).status_code == 404
    # still intact for tenant A
    r = await client.get(item_url, headers=tenants.a.headers("admin"))
    assert r.status_code == 200 and r.json()["name"] != "hacked"


async def test_filter_by_other_tenant_parent_returns_nothing(client, tenants, a_assets):
    h = tenants.b.headers("admin")
    r = await client.get("/api/machines/", params={"line_id": a_assets.line["id"]}, headers=h)
    assert r.json() == []
    r = await client.get("/api/tags/", params={"machine_id": a_assets.machine["id"]}, headers=h)
    assert r.json() == []
    r = await client.get("/api/cameras/", params={"machine_id": a_assets.machine["id"]}, headers=h)
    assert r.json() == []


async def test_alert_rules_isolated(client, tenants, a_assets, b_assets):
    h = tenants.b.headers("admin")
    ids = {r["id"] for r in (await client.get("/api/alerts/rules", headers=h)).json()}
    assert b_assets.rule["id"] in ids and a_assets.rule["id"] not in ids
    assert (await client.patch(f"/api/alerts/rules/{a_assets.rule['id']}/toggle", headers=h)).status_code == 404
    assert (await client.delete(f"/api/alerts/rules/{a_assets.rule['id']}", headers=h)).status_code == 404


async def test_alert_events_isolated(client, tenants, a_assets, db):
    from src.models import AlertEvent

    ev = AlertEvent(tenant_id=tenants.a.id, rule_id=a_assets.rule["id"],
                    triggered_at=datetime.now(timezone.utc), value_at_trigger=91.5)
    db.add(ev)
    await db.commit()
    h = tenants.b.headers("admin")
    ids = {e["id"] for e in (await client.get("/api/alerts/events", headers=h)).json()}
    assert ev.id not in ids
    assert (await client.post(f"/api/alerts/events/{ev.id}/resolve", headers=h)).status_code == 404
    ids_a = {e["id"] for e in (await client.get("/api/alerts/events", headers=tenants.a.headers("admin"))).json()}
    assert ev.id in ids_a


async def test_cannot_reference_other_tenant_assets(client, tenants, a_assets, b_assets):
    """Foreign keys in request bodies are checked against the caller's tenant."""
    h = tenants.b.headers("admin")
    r = await client.post("/api/machines/", json={"line_id": a_assets.line["id"], "name": "x"}, headers=h)
    assert r.status_code == 404
    r = await client.patch(f"/api/machines/{b_assets.machine['id']}",
                           json={"line_id": a_assets.line["id"]}, headers=h)
    assert r.status_code == 404
    r = await client.post("/api/tags/", json={"machine_id": a_assets.machine["id"], "name": "x",
                                              "protocol": "mqtt", "address": "a"}, headers=h)
    assert r.status_code == 404
    r = await client.post("/api/alerts/rules", json={"tag_id": a_assets.tag["id"], "name": "x",
                                                     "condition": "gt", "threshold": 1}, headers=h)
    assert r.status_code == 404
    r = await client.post("/api/cameras/", json={"name": "x", "rtsp_url": "rtsp://x",
                                                 "machine_id": a_assets.machine["id"]}, headers=h)
    assert r.status_code == 404
    r = await client.patch(f"/api/cameras/{b_assets.camera['id']}",
                           json={"machine_id": a_assets.machine["id"]}, headers=h)
    assert r.status_code == 404


async def test_hls_proxy_other_tenant_camera_is_404(client, tenants, a_assets):
    r = await client.get(f"/api/cameras/{a_assets.camera['id']}/hls/index.m3u8",
                         headers=tenants.b.headers("admin"))
    assert r.status_code == 404


@pytest.mark.parametrize("url", ["/api/lines/", "/api/machines/", "/api/tags/", "/api/cameras/"])
async def test_malformed_id_is_404_not_500(client, tenants, url):
    r = await client.get(f"{url}not-a-uuid", headers=tenants.a.headers("admin"))
    assert r.status_code == 404
