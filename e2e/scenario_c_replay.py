"""Golden scenario C (docs/scenarios/scenario_c_oee_shift.json) checked against a RUNNING server.

Two ways to feed it:

* ``mqtt``  — registers a throw-away tenant, builds plant/line/Torno CNC/tags/Turno A/product/run through the
  REST API and publishes the scenario readings (run/fault transitions + a heartbeat every 30 s + part counters,
  same series as server/tests/oee_helpers.scenario_c_readings) to the REAL broker
  (factory/{tenant}/sensors/{tag}), shifted back SHIFT_DAYS whole days. Then it verifies.
* ``verify`` — nothing is published (the real path: virtual plant --scenario → gateway → MQTT already did it);
  logs in as EMAIL/PASSWORD, finds the machine named MACHINE (default "Torno CNC") and verifies the window
  of the scenario shifted SHIFT_DAYS days back.

Verification: waits for the 6 downtime events of `expected` (detector runs every 10 s), justifies the operator
reasons through PATCH /api/downtime/{id} (MAT-01, SET-01, confirms the EQP faults; 11:50 stays UNK-01 by the
shift-end rule) and compares GET /api/oee, /api/losses/pareto (code and category), /api/reliability and
/api/reports/shift with `expected`. Tolerance: 1e-6 relative (numbers in `expected` are exact fractions; the
Pareto pct are given rounded to 6 decimals → 5e-7).

Run from the repo root (server venv has httpx + paho):
    .venv/bin/python e2e/scenario_c_replay.py mqtt   [env: API, BROKER, BROKER_PORT, SHIFT_DAYS=3]
    .venv/bin/python e2e/scenario_c_replay.py verify [env: API, EMAIL, PASSWORD, MACHINE, SHIFT_DAYS=2]
Exit code 0 = every check passed.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
C = json.loads((ROOT / "docs/scenarios/scenario_c_oee_shift.json").read_text(encoding="utf-8"))
X = C["expected"]
API = os.getenv("API", "http://localhost:8000")
BROKER = os.getenv("BROKER", "localhost")
BROKER_PORT = int(os.getenv("BROKER_PORT", "1883"))
WAIT_S = float(os.getenv("WAIT_S", "240"))
TZ = ZoneInfo(C["plant"]["timezone"])
HEARTBEAT_S = 30

results: list[tuple[bool, str]] = []


def check(ok, name, got=None, exp=None):
    results.append((bool(ok), name))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   expected={exp!r} got={got!r}"))


TOL = float(os.getenv("TOL", "1e-6"))  # exact `expected` values; TOL=1e-9 for a strict run


def close(a, b, tol=None):
    tol = TOL if tol is None else tol
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=0, abs_tol=tol * max(1.0, abs(b)))


def eq(name, got, exp, tol=None):
    if isinstance(exp, (int, float)) and not isinstance(exp, bool):
        check(isinstance(got, (int, float)) and close(got, exp, tol), name, got, exp)
    else:
        check(got == exp, name, got, exp)


class Api:
    def __init__(self):
        self.c = httpx.Client(base_url=API, timeout=60)

    def login(self, email, pw):
        r = self.c.post("/api/auth/login", json={"email": email, "password": pw})
        r.raise_for_status()
        self.c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    def __getattr__(self, verb):
        def call(path, expect=(200, 201), **kw):
            r = getattr(self.c, verb)(path, **kw)
            if r.status_code not in expect:
                raise RuntimeError(f"{verb.upper()} {path} -> {r.status_code} {r.text[:300]}")
            return r.json() if r.content else None
        return call


def shift_fn(days: int):
    d = timedelta(days=days)
    return lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")) - d


# ── mode mqtt: build + publish ──────────────────────────────────────────────

def build(api: Api, ts) -> dict:
    stamp = int(time.time())
    email = f"qa-scc-{stamp}@qa-factoryconnect.com.br"
    api.post("/api/auth/register", json={"company_name": f"QA Cenario C {stamp}", "admin_name": "QA",
                                         "admin_email": email, "admin_password": "QaGolden#2026"})
    api.login(email, "QaGolden#2026")
    tenant = api.get("/api/auth/me")["tenant_id"]
    m = C["machine"]
    plant = api.post("/api/plants/", json={"name": C["plant"]["name"], "timezone": C["plant"]["timezone"]})
    line = api.post("/api/lines/", json={"name": "Linha C", "plant_id": plant["id"]})
    mach = api.post("/api/machines/", json={"line_id": line["id"], "name": m["name"]})
    api.patch(f"/api/machines/{mach['id']}", json={"micro_stop_threshold_s": m["micro_stop_threshold_s"],
                                                   "fault_code_map": m["fault_code_map"]})
    api.patch(f"/api/lines/{line['id']}", json={"output_machine_id": mach["id"]})
    tags = {}
    for name, role, dt in (("run", "run_state", "bool"), ("fault", "fault_state", "int"),
                           ("good", "part_counter_good", "int"), ("scrap", "part_counter_scrap", "int")):
        tags[name] = api.post("/api/tags/", json={"machine_id": mach["id"], "name": name, "protocol": "opc_ua",
                                                  "address": f"ns=2;s=QA.C.{name}", "data_type": dt,
                                                  "semantic_role": role})["id"]
    breaks = [{"start_local": e["from_local"][:5], "end_local": e["to_local"][:5], "reason_code": e["reason_code"]}
              for e in C["events"] if e["kind"] == "planned_break"]
    shift = api.post("/api/shifts/", json={"plant_id": plant["id"], "name": C["shift"]["name"],
                                           "start_local": C["shift"]["start_local"], "end_local": C["shift"]["end_local"],
                                           "weekdays": [0, 1, 2, 3, 4, 5, 6], "breaks": breaks})
    prod = api.post("/api/products/", json={"code": m["product"], "name": "Eixo 20 mm"})
    api.post(f"/api/machines/{mach['id']}/products", json={"product_id": prod["id"],
                                                           "ideal_cycle_time_s": m["ideal_cycle_time_s"], "is_default": True})
    api.post("/api/production-runs/", json={"machine_id": mach["id"], "product_id": prod["id"],
                                            "started_at": ts(C["shift"]["start_utc"]).isoformat()})
    print(f"tenant {tenant}  login {email} / QaGolden#2026  machine {mach['id']}")
    return {"tenant": tenant, "machine": mach["id"], "line": line["id"], "plant": plant["id"],
            "shift": shift["id"], "tags": tags, "email": email}


def readings(tags: dict, ts) -> list[tuple[str, float, datetime]]:
    sig = C["signals"]
    out = []
    for i, s in enumerate(sig):
        t0 = ts(s["ts"])
        out += [(tags["run"], s["run_state"], t0), (tags["fault"], s["fault_state"], t0)]
        if i + 1 < len(sig):
            nxt, hb = ts(sig[i + 1]["ts"]), t0 + timedelta(seconds=HEARTBEAT_S)
            while hb < nxt:
                out += [(tags["run"], s["run_state"], hb), (tags["fault"], s["fault_state"], hb)]
                hb += timedelta(seconds=HEARTBEAT_S)
    start = ts(C["shift"]["start_utc"])
    g, sc, n = C["production"]["good"], C["production"]["scrap"], 48
    for k in range(n):
        tk = start + timedelta(seconds=600 * k + 1)
        out += [(tags["good"], round(g * k / (n - 1)), tk), (tags["scrap"], round(sc * k / (n - 1)), tk)]
    out.sort(key=lambda r: r[2])
    return out


def publish(ids: dict, ts) -> int:
    import paho.mqtt.client as mqtt
    msgs = readings(ids["tags"], ts)
    cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"qa-scc-{os.getpid()}")
    cli.connect(BROKER, BROKER_PORT)
    cli.loop_start()
    for tag, v, at in msgs:
        cli.publish(f"factory/{ids['tenant']}/sensors/{tag}",
                    json.dumps({"v": 1, "value": v, "ts": at.isoformat().replace("+00:00", "Z")}), qos=1).wait_for_publish()
    cli.loop_stop()
    cli.disconnect()
    return len(msgs)


# ── verification ────────────────────────────────────────────────────────────

def local(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(TZ).strftime("%H:%M:%S")


def wait_events(api: Api, mid: str, params: dict) -> list[dict]:
    want = [d["from_local"] for d in X["downtime_events"]]
    t0, evs = time.time(), []
    while time.time() - t0 < WAIT_S:
        evs = sorted(api.get("/api/downtime/", params={"machine_id": mid, **params}), key=lambda e: e["start"])
        closed = [e for e in evs if e["end"] is not None]
        unk = any(e["reason_code"] == "UNK-01" for e in evs)
        if [local(e["start"]) for e in closed] == want and len(evs) == len(want) and unk:
            print(f"  6 events closed (+UNK-01 at shift end) after {time.time() - t0:.0f} s")
            return evs
        time.sleep(5)
    print(f"  timeout after {WAIT_S:.0f} s waiting for the events")
    return evs


def wait_ingested(api: Api, tags: dict, ts) -> None:
    """Barrier (mqtt mode): every published run/fault reading is in InfluxDB. Under a shared-broker backlog the
    downtime events can look complete (UNK-01 is set by the shift-end rule on wall time) while the tail of the
    shift (e.g. the 13:50 micro-stop) is still queued in the MQTT handler; verifying then is a harness race."""
    want: dict[str, set] = {}
    for tag, _v, at in readings(tags, ts):
        if tag in (tags["run"], tags["fault"]):
            want.setdefault(tag, set()).add(at)
    t0 = time.time()
    while True:
        missing = 0
        for tag, tss in want.items():
            lo, hi = min(tss), max(tss)
            pts = api.get("/api/sensors/history", params={"tag_id": tag, "window": "1s", "start": lo.isoformat(),
                                                          "stop": (hi + timedelta(seconds=1)).isoformat()})["points"]
            missing += len(tss) - len(pts)
        if missing <= 0:
            break
        if time.time() - t0 > WAIT_S:
            print(f"  WARNING: {missing} readings still not in InfluxDB after {WAIT_S:.0f} s")
            return
        time.sleep(5)
    print(f"  all readings ingested after {time.time() - t0:.0f} s")


def wait_settled(api: Api, mid: str, params: dict) -> None:
    """Barrier (both modes): the detector re-derives provisional micro-stops and late data on every tick; wait
    until the machine's OEE loss split is identical on two reads > settle + 1 tick apart."""
    keys = ("micro_stop_count", "micro_stop_loss_s", "speed_loss_s", "adot_s", "data_coverage", "good", "scrap")
    gap = float(os.getenv("DETECTOR_SETTLE_WAIT_S", "45"))   # downtime_settle_s 30 + tick 10 + margin
    t0, prev = time.time(), None
    while time.time() - t0 < WAIT_S:
        o = api.get("/api/oee", params={"scope": "machine", "id": mid, **params})
        cur = tuple(o.get(k) for k in keys)
        if cur == prev:
            print(f"  detector settled after {time.time() - t0:.0f} s")
            return
        prev = cur
        time.sleep(gap)
    print(f"  WARNING: OEE still changing after {WAIT_S:.0f} s")


