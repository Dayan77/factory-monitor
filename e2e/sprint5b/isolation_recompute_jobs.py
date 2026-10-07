"""Sprint 5b black-box checks against a RUNNING server (real HTTP) for the endpoints added in 5b:
POST /api/predictive/recompute {background:true} / ?background=true (202 + job), GET /api/predictive/recompute/jobs/{id},
the one-recompute-per-tenant rule (409 with the running job id), and the new fields of POST /monitors/{id}/process.

Tenant A = dev seed + e2e/sprint5/seed_predictive_f1.py (has a stroke_time monitor); tenant B = freshly registered,
one user per role. Checks: only an admin may start / read a job (every other role 403, no token 401); B's admin
never sees A's job (404) and cannot start a recompute on A's monitor (404); A's job finishes with the same body as
the synchronous call; a second recompute while one runs for the same tenant → 409 carrying the running job id,
while tenant B can still run its own recompute (the limit is per tenant).

    API=http://localhost:8161 .venv/bin/python e2e/sprint5b/isolation_recompute_jobs.py
"""
import os
import sys
import time
import uuid

import httpx

API = os.getenv("API", "http://localhost:8161")
results: list[bool] = []


def check(ok, name, got=None):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   got={str(got)[:400]!r}"), flush=True)


def login(email, pw):
    r = httpx.post(f"{API}/api/auth/login", json={"email": email, "password": pw}, trust_env=False)
    r.raise_for_status()
    return r.json()["access_token"]


def C(token=None):
    return httpx.Client(base_url=API, timeout=300, trust_env=False,
                        headers={"Authorization": f"Bearer {token}"} if token else {})


def wait_job(cl, job_id, timeout_s=600):
    end = time.time() + timeout_s
    while time.time() < end:
        r = cl.get(f"/api/predictive/recompute/jobs/{job_id}")
        if r.status_code != 200 or r.json()["status"] in ("done", "failed"):
            return r
        time.sleep(0.5)
    return r


ROLES = ("manager", "maintenance_planner", "technician", "production_supervisor", "operator")
stamp = f"{int(time.time())}{uuid.uuid4().hex[:4]}"
SUPER = C(login("superadmin@factory-monitor.com", "Super@1234"))
A = {"admin": C(login("admin@factory-monitor.com", "Admin@1234")),
     "maintenance_planner": C(login("planejador@factory-monitor.com", "Planner@1234")),
     "technician": C(login("tecnico@factory-monitor.com", "Tecnico@1234")),
     "production_supervisor": C(login("supervisor@factory-monitor.com", "Supervisor@1234"))}
a_tenant = A["admin"].get("/api/auth/me").json()["tenant_id"]
SUPER.patch(f"/api/admin/tenants/{a_tenant}", json={"max_users": 60}).raise_for_status()
for role in ("manager", "operator"):
    email = f"qa-5b-a-{role}-{stamp}@qa-factoryconnect.com.br"
    A["admin"].post("/api/users/", json={"email": email, "password": "QaRole#2026x", "name": f"A {role}",
                                         "role": role}).raise_for_status()
    A[role] = C(login(email, "QaRole#2026x"))
mon = next(m for m in A["maintenance_planner"].get("/api/predictive/monitors").json() if m["feature"] == "stroke_time")
M = mon["id"]

b_email = f"qa-5b-b-{stamp}@qa-factoryconnect.com.br"
httpx.post(f"{API}/api/auth/register", trust_env=False, json={
    "company_name": f"QA 5b B {stamp}", "admin_name": "QA B", "admin_email": b_email,
    "admin_password": "QaIso#2026x"}).raise_for_status()
B = {"admin": C(login(b_email, "QaIso#2026x"))}
b_tenant = B["admin"].get("/api/auth/me").json()["tenant_id"]
SUPER.patch(f"/api/admin/tenants/{b_tenant}", json={"max_users": 20}).raise_for_status()
for role in ROLES:
    email = f"qa-5b-{role}-{stamp}@qa-factoryconnect.com.br"
    B["admin"].post("/api/users/", json={"email": email, "password": "QaRole#2026x", "name": f"B {role}",
                                         "role": role}).raise_for_status()
    B[role] = C(login(email, "QaRole#2026x"))
print(f"tenant A {a_tenant[:8]} monitor {M[:8]}; tenant B {b_tenant[:8]}")

# ── synchronous reference ─────────────────────────────────────────────────
sync = A["admin"].post("/api/predictive/recompute", json={"monitor_id": M})
check(sync.status_code == 200, "A admin: synchronous recompute → 200", sync.text)

