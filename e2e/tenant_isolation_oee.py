"""Black-box tenant isolation + RBAC of every Sprint 3 endpoint against a running server.

Tenant A and tenant B are freshly registered. Operator/manager users of A are inserted straight into the
server's database (there is no user-management endpoint), so run it from server/ with the server's .env:

    cd server && set -a && . ../.env && set +a && POSTGRES_DB=<db of that server> \
        ../.venv/bin/python ../e2e/tenant_isolation_oee.py          [env: API=http://localhost:8000]

A builds plant/line/machine/tags, a shift, a calendar exception, a product + ideal cycle time, a production run,
a tenant reason code and a manual stop. Then:
* every read/write by id from B on A's rows → 404 (never 200/204), A's data unchanged; B's lists never contain
  A's rows; B cannot reference A's ids in its own writes (404/422); KPI endpoints on A's scopes → 404;
* WebSocket /ws/sensors/{A} with B's token is refused;
* RBAC inside A: operator reads everything, justifies stops, registers manual stops and production runs, and
  gets 403 on shifts/calendar/products/PRI/reason codes/machine settings; manager can edit machine settings but
  not shifts; unauthenticated → 401.
Exit code 0 = every check passed.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx

API = os.getenv("API", "http://localhost:8000")
results: list[tuple[bool, str]] = []


def check(ok, name, got=None):
    results.append((bool(ok), name))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   got={got!r}"))


def client(token=None):
    c = httpx.Client(base_url=API, timeout=30)
    if token:
        c.headers["Authorization"] = f"Bearer {token}"
    return c


def login(email, pw):
    r = httpx.post(f"{API}/api/auth/login", json={"email": email, "password": pw})
    r.raise_for_status()
    return r.json()["access_token"]


def register(label):
    stamp = f"{int(time.time())}{uuid.uuid4().hex[:4]}"
    email = f"qa-iso3-{label}-{stamp}@qa-factoryconnect.com.br"
    r = httpx.post(f"{API}/api/auth/register", json={"company_name": f"QA Iso3 {label} {stamp}", "admin_name": "QA",
                                                     "admin_email": email, "admin_password": "QaIsola#2026x"})
    r.raise_for_status()
    c = client(login(email, "QaIsola#2026x"))
    return c, c.get("/api/auth/me").json()["tenant_id"]


async def add_users(tenant_id: str) -> dict[str, str]:
    sys.path.insert(0, os.getcwd())
    from src.auth.security import hash_password
    from src.db import AsyncSessionLocal, engine
    from src.models import User, UserRole
    out = {}
    async with AsyncSessionLocal() as s:
        for role in ("operator", "manager"):
            email = f"qa-{role}-{uuid.uuid4().hex[:8]}@qa-factoryconnect.com.br"
            s.add(User(tenant_id=tenant_id, email=email, password_hash=hash_password("QaRole#2026x"),
                       name=f"QA {role}", role=UserRole(role)))
            out[role] = email
        await s.commit()
    await engine.dispose()
    return out


def J(r):
    if r.status_code not in (200, 201):
        print(f"  setup call failed: {r.request.method} {r.request.url.path} -> {r.status_code} {r.text[:200]}")
        return {"_status": r.status_code}
    return r.json()


def main() -> int:
    a, ta = register("a")
    b, tb = register("b")
    now = datetime.now(timezone.utc)
    day = (now - timedelta(days=1)).date().isoformat()

    plant = a.post("/api/plants/", json={"name": "A plant", "timezone": "America/Manaus"}).json()
    line = a.post("/api/lines/", json={"name": "A line", "plant_id": plant["id"]}).json()
    mach = a.post("/api/machines/", json={"name": "A machine", "line_id": line["id"]}).json()
    a.patch(f"/api/lines/{line['id']}", json={"output_machine_id": mach["id"]})
    a.post("/api/tags/", json={"machine_id": mach["id"], "name": "run", "protocol": "opc_ua", "address": "ns=2;s=A.run",
                               "data_type": "bool", "semantic_role": "run_state"})
    shift = J(a.post("/api/shifts/", json={"plant_id": plant["id"], "name": "T1", "start_local": "06:00", "end_local": "14:00",
                                         "weekdays": [0, 1, 2, 3, 4, 5, 6],
                                         "breaks": [{"start_local": "09:00", "end_local": "09:10"}]}))
    exc = J(a.post("/api/calendar-exceptions/", json={"plant_id": plant["id"], "date": "2026-12-25", "kind": "holiday",
                                                    "note": "Natal"}))
    prod = J(a.post("/api/products/", json={"code": "A-P1", "name": "A product"}))
    mp = J(a.post(f"/api/machines/{mach['id']}/products", json={"product_id": prod["id"], "ideal_cycle_time_s": 30,
                                                              "is_default": True}))
    run = J(a.post("/api/production-runs/", json={"machine_id": mach["id"], "product_id": prod["id"],
                                                "started_at": (now - timedelta(hours=3)).isoformat()}))
    rc = J(a.post("/api/reason-codes/", json={"code": "MAT-09", "category": "MAT", "label": "A material", "planned": False}))
    ev = J(a.post("/api/downtime/", json={"machine_id": mach["id"], "start": (now - timedelta(hours=2)).isoformat(),
                                        "end": (now - timedelta(hours=2) + timedelta(minutes=5)).isoformat(),
                                        "reason_code": "MAT-01", "comment": "A stop"}))
    for name, obj in (("shift", shift), ("exception", exc), ("product", prod), ("machine product", mp), ("run", run),
                      ("reason code", rc), ("manual stop", ev)):
        check(isinstance(obj, dict) and "id" in obj, f"A created {name}", obj)

    # B's own hierarchy, to try cross references
    bplant = b.post("/api/plants/", json={"name": "B plant"}).json()
    bline = b.post("/api/lines/", json={"name": "B line", "plant_id": bplant["id"]}).json()
    bmach = b.post("/api/machines/", json={"name": "B machine", "line_id": bline["id"]}).json()
    bprod = b.post("/api/products/", json={"code": "A-P1", "name": "same code, other tenant"})
    check(bprod.status_code == 201, "B may reuse A's product code (per-tenant uniqueness)", bprod.status_code)
    bprod = bprod.json()

    print("\n[reads/writes by id from tenant B -> 404]")
    by_id = [
        ("GET", f"/api/shifts/{shift['id']}", None), ("PATCH", f"/api/shifts/{shift['id']}", {"name": "x"}),
        ("DELETE", f"/api/shifts/{shift['id']}", None),
        ("GET", f"/api/calendar-exceptions/{exc['id']}", None),
        ("PATCH", f"/api/calendar-exceptions/{exc['id']}", {"note": "x"}),
        ("DELETE", f"/api/calendar-exceptions/{exc['id']}", None),
        ("GET", f"/api/products/{prod['id']}", None), ("PATCH", f"/api/products/{prod['id']}", {"name": "x"}),
        ("DELETE", f"/api/products/{prod['id']}", None),
        ("GET", f"/api/machines/{mach['id']}/products", None),
        ("POST", f"/api/machines/{mach['id']}/products", {"product_id": bprod["id"], "ideal_cycle_time_s": 1}),
        ("PATCH", f"/api/machines/{mach['id']}/products/{mp['id']}", {"ideal_cycle_time_s": 1}),
        ("PATCH", f"/api/machines/{bmach['id']}/products/{mp['id']}", {"ideal_cycle_time_s": 1}),
        ("DELETE", f"/api/machines/{mach['id']}/products/{mp['id']}", None),
        ("GET", f"/api/production-runs/{run['id']}", None), ("POST", f"/api/production-runs/{run['id']}/stop", {}),
        ("DELETE", f"/api/production-runs/{run['id']}", None),
        ("PATCH", f"/api/reason-codes/{rc['id']}", {"label": "x"}),
        ("PATCH", f"/api/downtime/{ev['id']}", {"reason_code": "UNK-01"}),
        ("PATCH", f"/api/machines/{mach['id']}", {"fault_code_map": {"1": "EQP-01"}}),
    ]
    for verb, path, body in by_id:
        r = b.request(verb, path, json=body) if body is not None else b.request(verb, path)
        check(r.status_code == 404, f"B {verb} {path.replace(API, '')[:70]} -> {r.status_code}", r.text[:120])

    print("\n[KPI endpoints on A's scopes from B -> 404]")
    win = {"start": (now - timedelta(days=1)).isoformat(), "stop": now.isoformat()}
    for path, params in (("/api/oee", {"scope": "machine", "id": mach["id"], **win}),
                         ("/api/oee", {"scope": "line", "id": line["id"], **win}),
                         ("/api/oee", {"scope": "plant", "id": plant["id"], **win}),
                         ("/api/losses/pareto", {"scope": "machine", "id": mach["id"], **win}),
                         ("/api/losses/pareto", {"scope": "line", "id": line["id"], **win}),
                         ("/api/reliability", {"scope": "machine", "id": mach["id"]}),
                         ("/api/reliability", {"scope": "line", "id": line["id"]}),
                         ("/api/reports/shift", {"line_id": line["id"], "productive_day": day}),
                         ("/api/overview", {"plant_id": plant["id"]})):
        r = b.get(path, params=params)
        check(r.status_code == 404, f"B GET {path} {params.get('scope', '')} -> {r.status_code}", r.text[:120])

    print("\n[lists from B do not leak A]")
    for path, params in (("/api/shifts/", {}), ("/api/shifts/", {"plant_id": plant["id"]}),
                         ("/api/shifts/", {"include_history": "true"}),
                         ("/api/shifts/", {"include_history": "true", "plant_id": plant["id"]}),
                         ("/api/calendar-exceptions/", {}), ("/api/calendar-exceptions/", {"plant_id": plant["id"]}),
                         ("/api/products/", {}), ("/api/production-runs/", {}),
                         ("/api/production-runs/", {"machine_id": mach["id"]}),
                         ("/api/downtime/", {}), ("/api/downtime/", {"machine_id": mach["id"]}),
                         ("/api/downtime/", {"line_id": line["id"]})):
        r = b.get(path, params=params)
        body = r.json() if r.status_code == 200 else None
        ids = {x.get("id") for x in body} if isinstance(body, list) else set()
        leaked = ids & {shift["id"], exc["id"], prod["id"], run["id"], ev["id"]}
        check(r.status_code in (200, 404) and not leaked, f"B GET {path} {params} -> {r.status_code}, no A rows",
              (r.status_code, leaked))
    codes = {x["code"] for x in b.get("/api/reason-codes/", params={"include_inactive": "true"}).json()}
    check("MAT-09" not in codes and "MAT-01" in codes, "B reason codes: defaults yes, A's MAT-09 no", sorted(codes)[:5])
    ov = b.get("/api/overview")
    check(ov.status_code in (200, 404) and plant["id"] not in ov.text, "B overview (default plant) has no A data",
          ov.text[:150])

    print("\n[B referencing A's ids in its own writes -> 404/422]")
    for verb, path, body in (
            ("POST", "/api/shifts/", {"plant_id": plant["id"], "name": "x", "start_local": "06:00", "end_local": "07:00",
                                      "weekdays": [0]}),
            ("POST", "/api/calendar-exceptions/", {"plant_id": plant["id"], "date": "2026-12-24", "kind": "holiday"}),
            ("POST", f"/api/machines/{bmach['id']}/products", {"product_id": prod["id"], "ideal_cycle_time_s": 10}),
            ("POST", "/api/production-runs/", {"machine_id": mach["id"], "product_id": bprod["id"]}),
            ("POST", "/api/production-runs/", {"machine_id": bmach["id"], "product_id": prod["id"]}),
            ("POST", "/api/downtime/", {"machine_id": mach["id"], "start": now.isoformat(), "reason_code": "MAT-01"}),
            ("POST", "/api/downtime/", {"machine_id": bmach["id"], "start": (now - timedelta(minutes=9)).isoformat(),
                                        "end": (now - timedelta(minutes=5)).isoformat(), "reason_code": "MAT-09"})):
        r = b.request(verb, path, json=body)
        check(r.status_code in (404, 422), f"B {verb} {path} with A id -> {r.status_code}", r.text[:150])

    print("\n[A's data unchanged]")
    check(a.get(f"/api/shifts/{shift['id']}").json()["name"] == "T1", "A shift intact")
    check(a.get(f"/api/products/{prod['id']}").json()["name"] == "A product", "A product intact")
    a_ev = [e for e in a.get("/api/downtime/", params={"machine_id": mach["id"]}).json() if e["id"] == ev["id"]]
    check(a_ev and a_ev[0]["reason_code"] == "MAT-01", "A stop reason intact", a_ev)
    check(a.get(f"/api/production-runs/{run['id']}").json()["ended_at"] is None, "A run still open")

    print("\n[WebSocket: B's token on A's channel]")
    try:
        import websockets
        ws_url = API.replace("http", "ws") + f"/ws/sensors/{ta}"

        async def try_ws(tok):
            try:
                async with websockets.connect(ws_url, subprotocols=["bearer", tok], open_timeout=5) as w:
                    try:
                        await asyncio.wait_for(w.recv(), timeout=2)
                    except asyncio.TimeoutError:
                        pass
                    return "open"
            except Exception as e:  # noqa: BLE001
                return f"refused ({type(e).__name__})"
        tok_b = b.headers["Authorization"].split()[1]
        tok_a = a.headers["Authorization"].split()[1]
        rb, ra = asyncio.run(try_ws(tok_b)), asyncio.run(try_ws(tok_a))
        check(rb.startswith("refused"), f"B token on /ws/sensors/A -> {rb}")
        check(ra == "open", f"A token on /ws/sensors/A -> {ra}")
    except ImportError:
        print("  (websockets not installed: skipped)")

    print("\n[RBAC inside A]")
    users = asyncio.run(add_users(ta))
    op = client(login(users["operator"], "QaRole#2026x"))
    mg = client(login(users["manager"], "QaRole#2026x"))
    for path, params in (("/api/shifts/", {}), ("/api/calendar-exceptions/", {}), ("/api/products/", {}),
                         (f"/api/machines/{mach['id']}/products", {}), ("/api/production-runs/", {}),
                         ("/api/reason-codes/", {}), ("/api/downtime/", {}),
                         ("/api/oee", {"scope": "machine", "id": mach["id"], **win}),
                         ("/api/losses/pareto", {"scope": "line", "id": line["id"], **win}),
                         ("/api/reliability", {"scope": "machine", "id": mach["id"]}),
                         ("/api/reports/shift", {"line_id": line["id"], "productive_day": day}),
                         ("/api/overview", {})):
        r = op.get(path, params=params)
        check(r.status_code == 200, f"operator GET {path} -> {r.status_code}", r.text[:120])
    r = op.patch(f"/api/downtime/{ev['id']}", json={"reason_code": "MAT-09", "comment": "operador"})
    check(r.status_code == 200 and r.json()["reason_code"] == "MAT-09", f"operator justifies a stop -> {r.status_code}",
          r.text[:150])
    r = op.post("/api/downtime/", json={"machine_id": mach["id"], "start": (now - timedelta(minutes=50)).isoformat(),
                                        "end": (now - timedelta(minutes=40)).isoformat(), "reason_code": "SET-01"})
    check(r.status_code == 201, f"operator registers a manual stop -> {r.status_code}", r.text[:150])
    r = op.post("/api/production-runs/", json={"machine_id": mach["id"], "product_id": prod["id"]})
    check(r.status_code == 201, f"operator starts a production run -> {r.status_code}", r.text[:150])
    if r.status_code == 201:
        r2 = op.post(f"/api/production-runs/{r.json()['id']}/stop", json={})
        check(r2.status_code == 200, f"operator stops the run -> {r2.status_code}", r2.text[:150])
    forbidden = [
        ("POST", "/api/shifts/", {"plant_id": plant["id"], "name": "T2", "start_local": "14:00", "end_local": "22:00",
                                  "weekdays": [0]}),
        ("PATCH", f"/api/shifts/{shift['id']}", {"name": "hack"}), ("DELETE", f"/api/shifts/{shift['id']}", None),
        ("POST", "/api/calendar-exceptions/", {"plant_id": plant["id"], "date": "2026-12-31", "kind": "holiday"}),
        ("PATCH", f"/api/calendar-exceptions/{exc['id']}", {"note": "hack"}),
        ("DELETE", f"/api/calendar-exceptions/{exc['id']}", None),
        ("POST", "/api/products/", {"code": "OP-1", "name": "x"}), ("PATCH", f"/api/products/{prod['id']}", {"name": "x"}),
        ("DELETE", f"/api/products/{prod['id']}", None),
        ("POST", f"/api/machines/{mach['id']}/products", {"product_id": prod["id"], "ideal_cycle_time_s": 1}),
        ("PATCH", f"/api/machines/{mach['id']}/products/{mp['id']}", {"ideal_cycle_time_s": 1}),
        ("DELETE", f"/api/machines/{mach['id']}/products/{mp['id']}", None),
        ("POST", "/api/reason-codes/", {"code": "MAT-08", "category": "MAT", "label": "x", "planned": False}),
        ("PATCH", f"/api/reason-codes/{rc['id']}", {"label": "x"}),
        ("DELETE", f"/api/production-runs/{run['id']}", None),
        ("PATCH", f"/api/machines/{mach['id']}", {"micro_stop_threshold_s": 30, "fault_code_map": {"9": "EQP-01"}}),
    ]
    for verb, path, body in forbidden:
        r = op.request(verb, path, json=body) if body is not None else op.request(verb, path)
        check(r.status_code == 403, f"operator {verb} {path[:60]} -> {r.status_code}", r.text[:120])
    r = mg.patch(f"/api/machines/{mach['id']}", json={"micro_stop_threshold_s": 90})
    check(r.status_code == 200 and r.json().get("micro_stop_threshold_s") == 90, f"manager edits machine threshold -> {r.status_code}")
    r = mg.patch(f"/api/shifts/{shift['id']}", json={"name": "hack"})
    check(r.status_code == 403, f"manager PATCH shift -> {r.status_code}")
    check(a.get(f"/api/shifts/{shift['id']}").json()["name"] == "T1", "shift unchanged after forbidden writes")

    print("\n[unauthenticated -> 401]")
    anon = client()
    for path in ("/api/shifts/", "/api/products/", "/api/downtime/", "/api/oee", "/api/losses/pareto",
                 "/api/reliability", "/api/reports/shift", "/api/overview", "/api/reason-codes/",
                 "/api/production-runs/", "/api/calendar-exceptions/"):
        r = anon.get(path)
        check(r.status_code == 401, f"anon GET {path} -> {r.status_code}")

    bad = [n for ok, n in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
