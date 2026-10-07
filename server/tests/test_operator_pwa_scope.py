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