def verify(api: Api, mid: str, line_id: str, ts, tags: dict | None = None) -> None:
    start, end = ts(C["shift"]["start_utc"]), ts(C["shift"]["end_utc"])
    params = {"start": start.isoformat(), "stop": end.isoformat()}
    if tags:
        print("\n[ingest] waiting for the MQTT backlog to drain")
        wait_ingested(api, tags, ts)
    print(f"\n[events] window {start.isoformat()} → {end.isoformat()}")
    evs = wait_events(api, mid, params)
    wait_settled(api, mid, params)
    eq("downtime event starts (local)", [local(e["start"]) for e in evs], [d["from_local"] for d in X["downtime_events"]])
    eq("downtime event durations", [e["duration_s"] for e in evs], [d["duration_s"] for d in X["downtime_events"]])
    by = {local(e["start"]): e for e in evs}
    for lt, code in (("07:12:00", "EQP-01"), ("09:45:00", "EQP-02"), ("12:20:00", "EQP-01")):
        eq(f"{lt} suggested reason (fault_code_map)", by.get(lt, {}).get("suggested_reason_code"), code)
    eq("11:50 → UNK-01 at shift end", by.get("11:50:00", {}).get("reason_code"), "UNK-01")
    eq("events carry the shift", {e["shift_id"] for e in evs} != {None}, True)

    print("\n[justify] PATCH /api/downtime/{id}")
    for lt, code in (("08:30:00", "MAT-01"), ("13:00:00", "SET-01"), ("07:12:00", "EQP-01"),
                     ("09:45:00", "EQP-02"), ("12:20:00", "EQP-01")):
        if lt in by:
            r = api.patch(f"/api/downtime/{by[lt]['id']}", json={"reason_code": code})
            eq(f"justify {lt} = {code}", (r["reason_code"], r["unjustified"]), (code, False))
    evs = api.get("/api/downtime/", params={"machine_id": mid, **params})
    eq("effective reasons", sorted((local(e["start"]), e["effective_reason_code"], e["duration_s"]) for e in evs),
       [(d["from_local"], d["reason_code"], d["duration_s"]) for d in X["downtime_events"]])

    print("\n[oee] GET /api/oee scope=machine")
    o = api.get("/api/oee", params={"scope": "machine", "id": mid, **params})
    for k, v in (("pot_s", X["pot_min"] * 60), ("pdot_s", X["pdot_min"] * 60), ("pbt_s", X["pbt_min"] * 60),
                 ("adot_s", X["adot_min"] * 60), ("apt_s", X["apt_min"] * 60), ("data_coverage", X["data_coverage"]),
                 ("micro_stop_count", X["micro_stop_count"]), ("micro_stop_loss_s", X["micro_stop_s"]),
                 ("micro_stops_per_apt_hour", X["micro_stops_per_apt_hour"]),
                 ("ideal_produced_s", X["ideal_time_produced_min"] * 60), ("net_productive_s", X["net_productive_min"] * 60),
                 ("availability", X["availability"]), ("performance", X["performance"]), ("quality", X["quality"]),
                 ("oee", X["oee"]), ("cadence_planned_per_hour", X["cadence"]["parts_per_hour_planned"]),
                 ("cadence_running_per_hour", X["cadence"]["parts_per_hour_running"]),
                 ("speed_loss_s", X["loss_pareto"]["speed_loss_min"] * 60),
                 ("availability_operational", X["availability_operational"]["ao"]),
                 ("good", C["production"]["good"]), ("scrap", C["production"]["scrap"]),
                 ("produced", C["production"]["produced"])):
        eq(f"oee.{k}", o.get(k), v)
    print(f"  (info) product_unknown={o.get('product_unknown')} calendar_default={o.get('calendar_default')}")
    is_output = api.get(f"/api/lines/{line_id}").get("output_machine_id") == mid
    if is_output:
        lo = api.get("/api/oee", params={"scope": "line", "id": line_id, **params})
        eq("line scope oee (machine is the line output)", lo.get("oee"), X["oee"])
    else:
        print("  (info) machine is not its line's output machine: line-scope OEE and shift report not compared")
    g = api.get("/api/oee", params={"scope": "machine", "id": mid, "group_by": "hour", **params})
    eq("group_by=hour: 8 points", len(g["points"]), 8)
    eq("group_by=hour: Σ good", sum(p["good"] for p in g["points"]), C["production"]["good"])

    print("\n[pareto] GET /api/losses/pareto")
    par = api.get("/api/losses/pareto", params={"scope": "machine", "id": mid, **params})
    exp = X["loss_pareto"]["items"]
    eq("pareto order", [r["reason_code"] for r in par], [i[0] for i in exp])
    for r, (code, minutes, pct, cum) in zip(par, exp):
        eq(f"pareto {code} min", r["duration_s"] / 60, minutes)
        eq(f"pareto {code} pct", r["pct"], pct, 5e-7)
        eq(f"pareto {code} cumulative", r["cumulative_pct"], cum, 5e-7)
    eq("pareto total min", sum(r["duration_s"] for r in par) / 60, X["loss_pareto"]["total_loss_min"])
    cat = api.get("/api/losses/pareto", params={"scope": "machine", "id": mid, "by": "category", **params})
    eq("pareto by category", [(r["category"], round(r["duration_s"] / 60, 6)) for r in cat],
       [(k, v) for k, v in X["loss_pareto_by_category"]])

    print("\n[reliability] GET /api/reliability")
    rel = api.get("/api/reliability", params={"scope": "machine", "id": mid, **params})
    eq("failures", rel["failures"], X["failures"]["n"])
    eq("incipient", rel["incipient"], X["failures"]["incipient"])
    eq("short faults (not failures)", rel["short_faults"], X["failures"]["short_faults_not_failures"])
    eq("operating min (run_state)", rel["operating_h"] * 60, X["operating_min_run_state"])
    eq("MTBF h", rel["mtbf_h"], X["mtbf_h"])
    eq("MTTR s", rel["mttr_s"], X["mttr_s"])
    eq("MTTR min", rel["mttr_h"] * 60, X["mttr_min"])

    if not is_output:
        return
    print("\n[report] GET /api/reports/shift")
    rep = api.get("/api/reports/shift", params={"line_id": line_id,
                                                "productive_day": start.astimezone(TZ).date().isoformat()})
    eq("shift report oee", (rep.get("oee") or {}).get("oee"), X["oee"])
    eq("shift report downtime count", len(rep.get("downtime") or []), 6)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "verify"
    api = Api()
    if mode == "mqtt":
        ts = shift_fn(int(os.getenv("SHIFT_DAYS", "3")))
        ids = build(api, ts)
        if os.getenv("BUILD_ONLY") != "1":
            n = publish(ids, ts)
            print(f"published {n} readings to {BROKER}:{BROKER_PORT}")
        if os.getenv("PUBLISH_ONLY") == "1":
            json.dump(ids, sys.stdout)
            print()
            return 0
        mid, line_id, tags = ids["machine"], ids["line"], ids["tags"]
    else:
        tags = None
        ts = shift_fn(int(os.getenv("SHIFT_DAYS", "2")))
        if os.getenv("TENANT_EMAIL"):
            api.login(os.environ["TENANT_EMAIL"], os.getenv("TENANT_PASSWORD", "QaGolden#2026"))
        else:
            api.login(os.getenv("EMAIL", "admin@factory-monitor.com"), os.getenv("PASSWORD", "Admin@1234"))
        name = os.getenv("MACHINE", C["machine"]["name"])
        ms = [m for m in api.get("/api/machines/") if m["name"] == name]
        if os.getenv("MACHINE_ID"):
            ms = [m for m in api.get("/api/machines/") if m["id"] == os.environ["MACHINE_ID"]]
        if len(ms) != 1:
            print(f"machine {name!r}: {len(ms)} matches")
            return 2
        mid, line_id = ms[0]["id"], ms[0]["line_id"]
    verify(api, mid, line_id, ts, tags)
    bad = [n for ok, n in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
