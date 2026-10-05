"""Plants / positions / components / usage REST API: behaviour, RBAC and tenant isolation."""
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from usage_helpers import add_component, add_position, add_tag, api, build_plant


@pytest_asyncio.fixture(scope="session")
async def world(client, tenants):
    """Tenant A and B each with plant → line → machine → position (+component, tags)."""
    out = {}
    for key, t in (("a", tenants.a), ("b", tenants.b)):
        p = await build_plant(client, t, f"api-{key}")
        pos = await add_position(client, t, p, f"Cilindro {key}")
        comp = await add_component(client, t, p, "c", pos, nominal_life={"cycles": 5_000_000})
        stock = await add_component(client, t, p, "stock")
        tag = await add_tag(client, t, p, f"ciclos-{key}", "cycle_counter", pos)
        out[key] = {"p": p, "pos": pos, "comp": comp, "stock": stock, "tag": tag}
    return out


# ── Plants ──────────────────────────────────────────────────────────────────

async def test_plant_crud_and_validation(client, tenants):
    h = tenants.a.headers("admin")
    plant = await api(client, "POST", "/api/plants/", h, 201, json={"name": "Planta X", "timezone": "America/Manaus",
                                                                     "address": "Distrito Industrial"})
    assert plant["timezone"] == "America/Manaus"
    await api(client, "POST", "/api/plants/", h, 422, json={"name": "Bad", "timezone": "Mars/Base"})
    upd = await api(client, "PATCH", f"/api/plants/{plant['id']}", h, json={"name": "Planta Y", "address": None})
    assert upd["name"] == "Planta Y" and upd["address"] is None
    line = await api(client, "POST", "/api/lines/", h, 201, json={"name": "L", "plant_id": plant["id"]})
    await api(client, "DELETE", f"/api/plants/{plant['id']}", h, 409)
    await api(client, "DELETE", f"/api/lines/{line['id']}", h, 204)
    await api(client, "DELETE", f"/api/plants/{plant['id']}", h, 204)


async def test_line_without_plant_goes_to_default_plant(client, tenants):
    h = tenants.a.headers("admin")
    line = await api(client, "POST", "/api/lines/", h, 201, json={"name": "Sem planta"})
    plants = await api(client, "GET", "/api/plants/", h)
    assert line["plant_id"] in {p["id"] for p in plants}


async def test_plant_tree(client, tenants, world):
    tree = await api(client, "GET", "/api/plants/tree", tenants.a.headers("operator"))
    p = world["a"]["p"]
    plant = next(x for x in tree if x["id"] == p.plant["id"])
    assert plant["timezone"] == "America/Manaus"
    line = next(x for x in plant["lines"] if x["id"] == p.line["id"])
    assert line["status_summary"] == {"running": 0, "stopped": 0, "error": 0, "unknown": 1, "maintenance": 0}
    assert line["machines"] == [{"id": p.machine["id"], "name": p.machine["name"], "status": "unknown",
                                 "last_seen_at": None, "positions_count": 1}]
    ids_b = {x["id"] for x in await api(client, "GET", "/api/plants/tree", tenants.b.headers("operator"))}
    assert p.plant["id"] not in ids_b


async def test_line_output_machine_must_belong_to_line(client, tenants, world):
    h = tenants.a.headers("admin")
    p = world["a"]["p"]
    other = await build_plant(client, tenants.a)
    await api(client, "PATCH", f"/api/lines/{p.line['id']}", h, 422, json={"output_machine_id": other.machine["id"]})
    r = await api(client, "PATCH", f"/api/lines/{p.line['id']}", h, json={"output_machine_id": p.machine["id"],
                                                                           "bottleneck_machine_id": p.machine["id"]})
    assert r["output_machine_id"] == p.machine["id"]
    r = await api(client, "PATCH", f"/api/lines/{p.line['id']}", h, json={"bottleneck_machine_id": None})
    assert r["bottleneck_machine_id"] is None and r["output_machine_id"] == p.machine["id"]


async def test_line_production_without_output_machine(client, tenants):
    p = await build_plant(client, tenants.a)
    r = await api(client, "GET", f"/api/lines/{p.line['id']}/production", tenants.a.headers("operator"))
    assert r["reason"] == "no_output_machine" and r["total"] == 0 and r["cadence_per_hour"] is None


# ── Positions / components ──────────────────────────────────────────────────

