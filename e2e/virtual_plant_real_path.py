"""Real-path check: virtual plant -> edge gateway -> MQTT -> server -> REST.

Run it AFTER the virtual plant stopped producing (e.g. started with `--shifts` so the shift ended) and the
server drained its backlog. It compares, with tolerance 0:

* cycle_pulse: the virtual plant's emitted pulses == the edge counter (SQLite) == what the gateway published
  last, and the server position's cycles == last published - first published (the first reading is a baseline).
* 16-bit / 32-bit PLC counters: server cycles == sum of the wrapped deltas of the raw series stored in Influx,
  wraps seen in Influx == the virtual plant's own rollover count, and the last raw value == the PLC register.
* run_state: server starts/hours printed next to the virtual plant truth (informative: in accelerated mode
  the server measures wall-clock time and the 1 s debounce equals 60 simulated seconds at --speed 60).
* REST: plant tree, machine usage, line production and cadence return data.

Env: VP_STATE (http://127.0.0.1:8161/state), PLANT_MAP (provisioned plant.local.json), COUNTER_DB (gateway
counter SQLite), API (http://localhost:8000), INFLUX_URL/INFLUX_TOKEN/INFLUX_ORG/INFLUX_BUCKET, START (RFC3339
lower bound for the Influx range), EMAIL/PASSWORD. Exit code 0 = every strict check passed.
"""
from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import sys

import httpx

VP_STATE = os.getenv("VP_STATE", "http://127.0.0.1:8161/state")
PLANT_MAP = os.environ["PLANT_MAP"]
COUNTER_DB = os.environ["COUNTER_DB"]
API = os.getenv("API", "http://localhost:8000")
INFLUX = os.getenv("INFLUX_URL", "http://localhost:8086")
TOKEN = os.getenv("INFLUX_TOKEN", "dev-token-change-me")
ORG = os.getenv("INFLUX_ORG", "factory-monitor")
BUCKET = os.getenv("INFLUX_BUCKET", "sensors")
START = os.environ["START"]
# Optional: JSON written by `--snapshot` before the run, so the checks compare INCREMENTS on a backend whose
# positions already hold usage from earlier runs (e.g. the shared dev backend). Without it totals must start at 0.
BASELINE = os.getenv("BASELINE")

results: list[tuple[bool, str]] = []


def check(ok, name, got=None, exp=None):
    results.append((bool(ok), name))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   expected={exp!r} got={got!r}"))


def influx_series(tag_id: str) -> list[tuple[str, float]]:
    flux = (f'from(bucket:"{BUCKET}") |> range(start: {START}) '
            f'|> filter(fn:(r)=> r.tag_id == "{tag_id}" and r._field == "value") '
            f'|> keep(columns:["_time","_value"]) |> sort(columns:["_time"])')
    r = httpx.post(f"{INFLUX}/api/v2/query", params={"org": ORG}, content=flux, timeout=60,
                   headers={"Authorization": f"Token {TOKEN}", "Content-Type": "application/vnd.flux",
                            "Accept": "application/csv"})
    r.raise_for_status()
    out = []
    for row in csv.DictReader(io.StringIO(r.text)):
        if row.get("_time"):
            out.append((row["_time"], float(row["_value"])))
    return out


def wrapped_sum(series, counter_max):
    total, wraps = 0, 0
    for (_, a), (_, b) in zip(series, series[1:]):
        if b >= a:
            total += b - a
        else:
            wraps += 1
            total += (counter_max - a) + b + 1
    return total, wraps


