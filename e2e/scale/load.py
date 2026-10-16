"""Sprint 7 — performance & scale harness (docs/performance.md).

Simulates T tenants × L lines × M machines (default 5 × 4 × 10 = 200 machines), each machine publishing 12 signal
tags at 1 Hz (run_state, fault_state and 10 analog) plus its part counter: ~2 600 readings/s through the REAL path —
MQTT broker (own container) → the server's ingestion pipeline → InfluxDB + Postgres → detectors in the worker process
— while API clients hammer the read endpoints. History of HISTORY_DAYS (default 30) is seeded in Postgres first, so the
30-day queries read real volume.

Measured (results in e2e/scale/result.json, summary on stdout):
  * ingestion lag  — end-to-end: publish (reading ts) → live WebSocket message, for every reading of tenant 1
                     (p50/p95/p99), plus the server's fm_mqtt_processing_lag_seconds histogram (all tenants);
  * API latency    — p50/p95/max of GET /api/overview, /api/oee (line, 30 days, group_by=day),
                     /api/losses/pareto (line, 30 days), /api/maintenance-plans/status, under the load;
  * report         — POST /api/reports/generate (weekly_oee, monthly_maintenance, daily_production; json) duration;
  * detectors      — fm_detector_lag_max_seconds (downtime) at the end; dropped readings; Influx count check.

Targets (Sprint 7): ingestion lag p95 < 2 s, API p95 < 500 ms at that load.

Time: plant time = wall time at SPEED=1 (readings are live). SPEED=k sends k plant-seconds per wall second
(k × the rate — a stress multiplier); plant time then starts DURATION_S·(1 − 1/k) in the past so it ends at "now".

Run (repo root; starts its own broker on BROKER_PORT, its own server on API_PORT with its own database, stops them):

    .venv/bin/python e2e/scale/load.py                       # 5×4×10, 30 min
    DURATION_S=300 .venv/bin/python e2e/scale/load.py        # 5 min
    TENANTS=1 LINES=2 MACHINES=5 DURATION_S=60 .venv/bin/python e2e/scale/load.py   # smoke

Env: TENANTS (5), LINES (4), MACHINES (10), HISTORY_DAYS (30), DURATION_S (1800), SPEED (1), PUBLISHERS (4),
API_CONC (4; 0 = no API probes), REPORTS (1; 0 = no report probe), API_PORT (8160), BROKER_PORT (8162),
DB (fm_scale), KEEP_DB (0: drop the database and the InfluxDB buckets at the end), INGEST (process | inline),
SHARDS (ingestion processes; 1 is the best on a 4-vCPU host — docs/performance.md §3), BROKER_CONF
(e2e/scale/mosquitto.conf), DIRECT (1: talk to the Postgres/InfluxDB containers by their docker-network IP, as the
server does in production, instead of through docker-proxy on localhost), SCALE_BUCKET (fm_scale_<API_PORT>: the
harness writes to its own InfluxDB bucket, never to the dev bucket), API_CACHE_TTL_S (server response cache; 0 =
measure uncached), OUT (e2e/scale/result.json). Exit code 0 = targets met.
"""
from __future__ import annotations