async def test_position_detail_life_and_projection(client, tenants, db):
    """kpis.md U1/U3: 3.6 M of 5 M cycles → 0.72; 280 000 cycles in 14 days → 70 days."""
    from src.models import UsageBucket, UsageTotal
    t = tenants.a
    p = await build_plant(client, t)
    pos = await add_position(client, t, p, "Cilindro U1")
    now = datetime.now(timezone.utc)
    comp = await add_component(client, t, p, "u1", pos, nominal_life={"cycles": 5_000_000},
                               installed_at=now - timedelta(days=200))
    db.add(UsageTotal(scope="component", entity_id=comp["id"], measure="cycles", tenant_id=t.id, value=3_600_000))
    db.add(UsageTotal(scope="position", entity_id=pos["id"], measure="cycles", tenant_id=t.id, value=4_100_000))
    hour = now.replace(minute=0, second=0, microsecond=0)
    for d in range(14):    # 20 000 cycles/day spread in the last 14 days
        db.add(UsageBucket(scope="component", entity_id=comp["id"], measure="cycles", tenant_id=t.id,
                           bucket_start=hour - timedelta(days=d, hours=1), value=20_000))
    db.add(UsageBucket(scope="component", entity_id=comp["id"], measure="cycles", tenant_id=t.id,
                       bucket_start=hour - timedelta(days=30), value=999_999))   # outside the window
    await db.commit()
    r = await api(client, "GET", f"/api/positions/{pos['id']}", t.headers("operator"))
    assert r["usage"]["cycles_total"] == 4_100_000
    assert r["usage"]["cycles_estimated"] is True       # no counter tag on this position
    cu = r["component_usage"]
    assert cu["cycles"] == 3_600_000 and cu["life_consumed_pct"] == pytest.approx(0.72)
    assert cu["life_measure"] == "cycles" and cu["rul_days"] == 70 and cu["low_confidence"] is False
    from zoneinfo import ZoneInfo
    today = now.astimezone(ZoneInfo("America/Manaus")).date()
    assert cu["projected_end_date"] == (today + timedelta(days=70)).isoformat()
    assert r["installed_component"]["life_consumed_pct"] == pytest.approx(0.72)

    usage = await api(client, "GET", f"/api/machines/{p.machine['id']}/usage", t.headers("operator"))
    row = usage["positions"][0]
    assert row["cycles_total"] == 4_100_000 and row["life_consumed_pct"] == pytest.approx(0.72)


async def test_projection_null_without_nominal_life_or_history(client, tenants):
    t = tenants.a
    p = await build_plant(client, t)
    pos = await add_position(client, t, p, "Motor sem vida")
    await add_component(client, t, p, "m", pos)
    cu = (await api(client, "GET", f"/api/positions/{pos['id']}", t.headers("operator")))["component_usage"]
    assert cu["life_consumed_pct"] is None and cu["projected_end_date"] is None
    assert cu["rul_reason"] == "no_nominal_life"
    pos2 = await add_position(client, t, p, "Cilindro novo")
    await add_component(client, t, p, "n", pos2, nominal_life={"cycles": 1000})
    cu = (await api(client, "GET", f"/api/positions/{pos2['id']}", t.headers("operator")))["component_usage"]
    assert cu["life_consumed_pct"] == 0 and cu["rul_reason"] == "insufficient_data"


