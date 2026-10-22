"""Sprint 5 acceptance (c) against a RUNNING server (real HTTP, real clock): a predictive alert converted into a
work order → the WO is planned work in PMP; completing it (defect confirmed) closes the alert as `confirmed` and
resets the baseline (`work_order`); installing a new component at a monitored position resets the baseline
(`installation`) on the next engine run.

Seeds two F1 monitors (blocks 1–52, time-shifted to now) with e2e/sprint5/seed_predictive_f1.py first:

    cd server && (env of the server's DB) BASE=http://localhost:8161 SUFFIX=" QA-c" ../.venv/bin/python ../e2e/sprint5/seed_predictive_f1.py
    cd server && ... SUFFIX=" QA-c2" ...
    API=http://localhost:8161 python e2e/sprint5/predictive_wo_flow.py
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import httpx

API = os.getenv("API", "http://localhost:8161")
results: list[tuple[bool, str]] = []


def check(ok, name, got=None):
    results.append((bool(ok), name))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   got={str(got)[:400]!r}"), flush=True)


def C(email, pw):
    t = httpx.post(f"{API}/api/auth/login", json={"email": email, "password": pw}, trust_env=False).json()["access_token"]
    return httpx.Client(base_url=API, timeout=60, trust_env=False, headers={"Authorization": f"Bearer {t}"})


admin = C("admin@factory-monitor.com", "Admin@1234")
plan = C("planejador@factory-monitor.com", "Planner@1234")
tech = C("tecnico@factory-monitor.com", "Tecnico@1234")
tech_id = tech.get("/api/auth/me").json()["id"]
now = datetime.now(timezone.utc)
k_start, k_stop = (now - timedelta(days=1)).isoformat(), (now + timedelta(days=2)).isoformat()


def kpis():
    return plan.get("/api/maintenance/kpis", params={"start": k_start, "stop": k_stop}).json()


def alert_for(suffix):
    rows = plan.get("/api/predictive/alerts", params={"state": ["open", "acknowledged", "converted"]}).json()
    return next(a for a in rows if a["position_name"].endswith(suffix))


# ── (c1) convert → WO → PMP → complete → confirmed + rebaseline ────────────
a = alert_for("QA-c")
mon_id = a["monitor_id"]
k0 = kpis()
out = plan.post(f"/api/predictive/alerts/{a['id']}/convert", json={"estimated_hours": 2, "note": "QA"})
check(out.status_code == 201, "planner converts the alert", out.text)
out = out.json()
wo = out["work_order"]
rul = (a.get("rul_condition") or {})
check(wo["type"] == "predictive" and wo["state"] == "approved" and wo["source_alert_id"] == a["id"],
      "WO is predictive, approved, linked to the alert", wo)
check(wo.get("due_by") == rul.get("projected_date_low"), f"WO due_by = low end of the RUL band ({rul.get('projected_date_low')})",
      wo.get("due_by"))
check(plan.post(f"/api/predictive/alerts/{a['id']}/convert", json={}).status_code == 422, "second convert → 422 (one WO per alert)")
r = plan.post(f"/api/work-orders/{wo['id']}/transition", json={"to": "scheduled", "scheduled_for": now.date().isoformat(),
                                                               "assignee_ids": [tech_id]})
check(r.status_code == 200, "planner schedules the WO for the technician", r.text)
r = tech.post(f"/api/work-orders/{wo['id']}/transition", json={"to": "in_progress"})
check(r.status_code == 200, "technician starts the WO", r.text)
r = tech.post(f"/api/work-orders/{wo['id']}/labor", json={"hours": 1.5, "note": "inspeção do cilindro"})
check(r.status_code == 201, "technician logs 1.5 h", r.text)
r = tech.post(f"/api/work-orders/{wo['id']}/transition", json={"to": "completed"})
check(r.status_code == 422 and "finding" in r.text, "completion without finding → 422", r.text)
r = tech.post(f"/api/work-orders/{wo['id']}/transition", json={"to": "completed", "finding": "defect_confirmed"})
check(r.status_code == 422, "defect_confirmed without a failure record → 422", r.text)
modes = re.findall(r"\b[A-Z]{3}\b", r.text)
mode = next((m for m in modes if m not in ("ISO", "WO", "OS")), "ELP")
f = tech.post("/api/failures/", json={"machine_id": a["machine_id"], "position_id": a["position_id"],
                                      "failed_at": datetime.now(timezone.utc).isoformat(), "severity": "incipient",
                                      "failure_mode": mode, "detection_method": "continuous_condition_monitoring",
                                      "work_order_id": wo["id"]})
check(f.status_code == 201, f"technician records the incipient failure (mode {mode})", f.text)
done = tech.post(f"/api/work-orders/{wo['id']}/transition", json={"to": "completed", "finding": "defect_confirmed"})
check(done.status_code == 200 and done.json()["finding"] == "defect_confirmed" and done.json()["resets_baseline"] is True,
      "WO completed with defect_confirmed (resets_baseline default true)", done.text)
a2 = plan.get(f"/api/predictive/alerts/{a['id']}").json()
check(a2["state"] == "closed" and a2["resolution"] == "confirmed", "alert closed as confirmed", (a2["state"], a2.get("resolution")))
mon = plan.get(f"/api/predictive/monitors/{mon_id}").json()
kinds = [b["reset_kind"] for b in mon["baselines"]]
check(kinds[-1] == "work_order" and mon["state"] == "learning", f"monitor rebaselined by the WO ({kinds}, {mon['state']})")
k1 = kpis()
check(wo["id"] in (k1.get("pmp_work_order_ids") or []) and abs((k1["planned_hours"] - k0["planned_hours"]) - 1.5) < 1e-6,
      f"PMP counts the predictive WO as planned (+1.5 h: {k0['planned_hours']} → {k1['planned_hours']}; pmp {k1.get('pmp')})",
      {k: k1.get(k) for k in ("planned_hours", "unplanned_hours", "pmp", "pmp_work_order_ids")})

# ── (c2) component installed at the position → baseline reset on the next run ──
b = alert_for("QA-c2")
mon2 = plan.get(f"/api/predictive/monitors/{b['monitor_id']}").json()
n0 = len(mon2["baselines"])
comp = admin.post("/api/components/", json={"component_type": "pneumatic_cylinder", "manufacturer": "QA", "model": "DSBC",
                                            "serial_number": f"QA-C2-{int(now.timestamp())}"})
check(comp.status_code == 201, "admin creates a new cylinder", comp.text)
ins = admin.post(f"/api/positions/{b['position_id']}/install", json={"component_id": comp.json()["id"]})
check(ins.status_code in (200, 201), "new cylinder installed at the monitored position", ins.text)
pr = plan.post(f"/api/predictive/monitors/{b['monitor_id']}/process")
mon2 = plan.get(f"/api/predictive/monitors/{b['monitor_id']}").json()
kinds2 = [x["reset_kind"] for x in mon2["baselines"]]
check(len(kinds2) == n0 + 1 and kinds2[-1] == "installation" and mon2["state"] == "learning",
      f"installation resets the baseline on the next run ({kinds2}, {mon2['state']})", pr.text)
b2 = plan.get(f"/api/predictive/alerts/{b['id']}").json()
print(f"    (alert of the replaced component after the install: state={b2['state']} resolution={b2.get('resolution')})")

print(f"\n{sum(ok for ok, _ in results)}/{len(results)} passed")
raise SystemExit(0 if all(ok for ok, _ in results) else 1)