import asyncio
import json
import math
import multiprocessing as mp
import os
import random
import re
import signal
import socket
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
TENANTS = int(os.getenv("TENANTS", "5"))
LINES = int(os.getenv("LINES", "4"))
MACHINES = int(os.getenv("MACHINES", "10"))
HISTORY_DAYS = int(os.getenv("HISTORY_DAYS", "30"))
DURATION_S = float(os.getenv("DURATION_S", "1800"))
SPEED = float(os.getenv("SPEED", "1"))
PUBLISHERS = int(os.getenv("PUBLISHERS", "4"))
API_CONC = int(os.getenv("API_CONC", "4"))
REPORTS = os.getenv("REPORTS", "1") == "1"
PORT = int(os.getenv("API_PORT", "8160"))
BROKER_PORT = int(os.getenv("BROKER_PORT", "8162"))
DB = os.getenv("DB", "fm_scale")
KEEP_DB = os.getenv("KEEP_DB", "0") == "1"
INGEST = os.getenv("INGEST", "process")                 # inline | process (MQTT ingestion in its own processes)
SHARDS = int(os.getenv("SHARDS", "1"))
METRICS_PORT = int(os.getenv("WORKER_METRICS_PORT", "8170"))   # ingestion shard k: +k; detector worker: +100
API = f"http://127.0.0.1:{PORT}"
BROKER = "127.0.0.1"
BROKER_NAME = f"fm_scale_mosq_{BROKER_PORT}"
CLIENT_ID = f"fm-scale-{PORT}"
def _container_ip(name: str):
    """IP of a dev container on its docker network — the harness talks to Postgres/InfluxDB directly, as the
    server does in production (docker network), instead of through docker-proxy on localhost, which costs ~0.3 ms
    of CPU per round trip and becomes the bottleneck at 2 600 readings/s (docs/performance.md). DIRECT=0: localhost."""
    if os.getenv("DIRECT", "1") != "1":
        return None
    try:
        ip = subprocess.run(["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                             name], capture_output=True, text=True, timeout=10).stdout.strip()
        return ip or None
    except (OSError, subprocess.SubprocessError):
        return None


_PG_IP = _container_ip("fm_postgres")
_INFLUX_IP = _container_ip("fm_influxdb")
PG = dict(host=os.getenv("POSTGRES_HOST", _PG_IP or "localhost"), port=int(os.getenv("POSTGRES_PORT", "5432")),
          user=os.getenv("POSTGRES_USER", "fm_user"), password=os.getenv("POSTGRES_PASSWORD", "fm_dev_password"))
# Own InfluxDB bucket (created at the start, deleted at the end unless KEEP_DB=1): the load never mixes with the dev
# bucket `sensors` (and the downsampling buckets/tasks of services/influx_downsampling.py get their own names).
INFLUX = dict(url=os.getenv("INFLUX_URL", f"http://{_INFLUX_IP or 'localhost'}:8086"), token=os.getenv("INFLUX_TOKEN", "dev-token-change-me"),
              org=os.getenv("INFLUX_ORG", "factory-monitor"), bucket=os.getenv("SCALE_BUCKET", f"fm_scale_{PORT}"))
OUT = Path(os.getenv("OUT", str(Path(__file__).parent / "result.json")))
# broker config: the dev one with a larger in-flight window (BROKER_CONF=infra/mosquitto/mosquitto.conf for the
# default window of 100 — docs/performance.md shows the difference)
BROKER_CONF = Path(os.getenv("BROKER_CONF", str(Path(__file__).parent / "mosquitto.conf"))).resolve()
LOG = Path(__file__).parent / "server.log"
UTC = timezone.utc


def iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z")


def pct(xs: list[float], p: float):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, math.ceil(p / 100 * len(s)) - 1))]


# ── infrastructure ───────────────────────────────────────────────────────────

def server_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MQTT_", "PREDICTIVE_", "POSTGRES_", "INFLUX_"))}
    env.update(POSTGRES_HOST=PG["host"], POSTGRES_PORT=str(PG["port"]), POSTGRES_USER=PG["user"],
               POSTGRES_PASSWORD=PG["password"], POSTGRES_DB=DB, INFLUX_URL=INFLUX["url"],
               INFLUX_TOKEN=INFLUX["token"], INFLUX_ORG=INFLUX["org"], INFLUX_BUCKET=INFLUX["bucket"],
               MQTT_BROKER_HOST=BROKER, MQTT_BROKER_PORT=str(BROKER_PORT), MQTT_CLIENT_ID=CLIENT_ID,
               MQTT_PERSISTENT_SESSION="true", PREDICTIVE_WORKER="process", DETECTORS_IN_WORKER="true",
               MQTT_INGEST=INGEST, MQTT_INGEST_SHARDS=str(SHARDS), WORKER_METRICS_PORT=str(METRICS_PORT),
               APP_ENV="development", LOG_LEVEL="WARNING",
               APP_SECRET_KEY=os.getenv("APP_SECRET_KEY", "scale-harness-secret-not-used-elsewhere"),
               ENCRYPTION_KEY="", MEDIAMTX_API_URL="http://127.0.0.1:9", SEED_DEMO_PLANT="0", DEMO_BOT_ENABLED="false",
               RATE_LIMIT_ENABLED="false", SOURCE_TS_MAX_AGE_S=str(400 * 86400),
               # access tokens last 15 min in production (docs/security.md); the probes re-login on 401 anyway
               JWT_ACCESS_TOKEN_EXPIRE_MINUTES=os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "240"))
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


