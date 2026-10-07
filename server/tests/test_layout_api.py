"""Sprint 6 §1 — plant layouts API: drafts/published versions, markers (409 duplicate, 422 other plant / out of
range), image upload (formats, size, SVG refused when active/external), authenticated image serving with
Cache-Control, 'revisar posições' on aspect change, publish audit, published view with the visual state of every
machine (§1.2), automatic layout (§1.5), RBAC and tenant isolation (another tenant's layout/plant → 404)."""
from __future__ import annotations

import json
import re
import struct
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, update

from conftest import _make_tenant
from usage_helpers import api, build_plant

DOC = (Path(__file__).resolve().parents[2] / "docs" / "sprint6.md").read_text(encoding="utf-8")
EXAMPLE = json.loads(re.search(r"```json s6-layout-example\n(.*?)\n```", DOC, re.S).group(1))


@pytest.fixture(autouse=True)
def _files_dir(tmp_path, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "files_dir", str(tmp_path / "files"))


async def _tenant(label):
    from src.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, label)
        await s.commit()
    return t


def png(w: int, h: int) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\xff\xff" * w for _ in range(min(h, 4)))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg(w: int, h: int) -> bytes:
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + sof + b"\xff\xd9"


def svg(body: str = "", attrs: str = 'width="400" height="250"') -> bytes:
    return f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" {attrs}>{body}</svg>'.encode()


async def _upload(client, h, lay_id, data: bytes, name="planta.png", expect=200):
    r = await client.post(f"/api/layouts/{lay_id}/image", headers=h, files={"file": (name, data, "application/octet-stream")})
    assert r.status_code == expect, r.text
    return r.json()


async def _plant_with_machines(client, t, label, n=3):
    p = await build_plant(client, t, label)
    h = t.headers("admin")
    ms = [p.machine]
    for i in range(1, n):
        ms.append(await api(client, "POST", "/api/machines/", h, 201, json={"line_id": p.line["id"],
                                                                              "name": f"M{i} {label}"}))
    await api(client, "PATCH", f"/api/lines/{p.line['id']}", h, json={"output_machine_id": ms[-1]["id"]})
    return p, ms


def test_svg_safety_rules():
    from src.services.layout.images import ImageError, check_svg, inspect
    assert check_svg(svg('<rect x="1" y="1" width="10" height="10" fill="url(#g)"/>')) == (400, 250)
    assert check_svg(svg(attrs='viewBox="0 0 4000 2500"')) == (4000, 2500)
    assert inspect(svg('<image href="data:image/png;base64,AAAA"/>')).kind == "svg"
    bad = [svg("<script>alert(1)</script>"), svg('<rect onclick="x()"/>'), svg("<foreignObject><p/></foreignObject>"),
           svg('<image href="https://evil.example/x.png"/>'), svg('<use xlink:href="http://evil/x.svg#a"/>'),
           svg('<rect style="fill:url(https://evil/x)"/>'), svg("<style>@import url(https://evil/x.css);</style>"),
           svg('<a href="javascript:alert(1)"><rect/></a>'),
           b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x "y">]><svg width="1" height="1">&x;</svg>']
    for b in bad:
        with pytest.raises(ImageError):
            check_svg(b)
    with pytest.raises(ImageError):
        inspect(png(8001, 100))                            # larger side > 8 000 px
    assert inspect(png(4000, 2500)).width == 4000 and inspect(jpeg(1200, 800)).height == 800
    with pytest.raises(ImageError):
        inspect(b"GIF89a....")