# ── RBAC: only admin starts a background job ──────────────────────────────
for role in ROLES:
    for how, kw in (("body", {"json": {"monitor_id": M, "background": True}}),
                    ("query", {"json": {"monitor_id": M}, "params": {"background": "true"}})):
        r = A[role].post("/api/predictive/recompute", **kw)
        check(r.status_code == 403, f"A {role}: POST recompute background ({how}) → 403", (r.status_code, r.text))
r = C().post("/api/predictive/recompute", json={"monitor_id": M, "background": True})
check(r.status_code == 401, "no token: POST recompute background → 401", r.status_code)

# ── A admin background job (query form) ───────────────────────────────────
r = A["admin"].post("/api/predictive/recompute", json={"monitor_id": M}, params={"background": "true"})
check(r.status_code == 202 and r.json().get("job_id"), "A admin: ?background=true → 202 + job_id", r.text)
job = r.json()
jid = job["job_id"]
# while (possibly) running: other roles / tenants / anonymous cannot read it
for role in ROLES:
    rr = A[role].get(f"/api/predictive/recompute/jobs/{jid}")
    check(rr.status_code == 403, f"A {role}: GET job → 403", rr.status_code)
for role in ("admin",) + ROLES:
    rr = B[role].get(f"/api/predictive/recompute/jobs/{jid}")
    check(rr.status_code in (403, 404) and M not in rr.text, f"B {role}: GET A's job → 403/404, nothing leaks",
          (rr.status_code, rr.text[:200]))
rr = C().get(f"/api/predictive/recompute/jobs/{jid}")
check(rr.status_code == 401, "no token: GET job → 401", rr.status_code)
rr = A["admin"].get(f"/api/predictive/recompute/jobs/{uuid.uuid4()}")
check(rr.status_code == 404, "A admin: unknown job id → 404", rr.status_code)
done = wait_job(A["admin"], jid)
dj = done.json()
check(done.status_code == 200 and dj["status"] == "done", "A job finishes (done)", dj)
res, ref = dj.get("result") or {}, sync.json()
check(res.get("same") == ref.get("same") is True and len(res.get("alerts", [])) == len(ref.get("alerts", []))
      and [a.get("id") for a in res.get("alerts", [])] == [a.get("id") for a in ref.get("alerts", [])],
      "background result == synchronous result (same=true, same alerts)", {"bg": res, "sync": ref})

# ── B cannot target A's monitor ───────────────────────────────────────────
for bg in (False, True):
    rr = B["admin"].post("/api/predictive/recompute", json={"monitor_id": M, "background": bg})
    check(rr.status_code == 404, f"B admin: recompute A's monitor (background={bg}) → 404", (rr.status_code, rr.text[:200]))

# ── one per tenant: 409 while A's reextract_all job runs; B unaffected ───
r1 = A["admin"].post("/api/predictive/recompute", json={"monitor_id": M, "reextract_all": True, "background": True})
check(r1.status_code == 202, "A admin: reextract_all background → 202", r1.text)
j1 = r1.json().get("job_id")
r2 = A["admin"].post("/api/predictive/recompute", json={"monitor_id": M, "background": True})
r3 = A["admin"].post("/api/predictive/recompute", json={"monitor_id": M})
st1 = A["admin"].get(f"/api/predictive/recompute/jobs/{j1}").json()["status"]
rb = B["admin"].post("/api/predictive/recompute", json={})
if st1 == "running" or r2.status_code == 409:
    check(r2.status_code == 409 and (r2.json().get("detail") or {}).get("job_id") == j1,
          "A 2nd background recompute while one runs → 409 with the running job id", (r2.status_code, r2.text))
    check(r3.status_code == 409, "A synchronous recompute while a job runs → 409", (r3.status_code, r3.text[:200]))
else:
    print(f"  NOTE  A's reextract_all job ended before the 2nd call (status {st1}); 409 not exercised here")
check(rb.status_code == 200, "B admin: own recompute while A's job runs → 200 (limit is per tenant)",
      (rb.status_code, rb.text[:200]))
d1 = wait_job(A["admin"], j1).json()
check(d1["status"] == "done" and (d1.get("result") or {}).get("same") is True,
      "A reextract_all job: done, same=true", d1)

# ── POST /monitors/{id}/process carries late_windows; RBAC and isolation ─
pr = A["maintenance_planner"].post(f"/api/predictive/monitors/{M}/process")
check(pr.status_code == 200 and "late_windows" in pr.json(), "A planner: /process → 200 with late_windows",
      (pr.status_code, pr.text[:300]))
for role in ("admin",) + ROLES:
    rr = B[role].post(f"/api/predictive/monitors/{M}/process")
    check(rr.status_code in (403, 404), f"B {role}: /process on A's monitor → 403/404", rr.status_code)
for role in ("technician", "production_supervisor", "operator"):
    rr = A[role].post(f"/api/predictive/monitors/{M}/process")
    check(rr.status_code == 403, f"A {role}: /process → 403", rr.status_code)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
