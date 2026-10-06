"""Sprint 5 acceptance (f), real path (virtual plant → gateway → MQTT → server), real clock: a robot fault forced
through the plant control API DURING a planned break (kpis.md S5-1):
  * FailureRecord created immediately (failed_at = fault start, inside the break) and the emergency call goes out
    during the break (technician notification `emergency_call` + emergency WO);
  * no DowntimeEvent during the break; when the break ends with the fault still active the EQP DowntimeEvent starts
    at the break end, covers only the productive part and is linked to the same failure;
  * MTTR in clock time: restored_at = repair instant; OEE: the break stays planned downtime (PDOT includes the whole
    break), the unplanned downtime of the fault is only the productive part.
Also probes kpis S5-1b (short fault < 120 s entirely inside the break → no FailureRecord / no call) on a 2nd machine.

    python e2e/sprint5/break_fault_live.py --api http://localhost:8161 --control http://127.0.0.1:8202 \
        --break-start 2026-10-06T04:30:00Z --break-end 2026-10-06T04:40:00Z
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone

import httpx

ap = argparse.ArgumentParser()
ap.add_argument("--api", default="http://localhost:8161")
ap.add_argument("--control", default="http://127.0.0.1:8202")
ap.add_argument("--break-start", required=True)
ap.add_argument("--break-end", required=True)
ap.add_argument("--machine", default="Robô de Solda")
ap.add_argument("--short-machine", default="Estação de Fixação")
ap.add_argument("--repair-after-end-s", type=int, default=240)
args = ap.parse_args()
BS = datetime.fromisoformat(args.break_start.replace("Z", "+00:00"))
BE = datetime.fromisoformat(args.break_end.replace("Z", "+00:00"))
results = []


def check(ok, name, got=None):
    results.append((bool(ok), name))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   got={str(got)[:500]!r}"), flush=True)


def now():
    return datetime.now(timezone.utc)


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def wait_until(t):
    while now() < t:
        time.sleep(min(5.0, max(0.2, (t - now()).total_seconds())))


def C(email, pw):
    t = httpx.post(f"{args.api}/api/auth/login", json={"email": email, "password": pw}, trust_env=False).json()["access_token"]
    return httpx.Client(base_url=args.api, timeout=60, trust_env=False, headers={"Authorization": f"Bearer {t}"})


sup, plan = C("supervisor@factory-monitor.com", "Supervisor@1234"), C("planejador@factory-monitor.com", "Planner@1234")
tech = C("tecnico@factory-monitor.com", "Tecnico@1234")
ctl = httpx.Client(base_url=args.control, timeout=20, trust_env=False)
machines = {m["name"]: m for m in sup.get("/api/machines/").json()}
robot, short = machines[args.machine], machines[args.short_machine]
key = {"Robô de Solda": "montagem.robo", "Estação de Fixação": "montagem.fixacao"}


def failures(mid):
    return plan.get("/api/failures/", params={"machine_id": mid}).json()


def downtimes(mid, start, stop):
    r = sup.get("/api/downtime/", params={"machine_id": mid, "start": start.isoformat(), "stop": stop.isoformat()}).json()
    return r if isinstance(r, list) else r.get("items", [])


f_before = {f["id"] for f in failures(robot["id"])}
fs_before = {f["id"] for f in failures(short["id"])}
n_before = {n["id"] for n in tech.get("/api/notifications/").json().get("items", [])}

# 1. robot fault 60 s into the break (log in again after long waits: access tokens expire)
wait_until(BS + timedelta(seconds=60))
sup, plan = C("supervisor@factory-monitor.com", "Supervisor@1234"), C("planejador@factory-monitor.com", "Planner@1234")
tech = C("tecnico@factory-monitor.com", "Tecnico@1234")
st = ctl.get("/status").json()
r = ctl.post("/force-failure", json={"machine": key[args.machine]})
t_fault = now()
check(r.status_code == 200, f"control API: robot fault forced at {t_fault:%H:%M:%S}Z (inside the break)", r.text)
fr = None
for _ in range(45):
    new = [f for f in failures(robot["id"]) if f["id"] not in f_before]
    if new:
        fr = new[0]
        break
    time.sleep(2)
t_seen = now()
check(fr is not None, f"FailureRecord created during the break ({(t_seen - t_fault).total_seconds():.0f} s after the fault)")
if fr:
    check(abs((ts(fr["failed_at"]) - t_fault).total_seconds()) < 15 and BS <= ts(fr["failed_at"]) < BE,
          f"failed_at = fault start inside the break ({fr['failed_at']})", fr)
    check(fr.get("downtime_event_id") is None and fr.get("open") is True, "failure open, no DowntimeEvent yet", fr)
call = None
for _ in range(45):
    items = tech.get("/api/notifications/").json().get("items", [])
    call = next((n for n in items if n["id"] not in n_before and n.get("kind") == "emergency_call"
                 and (n.get("data") or {}).get("failure_id") == (fr or {}).get("id")), None)
    if call:
        break
    time.sleep(2)
check(call is not None and now() < BE, "on-call technician got the emergency call DURING the break", call)
if call:
    wo = plan.get(f"/api/work-orders/{call['data']['work_order_id']}").json()
    check(wo.get("type") == "corrective" or wo.get("emergency") or "emerg" in json.dumps(wo).lower(),
          f"emergency WO {wo.get('number')} opened during the break", wo)
dts_break = [d for d in downtimes(robot["id"], BS, now()) if not d["planned"] and ts(d["start"]) < BE
             and ts(d["start"]) >= BS]
check(not dts_break, "no unplanned DowntimeEvent inside the break", dts_break)

# 2. S5-1b probe: 20 s fault entirely inside the break on another machine
wait_until(BS + timedelta(seconds=180))
r = ctl.post("/force-failure", json={"machine": key[args.short_machine], "duration_s": 20})
t_short = now()
check(r.status_code == 200, f"control API: 20 s fault on {args.short_machine} inside the break", r.text)
time.sleep(75)
new_short = [f for f in failures(short["id"]) if f["id"] not in fs_before]
check(not new_short, "kpis S5-1b: a 20 s fault inside the break creates NO FailureRecord (short fault)",
      [(f["failed_at"], f.get("restored_at"), f.get("severity")) for f in new_short])

# 3. fault still active after the break → repair 4 min after the break end
wait_until(BE + timedelta(seconds=args.repair_after_end_s))
sup, plan = C("supervisor@factory-monitor.com", "Supervisor@1234"), C("planejador@factory-monitor.com", "Planner@1234")
r = ctl.post("/repair", json={"machine": key[args.machine]})
t_rep = now()
check(r.status_code == 200, f"control API: robot repaired at {t_rep:%H:%M:%S}Z", r.text)
# the event closes when the machine runs again (the virtual plant may take minutes to restart the cell)
for _ in range(120):
    f2 = plan.get(f"/api/failures/{fr['id']}").json() if fr else {}
    if f2.get("restored_at"):
        break
    time.sleep(10)
t_run = ts(f2.get("restored_at")) if f2 else None
f2 = plan.get(f"/api/failures/{fr['id']}").json() if fr else {}
ev = None
for d in downtimes(robot["id"], BS - timedelta(minutes=5), now()):
    if d.get("failure_id") == f2.get("id"):
        ev = d
check(ev is not None, "an EQP DowntimeEvent is linked to the same failure", downtimes(robot["id"], BS, now()))
if ev:
    check(abs((ts(ev["start"]) - BE).total_seconds()) < 3, f"event starts at the break end ({ev['start']})", ev)
    check(ev["end"] and ts(ev["end"]) >= t_rep - timedelta(seconds=15), f"event ends when the robot runs again ({ev['end']}, repair {t_rep:%H:%M:%S}Z)", ev)
    check(f2.get("downtime_event_id") == ev["id"], "failure.downtime_event_id = that event", f2)
if f2:
    check(f2.get("restored_at") and ev and f2["restored_at"] == ev["end"]
          and abs(f2.get("restore_duration_s", 0) - (ts(f2["restored_at"]) - ts(fr["failed_at"])).total_seconds()) < 1,
          f"restored_at = end of the stop (fault cleared in productive time); clock MTTR {f2.get('restore_duration_s')} s "
          f"counted from failed_at inside the break", f2)
    if call:
        wo2 = plan.get(f"/api/work-orders/{call['data']['work_order_id']}").json()
        check(wo2.get("downtime_event_id") == (ev or {}).get("id"), "the emergency WO is linked to the stop", wo2.get("downtime_event_id"))

# 4. OEE: break stays planned downtime, the fault counts only after the break
o_start, o_stop = BS - timedelta(minutes=10), (t_run or now()) + timedelta(minutes=1)
o = sup.get("/api/oee", params={"scope": "machine", "id": robot["id"], "start": o_start.isoformat(),
                                "stop": o_stop.isoformat()}).json()
br = (BE - BS).total_seconds()
dts = downtimes(robot["id"], o_start, o_stop)
unpl = sum(min(ts(d["end"] or o_stop.isoformat()), o_stop).timestamp() - max(ts(d["start"]), o_start).timestamp()
           for d in dts if not d["planned"])
check(abs(o["pdot_s"] - br) < 2, f"OEE: PDOT = whole break ({o['pdot_s']} s vs {br} s)", o)
check(abs(o["adot_s"] - unpl) < 5 and o["adot_s"] < br,
      f"OEE: unplanned downtime = stops in productive time only ({o['adot_s']:.0f} s; fault part "
      f"{(t_rep - BE).total_seconds():.0f} s)", {k: o.get(k) for k in ("pot_s", "pdot_s", "pbt_s", "adot_s", "apt_s", "availability")})
print(json.dumps({k: o.get(k) for k in ("pot_s", "pdot_s", "pbt_s", "adot_s", "apt_s", "nodata_s", "availability", "oee")}))
print(f"\n{sum(ok for ok, _ in results)}/{len(results)} passed")
raise SystemExit(0 if all(ok for ok, _ in results) else 1)
