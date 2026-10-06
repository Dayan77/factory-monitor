"""WebSocket {type:'predictive_alert'} goes only to users allowed to read alerts (P-8/P-12), real server + real sockets.

Opens /ws/sensors/{tenant} as planner, technician and operator, then seeds a new F1 monitor whose processing opens
a critical alert (e2e/sprint5/seed_predictive_f1.py with a unique SUFFIX, run by this script through the server's
own env). Planner and technician must receive `predictive_alert` (event opened); the operator must not.

    cd server && (env of the server's DB) ../.venv/bin/python ../e2e/sprint5/ws_predictive_alert.py http://localhost:8161
"""
import asyncio
import json
import os
import subprocess
import sys
import time

import httpx
import websockets

API = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8161"
WS = API.replace("http", "ws", 1)
SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_predictive_f1.py")


def login(e, p):
    return httpx.post(f"{API}/api/auth/login", json={"email": e, "password": p}, trust_env=False).json()["access_token"]


async def main():
    admin = login("admin@factory-monitor.com", "Admin@1234")
    tenant = httpx.get(f"{API}/api/auth/me", headers={"Authorization": f"Bearer {admin}"}, trust_env=False).json()["tenant_id"]
    stamp = str(int(time.time()))
    op_email = f"qa-ws-op-{stamp}@qa-factoryconnect.com.br"
    r = httpx.post(f"{API}/api/users/", headers={"Authorization": f"Bearer {admin}"}, trust_env=False,
                   json={"email": op_email, "password": "QaRole#2026x", "name": "QA op", "role": "operator"})
    r.raise_for_status()
    toks = {"planner": login("planejador@factory-monitor.com", "Planner@1234"),
            "technician": login("tecnico@factory-monitor.com", "Tecnico@1234"),
            "operator": login(op_email, "QaRole#2026x")}
    got = {k: [] for k in toks}

    async def listen(role):
        async with websockets.connect(f"{WS}/ws/sensors/{tenant}", subprotocols=["bearer", toks[role]]) as ws:
            end = time.time() + 40
            while time.time() < end:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
                except asyncio.TimeoutError:
                    continue
                if msg.get("type") == "predictive_alert":
                    got[role].append(msg)

    tasks = [asyncio.create_task(listen(r)) for r in toks]
    await asyncio.sleep(3)
    env = dict(os.environ, BASE=API, SUFFIX=f" WS-{stamp}")
    p = await asyncio.to_thread(subprocess.run, [sys.executable, SEED], env=env, capture_output=True, text=True)
    print(p.stdout[-300:], p.stderr[-300:])
    await asyncio.gather(*tasks)
    ok = True
    for role, msgs in got.items():
        opened = [m for m in msgs if m.get("event") == "opened" and m["alert"]["position_name"].endswith(f"WS-{stamp}")]
        want = role != "operator"
        good = bool(opened) == want
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  {role}: predictive_alert opened received={bool(opened)} (expected {want}); "
              f"total predictive msgs {len(msgs)}")
    raise SystemExit(0 if ok else 1)


asyncio.run(main())