def _influx_api(method: str, path: str, **kw) -> httpx.Response:
    return httpx.request(method, INFLUX["url"] + path, headers={"Authorization": f"Token {INFLUX['token']}"},
                         timeout=30, **kw)


def influx_org_id() -> str:
    return _influx_api("GET", "/api/v2/orgs", params={"org": INFLUX["org"]}).json()["orgs"][0]["id"]


def drop_buckets() -> None:
    """Deletes the harness bucket and its downsampling buckets/tasks (name prefix = the harness bucket)."""
    org = influx_org_id()
    for b in _influx_api("GET", "/api/v2/buckets", params={"orgID": org, "limit": 100}).json().get("buckets", []):
        if b["name"] == INFLUX["bucket"] or b["name"].startswith(INFLUX["bucket"] + "_"):
            _influx_api("DELETE", f"/api/v2/buckets/{b['id']}")
    for t in _influx_api("GET", "/api/v2/tasks", params={"orgID": org, "limit": 500}).json().get("tasks", []):
        if t["name"].startswith(f"fm_downsample_{INFLUX['bucket']}"):
            _influx_api("DELETE", f"/api/v2/tasks/{t['id']}")


def recreate_bucket() -> None:
    drop_buckets()
    r = _influx_api("POST", "/api/v2/buckets", json={"orgID": influx_org_id(), "name": INFLUX["bucket"],
                                                      "retentionRules": [{"type": "expire", "everySeconds": 7776000}]})
    r.raise_for_status()


def start_broker() -> None:
    subprocess.run(["docker", "rm", "-f", BROKER_NAME], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--rm", "--name", BROKER_NAME, "-p", f"{BROKER_PORT}:1883", "-v",
                    f"{BROKER_CONF}:/mosquitto/config/mosquitto.conf:ro",
                    "eclipse-mosquitto:2.0.22"], check=True, capture_output=True)
    for _ in range(50):
        try:
            socket.create_connection((BROKER, BROKER_PORT), timeout=1).close()
            return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("broker did not start")


def stop_broker() -> None:
    subprocess.run(["docker", "rm", "-f", BROKER_NAME], capture_output=True)


def start_server() -> subprocess.Popen:
    env = server_env()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=SERVER, env=env, check=True,
                   capture_output=True)
    proc = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1",
                             "--port", str(PORT), "--no-proxy-headers"], cwd=SERVER, env=env,
                            stdout=open(LOG, "w"), stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(200):
        time.sleep(0.25)
        try:
            if httpx.get(f"{API}/api/health", timeout=2).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
    raise RuntimeError("server did not start: " + LOG.read_text()[-3000:])


def stop_server(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)


async def seed_data(now: datetime) -> dict:
    os.environ.update({k: v for k, v in server_env().items() if k.startswith(("POSTGRES_", "INFLUX_", "APP_"))})
    sys.path.insert(0, str(SERVER))
    sys.path.insert(0, str(Path(__file__).parent))
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from seed import seed
    from src.config import settings
    engine = create_async_engine(settings.database_url)
    try:
        dsn = f"postgresql://{PG['user']}:{PG['password']}@{PG['host']}:{PG['port']}/{DB}"
        return await seed(async_sessionmaker(engine, expire_on_commit=False), dsn, TENANTS, LINES, MACHINES,
                          HISTORY_DAYS, now)
    finally:
        await engine.dispose()


