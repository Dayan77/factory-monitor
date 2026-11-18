"""C3 — line templates of the onboarding wizard (services/onboarding/line_templates.py): the ueaPowerDriver template
matches edge/config/ueapd.example.json (no drift), apply is idempotent (twice = no duplicates), with a gateway the
ueapd devices + tag assignment produce a config the edge parses, RBAC, tenant isolation and tenant.max_machines."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from conftest import _make_tenant
from usage_helpers import api

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "edge" / "config" / "ueapd.example.json"
KEY = "ueapd-led-driver"
URL = f"/api/onboarding/line-templates/{KEY}/apply"


def edge_plant_map():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from edge.src import plant_map
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"edge not importable: {exc}")
    return plant_map


def test_template_matches_the_edge_example():
    pm = edge_plant_map()
    from src.services.onboarding.line_templates import DEVICE_FIELDS, get_template
    tpl = get_template(KEY)
    ex = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    (line,) = ex["assets"]["lines"]
    assert tpl["line_name"] == line["name"] and tpl["output_machine"] == line["output_machine"]
    assert tpl["machines"] == line["machines"]          # names, model, serial, ideal cycle, fault maps, positions
    tag_fields = ("key", "name", "machine", "position", "semantic_role", "data_type", "unit", "counter_max",
                  "max_rate_per_s", "source", "address", "poll_interval_ms")
    assert [{k: t[k] for k in tag_fields} for t in ex["tags"]] == [{k: t[k] for k in tag_fields} for t in tpl["tags"]]
    assert {t["line"] for t in ex["tags"]} == {line["name"]}
    # sources: the example's values with its ${VAR:-default} defaults
    sources = {s["id"]: s for s in pm.expand_env(ex["sources"], {})}
    assert [d["id"] for d in tpl["devices"]] == list(sources)
    for d in tpl["devices"]:
        s = sources[d["id"]]
        assert d["default_base_url"] == s["base_url"]
        for k in DEVICE_FIELDS:
            want = s[k]
            assert (float(want) if k == "max_clock_skew_s" else want) == d[k], (d["id"], k)
    # every selector valid for its role (server replica), every counter_tag points to a cycle_counter of the position
    from src.services.onboarding.edge_rules import ueapd_selector_error
    assert all(ueapd_selector_error(t["address"], t["semantic_role"]) is None for t in tpl["tags"])
    by_key = {t["key"]: t for t in tpl["tags"]}
    for m in tpl["machines"]:
        for p in m["positions"]:
            t = by_key[p["counter_tag"]]
            assert (t["machine"], t["position"], t["semantic_role"]) == (m["name"], p["name"], "cycle_counter")


async def _tenant(label, max_machines: int | None = None):
    from src.db import AsyncSessionLocal
    from src.models import Tenant
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, label)
        if max_machines is not None:
            await s.execute(update(Tenant).where(Tenant.id == t.id).values(max_machines=max_machines))
        await s.commit()
    return t


async def _counts(tenant_id: str) -> dict:
    from src.db import AsyncSessionLocal
    from src.models import Line, Machine, Position, Tag
    async with AsyncSessionLocal() as s:
        return {name: (await s.execute(select(func.count()).select_from(model).where(model.tenant_id == tenant_id)))
                .scalar_one() for name, model in (("lines", Line), ("machines", Machine), ("positions", Position),
                                                  ("tags", Tag))}


async def test_list_templates_and_rbac(client):
    t = await _tenant("LTL")
    out = await api(client, "GET", "/api/onboarding/line-templates", t.headers("admin"))
    (tpl,) = [x for x in out if x["key"] == KEY]
    assert tpl["name"] == "Linha ueaPowerDriver (M1 Hipot + M2 ATS2)" and tpl["description"]
    assert tpl["machines"] == ["M1 Hipot", "M2 ATS2"] and tpl["line_name"] == "Linha LED Driver"
    assert tpl["devices"] == [
        {"id": "ueapd-m1", "label": "Box M1 (hipot)", "default_base_url": "http://192.168.1.15:8003"},
        {"id": "ueapd-m2", "label": "Box M2 (ATS2)", "default_base_url": "http://192.168.1.16:8003"}]
    assert tpl["tag_count"] == 18
    await api(client, "GET", "/api/onboarding/line-templates", t.headers("manager"), 403)
    await api(client, "POST", URL, t.headers("manager"), 403, json={"plant_id": "x"})


async def test_apply_twice_without_gateway_is_idempotent(client):
    t = await _tenant("LTA")
    h = t.headers("admin")
    plant = await api(client, "POST", "/api/plants/", h, 201, json={"name": "Planta LTA"})
    a = await api(client, "POST", URL, h, json={"plant_id": plant["id"]})
    assert a["line_name"] == "Linha LED Driver" and a["line_created"] is True and a["gateway"] is None
    assert [(m["name"], m["created"]) for m in a["machines"]] == [("M1 Hipot", True), ("M2 ATS2", True)]
    assert (a["tags_created"], a["tags_updated"], a["tags_unchanged"]) == (18, 0, 0) and a["warnings"] == []
    after_first = await _counts(t.id)
    assert after_first == {"lines": 1, "machines": 2, "positions": 8, "tags": 18}

    b = await api(client, "POST", URL, h, json={"plant_id": plant["id"], "line_name": " linha led driver "})
    assert b["line_id"] == a["line_id"] and b["line_created"] is False
    assert [m["id"] for m in b["machines"]] == [m["id"] for m in a["machines"]]
    assert all(not m["created"] for m in b["machines"])
    assert (b["tags_created"], b["tags_updated"], b["tags_unchanged"]) == (0, 0, 18) and b["warnings"] == []
    assert await _counts(t.id) == after_first

    # structure as in the template: output machine, fault map, positions counters, tags http + selector
    m1, m2 = a["machines"]
    line = await api(client, "GET", f"/api/lines/{a['line_id']}", h)
    assert line["output_machine_id"] == m2["id"]
    mach = await api(client, "GET", f"/api/machines/{m1['id']}", h)
    assert mach["fault_code_map"]["401"] == "EQP-03" and mach["ideal_cycle_time_s"] == 18.0
    assert mach["serial_number"] == "UEAPD-M1"
    tags = await api(client, "GET", "/api/tags/", h, params={"machine_id": m1["id"]})
    assert len(tags) == 9 and {x["protocol"] for x in tags} == {"http"}
    by_name = {x["name"]: x for x in tags}
    assert by_name["Ciclos Hipot 1"]["address"] == "cycles:hipot_1"
    assert by_name["Peças boas (veredito OK)"]["semantic_role"] == "part_counter_good"
    pos = await api(client, "GET", "/api/positions/", h, params={"machine_id": m1["id"]})
    assert {p["name"]: p["counter_tag_id"] for p in pos}["Hipot 1"] == by_name["Ciclos Hipot 1"]["id"]
    assert all(p["counter_tag_id"] for p in pos)

    # a tag edited by hand is put back by the next apply (counted as updated)
    await api(client, "PATCH", f"/api/tags/{by_name['Ciclos Hipot 1']['id']}", h, json={"address": "cycles:other"})
    c = await api(client, "POST", URL, h, json={"plant_id": plant["id"]})
    assert (c["tags_created"], c["tags_updated"], c["tags_unchanged"]) == (0, 1, 17)


async def test_template_counters_do_not_ask_for_counter_max(client):
    """ueapd counters are totals the gateway accumulates and persists: they never wrap, so readiness does not warn
    about counter_max for them (it still does for a PLC counter, which wraps at its register size)."""
    t = await _tenant("LTC")
    h = t.headers("admin")
    plant = await api(client, "POST", "/api/plants/", h, 201, json={"name": "Planta LTC"})
    a = await api(client, "POST", URL, h, json={"plant_id": plant["id"]})
    rd = await api(client, "GET", "/api/onboarding/readiness", h, params={"plant_id": plant["id"]})
    chk = {c["id"]: c for c in rd["checks"]}["counter_max_set"]
    assert chk["ok"] and chk["items"] == [], chk

    # a modbus counter on the same machine without counter_max is still flagged
    m2 = a["machines"][1]
    await api(client, "POST", "/api/tags/", h, 201, json={
        "machine_id": m2["id"], "name": "Contador CLP", "protocol": "modbus_tcp", "address": "40001",
        "data_type": "int", "semantic_role": "cycle_counter"})
    rd = await api(client, "GET", "/api/onboarding/readiness", h, params={"plant_id": plant["id"]})
    chk = {c["id"]: c for c in rd["checks"]}["counter_max_set"]
    assert not chk["ok"] and chk["items"] == ["M2 ATS2 / Contador CLP"], chk


async def test_apply_with_gateway_configures_the_devices_and_the_map(client):
    pm = edge_plant_map()
    from edge.src.remote_config import _extract_map, _validate
    t = await _tenant("LTG")
    h = t.headers("admin")
    plant = await api(client, "POST", "/api/plants/", h, 201, json={"name": "Planta LTG"})
    gw = await api(client, "POST", "/api/gateways/", h, 201, json={"name": "gw ledd", "plant_id": plant["id"], "devices": [
        {"id": "modbus-plc", "protocol": "modbus", "host": "10.0.0.5"},
        {"id": "ueapd-m2", "protocol": "ueapd", "base_url": "http://10.9.9.9:8003", "poll_interval_s": 3}]})
    await api(client, "POST", URL, h, 422, json={"plant_id": plant["id"], "gateway_id": gw["id"],
                                                 "devices": {"ueapd-m1": {"base_url": "http://u:p@10.0.0.15:8003"}}})
    await api(client, "POST", URL, h, 422, json={"plant_id": plant["id"], "gateway_id": gw["id"],
                                                 "devices": {"ueapd-m9": {"base_url": "http://10.0.0.15:8003"}}})
    assert await _counts(t.id) == {"lines": 0, "machines": 0, "positions": 0, "tags": 0}       # rolled back

    out = await api(client, "POST", URL, h, json={"plant_id": plant["id"], "gateway_id": gw["id"], "line_name": "LED 1",
                                                  "devices": {"ueapd-m1": {"base_url": "http://10.0.0.15:8003"}}})
    g = out["gateway"]
    assert g["id"] == gw["id"] and g["tags_assigned"] == 18 and g["config_version"] >= 1
    devs = {d["id"]: d for d in g["devices"]}
    assert list(devs) == ["modbus-plc", "ueapd-m2", "ueapd-m1"]                      # merged by id, order kept
    assert devs["ueapd-m1"]["base_url"] == "http://10.0.0.15:8003" and devs["ueapd-m1"]["line"] == "hipot"
    assert devs["ueapd-m2"]["base_url"] == "http://10.9.9.9:8003"                    # current base_url kept
    assert devs["ueapd-m2"]["poll_interval_s"] == 2 and devs["ueapd-m2"]["fault_codes"]["ERRO_CLAMP"] == 401
    assert out["warnings"] == []

    prev = await api(client, "GET", f"/api/gateways/{gw['id']}/config-preview", h)
    assert prev["warnings"] == [] and prev["config_version"] == g["config_version"]
    pmap = prev["plant_map"]
    assert {s["id"] for s in pmap["sources"] if s["protocol"] == "ueapd"} == {"ueapd-m1", "ueapd-m2"}
    tpl = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    want = {t_["key"]: (t_["source"], t_["address"], t_["semantic_role"]) for t_ in tpl["tags"]}
    assert {x["key"]: (x["source"], x["address"], x["semantic_role"]) for x in pmap["tags"]} == want
    strict = pm.parse_plant_map(pmap, env={})
    assert len(strict.tags) == 18 and strict.skipped == []
    remote = _validate(_extract_map(prev)[0], gw["id"], env={})
    assert len(remote.tags) == 18 and getattr(remote, "rejected", []) == []
    assert {k: v.base_url for k, v in remote.sources.items() if v.protocol == "ueapd"} == {
        "ueapd-m1": "http://10.0.0.15:8003", "ueapd-m2": "http://10.9.9.9:8003"}

    # apply again with the same input: nothing changes, config_version stays
    again = await api(client, "POST", URL, h, json={"plant_id": plant["id"], "gateway_id": gw["id"], "line_name": "LED 1",
                                                    "devices": {"ueapd-m1": {"base_url": "http://10.0.0.15:8003"}}})
    assert again["gateway"]["config_version"] == g["config_version"] and again["tags_created"] == 0
    assert again["gateway"]["devices"] == g["devices"]
    assert await _counts(t.id) == {"lines": 1, "machines": 2, "positions": 8, "tags": 18}
    # new box URL → map changes → config_version bumps
    moved = await api(client, "POST", URL, h, json={"plant_id": plant["id"], "gateway_id": gw["id"], "line_name": "LED 1",
                                                    "devices": {"ueapd-m2": {"base_url": "http://10.0.0.16:8003"}}})
    assert moved["gateway"]["config_version"] == g["config_version"] + 1
    # a device id taken by another protocol → 422
    gw2 = await api(client, "POST", "/api/gateways/", h, 201, json={"name": "gw2", "plant_id": plant["id"], "devices": [
        {"id": "ueapd-m1", "protocol": "modbus", "host": "10.0.0.7"}]})
    r = await client.post(URL, headers=h, json={"plant_id": plant["id"], "gateway_id": gw2["id"], "line_name": "LED 1"})
    assert r.status_code == 422 and "ueapd-m1" in r.text


async def test_apply_tenant_isolation_and_limits(client):
    a, b = await _tenant("LTIA"), await _tenant("LTIB")
    ha, hb = a.headers("admin"), b.headers("admin")
    pa = await api(client, "POST", "/api/plants/", ha, 201, json={"name": "Planta A"})
    pa2 = await api(client, "POST", "/api/plants/", ha, 201, json={"name": "Planta A2"})
    pb = await api(client, "POST", "/api/plants/", hb, 201, json={"name": "Planta B"})
    gwa = await api(client, "POST", "/api/gateways/", ha, 201, json={"name": "gw a", "plant_id": pa["id"]})
    # B cannot apply on A's plant, nor attach A's gateway to its own plant (404, nothing written)
    await api(client, "POST", URL, hb, 404, json={"plant_id": pa["id"]})
    await api(client, "POST", URL, hb, 404, json={"plant_id": pb["id"], "gateway_id": gwa["id"]})
    await api(client, "POST", URL, hb, 404, json={"plant_id": "not-a-uuid"})
    await api(client, "POST", "/api/onboarding/line-templates/nope/apply", hb, 404, json={"plant_id": pb["id"]})
    assert await _counts(a.id) == {"lines": 0, "machines": 0, "positions": 0, "tags": 0}
    assert await _counts(b.id) == {"lines": 0, "machines": 0, "positions": 0, "tags": 0}
    assert (await api(client, "GET", f"/api/gateways/{gwa['id']}", ha))["devices"] == []
    # gateway of another plant of the same tenant → 422
    await api(client, "POST", URL, ha, 422, json={"plant_id": pa2["id"], "gateway_id": gwa["id"]})
    # A applies; B sees none of it
    out = await api(client, "POST", URL, ha, json={"plant_id": pa["id"], "gateway_id": gwa["id"]})
    assert await api(client, "GET", "/api/lines/", hb) == []
    await api(client, "GET", f"/api/machines/{out['machines'][0]['id']}", hb, 404)
    assert await api(client, "GET", "/api/tags/", hb) == []
    # B applying on its own plant creates its own copies (no cross-tenant matching by name)
    ob = await api(client, "POST", URL, hb, json={"plant_id": pb["id"]})
    assert ob["tags_created"] == 18 and {m["id"] for m in ob["machines"]}.isdisjoint({m["id"] for m in out["machines"]})

    # machine names are unique in the plant: the same template on another line of the plant → 409
    r = await client.post(URL, headers=ha, json={"plant_id": pa["id"], "line_name": "Outra linha"})
    assert r.status_code == 409 and "M1 Hipot" in r.text

    # tenant.max_machines: 1 machine left, the template needs 2 → 422 and nothing written
    c = await _tenant("LTIC", max_machines=3)
    hc = c.headers("admin")
    pc = await api(client, "POST", "/api/plants/", hc, 201, json={"name": "Planta C"})
    ln = await api(client, "POST", "/api/lines/", hc, 201, json={"name": "L", "plant_id": pc["id"]})
    await api(client, "POST", "/api/machines/", hc, 201, json={"line_id": ln["id"], "name": "X1"})
    await api(client, "POST", "/api/machines/", hc, 201, json={"line_id": ln["id"], "name": "X2"})
    r = await client.post(URL, headers=hc, json={"plant_id": pc["id"]})
    assert r.status_code == 422 and "Limite de máquinas" in r.text
    assert await _counts(c.id) == {"lines": 1, "machines": 2, "positions": 0, "tags": 0}


async def test_template_keys_survive_a_later_manual_assignment(client):
    """Applied without a gateway, then assigned by PUT /api/gateways/{id}/tags without `key`: the map keeps the
    template keys (ledd.m1.parts_good, ...) — the edge keys its counter state by them."""
    pm = edge_plant_map()
    t = await _tenant("LTK")
    h = t.headers("admin")
    plant = await api(client, "POST", "/api/plants/", h, 201, json={"name": "Planta LTK"})
    out = await api(client, "POST", URL, h, json={"plant_id": plant["id"]})
    gw = await api(client, "POST", "/api/gateways/", h, 201, json={"name": "gw", "plant_id": plant["id"], "devices": [
        {"id": "ueapd-m1", "protocol": "ueapd", "base_url": "http://10.0.0.15:8003"},
        {"id": "ueapd-m2", "protocol": "ueapd", "base_url": "http://10.0.0.16:8003"}]})
    ids = [x["id"] for m in out["machines"] for x in await api(client, "GET", "/api/tags/", h,
                                                                params={"machine_id": m["id"]})]
    await api(client, "PUT", f"/api/gateways/{gw['id']}/tags", h, json={"tags": [{"tag_id": i} for i in ids]})
    prev = await api(client, "GET", f"/api/gateways/{gw['id']}/config-preview", h)
    ex = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert {x["key"]: x["source"] for x in prev["plant_map"]["tags"]} == {t_["key"]: t_["source"] for t_ in ex["tags"]}
    assert len(pm.parse_plant_map(prev["plant_map"], env={}).tags) == 18


async def test_reapply_keeps_custom_keys_fault_codes_and_foreign_tags(client):
    """S4: re-applying never breaks counter continuity nor user customizations:
    - an existing tags.edge.key is kept (warning when it differs from the template key);
    - fault_code_map only gains missing codes, a code mapped differently is kept (warning);
    - a same-name tag with another protocol, read by another gateway, is not converted (warning, skipped)."""
    from src.db import AsyncSessionLocal
    from src.models import Tag
    t = await _tenant("LTS")
    h = t.headers("admin")
    plant = await api(client, "POST", "/api/plants/", h, 201, json={"name": "Planta LTS"})
    first = await api(client, "POST", URL, h, json={"plant_id": plant["id"]})
    m1, m2 = first["machines"]
    tags = {x["name"]: x for x in await api(client, "GET", "/api/tags/", h, params={"machine_id": m1["id"]})}
    good_id = tags["Peças boas (veredito OK)"]["id"]
    async with AsyncSessionLocal() as s:
        tg = await s.get(Tag, good_id)
        tg.edge = {"source": "ueapd-m1", "key": "custom.m1.good"}
        await s.commit()
    # user remaps a template code and adds one of their own
    mach = await api(client, "GET", f"/api/machines/{m1['id']}", h)
    fmap = {**mach["fault_code_map"], "401": "EQP-09", "999": "EQP-02"}
    fmap.pop("501")
    await api(client, "PATCH", f"/api/machines/{m1['id']}", h, json={"fault_code_map": fmap})
    # M2's run tag becomes a Modbus tag read by another gateway
    other = await api(client, "POST", "/api/gateways/", h, 201, json={"name": "gw modbus", "plant_id": plant["id"],
                                                                     "devices": [{"id": "plc", "protocol": "modbus",
                                                                                  "host": "10.0.0.9"}]})
    run2 = next(x for x in await api(client, "GET", "/api/tags/", h, params={"machine_id": m2["id"]})
                if x["semantic_role"] == "run_state")
    from src.models.tag import ProtocolType
    async with AsyncSessionLocal() as s:                  # protocol is not editable through the API
        tg = await s.get(Tag, run2["id"])
        tg.protocol, tg.address, tg.edge = ProtocolType.modbus_tcp, "unit=1;co=3", None
        await s.commit()
    await api(client, "PUT", f"/api/gateways/{other['id']}/tags", h, json={"tags": [{"tag_id": run2["id"]}]})

    gw = await api(client, "POST", "/api/gateways/", h, 201, json={"name": "gw ledd", "plant_id": plant["id"]})
    out = await api(client, "POST", URL, h, json={"plant_id": plant["id"], "gateway_id": gw["id"]})
    w = "\n".join(out["warnings"])
    assert "'custom.m1.good' mantida" in w
    assert "código de falha 401 mantido como EQP-09" in w
    assert "não convertida" in w and out["tags_skipped"] == 1 and out["tags_created"] == 0
    assert out["gateway"]["tags_assigned"] == 17
    mach = await api(client, "GET", f"/api/machines/{m1['id']}", h)
    assert mach["fault_code_map"]["401"] == "EQP-09" and mach["fault_code_map"]["999"] == "EQP-02"
    assert mach["fault_code_map"]["501"] == "EQP-05"                                     # missing code added back
    r2 = await api(client, "GET", f"/api/tags/{run2['id']}", h)
    assert (r2["protocol"], r2["gateway_id"]) == ("modbus_tcp", other["id"])            # untouched
    prev = await api(client, "GET", f"/api/gateways/{gw['id']}/config-preview", h)
    keys = {x["tag_id"]: x["key"] for x in prev["plant_map"]["tags"]}
    assert keys[good_id] == "custom.m1.good" and len(keys) == 17 and prev["warnings"] == []
    # and the Modbus gateway still maps its tag
    prev_o = await api(client, "GET", f"/api/gateways/{other['id']}/config-preview", h)
    assert [x["tag_id"] for x in prev_o["plant_map"]["tags"]] == [run2["id"]]
