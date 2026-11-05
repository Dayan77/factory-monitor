"""Lines/machines CRUD as used by the web forms: create, edit, clear optional fields, RBAC, tenant isolation."""
from conftest import create_assets


async def test_admin_creates_line_and_machine_with_oee_parameters(client, tenants):
    h = tenants.a.headers("admin")
    r = await client.post("/api/lines/", json={"name": "Linha LED Driver", "location": "Galpão 2", "description": None}, headers=h)
    assert r.status_code == 201, r.text
    line = r.json()
    assert line["plant_id"]  # defaults to the tenant's first plant
    r = await client.post("/api/machines/", json={
        "line_id": line["id"], "name": "M1 Hipot", "model": None, "serial_number": "SN-1",
        "ideal_cycle_time_s": 12.5, "micro_stop_threshold_s": 60,
    }, headers=h)
    assert r.status_code == 201, r.text
    m = r.json()
    assert (m["ideal_cycle_time_s"], m["micro_stop_threshold_s"], m["serial_number"]) == (12.5, 60, "SN-1")


async def test_machine_patch_null_clears_optional_fields_but_keeps_required(client, tenants):
    a = await create_assets(client, tenants.a)
    h = tenants.a.headers("admin")
    mid = a.machine["id"]
    r = await client.patch(f"/api/machines/{mid}", json={"model": "X", "serial_number": "S", "ideal_cycle_time_s": 3}, headers=h)
    assert r.status_code == 200, r.text
    r = await client.patch(f"/api/machines/{mid}", json={
        "model": None, "serial_number": None, "ideal_cycle_time_s": None, "name": None, "line_id": None,
    }, headers=h)
    assert r.status_code == 200, r.text
    m = r.json()
    assert (m["model"], m["serial_number"], m["ideal_cycle_time_s"]) == (None, None, None)
    assert m["name"] == a.machine["name"] and m["line_id"] == a.machine["line_id"]


async def test_operator_cannot_create_or_edit(client, tenants):
    a = await create_assets(client, tenants.a)
    h = tenants.a.headers("operator")
    assert (await client.post("/api/lines/", json={"name": "x"}, headers=h)).status_code == 403
    assert (await client.post("/api/machines/", json={"line_id": a.line["id"], "name": "x"}, headers=h)).status_code == 403
    assert (await client.patch(f"/api/lines/{a.line['id']}", json={"name": "x"}, headers=h)).status_code == 403
    assert (await client.patch(f"/api/machines/{a.machine['id']}", json={"model": None}, headers=h)).status_code == 403


async def test_cannot_edit_other_tenants_machine(client, tenants):
    a = await create_assets(client, tenants.a)
    r = await client.patch(f"/api/machines/{a.machine['id']}", json={"model": None}, headers=tenants.b.headers("admin"))
    assert r.status_code == 404
