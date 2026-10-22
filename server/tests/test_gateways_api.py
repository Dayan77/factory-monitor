"""Sprint 5 — field gateways: fleet CRUD + RBAC, one-time enrollment, remote config (edge map v1 contract,
ETag / 304, config_version), heartbeat status, Raio-X overview, MQTT credentials and tenant isolation."""
from __future__ import annotations

import base64
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import update

from conftest import _make_tenant
from usage_helpers import add_position, add_tag, api, build_plant

ROOT = Path(__file__).resolve().parents[2]

DEVICES = [{"id": "opcua-plc", "protocol": "opcua", "endpoint": "opc.tcp://plc.local:4840/fm/",
            "publishing_interval_ms": 250, "timestamps": "source"},
           {"id": "modbus-plc", "protocol": "modbus", "host": "10.0.0.5", "port": 502}]


async def _tenant(label):
    from src.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, label)
        await s.commit()
    return t


def gw_h(token: str) -> dict:
    return {"Authorization": f"Gateway {token}"}


async def _setup(client, t, label):
    p = await build_plant(client, t, label)
    pos = await add_position(client, t, p, "Cilindro A1")
    run = await add_tag(client, t, p, "run", "run_state", data_type="bool")
    stroke = await add_tag(client, t, p, "stroke", "stroke_time", position=pos)
    h = t.headers("admin")
    cnt = await api(client, "POST", "/api/tags/", h, 201, json={
        "machine_id": p.machine["id"], "position_id": pos["id"], "name": "ciclos A1", "protocol": "modbus_tcp",
        "address": "10", "semantic_role": "cycle_counter", "counter_max": 65535})
    gw = await api(client, "POST", "/api/gateways/", h, 201, json={
        "name": f"gw {label}", "plant_id": p.plant["id"], "devices": DEVICES, "heartbeat_s": 10})
    return p, pos, gw, {"run": run, "stroke": stroke, "cnt": cnt}


async def _enroll(client, t, gw):
    code = (await api(client, "POST", f"/api/gateways/{gw['id']}/enroll-token", t.headers("admin")))["code"]
    return await api(client, "POST", "/api/gateways/enroll", {}, json={"code": code})


