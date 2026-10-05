"""Sprint 4 acceptance §7 item 7 on the virtual plant (real path CLP → gateway → MQTT → server).

Phase `call`:  subscribes the on-call technician to Web Push (fake push service from receiver.py), registers a
               webhook (receiver.py), opens a WebSocket as the technician, forces a robot failure through the
               plant control API and waits for: downtime EQP + critical FailureRecord, the `emergency_call`
               notification record, the WebSocket message, the push POST (decrypted + VAPID verified) and the
               signed `failure.created` webhook. Writes the ids to --state.
Phase `repair`: POST /repair on the plant; waits for the downtime to close and the failure `restored_at`.
Phase `verify`: after the technician completed on the phone: WO in_progress→completed history, failure mode,
               restored_at == downtime end, MTTR of the robot position/machine includes it.

    python e2e/sprint4/emergency_live.py call   --api http://localhost:8120 --control http://127.0.0.1:7072 ...
"""
import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import websockets

ap = argparse.ArgumentParser()
ap.add_argument("phase", choices=["call", "repair", "verify"])
ap.add_argument("--api", default="http://localhost:8120")
ap.add_argument("--control", default="http://127.0.0.1:7072")
ap.add_argument("--sub", required=True, help="JSON with p256dh/auth/secret (made by the QA setup)")
ap.add_argument("--recv-log", required=True)
ap.add_argument("--state", required=True)
ap.add_argument("--push-endpoint", default="https://localhost:9443/push/qa-tech")
ap.add_argument("--webhook-url", default="http://127.0.0.1:9180/hook")
args = ap.parse_args()
SUB = json.loads(Path(args.sub).read_text())
FAILS = []


def check(cond, msg, extra=None):
    print(("  ✓ " if cond else "  ✗ ") + msg + ("" if cond or extra is None else f" — got {extra}"), flush=True)
    if not cond:
        FAILS.append(msg)


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


async def login(c, email, pw):
    r = await c.post("/api/auth/login", json={"email": email, "password": pw})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def wait_for(fn, timeout=180, every=2.0, what=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = await fn()
        if v:
            return v
        await asyncio.sleep(every)
    print(f"    (timeout waiting for {what})", flush=True)
    return None


def recv_lines(kind):
    p = Path(args.recv_log)
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip() and json.loads(x)["kind"] == kind]