def metrics() -> dict:
    """Prometheus text of the app + the worker processes → {name{labels}: value}, summed over the processes
    (histograms: _bucket/_count/_sum lines)."""
    urls = [f"{API}/metrics"] + [f"http://127.0.0.1:{METRICS_PORT + k}/metrics" for k in range(SHARDS)] \
        + [f"http://127.0.0.1:{METRICS_PORT + 100}/metrics"]
    out: dict = {}
    for url in urls:
        try:
            txt = httpx.get(url, timeout=10).text
        except httpx.HTTPError:
            continue
        for line in txt.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            k, _, v = line.rpartition(" ")
            try:
                out[k] = out.get(k, 0.0) + float(v)
            except ValueError:
                pass
    return out


def hist_quantile(m0: dict, m1: dict, name: str, q: float):
    """Quantile of a histogram between two scrapes (upper bound of the bucket, like histogram_quantile)."""
    pat = re.compile(rf'^{name}_bucket\{{.*le="([^"]+)".*\}}$')
    bs = []
    for k, v in m1.items():
        mm = pat.match(k)
        if mm:
            le = float("inf") if mm.group(1) == "+Inf" else float(mm.group(1))
            bs.append((le, v - m0.get(k, 0.0)))
    bs.sort()
    if not bs or bs[-1][1] <= 0:
        return None
    target = q * bs[-1][1]
    for le, c in bs:
        if c >= target:
            return le
    return None


# ── publishers (one process per slice of machines) ───────────────────────────