async def test_layout_lifecycle_rules_and_publish(client, db):
    t = await _tenant("LAY")
    h, hm, ho = t.headers("admin"), t.headers("manager"), t.headers("operator")
    p, ms = await _plant_with_machines(client, t, "LAY")
    pid = p.plant["id"]
    other_plant, other_ms = await _plant_with_machines(client, t, "LAY2", n=1)

    # no layout yet: published → 404 with the auto URL; auto layout works for every role
    r = await client.get(f"/api/plants/{pid}/layout/published", headers=ho)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "no_published_layout"
    auto = await api(client, "GET", f"/api/plants/{pid}/layout/auto", ho)
    assert auto["kind"] == "auto" and auto["banner"].startswith("Layout automático")
    lane = auto["lanes"][0]
    assert lane["machine_ids"][-1] == ms[-1]["id"]                    # output machine last (flow order)
    assert {m["machine_id"] for m in auto["machines"]} == {m["id"] for m in ms}
    assert all(0 <= mk["x"] <= 1 and 0 <= mk["y"] <= 1 for mk in auto["markers"])

    # RBAC: operator cannot create; manager can
    await api(client, "POST", f"/api/plants/{pid}/layouts", ho, 403)
    lay = await api(client, "POST", f"/api/plants/{pid}/layouts", hm, 201)
    assert lay["status"] == "draft" and lay["version"] == 1 and lay["markers"] == []
    r = await client.post(f"/api/plants/{pid}/layouts", headers=h)
    assert r.status_code == 409 and r.json()["detail"]["draft_id"] == lay["id"]       # one draft at a time

    # publish without image → 422 (screen uses the automatic layout)
    await api(client, "POST", f"/api/layouts/{lay['id']}/publish", h, 422)
    # image: formats / limits
    await _upload(client, h, lay["id"], svg("<script>alert(1)</script>"), "x.svg", expect=422)
    await _upload(client, h, lay["id"], b"GIF89a" + b"\x00" * 100, "x.gif", expect=415)
    from src.config import settings
    big = png(10, 10) + b"\x00" * (settings.layout_image_max_bytes + 1)
    await _upload(client, h, lay["id"], big, expect=413)
    await _upload(client, ho, lay["id"], png(10, 10), expect=403)
    out = await _upload(client, h, lay["id"], png(4000, 2500))
    assert out["image_width_px"] == 4000 and out["image_height_px"] == 2500 and out["has_image"]
    assert out["needs_review"] is False

    # markers: out of range → 422; duplicate → 409; machine of another plant → 422; line of another plant → 422
    base = [{"kind": "machine", "machine_id": ms[0]["id"], "x": 0.1, "y": 0.3},
            {"kind": "machine", "machine_id": ms[1]["id"], "x": 0.3, "y": 0.3, "rotation_deg": 90, "size": "l"},
            {"kind": "line_label", "line_id": p.line["id"], "x": 0.2, "y": 0.1},
            {"kind": "area_label", "label": "Almoxarifado", "x": 0.9, "y": 0.85}]
    url = f"/api/layouts/{lay['id']}/markers"
    await api(client, "PUT", url, h, 422, json={"markers": [{**base[0], "x": 1.01}]})
    await api(client, "PUT", url, h, 422, json={"markers": [{**base[0], "y": -0.1}]})
    await api(client, "PUT", url, h, 422, json={"markers": [{**base[0], "rotation_deg": 360}]})
    await api(client, "PUT", url, h, 422, json={"markers": [{"kind": "area_label", "x": 0.1, "y": 0.1}]})
    r = await client.put(url, headers=h, json={"markers": [base[0], {**base[0], "x": 0.5}]})
    assert r.status_code == 409, r.text
    r = await client.put(url, headers=h, json={"markers": [{**base[0], "machine_id": other_ms[0]["id"]}]})
    assert r.status_code == 422 and "outra planta" in r.text
    await api(client, "PUT", url, h, 422, json={"markers": [{"kind": "line_label", "line_id": other_plant.line["id"],
                                                             "x": 0.1, "y": 0.1}]})
    await api(client, "PUT", url, ho, 403, json={"markers": base})
    put = await api(client, "PUT", url, hm, json={"markers": base})
    assert len(put["markers"]) == 4
    assert {m["machine_id"] for m in put["markers"] if m["kind"] == "machine"} == {ms[0]["id"], ms[1]["id"]}

    # image served with the tenant JWT (header or ?token=), Cache-Control; without → 401
    r = await client.get(f"/api/layouts/{lay['id']}/image")
    assert r.status_code == 401
    r = await client.get(f"/api/layouts/{lay['id']}/image", headers=ho)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert "max-age" in r.headers["cache-control"] and r.content[:4] == b"\x89PNG"
    r = await client.get(f"/api/layouts/{lay['id']}/image", params={"token": t.users["operator"]["token"]})
    assert r.status_code == 200

    # publish (audited); the published layout is immutable — editing needs a new draft
    pub = await api(client, "POST", f"/api/layouts/{lay['id']}/publish", hm)
    assert pub["status"] == "published" and pub["published_by"] == t.users["manager"]["id"]
    from src.models import AuditEvent
    ev = (await db.execute(select(AuditEvent).where(AuditEvent.tenant_id == t.id,
                                                    AuditEvent.entity_id == lay["id"]))).scalars().all()
    assert [e.action for e in ev] == ["layout.publish"] and ev[0].data["markers"] == 4
    await api(client, "PUT", url, h, 409, json={"markers": base})

    view = await api(client, "GET", f"/api/plants/{pid}/layout/published", ho)
    assert view["kind"] == "published" and view["layout"]["id"] == lay["id"]
    assert view["unplaced_machine_ids"] == [ms[2]["id"]]                  # never disappears: in the tray
    assert {m["machine_id"] for m in view["machines"]} == {m["id"] for m in ms}
    assert all(m["state"] == "offline" and m["icon"] and m["text"] for m in view["machines"])   # no data yet
    assert view["lines"][0]["output_machine_id"] == ms[-1]["id"] and view["lines"][0]["output_state"] == "offline"

    # new draft copies image + markers (version 2); a different aspect → needs_review; publish archives v1
    d2 = await api(client, "POST", f"/api/plants/{pid}/layouts", h, 201)
    assert d2["version"] == 2 and len(d2["markers"]) == 4 and d2["has_image"]
    d2 = await _upload(client, h, d2["id"], png(4000, 2000))
    assert d2["needs_review"] is True and len(d2["markers"]) == 4
    d2b = await _upload(client, h, d2["id"], jpeg(2000, 1000))           # same aspect as 4000×2000
    assert d2b["image_content_type"] == "image/jpeg"
    p2 = await api(client, "POST", f"/api/layouts/{d2['id']}/publish", h)
    assert p2["needs_review"] is False
    vers = await api(client, "GET", f"/api/plants/{pid}/layouts", ho)
    assert [(v["version"], v["status"]) for v in vers] == [(2, "published"), (1, "archived")]
    r = await client.get(f"/api/layouts/{lay['id']}/image", headers=ho)  # archived v1 keeps its own image file
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert (await api(client, "PATCH", f"/api/layouts/{d2['id']}", h, 409, json={"scale_m_per_px": 0.01}))