async def phase_call(c, H, me):
    st = {}
    robot = next(m for m in (await c.get("/api/machines/", headers=H["sup"])).json() if m["name"] == "Robô de Solda")
    st["machine_id"] = robot["id"]
    st["tenant_id"] = me["tech"]["tenant_id"]
    check(me["tech"]["on_call"] and robot["line_id"] in me["tech"]["line_ids"], "technician is on call for the robot's line")

    r = await c.post("/api/push/subscribe", headers=H["tech"], json={"endpoint": args.push_endpoint,
                                                                     "keys": {"p256dh": SUB["p256dh"], "auth": SUB["auth"]}})
    check(r.status_code == 201, "technician push subscription stored", r.text)
    v = (await c.get("/api/push/vapid-public-key", headers=H["tech"])).json()
    check(v["enabled"] and v["public_key"], "VAPID enabled on this server")
    r = await c.post("/api/webhooks/", headers=H["admin"], json={"url": args.webhook_url, "secret": SUB["secret"],
                                                                "events": ["failure.created", "failure.updated", "work_order.created",
                                                                           "work_order.in_progress", "work_order.completed"],
                                                                "description": "QA receiver"})
    check(r.status_code == 201, "webhook registered", r.text)
    st["webhook_id"] = r.json().get("id")

    tok = H["tech"]["Authorization"].split()[1]
    ws_msgs = []
    started = datetime.now(timezone.utc)

    async def listen():
        async with websockets.connect(f"{args.api.replace('http', 'ws')}/ws/sensors/{st['tenant_id']}",
                                      subprotocols=["bearer", tok]) as ws:
            while True:
                m = json.loads(await ws.recv())
                if m.get("type") == "notification":
                    ws_msgs.append(m)
    lt = asyncio.create_task(listen())
    await asyncio.sleep(2)

    r = httpx.post(f"{args.control}/force-failure", json={"machine": "montagem.robo", "code": 331})
    check(r.status_code == 200, f"control API force-failure robot 331 → {r.status_code}", r.text)
    st["forced_at"] = started.isoformat()

    async def failure():
        fs = (await c.get("/api/failures/", headers=H["planner"], params={"machine_id": st["machine_id"]})).json()
        fs = fs if isinstance(fs, list) else fs.get("items", [])
        fs = [f for f in fs if f.get("downtime_event_id") and ts(f["failed_at"]) >= started - timedelta(minutes=2)]
        return fs[0] if fs else None
    f = await wait_for(failure, 240, what="failure record")
    check(f is not None, "server opened a FailureRecord from the downtime")
    if not f:
        lt.cancel()
        return st
    st["failure_id"], st["downtime_event_id"] = f["id"], f["downtime_event_id"]
    check(f["severity"] == "critical" and f["restored_at"] is None, f"failure critical and open (severity {f['severity']})")
    dts = (await c.get("/api/downtime/", headers=H["sup"], params={"machine_id": st["machine_id"]})).json()
    dt = next((d for d in dts if d["id"] == f["downtime_event_id"]), None)
    check(dt is not None and dt.get("fault_code") in (331, "331") and dt.get("end") is None and str(dt.get("effective_reason_code", "")).startswith("EQP"),
          f"downtime open with fault 331 / {dt and dt.get('effective_reason_code')}", dt)

    async def notif():
        n = (await c.get("/api/notifications/", headers=H["tech"])).json()
        return next((i for i in n["items"] if i["kind"] == "emergency_call" and (i.get("data") or {}).get("failure_id") == f["id"]), None)
    n = await wait_for(notif, 60, what="emergency_call notification")
    check(n is not None and n["priority"] == "emergency", "notification record emergency_call (priority emergency) for the on-call technician", n)
    if n:
        st["notification"] = n
    w = await wait_for(lambda: asyncio.sleep(0, result=[m for m in ws_msgs if m["notification"]["kind"] == "emergency_call"]), 30, 1, "ws")
    check(bool(w), "WebSocket {type:'notification', kind:'emergency_call'} delivered to the technician", ws_msgs)
    push = await wait_for(lambda: asyncio.sleep(0, result=[p for p in recv_lines("push") if (p.get("payload") or {}).get("kind") == "emergency_call"]), 60, 1, "push")
    if push:
        p = push[-1]
        check(p["vapid"]["ok"] and p["vapid"]["k_matches"], f"push POST to the push service with valid VAPID JWT (aud {p['vapid'].get('claims', {}).get('aud')})")
        check(p["headers"].get("Content-Encoding", p["headers"].get("content-encoding")) == "aes128gcm" and p["headers"].get("Urgency", p["headers"].get("urgency")) == "high",
              f"push encrypted aes128gcm, Urgency {p['headers'].get('Urgency')}")
        check("payload" in p and p["payload"]["title"].startswith("Chamado emergencial"), f"push payload decrypts: {p.get('payload')}")
    else:
        check(False, "push send attempt reached the push service", recv_lines("push"))
    sups = (await c.get("/api/notifications/", headers=H["sup"])).json()
    check(any(i["kind"] == "emergency_call" for i in sups["items"]), "supervisor of the line also called")
    pl = (await c.get("/api/notifications/", headers=H["planner"])).json()
    check(any(i["kind"] == "failure_new" for i in pl["items"]), "planner gets failure_new")
    hook = await wait_for(lambda: asyncio.sleep(0, result=[h for h in recv_lines("webhook") if (h["body"] or {}).get("event") == "failure.created" or "failure" in json.dumps(h["body"])[:200]]), 60, 1, "webhook")
    if hook:
        check(all(h["signature_ok"] for h in hook), f"webhook failure.created delivered with valid HMAC signature ({len(hook)} deliveries)")
        st["webhook_body"] = hook[0]["body"]
    else:
        check(False, "webhook delivered", recv_lines("webhook"))
    lt.cancel()
    return st


