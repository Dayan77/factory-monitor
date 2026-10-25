"""Helpers for the OEE / downtime tests: golden scenario C, calendar setup, detector runs."""
import json
from datetime import datetime, timedelta
from pathlib import Path

from usage_helpers import add_tag, api, build_plant, publish

SCENARIO_C = json.loads((Path(__file__).resolve().parents[2] / "docs" / "scenarios" /
                         "scenario_c_oee_shift.json").read_text(encoding="utf-8"))
# The API clips POT at the real "now": replay the scenario N whole days in the past (Manaus has no
# DST, so local times and every number stay the same — docs/scenarios/README.md).
SHIFT_DAYS = 7
HEARTBEAT_S = 30            # < machine_offline_timeout_s (60): data_coverage = 1 (C11)


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) - timedelta(days=SHIFT_DAYS)


def close(a, b, tol=1e-6) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= tol * max(1.0, abs(b))


async def add_shift(client, t, plant_id, name, start, end, weekdays=(0, 1, 2, 3, 4, 5, 6), breaks=()):
    return await api(client, "POST", "/api/shifts/", t.headers("admin"), 201, json={
        "plant_id": plant_id, "name": name, "start_local": start, "end_local": end,
        "weekdays": list(weekdays), "breaks": [dict(b) for b in breaks]})


async def run_detector(machine_id: str, tenant_id: str, now: datetime):
    from src.db import AsyncSessionLocal
    from src.services.oee.detector import detect_machine
    from src.services.oee.engine import load_calendar, machine_infos
    async with AsyncSessionLocal() as s:
        info = (await machine_infos(s, tenant_id, [machine_id]))[machine_id]
        cal = await load_calendar(s, tenant_id, info.plant_id, info.tz)
        return await detect_machine(s, info, cal, now)


async def setup_scenario_c(client, t, label="C"):
    """Torno CNC of scenario C: plant/line/machine, tags, Turno A with breaks, product + PRI, run."""
    C = SCENARIO_C
    h = t.headers("admin")
    p = await build_plant(client, t, label, tz=C["plant"]["timezone"])
    m = C["machine"]
    p.machine = await api(client, "PATCH", f"/api/machines/{p.machine['id']}", h, json={
        "micro_stop_threshold_s": m["micro_stop_threshold_s"], "fault_code_map": m["fault_code_map"],
        "name": m["name"]})
    await api(client, "PATCH", f"/api/lines/{p.line['id']}", h, json={"output_machine_id": p.machine["id"]})
    run = await add_tag(client, t, p, "run", "run_state", data_type="bool")
    fault = await add_tag(client, t, p, "fault", "fault_state")
    good = await add_tag(client, t, p, "good", "part_counter_good")
    scrap = await add_tag(client, t, p, "scrap", "part_counter_scrap")
    breaks = [{"start_local": e["from_local"][:5], "end_local": e["to_local"][:5], "reason_code": e["reason_code"]}
              for e in C["events"] if e["kind"] == "planned_break"]
    shift = await add_shift(client, t, p.plant["id"], C["shift"]["name"], C["shift"]["start_local"],
                            C["shift"]["end_local"], breaks=breaks)
    prod = await api(client, "POST", "/api/products/", h, 201, json={"code": m["product"], "name": "Eixo 20 mm"})
    await api(client, "POST", f"/api/machines/{p.machine['id']}/products", h, 201, json={
        "product_id": prod["id"], "ideal_cycle_time_s": m["ideal_cycle_time_s"], "is_default": True})
    start = ts(C["shift"]["start_utc"])
    await api(client, "POST", "/api/production-runs/", t.headers("operator"), 201, json={
        "machine_id": p.machine["id"], "product_id": prod["id"], "started_at": start.isoformat()})
    return p, {"run": run, "fault": fault, "good": good, "scrap": scrap}, shift, prod


def scenario_c_readings(tags: dict) -> list[tuple[str, float, datetime]]:
    """run/fault transitions of the JSON + heartbeats every HEARTBEAT_S + part counters
    (441 good, 9 scrap) spread over the running time, all inside the shift."""
    C = SCENARIO_C
    sig = C["signals"]
    out = []
    for i, s in enumerate(sig):
        t0 = ts(s["ts"])
        out.append((tags["run"]["id"], s["run_state"], t0))
        out.append((tags["fault"]["id"], s["fault_state"], t0))
        if i + 1 < len(sig):
            nxt = ts(sig[i + 1]["ts"])
            hb = t0 + timedelta(seconds=HEARTBEAT_S)
            while hb < nxt:
                out.append((tags["run"]["id"], s["run_state"], hb))
                out.append((tags["fault"]["id"], s["fault_state"], hb))
                hb += timedelta(seconds=HEARTBEAT_S)
    start, end = ts(C["shift"]["start_utc"]), ts(C["shift"]["end_utc"])
    good_total, scrap_total = C["production"]["good"], C["production"]["scrap"]
    n = 48                                    # one counter reading every 10 min
    for k in range(n):
        tk = start + timedelta(seconds=600 * k + 1)
        out.append((tags["good"]["id"], round(good_total * k / (n - 1)), tk))
        out.append((tags["scrap"]["id"], round(scrap_total * k / (n - 1)), tk))
    assert all(start <= r[2] <= end for r in out)
    out.sort(key=lambda r: r[2])
    return out


async def publish_all(tenant_id: str, readings, heartbeats_fast: bool = True) -> None:
    """Readings in time order. Signal changes and counters go through the real MQTT handler
    (`publish`); unchanged heartbeats, when `heartbeats_fast`, through the same two steps that
    matter here (Influx write + usage processing) on one pooled session — the full handler opens
    four NullPool connections per reading, which made this test take minutes."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from src.config import settings
    from src.services.influx import influx
    from src.services.usage.processor import process_reading
    from src.services.usage.topology import load_tag_info
    last: dict[str, float] = {}
    infos: dict = {}
    engine = create_async_engine(settings.database_url, pool_size=1, max_overflow=0)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            for tag_id, value, at in readings:
                if heartbeats_fast and last.get(tag_id) == value:
                    info = infos.get(tag_id) or await load_tag_info(s, tenant_id, tag_id)
                    infos[tag_id] = info
                    await influx.write(tenant_id=tenant_id, tag_id=tag_id, value=float(value), ts=at,
                                       machine_id=info.machine_id)
                    await process_reading(s, info, float(value), at, fresh=False)
                else:
                    await publish(tenant_id, tag_id, value, at)
                last[tag_id] = value
    finally:
        await engine.dispose()
