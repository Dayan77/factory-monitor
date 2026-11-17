"""C2 — ueapd source (box ueaPowerDriver) in the remote gateway config.

The server replicates edge/src/plant_map.py's selector rule (the server image does not ship the edge): the replica
must give the SAME result / message as the edge for a shared vector of selectors and selector × role pairs, and
every device the server accepts must be accepted by the edge parser. End to end: a gateway with ueapd devices and
http tags produces a map the edge parses (strict) and runs like a local map; an http tag without a ueapd device
stays out of the map with a warning; the tag CSV import accepts ueapd rows."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from conftest import _make_tenant
from usage_helpers import api, build_plant

ROOT = Path(__file__).resolve().parents[2]

SELECTORS = [
    "state:run", "state:fault", "state:cadence", "passes:good", "passes:scrap", "passes:total",
    " state:run ", "cycles:hipot_1", "cycles:laser_1:P1", "cycles:ats2:p2", "cycles:ats2:P99", "cycles:x:P0",
    "cycles:x:P100", "cycles:x:Q1", "cycles:", "cycles", "cycles:hi-pot", "cycles:a b", "measure:hipot_1:kv",
    "measure:hipot_1:result.kv", "measure:hipot_1:", "measure::kv", "measure:hipot_1:..", "measure",
    "state:other", "passes:all", "foo", "", "   ", None, 5, ["state:run"], "STATE:RUN", "state:run:x",
]
ROLES = ["run_state", "fault_state", "analog", "speed", "current", "cycle_counter", "part_counter_good",
         "part_counter_scrap", "part_counter_total", "cycle_pulse", "stroke_time"]


def edge_plant_map():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from edge.src import plant_map
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"edge not importable: {exc}")
    return plant_map


def _outcome(fn, arg):
    try:
        return ("ok", fn(arg))
    except ValueError as e:
        return ("err", str(e))


def test_selector_replica_matches_the_edge():
    pm = edge_plant_map()
    from src.services.onboarding import edge_rules as er
    assert er.UEAPD_SELECTOR_ROLES == pm.UEAPD_SELECTOR_ROLES and er.UEAPD_BACKFILL == pm.UEAPD_BACKFILL
    n_ok = n_err = 0
    for sel in SELECTORS:
        server, edge = _outcome(er.parse_ueapd_selector, sel), _outcome(pm.parse_ueapd_selector, sel)
        assert server == edge, sel
        n_ok += server[0] == "ok"
        n_err += server[0] == "err"
    assert n_ok >= 10 and n_err >= 10


def test_tag_error_matches_the_edge_parser_for_selector_and_role():
    pm = edge_plant_map()
    from src.services.onboarding.edge_rules import tag_error
    src = [{"id": "ueapd-m1", "protocol": "ueapd", "base_url": "http://10.0.0.15:8003"}]
    n_err = 0
    for sel in SELECTORS:
        for role in ROLES:
            entry = {"key": "k", "tag_id": "x", "source": "ueapd-m1", "semantic_role": role, "address": sel}
            try:
                pm.parse_plant_map({"version": 1, "gateway_id": "gw", "sources": src, "tags": [entry]}, env={})
                edge_msg = None
            except pm.PlantMapError as e:
                edge_msg = str(e)
            assert tag_error(dict(entry), {"ueapd-m1": "ueapd"}, set()) == edge_msg, (sel, role)
            n_err += edge_msg is not None
    assert n_err > len(SELECTORS)


UEAPD_OK = [
    {"id": "u1", "protocol": "ueapd", "base_url": "http://192.168.1.15:8003"},
    {"id": "u2", "protocol": "ueapd", "base_url": "https://box.local/", "line": "hipot", "poll_interval_s": 0.2,
     "timeout_s": 120, "timestamps": "source", "max_clock_skew_s": "off", "backfill": "latest_day",
     "default_fault_code": 399, "fault_codes": {"ERRO_CLAMP": 401}},
    {"id": "u3", "protocol": "ueapd", "base_url": "${UEAPD_M1_URL:-http://192.168.1.15:8003}", "max_clock_skew_s": 2},
    {"id": "u4", "protocol": "ueapd", "base_url": "${UEAPD_URL}"},
    {"id": "u5", "protocol": "ueapd", "base_url": "http://${BOX_HOST}:8003", "backfill": "all"},
    {"id": "u6", "protocol": "ueapd", "base_url": "http://[fe80::1]:8003", "poll_interval_s": "${POLL:-2}"},
]
UEAPD_BAD = [
    {"id": "b", "protocol": "ueapd"},
    {"id": "b", "protocol": "ueapd", "base_url": "ftp://h:21"},
    {"id": "b", "protocol": "ueapd", "base_url": "192.168.1.15:8003"},
    {"id": "b", "protocol": "ueapd", "base_url": "http://user:pw@h:8003"},
    {"id": "b", "protocol": "ueapd", "base_url": "http://h:8003/api/v1"},
    {"id": "b", "protocol": "ueapd", "base_url": "http://h:99999"},
    {"id": "b", "protocol": "ueapd", "base_url": "http://h:8003?x=1"},
    {"id": "b", "protocol": "ueapd", "base_url": "http://${HOST:-u:p@h}:8003"},
    {"id": "b", "protocol": "ueapd", "base_url": "http://h", "poll_interval_s": 0.1},
    {"id": "b", "protocol": "ueapd", "base_url": "http://h", "timeout_s": 500},
    {"id": "b", "protocol": "ueapd", "base_url": "http://h", "backfill": "everything"},
    {"id": "b", "protocol": "ueapd", "base_url": "http://h", "default_fault_code": 0},
    {"id": "b", "protocol": "ueapd", "base_url": "http://h", "fault_codes": {"X": "101"}},
    {"id": "b", "protocol": "ueapd", "base_url": "http://h", "fault_codes": {"X": True}},
    {"id": "b", "protocol": "ueapd", "base_url": "http://h", "timestamps": "plc"},
    {"id": "b", "protocol": "ueapd", "base_url": "http://h", "max_clock_skew_s": -1},
    {"id": "b", "protocol": "ueapd", "base_url": "http://h", "host": "h"},           # Modbus field on ueapd
    {"id": "b", "protocol": "modbus", "host": "h", "base_url": "http://h"},          # ueapd field on Modbus
]


def test_devices_accepted_by_the_server_are_accepted_by_the_edge():
    pm = edge_plant_map()
    from src.services import gateways as gs
    env = {"UEAPD_URL": "http://10.1.1.1:8003", "BOX_HOST": "10.1.1.2"}
    out = gs.validate_devices(UEAPD_OK)
    assert [d["id"] for d in out] == ["u1", "u2", "u3", "u4", "u5", "u6"]
    parsed = pm.parse_plant_map({"version": 1, "gateway_id": "gw", "sources": out, "tags": []}, env=env)
    assert parsed.sources["u1"].base_url == "http://192.168.1.15:8003"
    assert parsed.sources["u2"].backfill == "latest_day" and parsed.sources["u2"].fault_codes == {"ERRO_CLAMP": 401}
    assert parsed.sources["u4"].base_url == "http://10.1.1.1:8003"
    for bad in UEAPD_BAD:
        with pytest.raises(gs.ConfigError):
            gs.validate_devices([bad])


async def _tenant(label):
    from src.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, label)
        await s.commit()
    return t


async def test_gateway_map_with_ueapd_sources_is_parsed_by_the_edge(client):
    pm = edge_plant_map()
    from edge.src.remote_config import _extract_map, _validate
    t = await _tenant("UEA")
    p = await build_plant(client, t, "UEA")
    h = t.headers("admin")
    mk = lambda name, addr, role, **kw: api(client, "POST", "/api/tags/", h, 201, json={  # noqa: E731
        "machine_id": p.machine["id"], "name": name, "protocol": "http", "address": addr, "semantic_role": role, **kw})
    run = await mk("run", "state:run", "run_state", data_type="bool")
    good = await mk("good", "passes:good", "part_counter_good", data_type="int")
    cyc = await mk("cyc", "cycles:hipot_1:P2", "cycle_counter", data_type="int")
    kv = await mk("kv", "measure:hipot_1:result.kv", "analog")
    bad_role = await mk("bad", "state:run", "analog")
    devices = [{"id": "ueapd-m1", "protocol": "ueapd", "base_url": "http://192.168.1.15:8003", "line": "hipot",
                "poll_interval_s": 2, "timeout_s": 5, "timestamps": "gateway", "max_clock_skew_s": 2,
                "backfill": "none", "default_fault_code": 301, "fault_codes": {"ERRO_TRANSFERENCIA": 101}},
               {"id": "ueapd-m2", "protocol": "ueapd", "base_url": "http://192.168.1.16:8003/"},
               {"id": "modbus-plc", "protocol": "modbus", "host": "10.0.0.5"}]
    gw = await api(client, "POST", "/api/gateways/", h, 201, json={"name": "gw u", "plant_id": p.plant["id"],
                                                                 "devices": devices})
    assert gw["devices"] == devices
    # selector incompatible with the role / Modbus fields on a ueapd tag / http tag on a Modbus device → 422
    await api(client, "PUT", f"/api/gateways/{gw['id']}/tags", h, 422, json={"tags": [{"tag_id": bad_role["id"]}]})
    await api(client, "PUT", f"/api/gateways/{gw['id']}/tags", h, 422,
              json={"tags": [{"tag_id": run["id"], "unit_id": 2}]})
    await api(client, "PUT", f"/api/gateways/{gw['id']}/tags", h, 422,
              json={"tags": [{"tag_id": run["id"], "source": "modbus-plc"}]})
    out = await api(client, "PUT", f"/api/gateways/{gw['id']}/tags", h, json={"tags": [
        {"tag_id": run["id"]}, {"tag_id": good["id"], "source": "ueapd-m1"},
        {"tag_id": cyc["id"], "source": "ueapd-m2", "key": "ledd.m2.cyc"}, {"tag_id": kv["id"]}]})
    assert out["warnings"] == []
    prev = await api(client, "GET", f"/api/gateways/{gw['id']}/config-preview", h)
    pmap = prev["plant_map"]
    assert pmap["sources"] == devices
    by_id = {x["tag_id"]: x for x in pmap["tags"]}
    assert {k: (v["source"], v["address"]) for k, v in by_id.items()} == {
        run["id"]: ("ueapd-m1", "state:run"), good["id"]: ("ueapd-m1", "passes:good"),
        cyc["id"]: ("ueapd-m2", "cycles:hipot_1:P2"), kv["id"]: ("ueapd-m1", "measure:hipot_1:result.kv")}
    assert not any(k in by_id[run["id"]] for k in ("unit_id", "register_type", "data_type_plc", "word_order"))
    # the edge parses it strictly (= a local PLANT_MAP_PATH) and through the remote-config path, nothing rejected
    strict = pm.parse_plant_map(pmap, env={})
    assert {tg.tag_id for tg in strict.tags} == set(by_id) and strict.skipped == []
    assert strict.sources["ueapd-m2"].base_url == "http://192.168.1.16:8003"
    doc, _ = _extract_map(prev)
    remote = _validate(doc, gw["id"], env={})
    assert len(remote.tags) == 4 and getattr(remote, "rejected", []) == []
    assert {tg.key for tg in remote.tags if tg.source == "ueapd-m2"} == {"ledd.m2.cyc"}

    # removing the ueapd devices: the http tags stay out of the map with a warning (not a broken map)
    await api(client, "PATCH", f"/api/gateways/{gw['id']}", h, json={"devices": [devices[2]]})
    prev = await api(client, "GET", f"/api/gateways/{gw['id']}/config-preview", h)
    assert prev["plant_map"]["tags"] == [] and len(prev["warnings"]) == 4
    assert all("ignorada" in w for w in prev["warnings"])
    pm.parse_plant_map(prev["plant_map"], env={})


async def test_tag_csv_import_accepts_ueapd_rows(client):
    pm = edge_plant_map()
    t = await _tenant("UEC")
    h = t.headers("admin")
    plant = await api(client, "POST", "/api/plants/", h, 201, json={"name": "Planta UEC"})
    line = await api(client, "POST", "/api/lines/", h, 201, json={"name": "Linha LED", "plant_id": plant["id"]})
    m1 = await api(client, "POST", "/api/machines/", h, 201, json={"line_id": line["id"], "name": "M1 Hipot"})
    gw = await api(client, "POST", "/api/gateways/", h, 201, json={"name": "gw", "plant_id": plant["id"], "devices": [
        {"id": "ueapd-m1", "protocol": "ueapd", "base_url": "http://192.168.1.15:8003"}]})
    csv = ("chave;linha;maquina;posicao;papel;fonte;endereco\n"
           "ledd.m1.run;Linha LED;M1 Hipot;;run_state;ueapd-m1;state:run\n"
           "ledd.m1.good;Linha LED;M1 Hipot;;part_counter_good;ueapd-m1;passes:good\n"
           "ledd.m1.bad;Linha LED;M1 Hipot;;analog;ueapd-m1;cycles:x\n")
    pv = await api(client, "POST", "/api/onboarding/tags/import", h, json={"gateway_id": gw["id"], "csv": csv})
    assert pv["summary"] == {"ok": 2, "update": 0, "error": 1}
    assert "exige semantic_role" in pv["rows"][2]["message"]
    out = await api(client, "POST", "/api/onboarding/tags/import", h, json={
        "gateway_id": gw["id"], "csv": "\n".join(csv.splitlines()[:3]) + "\n", "confirm": True})
    assert out["created"] == 2 and out["config_warnings"] == []
    tags = await api(client, "GET", "/api/tags/", h, params={"machine_id": m1["id"]})
    assert {x["protocol"] for x in tags} == {"http"}
    prev = await api(client, "GET", f"/api/gateways/{gw['id']}/config-preview", h)
    assert len(pm.parse_plant_map(prev["plant_map"], env={}).tags) == 2
