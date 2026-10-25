"""Sprint 5 acceptance (e), complement to gateway_enrollment.verify.mjs: the hot reload must not lose COUNTERS.
Real path: live virtual plant (Modbus + OPC UA) -> real edge gateway (enrolled with ENROLL_CODE) -> MQTT -> server.

Assigns Estação de Fixação (Modbus, incl. the a1.eos pulse counted AT THE EDGE) + Torno CNC (OPC UA) to a new
gateway, runs the real gateway, waits until the counters advance, changes the config on the server (adds Centro de
Usinagem) and checks: same PID reloads (config_reloaded), every counter keeps increasing (never goes back / to 0),
the edge-counted pulse counter continues from its value, new tags flow, heartbeat reports the new config_version.

    API=http://localhost:8161 BROKER_PORT=18852 VP_PORT=8200 .venv/bin/python e2e/sprint5/gateway_hot_reload_counters.py
"""
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

import httpx

API = os.getenv("API", "http://localhost:8161")
BROKER = os.getenv("BROKER_PORT", "18852")
VP = int(os.getenv("VP_PORT", "8200"))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
c = httpx.Client(base_url=API, timeout=60, trust_env=False)
res = []


def check(ok, name, got=None):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got={str(got)[:500]}"), flush=True)


tok = c.post("/api/auth/login", json={"email": "admin@factory-monitor.com", "password": "Admin@1234"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
tree = c.get("/api/plants/tree", headers=H).json()
mach = {m["name"]: m["id"] for p in tree for l in p["lines"] for m in l["machines"]}
plant_id = tree[0]["id"]
tags = c.get("/api/tags/?limit=1000", headers=H).json()
fix = [t for t in tags if t["machine_id"] == mach["Estação de Fixação"] and t["protocol"] == "modbus_tcp"]
torno = [t for t in tags if t["machine_id"] == mach["Torno CNC"] and t["protocol"] == "opc_ua"]
centro = [t for t in tags if t["machine_id"] == mach["Centro de Usinagem"] and t["protocol"] == "opc_ua"]
counters = [t for t in fix + torno if (t.get("semantic_role") or "") in ("cycle_counter", "cycle_pulse", "good_count",
                                                                          "total_count", "part_counter")]
print("counter tags:", [(t["name"], t.get("semantic_role")) for t in counters])
devs = [{"id": "opcua-plc", "protocol": "opcua", "endpoint": f"opc.tcp://127.0.0.1:{VP}/factoryconnect/virtual-plant/"},
        {"id": "modbus-plc", "protocol": "modbus", "host": "127.0.0.1", "port": VP + 1}]
r = c.post("/api/gateways/", headers=H, json={"name": f"QA reload {int(time.time())}", "plant_id": plant_id, "devices": devs,
                                             "heartbeat_s": 5})
check(r.status_code == 201, "gateway created", r.text)
gw = r.json()
r = c.put(f"/api/gateways/{gw['id']}/tags", headers=H, json={"tags": [{"tag_id": t["id"]} for t in fix + torno]})
check(r.status_code == 200, f"{len(fix)} Modbus (Fixação) + {len(torno)} OPC UA (Torno) tags assigned", r.text)
v0 = r.json()["config_version"]
code = c.post(f"/api/gateways/{gw['id']}/enroll-token", headers=H).json()["code"]
work = tempfile.mkdtemp(prefix="qa-gw-reload-")
env = {k: v for k, v in os.environ.items() if not k.startswith(("MQTT_", "GATEWAY_", "PLANT_MAP", "SERVER_URL", "ENROLL",
                                                                   "TENANT_ID"))}
env.update(SERVER_URL=API, ENROLL_CODE=code, GATEWAY_CREDENTIALS_PATH=f"{work}/creds.json",
           GATEWAY_CONFIG_CACHE=f"{work}/cfg.json", GATEWAY_HEARTBEAT_S="5", GATEWAY_CONFIG_POLL_S="600",
           MQTT_BROKER_HOST="127.0.0.1", MQTT_BROKER_PORT=BROKER, MQTT_TLS="false", BUFFER_DB_PATH=f"{work}/buf.db",
           COUNTER_DB_PATH=f"{work}/cnt.db", ENCRYPTION_KEY="", EDGE_VERSION="qa-reload", LOG_LEVEL="INFO")
log = open(f"{work}/gw.log", "w")
# readings of THIS gateway only (an earlier run of this script leaves its own edge-counted values in the history)
GW_START = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "+00:00")
p = subprocess.Popen([f"{REPO}/.venv-edge/bin/python", "-m", "edge.src.gateway"], cwd=REPO, env=env, stdout=log,
                     stderr=subprocess.STDOUT)


def series(tag_id, minutes=10):
    start = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    r = c.get("/api/sensors/history", headers=H, params={"tag_id": tag_id, "start": start, "window": "1s"})
    return [(x["timestamp"], x["value"]) for x in (r.json().get("points") or []) if x.get("value") is not None]


try:
    t0 = time.time()
    while time.time() - t0 < 180:
        time.sleep(10)
        adv = {t["name"]: series(t["id"]) for t in counters}
        moving = [n for n, s in adv.items() if len(s) >= 2 and s[-1][1] > s[0][1]]
        if len(moving) >= 2:
            break
    print("advancing counters before reload:", moving)
    check(len(moving) >= 1, "counters advance through the real gateway before the reload", {n: s[-3:] for n, s in adv.items()})
    before = {t["id"]: (series(t["id"]) or [(None, None)])[-1] for t in counters}
    pid = p.pid
    r = c.put(f"/api/gateways/{gw['id']}/tags", headers=H,
              json={"tags": [{"tag_id": t["id"]} for t in fix + torno + centro]})
    check(r.status_code == 200 and r.json()["config_version"] == v0 + 1, f"config change → config_version {v0 + 1}", r.text)
    t1 = time.time()
    while time.time() - t1 < 60 and "config_reloaded" not in open(f"{work}/gw.log").read():
        time.sleep(1)
    txt = open(f"{work}/gw.log").read()
    check("config_reloaded" in txt and p.poll() is None and p.pid == pid, "same gateway process hot-reloaded", txt[-600:])
    # the Fixação cycle is >= 40 s (ideal) and one cylinder may skip a cycle: wait (up to 240 s) until every counter
    # that was moving before the reload has moved past its pre-reload value, instead of a fixed 45 s window
    t2 = time.time()
    while time.time() - t2 < 240:
        time.sleep(10)
        pend = [t for t in counters if t["name"] in moving
                and not any(before[t["id"]][0] and ts > before[t["id"]][0] and v > (before[t["id"]][1] or 0)
                            for ts, v in series(t["id"], 15))]
        if not pend:
            break
    for t in counters:
        s = series(t["id"], 15)
        b = before[t["id"]]
        after_reload = [v for ts, v in s if b[0] and ts > b[0]]
        mine = [x for x in s if datetime.fromisoformat(x[0]) >= datetime.fromisoformat(GW_START)]
        drops = [(mine[i - 1], mine[i]) for i in range(1, len(mine)) if mine[i][1] < mine[i - 1][1]]
        check(not drops, f"{t['name']}: never decreases across the reload (before {b[1]}, now {s[-1][1] if s else None})",
              drops[:3])
        if t["name"] in moving:
            check(after_reload and max(after_reload) > (b[1] or 0), f"{t['name']}: keeps advancing after the reload",
                  after_reload[-3:])
    cen = [t for t in centro if series(t["id"], 2)]
    check(cen, f"new Centro de Usinagem tags flow after the reload ({len(cen)} with data)")
    g = c.get(f"/api/gateways/{gw['id']}", headers=H).json()
    check((g.get("last_heartbeat") or {}).get("config_version") == v0 + 1 and g["status"] == "online",
          "heartbeat reports the new config_version, status online", g.get("last_heartbeat"))
finally:
    p.send_signal(signal.SIGTERM)
    p.wait(30)
    c.post(f"/api/gateways/{gw['id']}/revoke", headers=H)
    c.delete(f"/api/gateways/{gw['id']}", headers=H)
print(f"{sum(res)}/{len(res)} passed; work dir {work}")
sys.exit(0 if all(res) else 1)
