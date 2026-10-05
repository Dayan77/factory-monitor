"""Emergency call on a line with NO on-call technician (dev seed: Linha de Usinagem) must reach someone, or at
least tell the caller nobody was reached. maintenance_workflows §5: "técnicos de plantão da área, supervisor".

    API=http://localhost:8120 python e2e/sprint4/emergency_no_oncall.py
"""
import os
import sys

import httpx

API = os.getenv("API", "http://localhost:8120")
c = httpx.Client(base_url=API, trust_env=False, timeout=30)


def login(e, p):
    return {"Authorization": "Bearer " + c.post("/api/auth/login", json={"email": e, "password": p}).json()["access_token"]}


adm, pl = login("admin@factory-monitor.com", "Admin@1234"), login("planejador@factory-monitor.com", "Planner@1234")
users = c.get("/api/users/", headers=adm).json()
users = users if isinstance(users, list) else users.get("items", [])
covered = set()
for u in users:
    if "technician" in (u.get("roles") or [u["role"]]) and u.get("on_call") and u.get("is_active", True):
        covered |= set(u.get("line_ids") or ["*"])
machines = c.get("/api/machines/", headers=adm).json()
m = next((m for m in machines if "*" not in covered and m["line_id"] not in covered), None)
if not m:
    print("every line has an on-call technician; nothing to check")
    sys.exit(0)
ids_before = {n["id"] for u in (adm, pl) for n in c.get("/api/notifications/", headers=u).json()["items"]}
r = c.post("/api/work-orders/emergency-call", headers=adm, json={"machine_id": m["id"], "title": "QA sem plantão"})
print(f"emergency-call on {m['name']} (no on-call technician on its line) → {r.status_code}")
wo = r.json()
from_db = "?"
fails = []
new = [n for u in (adm, pl) for n in c.get("/api/notifications/", headers=u).json()["items"]
       if n["id"] not in ids_before and n["kind"] == "emergency_call"]
hint = {k: wo.get(k) for k in ("notified", "recipients", "notified_count", "warning") if k in wo}
ok = bool(new) or bool(hint)
print(("  ✓ " if ok else "  ✗ ") + f"someone is reached or the caller is told nobody was (new emergency notifications for admin/planner: {len(new)}; response hints: {hint})")
if r.status_code in (200, 201):
    c.post(f"/api/work-orders/{wo['id']}/transition", headers=pl, json={"to": "cancelled", "cancel_reason": "duplicate"})
sys.exit(0 if ok else 1)
