"""Black-box tenant isolation of every Sprint 2 endpoint against a running server.

Tenant A = the dev seed's demo tenant (Planta Manaus). Tenant B = a freshly registered tenant. Every read,
write and cross-tenant reference from B to A's ids must answer 404 (or 422 for invalid references) and must
not change A's data; lists and the tree must not leak A's rows. Also checks RBAC (operator can read, cannot
write) inside A and unauthenticated access (401).

Env: API (http://localhost:8000), EMAIL/PASSWORD of tenant A's admin. Exit code 0 = all checks passed.
"""
from __future__ import annotations

import os
import sys
import time

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


def main() -> int:
    a = client(login(os.getenv("EMAIL", "admin@factory-monitor.com"), os.getenv("PASSWORD", "Admin@1234")))
    stamp = int(time.time())
    email_b = f"qa-iso-{stamp}@qa-factoryconnect.com.br"
    httpx.post(f"{API}/api/auth/register", json={"company_name": f"QA Iso {stamp}", "admin_name": "QA",
                                                 "admin_email": email_b, "admin_password": "QaIsola#2026x"}).raise_for_status()
    b = client(login(email_b, "QaIsola#2026x"))

    tree = a.get("/api/plants/tree").json()
    plant = next(p for p in tree if p["name"] == "Planta Manaus")
    line, machine = next((ln, m) for ln in plant["lines"] for m in ln["machines"] if m["positions_count"] > 0)
    pos = a.get("/api/positions/", params={"machine_id": machine["id"]}).json()[0]
    comp_id = pos["installed_component_id"]
    tag = a.get("/api/tags/", params={"position_id": pos["id"]}).json()
    before = a.get(f"/api/positions/{pos['id']}").json()

    # B creates its own minimal hierarchy (to try cross references)
    bp = b.post("/api/plants/", json={"name": "B plant"}).json()
    bl = b.post("/api/lines/", json={"name": "B line", "plant_id": bp["id"]}).json()
    bm = b.post("/api/machines/", json={"name": "B machine", "line_id": bl["id"]}).json()
    bpos = b.post("/api/positions/", json={"machine_id": bm["id"], "name": "B pos", "component_type": "pneumatic_cylinder"}).json()
    bcomp = b.post("/api/components/", json={"component_type": "pneumatic_cylinder", "serial_number": f"B-{stamp}"}).json()

    print("\n[reads by id from tenant B -> 404]")
    for path in (f"/api/plants/{plant['id']}", f"/api/lines/{line['id']}", f"/api/lines/{line['id']}/production",
                 f"/api/machines/{machine['id']}", f"/api/machines/{machine['id']}/usage",
                 f"/api/positions/{pos['id']}", f"/api/components/{comp_id}"):
        r = b.get(path)
        check(r.status_code == 404, f"GET {path.split('/api/')[1].split('/')[0]} … -> {r.status_code}", r.text[:120])
    for params in ({"position_id": pos["id"], "measure": "cycles"}, {"machine_id": machine["id"], "measure": "hours"},
                   {"component_id": comp_id, "measure": "cycles"},
                   {"machine_id": machine["id"], "measure": "good"}):
        r = b.get("/api/usage/history", params=params)
        check(r.status_code == 404, f"GET usage/history {params} -> {r.status_code}", r.text[:120])

    print("\n[lists from tenant B do not leak A]")
    tb = b.get("/api/plants/tree").json()
    check(all(p["id"] != plant["id"] for p in tb) and len(tb) == 1, "tree only has B's plant", [p["name"] for p in tb])
    check(all(p["id"] != plant["id"] for p in b.get("/api/plants/").json()), "plants list")
    check(b.get("/api/lines/", params={"plant_id": plant["id"]}).json() == [], "lines?plant_id=A -> []")
    check(b.get("/api/positions/", params={"machine_id": machine["id"]}).json() == [], "positions?machine_id=A -> []")
    check(b.get("/api/components/", params={"position_id": pos["id"]}).json() == [], "components?position_id=A -> []")
    ser = a.get(f"/api/components/{comp_id}").json().get("serial_number")
    check(b.get("/api/components/", params={"serial_number": ser}).json() == [], "components?serial_number=A's -> []")
    check(all(c["id"] != comp_id for c in b.get("/api/components/", params={"include_removed": "true"}).json()), "components list")
    check(b.get("/api/tags/", params={"position_id": pos["id"]}).json() == [], "tags?position_id=A -> []")
    check(b.get("/api/tags/", params={"semantic_role": "cycle_counter"}).json() == [], "tags?semantic_role -> only B (none)")

    print("\n[writes on A's ids from tenant B -> 404]")
    for verb, path, body in (
        ("patch", f"/api/plants/{plant['id']}", {"name": "hacked"}),
        ("delete", f"/api/plants/{plant['id']}", None),
        ("patch", f"/api/lines/{line['id']}", {"output_machine_id": None}),
        ("patch", f"/api/positions/{pos['id']}", {"name": "hacked"}),
        ("delete", f"/api/positions/{pos['id']}", None),
        ("post", f"/api/positions/{pos['id']}/install", {"component_id": bcomp["id"]}),
        ("patch", f"/api/components/{comp_id}", {"model": "hacked"}),
        ("post", f"/api/components/{comp_id}/remove", {"reason": "failure"}),
    ):
        r = getattr(b, verb)(path, **({"json": body} if body is not None else {}))
        check(r.status_code == 404, f"{verb.upper()} {path.replace(API, '')[:60]} -> {r.status_code}", r.text[:120])

    print("\n[cross-tenant references in B's request bodies -> 404/422, nothing created]")
    for verb, path, body in (
        ("post", "/api/lines/", {"name": "x", "plant_id": plant["id"]}),
        ("patch", f"/api/lines/{bl['id']}", {"output_machine_id": machine["id"]}),
        ("patch", f"/api/lines/{bl['id']}", {"bottleneck_machine_id": machine["id"]}),
        ("post", "/api/positions/", {"machine_id": machine["id"], "name": "x", "component_type": "valve"}),
        ("patch", f"/api/positions/{bpos['id']}", {"counter_tag_id": tag[0]["id"] if tag else "00000000-0000-0000-0000-000000000000"}),
        ("post", f"/api/positions/{bpos['id']}/install", {"component_id": comp_id}),
        ("post", "/api/components/", {"component_type": "pneumatic_cylinder", "position_id": pos["id"]}),
        ("post", "/api/tags/", {"name": "x", "protocol": "modbus_tcp", "address": "unit=1;hr=1:uint16",
                                "semantic_role": "cycle_counter", "position_id": pos["id"]}),
        ("post", "/api/tags/", {"name": "x", "protocol": "modbus_tcp", "address": "unit=1;hr=1:uint16",
                                "semantic_role": "cycle_counter", "machine_id": bm["id"], "position_id": pos["id"]}),
    ):
        r = getattr(b, verb)(path, json=body)
        check(r.status_code in (404, 422), f"{verb.upper()} {path[:45]} {list(body)} -> {r.status_code}", r.text[:150])

    print("\n[recompute from B touches only B]")
    r = b.post("/api/usage/recompute", params={"apply": "true"})
    check(r.status_code == 200 and all(d["entity_id"] != pos["id"] for d in r.json()["differences"]), "recompute(B)", r.text[:150])
    after = a.get(f"/api/positions/{pos['id']}").json()
    check(after["name"] == before["name"] and after["installed_component_id"] == before["installed_component_id"]
          and after["usage"]["cycles_total"] >= before["usage"]["cycles_total"], "A's position unchanged")
    check(a.get(f"/api/plants/{plant['id']}").json()["name"] == "Planta Manaus", "A's plant unchanged")

    print("\n[recompute background jobs are tenant-scoped]")
    r = b.post("/api/usage/recompute", params={"apply": "false", "background": "true"})
    check(r.status_code == 202 and r.json().get("job_id"), f"B starts a background dry-run -> {r.status_code}", r.text[:150])
    if r.status_code == 202:
        job = r.json()["job_id"]
        for _ in range(60):
            rb = b.get(f"/api/usage/recompute/jobs/{job}")
            if rb.status_code != 200 or rb.json()["status"] not in ("pending", "running", "queued"):
                break
            time.sleep(0.5)
        check(rb.status_code == 200, f"B reads its own job -> {rb.status_code} {rb.json().get('status') if rb.status_code == 200 else ''}")
        ra = a.get(f"/api/usage/recompute/jobs/{job}")
        check(ra.status_code == 404, f"A reads B's job id -> {ra.status_code}", ra.text[:120])
        check(client().get(f"/api/usage/recompute/jobs/{job}").status_code == 401, "no token reads job -> 401")

    print("\n[auth / RBAC]")
    anon = client()
    for path in ("/api/plants/tree", f"/api/positions/{pos['id']}", "/api/usage/history", "/api/components/"):
        r = anon.get(path)
        check(r.status_code == 401, f"no token GET {path[:40]} -> {r.status_code}")
    r = anon.post("/api/usage/recompute")
    check(r.status_code == 401, f"no token POST recompute -> {r.status_code}")
    users = a.get("/api/users/")
    if users.status_code != 200:
        print("  (operator RBAC not checked here: no tenant user-management endpoint; covered by server/tests/test_assets_api.py)")
    else:
        op_email = f"qa-op-{stamp}@qa-factoryconnect.com.br"
        r = a.post("/api/users/", json={"email": op_email, "name": "QA Operador", "password": "QaOper#2026x", "role": "operator"})
        if r.status_code in (200, 201):
            op = client(login(op_email, "QaOper#2026x"))
            check(op.get("/api/plants/tree").status_code == 200, "operator can read tree")
            check(op.get(f"/api/positions/{pos['id']}").status_code == 200, "operator can read position")
            for verb, path, body in (("post", "/api/plants/", {"name": "op"}),
                                     ("patch", f"/api/positions/{pos['id']}", {"name": "op"}),
                                     ("post", f"/api/components/{comp_id}/remove", {"reason": "failure"}),
                                     ("post", "/api/usage/recompute", None)):
                rr = getattr(op, verb)(path, **({"json": body} if body is not None else {}))
                check(rr.status_code == 403, f"operator {verb.upper()} {path[:40]} -> {rr.status_code}")
            a.delete(f"/api/users/{r.json()['id']}")
        else:
            print(f"  (skipped operator RBAC: could not create user: {r.status_code} {r.text[:100]})")
    failed = [n for ok, n in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