def main() -> int:
    vp = httpx.get(VP_STATE, timeout=10).json()
    truth, values = vp["truth"], vp["values"]
    pmap = json.load(open(PLANT_MAP))
    tags = {t["key"]: t for t in pmap["tags"]}
    api = httpx.Client(base_url=API, timeout=30)
    tok = api.post("/api/auth/login", json={"email": os.getenv("EMAIL", "admin@factory-monitor.com"),
                                            "password": os.getenv("PASSWORD", "Admin@1234")}).json()["access_token"]
    api.headers["Authorization"] = f"Bearer {tok}"
    positions = {}
    for m_name, m_id in pmap["asset_ids"].get("machines", {}).items():
        for p in api.get(f"/api/machines/{m_id}/usage").json()["positions"]:
            positions[(m_name.split("/")[-1], p["name"])] = p
    if BASELINE:
        base = json.load(open(BASELINE))
        for k, p in positions.items():
            b = base.get(f"{k[0]}|{k[1]}", {})
            for meas in ("cycles_total", "hours_total", "starts_total"):
                p[meas] = round((p[meas] or 0) - b.get(meas, 0), 9)
    print(f"virtual plant sim_time_s={vp['sim_time_s']}  pulses={vp['runtime']['pulses']}")

    print("\n[cycle_pulse] Cilindro A1 (tolerance 0)")
    key = "montagem.fixacao.a1.eos"
    t = tags[key]
    emitted = vp["runtime"]["pulses"][key]["emitted"]
    backlog = vp["runtime"]["pulses"][key]["backlog"]
    edge = sqlite3.connect(COUNTER_DB).execute("SELECT value FROM edge_counters WHERE key=?", (key,)).fetchone()[0]
    ser = influx_series(t["tag_id"])
    check(backlog == 0, "virtual plant pulse backlog drained", backlog, 0)
    check(edge == emitted, "edge counter == pulses emitted by the virtual plant", edge, emitted)
    check(ser and ser[-1][1] == edge, "last published value == edge counter", ser[-1][1] if ser else None, edge)
    p = positions[(t["machine"], t["position"])]
    exp = ser[-1][1] - ser[0][1]
    check(p["cycles_total"] == exp, f"server A1 cycles == last - first published ({ser[-1][1]:.0f} - {ser[0][1]:.0f})",
          p["cycles_total"], exp)
    print(f"        first published {ser[0][1]:.0f} at {ser[0][0]}; VP strokes A1 = {truth['cylinders']['A1']['strokes']}")

    print("\n[PLC counters] wrapped deltas vs server, rollovers vs virtual plant")
    for cyl in ("A2", "A3", "A4"):
        key = f"montagem.fixacao.{cyl.lower()}.cycles"
        t = tags[key]
        ser = influx_series(t["tag_id"])
        total, wraps = wrapped_sum(ser, t["counter_max"])
        p = positions[(t["machine"], t["position"])]
        check(ser[-1][1] == values[key], f"{cyl} last raw == PLC register {values[key]}", ser[-1][1], values[key])
        check(p["cycles_total"] == total, f"{cyl} server cycles == Σ wrapped deltas of raw series ({len(ser)} readings)",
              p["cycles_total"], total)
        check(wraps == truth["cylinders"][cyl]["rollovers"] - _rollovers_before(ser, cyl, truth),
              f"{cyl} wraps in raw series ({wraps}) == virtual plant rollovers ({truth['cylinders'][cyl]['rollovers']})",
              wraps, truth["cylinders"][cyl]["rollovers"])
    # machine-level 16-bit A1 PLC counter (cross-check tag)
    key = "montagem.fixacao.a1.cycles"
    ser = influx_series(tags[key]["tag_id"])
    total, wraps = wrapped_sum(ser, 65535)
    print(f"        A1 PLC 16-bit (machine-level): Σ wrapped deltas {total}, wraps {wraps}, last {ser[-1][1]:.0f}")
    for i in range(1, 7):
        key = f"montagem.robo.axis{i}.cycles"
        t = tags[key]
        ser = influx_series(t["tag_id"])
        total, _ = wrapped_sum(ser, t["counter_max"])
        p = positions[(t["machine"], t["position"])]
        check(p["cycles_total"] == total, f"Robô Eixo {i} server cycles == Σ deltas ({total})", p["cycles_total"], total)

    print("\n[run_state] informative (wall clock vs simulated time)")
    speed = vp["runtime"]["speed"]
    for key, tv in truth["run_state"].items():
        t = tags.get(key)
        if not t or not t["position"]:
            continue
        p = positions[(t["machine"], t["position"])]
        print(f"        {t['machine']} / {t['position']}: server starts={p['starts_total']:.0f} hours={p['hours_total']:.4f}"
              f"  | VP starts={tv['starts']} on_s/speed={tv['on_s'] / speed / 3600:.4f} h")
        check(p["hours_total"] > 0, f"{t['position']} ({t['machine']}) accumulated hours > 0", p["hours_total"], ">0")

    print("\n[REST] tree, line production, cadence")
    tree = api.get("/api/plants/tree").json()
    plant = next(pl for pl in tree if pl["name"] == "Planta Manaus")
    # Only the virtual plant's lines: on the shared dev backend the legacy simulator's "Linha Demo (Simulador)" also
    # lands in the tenant's first plant (LineCreate default).
    vp_lines = set(pmap["asset_ids"].get("lines", {}).values())
    mine = [ln for ln in plant["lines"] if ln["id"] in vp_lines]
    check(len(mine) == 2, "tree has the 2 virtual-plant lines", [ln["name"] for ln in plant["lines"]], 2)
    for ln in mine:
        prod = api.get(f"/api/lines/{ln['id']}/production", params={"start": START, "bucket": "1h"}).json()
        out_m = next(m["name"] for m in ln["machines"] if m["id"] == ln["output_machine_id"])
        print(f"        {ln['name']} (saída {out_m}): good={prod['good']} scrap={prod['scrap']} total={prod['total']} "
              f"cadence={prod['cadence_per_hour']} running={prod['cadence_running_per_hour']} points={len(prod['points'])}")
        check(prod["total"] > 0 and prod["points"], f"{ln['name']} production has data", prod["total"], ">0")
        good_key = {"Centro de Usinagem": "usinagem.centro", "Transportador de Saída": "montagem.transportador"}[out_m]
        g = influx_series(tags[f"{good_key}.parts_good"]["tag_id"])
        s = influx_series(tags[f"{good_key}.parts_scrap"]["tag_id"])
        exp_good, exp_scrap = g[-1][1] - g[0][1], s[-1][1] - s[0][1]
        if BASELINE:
            b = json.load(open(BASELINE)).get(f"line|{ln['id']}", {})
            prod["good"] -= b.get("good", 0)
            prod["scrap"] -= b.get("scrap", 0)
        check((prod["good"], prod["scrap"]) == (exp_good, exp_scrap),
              f"{ln['name']} good/scrap == last - first raw part counters", (prod["good"], prod["scrap"]), (exp_good, exp_scrap))
    failed = [n for ok, n in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


def _rollovers_before(series, cyl, truth) -> int:
    """Rollovers the virtual plant made before the first reading reached the server (not observable)."""
    return int(os.getenv(f"ROLLOVERS_BEFORE_{cyl}", "0"))


def snapshot(path: str) -> None:
    """Store every provisioned position's current totals (used as BASELINE by a later run)."""
    pmap = json.load(open(PLANT_MAP))
    api = httpx.Client(base_url=API, timeout=30)
    tok = api.post("/api/auth/login", json={"email": os.getenv("EMAIL", "admin@factory-monitor.com"),
                                            "password": os.getenv("PASSWORD", "Admin@1234")}).json()["access_token"]
    api.headers["Authorization"] = f"Bearer {tok}"
    out = {}
    for m_name, m_id in pmap["asset_ids"].get("machines", {}).items():
        for p in api.get(f"/api/machines/{m_id}/usage").json()["positions"]:
            out[f"{m_name.split('/')[-1]}|{p['name']}"] = {k: p[k] or 0 for k in ("cycles_total", "hours_total", "starts_total")}
    # production buckets are hourly (start is floored to the hour): keep what the window already holds
    for ln_id in pmap["asset_ids"].get("lines", {}).values():
        prod = api.get(f"/api/lines/{ln_id}/production", params={"start": START, "bucket": "1h"}).json()
        out[f"line|{ln_id}"] = {"good": prod["good"], "scrap": prod["scrap"]}
    json.dump(out, open(path, "w"), indent=1)
    print(f"snapshot of {len(out)} positions -> {path}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--snapshot":
        snapshot(sys.argv[2])
        sys.exit(0)
    sys.exit(main())
