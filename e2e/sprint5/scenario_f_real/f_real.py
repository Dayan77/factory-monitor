"""QA Sprint 5 — golden scenario F through the real path:
virtual plant (--scenario F, OPC UA, shifted clock) -> real edge gateway -> MQTT (qa broker :18851) -> QA server
:8160 (db fm_qa_live) -> Influx -> predictive engine (background task, PREDICTIVE_TICK_S=10) -> API.

Server: tenant seeded with SEED_DEMO_PLANT=0 (components must be installed by provisioning at the scenario's
installed_at), SOURCE_TS_MAX_AGE_S >= 1 year, its own broker. Env: API, STATE (json path), SHIFT_DAYS (182 = whole
weeks, both cases), BROKER_PORT.
Phase `setup`: write map + provision (ids to STATE).
Phase `run CASE SPEED HB OPCUA_PORT CONTROL_PORT RUN_S`: playback + gateway for one case (F1: 150 0; F2: 500 10).
Phase `monitor F1|F2`: create the monitor. F1: right after the playback started (an engine run on an empty first
slice skips it for good); F2: only after the playback passed the last day to check (time windows already closed
without data are stored invalid for good).
Phase `check`: compare features/alerts with the JSON (F2: f2_check.py).
"""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

API = os.environ.get("API", "http://127.0.0.1:8160")
QA = os.path.dirname(os.path.abspath(__file__))
REPO = "/home/user/factory-monitor"
PY = f"{REPO}/.venv-edge/bin/python"
DOC = json.load(open(f"{REPO}/docs/scenarios/scenario_f_predictive.json"))
STATE = os.environ.get("STATE", f"{QA}/f_state.json")
SHIFT = int(os.environ.get("SHIFT_DAYS", "182"))


