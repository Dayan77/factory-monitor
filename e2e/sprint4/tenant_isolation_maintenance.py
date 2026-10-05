"""Black-box tenant isolation + RBAC of every Sprint 4 endpoint against a running server (§7 item 8).

Tenant A = the dev-seeded tenant of that server (planner/technician/supervisor + plans, WOs, failures,
notifications); tenant B is freshly registered and creates one user per role through POST /api/users/.
Checks:
* every read/write by id from B (admin and every other role) on A's rows → 403/404, never 2xx; A unchanged;
* B's lists (plans, status, WOs, failures, notifications, users, tokens, webhooks, absences, components) never
  contain A's rows; KPIs/reliability of B are empty; B cannot reference A's ids in its own writes;
* notifications: B cannot read/mark A's notifications; A's emergency call reaches A's technician WebSocket and
  never B's; WebSocket /ws/sensors/{A} with B's token is refused;
* RBAC inside B: role matrix for planner-only/admin-only/technician actions; unauthenticated → 401.

    API=http://localhost:8120 python e2e/sprint4/tenant_isolation_maintenance.py
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import httpx
import websockets

API = os.getenv("API", "http://localhost:8120")
results: list[tuple[bool, str]] = []


def check(ok, name, got=None):
    results.append((bool(ok), name))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   got={got!r}"), flush=True)


def login(email, pw):
    r = httpx.post(f"{API}/api/auth/login", json={"email": email, "password": pw}, trust_env=False)
    r.raise_for_status()
    return r.json()["access_token"]


def C(token=None):
    return httpx.Client(base_url=API, timeout=30, trust_env=False,
                        headers={"Authorization": f"Bearer {token}"} if token else {})


def items(r):
    d = r.json()
    return d if isinstance(d, list) else d.get("items", d.get("rows", []))


ROLES = ("manager", "maintenance_planner", "technician", "production_supervisor", "operator")

# ── tenant A (seeded) ──────────────────────────────────────────────────────
A = {"admin": C(login("admin@factory-monitor.com", "Admin@1234")),
     "planner": C(login("planejador@factory-monitor.com", "Planner@1234")),
     "tech": C(login("tecnico@factory-monitor.com", "Tecnico@1234")),
     "sup": C(login("supervisor@factory-monitor.com", "Supervisor@1234"))}
a_tenant = A["admin"].get("/api/auth/me").json()["tenant_id"]
a_plan = items(A["planner"].get("/api/maintenance-plans/"))[0]
a_wo = next(w for w in items(A["planner"].get("/api/work-orders/")) if w["state"] not in ("completed", "closed", "cancelled"))
a_wo_detail = A["planner"].get(f"/api/work-orders/{a_wo['id']}").json()
a_fail = items(A["planner"].get("/api/failures/"))[0]
a_notif = A["tech"].get("/api/notifications/").json()["items"][0]
a_tech_id = A["tech"].get("/api/auth/me").json()["id"]
a_users = items(A["admin"].get("/api/users/"))
a_comp = A["planner"].get("/api/maintenance/components").json()[0]
a_machine = A["planner"].get(f"/api/machines/{a_wo['machine_id']}").json()
a_hook = A["admin"].post("/api/webhooks/", json={"url": "http://127.0.0.1:9180/a-hook", "secret": "a" * 24,
                                                 "events": ["work_order.created"]}).json()
a_tok = A["admin"].post("/api/api-tokens/", json={"name": "qa iso A", "scopes": ["read"]}).json()
a_abs = A["planner"].post("/api/maintenance/absences", json={"user_id": a_tech_id, "date": "2026-10-07", "hours": 2}).json()
a_down = items(A["sup"].get("/api/downtime/"))
print(f"tenant A {a_tenant[:8]}: plan {a_plan['id'][:8]}, WO {a_wo['number']}, failure {a_fail['id'][:8]}, notif {a_notif['id'][:8]}")

# ── tenant B (fresh) ───────────────────────────────────────────────────────
stamp = f"{int(time.time())}{uuid.uuid4().hex[:4]}"
b_admin_email = f"qa-s4-b-{stamp}@qa-factoryconnect.com.br"
r = httpx.post(f"{API}/api/auth/register", trust_env=False,
               json={"company_name": f"QA S4 B {stamp}", "admin_name": "QA B", "admin_email": b_admin_email, "admin_password": "QaIso#2026x"})
r.raise_for_status()
B = {"admin": C(login(b_admin_email, "QaIso#2026x"))}
b_tenant = B["admin"].get("/api/auth/me").json()["tenant_id"]
check(b_tenant != a_tenant, "tenant B registered")
# the self-service plan allows 5 users; the superadmin raises it (one user per role + extras)
C(login("superadmin@factory-monitor.com", "Super@1234")).patch(f"/api/admin/tenants/{b_tenant}", json={"max_users": 20}).raise_for_status()
for role in ROLES:
    email = f"qa-s4-{role}-{stamp}@qa-factoryconnect.com.br"
    rr = B["admin"].post("/api/users/", json={"email": email, "password": "QaRole#2026x", "name": f"B {role}", "role": role,
                                              "on_call": role == "technician"})
    check(rr.status_code == 201, f"B admin creates {role} via POST /api/users/", rr.text)
    B[role] = C(login(email, "QaRole#2026x"))
rr = B["admin"].post("/api/users/", json={"email": f"x-{stamp}@qa.com.br", "password": "QaRole#2026x", "name": "x",
                                          "role": "technician", "line_ids": [a_machine["line_id"]]})
check(rr.status_code in (404, 422), "B cannot create a user bound to A's line", rr.status_code)

# ── cross-tenant by id ─────────────────────────────────────────────────────
WO, PL, FA = a_wo["id"], a_plan["id"], a_fail["id"]
probes = [
    ("GET", f"/api/maintenance-plans/{PL}", None), ("PATCH", f"/api/maintenance-plans/{PL}", {"name": "hacked"}),
    ("DELETE", f"/api/maintenance-plans/{PL}", None),
    ("GET", f"/api/work-orders/{WO}", None), ("PATCH", f"/api/work-orders/{WO}", {"title": "hacked"}),
    ("POST", f"/api/work-orders/{WO}/transition", {"to": "cancelled", "cancel_reason": "other"}),
    ("POST", f"/api/work-orders/{WO}/approve-draft", None),
    ("POST", f"/api/work-orders/{WO}/labor", {"hours": 1}),
    ("PATCH", f"/api/work-orders/{WO}/tasks", {"task_id": (a_wo_detail["task_list"] or [{"id": "x"}])[0].get("id", "x"), "done": True}),
    ("POST", f"/api/work-orders/{WO}/replacements", {"position_id": a_comp.get("position_id") or "x", "removal_reason": "failure",
                                                     "new_component": {"serial_number": "HACK"}}),
    ("DELETE", f"/api/work-orders/{WO}/replacements/{uuid.uuid4()}", None),
    ("GET", f"/api/failures/{FA}", None), ("PATCH", f"/api/failures/{FA}", {"note": "hacked"}),
    ("POST", f"/api/notifications/{a_notif['id']}/read", None),
    ("GET", f"/api/webhooks/{a_hook['id']}", None), ("PATCH", f"/api/webhooks/{a_hook['id']}", {"is_active": False}),
    ("GET", f"/api/webhooks/{a_hook['id']}/deliveries", None), ("DELETE", f"/api/webhooks/{a_hook['id']}", None),
    ("DELETE", f"/api/api-tokens/{a_tok['id']}", None),
    ("DELETE", f"/api/maintenance/absences/{a_abs['id']}", None),
    ("GET", f"/api/users/{a_tech_id}", None), ("PATCH", f"/api/users/{a_tech_id}", {"on_call": False}),
    ("DELETE", f"/api/users/{a_tech_id}", None),
]
for who in ("admin",) + ROLES:
    bad = []
    for m, p, body in probes:
        rr = B[who].request(m, p, json=body) if body is not None else B[who].request(m, p)
        if rr.status_code not in (403, 404):
            bad.append((m, p.split("/api/")[1][:40], rr.status_code))
    check(not bad, f"B {who}: {len(probes)} by-id probes on A's rows → 403/404", bad)

# A unchanged
check(A["planner"].get(f"/api/maintenance-plans/{PL}").json()["name"] == a_plan["name"], "A's plan unchanged")
wo2 = A["planner"].get(f"/api/work-orders/{WO}").json()
check(wo2["state"] == a_wo["state"] and wo2["title"] == a_wo["title"] and wo2["labor_hours"] == a_wo_detail["labor_hours"], "A's WO unchanged")
check(A["planner"].get(f"/api/failures/{FA}").json().get("note") == a_fail.get("note"), "A's failure unchanged")
n2 = next(i for i in A["tech"].get("/api/notifications/").json()["items"] if i["id"] == a_notif["id"])
check(n2["read"] == a_notif["read"], "A's notification read state unchanged")
check(A["admin"].get(f"/api/webhooks/{a_hook['id']}").json()["is_active"] is True, "A's webhook unchanged")
check(any(t["id"] == a_tok["id"] for t in items(A["admin"].get("/api/api-tokens/"))), "A's API token still there")
check(A["tech"].get("/api/auth/me").json()["on_call"] is True, "A's technician still on call")

# ── B lists ────────────────────────────────────────────────────────────────
a_ids = {PL, WO, FA, a_notif["id"], a_hook["id"], a_tok["id"], a_abs["id"], a_comp.get("component_id"), *[u["id"] for u in a_users]}
lists = ["/api/maintenance-plans/", "/api/maintenance-plans/status", "/api/work-orders/", "/api/failures/", "/api/users/",
         "/api/api-tokens/", "/api/webhooks/", "/api/maintenance/absences", "/api/maintenance/components", "/api/notifications/"]
for p in lists:
    rr = B["admin"].get(p)
    blob = rr.text
    leak = [i for i in a_ids if i and i in blob]
    check(rr.status_code == 200 and not leak, f"B admin list {p}: no A rows", (rr.status_code, leak[:3]))
k = B["admin"].get("/api/maintenance/kpis").json()
check(k.get("backlog_count", 0) == 0 and not k.get("overdue_ids"), "B KPIs: empty backlog / overdue", {x: k.get(x) for x in ("backlog_count", "overdue_ids")})
for g in ("position", "machine", "component_type", "manufacturer", "manufacturer_model"):
    rel = B["admin"].get("/api/maintenance/reliability", params={"group_by": g}).json()
    check(rel.get("rows") == [], f"B reliability by {g}: no rows", len(rel.get("rows", [])))
rr = B["admin"].get("/api/maintenance/kpis", params={"plant_id": a_machine.get("plant_id") or ""})
check(rr.status_code in (404, 422) or (rr.status_code == 200 and rr.json().get("backlog_count", 0) == 0), "B KPIs with A's plant_id: 404/empty", rr.status_code)
rr = B["admin"].get("/api/maintenance-plans/status", params={"machine_id": a_wo["machine_id"]})
check(rr.status_code in (404, 422) or rr.json() == [], "B plan status filtered by A's machine: 404/empty", rr.status_code)

# ── B writes referencing A ids ─────────────────────────────────────────────
refs = [
    ("POST", "/api/maintenance-plans/", {"name": "x", "wo_type": "preventive", "estimated_hours": 1, "machine_id": a_wo["machine_id"], "interval_days": 30}),
    ("POST", "/api/work-orders/", {"type": "corrective", "title": "x", "estimated_hours": 1, "machine_id": a_wo["machine_id"]}),
    ("POST", "/api/failures/", {"machine_id": a_wo["machine_id"], "failed_at": "2026-10-01T10:00:00Z", "severity": "critical"}),
    ("POST", "/api/work-orders/emergency-call", {"machine_id": a_wo["machine_id"]}),
    ("POST", "/api/maintenance/absences", {"user_id": a_tech_id, "date": "2026-10-08", "hours": 2}),
]
if a_down:
    refs.append(("POST", "/api/work-orders/emergency-call", {"downtime_event_id": a_down[0]["id"]}))
for m, p, body in refs:
    rr = B["admin"].request(m, p, json=body)
    check(rr.status_code in (403, 404, 422), f"B admin {m} {p} with A's ids → 4xx", (rr.status_code, rr.text[:120]))

# ── RBAC inside B ──────────────────────────────────────────────────────────
b_line = None
mat = {
    ("POST", "/api/maintenance-plans/recompute"): {"admin", "manager", "maintenance_planner"},
    ("GET", "/api/maintenance/kpis"): {"admin", "manager", "maintenance_planner", "production_supervisor"},
    ("PUT", "/api/maintenance/settings"): {"admin", "manager"},   # targets/capacity: manager (workflows §1)
    ("GET", "/api/api-tokens/"): {"admin"},
    ("GET", "/api/webhooks/"): {"admin"},
    ("POST", "/api/users/"): {"admin"},
}
for (m, p), allowed in mat.items():
    got = {}
    for who in ("admin",) + ROLES:
        body = {} if m == "PUT" else ({"email": f"y{who}{stamp}@qa.com.br", "password": "QaRole#2026x", "name": "y"} if p == "/api/users/" else None)
        rr = B[who].request(m, p, json=body) if body is not None else B[who].request(m, p)
        got[who] = rr.status_code
    denied = {w for w, s in got.items() if s == 403}
    ok_roles = {w for w, s in got.items() if s < 400}
    check(denied == set(got) - allowed and ok_roles <= allowed, f"RBAC {m} {p}: allowed {sorted(allowed)}", got)
unauth = C()
bad = [(m, p) for m, p in [("GET", "/api/work-orders/"), ("GET", "/api/maintenance-plans/status"), ("GET", "/api/failures/"),
                           ("GET", "/api/notifications/"), ("GET", "/api/maintenance/kpis"), ("GET", "/api/users/"),
                           ("GET", "/api/webhooks/"), ("GET", "/api/api-tokens/"), ("POST", "/api/push/subscribe")]
       if unauth.request(m, p).status_code != 401]
check(not bad, "unauthenticated → 401 on every maintenance/notification endpoint", bad)
# read-only API token cannot write; token of B cannot read A
tk = B["admin"].post("/api/api-tokens/", json={"name": "ro", "scopes": ["read"]}).json()
ro = C(tk["token"])
check(ro.get("/api/work-orders/").status_code == 200, "B read-only API token reads B")
check(ro.post("/api/work-orders/", json={"type": "corrective", "title": "x", "estimated_hours": 1, "machine_id": a_wo["machine_id"]}).status_code == 403, "read-only token cannot write")
check(ro.get(f"/api/work-orders/{WO}").status_code == 404, "B API token cannot read A's WO")


# ── WebSocket / notification isolation ─────────────────────────────────────
async def ws_part():
    ws_url = API.replace("http", "ws")
    b_tech_tok = B["technician"].headers["Authorization"].split()[1]
    a_tech_tok = A["tech"].headers["Authorization"].split()[1]
    try:
        async with websockets.connect(f"{ws_url}/ws/sensors/{a_tenant}", subprotocols=["bearer", b_tech_tok]) as ws:
            await asyncio.wait_for(ws.recv(), 3)
            check(False, "B token on /ws/sensors/{A} refused", "connected and received")
    except Exception as e:  # noqa: BLE001
        check(True, f"B token on /ws/sensors/{{A}} refused ({type(e).__name__})")
    got_a, got_b = [], []

    async def listen(url, tok, sink):
        async with websockets.connect(url, subprotocols=["bearer", tok]) as ws:
            while True:
                m = json.loads(await ws.recv())
                if m.get("type") == "notification":
                    sink.append(m)
    ta = asyncio.create_task(listen(f"{ws_url}/ws/sensors/{a_tenant}", a_tech_tok, got_a))
    tb = asyncio.create_task(listen(f"{ws_url}/ws/sensors/{b_tenant}", b_tech_tok, got_b))
    await asyncio.sleep(2)
    async with httpx.AsyncClient(base_url=API, trust_env=False, headers=dict(A["sup"].headers)) as c:
        rr = await c.post("/api/work-orders/emergency-call", json={"machine_id": a_wo["machine_id"], "title": "QA isolamento", "note": "QA"})
    await asyncio.sleep(4)
    ta.cancel(); tb.cancel()
    check(any(m["notification"]["kind"] == "emergency_call" for m in got_a), "A's emergency call reaches A's technician WebSocket", (rr.status_code, got_a))
    check(not got_b, "…and nothing reaches B's technician WebSocket", got_b)
    bn = B["technician"].get("/api/notifications/").json()
    check(not any("QA isolamento" in json.dumps(i) for i in bn["items"]), "B technician notification list has nothing of A")
    return rr.json().get("id") if rr.status_code in (200, 201) else None

eid = asyncio.run(ws_part())
# cleanup in A
if eid:
    A["planner"].post(f"/api/work-orders/{eid}/transition", json={"to": "cancelled", "cancel_reason": "duplicate"})
A["admin"].delete(f"/api/webhooks/{a_hook['id']}")
A["admin"].delete(f"/api/api-tokens/{a_tok['id']}")
A["planner"].delete(f"/api/maintenance/absences/{a_abs['id']}")

fails = [n for ok, n in results if not ok]
print(f"\n{len(results) - len(fails)}/{len(results)} checks passed" + (f"; FAILED: {fails}" if fails else ""))
raise SystemExit(1 if fails else 0)