async def test_published_view_states_follow_the_precedence(client, db):
    from src.models import DowntimeEvent, FailureRecord, Machine, MachineStatus, WorkOrder
    t = await _tenant("LST")
    h = t.headers("admin")
    p, ms = await _plant_with_machines(client, t, "LST", n=5)
    lay = await api(client, "POST", f"/api/plants/{p.plant['id']}/layouts", h, 201)
    await _upload(client, h, lay["id"], png(1000, 600))
    await api(client, "PUT", f"/api/layouts/{lay['id']}/markers", h, json={"markers": [
        {"kind": "machine", "machine_id": m["id"], "x": 0.1 + 0.15 * i, "y": 0.5} for i, m in enumerate(ms)]})
    await api(client, "POST", f"/api/layouts/{lay['id']}/publish", h)
    now = datetime.now(timezone.utc)
    m_off, m_fail, m_maint, m_stop, m_run = [m["id"] for m in ms]
    for mid, st in ((m_off, MachineStatus.unknown), (m_fail, MachineStatus.stopped), (m_maint, MachineStatus.running),
                    (m_stop, MachineStatus.stopped), (m_run, MachineStatus.running)):
        await db.execute(update(Machine).where(Machine.id == mid).values(
            status=st, last_seen_at=None if st == MachineStatus.unknown else now))
    db.add(FailureRecord(tenant_id=t.id, machine_id=m_fail, failed_at=now - timedelta(minutes=8),
                         failure_mode="ELP", status="draft"))
    db.add(WorkOrder(tenant_id=t.id, number=123, type="corrective", state="in_progress", machine_id=m_maint,
                     title="troca", stops_machine=True, started_at=now - timedelta(minutes=5)))
    db.add(DowntimeEvent(tenant_id=t.id, machine_id=m_stop, start=now - timedelta(minutes=6), source="manual",
                         stop_start=now - timedelta(minutes=6)))
    await db.commit()
    view = await api(client, "GET", f"/api/plants/{p.plant['id']}/layout/published", t.headers("operator"))
    st = {m["machine_id"]: m for m in view["machines"]}
    assert st[m_off]["state"] == "offline"
    assert st[m_fail]["state"] == "failure" and st[m_fail]["text"] == "falha — ELP"
    assert st[m_maint]["state"] == "maintenance" and st[m_maint]["text"] == "manutenção — OS-123"
    assert st[m_stop]["state"] == "stopped" and st[m_stop]["text"] == "parada — sem motivo"
    assert st[m_run]["state"] == "running"
    assert view["shift"] is not None and view["shift"]["name"]            # implicit 24 h calendar

    # a shift that does not contain "now" → every machine with data and no failure/WO is off_shift
    tz = __import__("zoneinfo").ZoneInfo("America/Manaus")
    loc = now.astimezone(tz) + timedelta(hours=6)
    await api(client, "POST", "/api/shifts/", h, 201, json={
        "plant_id": p.plant["id"], "name": "Turno longe", "start_local": loc.strftime("%H:00"),
        "end_local": (loc + timedelta(hours=1)).strftime("%H:00"), "weekdays": list(range(7)), "breaks": []})
    view = await api(client, "GET", f"/api/plants/{p.plant['id']}/layout/published", t.headers("operator"))
    st = {m["machine_id"]: m["state"] for m in view["machines"]}
    assert st == {m_off: "offline", m_fail: "failure", m_maint: "maintenance", m_stop: "off_shift",
                  m_run: "off_shift"}
    assert view["shift"] is None


