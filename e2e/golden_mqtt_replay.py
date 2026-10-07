"""Golden scenarios A and B replayed through the REAL path: MQTT broker -> server subscriber -> usage
processor -> Postgres/Influx -> REST API (docs/scenarios/README.md, "Como o QA roda de ponta a ponta").

It registers a fresh tenant, builds the scenario assets through the REST API, publishes every reading of
docs/scenarios/scenario_{a,b}*.json in JSON order to factory/{tenant}/sensors/{tag_id} (timestamps shifted
back by SHIFT_DAYS whole days, see README), then checks the API against `expected`.

Then (seeded part) it adds the scenario `seed` block and the 14-day window usage straight into
usage_totals/usage_buckets of this throw-away tenant and checks life_consumed_pct through the API and
projected_end_date via component_usage_detail(now=evaluate_at - SHIFT_DAYS) — the API uses the real clock.

Prerequisites: a running server subscribed to the broker (default http://localhost:8000, localhost:1883),
plaintext MQTT accepted, Postgres reachable with the server's .env. Run from the repo root:

    set -a && . ./.env && set +a && cd server && ../.venv/bin/python ../e2e/golden_mqtt_replay.py

Env: GOLDEN_API (http://localhost:8000), GOLDEN_BROKER (localhost), GOLDEN_BROKER_PORT (1883),
GOLDEN_SHIFT_DAYS (2). Exit code 0 = every check passed.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parents[1]
API = os.getenv("GOLDEN_API", "http://localhost:8000")
BROKER = os.getenv("GOLDEN_BROKER", "localhost")
BROKER_PORT = int(os.getenv("GOLDEN_BROKER_PORT", "1883"))
SHIFT = timedelta(days=int(os.getenv("GOLDEN_SHIFT_DAYS", "2")))
TOL = 1e-6

A = json.loads((ROOT / "docs/scenarios/scenario_a_usage.json").read_text())
B = json.loads((ROOT / "docs/scenarios/scenario_b_production.json").read_text())

results: list[tuple[bool, str]] = []


def check(ok: bool, name: str, got=None, exp=None) -> None:
    results.append((bool(ok), name))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   expected={exp!r} got={got!r}"))


def eq(name, got, exp, tol=TOL):
    if isinstance(exp, (int, float)) and isinstance(got, (int, float)):
        check(math.isclose(got, exp, rel_tol=0, abs_tol=tol), name, got, exp)
    else:
        check(got == exp, name, got, exp)


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def shifted(s: str) -> str:
    return (ts(s) - SHIFT).isoformat().replace("+00:00", "Z")


def shifted_date(s: str) -> str:
    return (date.fromisoformat(s) - SHIFT).isoformat()


class Api:
    def __init__(self):
        self.c = httpx.Client(base_url=API, timeout=30)

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


def build(api: Api) -> dict:
    stamp = int(time.time())
    email = f"qa-golden-{stamp}@qa-factoryconnect.com.br"
    api.post("/api/auth/register", json={"company_name": f"QA Golden {stamp}", "admin_name": "QA",
                                         "admin_email": email, "admin_password": "QaOuro#Teste2026"})
    api.login(email, "QaOuro#Teste2026")
    me = api.get("/api/auth/me")
    ids = {"tenant": me["tenant_id"]}
    plant = api.post("/api/plants/", json={"name": A["plant"]["name"], "timezone": A["plant"]["timezone"]})
    line = api.post("/api/lines/", json={"name": "Linha de Montagem", "plant_id": plant["id"]})
    fix = api.post("/api/machines/", json={"line_id": line["id"], "name": "Estação de Fixação",
                                           "ideal_cycle_time_s": A["a1_cylinder"]["asset"]["machine_ideal_cycle_time_s"]})
    tra = api.post("/api/machines/", json={"line_id": line["id"], "name": "Transportador de Saída",
                                           "ideal_cycle_time_s": B["asset"]["ideal_cycle_time_s"]})
    api.patch(f"/api/lines/{line['id']}", json={"output_machine_id": tra["id"]})
    a1 = A["a1_cylinder"]["asset"]
    p_a1 = api.post("/api/positions/", json={"machine_id": fix["id"], "name": a1["position"],
                                             "component_type": a1["component_type"],
                                             "cycles_per_part": a1["cycles_per_part"]})
    c_a1 = api.post("/api/components/", json={"component_type": a1["component_type"], "serial_number": a1["component_serial"],
                                              "nominal_life": A["a1_cylinder"]["nominal_life"],
                                              "position_id": p_a1["id"], "installed_at": shifted(a1["installed_at"])})
    t_a1 = api.post("/api/tags/", json={"machine_id": fix["id"], "position_id": p_a1["id"], "name": "A1 ciclos",
                                        "protocol": "modbus_tcp", "address": "unit=2;hr=1:uint16",
                                        "semantic_role": "cycle_counter", "counter_max": 65535})
    api.patch(f"/api/positions/{p_a1['id']}", json={"counter_tag_id": t_a1["id"]})
    a2 = A["a2_motor"]["asset"]
    p_m = api.post("/api/positions/", json={"machine_id": tra["id"], "name": a2["position"],
                                            "component_type": a2["component_type"]})
    c_m = api.post("/api/components/", json={"component_type": a2["component_type"], "serial_number": a2["component_serial"],
                                             "nominal_life": A["a2_motor"]["nominal_life"],
                                             "position_id": p_m["id"], "installed_at": shifted(a2["installed_at"])})
    t_m = api.post("/api/tags/", json={"machine_id": tra["id"], "position_id": p_m["id"], "name": "Motor run",
                                       "protocol": "modbus_tcp", "address": "unit=3;di=0", "semantic_role": "run_state"})
    t_g = api.post("/api/tags/", json={"machine_id": tra["id"], "name": "Boas", "protocol": "modbus_tcp",
                                       "address": "unit=3;hr=2:uint32", "semantic_role": "part_counter_good",
                                       "counter_max": B["tags"]["good"]["counter_max"]})
    t_s = api.post("/api/tags/", json={"machine_id": tra["id"], "name": "Refugo", "protocol": "modbus_tcp",
                                       "address": "unit=3;hr=4:uint32", "semantic_role": "part_counter_scrap",
                                       "counter_max": B["tags"]["scrap"]["counter_max"]})
    ids.update(plant=plant["id"], line=line["id"], fix=fix["id"], tra=tra["id"], p_a1=p_a1["id"], c_a1=c_a1["id"],
               t_a1=t_a1["id"], p_m=p_m["id"], c_m=c_m["id"], t_m=t_m["id"], t_g=t_g["id"], t_s=t_s["id"])
    return ids


def publish(ids: dict) -> int:
    msgs = [(ids["t_a1"], r) for r in A["a1_cylinder"]["readings"]]
    msgs += [(ids["t_m"], r) for r in A["a2_motor"]["readings"]]
    b = [(ids["t_g"], r) for r in B["readings"]["good"]] + [(ids["t_s"], r) for r in B["readings"]["scrap"]]
    b.sort(key=lambda x: ts(x[1]["ts"]))      # stable: keeps JSON order (incl. the duplicate) per tag
    msgs += b
    cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"qa-golden-{os.getpid()}")
    cli.connect(BROKER, BROKER_PORT)
    cli.loop_start()
    for tag_id, r in msgs:
        info = cli.publish(f"factory/{ids['tenant']}/sensors/{tag_id}",
                           json.dumps({"v": 1, "value": r["value"], "ts": shifted(r["ts"])}), qos=1)
        info.wait_for_publish()
        time.sleep(0.01)
    cli.loop_stop()
    cli.disconnect()
    return len(msgs)


async def wait_drained(ids: dict, timeout: float) -> None:
    sys.path.insert(0, str(ROOT / "server"))
    from sqlalchemy import text
    from src.db import AsyncSessionLocal, engine
    last = {ids["t_a1"]: ts(A["a1_cylinder"]["expected"]["last_accepted"]["ts"]) - SHIFT,
            ids["t_m"]: ts(A["a2_motor"]["readings"][-1]["ts"]) - SHIFT,
            ids["t_g"]: max(ts(r["ts"]) for r in B["readings"]["good"]) - SHIFT,
            ids["t_s"]: max(ts(r["ts"]) for r in B["readings"]["scrap"]) - SHIFT}
    t0 = time.time()
    while True:
        async with AsyncSessionLocal() as db:
            rows = dict((await db.execute(text("SELECT tag_id::text, last_ts FROM tag_states WHERE tag_id::text = ANY(:ids)"),
                                          {"ids": list(last)})).all())
        if all(rows.get(k) is not None and rows[k] >= v for k, v in last.items()):
            print(f"processor drained after {time.time() - t0:.1f} s")
            await asyncio.sleep(2)     # bucket/total writes of the last reading are in the same commit
            await engine.dispose()     # the pool is bound to this event loop
            return
        if time.time() - t0 > timeout:
            print(f"WARNING: processor not drained after {timeout:.0f} s: {rows}")
            await engine.dispose()
            return
        await asyncio.sleep(1)


def hist(api, **params):
    return {p["t"]: p["value"] for p in api.get("/api/usage/history", params=params)["points"]}


def norm(t: str) -> str:
    return ts(t).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def verify_unseeded(api: Api, ids: dict) -> None:
    ea, em = A["a1_cylinder"]["expected"], A["a2_motor"]["expected"]
    print("\n[A1] Cilindro A1 — 16-bit counter")
    pos = api.get(f"/api/positions/{ids['p_a1']}")
    eq("A1 position cycles_total == cycles_delta (197)", pos["usage"]["cycles_total"], ea["cycles_delta"])
    eq("A1 component usage since install cycles", pos["component_usage"]["cycles"], ea["cycles_delta"])
    eq("A1 life_consumed_pct without seed", pos["component_usage"]["life_consumed_pct"], ea["without_seed"]["life_consumed_pct"], 1e-12)
    eq("A1 life_measure", pos["component_usage"]["life_measure"], "cycles")
    start, stop = shifted("2026-10-05T09:00:00Z"), shifted("2026-10-05T14:00:00Z")
    h = {norm(k): v for k, v in hist(api, position_id=ids["p_a1"], measure="cycles", bucket="1h", start=start, stop=stop).items()}
    eq("A1 hourly buckets (UTC)", h, {shifted(k): v for k, v in ea["cycles_by_hour_utc"].items()})
    hc = {norm(k): v for k, v in hist(api, component_id=ids["c_a1"], measure="cycles", bucket="1h", start=start, stop=stop).items()}
    eq("A1 component hourly buckets", hc, {shifted(k): v for k, v in ea["cycles_by_hour_utc"].items()})

    print("\n[A2] Motor do transportador — run_state")
    pos = api.get(f"/api/positions/{ids['p_m']}")
    eq("A2 hours_total == 1.515889 h", pos["usage"]["hours_total"], em["hours_delta"])
    eq("A2 starts_total == 3", pos["usage"]["starts_total"], em["starts_delta"])
    # the motor's machine (Transportador) also carries scenario B's part counters: a position without its own
    # cycle tag gets estimated cycles = machine parts x cycles_per_part (C14), so 627 estimated here, not 0
    eq("A2 cycles_estimated flag (shares machine with B)", pos["usage"]["cycles_estimated"], True)
    eq("A2 cycles_total == B parts x 1 (estimated)", pos["usage"]["cycles_total"], B["expected"]["shift_totals"]["total"])
    eq("A2 component hours", pos["component_usage"]["hours"], em["hours_delta"])
    eq("A2 component starts", pos["component_usage"]["starts"], em["starts_delta"])
    hh = {norm(k): v for k, v in hist(api, position_id=ids["p_m"], measure="hours", bucket="1h", start=start, stop=stop).items()}
    eq("A2 hourly hours buckets", hh, {shifted(k): v / 3600.0 for k, v in em["operating_seconds_by_hour_utc"].items()})
    hs = {norm(k): v for k, v in hist(api, position_id=ids["p_m"], measure="starts", bucket="1h", start=start, stop=stop).items()}
    eq("A2 hourly starts buckets", hs, {shifted(k): v for k, v in em["starts_by_hour_utc"].items()})

    print("\n[B] Produção — turno que cruza a meia-noite")
    eb = B["expected"]
    s0, s1 = shifted(B["shift"]["start_utc"]), shifted(B["shift"]["end_utc"])
    prod = api.get(f"/api/lines/{ids['line']}/production", params={"start": s0, "stop": s1, "bucket": "1h"})
    eq("B line good", prod["good"], eb["shift_totals"]["good"])
    eq("B line scrap", prod["scrap"], eb["shift_totals"]["scrap"])
    eq("B line total (good+scrap, no total tag)", prod["total"], eb["shift_totals"]["total"])
    eq("B output_machine_id", prod["output_machine_id"], ids["tra"])
    pts = {norm(p["t"]): (p["good"], p["scrap"]) for p in prod["points"] if p["good"] or p["scrap"]}
    eq("B hourly good/scrap points (UTC)", pts,
       {shifted(k): (v["good"], v["scrap"]) for k, v in eb["by_hour_utc"].items()})
    eq("B cadence_per_hour = total / window hours (8 h window)", prod["cadence_per_hour"], eb["shift_totals"]["total"] / 8.0)
    day0 = shifted("2026-10-05T04:00:00Z")      # local midnight 2026-10-05 Manaus
    day2 = shifted("2026-10-07T04:00:00Z")
    for measure in ("good", "scrap", "total"):
        d = hist(api, machine_id=ids["tra"], measure=measure, bucket="1d", start=day0, stop=day2)
        got = {norm(k): v for k, v in d.items()}
        exp = {shifted(f"{k}T04:00:00Z"): v[measure] for k, v in eb["by_local_calendar_day"].items()}
        eq(f"B 1d buckets by LOCAL calendar day ({measure})", got, exp)
        wrong = {shifted(f"{k}T04:00:00Z"): v[measure] for k, v in eb["wrong_answers"]["by_utc_day"].items() if k != "why"}
        check(got != wrong, f"B 1d {measure} is not the UTC-day wrong answer")


async def db_checks_and_seed(ids: dict) -> None:
    sys.path.insert(0, str(ROOT / "server"))
    from sqlalchemy import select, text
    from src.db import AsyncSessionLocal
    from src.models import Component
    from src.services.usage.queries import component_usage_detail, tzinfo

    ea, em = A["a1_cylinder"]["expected"], A["a2_motor"]["expected"]
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text("SELECT event, ts, delta FROM usage_events WHERE tag_id = :t ORDER BY ts"),
                                 {"t": ids["t_a1"]})).all()
        got = [(e, t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), d) for e, t, d in rows]
        exp = [(e["event"], shifted(e["ts"]), e["delta"]) for e in ea["events"]]
        eq("A1 usage_events (rollover, glitch, reset) at the right ts", got, exp)
        st = (await db.execute(text("SELECT last_value, last_ts FROM tag_states WHERE tag_id = :t"), {"t": ids["t_a1"]})).one()
        eq("A1 last accepted (value, ts)", (st[0], st[1].astimezone(timezone.utc).isoformat().replace("+00:00", "Z")),
           (ea["last_accepted"]["value"], shifted(ea["last_accepted"]["ts"])))

        # ── seed: scenario `seed` blocks + 14-day window usage ──────────────────
        def add_total(scope, eid, measure, v):
            return db.execute(text("""INSERT INTO usage_totals (scope, entity_id, measure, tenant_id, value, updated_at)
                VALUES (:s, :e, :m, :t, :v, now()) ON CONFLICT (scope, entity_id, measure)
                DO UPDATE SET value = usage_totals.value + EXCLUDED.value"""),
                {"s": scope, "e": eid, "m": measure, "t": ids["tenant"], "v": v})

        def add_bucket(scope, eid, measure, b, v):
            return db.execute(text("""INSERT INTO usage_buckets (scope, entity_id, measure, bucket_start, tenant_id, value)
                VALUES (:s, :e, :m, :b, :t, :v) ON CONFLICT (scope, entity_id, measure, bucket_start)
                DO UPDATE SET value = usage_buckets.value + EXCLUDED.value"""),
                {"s": scope, "e": eid, "m": measure, "b": b, "t": ids["tenant"], "v": v})

        sa, sm = A["a1_cylinder"]["seed"], A["a2_motor"]["seed"]
        await add_total("position", ids["p_a1"], "cycles", sa["position_usage_total"]["cycles"])
        await add_total("component", ids["c_a1"], "cycles", sa["component_usage_since_install"]["cycles"])
        await add_total("position", ids["p_m"], "operating_seconds", sm["position_usage_total"]["hours"] * 3600)
        await add_total("component", ids["c_m"], "operating_seconds", sm["component_usage_since_install"]["hours"] * 3600)
        await add_total("position", ids["p_m"], "starts", sm["position_usage_total"]["starts"])
        await add_total("component", ids["c_m"], "starts", sm["component_usage_since_install"]["starts"])
        # the scenario readings are inside the 14-day window: top up to window_14d_usage
        wb = ts(A["evaluate_at"]) - SHIFT - timedelta(days=7)
        wb = wb.replace(minute=0, second=0, microsecond=0)
        await add_bucket("component", ids["c_a1"], "cycles", wb, A["a1_cylinder"]["window_14d_usage"]["cycles"] - ea["cycles_delta"])
        await add_bucket("component", ids["c_m"], "operating_seconds", wb,
                         A["a2_motor"]["window_14d_usage"]["hours"] * 3600 - em["operating_seconds"])
        await add_bucket("component", ids["c_m"], "starts", wb, A["a2_motor"]["window_14d_usage"]["starts"] - em["starts_delta"])
        await db.commit()

        now = ts(A["evaluate_at"]) - SHIFT
        tz = tzinfo(A["plant"]["timezone"])
        for key, cid, exp in (("A1", ids["c_a1"], ea["component"]), ("A2", ids["c_m"], em["component"])):
            comp = (await db.execute(select(Component).where(Component.id == cid))).scalar_one()
            d = await component_usage_detail(db, ids["tenant"], comp, tz, now=now)
            eq(f"{key} service life_consumed_pct", d["life_consumed_pct"], exp["life_consumed_pct"])
            eq(f"{key} service life_measure", d["life_measure"], exp["life_measure"])
            eq(f"{key} service rul_days", d["rul_days"], exp["rul_days"])
            eq(f"{key} service rul_measure", d["rul_measure"], exp["rul_measure"])
            pdate = d["projected_end_date"].isoformat() if hasattr(d["projected_end_date"], "isoformat") else d["projected_end_date"]
            eq(f"{key} service projected_end_date (shifted)", pdate, shifted_date(exp["projected_end_date"]))
            eq(f"{key} service low_confidence/overdue", (d["low_confidence"], d["overdue"]), (exp["low_confidence"], exp["overdue"]))


def verify_seeded(api: Api, ids: dict) -> None:
    ea, em = A["a1_cylinder"]["expected"], A["a2_motor"]["expected"]
    print("\n[A seeded] through the API")
    pos = api.get(f"/api/positions/{ids['p_a1']}")
    eq("A1 cycles_total 7,200,000", pos["usage"]["cycles_total"], ea["position"]["cycles_total"])
    eq("A1 component cycles 3,600,000", pos["component_usage"]["cycles"], ea["component"]["usage_since_install"]["cycles"])
    eq("A1 life_consumed_pct 0.72", pos["component_usage"]["life_consumed_pct"], ea["component"]["life_consumed_pct"])
    comp = api.get(f"/api/components/{ids['c_a1']}")
    eq("A1 GET /components life_consumed_pct 0.72", comp["life_consumed_pct"], ea["component"]["life_consumed_pct"])
    pos = api.get(f"/api/positions/{ids['p_m']}")
    eq("A2 hours_total 12000", pos["usage"]["hours_total"], em["position"]["hours_total"], 1e-6)
    eq("A2 starts_total 130000", pos["usage"]["starts_total"], em["position"]["starts_total"])
    eq("A2 life_consumed_pct 0.65", pos["component_usage"]["life_consumed_pct"], em["component"]["life_consumed_pct"])
    eq("A2 life_measure starts", pos["component_usage"]["life_measure"], em["component"]["life_measure"])
    # A3: same component without nominal life
    api.patch(f"/api/components/{ids['c_m']}", json={"nominal_life": {}})
    pos = api.get(f"/api/positions/{ids['p_m']}")
    e3 = A["a3_no_nominal_life"]["expected"]
    eq("A3 life_consumed_pct null", pos["component_usage"]["life_consumed_pct"], e3["life_consumed_pct"])
    eq("A3 life_reason", pos["component_usage"]["life_reason"], e3["life_reason"])
    eq("A3 projected_end_date null", pos["component_usage"]["projected_end_date"], e3["projected_end_date"])
    eq("A3 rul_reason", pos["component_usage"]["rul_reason"], e3["rul_reason"])


def main() -> int:
    api = Api()
    ids = build(api)
    print(f"tenant {ids['tenant']}  (shift {SHIFT.days} days)")
    n = publish(ids)
    print(f"published {n} readings")
    # wait until the processor has drained: every tag's last accepted ts == its last reading ts
    asyncio.run(wait_drained(ids, timeout=float(os.getenv("GOLDEN_DRAIN_TIMEOUT_S", "600"))))
    verify_unseeded(api, ids)
    print("\n[recompute] POST /api/usage/recompute?apply=false")
    rc = api.post("/api/usage/recompute", params={"apply": "false"})
    eq("recompute == live (0 differences)", rc["differences"], [])
    print("\n[events / seed] DB + service level")
    asyncio.run(db_checks_and_seed(ids))
    verify_seeded(api, ids)
    failed = [n for ok, n in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