def publisher(k: int, machines: list[dict], plant_t0: float, wall_t0: float, duration_s: float, speed: float,
              counts: "mp.Array") -> None:
    import paho.mqtt.client as mqtt
    cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"fm-scale-pub-{k}-{os.getpid()}")
    cli.max_inflight_messages_set(2000)
    cli.max_queued_messages_set(0)
    cli.connect(BROKER, BROKER_PORT, keepalive=120)
    cli.loop_start()
    rnd = random.Random(k)
    state = {m["id"]: {"parts": 0, "analog": [rnd.uniform(10, 90) for _ in range(10)]} for m in machines}
    sent = 0
    tick = 0
    while True:
        wall_target = wall_t0 + tick / speed
        if wall_target - wall_t0 >= duration_s:
            break
        delay = wall_target - time.time()
        if delay > 0:
            time.sleep(delay)
        plant = plant_t0 + tick
        ts = iso(datetime.fromtimestamp(plant, UTC))
        for i, m in enumerate(machines):
            st = state[m["id"]]
            cyc = int(plant + i * 37) % 1200            # every 20 min: 3 min stop; every 5 min a 40 s micro-stop
            running = not (cyc < 180 or 600 <= cyc < 640)
            fault = 3 if (cyc < 180 and (int(plant) // 1200) % 4 == 0) else 0
            if running and rnd.random() < 1.0 / m["pri"]:
                st["parts"] += 1
            tags = m["tags"]
            base = f"factory/{m['tenant_id']}/sensors/"
            cli.publish(base + tags["run"], json.dumps({"value": 1 if running else 0, "ts": ts}), qos=1)
            cli.publish(base + tags["fault"], json.dumps({"value": fault, "ts": ts}), qos=1)
            for j, name in enumerate(m["analog_names"]):
                st["analog"][j] += rnd.uniform(-0.5, 0.5)
                cli.publish(base + tags[name], json.dumps({"value": round(st["analog"][j], 3), "unit": "u", "ts": ts}),
                            qos=1)
            cli.publish(base + m["counter"], json.dumps({"value": st["parts"], "ts": ts}), qos=1)
            sent += 13
        tick += 1
        counts[k] = sent
    time.sleep(2)
    cli.loop_stop()
    cli.disconnect()
    counts[k] = sent


# ── API probes ───────────────────────────────────────────────────────────────

async def api_probe(tokens: list[dict], stop: asyncio.Event, lat: dict, conc: int) -> None:
    now = datetime.now(UTC)
    start30 = iso(now - timedelta(days=30))

    def reqs(t: dict):
        ln = random.choice(t["lines"])
        return [
            ("overview", "GET", "/api/overview", {}),
            ("oee_30d", "GET", "/api/oee", {"scope": "line", "id": ln["id"], "start": start30, "group_by": "day"}),
            ("pareto_30d", "GET", "/api/losses/pareto", {"scope": "line", "id": ln["id"], "start": start30}),
            ("plans_status", "GET", "/api/maintenance-plans/status", {}),
        ]

    async def worker(w: int):
        async with httpx.AsyncClient(base_url=API, timeout=60) as c:
            k = w
            while not stop.is_set():
                t = tokens[k % len(tokens)]
                for name, method, path, params in reqs(t):
                    if stop.is_set():
                        break
                    t0 = time.perf_counter()
                    try:
                        r = await c.request(method, path, params=params,
                                            headers={"Authorization": f"Bearer {t['token']}"})
                        code = r.status_code
                    except httpx.HTTPError as e:
                        code = type(e).__name__
                    dt = time.perf_counter() - t0
                    if code == 200:                     # latency of answers only (an error is not a fast answer)
                        lat.setdefault(name, []).append(dt)
                    else:
                        lat.setdefault(name + "_errors", []).append(code)
                        if code == 401:
                            t["token"] = await asyncio.to_thread(login, t)
                k += 1

    await asyncio.gather(*(worker(w) for w in range(conc)))


async def report_probe(tokens: list[dict], stop: asyncio.Event, lat: dict) -> None:
    async with httpx.AsyncClient(base_url=API, timeout=300) as c:
        k = 0
        while not stop.is_set():
            t = tokens[k % len(tokens)]
            k += 1
            for code, scope in (("weekly_oee", "plant"), ("monthly_maintenance", "plant"), ("daily_production", "plant")):
                sid = t["plant_id"]
                t0 = time.perf_counter()
                try:
                    r = await c.post("/api/reports/generate", headers={"Authorization": f"Bearer {t['token']}"},
                                     json={"code": code, "scope_type": scope, "scope_id": sid, "format": "json"})
                    err = None if r.status_code == 200 else f"{r.status_code} {r.text[:120]}"
                    if r.status_code == 401:
                        t["token"] = await asyncio.to_thread(login, t)
                except httpx.HTTPError as e:
                    err = type(e).__name__
                if err:
                    lat.setdefault(f"report_{code}_errors", []).append(err)
                else:
                    lat.setdefault(f"report_{code}", []).append(time.perf_counter() - t0)
            for _ in range(30):
                if stop.is_set():
                    break
                await asyncio.sleep(1)


async def ws_probe(tenant: dict, stop: asyncio.Event, lat: list) -> None:
    import websockets
    url = f"ws://127.0.0.1:{PORT}/ws/sensors/{tenant['id']}?token={tenant['token']}"
    async with websockets.connect(url, max_size=2 ** 24) as ws:
        while not stop.is_set():
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
            except asyncio.TimeoutError:
                continue
            if msg.get("type") == "sensor_reading" and msg.get("timestamp"):
                sent = datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
                lat.append((datetime.now(UTC) - sent).total_seconds())


async def influx_count(tenant: str, start: datetime) -> int:
    from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync
    # count of millions of points on a loaded host: the client's default 10 s timeout is not enough
    async with InfluxDBClientAsync(url=INFLUX["url"], token=INFLUX["token"], org=INFLUX["org"],
                                   timeout=int(os.getenv("INFLUX_COUNT_TIMEOUT_MS", "600000"))) as c:
        q = f'''from(bucket: "{INFLUX["bucket"]}") |> range(start: {iso(start - timedelta(seconds=2))})
          |> filter(fn: (r) => r["_measurement"] == "sensors" and r["tenant_id"] == "{tenant}")
          |> filter(fn: (r) => r["_field"] == "value") |> group() |> count()'''
        n = 0
        for table in await c.query_api().query(q):
            for rec in table.records:
                n += int(rec.get_value())
        return n


def login(t: dict) -> str:
    from seed import PASSWORD
    r = httpx.post(f"{API}/api/auth/login", json={"email": t["email"], "password": PASSWORD}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


async def main() -> int:
    report: dict = {"config": dict(tenants=TENANTS, lines=LINES, machines=MACHINES, history_days=HISTORY_DAYS,
                                   duration_s=DURATION_S, speed=SPEED, publishers=PUBLISHERS, api_conc=API_CONC,
                                   ingest=INGEST, shards=SHARDS, broker_conf=str(BROKER_CONF.name),
                                   machines_total=TENANTS * LINES * MACHINES, direct_db=bool(_PG_IP),
                                   api_cache_ttl_s=float(os.getenv("API_CACHE_TTL_S", "10")), reports=REPORTS,
                                   cpus=os.cpu_count(), loadavg_start=os.getloadavg()[0],
                                   readings_per_s=TENANTS * LINES * MACHINES * 13 * SPEED)}
    print("config", report["config"], flush=True)
    start_broker()
    recreate_bucket()
    await recreate_db()
    env = server_env()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=SERVER, env=env, check=True,
                   capture_output=True)
    now = datetime.now(UTC)
    t0 = time.time()
    data = await seed_data(now)
    report["seed"] = {"seconds": round(time.time() - t0, 1), **data["counts"]}
    print("seeded", report["seed"], flush=True)
    proc = start_server()
    try:
        tokens = []
        for t in data["tenants"]:
            tokens.append({**t, "token": login(t)})
        # warm-up (imports, caches) + baseline latencies without load
        base: dict = {}
        stop = asyncio.Event()
        task = asyncio.create_task(api_probe(tokens, stop, base, 1))
        await asyncio.sleep(20)
        stop.set()
        await task
        report["api_idle"] = {k: {"n": len(v), "p50_ms": round(pct(v, 50) * 1000), "p95_ms": round(pct(v, 95) * 1000)}
                              for k, v in base.items() if not k.endswith("_errors")}
        print("api idle", report["api_idle"], flush=True)

        machines = []
        for t in data["tenants"]:
            for m in t["machines"]:
                machines.append({**m, "tenant_id": t["id"],
                                 "analog_names": [n for n in m["tags"] if n not in ("run", "fault")]})
        counts = mp.Array("l", PUBLISHERS)
        wall_t0 = time.time() + 2
        plant_t0 = wall_t0 - DURATION_S * (1 - 1 / SPEED) if SPEED > 1 else wall_t0
        load_start = datetime.fromtimestamp(plant_t0, UTC)
        m0 = metrics()
        procs = [mp.Process(target=publisher, args=(k, machines[k::PUBLISHERS], plant_t0, wall_t0, DURATION_S, SPEED,
                                                    counts), daemon=True) for k in range(PUBLISHERS)]
        for p in procs:
            p.start()
        lat: dict = {}
        ws_lat: list = []
        stop = asyncio.Event()
        await asyncio.sleep(max(0.0, wall_t0 - time.time()) + 15)       # API probes once the stream is flowing
        probes = [asyncio.create_task(ws_probe(tokens[0], stop, ws_lat))]
        if API_CONC > 0:
            probes.append(asyncio.create_task(api_probe(tokens, stop, lat, API_CONC)))
        if REPORTS:
            probes.append(asyncio.create_task(report_probe(tokens, stop, lat)))
        samples = []
        while any(p.is_alive() for p in procs):
            await asyncio.sleep(10)
            try:
                mm = metrics()
                samples.append({"t": round(time.time() - wall_t0), "published": int(sum(counts[:])),
                                "inflight": mm.get("fm_mqtt_inflight", mm.get("fm_ingest_queue_depth")),
                                "processed_ok": mm.get('fm_mqtt_messages_total{result="ok"}'),
                                "detector_lag_max_s": mm.get('fm_detector_lag_max_seconds{detector="downtime"}')})
                print("  ", samples[-1], flush=True)
            except httpx.HTTPError as e:
                print("   metrics error", e, flush=True)
        stop.set()
        await asyncio.gather(*probes, return_exceptions=True)
        published = int(sum(counts[:]))
        wall_s = time.time() - wall_t0
        # drain: everything published must be processed
        t_drain = time.time()
        while time.time() - t_drain < float(os.getenv("DRAIN_S", "600")):
            mm = metrics()
            if mm.get('fm_mqtt_messages_total{result="ok"}', 0) - m0.get('fm_mqtt_messages_total{result="ok"}', 0) \
                    >= published:
                break
            await asyncio.sleep(2)
        drain_s = time.time() - t_drain
        await asyncio.sleep(40)                            # detectors: a couple of ticks after the end
        m1 = metrics()
        ok = m1.get('fm_mqtt_messages_total{result="ok"}', 0) - m0.get('fm_mqtt_messages_total{result="ok"}', 0)
        try:
            stored = sum([await influx_count(t["id"], load_start) for t in data["tenants"]])
        except Exception as e:                          # keep every other measurement (OUT is written below)
            print("   influx count failed:", repr(e), flush=True)
            stored = None
        ok_at_end = samples[-1]["processed_ok"] if samples else None
        report["load"] = {"published": published, "wall_s": round(wall_s, 1), "rate_published": round(published / wall_s),
                          "rate_processed_during_load": round((ok_at_end or 0) / wall_s),
                          "processed_ok": int(ok), "influx_points": stored, "drain_after_end_s": round(drain_s, 1),
                          "dropped": m1.get("fm_ingest_dropped_total", 0) - m0.get("fm_ingest_dropped_total", 0),
                          "dead_letters": sum(v for k, v in m1.items() if k.startswith("fm_ingest_dead_letters_total"))
                          - sum(v for k, v in m0.items() if k.startswith("fm_ingest_dead_letters_total")),
                          "samples": samples}
        report["ingestion_lag"] = {
            "ws_n": len(ws_lat), "ws_p50_s": pct(ws_lat, 50), "ws_p95_s": pct(ws_lat, 95), "ws_p99_s": pct(ws_lat, 99),
            "ws_max_s": max(ws_lat) if ws_lat else None,
            "server_p95_s_bucket": hist_quantile(m0, m1, "fm_mqtt_processing_lag_seconds", 0.95),
            "server_p50_s_bucket": hist_quantile(m0, m1, "fm_mqtt_processing_lag_seconds", 0.50),
            "ingest_latency_p95_s_bucket": hist_quantile(m0, m1, "fm_ingest_latency_seconds", 0.95)}
        report["api_load"] = {}
        for k, v in sorted(lat.items()):
            if k.endswith("_errors"):
                report["api_load"][k] = v[:5] + [f"… {len(v)} total"]
                continue
            report["api_load"][k] = {"n": len(v), "p50_ms": round(pct(v, 50) * 1000), "p95_ms": round(pct(v, 95) * 1000),
                                     "max_ms": round(max(v) * 1000), "mean_ms": round(statistics.mean(v) * 1000)}
        report["detectors"] = {"downtime_lag_max_s": m1.get('fm_detector_lag_max_seconds{detector="downtime"}'),
                               "downtime_lagging": m1.get('fm_detector_lagging{detector="downtime"}')}
        targets = {"ingestion_lag_p95_lt_2s": (report["ingestion_lag"]["ws_p95_s"] or 99) < 2.0,
                   "no_loss": report["load"]["dropped"] == 0 and stored is not None and stored >= published * 0.999}
        for k in ("overview", "oee_30d", "pareto_30d", "plans_status"):
            targets[f"{k}_p95_lt_500ms"] = (report["api_load"].get(k) or {}).get("p95_ms", 1e9) < 500
        targets["api_errors_lt_0_1pct"] = sum(len(v) - 1 if isinstance(v[-1], str) and v[-1].startswith("…") else len(v)
                                               for k, v in lat.items() if k.endswith("_errors")) \
            <= 0.001 * sum(len(v) for k, v in lat.items() if not k.endswith("_errors"))
        report["targets"] = targets
        report["config"]["loadavg_end"] = os.getloadavg()[0]
    finally:
        stop_server(proc)
        stop_broker()
        if not KEEP_DB:
            await drop_db()
            drop_buckets()
    OUT.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: v for k, v in report.items() if k != "load"}, indent=1, default=str))
    print("load", {k: v for k, v in report["load"].items() if k != "samples"})
    return 0 if all(report["targets"].values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