async def test_install_rules(client, tenants, world):
    h = tenants.a.headers("admin")
    w = world["a"]
    # occupied position → 409
    await api(client, "POST", f"/api/positions/{w['pos']['id']}/install", h, 409,
              json={"component_id": w["stock"]["id"]})
    # wrong type → 422
    p = await build_plant(client, tenants.a)
    motor_pos = await add_position(client, tenants.a, p, "Motor", "electric_motor")
    await api(client, "POST", f"/api/positions/{motor_pos['id']}/install", h, 422,
              json={"component_id": w["stock"]["id"]})
    # stock list
    stock = await api(client, "GET", "/api/components/", h, params={"in_stock": "true"})
    assert w["stock"]["id"] in {c["id"] for c in stock} and w["comp"]["id"] not in {c["id"] for c in stock}
    assert all(c["state"] == "in_stock" for c in stock)
    # future install date → 422
    cyl = await add_position(client, tenants.a, p, "Cilindro livre")
    await api(client, "POST", f"/api/positions/{cyl['id']}/install", h, 422, json={
        "component_id": w["stock"]["id"], "installed_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()})
    d = await api(client, "POST", f"/api/positions/{cyl['id']}/install", h, json={"component_id": w["stock"]["id"]})
    assert d["installed_component"]["id"] == w["stock"]["id"] and d["installed_component_id"] == w["stock"]["id"]
    # removing → kept as history, cannot be reinstalled, removing twice → 409
    rm = await api(client, "POST", f"/api/components/{w['stock']['id']}/remove", h, json={"reason": "failure"})
    assert rm["removal_reason"] == "failure" and rm["state"] == "removed"
    await api(client, "POST", f"/api/components/{w['stock']['id']}/remove", h, 409, json={"reason": "failure"})
    await api(client, "POST", f"/api/positions/{cyl['id']}/install", h, 409, json={"component_id": w["stock"]["id"]})
    await api(client, "POST", f"/api/components/{w['stock']['id']}/remove", h, 422, json={"reason": "lost"})
    assert (await api(client, "GET", f"/api/components/{w['stock']['id']}", h))["state"] == "removed"


async def test_position_delete_requires_no_installed_component(client, tenants):
    t = tenants.a
    h = t.headers("admin")
    p = await build_plant(client, t)
    pos = await add_position(client, t, p, "Temporária")
    c = await add_component(client, t, p, "c", pos)
    await api(client, "DELETE", f"/api/positions/{pos['id']}", h, 409)
    await api(client, "POST", f"/api/components/{c['id']}/remove", h, json={"reason": "other"})
    await api(client, "DELETE", f"/api/positions/{pos['id']}", h, 204)
    assert (await api(client, "GET", f"/api/components/{c['id']}", h))["state"] == "removed"


async def test_position_counter_tag_and_patch(client, tenants, world):
    h = tenants.a.headers("admin")
    w = world["a"]
    other = await build_plant(client, tenants.a)
    other_tag = await add_tag(client, tenants.a, other, "alheia", "cycle_counter")
    await api(client, "PATCH", f"/api/positions/{w['pos']['id']}", h, 422, json={"counter_tag_id": other_tag["id"]})
    r = await api(client, "PATCH", f"/api/positions/{w['pos']['id']}", h,
                  json={"counter_tag_id": w["tag"]["id"], "cycles_per_part": 2})
    assert r["counter_tag_id"] == w["tag"]["id"] and r["cycles_per_part"] == 2
    r = await api(client, "PATCH", f"/api/positions/{w['pos']['id']}", h, json={"counter_tag_id": None})
    assert r["counter_tag_id"] is None
    await api(client, "PATCH", f"/api/positions/{w['pos']['id']}", h, 422, json={"cycles_per_part": 0})


async def test_component_nominal_life_validation_and_edit(client, tenants, world):
    h = tenants.a.headers("admin")
    await api(client, "POST", "/api/components/", h, 422, json={"component_type": "valve", "nominal_life": {"cycles": -1}})
    await api(client, "POST", "/api/components/", h, 422, json={"component_type": "valve", "nominal_life": {"km": 5}})
    await api(client, "POST", "/api/components/", h, 422, json={"component_type": "turbine"})
    c = world["a"]["comp"]
    r = await api(client, "PATCH", f"/api/components/{c['id']}", h,
                  json={"nominal_life": {"cycles": 8_000_000, "hours": 20000}, "manufacturer": "SMC"})
    assert r["nominal_life"] == {"cycles": 8_000_000, "hours": 20000.0, "starts": None}
    assert r["manufacturer"] == "SMC"


# ── Tags: semantic role ─────────────────────────────────────────────────────

async def test_tag_semantic_fields(client, tenants, world):
    h = tenants.a.headers("admin")
    w = world["a"]
    tag = await api(client, "POST", "/api/tags/", h, 201, json={
        "position_id": w["pos"]["id"], "name": "pos-only", "protocol": "modbus_tcp", "address": "hr:100",
        "semantic_role": "cycle_counter", "counter_max": 65535})
    assert tag["machine_id"] == w["p"].machine["id"]       # derived from the position
    assert tag["semantic_role"] == "cycle_counter" and tag["counter_max"] == 65535
    # roles that must be linked
    for role in ("cycle_counter", "run_state", "part_counter_good"):
        await api(client, "POST", "/api/tags/", h, 422, json={"name": "x", "protocol": "mqtt", "address": "a",
                                                               "semantic_role": role})
    await api(client, "POST", "/api/tags/", h, 422, json={"name": "x", "protocol": "mqtt", "address": "a",
                                                           "semantic_role": "bogus", "machine_id": w["p"].machine["id"]})
    # position of another machine
    other = await build_plant(client, tenants.a)
    await api(client, "POST", "/api/tags/", h, 422, json={
        "machine_id": other.machine["id"], "position_id": w["pos"]["id"], "name": "x", "protocol": "mqtt",
        "address": "a", "semantic_role": "cycle_counter"})
    upd = await api(client, "PATCH", f"/api/tags/{tag['id']}", h, json={"counter_max": None, "semantic_role": "cycle_pulse"})
    assert upd["counter_max"] is None and upd["semantic_role"] == "cycle_pulse"
    upd = await api(client, "PATCH", f"/api/tags/{tag['id']}", h, json={"position_id": None})
    assert upd["position_id"] is None and upd["machine_id"] == w["p"].machine["id"]
    lst = await api(client, "GET", "/api/tags/", h, params={"semantic_role": "cycle_pulse"})
    assert tag["id"] in {x["id"] for x in lst}
    # default role
    analog = await api(client, "POST", "/api/tags/", h, 201, json={"name": "t", "protocol": "mqtt", "address": "a"})
    assert analog["semantic_role"] == "analog"


async def test_usage_history_validation(client, tenants, world):
    h = tenants.a.headers("operator")
    w = world["a"]
    await api(client, "GET", "/api/usage/history", h, 422, params={"measure": "cycles"})
    await api(client, "GET", "/api/usage/history", h, 422, params={"measure": "good", "position_id": w["pos"]["id"]})
    await api(client, "GET", "/api/usage/history", h, 422, params={"measure": "km", "position_id": w["pos"]["id"]})
    r = await api(client, "GET", "/api/usage/history", h, params={"measure": "hours", "position_id": w["pos"]["id"],
                                                                  "bucket": "1d"})
    assert r["points"] == [] and r["measure"] == "hours"


async def test_usage_history_daily_buckets_in_plant_timezone(client, tenants, db):
    from src.models import UsageBucket
    t = tenants.a
    p = await build_plant(client, t, tz="America/Sao_Paulo")      # UTC−3
    pos = await add_position(client, t, p, "Motor H", "electric_motor")
    day = datetime(2026, 9, 1, tzinfo=timezone.utc)
    # 02:00Z and 04:00Z on Sep 1 → Aug 31 23:00 and Sep 1 01:00 local
    for hh in (2, 4):
        db.add(UsageBucket(scope="position", entity_id=pos["id"], measure="operating_seconds", tenant_id=t.id,
                           bucket_start=day.replace(hour=hh), value=1800))
    await db.commit()
    r = await api(client, "GET", "/api/usage/history", t.headers("operator"), params={
        "position_id": pos["id"], "measure": "hours", "bucket": "1d",
        "start": "2026-08-31T00:00:00Z", "stop": "2026-09-02T00:00:00Z"})
    assert [pt["value"] for pt in r["points"]] == [0.5, 0.5]
    assert r["points"][0]["t"].startswith("2026-08-31T03:00")


# ── RBAC ────────────────────────────────────────────────────────────────────

async def test_rbac_read_operator_write_admin(client, tenants, world):
    op, mgr = tenants.a.headers("operator"), tenants.a.headers("manager")
    w = world["a"]
    for url in ("/api/plants/", "/api/plants/tree", f"/api/positions/?machine_id={w['p'].machine['id']}",
                f"/api/positions/{w['pos']['id']}", "/api/components/", f"/api/components/{w['comp']['id']}",
                f"/api/machines/{w['p'].machine['id']}/usage", f"/api/lines/{w['p'].line['id']}/production"):
        assert (await client.get(url, headers=op)).status_code == 200, url
    writes = [
        ("POST", "/api/plants/", {"name": "x"}),
        ("PATCH", f"/api/plants/{w['p'].plant['id']}", {"name": "x"}),
        ("DELETE", f"/api/plants/{w['p'].plant['id']}", None),
        ("POST", "/api/positions/", {"machine_id": w["p"].machine["id"], "name": "x", "component_type": "valve"}),
        ("PATCH", f"/api/positions/{w['pos']['id']}", {"name": "x"}),
        ("DELETE", f"/api/positions/{w['pos']['id']}", None),
        ("POST", f"/api/positions/{w['pos']['id']}/install", {"component_id": w["stock"]["id"]}),
        ("POST", "/api/components/", {"component_type": "valve"}),
        ("PATCH", f"/api/components/{w['comp']['id']}", {"model": "x"}),
        ("POST", f"/api/components/{w['comp']['id']}/remove", {"reason": "failure"}),
    ]
    for headers in (op, mgr):
        for method, url, body in writes:
            r = await client.request(method, url, json=body, headers=headers)
            assert r.status_code == 403, (method, url, r.status_code)
    for url in ("/api/plants/", "/api/plants/tree", "/api/components/"):
        assert (await client.get(url)).status_code == 401


# ── Tenant isolation (every new endpoint) ───────────────────────────────────

async def test_tenant_isolation_lists(client, tenants, world):
    hb = tenants.b.headers("admin")
    a = world["a"]
    assert a["p"].plant["id"] not in {x["id"] for x in await api(client, "GET", "/api/plants/", hb)}
    assert a["pos"]["id"] not in {x["id"] for x in await api(client, "GET", "/api/positions/", hb)}
    assert await api(client, "GET", "/api/positions/", hb, params={"machine_id": a["p"].machine["id"]}) == []
    comps = await api(client, "GET", "/api/components/", hb)
    assert a["comp"]["id"] not in {x["id"] for x in comps} and world["b"]["comp"]["id"] in {x["id"] for x in comps}
    assert await api(client, "GET", "/api/components/", hb, params={"position_id": a["pos"]["id"]}) == []
    assert await api(client, "GET", "/api/lines/", hb, params={"plant_id": a["p"].plant["id"]}) == []
    assert await api(client, "GET", "/api/tags/", hb, params={"position_id": a["pos"]["id"]}) == []


async def test_tenant_isolation_by_id(client, tenants, world):
    hb = tenants.b.headers("admin")
    a, b = world["a"], world["b"]
    for method, url, body in [
        ("GET", f"/api/plants/{a['p'].plant['id']}", None),
        ("PATCH", f"/api/plants/{a['p'].plant['id']}", {"name": "hacked"}),
        ("DELETE", f"/api/plants/{a['p'].plant['id']}", None),
        ("GET", f"/api/positions/{a['pos']['id']}", None),
        ("PATCH", f"/api/positions/{a['pos']['id']}", {"name": "hacked"}),
        ("DELETE", f"/api/positions/{a['pos']['id']}", None),
        ("POST", f"/api/positions/{a['pos']['id']}/install", {"component_id": b["stock"]["id"]}),
        ("POST", f"/api/positions/{b['pos']['id']}/install", {"component_id": a["stock"]["id"]}),
        ("GET", f"/api/components/{a['comp']['id']}", None),
        ("PATCH", f"/api/components/{a['comp']['id']}", {"model": "hacked"}),
        ("POST", f"/api/components/{a['comp']['id']}/remove", {"reason": "failure"}),
        ("GET", f"/api/machines/{a['p'].machine['id']}/usage", None),
        ("GET", f"/api/lines/{a['p'].line['id']}/production", None),
        ("GET", f"/api/usage/history?measure=cycles&position_id={a['pos']['id']}", None),
        ("GET", f"/api/usage/history?measure=good&machine_id={a['p'].machine['id']}", None),
        ("GET", f"/api/usage/history?measure=cycles&component_id={a['comp']['id']}", None),
    ]:
        r = await client.request(method, url, json=body, headers=hb)
        assert r.status_code == 404, (method, url, r.status_code, r.text)
    # references to tenant A's rows in tenant B's bodies
    for method, url, body in [
        ("POST", "/api/lines/", {"name": "x", "plant_id": a["p"].plant["id"]}),
        ("PATCH", f"/api/lines/{b['p'].line['id']}", {"plant_id": a["p"].plant["id"]}),
        ("PATCH", f"/api/lines/{b['p'].line['id']}", {"output_machine_id": a["p"].machine["id"]}),
        ("POST", "/api/positions/", {"machine_id": a["p"].machine["id"], "name": "x", "component_type": "valve"}),
        ("PATCH", f"/api/positions/{b['pos']['id']}", {"counter_tag_id": a["tag"]["id"]}),
        ("POST", "/api/components/", {"component_type": "pneumatic_cylinder", "position_id": a["pos"]["id"]}),
        ("POST", "/api/tags/", {"name": "x", "protocol": "mqtt", "address": "a", "semantic_role": "cycle_counter",
                                "position_id": a["pos"]["id"]}),
        ("PATCH", f"/api/tags/{b['tag']['id']}", {"position_id": a["pos"]["id"]}),
    ]:
        r = await client.request(method, url, json=body, headers=hb)
        assert r.status_code == 404, (method, url, r.status_code, r.text)
    # tenant A untouched
    ha = tenants.a.headers("admin")
    assert (await api(client, "GET", f"/api/plants/{a['p'].plant['id']}", ha))["name"] != "hacked"
    assert (await api(client, "GET", f"/api/components/{a['comp']['id']}", ha))["state"] == "installed"


@pytest.mark.parametrize("url", ["/api/plants/", "/api/positions/", "/api/components/"])
async def test_malformed_ids_are_404(client, tenants, url):
    assert (await client.get(f"{url}not-a-uuid", headers=tenants.a.headers("admin"))).status_code == 404