async def phase_repair(c, H, st):
    r = httpx.post(f"{args.control}/repair", json={"machine": "montagem.robo"})
    check(r.status_code == 200, f"control API repair → {r.status_code}", r.text)
    st["repaired_at"] = datetime.now(timezone.utc).isoformat()

    async def restored():
        f = (await c.get(f"/api/failures/{st['failure_id']}", headers=H["planner"])).json()
        return f if f.get("restored_at") else None
    f = await wait_for(restored, 240, what="restored_at")
    check(f is not None, "failure restored_at set after the plant came back")
    dts = (await c.get("/api/downtime/", headers=H["sup"], params={"machine_id": st["machine_id"]})).json()
    dt = next((d for d in dts if d["id"] == st["downtime_event_id"]), None)
    check(dt and dt.get("end"), "downtime closed", dt)
    if f and dt:
        check(ts(f["restored_at"]) == ts(dt["end"]), f"restored_at {f['restored_at']} == downtime end {dt['end']}")
        st["restore_duration_s"] = f.get("restore_duration_s")
    s = httpx.get(f"{args.control}/status").json()
    check(s["machines"]["montagem.robo"]["fault_code"] == 0, "plant: robot fault cleared")
    return st


async def phase_verify(c, H, st):
    f = (await c.get(f"/api/failures/{st['failure_id']}", headers=H["planner"])).json()
    check(f.get("failure_mode"), f"failure mode recorded from the phone: {f.get('failure_mode')}")
    check(f.get("work_order_id"), "failure linked to the emergency WO")
    wo = (await c.get(f"/api/work-orders/{f['work_order_id']}", headers=H["planner"])).json()
    hist = [h["to_state"] for h in wo["history"]]
    check(wo["urgency"] == "emergency" and wo["type"] == "corrective", f"WO {wo['number']} corrective emergency")
    check(hist[:2] == ["open", "in_progress"] and wo["state"] == "completed", f"history open → in_progress (shortcut) … completed: {hist}")
    rel = (await c.get("/api/maintenance/reliability", headers=H["planner"],
                       params={"group_by": "machine", "start": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                               "stop": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()})).json()
    row = next((r for r in rel["rows"] if r.get("machine_id") == st["machine_id"]), None)
    check(row and row["n"] >= 1 and row["restored"] >= 1 and row["mttr_s"] is not None, f"robot enters MTTR: {row and {k: row[k] for k in ('n', 'restored', 'mttr_s')}}")
    if row and st.get("restore_duration_s") and row["restored"] == 1:
        check(abs(row["mttr_s"] - st["restore_duration_s"]) < 1, f"MTTR {row['mttr_s']} s == restore duration {st['restore_duration_s']} s")
    return st


async def main():
    st = json.loads(Path(args.state).read_text()) if Path(args.state).exists() and args.phase != "call" else {}
    async with httpx.AsyncClient(base_url=args.api, timeout=30) as c:
        H = {"tech": await login(c, "tecnico@factory-monitor.com", "Tecnico@1234"),
             "sup": await login(c, "supervisor@factory-monitor.com", "Supervisor@1234"),
             "planner": await login(c, "planejador@factory-monitor.com", "Planner@1234"),
             "admin": await login(c, "admin@factory-monitor.com", "Admin@1234")}
        me = {"tech": (await c.get("/api/auth/me", headers=H["tech"])).json()}
        if args.phase == "call":
            st = await phase_call(c, H, me)
        elif args.phase == "repair":
            st = await phase_repair(c, H, st)
        else:
            st = await phase_verify(c, H, st)
    Path(args.state).write_text(json.dumps(st, indent=1, default=str))
    print("FAILED: " + "; ".join(FAILS) if FAILS else "ALL CHECKS PASSED", flush=True)
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