async def test_gateway_lifecycle_enroll_config_heartbeat(client):
    t = await _tenant("GW")
    p, pos, gw, tags = await _setup(client, t, "GW")
    h = t.headers("admin")
    assert gw["status"] == "pending" and gw["enrolled"] is False and gw["config_version"] == 0
    # RBAC: operator cannot see the fleet; manager can list but not create
    await api(client, "GET", "/api/gateways/", t.headers("operator"), 403)
    assert [g["id"] for g in await api(client, "GET", "/api/gateways/", t.headers("manager"))] == [gw["id"]]
    await api(client, "POST", "/api/gateways/", t.headers("manager"), 403, json={"name": "x", "plant_id": p.plant["id"]})
    # invalid devices → 422 (credentials in URL refused, unknown protocol)
    await api(client, "PATCH", f"/api/gateways/{gw['id']}", h, 422,
              json={"devices": [{"id": "x", "protocol": "opcua", "endpoint": "opc.tcp://user:pw@plc:4840/"}]})
    await api(client, "PATCH", f"/api/gateways/{gw['id']}", h, 422, json={"devices": [{"id": "x", "protocol": "s7"}]})

    # assign tags (OPC UA defaults to the OPC UA device; Modbus with edge fields)
    out = await api(client, "PUT", f"/api/gateways/{gw['id']}/tags", h, json={"tags": [
        {"tag_id": tags["run"]["id"]}, {"tag_id": tags["stroke"]["id"], "key": "montagem.fixacao.a1.stroke"},
        {"tag_id": tags["cnt"]["id"], "source": "modbus-plc", "unit_id": 2, "register_type": "holding"}]})
    assert out["assigned"] == 3 and out["config_version"] == 1 and out["warnings"] == []
    # wrong register type for the protocol rule / unknown source → 422, nothing changed
    await api(client, "PUT", f"/api/gateways/{gw['id']}/tags", h, 422, json={"tags": [
        {"tag_id": tags["cnt"]["id"], "source": "opcua-plc"}]})

    # enrollment: one-time code → token; the code cannot be reused
    code = (await api(client, "POST", f"/api/gateways/{gw['id']}/enroll-token", h))["code"]
    assert len(code.replace("-", "")) == 12
    enr = await api(client, "POST", "/api/gateways/enroll", {}, json={"code": code.lower()})
    token = enr["token"]
    assert enr["gateway_id"] == gw["id"] and token.startswith("fcg_") and enr["mqtt"]["password"]
    assert enr["mqtt"]["topic_prefix"] == f"factory/{t.id}/"
    await api(client, "POST", "/api/gateways/enroll", {}, 401, json={"code": code})

    # device config: exact edge contract (validated by the edge's own parser)
    r = await client.get(f"/api/gateways/{gw['id']}/config", headers=gw_h(token))
    assert r.status_code == 200, r.text
    body, etag = r.json(), r.headers["ETag"]
    assert body["gateway_id"] == gw["id"] and body["config_version"] == 1 and body["revision"] == "1"
    pm = body["plant_map"]
    assert pm["version"] == 1 and pm["gateway_id"] == gw["id"] and pm["tenant_id"] == t.id
    assert pm["sources"] == DEVICES and pm["heartbeat_s"] == 10
    by_id = {x["tag_id"]: x for x in pm["tags"]}
    assert set(by_id) == {tags["run"]["id"], tags["stroke"]["id"], tags["cnt"]["id"]}
    assert by_id[tags["stroke"]["id"]]["key"] == "montagem.fixacao.a1.stroke"
    assert by_id[tags["stroke"]["id"]]["position_id"] == pos["id"]
    c = by_id[tags["cnt"]["id"]]
    assert (c["source"], c["address"], c["unit_id"], c["register_type"], c["counter_max"], c["semantic_role"]) == (
        "modbus-plc", 10, 2, "holding", 65535, "cycle_counter")
    assert by_id[tags["run"]["id"]]["source"] == "opcua-plc" and by_id[tags["run"]["id"]]["semantic_role"] == "run_state"
    sys.path.insert(0, str(ROOT))
    from edge.src.plant_map import parse_plant_map
    from edge.src.remote_config import _extract_map, _validate
    doc, revision = _extract_map(body)
    parsed = _validate(doc, gw["id"], env={})
    assert revision == "1" and len(parsed.tags) == 3 and parsed.skipped == []
    assert {tg.tag_id for tg in parse_plant_map(doc, env={}).tags} == set(by_id)

    # If-None-Match → 304; a tag change bumps config_version (new ETag)
    r = await client.get(f"/api/gateways/{gw['id']}/config", headers={**gw_h(token), "If-None-Match": etag})
    assert r.status_code == 304
    await api(client, "PATCH", f"/api/tags/{tags['run']['id']}", h, json={"poll_interval_ms": 500})
    r = await client.get(f"/api/gateways/{gw['id']}/config", headers={**gw_h(token), "If-None-Match": etag})
    assert r.status_code == 200 and r.json()["config_version"] == 2 and r.headers["ETag"] != etag

    # auth: no / wrong token → 401; the token only opens its own gateway
    assert (await client.get(f"/api/gateways/{gw['id']}/config")).status_code == 401
    assert (await client.get(f"/api/gateways/{gw['id']}/config", headers=gw_h("fcg_wrong"))).status_code == 401
    assert (await client.get(f"/api/gateways/{gw['id']}/config", headers=t.headers("admin"))).status_code == 401

    # heartbeat → online / degraded; offline after silence
    hb = await api(client, "POST", f"/api/gateways/{gw['id']}/heartbeat", gw_h(token),
                   json={"version": "edge-1.4.0", "uptime_s": 120, "buffer_depth": 0, "errors": [], "config_version": 1})
    assert hb == {"status": "online", "config_version": 2, "config_changed": True, "commands": []}   # Sprint 6: pulled commands
    g = await api(client, "GET", f"/api/gateways/{gw['id']}", h)
    assert g["status"] == "online" and g["version"] == "edge-1.4.0" and g["enrolled"] and g["tag_count"] == 3
    hb = await api(client, "POST", f"/api/gateways/{gw['id']}/heartbeat", gw_h(token),
                   json={"version": "edge-1.4.0", "uptime_s": 130, "buffer_depth": 5000, "errors": ["modbus-plc: timeout"]})
    assert hb["status"] == "degraded"
    ov = await api(client, "GET", "/api/overview", t.headers("operator"), params={"plant_id": p.plant["id"]})
    assert [(x["id"], x["status"]) for x in ov["gateways"]] == [(gw["id"], "degraded")]
    from src.db import AsyncSessionLocal
    from src.models import Gateway
    async with AsyncSessionLocal() as s:
        await s.execute(update(Gateway).where(Gateway.id == gw["id"]).values(
            last_seen_at=datetime.now(timezone.utc) - timedelta(minutes=10)))
        await s.commit()
    assert (await api(client, "GET", "/api/gateways/", h))[0]["status"] == "offline"

    # MQTT: gateways.conf line in mosquitto_passwd (sha512-pbkdf2) format, verifiable with the password
    mq = await api(client, "GET", f"/api/gateways/{gw['id']}/mqtt", h)
    user, tenant, hsh = mq["gateways_conf_line"].split(" ")
    assert user == enr["mqtt"]["username"] and tenant == t.id and mq["acl"] == f"topic readwrite factory/{t.id}/#"
    _, alg, it, salt, dk = hsh.split("$")
    assert alg == "7" and base64.b64decode(dk) == hashlib.pbkdf2_hmac(
        "sha512", enr["mqtt"]["password"].encode(), base64.b64decode(salt), int(it), 64)

    # re-enrollment rotates the token; revoke kills it
    enr2 = await _enroll(client, t, gw)
    assert (await client.get(f"/api/gateways/{gw['id']}/config", headers=gw_h(token))).status_code == 401
    assert (await client.get(f"/api/gateways/{gw['id']}/config", headers=gw_h(enr2["token"]))).status_code == 200
    await api(client, "POST", f"/api/gateways/{gw['id']}/revoke", h)
    assert (await client.get(f"/api/gateways/{gw['id']}/config", headers=gw_h(enr2["token"]))).status_code == 401

    # delete: the tags lose the assignment
    await api(client, "DELETE", f"/api/gateways/{gw['id']}", h, 204)
    assert (await api(client, "GET", f"/api/tags/{tags['run']['id']}", h))["gateway_id"] is None