def call(method, path, body=None, token=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def login(email="admin@factory-monitor.com", pw="Admin@1234"):
    return call("POST", "/api/auth/login", {"email": email, "password": pw})[1]["access_token"]


def dev(ts: str) -> str:
    t = datetime.fromisoformat(ts.replace("Z", "+00:00")) - timedelta(days=SHIFT)
    return t.astimezone(timezone.utc).isoformat()


def rules(case):
    return [{"code": r["id"], "type": r["type"], "value": r["value"], "sustained_for": r["sustained_for"],
             "severity": r["severity"]} for r in DOC["cases"][case]["rules"]]


def setup():
    mp, local = f"{QA}/plant.f.json", STATE.replace(".json", ".map.json")
    subprocess.run([PY, "-m", "edge.virtual_plant", "--write-map", mp, "--scenario",
                    "docs/scenarios/scenario_f_predictive.json", "--scenario-shift-days", str(SHIFT)], cwd=REPO, check=True)
    r = subprocess.run([PY, "-m", "edge.src.provision", "--server", API, "--map", mp, "--out", local, "--speed", "500"],
                       cwd=REPO, capture_output=True, text=True)
    print("provision:", r.returncode, (r.stdout or r.stderr)[-800:])
    assert r.returncode == 0
    jwt = login()
    pm = json.load(open(local))
    tag = {t["key"]: t for t in pm["tags"]}
    st = tag["montagem.fixacao.b3.stroke_time"]["tag_id"]
    cur = tag["montagem.transportador.motor_b.current"]["tag_id"]
    pos1 = call("GET", f"/api/tags/{st}", token=jwt)[1]["position_id"]
    pos2 = call("GET", f"/api/tags/{cur}", token=jwt)[1]["position_id"]
    json.dump({"pos1": pos1, "pos2": pos2, "st": st, "cur": cur, "local": local, "shift": SHIFT},
              open(STATE, "w"))


def monitor(case):
    stt = json.load(open(STATE))
    jwt = login()
    pos1, pos2, st, cur = stt["pos1"], stt["pos2"], stt["st"], stt["cur"]
    c1, c2 = DOC["cases"]["F1"], DOC["cases"]["F2"]
    if case == "F1":
        s, m1 = call("POST", "/api/predictive/monitors", {
            "position_id": pos1, "feature": "stroke_time", "tag_id": st, "window_cycles": c1["window"]["cycles"],
            "warmup_windows": c1["warmup_windows"], "failure_limit": c1["failure_limit"]["value"],
            "failure_limit_relative": True, "start_at": dev(c1["installed_at"]), "rules": rules("F1")}, jwt)
        print("monitor F1", s, m1.get("id"), m1.get("state"))
        stt["m1"] = m1["id"]
    else:
        day1 = datetime.fromisoformat(f"{c2['first_day']}T00:00:00-04:00")
        s2, m2 = call("POST", "/api/predictive/monitors", {
            "position_id": pos2, "feature": "current", "tag_id": cur, "window_hours": c2["window"]["duration_h"],
            "warmup_windows": c2["warmup_windows"], "failure_limit": c2["failure_limit"]["value"],
            "failure_limit_relative": False, "start_at": dev(day1.isoformat()), "rules": rules("F2")}, jwt)
        print("monitor F2", s2, m2.get("id") if isinstance(m2, dict) else m2)
        stt["m2"] = m2["id"]
    json.dump(stt, open(STATE, "w"))


def run(case, speed, hb, opcua_port, control_port, run_s):
    stt = json.load(open(STATE))
    local = stt["local"]
    gmap = json.load(open(local))
    prefix = {"F1": "montagem.fixacao.", "F2": "montagem.transportador."}[case]
    gmap["tags"] = [t for t in gmap["tags"] if t["key"].startswith(prefix)]
    tag = f"{QA}/{case}" + os.environ.get("RUN_SUFFIX", "")
    os.makedirs(tag, exist_ok=True)
    local = f"{tag}/map.json"
    json.dump(gmap, open(local, "w"))
    print("tags", [t["key"] for t in gmap["tags"]], flush=True)
    vp = subprocess.Popen([PY, "-m", "edge.virtual_plant", "--scenario", "docs/scenarios/scenario_f_predictive.json",
                           "--scenario-cases", case, "--speed", str(speed), "--scenario-heartbeat-s", str(hb),
                           "--scenario-shift-days", str(SHIFT), "--opcua-port", str(opcua_port),
                           "--modbus-port", str(opcua_port + 1), "--status-port", "0", "--control-port", str(control_port)],
                          cwd=REPO, stdout=open(f"{tag}/vp.log", "w"), stderr=subprocess.STDOUT)
    time.sleep(4)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MQTT_", "GATEWAY_", "SERVER_URL", "ENROLL"))}
    env.update(PLANT_MAP_PATH=local, VIRTUAL_PLANT_HOST="127.0.0.1", VIRTUAL_PLANT_OPCUA_PORT=str(opcua_port),
               VIRTUAL_PLANT_MODBUS_PORT=str(opcua_port + 1), VIRTUAL_PLANT_MAX_CLOCK_SKEW_S="off",
               MQTT_BROKER_HOST="127.0.0.1", MQTT_BROKER_PORT=os.environ.get("BROKER_PORT", "18851"), ENCRYPTION_KEY="",
               BUFFER_DB_PATH=f"{tag}/b.db", COUNTER_DB_PATH=f"{tag}/c.db", TENANT_ID="", GATEWAY_ID=f"qa-{case}")
    gw = subprocess.Popen([PY, "-m", "edge.src.gateway"], cwd=REPO, env=env, stdout=open(f"{tag}/gw.log", "w"),
                          stderr=subprocess.STDOUT)
    t0 = time.time()
    try:
        while time.time() - t0 < run_s:
            time.sleep(60)
            try:
                tr = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{control_port}/truth", timeout=10).read())
                d = tr["degradations"][0] if tr.get("degradations") else {}
                print(f"{int(time.time() - t0)} s: sim {tr.get('sim_time_s', 0) / 3600:.1f} h now={json.dumps(d.get('now'))[:160]}",
                      flush=True)
                if d.get("state") in ("failed", "done", "finished"):
                    pass
            except Exception as e:  # noqa: BLE001
                print("truth error", e, flush=True)
            if vp.poll() is not None:
                print("vp exited", vp.returncode, flush=True)
                break
    finally:
        gw.send_signal(signal.SIGTERM)
        vp.send_signal(signal.SIGTERM)
        for p in (gw, vp):
            try:
                p.wait(30)
            except Exception:  # noqa: BLE001
                p.kill()


def check():
    stt = json.load(open(STATE))
    jwt = login()
    out = {}
    for case, pos in (("F1", stt["pos1"]), ("F2", stt["pos2"])):
        s, feats = call("GET", f"/api/predictive/features?position_id={pos}", token=jwt)
        pts = feats["series"][0]["points"] if feats.get("series") else []
        rows = {w["i"]: w for w in DOC["cases"][case]["windows"]}
        res = []
        for p in pts:
            w = rows.get(p["i"], {})
            res.append({k: p.get(k) for k in ("i", "x", "n", "valid", "start_at", "end_at", "phase", "z", "ewma", "cusum",
                                              "triggers", "severity")} | {"exp_x": w.get("x")})
        s, alerts = call("GET", f"/api/predictive/alerts?position_id={pos}", token=jwt)
        out[case] = {"points": res, "alerts": alerts}
    json.dump(out, open(f"{QA}/f_check.json", "w"), indent=1, default=str)
    for case in out:
        pts = out[case]["points"]
        bad = [p for p in pts if p["exp_x"] is not None and (p["x"] is None or abs(p["x"] - p["exp_x"]) > 1e-9)]
        print(case, "windows", len(pts), "x mismatches", len(bad), [(p["i"], p["x"], p["exp_x"]) for p in bad[:10]])
        print(case, "n", [p["n"] for p in pts][:80])
        print(case, "ends", [p["end_at"] for p in pts][:6], "...", [p["end_at"] for p in pts][-3:])
        print(case, "alerts", json.dumps(out[case]["alerts"], default=str)[:1500])


if __name__ == "__main__":
    ph = sys.argv[1]
    if ph == "setup":
        setup()
    elif ph == "monitor":
        monitor(sys.argv[2])
    elif ph == "run":
        run(sys.argv[2], int(sys.argv[3]), float(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6]), int(sys.argv[7]))
    else:
        check()
