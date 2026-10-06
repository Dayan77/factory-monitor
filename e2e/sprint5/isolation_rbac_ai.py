"""Sprint 5 black-box checks against a RUNNING server (real HTTP): tenant isolation + RBAC of every new endpoint
(/api/predictive/*, /api/gateways/*, /api/ai/*) and the AI assistant behaviour with a scripted model.

Server: e2e/sprint5/ai_qa_server.py (real app, QA scripted model injected), seeded with scripts/seed_dev.py and
e2e/sprint5/seed_predictive_f1.py (tenant A has a predictive monitor + alert).

    API=http://localhost:8161 WEB_SRC=web/src python e2e/sprint5/isolation_rbac_ai.py

Tenant A = dev seed (admin/planner/technician/supervisor + manager/operator created here); tenant B = freshly
registered with one user per role. Checks:
* every id-based predictive/gateway/AI call from every B role on A's rows → 403/404 (never 2xx), A unchanged;
  B's lists never contain A's rows; gateway device token of B cannot read/heartbeat A's gateway;
* RBAC matrix inside A for predictive (read/config/ack/close/convert/evaluation/recompute), gateways (manager read,
  admin write), AI audit (admin), unauthenticated → 401;
* AI: tools offered per role (a technician's model is never handed get_maintenance_kpis/get_reliability; a forced
  call is refused by the route → 403 → refusal no_permission); invented number → verifier rejects → template;
  grounded answer verified; every citation URL path is a route of the web app; B's assistant cannot read A's data
  (A's line id → not found, A's conversation → 404); SSE stream ends with `done` carrying the verified answer.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid

import httpx

API = os.getenv("API", "http://localhost:8161")
WEB_SRC = os.getenv("WEB_SRC", os.path.join(os.path.dirname(__file__), "..", "..", "web", "src"))
results: list[tuple[bool, str]] = []


def check(ok, name, got=None):
    results.append((bool(ok), name))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   got={str(got)[:300]!r}"), flush=True)


def login(email, pw):
    r = httpx.post(f"{API}/api/auth/login", json={"email": email, "password": pw}, trust_env=False)
    r.raise_for_status()
    return r.json()["access_token"]


def C(token=None):
    return httpx.Client(base_url=API, timeout=60, trust_env=False,
                        headers={"Authorization": f"Bearer {token}"} if token else {})


def items(r):
    d = r.json()
    return d if isinstance(d, list) else d.get("items", d.get("rows", []))


ROLES = ("manager", "maintenance_planner", "technician", "production_supervisor", "operator")
stamp = f"{int(time.time())}{uuid.uuid4().hex[:4]}"
SUPER = C(login("superadmin@factory-monitor.com", "Super@1234"))

# ── tenant A ───────────────────────────────────────────────────────────────
A = {"admin": C(login("admin@factory-monitor.com", "Admin@1234")),
     "maintenance_planner": C(login("planejador@factory-monitor.com", "Planner@1234")),
     "technician": C(login("tecnico@factory-monitor.com", "Tecnico@1234")),
     "production_supervisor": C(login("supervisor@factory-monitor.com", "Supervisor@1234"))}
a_tenant = A["admin"].get("/api/auth/me").json()["tenant_id"]
SUPER.patch(f"/api/admin/tenants/{a_tenant}", json={"max_users": 50}).raise_for_status()
for role in ("manager", "operator"):
    email = f"qa-s5-a-{role}-{stamp}@qa-factoryconnect.com.br"
    r = A["admin"].post("/api/users/", json={"email": email, "password": "QaRole#2026x", "name": f"A {role}", "role": role})
    r.raise_for_status()
    A[role] = C(login(email, "QaRole#2026x"))
P = A["maintenance_planner"]
mons = P.get("/api/predictive/monitors").json()
a_mon = next(m for m in mons if m["feature"] == "stroke_time")
a_alert = P.get("/api/predictive/alerts", params={"state": ["open", "acknowledged", "converted", "closed"]}).json()[0]
a_rule = P.get("/api/predictive/rules", params={"monitor_id": a_mon["id"]}).json()
a_rule = (a_rule if isinstance(a_rule, list) else a_rule.get("items", []))[0]
a_gw = A["admin"].post("/api/gateways/", json={"name": f"QA A gw {stamp}", "plant_id": A["admin"].get("/api/plants/").json()[0]["id"]})
check(a_gw.status_code == 201, "A admin creates a gateway", a_gw.text)
a_gw = a_gw.json()
a_code = A["admin"].post(f"/api/gateways/{a_gw['id']}/enroll-token").json()
a_enr = httpx.post(f"{API}/api/gateways/enroll", json={"code": a_code.get("code") or a_code.get("enroll_code")}, trust_env=False)
check(a_enr.status_code == 200, "A device enrolls with the one-time code", a_enr.text)
a_dev = a_enr.json()
a_conv = P.post("/api/ai/chat", json={"message": "O que está parado agora?"}).json()
check(a_conv.get("conversation_id"), "A planner has an AI conversation", a_conv)
a_tree = P.get("/api/plants/tree").json()
a_line = a_tree[0]["lines"][0]["id"]
a_machine = a_mon["machine_id"]
a_alert_before = P.get(f"/api/predictive/alerts/{a_alert['id']}").json()
a_mon_before = P.get(f"/api/predictive/monitors/{a_mon['id']}").json()
print(f"tenant A {a_tenant[:8]}: monitor {a_mon['id'][:8]}, alert {a_alert['id'][:8]} ({a_alert['state']}), "
      f"rule {a_rule['id'][:8]}, gateway {a_gw['id'][:8]}")

# ── tenant B ───────────────────────────────────────────────────────────────
b_admin_email = f"qa-s5-b-{stamp}@qa-factoryconnect.com.br"
r = httpx.post(f"{API}/api/auth/register", trust_env=False, json={
    "company_name": f"QA S5 B {stamp}", "admin_name": "QA B", "admin_email": b_admin_email, "admin_password": "QaIso#2026x"})
r.raise_for_status()
B = {"admin": C(login(b_admin_email, "QaIso#2026x"))}
b_tenant = B["admin"].get("/api/auth/me").json()["tenant_id"]
check(b_tenant != a_tenant, "tenant B registered")
SUPER.patch(f"/api/admin/tenants/{b_tenant}", json={"max_users": 20}).raise_for_status()
for role in ROLES:
    email = f"qa-s5-{role}-{stamp}@qa-factoryconnect.com.br"
    rr = B["admin"].post("/api/users/", json={"email": email, "password": "QaRole#2026x", "name": f"B {role}", "role": role})
    rr.raise_for_status()
    B[role] = C(login(email, "QaRole#2026x"))
b_plant = B["admin"].post("/api/plants/", json={"name": f"Planta B {stamp}", "timezone": "America/Manaus"})
b_plant_id = b_plant.json()["id"] if b_plant.status_code in (200, 201) else None
b_gw = B["admin"].post("/api/gateways/", json={"name": f"QA B gw {stamp}", "plant_id": b_plant_id}).json()
b_code = B["admin"].post(f"/api/gateways/{b_gw['id']}/enroll-token").json()
b_dev = httpx.post(f"{API}/api/gateways/enroll", json={"code": b_code.get("code") or b_code.get("enroll_code")}, trust_env=False).json()

# ── cross-tenant by id ─────────────────────────────────────────────────────
M, AL, RU, GW = a_mon["id"], a_alert["id"], a_rule["id"], a_gw["id"]
probes = [
    ("GET", f"/api/predictive/monitors/{M}", None), ("PATCH", f"/api/predictive/monitors/{M}", {"enabled": False}),
    ("POST", f"/api/predictive/monitors/{M}/rebaseline", {"reason": "hack"}),
    ("POST", f"/api/predictive/monitors/{M}/process", None),
    ("PATCH", f"/api/predictive/rules/{RU}", {"value": 1.0}), ("DELETE", f"/api/predictive/rules/{RU}", None),
    ("POST", "/api/predictive/rules", {"monitor_id": M, "code": "HACK", "type": "threshold", "value": 1, "sustained_for": 1,
                                       "severity": "critical"}),
    ("GET", f"/api/predictive/alerts/{AL}", None),
    ("POST", f"/api/predictive/alerts/{AL}/transition", {"to": "acknowledged"}),
    ("POST", f"/api/predictive/alerts/{AL}/transition", {"to": "closed", "resolution": "false_alarm", "comment": "x"}),
    ("POST", f"/api/predictive/alerts/{AL}/convert", {}),
    ("POST", "/api/predictive/monitors", {"position_id": a_mon["position_id"], "feature": "stroke_time", "window_cycles": 100}),
    ("POST", "/api/predictive/condition-plans", {"position_id": a_mon["position_id"], "name": "hack", "min_severity": "warning"}),
    ("PUT", "/api/predictive/production-plan", {"machine_id": a_machine, "days": [{"date": "2026-10-10", "parts": 1}]}),
    ("GET", f"/api/gateways/{GW}", None), ("PATCH", f"/api/gateways/{GW}", {"name": "hacked"}),
    ("POST", f"/api/gateways/{GW}/enroll-token", None), ("POST", f"/api/gateways/{GW}/revoke", None),
    ("PUT", f"/api/gateways/{GW}/tags", {"tags": []}), ("GET", f"/api/gateways/{GW}/config-preview", None),
    ("GET", f"/api/gateways/{GW}/mqtt", None), ("DELETE", f"/api/gateways/{GW}", None),
    ("GET", f"/api/ai/conversations/{a_conv['conversation_id']}", None),
    ("DELETE", f"/api/ai/conversations/{a_conv['conversation_id']}", None),
    ("POST", "/api/ai/chat", {"message": "continua", "conversation_id": a_conv["conversation_id"]}),
]
for role, cl in B.items():
    bad = []
    for meth, path, body in probes:
        rr = cl.request(meth, path, json=body)
        if rr.status_code < 300 or rr.status_code >= 500:
            bad.append((meth, path, rr.status_code, rr.text[:120]))
    check(not bad, f"B {role}: {len(probes)} id-based calls on A's predictive/gateway/AI rows → 4xx", bad)
# query-param references to A's ids
for role in ("admin", "manager", "maintenance_planner", "technician"):
    cl = B[role]
    f = cl.get("/api/predictive/features", params={"position_id": a_mon["position_id"]})
    check(f.status_code in (403, 404) or not (f.json().get("series") or []), f"B {role}: features of A's position → nothing",
          (f.status_code, f.text[:200]))
    bd = cl.get("/api/predictive/badges", params={"machine_id": a_machine})
    check(bd.status_code in (403, 404) or not bd.json() or not any(bd.json().values() if isinstance(bd.json(), dict) else bd.json()),
          f"B {role}: badges of A's machine → nothing", (bd.status_code, bd.text[:200]))
for role in ("admin", "manager", "maintenance_planner"):
    cl = B[role]
    lst = [x["id"] for x in cl.get("/api/predictive/monitors").json()]
    check(M not in lst, f"B {role}: A's monitor not in B's monitor list")
    al = [x["id"] for x in cl.get("/api/predictive/alerts", params={"state": ["open", "acknowledged", "converted", "closed"]}).json()]
    check(AL not in al, f"B {role}: A's alert not in B's alert list")
    ev = cl.get("/api/predictive/evaluation")
    check(ev.status_code == 200 and json.dumps(ev.json()).find(M) < 0 and (ev.json().get("true_positives", 0) == 0),
          f"B {role}: evaluation scoped to B (no A positives)", ev.text[:200])
for role in ("admin", "manager"):
    gws = [g["id"] for g in B[role].get("/api/gateways/").json()]
    check(GW not in gws and b_gw["id"] in gws, f"B {role}: gateway list has B's gateway only")
convs = [c["id"] for c in items(B["admin"].get("/api/ai/conversations"))]
check(a_conv["conversation_id"] not in convs, "B admin: A's conversation not listed")
aud = B["admin"].get("/api/ai/audit")
check(aud.status_code == 200 and a_conv["conversation_id"] not in aud.text, "B admin: AI audit has no A rows", aud.text[:200])
rc = B["admin"].post("/api/predictive/recompute", json={"monitor_id": M})
check(rc.status_code in (403, 404) or M not in rc.text, "B admin: recompute of A's monitor refused / empty", rc.text[:200])
# device tokens: B's gateway token cannot read or heartbeat A's gateway
hdr = {"Authorization": f"Gateway {b_dev['token']}"}
r1 = httpx.get(f"{API}/api/gateways/{GW}/config", headers=hdr, trust_env=False)
r2 = httpx.post(f"{API}/api/gateways/{GW}/heartbeat", headers=hdr, json={"version": "x", "uptime_s": 1, "buffer_depth": 0,
                                                                        "errors": []}, trust_env=False)
check(r1.status_code in (401, 403, 404) and r2.status_code in (401, 403, 404), "B device token on A's gateway → refused",
      (r1.status_code, r2.status_code))
r3 = httpx.get(f"{API}/api/gateways/{GW}/config", headers={"Authorization": f"Bearer {login(b_admin_email, 'QaIso#2026x')}"},
               trust_env=False)
check(r3.status_code in (401, 403, 404), "user JWT is not a gateway token", r3.status_code)
r4 = httpx.get(f"{API}/api/gateways/{GW}/config", headers={"Authorization": f"Gateway {a_dev['token']}"}, trust_env=False)
check(r4.status_code == 200 and r4.json().get("gateway_id") == GW, "A's own device token reads A's config", r4.status_code)
# A unchanged
check(P.get(f"/api/predictive/alerts/{AL}").json()["state"] == a_alert_before["state"], "A's alert state unchanged")
am = P.get(f"/api/predictive/monitors/{M}").json()
check(am.get("enabled") == a_mon_before.get("enabled") and len(am["baselines"]) == len(a_mon_before["baselines"]),
      "A's monitor unchanged (enabled, baselines)")
check(A["admin"].get(f"/api/gateways/{GW}").json()["name"] == a_gw["name"], "A's gateway unchanged")
check(P.get(f"/api/ai/conversations/{a_conv['conversation_id']}").status_code == 200, "A's conversation still there")

# ── RBAC inside A ──────────────────────────────────────────────────────────
def st(role, meth, path, body=None, params=None):
    return A[role].request(meth, path, json=body, params=params).status_code


READ = {"admin", "manager", "maintenance_planner", "technician", "production_supervisor"}
for role in A:
    s = st(role, "GET", "/api/predictive/monitors")
    check((s == 200) == (role in READ), f"A {role}: GET /predictive/monitors → {s}")
    s = st(role, "GET", "/api/predictive/alerts")
    check((s == 200) == (role in READ), f"A {role}: GET /predictive/alerts → {s}")
    s = st(role, "GET", "/api/predictive/badges", params={"machine_id": a_machine})
    check(s == 200, f"A {role}: GET /predictive/badges (any role) → {s}")
    s = st(role, "POST", "/api/predictive/monitors", {"position_id": str(uuid.uuid4()), "feature": "stroke_time"})
    check((s in (404, 422)) == (role in {"admin", "manager", "maintenance_planner"}) and (s != 201),
          f"A {role}: POST /predictive/monitors (config roles reach validation, others 403) → {s}")
    s = st(role, "GET", "/api/predictive/evaluation")
    check((s == 200) == (role in {"admin", "manager", "maintenance_planner"}), f"A {role}: GET /predictive/evaluation → {s}")
    s = st(role, "POST", "/api/predictive/recompute", {"monitor_id": M})
    check((s == 200) == (role == "admin"), f"A {role}: POST /predictive/recompute → {s}")
    s = st(role, "GET", "/api/gateways/")
    check((s == 200) == (role in {"admin", "manager"}), f"A {role}: GET /gateways/ → {s}")
    s = st(role, "POST", "/api/gateways/", {"name": f"x {role}", "plant_id": None})
    check((s in (201, 422)) == (role == "admin"), f"A {role}: POST /gateways/ → {s}")
    s = st(role, "GET", "/api/ai/audit")
    check((s == 200) == (role == "admin"), f"A {role}: GET /ai/audit → {s}")
for path in ("/api/predictive/monitors", "/api/predictive/alerts", "/api/gateways/", "/api/ai/conversations", "/api/ai/status",
             "/api/predictive/badges"):
    s = httpx.get(f"{API}{path}", trust_env=False).status_code
    check(s == 401, f"unauthenticated GET {path} → {s}")
s = httpx.post(f"{API}/api/ai/chat", json={"message": "oi"}, trust_env=False).status_code
check(s == 401, f"unauthenticated POST /api/ai/chat → {s}")

# ── AI assistant through real HTTP (QA scripted model) ─────────────────────
routes_src = open(os.path.join(WEB_SRC, "App.tsx"), encoding="utf-8").read()
ROUTES = set(re.findall(r'path="([^"]+)"', routes_src)) | set(re.findall(r"path:\s*['\"]([^'\"]+)['\"]", routes_src))


def route_ok(url: str) -> bool:
    p = url.split("?")[0].split("#")[0]
    for r in ROUTES:
        rr = r if r.startswith("/") else "/" + r
        rx = "^" + re.sub(r":[A-Za-z_]+", "[^/]+", rr.rstrip("/*")) + "/?$"
        if re.match(rx, p):
            return True
    return False


def chat(cl, msg, **kw):
    r = cl.post("/api/ai/chat", json={"message": msg, **kw})
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


CATALOG_ROLES = {"get_component_life": READ - {"admin"}, "get_maintenance_due": READ - {"admin"},
                 "get_maintenance_kpis": {"maintenance_planner", "manager", "production_supervisor"},
                 "get_reliability": {"maintenance_planner", "manager", "production_supervisor"},
                 "search_failures": READ - {"admin"}, "get_predictive_alerts": READ - {"admin"},
                 "get_condition_trend": READ - {"admin"}}
for role in A:
    s, lst = chat(A[role], "QA-LIST")
    offered = set((lst.get("reply") or "").split("ferramentas: ")[-1].split(", ")) if s == 200 else set()
    wrong = [t for t, rs in CATALOG_ROLES.items() if (t in offered) != (role in rs or role == "admin")]
    check(s == 200 and "get_oee" in offered and not wrong, f"A {role}: tools offered to the model follow the catalog roles",
          (wrong, sorted(offered)))
s, tech = chat(A["technician"], 'QA-TOOL get_maintenance_kpis {}')
tc = (tech.get("tool_calls") or [{}])[0] if isinstance(tech, dict) else {}
check(s == 200 and tc.get("name") == "get_maintenance_kpis" and tc.get("status") in ("forbidden", "error", "no_permission", "denied")
      and tech.get("refused") and "no_permission" in (tech.get("refusal_reasons") or [tech.get("refusal_reason")]),
      "technician: forced get_maintenance_kpis → route 403 → refusal no_permission", tech)
check(not re.search(r"\d", (tech.get("reply") or "").replace("[1]", "")) or tech.get("answer_source") in ("refusal", "template"),
      "technician: no KPI number leaked in the refusal", tech.get("reply") if isinstance(tech, dict) else tech)
s, tech2 = chat(A["technician"], 'QA-TOOL get_reliability {}')
check(s == 200 and tech2.get("refused") and "no_permission" in (tech2.get("refusal_reasons") or []),
      "technician: forced get_reliability → no_permission", tech2)
s, op = chat(A["operator"], 'QA-TOOL get_predictive_alerts {}')
check(s == 200 and op.get("refused") and "no_permission" in (op.get("refusal_reasons") or []),
      "operator: forced get_predictive_alerts → no_permission", op)
s, mgr = chat(A["manager"], 'QA-GROUNDED get_maintenance_kpis {}')
check(s == 200 and mgr.get("verified") and not mgr.get("refused") and mgr["tool_calls"][0]["status"] == "ok",
      "manager: get_maintenance_kpis allowed, answer verified", mgr)
s, inv = chat(A["maintenance_planner"], "QA-INVENT")
check(s == 200 and "87,3" not in (inv.get("reply") or "") and inv.get("answer_source") == "template",
      "invented number (87,3 %) rejected by the verifier → template answer, number never shown", inv)
check(any("confer" in w.lower() or "verific" in w.lower() or "modelo" in w.lower() for w in inv.get("warnings") or []),
      "template fallback carries a warning", inv.get("warnings"))
s, gr = chat(A["maintenance_planner"], 'QA-GROUNDED get_predictive_alerts {}')
check(s == 200 and gr.get("verified") is True and gr.get("answer_source") == "model", "grounded predictive answer verified", gr)
s, oee = chat(A["production_supervisor"], "Qual o OEE desta linha hoje por turno?", context={"line_id": a_line, "route": "/oee"})
check(s == 200 and oee.get("verified") and oee["tool_calls"] and oee["tool_calls"][0]["name"] == "get_oee",
      "supervisor: OEE question → get_oee, verified", oee)
cites = [c for x in (mgr, gr, oee, a_conv) if isinstance(x, dict) for c in x.get("citations") or []]
bad = [c["url"] for c in cites if not route_ok(c["url"])]
check(cites and not bad, f"{len(cites)} citation URLs all map to web routes", bad)
# B's assistant cannot see A's data
s, bx = chat(B["manager"], "Qual o OEE desta linha hoje?", context={"line_id": a_line, "route": "/oee"})
txt = json.dumps(bx)
check(s in (200, 404, 422) and a_line not in "".join(c.get("url", "") for c in (bx.get("citations") or [] if isinstance(bx, dict) else []))
      and (not isinstance(bx, dict) or not bx.get("verified") or all(tc.get("status") != "ok" for tc in bx.get("tool_calls") or [])),
      "B manager with A's line in context → no A data", txt[:400])
s, bt = chat(B["manager"], f'QA-TOOL get_condition_trend {{"position_id": "{a_mon["position_id"]}", "feature": "stroke_time"}}')
check(s == 200 and all(tc.get("status") != "ok" or not tc.get("rows") for tc in bt.get("tool_calls") or []),
      "B manager: get_condition_trend on A's position → not found / empty", bt)
s, ba = chat(B["manager"], 'QA-TOOL get_predictive_alerts {}')
check(s == 200 and AL not in json.dumps(ba), "B manager: get_predictive_alerts never returns A's alert", ba)
# SSE stream
with A["maintenance_planner"].stream("POST", "/api/ai/chat/stream", json={"message": "O que está parado agora?"}) as rs:
    body = "".join(rs.iter_text())
events = re.findall(r"^event:\s*(\S+)", body, re.M)
check(rs.status_code == 200 and events[:1] == ["start"] and "tool_call" in events and "verifying" in events and events[-1] == "done",
      f"SSE: start → tool_call → verifying → … → done ({events})", body[:300])
check(events.index("verifying") < events.index("delta") if "delta" in events else False,
      "SSE: answer text (delta) only after the verifier step")

print(f"\n{sum(ok for ok, _ in results)}/{len(results)} passed")
raise SystemExit(0 if all(ok for ok, _ in results) else 1)