async def test_enroll_code_expires_and_bad_codes_are_rate_limited(client):
    from src.api.routes import gateways as route
    from src.db import AsyncSessionLocal
    from src.models import Gateway
    t = await _tenant("GWX")
    p = await build_plant(client, t, "GWX")
    gw = await api(client, "POST", "/api/gateways/", t.headers("admin"), 201, json={"name": "g", "plant_id": p.plant["id"]})
    code = (await api(client, "POST", f"/api/gateways/{gw['id']}/enroll-token", t.headers("admin")))["code"]
    async with AsyncSessionLocal() as s:
        await s.execute(update(Gateway).where(Gateway.id == gw["id"]).values(
            enroll_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        await s.commit()
    await api(client, "POST", "/api/gateways/enroll", {}, 401, json={"code": code})
    route._enroll_failures.clear()
    for _ in range(route.ENROLL_MAX_FAILURES):
        await api(client, "POST", "/api/gateways/enroll", {}, 401, json={"code": "AAAA-BBBB-CCCC"})
    await api(client, "POST", "/api/gateways/enroll", {}, 429, json={"code": "AAAA-BBBB-CCCC"})
    route._enroll_failures.clear()


async def test_gateway_tenant_isolation(client):
    a, b = await _tenant("GIA"), await _tenant("GIB")
    pa, _, gwa, tags_a = await _setup(client, a, "GIA")
    pb = await build_plant(client, b, "GIB")
    hb = b.headers("admin")
    # B sees nothing of A and gets 404 on every admin route of A's gateway
    assert await api(client, "GET", "/api/gateways/", hb) == []
    for method, url, kw in [("GET", f"/api/gateways/{gwa['id']}", {}),
                            ("PATCH", f"/api/gateways/{gwa['id']}", {"json": {"name": "x"}}),
                            ("DELETE", f"/api/gateways/{gwa['id']}", {}),
                            ("POST", f"/api/gateways/{gwa['id']}/enroll-token", {}),
                            ("POST", f"/api/gateways/{gwa['id']}/revoke", {}),
                            ("PUT", f"/api/gateways/{gwa['id']}/tags", {"json": {"tags": []}}),
                            ("GET", f"/api/gateways/{gwa['id']}/config-preview", {}),
                            ("GET", f"/api/gateways/{gwa['id']}/mqtt", {})]:
        await api(client, method, url, hb, 404, **kw)
    # B cannot create a gateway on A's plant nor assign A's tags to its own gateway
    await api(client, "POST", "/api/gateways/", hb, 404, json={"name": "x", "plant_id": pa.plant["id"]})
    gwb = await api(client, "POST", "/api/gateways/", hb, 201, json={"name": "b", "plant_id": pb.plant["id"],
                                                                     "devices": DEVICES})
    await api(client, "PUT", f"/api/gateways/{gwb['id']}/tags", hb, 404, json={"tags": [{"tag_id": tags_a["run"]["id"]}]})
    # a tag of another plant of the same tenant is refused
    pa2 = await build_plant(client, a, "GIA2")
    other = await add_tag(client, a, pa2, "run2", "run_state", data_type="bool")
    await api(client, "PUT", f"/api/gateways/{gwa['id']}/tags", a.headers("admin"), 422,
              json={"tags": [{"tag_id": other["id"]}]})
    # A's device token does not open B's gateway; B's overview does not list A's gateway
    ea = await _enroll(client, a, gwa)
    assert (await client.get(f"/api/gateways/{gwb['id']}/config", headers=gw_h(ea["token"]))).status_code == 401
    assert (await client.post(f"/api/gateways/{gwb['id']}/heartbeat", headers=gw_h(ea["token"]),
                              json={})).status_code == 401
    ov = await api(client, "GET", "/api/overview", b.headers("operator"), params={"plant_id": pb.plant["id"]})
    assert [g["id"] for g in ov["gateways"]] == [gwb["id"]]


async def test_modbus_tags_in_the_provisioned_address_format_are_assigned_and_parsed_by_the_edge(client):
    """Tag.address as seed_dev.py / edge provision.py store it ('unit=2;hr=10:uint16', 'unit=1;di=1'): the
    assignment is accepted and the map carries integer register + unit/area/type, valid for the edge parser.
    Explicit edge fields win over the ones encoded in the address."""
    t = await _tenant("GWM")
    p = await build_plant(client, t, "GWM")
    h = t.headers("admin")
    mk = lambda name, addr, role, **kw: api(client, "POST", "/api/tags/", h, 201, json={  # noqa: E731
        "machine_id": p.machine["id"], "name": name, "protocol": "modbus_tcp", "address": addr,
        "semantic_role": role, **kw})
    cnt = await mk("ciclos", "unit=2;hr=10:uint32", "cycle_counter", counter_max=2**32 - 1)
    pulse = await mk("pulso", "unit=1;di=1", "cycle_pulse", data_type="bool")
    temp = await mk("temp", "unit=3;ir=7:float32", "analog")
    bare = await mk("run", "4", "run_state", data_type="bool")
    gw = await api(client, "POST", "/api/gateways/", h, 201, json={
        "name": "gw m", "plant_id": p.plant["id"], "devices": DEVICES})
    out = await api(client, "PUT", f"/api/gateways/{gw['id']}/tags", h, json={"tags": [
        {"tag_id": cnt["id"]}, {"tag_id": pulse["id"]}, {"tag_id": temp["id"], "unit_id": 9},
        {"tag_id": bare["id"], "register_type": "coil"}]})
    assert out["assigned"] == 4 and out["warnings"] == []
    prev = await api(client, "GET", f"/api/gateways/{gw['id']}/config-preview", h)
    by_id = {x["tag_id"]: x for x in prev["plant_map"]["tags"]}
    view = lambda x: (x["source"], x["address"], x["unit_id"], x["register_type"], x["data_type_plc"])  # noqa: E731
    assert view(by_id[cnt["id"]]) == ("modbus-plc", 10, 2, "holding", "uint32")
    assert view(by_id[pulse["id"]]) == ("modbus-plc", 1, 1, "discrete", "uint16")
    assert view(by_id[temp["id"]]) == ("modbus-plc", 7, 9, "input", "float32")
    assert view(by_id[bare["id"]]) == ("modbus-plc", 4, 1, "coil", "uint16")
    # only the explicit fields are stored on the tag (the address keeps its own unit/area/type)
    from src.db import AsyncSessionLocal
    from src.models import Tag
    async with AsyncSessionLocal() as s:
        assert (await s.get(Tag, cnt["id"])).edge == {"source": "modbus-plc"}
        assert (await s.get(Tag, temp["id"])).edge == {"source": "modbus-plc", "unit_id": 9}
    sys.path.insert(0, str(ROOT))
    from edge.src.plant_map import parse_plant_map
    parsed = {tg.tag_id: tg for tg in parse_plant_map(prev["plant_map"], env={}).tags}
    assert set(parsed) == {cnt["id"], pulse["id"], temp["id"], bare["id"]}
    assert (parsed[cnt["id"]].address, parsed[cnt["id"]].unit_id, parsed[cnt["id"]].register_type) == (10, 2, "holding")
    assert parsed[pulse["id"]].register_type == "discrete"
    # a malformed address is still refused (422) and a pulse on a holding register too
    bad = await mk("ruim", "unit=1;xx=3", "analog")
    await api(client, "PUT", f"/api/gateways/{gw['id']}/tags", h, 422, json={"tags": [{"tag_id": bad["id"]}]})
    hp = await mk("pulso hr", "unit=1;hr=3", "cycle_pulse", data_type="bool")
    await api(client, "PUT", f"/api/gateways/{gw['id']}/tags", h, 422, json={"tags": [{"tag_id": hp["id"]}]})


async def test_revoked_gateway_loses_its_mqtt_credential_and_its_readings_are_discarded(client):
    import json as _json

    from src.services import mqtt_subscriber
    from src.services.predictive import features as fx
    t = await _tenant("GWR")
    p, pos, gw, tags = await _setup(client, t, "GWR")
    h = t.headers("admin")
    await api(client, "PUT", f"/api/gateways/{gw['id']}/tags", h, json={"tags": [{"tag_id": tags["run"]["id"]}]})
    enr = await _enroll(client, t, gw)
    assert (await api(client, "GET", f"/api/gateways/{gw['id']}/mqtt", h))["revoked"] is False
    # an enrolled gateway cannot be deleted before it is revoked (its MQTT credential is still valid)
    await api(client, "DELETE", f"/api/gateways/{gw['id']}", h, 409)
    topic = f"factory/{t.id}/sensors/{tags['run']['id']}"

    async def publish(at: datetime) -> None:
        await mqtt_subscriber._handle_message(topic, _json.dumps(
            {"value": 1, "ts": at.isoformat().replace("+00:00", "Z")}).encode())

    t1 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=2)
    await publish(t1)
    rv = await api(client, "POST", f"/api/gateways/{gw['id']}/revoke", h)
    assert rv["enrolled"] is False and rv["mqtt_revoked"] is True
    assert enr["mqtt"]["username"] in rv["mqtt_action"] and "gateways.conf" in rv["mqtt_action"]
    mq = await api(client, "GET", f"/api/gateways/{gw['id']}/mqtt", h)
    assert mq["revoked"] is True and mq["gateways_conf_line"] is None and "mqtt-gateway-remove" in mq["action"]
    # the device keeps publishing with the old broker credential: the server discards it
    await publish(t1 + timedelta(seconds=1))
    pts = await fx.influx_points(t.id, [tags["run"]["id"]], t1 - timedelta(seconds=1), t1 + timedelta(seconds=5))
    assert [x for x, _ in pts[tags["run"]["id"]]] == [t1]
    # enrolling again restores it; the gateway can then be revoked and deleted
    await _enroll(client, t, gw)
    g = await api(client, "GET", f"/api/gateways/{gw['id']}", h)
    assert g["mqtt_revoked"] is False and g["mqtt_action"] is None
    await publish(t1 + timedelta(seconds=2))
    pts = await fx.influx_points(t.id, [tags["run"]["id"]], t1 - timedelta(seconds=1), t1 + timedelta(seconds=5))
    assert len(pts[tags["run"]["id"]]) == 2
    await api(client, "POST", f"/api/gateways/{gw['id']}/revoke", h)
    await api(client, "DELETE", f"/api/gateways/{gw['id']}", h, 204)
