"""Golden scenario D (docs/scenarios/scenario_d_line_multiday.json) replayed through MQTT against a RUNNING server.

Builds a throw-away tenant through the REST API (plant/line with output + bottleneck machine, products and ideal
cycle times, shifts A/B/C, the calendar exceptions, production runs), publishes the output machine's run/fault
transitions (+ a heartbeat every HEARTBEAT_S, none inside the data gap; the last reading before the gap exactly at
`last_reading_utc`) and cumulative part counters (one reading per hour at hh:50, the hour's `production_by_hour`)
to the real broker, shifted back SHIFT_DAYS whole days (multiple of 7: weekdays matter). Waits for the detector,
justifies the operator reasons (PATCH /api/downtime/{id}), then compares GET /api/oee (line scope, period /
group_by=day / group_by=shift, machine scope of the output machine) and /api/losses/pareto with `expected`.

The data is ≥ 14 days old, so the server must run with DOWNTIME_REWIND_HORIZON_H and DOWNTIME_INITIAL_LOOKBACK_H
covering it (e.g. 1000).

    API=http://localhost:8173 SHIFT_DAYS=14 .venv/bin/python e2e/scenario_d_replay.py
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
D = json.loads((ROOT / "docs/scenarios/scenario_d_line_multiday.json").read_text(encoding="utf-8"))
X = D["expected"]
API = os.getenv("API", "http://localhost:8000")
BROKER = os.getenv("BROKER", "localhost")
BROKER_PORT = int(os.getenv("BROKER_PORT", "1883"))
SHIFT_DAYS = int(os.getenv("SHIFT_DAYS", "14"))
assert SHIFT_DAYS % 7 == 0, "SHIFT_DAYS must keep the weekday"
SH = timedelta(days=SHIFT_DAYS)
HEARTBEAT_S = int(os.getenv("HEARTBEAT_S", "30"))
WAIT_S = float(os.getenv("WAIT_S", "600"))
TOL = float(os.getenv("TOL", "1e-9"))
TZ = ZoneInfo(D["plant"]["timezone"])
OUT, BN = D["line"]["output_machine"], D["line"]["bottleneck_machine"]
results: list[tuple[bool, str]] = []


def check(ok, name, got=None, exp=None):
    results.append((bool(ok), name))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   expected={exp!r} got={got!r}"))


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


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) - SH


def dshift(d: str) -> str:
    return (datetime.fromisoformat(d) - SH).date().isoformat()


class Api:
    def __init__(self):
        self.c = httpx.Client(base_url=API, timeout=120)

    def login(self, email, pw):
        r = self.c.post("/api/auth/login", json={"email": email, "password": pw})
        r.raise_for_status()
        self.c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    def __getattr__(self, verb):
        def call(path, expect=(200, 201), **kw):
            r = getattr(self.c, verb)(path, **kw)
            if r.status_code not in expect:
                raise RuntimeError(f"{verb.upper()} {path} -> {r.status_code} {r.text[:400]}")
            return r.json() if r.content else None
        return call


def build(api: Api) -> dict:
    stamp = int(time.time())
    email = f"qa-scd-{stamp}@qa-factoryconnect.com.br"
    api.post("/api/auth/register", json={"company_name": f"QA Cenario D {stamp}", "admin_name": "QA",
                                         "admin_email": email, "admin_password": "QaGolden#2026"})
    api.login(email, "QaGolden#2026")
    tenant = api.get("/api/auth/me")["tenant_id"]
    plant = api.post("/api/plants/", json={"name": D["plant"]["name"], "timezone": D["plant"]["timezone"]})
    line = api.post("/api/lines/", json={"name": D["line"]["name"], "plant_id": plant["id"]})
    prods = {}
    for code in sorted({p["product"] for m in D["machines"].values() for p in m["products"]}):
        prods[code] = api.post("/api/products/", json={"code": code, "name": code})["id"]
    mach = {}
    for name, m in D["machines"].items():
        mm = api.post("/api/machines/", json={"line_id": line["id"], "name": name})
        patch = {"micro_stop_threshold_s": m["micro_stop_threshold_s"]}
        if m.get("fault_code_map"):
            patch["fault_code_map"] = m["fault_code_map"]
        api.patch(f"/api/machines/{mm['id']}", json=patch)
        for p in m["products"]:
            api.post(f"/api/machines/{mm['id']}/products", json={"product_id": prods[p["product"]],
                                                                 "ideal_cycle_time_s": p["ideal_cycle_time_s"],
                                                                 "is_default": bool(p.get("is_default"))})
        mach[name] = mm["id"]
    api.patch(f"/api/lines/{line['id']}", json={"output_machine_id": mach[OUT], "bottleneck_machine_id": mach[BN]})
    tags = {}
    for name, role, dt in (("run", "run_state", "bool"), ("fault", "fault_state", "int"),
                           ("good", "part_counter_good", "int"), ("scrap", "part_counter_scrap", "int")):
        tags[name] = api.post("/api/tags/", json={"machine_id": mach[OUT], "name": name, "protocol": "opc_ua",
                                                  "address": f"ns=2;s=QA.D.{name}", "data_type": dt,
                                                  "semantic_role": role})["id"]
    shift_ids = {}
    for s in D["calendar"]["shifts"]:
        shift_ids[s["id"]] = api.post("/api/shifts/", json={
            "plant_id": plant["id"], "name": s["name"], "start_local": s["start_local"], "end_local": s["end_local"],
            "weekdays": s["weekdays"], "breaks": [{k: b[k] for k in ("start_local", "end_local", "reason_code")}
                                                  for b in s["breaks"]]})["id"]
    for e in D["calendar"]["exceptions"]:
        body = {"plant_id": plant["id"], "date": dshift(e["date"]), "kind": e["kind"], "note": e.get("note", "")}
        if e.get("shift_id"):
            body["shift_id"] = shift_ids[e["shift_id"]]
        for k in ("start_local", "end_local", "reason_code"):
            if e.get(k):
                body[k] = e[k]
        api.post("/api/calendar-exceptions/", json=body)
    for r in D["production_runs"]:
        for name in (OUT, BN):
            run = api.post("/api/production-runs/", json={"machine_id": mach[name], "product_id": prods[r["product"]],
                                                         "started_at": ts(r["from_utc"]).isoformat()})
            api.post(f"/api/production-runs/{run['id']}/stop", json={"ended_at": ts(r["to_utc"]).isoformat()})
    print(f"tenant {tenant}  login {email} / QaGolden#2026  line {line['id']}")
    return {"tenant": tenant, "plant": plant["id"], "line": line["id"], "machines": mach, "tags": tags, "email": email}


def readings(tags: dict) -> list[tuple[str, float, datetime]]:
    tr = D["signals_output_machine"]["transitions"]
    gaps = [(ts(g["from_utc"]) - timedelta(seconds=60), ts(g["to_utc"])) for g in D["data_gaps"]]
    last_before = {ts(g["last_reading_utc"]) for g in D["data_gaps"]}
    out = []
    for i, s in enumerate(tr):
        t0 = ts(s["ts"])
        out += [(tags["run"], s["run_state"], t0), (tags["fault"], s["fault_state"], t0)]
        if i + 1 < len(tr):
            nxt, hb = ts(tr[i + 1]["ts"]), t0 + timedelta(seconds=HEARTBEAT_S)
            while hb < nxt:
                if not any(a < hb < b for a, b in gaps):      # silence inside the data gap
                    out += [(tags["run"], s["run_state"], hb), (tags["fault"], s["fault_state"], hb)]
                hb += timedelta(seconds=HEARTBEAT_S)
    for lb in last_before:                                   # last reading before the gap exactly there
        assert any(t == lb for _, _, t in out), lb
    g = sc = 0
    first = ts(tr[0]["ts"])
    out += [(tags["good"], 0, first), (tags["scrap"], 0, first)]
    for h in D["production_by_hour"]:
        g += h["good"]
        sc += h["scrap"]
        at = ts(h["hour_utc"]) + timedelta(minutes=50)
        assert not any(a < at < b for a, b in gaps), at
        out += [(tags["good"], g, at), (tags["scrap"], sc, at)]
    out.sort(key=lambda r: r[2])
    return out


def publish(ids: dict) -> int:
    import paho.mqtt.client as mqtt
    msgs = readings(ids["tags"])
    cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"qa-scd-{os.getpid()}")
    cli.max_inflight_messages_set(200)
    cli.connect(BROKER, BROKER_PORT)
    cli.loop_start()
    pend = []
    for tag, v, at in msgs:
        pend.append(cli.publish(f"factory/{ids['tenant']}/sensors/{tag}",
                                json.dumps({"v": 1, "value": v, "ts": at.isoformat().replace("+00:00", "Z")}), qos=1))
        if len(pend) >= 150:
            for p in pend:
                p.wait_for_publish()
            pend = []
    for p in pend:
        p.wait_for_publish()
    cli.loop_stop()
    cli.disconnect()
    return len(msgs)


def local(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(TZ).strftime("%Y-%m-%d %H:%M")


def unshift_local(ev_local: str) -> str:
    return (datetime.strptime(ev_local, "%Y-%m-%d %H:%M") + SH).strftime("%Y-%m-%d %H:%M")


def wait_ingested(api: Api, ids: dict) -> None:
    """Barrier: every published run/fault reading is in InfluxDB (the MQTT handler may still be draining a
    backlog long after the downtime events look complete — the tail after the last event, e.g. the last
    micro-stop, would be missed), then the detector settles (downtime_settle_s) and ticks again."""
    want: dict[str, set] = {}
    for tag, _v, at in readings(ids["tags"]):
        if tag in (ids["tags"]["run"], ids["tags"]["fault"]):
            want.setdefault(tag, set()).add(at)
    t0 = time.time()
    while True:
        missing = 0
        for tag, tss in want.items():
            lo, hi = min(tss), max(tss)
            pts = api.get("/api/sensors/history", params={"tag_id": tag, "window": "1s", "start": lo.isoformat(),
                                                          "stop": (hi + timedelta(seconds=1)).isoformat()})["points"]
            missing += len(tss) - len(pts)       # one 1-s window per reading (readings are ≥ 10 s apart)
        if missing <= 0:
            break
        if time.time() - t0 > WAIT_S:
            print(f"  WARNING: {missing} readings still not in InfluxDB after {WAIT_S:.0f} s")
            return
        time.sleep(5)
    settle = float(os.getenv("DETECTOR_SETTLE_WAIT_S", "60"))   # downtime_settle_s 30 + 2 ticks of 10 s + margin
    print(f"  all readings ingested after {time.time() - t0:.0f} s; waiting {settle:.0f} s for the detector")
    time.sleep(settle)


def verify(api: Api, ids: dict) -> None:
    q = D["query"]
    start, stop = ts(q["start_utc"]), ts(q["stop_utc"])
    win = {"start": start.isoformat(), "stop": stop.isoformat()}
    mid, lid = ids["machines"][OUT], ids["line"]
    print("\n[ingest] waiting for the MQTT backlog to drain")
    wait_ingested(api, ids)
    want = sorted(e["from_local"] for e in D["downtime_events"])
    print(f"\n[events] window {start.isoformat()} → {stop.isoformat()}")
    t0, evs = time.time(), []
    while time.time() - t0 < WAIT_S:
        evs = api.get("/api/downtime/", params={"machine_id": mid, "limit": 1000, **win})
        got = sorted(unshift_local(local(e["stop_start"] or e["start"])) for e in evs if e["end"])
        if sorted(set(got)) == want and any(e["reason_code"] == "UNK-01" for e in evs):
            print(f"  events ready after {time.time() - t0:.0f} s")
            break
        time.sleep(5)
    by_start: dict[str, list] = {}
    for e in evs:
        by_start.setdefault(unshift_local(local(e["start"])), []).append(e)
    eq("downtime event starts (local)", sorted(by_start), want)
    for d in D["downtime_events"]:
        es = by_start.get(d["from_local"], [])
        eq(f"{d['from_local']} duration", sum(e["duration_s"] or 0 for e in es), d["duration_s"])
        if d.get("fault_code"):
            eq(f"{d['from_local']} suggested reason", es[0]["suggested_reason_code"] if es else None,
               D["machines"][OUT]["fault_code_map"][str(d["fault_code"])])
    print("\n[justify] PATCH /api/downtime/{id}")
    for d in D["downtime_events"]:
        for e in by_start.get(d["from_local"], []):
            if d["operator_reason_code"]:
                r = api.patch(f"/api/downtime/{e['id']}", json={"reason_code": d["operator_reason_code"]})
                eq(f"justify {d['from_local']} = {d['operator_reason_code']}", r["reason_code"], d["operator_reason_code"])
            else:
                eq(f"{d['from_local']} stays UNK-01 (shift end)", e["reason_code"], "UNK-01")

    def cmp(prefix, o, x):
        for k, v in (("pot_s", x["pot_min"] * 60), ("pdot_s", x["pdot_min"] * 60), ("nodata_s", x["nodata_min"] * 60),
                     ("pbt_s", x["pbt_min"] * 60), ("adot_s", x["adot_min"] * 60), ("apt_s", x["apt_min"] * 60),
                     ("data_coverage", x["data_coverage"]), ("produced", x["produced"]), ("good", x["good"]),
                     ("scrap", x["scrap"]), ("ideal_produced_s", x["ideal_produced_min"] * 60),
                     ("net_productive_s", x["net_productive_min"] * 60), ("availability", x["availability"]),
                     ("performance", x["performance"]), ("quality", x["quality"]), ("oee", x["oee"]),
                     ("micro_stop_count", x["micro_stop_count"]), ("micro_stop_loss_s", x["micro_stop_min"] * 60),
                     ("speed_loss_s", x["speed_loss_min"] * 60), ("quality_loss_s", x["quality_loss_min"] * 60),
                     ("availability_operational", x["availability_operational"]["ao"]),
                     ("loading", x.get("loading")), ("teep", x.get("teep"))):
            if v is not None or k in x:
                eq(f"{prefix}.{k}", o.get(k), v)

    print("\n[oee] line, whole period")
    o = api.get("/api/oee", params={"scope": "line", "id": lid, **win})
    cmp("period", o, X["period"])
    pos = X["produced_outside_schedule"]
    eq("period.produced_outside_schedule", {k: (o.get("produced_outside_schedule") or {}).get(k) for k in ("total", "good", "scrap")},
       {"total": pos["total"], "good": pos["good"], "scrap": pos["scrap"]})
    print(f"  (info) counts_prorated={o.get('counts_prorated')} product_unknown={o.get('product_unknown')} "
          f"calendar_default={o.get('calendar_default')} reason={o.get('reason')}")

    for gb, exp, key in (("day", X["days"], "productive_day"), ("shift", X["shifts"], "key")):
        print(f"\n[oee] line, group_by={gb}")
        g = api.get("/api/oee", params={"scope": "line", "id": lid, "group_by": gb, **win})
        pts = [p for p in g["points"] if (p.get("pbt_s") or 0) > 0 or p.get("oee") is not None or (p.get("good") or 0) > 0]
        print("  points:", [(local(p["t"]), p.get("label"), p.get("oee")) for p in g["points"]])
        if gb == "day":
            bykey = {(datetime.fromisoformat(p["t"]).astimezone(TZ) + SH).date().isoformat(): p for p in g["points"]}
        else:
            bykey = {}
            for x in exp:
                st = ts(x["start_utc"])
                hit = [p for p in g["points"] if abs((datetime.fromisoformat(p["t"]) - st).total_seconds()) < 1]
                if hit:
                    bykey[x[key]] = hit[0]
            eq("shift points = expected instances", len(pts), len(exp))
        for x in exp:
            p = bykey.get(x[key])
            if p is None:
                check(False, f"{gb} {x[key]}: point present", None, x[key])
                continue
            for k in ("availability", "performance", "quality", "oee", "teep"):
                if k in x:
                    eq(f"{gb} {x[key]}.{k}", p.get(k), x[k])
            eq(f"{gb} {x[key]}.pbt_s", p.get("pbt_s"), x["pbt_min"] * 60)
            eq(f"{gb} {x[key]}.good", p.get("good"), x["good"])
            eq(f"{gb} {x[key]}.scrap", p.get("scrap"), x["scrap"])
        cmp(f"group_by={gb} total", g, X["period"]) if gb == "day" else None

    print("\n[pareto] line")
    par = api.get("/api/losses/pareto", params={"scope": "line", "id": lid, **win})
    lp = X["period"]["loss_pareto"]
    eq("pareto order", [r["reason_code"] for r in par], [i[0] for i in lp["items"]])
    for r, (code, minutes, pct, cum) in zip(par, lp["items"]):
        eq(f"pareto {code} min", r["duration_s"] / 60, minutes)
        eq(f"pareto {code} pct", r["pct"], pct, 5e-7)
        eq(f"pareto {code} cumulative", r["cumulative_pct"], cum, 5e-7)
    cat = api.get("/api/losses/pareto", params={"scope": "line", "id": lid, "by": "category", **win})
    eq("pareto by category", [(r["category"], round(r["duration_s"] / 60, 6)) for r in cat],
       [(k, v) for k, v in X["period"]["loss_pareto_by_category"]])

    print("\n[oee] machine scope (output machine, its own ideal cycle)")
    m = api.get("/api/oee", params={"scope": "machine", "id": mid, **win})
    mso = X["machine_scope_output"]
    for k, v in (("pbt_s", mso["pbt_min"] * 60), ("apt_s", mso["apt_min"] * 60),
                 ("ideal_produced_s", mso["ideal_produced_min"] * 60), ("net_productive_s", mso["net_productive_min"] * 60),
                 ("availability", mso["availability"]), ("performance", mso["performance"]),
                 ("quality", mso["quality"]), ("oee", mso["oee"])):
        eq(f"machine.{k}", m.get(k), v)


def main() -> int:
    api = Api()
    if os.getenv("VERIFY_IDS"):                      # verify an earlier run again (no build/publish)
        ids = json.load(open(os.environ["VERIFY_IDS"]))
        api.login(ids["email"], "QaGolden#2026")
    else:
        ids = build(api)
        n = publish(ids)
        print(f"published {n} readings to {BROKER}:{BROKER_PORT}")
        json.dump(ids, open(os.getenv("IDS_OUT", "/dev/null"), "w"))
    verify(api, ids)
    bad = [n for ok, n in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