async def test_layout_tenant_isolation(client):
    a = await _tenant("LIA")
    b = await _tenant("LIB")
    pa, msa = await _plant_with_machines(client, a, "LIA", n=1)
    pb, msb = await _plant_with_machines(client, b, "LIB", n=1)
    ha, hb = a.headers("admin"), b.headers("admin")
    lay = await api(client, "POST", f"/api/plants/{pa.plant['id']}/layouts", ha, 201)
    await _upload(client, ha, lay["id"], png(100, 100))
    # B cannot see / edit A's plant or layout (404, existence not leaked)
    for method, url, kw in (("GET", f"/api/layouts/{lay['id']}", {}),
                            ("PATCH", f"/api/layouts/{lay['id']}", {"json": {"scale_m_per_px": 0.1}}),
                            ("PUT", f"/api/layouts/{lay['id']}/markers", {"json": {"markers": []}}),
                            ("POST", f"/api/layouts/{lay['id']}/publish", {}),
                            ("GET", f"/api/layouts/{lay['id']}/image", {}),
                            ("GET", f"/api/plants/{pa.plant['id']}/layouts", {}),
                            ("POST", f"/api/plants/{pa.plant['id']}/layouts", {}),
                            ("GET", f"/api/plants/{pa.plant['id']}/layout/published", {}),
                            ("GET", f"/api/plants/{pa.plant['id']}/layout/auto", {}),
                            ("GET", "/api/layouts/not-a-uuid", {})):
        r = await client.request(method, url, headers=hb, **kw)
        assert r.status_code == 404, (method, url, r.status_code, r.text)
    await _upload(client, hb, lay["id"], png(10, 10), expect=404)
    # A cannot place B's machine on its layout
    r = await client.put(f"/api/layouts/{lay['id']}/markers", headers=ha, json={"markers": [
        {"kind": "machine", "machine_id": msb[0]["id"], "x": 0.5, "y": 0.5}]})
    assert r.status_code == 422 and "não encontrada" in r.text
    # B's auto layout lists only B's machines
    auto = await api(client, "GET", f"/api/plants/{pb.plant['id']}/layout/auto", hb)
    assert {m["machine_id"] for m in auto["machines"]} == {msb[0]["id"]}


async def test_layout_example_block_is_accepted(client):
    """The s6-layout-example (normalised coordinates, one marker per machine) through the API."""
    t = await _tenant("LEX")
    h = t.headers("admin")
    plant = await api(client, "POST", "/api/plants/", h, 201, json={"name": EXAMPLE["plant"]})
    ids = {}
    for ln_name in {m["line"] for m in EXAMPLE["markers"] if m["kind"] == "line_label"}:
        ids[ln_name] = (await api(client, "POST", "/api/lines/", h, 201,
                                  json={"name": ln_name, "plant_id": plant["id"]}))["id"]
    line_for = {"Torno CNC": "Linha de Usinagem", "Centro de Usinagem": "Linha de Usinagem"}
    for mk in EXAMPLE["markers"]:
        if mk["kind"] == "machine":
            ln = line_for.get(mk["machine"], "Linha de Montagem")
            ids[mk["machine"]] = (await api(client, "POST", "/api/machines/", h, 201,
                                            json={"line_id": ids[ln], "name": mk["machine"]}))["id"]
    lay = await api(client, "POST", f"/api/plants/{plant['id']}/layouts", h, 201)
    w, hh = EXAMPLE["image"]["width_px"], EXAMPLE["image"]["height_px"]
    await _upload(client, h, lay["id"], png(w, hh))
    markers = []
    for mk in EXAMPLE["markers"]:
        body = {"kind": mk["kind"], "x": mk["x"], "y": mk["y"], "rotation_deg": mk.get("rotation_deg", 0),
                "size": mk.get("size", "m")}
        if mk["kind"] == "machine":
            body["machine_id"] = ids[mk["machine"]]
        elif mk["kind"] == "line_label":
            body["line_id"] = ids[mk["line"]]
        else:
            body["label"] = mk["label"]
        markers.append(body)
    out = await api(client, "PUT", f"/api/layouts/{lay['id']}/markers", h, json={"markers": markers})
    assert len(out["markers"]) == len(EXAMPLE["markers"])
    await api(client, "POST", f"/api/layouts/{lay['id']}/publish", h)
    view = await api(client, "GET", f"/api/plants/{plant['id']}/layout/published", h)
    assert view["unplaced_machine_ids"] == [] and len(view["machines"]) == 5
