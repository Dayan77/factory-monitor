"""QA helper: creates + enrolls a gateway for the machines given (live virtual plant), assigns their tags and runs
the REAL edge gateway in the foreground until SIGTERM (then revokes + deletes it). Used by break_fault_live.py runs.

    API=http://localhost:8161 BROKER_PORT=18852 VP_PORT=8200 python e2e/sprint5/run_line_gateway.py "Robô de Solda" "Estação de Fixação"
"""
import os, signal, subprocess, sys, tempfile, time
import httpx

API = os.getenv("API", "http://localhost:8161")
VP = int(os.getenv("VP_PORT", "8200"))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
c = httpx.Client(base_url=API, timeout=60, trust_env=False)
H = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"email": "admin@factory-monitor.com",
                                                                  "password": "Admin@1234"}).json()["access_token"]}
tree = c.get("/api/plants/tree", headers=H).json()
mach = {m["name"]: m["id"] for p in tree for l in p["lines"] for m in l["machines"]}
ids = {mach[n] for n in sys.argv[1:]}
tags = [t for t in c.get("/api/tags/?limit=1000", headers=H).json() if t["machine_id"] in ids]
devs = [{"id": "opcua-plc", "protocol": "opcua", "endpoint": f"opc.tcp://127.0.0.1:{VP}/factoryconnect/virtual-plant/"},
        {"id": "modbus-plc", "protocol": "modbus", "host": "127.0.0.1", "port": VP + 1}]
gw = c.post("/api/gateways/", headers=H, json={"name": f"QA line {int(time.time())}", "plant_id": tree[0]["id"],
                                              "devices": devs, "heartbeat_s": 10}).json()
r = c.put(f"/api/gateways/{gw['id']}/tags", headers=H, json={"tags": [{"tag_id": t["id"]} for t in tags]})
print("assign", r.status_code, len(tags), r.text[:200], flush=True)
code = c.post(f"/api/gateways/{gw['id']}/enroll-token", headers=H).json()["code"]
w = tempfile.mkdtemp(prefix="qa-linegw-")
env = {k: v for k, v in os.environ.items() if not k.startswith(("MQTT_", "GATEWAY_", "PLANT_MAP", "SERVER_URL", "ENROLL", "TENANT_ID"))}
env.update(SERVER_URL=API, ENROLL_CODE=code, GATEWAY_CREDENTIALS_PATH=f"{w}/creds.json", GATEWAY_CONFIG_CACHE=f"{w}/cfg.json",
           MQTT_BROKER_HOST="127.0.0.1", MQTT_BROKER_PORT=os.getenv("BROKER_PORT", "18852"), MQTT_TLS="false",
           BUFFER_DB_PATH=f"{w}/buf.db", COUNTER_DB_PATH=f"{w}/cnt.db", ENCRYPTION_KEY="", LOG_LEVEL="INFO")
p = subprocess.Popen([f"{REPO}/.venv-edge/bin/python", "-m", "edge.src.gateway"], cwd=REPO, env=env,
                     stdout=open(f"{w}/gw.log", "w"), stderr=subprocess.STDOUT)
print("gateway", gw["id"], "pid", p.pid, "log", f"{w}/gw.log", flush=True)
stop = []
signal.signal(signal.SIGTERM, lambda *a: stop.append(1))
while not stop and p.poll() is None:
    time.sleep(1)
p.send_signal(signal.SIGTERM); p.wait(30)
c.post(f"/api/gateways/{gw['id']}/revoke", headers=H); print("delete", c.delete(f"/api/gateways/{gw['id']}", headers=H).status_code)
