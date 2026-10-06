"""Sprint 5b — MQTT ingestion load test through the REAL path (broker → server pipeline → Influx/Postgres →
predictive engine in its worker process → API), golden scenario F1 data.

Phases
  1. setup     a fresh tenant; two cylinder positions (FAST, SLOW), each with a stroke_time tag and a cycle counter
               (position counter), and a predictive stroke_time monitor (blocks of 10 000 cycles, F1 rules).
  2. fast      the first W blocks of F1 (W·10 000 cycles = W·20 000 readings + 1 reference; W=5 → 100 001) published
               at MAXIMUM speed (QoS 1, publisher in-flight window 1000) for FAST: the server is far slower than the
               publisher → backpressure (the broker keeps the backlog, persistent session).
  3. slow      the same readings for SLOW, rate-limited (SLOW_RATE msg/s).
  4. check     Influx count == published per tag; blocks of both monitors identical (i, start, end, n, median) and
               equal to F1 (n = 10 000, x = docs/scenarios/scenario_f_predictive.json); usage cycles identical
               (= W·10 000); fm_ingest_dropped_total == 0, no dead letters; full recompute (reextract_all) same.
  5. latency   live readings (ts = now, LIVE_RATE msg/s for LIVE_S s) measured end-to-end over the WebSocket
               (publish → server broadcast) while the predictive worker re-extracts heavy time windows (5 days of
               1 Hz motor current written straight to Influx, plus late readings that dirty windows every few
               seconds). p95 must be < 1 s.

Each block's stroke times are built so that its median is exactly the F1 value (4 999 below, two equal, 4 999
above, shuffled with a seed).

Run (repo root; starts its own server on API_PORT with its own database and client id, stops it at the end):

    .venv/bin/python e2e/sprint5b/ingest_load.py            # W=5 (100k readings), PREDICTIVE_WORKER=process
    W=2 SLOW_RATE=400 .venv/bin/python e2e/sprint5b/ingest_load.py
    MODE=inline .venv/bin/python e2e/sprint5b/ingest_load.py --only-latency   # contrast: engine in the API loop

    FULL_F1=1 .venv/bin/python e2e/sprint5b/ingest_load.py   # all 70 F1 blocks (1.4 M readings, ~1 h): the golden
                                                             # checks of scenario_f_real/f1_check.py + full recompute

Env: W (5), SLOW_RATE (500), LIVE_RATE (20), LIVE_S (60), API_PORT (8150), DB (fm_qa_ingest), BROKER (localhost),
BROKER_PORT (1883), MODE (process|inline), KEEP_DB (0). Exit code 0 = every check passed. Results also in
e2e/sprint5b/ingest_load.result.json.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
F = json.loads((ROOT / "docs/scenarios/scenario_f_predictive.json").read_text())
F1 = F["cases"]["F1"]
N = F1["window"]["cycles"]
FULL_F1 = os.getenv("FULL_F1", "0") == "1"       # all 70 blocks of F1 (1.4 M readings), golden checks
W = 70 if FULL_F1 else int(os.getenv("W", "5"))
SHIFT_DAYS = 182                                     # whole weeks: F1 dates (2027) shifted into the past
SLOW_RATE = float(os.getenv("SLOW_RATE", "500"))
LIVE_RATE = float(os.getenv("LIVE_RATE", "20"))
LIVE_S = float(os.getenv("LIVE_S", "60"))
PORT = int(os.getenv("API_PORT", "8150"))
DB = os.getenv("DB", "fm_qa_ingest")
# Own broker by default (docker, infra/mosquitto/mosquitto.conf on BROKER_PORT): the shared dev broker may run
# with mosquitto's defaults (max_queued_messages 1000 → "Outgoing messages are being dropped") if it was started
# before that file changed. OWN_BROKER=0 BROKER=… BROKER_PORT=… uses an existing broker.
OWN_BROKER = os.getenv("OWN_BROKER", "1") == "1"
BROKER = os.getenv("BROKER", "127.0.0.1")
BROKER_PORT = int(os.getenv("BROKER_PORT", "8152" if OWN_BROKER else "1883"))
BROKER_NAME = f"fm_qa_mosq_ingest_{BROKER_PORT}"
MODE = os.getenv("MODE", "process")
API = f"http://127.0.0.1:{PORT}"
CLIENT_ID = f"fm-qa-ingest-{PORT}"
PG = dict(host=os.getenv("POSTGRES_HOST", "localhost"), port=int(os.getenv("POSTGRES_PORT", "5432")),
          user=os.getenv("POSTGRES_USER", "fm_user"), password=os.getenv("POSTGRES_PASSWORD", "fm_dev_password"))
INFLUX = dict(url=os.getenv("INFLUX_URL", "http://localhost:8086"), token=os.getenv("INFLUX_TOKEN", "dev-token-change-me"),
              org=os.getenv("INFLUX_ORG", "factory-monitor"), bucket=os.getenv("INFLUX_BUCKET", "sensors"))
DT = 86400.0 / F1["generator"]["cycles_per_day"]          # 4.32 s per cycle
DT_US = 86400 * 1_000_000 // F1["generator"]["cycles_per_day"]   # exact: block i ends at installed_at + i·12 h

results: list[tuple[bool, str]] = []
report: dict = {}


def check(ok: bool, name: str, got=None, exp=None) -> None:
    results.append((bool(ok), name))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   expected={exp!r} got={got!r}"), flush=True)


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ── server ────────────────────────────────────────────────────────────────────

def server_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MQTT_", "PREDICTIVE_", "POSTGRES_", "INFLUX_"))}
    env.update(POSTGRES_HOST=PG["host"], POSTGRES_PORT=str(PG["port"]), POSTGRES_USER=PG["user"],
               POSTGRES_PASSWORD=PG["password"], POSTGRES_DB=DB, INFLUX_URL=INFLUX["url"],
               INFLUX_TOKEN=INFLUX["token"], INFLUX_ORG=INFLUX["org"], INFLUX_BUCKET=INFLUX["bucket"],
               MQTT_BROKER_HOST=BROKER, MQTT_BROKER_PORT=str(BROKER_PORT), MQTT_CLIENT_ID=CLIENT_ID,
               MQTT_PERSISTENT_SESSION="true", PREDICTIVE_WORKER=MODE, PREDICTIVE_TICK_S="5",
               APP_ENV="development", LOG_LEVEL="WARNING", APP_SECRET_KEY=os.getenv("APP_SECRET_KEY", "qa-load-secret"),
               ENCRYPTION_KEY="", MEDIAMTX_API_URL="http://127.0.0.1:9", SEED_DEMO_PLANT="0",
               INGEST_DEAD_LETTER_FILE=str(Path(__file__).parent / "dead_letters.jsonl"),
               SOURCE_TS_MAX_AGE_S=str(400 * 86400))
    return env


async def recreate_db() -> None:
    import asyncpg
    c = await asyncpg.connect(database="postgres", **PG)
    try:
        await c.execute(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
        await c.execute(f'CREATE DATABASE "{DB}"')
    finally:
        await c.close()


async def drop_db() -> None:
    import asyncpg
    c = await asyncpg.connect(database="postgres", **PG)
    try:
        await c.execute(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
    finally:
        await c.close()


def start_server(log_path: Path) -> subprocess.Popen:
    env = server_env()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=SERVER, env=env, check=True,
                   capture_output=True)
    py = str(ROOT / ".venv/bin/python")
    proc = subprocess.Popen([py, "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(PORT),
                             "--no-proxy-headers"], cwd=SERVER, env=env, stdout=open(log_path, "w"),
                            stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(150):
        time.sleep(0.2)
        try:
            if httpx.get(f"{API}/api/health", timeout=2).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
    raise RuntimeError("server did not start: " + log_path.read_text()[-2000:])


def stop_server(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
    # drop the persistent session from the broker (it would keep queueing every reading for this id)
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=CLIENT_ID, clean_session=True)
    try:
        c.connect(BROKER, BROKER_PORT)
        c.loop_start()
        time.sleep(0.5)
        c.loop_stop()
        c.disconnect()
    except OSError:
        pass


def start_broker() -> None:
    subprocess.run(["docker", "rm", "-f", BROKER_NAME], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--rm", "--name", BROKER_NAME, "-p", f"{BROKER_PORT}:1883", "-v",
                    f"{ROOT}/infra/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro",
                    "eclipse-mosquitto:2.0.22"], check=True, capture_output=True)
    import socket
    for _ in range(50):
        try:
            socket.create_connection((BROKER, BROKER_PORT), timeout=1).close()
            return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("broker did not start")


def broker_dropped() -> str:
    r = subprocess.run(["docker", "exec", BROKER_NAME, "sh", "-c",
                        "grep -c 'Outgoing messages are being dropped' /mosquitto/log/mosquitto.log || true"],
                       capture_output=True, text=True)
    return r.stdout.strip()


def stop_broker() -> None:
    subprocess.run(["docker", "rm", "-f", BROKER_NAME], capture_output=True)


# ── API ───────────────────────────────────────────────────────────────────────

class Api:
    def __init__(self):
        self.c = httpx.Client(base_url=API, timeout=300)

    def login(self, email, pw):
        r = self.c.post("/api/auth/login", json={"email": email, "password": pw})
        r.raise_for_status()
        self.token = r.json()["access_token"]
        self.c.headers["Authorization"] = f"Bearer {self.token}"

    def __getattr__(self, verb):
        def call(path, expect=(200, 201), **kw):
            r = getattr(self.c, verb)(path, **kw)
            if r.status_code not in expect:
                raise RuntimeError(f"{verb.upper()} {path} -> {r.status_code} {r.text[:400]}")
            return r.json() if r.content else None
        return call


def setup(api: Api, installed: datetime) -> dict:
    stamp = uuid.uuid4().hex[:8]
    email = f"qa-load-{stamp}@qa-factoryconnect.com.br"
    api.post("/api/auth/register", json={"company_name": f"QA Carga {stamp}", "admin_name": "QA",
                                         "admin_email": email, "admin_password": "QaCarga#2026"})
    api.login(email, "QaCarga#2026")
    me = api.get("/api/auth/me")
    ids = {"tenant": me["tenant_id"]}
    plant = api.post("/api/plants/", json={"name": "Planta QA", "timezone": F["plant"]["timezone"]})
    line = api.post("/api/lines/", json={"name": "Linha de Montagem", "plant_id": plant["id"]})
    fix = api.post("/api/machines/", json={"line_id": line["id"], "name": "Estação de Fixação"})
    mot = api.post("/api/machines/", json={"line_id": line["id"], "name": "Transportador de Saída"})
    ids.update(machine=fix["id"], motor_machine=mot["id"])
    rules = [{"code": r["id"], "type": r["type"], "value": r["value"], "sustained_for": r["sustained_for"],
              "severity": r["severity"]} for r in F1["rules"]]
    for name in ("fast", "slow"):
        pos = api.post("/api/positions/", json={"machine_id": fix["id"], "name": f"Cilindro B3 {name}",
                                                "component_type": "pneumatic_cylinder"})
        st = api.post("/api/tags/", json={"machine_id": fix["id"], "position_id": pos["id"], "name": f"curso {name}",
                                          "protocol": "mqtt", "address": f"qa/{name}/st", "unit": "ms",
                                          "semantic_role": "stroke_time"})
        ct = api.post("/api/tags/", json={"machine_id": fix["id"], "position_id": pos["id"], "name": f"ciclos {name}",
                                          "protocol": "mqtt", "address": f"qa/{name}/ct", "unit": "",
                                          "semantic_role": "cycle_counter", "counter_max": 2 ** 32 - 1})
        api.patch(f"/api/positions/{pos['id']}", json={"counter_tag_id": ct["id"]})
        mon = api.post("/api/predictive/monitors", json={
            "position_id": pos["id"], "feature": "stroke_time", "tag_id": st["id"], "window_cycles": N,
            "warmup_windows": F1["warmup_windows"], "failure_limit": F1["failure_limit"]["value"],
            "failure_limit_relative": True, "start_at": iso(installed), "rules": rules})
        ids[name] = {"pos": pos["id"], "st": st["id"], "ct": ct["id"], "mon": mon["id"]}
    live = api.post("/api/tags/", json={"machine_id": mot["id"], "name": "temperatura ao vivo", "protocol": "mqtt",
                                        "address": "qa/live", "unit": "°C"})
    ids["live"] = live["id"]
    return ids


# ── data ──────────────────────────────────────────────────────────────────────

def f1_readings(installed: datetime) -> list[tuple[str, datetime, float]]:
    """('ct'|'st', ts, value): reference counter 0 at the installation, then per cycle k the stroke time at t_k and
    the counter = k at the same instant. Block i median = F1 x_i exactly; block i ends at installed_at + i·12 h."""
    out = [("ct", installed, 0.0)]
    xs = [w["x"] for w in F1["windows"][:W]]
    for i, x in enumerate(xs, start=1):
        rnd = random.Random(1000 + i)
        d = [((j % 97) + 1) * 0.013 for j in range(1, N // 2)]
        vals = [round(x - v, 6) for v in d] + [round(x + v, 6) for v in d] + [x, x]
        rnd.shuffle(vals)
        for j, v in enumerate(vals):
            k = (i - 1) * N + j + 1
            t = installed + timedelta(microseconds=k * DT_US)
            out.append(("st", t, v))
            out.append(("ct", t, float(k)))          # same instant as the stroke (tie: belongs to that block)
    return out


def publish(ids: dict, who: str, rows, rate: float | None) -> dict:
    cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"qa-load-pub-{who}-{os.getpid()}")
    cli.max_inflight_messages_set(1000)
    cli.max_queued_messages_set(0)
    acked = {"n": 0}
    cli.on_publish = lambda c, u, mid, rc, props: acked.__setitem__("n", acked["n"] + 1)
    cli.connect(BROKER, BROKER_PORT)
    cli.loop_start()
    tags = ids[who]
    t0 = time.time()
    for k, (key, ts, v) in enumerate(rows):
        cli.publish(f"factory/{ids['tenant']}/sensors/{tags[key]}",
                    json.dumps({"value": v, "unit": "ms" if key == "st" else "", "ts": iso(ts)}), qos=1)
        if rate:
            ahead = (k + 1) / rate - (time.time() - t0)
            if ahead > 0:
                time.sleep(ahead)
        else:
            # maximum speed, but never more than ~5000 queued in paho (its out-queue scan is O(n) per PUBACK)
            while k - acked["n"] > 5000:
                time.sleep(0.001)
    sent = time.time() - t0
    while acked["n"] < len(rows) and time.time() - t0 < 3600:
        time.sleep(0.05)
    total = time.time() - t0
    cli.loop_stop()
    cli.disconnect()
    return {"published": len(rows), "acked_by_broker": acked["n"], "enqueue_s": round(sent, 2),
            "broker_ack_s": round(total, 2), "publish_rate": round(len(rows) / max(total, 1e-9))}


async def influx_counts(tenant: str, tags: list[str], start: datetime, stop: datetime) -> dict:
    from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync
    async with InfluxDBClientAsync(url=INFLUX["url"], token=INFLUX["token"], org=INFLUX["org"]) as c:
        flt = " or ".join(f'r["tag_id"] == "{t}"' for t in tags)
        q = f'''from(bucket: "{INFLUX["bucket"]}") |> range(start: {iso(start - timedelta(seconds=1))}, stop: {iso(stop)})
          |> filter(fn: (r) => r["_measurement"] == "sensors" and r["tenant_id"] == "{tenant}" and ({flt}))
          |> filter(fn: (r) => r["_field"] == "value") |> group(columns: ["tag_id"]) |> count()'''
        out = {t: 0 for t in tags}
        for table in await c.query_api().query(q):
            for rec in table.records:
                out[rec.values["tag_id"]] += int(rec.get_value())
        return out


def metrics() -> dict:
    txt = httpx.get(f"{API}/metrics", timeout=10).text
    out = {}
    for line in txt.splitlines():
        if line.startswith("#"):
            continue
        m = re.match(r'^([a-zA-Z_:]+)(\{[^}]*\})?\s+([-+0-9.eE]+|NaN)$', line)
        if m:
            out[m.group(1) + (m.group(2) or "")] = float(m.group(3))
    return out


def hist_p95(before: dict, after: dict, name: str) -> float | None:
    """p95 (upper bucket bound) of the observations between two scrapes of a Prometheus histogram."""
    buckets = []
    for k, v in after.items():
        m = re.match(rf'^{name}_bucket\{{le="([^"]+)"\}}$', k)
        if m:
            le = math.inf if m.group(1) == "+Inf" else float(m.group(1))
            buckets.append((le, v - before.get(k, 0.0)))
    buckets.sort()
    if not buckets or buckets[-1][1] <= 0:
        return None
    total = buckets[-1][1]
    for le, c in buckets:
        if c >= 0.95 * total:
            return le
    return None


async def wait_ingested(ids: dict, who: str, rows, start, stop, timeout: float) -> tuple[dict, float]:
    want = {ids[who]["st"]: sum(1 for r in rows if r[0] == "st"), ids[who]["ct"]: sum(1 for r in rows if r[0] == "ct")}
    t0 = time.time()
    while True:
        got = await influx_counts(ids["tenant"], list(want), start, stop)
        if got == want or time.time() - t0 > timeout:
            return {"want": want, "got": got}, time.time() - t0
        await asyncio.sleep(2)


def blocks(api: Api, pos: str) -> list[tuple]:
    feats = api.get(f"/api/predictive/features?position_id={pos}")
    pts = feats["series"][0]["points"] if feats["series"] else []
    return [(p["i"], p["start_at"], p["end_at"], p["n"], p["valid"], p["x"]) for p in pts]


# ── latency under engine load ─────────────────────────────────────────────────

async def latency_phase(api: Api, ids: dict) -> dict:
    """Live readings at LIVE_RATE/s, latency publish → WebSocket broadcast, while the worker extracts heavy
    time windows."""
    import websockets
    from influxdb_client import Point
    from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync
    # heavy work for the predictive engine: 5 days of 1 Hz motor current + run_state, straight to Influx
    pos = api.post("/api/positions/", json={"machine_id": ids["motor_machine"], "name": "Motor carga",
                                            "component_type": "electric_motor"})
    cur = api.post("/api/tags/", json={"machine_id": ids["motor_machine"], "position_id": pos["id"], "name": "corrente",
                                       "protocol": "mqtt", "address": "qa/cur", "unit": "A", "semantic_role": "current"})
    run = api.post("/api/tags/", json={"machine_id": ids["motor_machine"], "position_id": pos["id"], "name": "run",
                                       "protocol": "mqtt", "address": "qa/run", "semantic_role": "run_state"})
    now = datetime.now(timezone.utc).replace(microsecond=0)
    d0 = (now - timedelta(days=5)).replace(hour=0, minute=0, second=0)
    t_w = time.time()
    async with InfluxDBClientAsync(url=INFLUX["url"], token=INFLUX["token"], org=INFLUX["org"]) as c:
        w = c.write_api()
        batch = []
        secs = int((now - timedelta(hours=1) - d0).total_seconds())
        for s in range(0, secs):
            t = d0 + timedelta(seconds=s)
            batch.append(Point("sensors").tag("tenant_id", ids["tenant"]).tag("tag_id", cur["id"])
                         .tag("machine_id", ids["motor_machine"]).tag("unit", "A")
                         .field("value", 10.0 + (s % 600) / 600.0).time(t))
            if s % 600 == 0:
                batch.append(Point("sensors").tag("tenant_id", ids["tenant"]).tag("tag_id", run["id"])
                             .tag("machine_id", ids["motor_machine"]).tag("unit", "").field("value", 1.0).time(t))
            if len(batch) >= 20000:
                await w.write(bucket=INFLUX["bucket"], record=batch)
                batch = []
        if batch:
            await w.write(bucket=INFLUX["bucket"], record=batch)
    print(f"  heavy current data written ({secs} points) in {time.time() - t_w:.0f} s", flush=True)

    lat: list[float] = []
    stop = asyncio.Event()

    async def ws_reader():
        url = f"ws://127.0.0.1:{PORT}/ws/sensors/{ids['tenant']}?token={api.token}"
        async with websockets.connect(url, max_size=2 ** 22) as ws:
            while not stop.is_set():
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
                except asyncio.TimeoutError:
                    continue
                if msg.get("type") == "sensor_reading" and msg.get("tag_id") == ids["live"]:
                    sent = datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
                    lat.append((datetime.now(timezone.utc) - sent).total_seconds())

    cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"qa-load-live-{os.getpid()}")
    cli.connect(BROKER, BROKER_PORT)
    cli.loop_start()
    reader = asyncio.create_task(ws_reader())
    await asyncio.sleep(1.0)
    m0 = metrics()
    # the engine work starts with the live traffic: monitor created → next worker tick extracts ~15 windows of 8 h
    mon = api.post("/api/predictive/monitors", json={
        "position_id": pos["id"], "feature": "current", "tag_id": cur["id"], "window_hours": 8, "warmup_windows": 1,
        "start_at": iso(d0), "params": {"baseline_min_windows": 3, "baseline_min_span_h": 0}})
    t0 = time.time()
    k = 0
    late_sent = 0
    while time.time() - t0 < LIVE_S:
        cli.publish(f"factory/{ids['tenant']}/sensors/{ids['live']}",
                    json.dumps({"value": 20.0 + k % 5, "unit": "°C", "ts": iso(datetime.now(timezone.utc))}), qos=1)
        k += 1
        if k % int(max(1, LIVE_RATE * 3)) == 0:    # every ~3 s a late current reading dirties old windows
            t_old = d0 + timedelta(seconds=random.randint(0, secs - 1))
            cli.publish(f"factory/{ids['tenant']}/sensors/{cur['id']}",
                        json.dumps({"value": 10.5, "unit": "A", "ts": iso(t_old)}), qos=1)
            late_sent += 1
        await asyncio.sleep(max(0.0, k / LIVE_RATE - (time.time() - t0)))
    await asyncio.sleep(2)
    stop.set()
    await reader
    cli.loop_stop()
    cli.disconnect()
    m1 = metrics()
    lat.sort()
    feats = api.get(f"/api/predictive/features?position_id={pos['id']}")
    windows = len(feats["series"][0]["points"]) if feats["series"] else 0
    p = (lambda q: lat[min(len(lat) - 1, int(q * len(lat)))] if lat else None)
    return {"mode": MODE, "live_published": k, "live_received_ws": len(lat), "late_readings": late_sent,
            "ws_latency_p50_s": p(0.5), "ws_latency_p95_s": p(0.95), "ws_latency_max_s": lat[-1] if lat else None,
            "ingest_latency_p95_le_s": hist_p95(m0, m1, "fm_ingest_latency_seconds"),
            "engine_windows_extracted": windows, "monitor": mon["id"]}


def P(x: str) -> datetime:
    return datetime.fromisoformat(x.replace("Z", "+00:00"))


def full_f1_checks(api: Api, ids: dict) -> None:
    """Golden F1 through the real ingestion path (the checks of e2e/sprint5/scenario_f_real/f1_check.py)."""
    from datetime import date
    X = F["expected"]["F1"]
    sh = lambda v: iso(P(v) - timedelta(days=SHIFT_DAYS)).replace(".000000", "")  # noqa: E731
    shd = lambda v: (date.fromisoformat(v) - timedelta(days=SHIFT_DAYS)).isoformat() if v else v  # noqa: E731
    close = lambda a, b, tol=1e-6: a is not None and b is not None and abs(a - b) <= tol * max(1.0, abs(b))  # noqa
    f = api.get(f"/api/predictive/features?position_id={ids['fast']['pos']}")
    pts = {p["i"]: p for p in f["series"][0]["points"]}
    rows = {w["i"]: w for w in F1["windows"]}
    check(sorted(pts) == list(range(1, 71)), f"F1: 70 windows extracted ({len(pts)})", sorted(pts)[-3:])
    bad = [(i, pts.get(i, {}).get("x"), rows[i]["x"]) for i in rows if not close(pts.get(i, {}).get("x"), rows[i]["x"], 1e-12)]
    check(not bad, "F1: x (block median) exact for every window", bad[:10])
    check(all(p["n"] == N for p in pts.values()), "F1: n = 10 000 in every block", [(i, p["n"]) for i, p in pts.items() if p["n"] != N][:5])
    inst = P(F1["installed_at"]) - timedelta(days=SHIFT_DAYS)
    bade = [(i, p["end_at"]) for i, p in pts.items() if P(p["end_at"]) != inst + timedelta(hours=12 * i)]
    check(not bade, "F1: every block ends exactly at installed_at + i·12 h", bade[:5])
    mon = api.get(f"/api/predictive/monitors/{ids['fast']['mon']}")
    b = [x for x in mon["baselines"] if x.get("established_at")][0]
    eb = X["baseline"]
    check(b["from_i"] == eb["from_i"] and b["to_i"] == eb["to_i"] and close(b["m0"], eb["m0"]) and close(b["sigma"], eb["sigma"]),
          f"F1: baseline {eb['from_i']}–{eb['to_i']} m0 {eb['m0']} σ {eb['sigma']}", b)
    for tr in X["trace"]:
        p = pts[tr["i"]]
        d = [(k, p.get(k), v) for k, v in tr.items() if k not in ("i", "triggers") and not close(p.get(k), v, 1e-3)]
        if sorted(p.get("triggers") or []) != sorted(tr["triggers"]):
            d.append(("triggers", p.get("triggers"), tr["triggers"]))
        check(not d, f"F1: trace window {tr['i']}", d)
    al = api.get(f"/api/predictive/alerts?position_id={ids['fast']['pos']}&state=open&state=acknowledged&state=closed&state=converted")
    check(len(al) == 1, f"F1: exactly one alert ({len(al)})", al)
    ea = X["alert"]
    if al:
        a = al[0]
        check(a["opened_window"] == ea["opened_window"] and P(a["opened_at"]) == P(sh(ea["opened_at"])),
              f"F1: alert opened at window {ea['opened_window']} ({sh(ea['opened_at'])})", (a["opened_window"], a["opened_at"]))
        check(sorted(a["first_triggers"]) == sorted(ea["first_triggers"]), "F1: first triggers", a["first_triggers"])
        check(a["severity"] == ea["severity"] and a.get("escalation_windows") == ea["escalation_windows"],
              "F1: severity / escalation windows", (a["severity"], a.get("escalation_windows")))
        check(a.get("triggers_seen") == ea["triggers_seen"], "F1: triggers_seen", a.get("triggers_seen"))
        fail = P(F1["failure"]["failed_at"]) - timedelta(days=SHIFT_DAYS)
        lt = {k: (fail - P(v)).total_seconds() / 3600 for k, v in (a.get("escalations") or {}).items()}
        check(lt == X["lead_time_h"], f"F1: lead time by severity {X['lead_time_h']} h", lt)
    for cp in X["rul_checkpoints"]:
        r = pts[cp["window"]]["rul"] or {}
        diffs = []
        for k, v in cp.items():
            if k == "window" or k.startswith("true_"):
                continue
            got = r.get(k)
            if k in ("today", "projected_date", "projected_date_low", "projected_date_high"):
                v = shd(v)
            if isinstance(v, float):
                if not close(got, v, 1e-4):
                    diffs.append((k, got, v))
            elif got != v:
                diffs.append((k, got, v))
        check(not diffs, f"F1: RUL checkpoint window {cp['window']}", diffs)
    rc = api.post("/api/predictive/recompute", json={"monitor_id": ids["fast"]["mon"], "reextract_all": True})
    check(rc["same"] and rc["monitors"][0]["windows_reextracted"] == 0,
          "F1: full recompute from Influx (reextract_all) gives identical blocks and alerts", rc["monitors"][0].get("differences"))


# ── main ──────────────────────────────────────────────────────────────────────

async def main() -> int:
    only_latency = "--only-latency" in sys.argv
    log_path = Path(__file__).parent / f"server_{MODE}.log"
    await recreate_db()
    if OWN_BROKER:
        start_broker()
    proc = start_server(log_path)
    try:
        api = Api()
        installed = (datetime.now(timezone.utc) - timedelta(days=math.ceil(W * N * DT / 86400) + 1)).replace(
            minute=0, second=0, microsecond=0)
        if FULL_F1:
            installed = P(F1["installed_at"]) - timedelta(days=SHIFT_DAYS)
        ids = setup(api, installed)
        report["ids"] = ids
        rows = f1_readings(installed)
        start, stop = installed, rows[-1][1] + timedelta(seconds=1)
        if not only_latency:
            print(f"\n[fast] {len(rows)} readings at maximum speed", flush=True)
            report["fast"] = publish(ids, "fast", rows, None)
            print("  ", report["fast"], flush=True)
            cnt, waited = await wait_ingested(ids, "fast", rows, start, stop, timeout=7200)
            report["fast"]["drain_s_after_publish"] = round(waited, 1)
            print(f"  ingested: {cnt} (+{waited:.0f} s after the broker took everything)", flush=True)
            check(cnt["got"] == cnt["want"], f"FAST: Influx count == published ({sum(cnt['want'].values())})",
                  cnt["got"], cnt["want"])
            if FULL_F1:
                await asyncio.sleep(15)
                api.post(f"/api/predictive/monitors/{ids['fast']['mon']}/process")
                full_f1_checks(api, ids)
                mt = metrics()
                check(mt.get("fm_ingest_dropped_total", 0.0) == 0, "fm_ingest_dropped_total == 0")
                check(broker_dropped() in ("0", ""), "broker never dropped outgoing messages")
                return finish()
            print(f"\n[slow] {len(rows)} readings at {SLOW_RATE:.0f}/s", flush=True)
            report["slow"] = publish(ids, "slow", rows, SLOW_RATE)
            cnt2, _ = await wait_ingested(ids, "slow", rows, start, stop, timeout=1800)
            check(cnt2["got"] == cnt2["want"], "SLOW: Influx count == published", cnt2["got"], cnt2["want"])
            await asyncio.sleep(8)                       # last usage commits
            for who in ("fast", "slow"):
                api.post(f"/api/predictive/monitors/{ids[who]['mon']}/process")
            bf, bs = blocks(api, ids["fast"]["pos"]), blocks(api, ids["slow"]["pos"])
            report["blocks_fast"], report["blocks_slow"] = bf, bs
            check(bf == bs, "predictive blocks FAST == SLOW (i, start, end, n, median)", bf, bs)
            exp = [(w["i"], N, w["x"]) for w in F1["windows"][:W]]
            check([(b[0], b[3], b[5]) for b in bf] == exp, f"blocks == golden F1 windows 1..{W} (n = {N}, x)",
                  [(b[0], b[3], b[5]) for b in bf], exp)
            ends = [iso(installed + timedelta(hours=12 * i)) for i in range(1, W + 1)]
            check([b[2].replace("+00:00", "Z") for b in bf] == ends, "block i ends at the counter reading = i·N",
                  [b[2] for b in bf], ends)
            uf = api.get(f"/api/positions/{ids['fast']['pos']}")["usage"]["cycles_total"]
            us = api.get(f"/api/positions/{ids['slow']['pos']}")["usage"]["cycles_total"]
            report["usage"] = {"fast": uf, "slow": us}
            check(uf == us == W * N, f"usage cycles FAST == SLOW == {W * N}", (uf, us), W * N)
            rc = api.post("/api/predictive/recompute", json={"monitor_id": ids["fast"]["mon"], "reextract_all": True})
            check(rc["same"] and rc["monitors"][0]["windows_reextracted"] == 0,
                  "full recompute from Influx (reextract_all) reproduces blocks and alerts", rc["monitors"][0].get(
                      "differences"), [])
            mt = metrics()
            dropped = mt.get("fm_ingest_dropped_total", 0.0)
            dls = sum(v for k, v in mt.items() if k.startswith("fm_ingest_dead_letters_total"))
            report["metrics"] = {"dropped": dropped, "dead_letters": dls,
                                 "influx_retries": mt.get("fm_influx_write_retries_total"),
                                 "late_readings": mt.get("fm_ingest_late_readings_total")}
            check(dropped == 0, "fm_ingest_dropped_total == 0", dropped, 0)
            check(dls == 0, "no dead letters", dls, 0)
            if OWN_BROKER:
                drops = broker_dropped()
                report["broker_drop_warnings"] = drops
                check(drops in ("0", ""), "broker never dropped outgoing messages", drops, "0")
        print("\n[latency] live readings while the predictive engine extracts heavy windows", flush=True)
        lat = await latency_phase(api, ids)
        report["latency"] = lat
        print("  ", lat, flush=True)
        check(lat["live_received_ws"] >= 0.98 * lat["live_published"], "live readings broadcast",
              lat["live_received_ws"], lat["live_published"])
        check(lat["ws_latency_p95_s"] is not None and lat["ws_latency_p95_s"] < 1.0,
              f"ingestion latency p95 < 1 s while the engine runs (mode {MODE})", lat["ws_latency_p95_s"], "< 1.0")
        check(lat["engine_windows_extracted"] > 0, "the engine really ran (time windows extracted)",
              lat["engine_windows_extracted"], "> 0")
    finally:
        stop_server(proc)
        if OWN_BROKER:
            stop_broker()
        if os.getenv("KEEP_DB", "0") != "1":
            await drop_db()
    return finish()


def finish() -> int:
    tag = "f1full" if FULL_F1 else MODE
    report["checks"] = [{"ok": ok, "name": n} for ok, n in results]
    (Path(__file__).parent / f"ingest_load.result.{tag}.json").write_text(json.dumps(report, indent=1, default=str))
    bad = [n for ok, n in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed" + (f"; FAILED: {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
