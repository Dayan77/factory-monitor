"""Sprint 6 §3 / acceptance 5 — operator tablet backend: an operator assigned to lines acts only on them (403 on
other lines: downtime list / justification / manual stop / production runs), and X-Client-Action-Id makes the
offline queue idempotent (a replay returns what was recorded; a stop already justified by someone else is not
overwritten — the server wins)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from conftest import _make_tenant
from usage_helpers import api, build_plant


async def _tenant(label):
    from src.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, label)
        await s.commit()
    return t


async def test_operator_line_scope_and_idempotent_actions(client, db):
    from src.models import DowntimeEvent
    t = await _tenant("OPS")
    h, ho, hm = t.headers("admin"), t.headers("operator"), t.headers("manager")
    p = await build_plant(client, t, "OPS")
    line2 = await api(client, "POST", "/api/lines/", h, 201, json={"name": "Linha 2 OPS", "plant_id": p.plant["id"]})
    m2 = await api(client, "POST", "/api/machines/", h, 201, json={"line_id": line2["id"], "name": "M2 OPS"})
    m1 = p.machine
    prod = await api(client, "POST", "/api/products/", h, 201, json={"code": "OPS-P", "name": "P"})
    now = datetime.now(timezone.utc)
    ev1 = DowntimeEvent(tenant_id=t.id, machine_id=m1["id"], start=now - timedelta(minutes=30),
                        end=now - timedelta(minutes=20), duration_s=600, source="auto_run_state",
                        stop_start=now - timedelta(minutes=30))
    ev2 = DowntimeEvent(tenant_id=t.id, machine_id=m2["id"], start=now - timedelta(minutes=30),
                        end=now - timedelta(minutes=20), duration_s=600, source="auto_run_state",
                        stop_start=now - timedelta(minutes=30))
    db.add_all([ev1, ev2])
    await db.commit()
    # before the assignment: the operator sees every line (unchanged behaviour)
    assert {e["id"] for e in await api(client, "GET", "/api/downtime/", ho)} == {ev1.id, ev2.id}
    await api(client, "PATCH", f"/api/users/{t.users['operator']['id']}", h, json={"line_ids": [p.line["id"]]})

    # list: only its line; another line / machine → 403
    assert [e["id"] for e in await api(client, "GET", "/api/downtime/", ho)] == [ev1.id]
    await api(client, "GET", "/api/downtime/", ho, 403, params={"line_id": line2["id"]})
    await api(client, "GET", "/api/downtime/", ho, 403, params={"machine_id": m2["id"]})
    # justification / manual stop / production run on another line → 403
    await api(client, "PATCH", f"/api/downtime/{ev2.id}", ho, 403, json={"reason_code": "MAT-01"})
    await api(client, "POST", "/api/downtime/", ho, 403, json={
        "machine_id": m2["id"], "start": (now - timedelta(minutes=10)).isoformat(),
        "end": (now - timedelta(minutes=5)).isoformat(), "reason_code": "MAT-01"})
    await api(client, "POST", "/api/production-runs/", ho, 403, json={"machine_id": m2["id"], "product_id": prod["id"]})
    run_other = await api(client, "POST", "/api/production-runs/", hm, 201,
                          json={"machine_id": m2["id"], "product_id": prod["id"]})
    await api(client, "POST", f"/api/production-runs/{run_other['id']}/stop", ho, 403)
    await api(client, "GET", f"/api/production-runs/{run_other['id']}", ho, 403)
    assert run_other["id"] not in {r["id"] for r in await api(client, "GET", "/api/production-runs/", ho)}
    # the manager is not restricted
    assert len(await api(client, "GET", "/api/downtime/", hm)) == 2

    # idempotent justification: same key twice → one effect, same answer
    key = f"tab-{uuid.uuid4().hex}"
    r1 = await client.patch(f"/api/downtime/{ev1.id}", headers={**ho, "X-Client-Action-Id": key},
                            json={"reason_code": "MAT-01"})
    assert r1.status_code == 200 and r1.json()["reason_code"] == "MAT-01"
    r2 = await client.patch(f"/api/downtime/{ev1.id}", headers={**ho, "X-Client-Action-Id": key},
                            json={"reason_code": "SET-01"})
    assert r2.status_code == 200 and r2.headers.get("idempotent-replay") == "true"
    assert r2.json()["reason_code"] == "MAT-01" and r2.json()["justified_at"] == r1.json()["justified_at"]
    # the key cannot be reused for another kind of action
    await api(client, "POST", "/api/downtime/", {**ho, "X-Client-Action-Id": key}, 422, json={
        "machine_id": m1["id"], "start": (now - timedelta(minutes=10)).isoformat(), "reason_code": "MAT-01"})
    await api(client, "PATCH", f"/api/downtime/{ev1.id}", {**ho, "X-Client-Action-Id": "x"}, 422,
              json={"reason_code": "MAT-01"})

    # conflict: justified by the manager meanwhile → the tablet's queued action does not overwrite (409 + current)
    ev3 = DowntimeEvent(tenant_id=t.id, machine_id=m1["id"], start=now - timedelta(minutes=15),
                        end=now - timedelta(minutes=12), duration_s=180, source="auto_run_state",
                        stop_start=now - timedelta(minutes=15))
    db.add(ev3)
    await db.commit()
    await api(client, "PATCH", f"/api/downtime/{ev3.id}", hm, json={"reason_code": "SET-01"})
    r = await client.patch(f"/api/downtime/{ev3.id}", headers={**ho, "X-Client-Action-Id": f"tab-{uuid.uuid4().hex}"},
                           json={"reason_code": "MAT-01"})
    assert r.status_code == 409 and r.json()["detail"]["current"]["reason_code"] == "SET-01"
    # without the key (online correction on purpose) the operator may still change it
    await api(client, "PATCH", f"/api/downtime/{ev3.id}", ho, json={"reason_code": "MAT-01"})

    # idempotent manual stop: the replay never creates a second stop
    k2 = f"tab-{uuid.uuid4().hex}"
    body = {"machine_id": m1["id"], "start": (now - timedelta(minutes=8)).isoformat(),
            "end": (now - timedelta(minutes=4)).isoformat(), "reason_code": "MAT-01"}
    a = await api(client, "POST", "/api/downtime/", {**ho, "X-Client-Action-Id": k2}, 201, json=body)
    b = await api(client, "POST", "/api/downtime/", {**ho, "X-Client-Action-Id": k2}, 201, json=body)
    assert a["id"] == b["id"]
    n = (await db.execute(select(func.count()).select_from(DowntimeEvent).where(
        DowntimeEvent.machine_id == m1["id"], DowntimeEvent.source == "manual"))).scalar_one()
    assert n == 1
    # idempotent product change
    k3 = f"tab-{uuid.uuid4().hex}"
    r1 = await api(client, "POST", "/api/production-runs/", {**ho, "X-Client-Action-Id": k3}, 201,
                   json={"machine_id": m1["id"], "product_id": prod["id"]})
    r2 = await api(client, "POST", "/api/production-runs/", {**ho, "X-Client-Action-Id": k3}, 201,
                   json={"machine_id": m1["id"], "product_id": prod["id"]})
    assert r1["id"] == r2["id"]
    # keys are per user: another user's identical key is a different action
    other = await api(client, "POST", "/api/downtime/", {**hm, "X-Client-Action-Id": k2}, 201, json=body)
    assert other["id"] != a["id"]


async def test_client_action_keys_are_tenant_isolated(client, db):
    from src.models import DowntimeEvent
    a = await _tenant("OPA")
    b = await _tenant("OPB")
    pa = await build_plant(client, a, "OPA")
    now = datetime.now(timezone.utc)
    ev = DowntimeEvent(tenant_id=a.id, machine_id=pa.machine["id"], start=now - timedelta(minutes=5),
                       end=now - timedelta(minutes=1), duration_s=240, source="auto_run_state",
                       stop_start=now - timedelta(minutes=5))
    db.add(ev)
    await db.commit()
    key = f"tab-{uuid.uuid4().hex}"
    await api(client, "PATCH", f"/api/downtime/{ev.id}", {**a.headers("operator"), "X-Client-Action-Id": key},
              json={"reason_code": "MAT-01"})
    # B with the same key and A's event id: 404, nothing replayed from A
    await api(client, "PATCH", f"/api/downtime/{ev.id}", {**b.headers("operator"), "X-Client-Action-Id": key}, 404,
              json={"reason_code": "MAT-01"})


def _png(w: int, h: int) -> bytes:
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\xff\xff" * w for _ in range(min(h, 4)))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


async def test_operator_reads_never_show_another_line(client, db):
    """Review: the map (published/auto), overview, plant tree, machine list and the WebSocket returned every
    machine of the plant to an operator restricted to L1. §3.2: 'nunca outras linhas'."""
    t = await _tenant("OPR")
    h, ho, hm = t.headers("admin"), t.headers("operator"), t.headers("manager")
    p = await build_plant(client, t, "OPR")
    pid = p.plant["id"]
    line2 = await api(client, "POST", "/api/lines/", h, 201, json={"name": "Linha 2 OPR", "plant_id": pid})
    m2 = await api(client, "POST", "/api/machines/", h, 201, json={"line_id": line2["id"], "name": "M2 OPR"})
    await api(client, "PATCH", f"/api/users/{t.users['operator']['id']}", h, json={"line_ids": [p.line["id"]]})
    mine, other = p.machine["id"], m2["id"]

    def machine_ids(view):
        return {m["machine_id"] for m in view["machines"]}

    auto = await api(client, "GET", f"/api/plants/{pid}/layout/auto", ho)
    assert machine_ids(auto) == {mine} and {ln["line_id"] for ln in auto["lines"]} == {p.line["id"]}
    assert {mk["machine_id"] for mk in auto["markers"] if mk["machine_id"]} == {mine}
    assert {ln["line_id"] for ln in auto["lanes"]} == {p.line["id"]}
    assert machine_ids(await api(client, "GET", f"/api/plants/{pid}/layout/auto", hm)) == {mine, other}

    lay = await api(client, "POST", f"/api/plants/{pid}/layouts", hm, 201)
    r = await client.post(f"/api/layouts/{lay['id']}/image", headers=hm,
                          files={"file": ("p.png", _png(400, 250), "application/octet-stream")})
    assert r.status_code == 200, r.text
    await api(client, "PUT", f"/api/layouts/{lay['id']}/markers", hm, json={"markers": [
        {"kind": "machine", "machine_id": mine, "x": 0.1, "y": 0.3},
        {"kind": "machine", "machine_id": other, "x": 0.5, "y": 0.3},
        {"kind": "line_label", "line_id": line2["id"], "x": 0.5, "y": 0.1},
        {"kind": "area_label", "label": "Almoxarifado", "x": 0.9, "y": 0.85}]})
    await api(client, "POST", f"/api/layouts/{lay['id']}/publish", hm)
    view = await api(client, "GET", f"/api/plants/{pid}/layout/published", ho)
    assert machine_ids(view) == {mine} and {ln["line_id"] for ln in view["lines"]} == {p.line["id"]}
    mk = view["layout"]["markers"]
    assert other not in {m["machine_id"] for m in mk} and line2["id"] not in {m["line_id"] for m in mk}
    assert "Almoxarifado" in {m["label"] for m in mk}                       # neutral labels stay
    assert other not in view["unplaced_machine_ids"]
    full = await api(client, "GET", f"/api/plants/{pid}/layout/published", hm)
    assert machine_ids(full) == {mine, other} and len(full["layout"]["markers"]) == 4

    ov = await api(client, "GET", "/api/overview", ho, params={"plant_id": pid})
    assert {ln["line_id"] for ln in ov["lines"]} == {p.line["id"]}
    assert all(m["line_id"] == p.line["id"] for m in ov["machines_in_error"])
    tree = await api(client, "GET", "/api/plants/tree", ho)
    tlines = [ln for pl in tree for ln in pl["lines"]]
    assert {ln["id"] for ln in tlines} == {p.line["id"]}
    assert {m["id"] for m in await api(client, "GET", "/api/machines/", ho)} == {mine}
    assert {m["id"] for m in await api(client, "GET", "/api/machines/", hm)} >= {mine, other}

    # WebSocket: the operator's socket only gets messages about his machines/tags/positions
    from src.websocket.manager import operator_ws_scope
    sc = await operator_ws_scope(t.users["operator"]["id"], t.id)
    assert sc is not None and sc.machine_ids == {mine}
    assert sc.allows({"type": "machine_status", "machine_id": mine})
    assert not sc.allows({"type": "machine_status", "machine_id": other})
    assert not sc.allows({"type": "downtime", "action": "open", "event": {"machine_id": other}})
    assert not sc.allows({"type": "sensor_reading", "tag_id": str(uuid.uuid4())})
    assert sc.allows({"type": "notification"})
    assert await operator_ws_scope(t.users["manager"]["id"], t.id) is None


async def test_broadcast_skips_sockets_out_of_scope():
    from src.websocket.manager import ConnectionManager, WsScope

    class FakeWs:
        def __init__(self):
            self.sent = []

        async def send_json(self, m):
            self.sent.append(m)

    mgr = ConnectionManager()
    op, sup = FakeWs(), FakeWs()
    tid = str(uuid.uuid4())
    await mgr.connect(op, tid, accept=False, scope=WsScope({"m1"}, {"t1"}, set()))
    await mgr.connect(sup, tid, accept=False)
    await mgr.broadcast(tid, {"type": "machine_status", "machine_id": "m2"})
    await mgr.broadcast(tid, {"type": "sensor_reading", "tag_id": "t1"})
    assert [m.get("tag_id") for m in op.sent] == ["t1"] and len(sup.sent) == 2
