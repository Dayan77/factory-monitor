"""Scale harness — data set (Sprint 7, docs/performance.md).

T tenants × L lines × M machines, each machine with 12 signal tags (run_state, fault_state, 10 analog) + 1 part
counter (part_counter_total); one plant per tenant with two shifts (06–14, 14–22, coffee + meal breaks), one product
with PRI per machine, 2 positions + components + 2 maintenance plans per machine, and HISTORY_DAYS of history written
straight to Postgres (hourly production/operating buckets, downtime events with reasons, micro-stops, failures, data
spans) — what 30 days of real operation would leave, so /api/oee (30 days), the Pareto and the plan status have real
volume to read. Detector cursors start at `now` (the history is final; the live load is detected from there).

Bulk tables go through COPY (asyncpg); entities through the ORM of the server itself.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
ANALOG = ("temperatura", "vibração", "corrente", "pressão", "vazão", "rotação", "torque", "umidade", "tensão",
          "nível")
REASONS = (("EQP-01", 0.30), ("EQP-03", 0.15), ("MAT-01", 0.15), ("SET-01", 0.15), ("QLT-01", 0.05),
           ("OPR-01", 0.10), ("UNK-01", 0.10))
PASSWORD = "Escala#Teste2026!"


def _uid() -> str:
    return str(uuid.uuid4())


async def seed(session_factory, pg_dsn: str, tenants: int, lines: int, machines: int, history_days: int,
               now: datetime, tz: str = "America/Manaus") -> dict:
    """Returns {"tenants": [{"id", "email", "plant_id", "lines": [{"id", "output"}], "machines": [{"id", "line_id",
    "tags": {name: id}, "counter": id}], "users": {...}}]}."""
    import asyncpg
    from src.auth.security import hash_password
    from src.models import (Component, DowntimeDetectorState, Line, Machine, MachineProduct, MaintenancePlan, Plant,
                            Position, Product, ProductionRun, Shift, Tag, Tenant, User, UserRole)
    from src.models.tag import SemanticRole

    rnd = random.Random(7)
    pw = hash_password(PASSWORD)
    out: dict = {"tenants": []}
    hist0 = (now - timedelta(days=history_days)).replace(minute=0, second=0, microsecond=0)
    bulk = {"usage_buckets": [], "usage_totals": [], "downtime_events": [], "micro_stop_buckets": [],
            "machine_data_spans": [], "failure_records": []}
    async with session_factory() as s:
        for ti in range(tenants):
            t = Tenant(name=f"Escala {ti + 1}", slug=f"escala-{ti + 1}-{uuid.uuid4().hex[:6]}")
            s.add(t)
            await s.flush()
            email = f"admin.escala{ti + 1}.{uuid.uuid4().hex[:4]}@example.com"
            s.add(User(tenant_id=t.id, email=email, password_hash=pw, name=f"Admin {ti + 1}", role=UserRole.admin))
            plant = Plant(tenant_id=t.id, name=f"Planta {ti + 1}", timezone=tz)
            s.add(plant)
            await s.flush()
            for name, a, b, brk in (("Turno A", "06:00", "14:00", [("09:00", "09:15"), ("11:30", "12:00")]),
                                    ("Turno B", "14:00", "22:00", [("17:00", "17:15"), ("19:30", "20:00")])):
                s.add(Shift(tenant_id=t.id, plant_id=plant.id, name=name, start_local=a, end_local=b,
                            weekdays=[0, 1, 2, 3, 4, 5], breaks=[{"start_local": x, "end_local": y,
                                                                  "reason_code": "PLN-01"} for x, y in brk],
                            valid_from=(now - timedelta(days=history_days + 2)).date()))
            prod = Product(tenant_id=t.id, code=f"P{ti + 1}", name=f"Peça {ti + 1}")
            s.add(prod)
            await s.flush()
            tout = {"id": t.id, "email": email, "plant_id": plant.id, "lines": [], "machines": []}
            for li in range(lines):
                ln = Line(tenant_id=t.id, plant_id=plant.id, name=f"Linha {li + 1}")
                s.add(ln)
                await s.flush()
                mids = []
                for mi in range(machines):
                    pri = 20.0 + 2 * mi
                    m = Machine(tenant_id=t.id, line_id=ln.id, name=f"L{li + 1} Máquina {mi + 1:02d}",
                                ideal_cycle_time_s=pri, micro_stop_threshold_s=120, fault_code_map={})
                    s.add(m)
                    await s.flush()
                    mids.append(m.id)
                    s.add(MachineProduct(tenant_id=t.id, machine_id=m.id, product_id=prod.id, ideal_cycle_time_s=pri,
                                         is_default=True))
                    s.add(ProductionRun(tenant_id=t.id, machine_id=m.id, product_id=prod.id,
                                        started_at=hist0 - timedelta(days=1)))
                    tags = {}
                    for nm, role, dt in (("run", SemanticRole.run_state, "bool"),
                                         ("fault", SemanticRole.fault_state, "int")):
                        tg = Tag(tenant_id=t.id, machine_id=m.id, name=nm, protocol="mqtt", address=f"x/{nm}",
                                 data_type=dt, semantic_role=role)
                        s.add(tg)
                        await s.flush()
                        tags[nm] = tg.id
                    for k, nm in enumerate(ANALOG):
                        tg = Tag(tenant_id=t.id, machine_id=m.id, name=nm, protocol="mqtt", address=f"x/a{k}",
                                 data_type="float", unit="u")
                        s.add(tg)
                        await s.flush()
                        tags[nm] = tg.id
                    ct = Tag(tenant_id=t.id, machine_id=m.id, name="peças", protocol="mqtt", address="x/ct",
                             data_type="int", semantic_role=SemanticRole.part_counter_total, counter_max=2 ** 31 - 1)
                    s.add(ct)
                    await s.flush()
                    poss = []
                    for pi, (pname, ctype) in enumerate((("Cilindro", "pneumatic_cylinder"),
                                                         ("Motor", "electric_motor"))):
                        pos = Position(tenant_id=t.id, machine_id=m.id, name=f"{pname} {mi + 1}", component_type=ctype,
                                       cycles_per_part=1)
                        s.add(pos)
                        await s.flush()
                        poss.append(pos.id)
                        comp = Component(tenant_id=t.id, position_id=pos.id, last_position_id=pos.id,
                                         component_type=ctype, manufacturer="Festo" if pi == 0 else "WEG",
                                         model="DSBC" if pi == 0 else "W22", serial_number=f"S{uuid.uuid4().hex[:8]}",
                                         installed_at=hist0 - timedelta(days=60),
                                         nominal_life={"cycles": 5_000_000} if pi == 0 else {"hours": 30000})
                        s.add(comp)
                        await s.flush()
                        s.add(MaintenancePlan(tenant_id=t.id, machine_id=m.id, position_id=pos.id,
                                              name=f"Plano {pname} {mi + 1}",
                                              interval_cycles=2_000_000 if pi == 0 else None,
                                              interval_hours=None if pi == 0 else 4000, interval_days=180,
                                              last_reset_at=hist0, last_reset_usage={}))
                        bulk["usage_totals"].append(("component", comp.id, "cycles" if pi == 0 else "operating_seconds",
                                                     t.id, 0.0))
                    s.add(DowntimeDetectorState(machine_id=m.id, tenant_id=t.id, checkpoint=now, state={}))
                    tout["machines"].append({"id": m.id, "line_id": ln.id, "tags": tags, "counter": ct.id,
                                             "positions": poss, "pri": pri})
                ln.output_machine_id = mids[-1]
                ln.bottleneck_machine_id = mids[len(mids) // 2]
                tout["lines"].append({"id": ln.id, "output": mids[-1]})
            out["tenants"].append(tout)
        await s.commit()

    # ── history (COPY) ────────────────────────────────────────────────────────
    for tout in out["tenants"]:
        tid = tout["id"]
        for m in tout["machines"]:
            mid, pri = m["id"], m["pri"]
            bulk["machine_data_spans"].append((_uid(), tid, mid, hist0, now))
            totals: dict[tuple, float] = {}
            h = hist0
            while h < now:
                local_h = (h - timedelta(hours=4)).hour            # America/Manaus (UTC−4, no DST)
                wd = (h - timedelta(hours=4)).weekday()
                in_shift = 6 <= local_h < 22 and wd < 6
                if in_shift:
                    run_s = rnd.uniform(2700, 3500)
                    parts = int(run_s / pri * rnd.uniform(0.85, 0.98))
                    scrap = int(parts * rnd.uniform(0.0, 0.03))
                    for meas, v in (("total", parts), ("good", parts - scrap), ("scrap", scrap),
                                    ("operating_seconds", run_s)):
                        bulk["usage_buckets"].append(("machine", mid, meas, h, tid, float(v)))
                    for pi, pid in enumerate(m["positions"]):
                        meas = "cycles" if pi == 0 else "operating_seconds"
                        v = float(parts) if pi == 0 else run_s
                        bulk["usage_buckets"].append(("position", pid, meas, h, tid, v))
                        totals[("position", pid, meas)] = totals.get(("position", pid, meas), 0.0) + v
                    if rnd.random() < 0.35:
                        n = rnd.randint(1, 4)
                        bulk["micro_stop_buckets"].append((mid, h, "", tid, None, (h - timedelta(hours=4)).date(), h, n,
                                                           float(n * rnd.uniform(30, 100)), 0))
                    if rnd.random() < 0.25:
                        start = h + timedelta(minutes=rnd.randint(0, 50))
                        dur = rnd.randint(150, 1500)
                        x, acc = rnd.random(), 0.0
                        code = REASONS[-1][0]
                        for c, p in REASONS:
                            acc += p
                            if x <= acc:
                                code = c
                                break
                        bulk["downtime_events"].append((_uid(), tid, mid, start, start + timedelta(seconds=dur),
                                                        float(dur), start, "auto_run_state", code,
                                                        code if code.startswith("EQP") else None, False,
                                                        (h - timedelta(hours=4)).date()))
                        if code.startswith("EQP") and rnd.random() < 0.3:
                            bulk["failure_records"].append((_uid(), tid, mid, m["positions"][0], start,
                                                            start + timedelta(seconds=dur), "critical", "complete",
                                                            "production_interference"))
                h += timedelta(hours=1)
            for (scope, eid, meas), v in totals.items():
                bulk["usage_totals"].append((scope, eid, meas, tid, v))
    cols = {
        "usage_buckets": ("scope", "entity_id", "measure", "bucket_start", "tenant_id", "value"),
        "usage_totals": ("scope", "entity_id", "measure", "tenant_id", "value"),
        "downtime_events": ("id", "tenant_id", "machine_id", "start", "end", "duration_s", "stop_start", "source",
                            "reason_code", "suggested_reason_code", "planned", "productive_day"),
        "micro_stop_buckets": ("machine_id", "bucket_start", "shift_slot", "tenant_id", "shift_id", "productive_day",
                               "piece_start", "count", "loss_s", "short_faults"),
        "machine_data_spans": ("id", "tenant_id", "machine_id", "start", "end"),
        "failure_records": ("id", "tenant_id", "machine_id", "position_id", "failed_at", "restored_at", "severity",
                            "status", "detection_method"),
    }
    conn = await asyncpg.connect(pg_dsn)
    try:
        for table, rows in bulk.items():
            if rows:
                if table == "usage_totals":          # component totals were added once per component
                    rows = list({(r[0], r[1], r[2]): r for r in rows}.values())
                await conn.copy_records_to_table(table, records=rows, columns=list(cols[table]))
        await conn.execute("ANALYZE")
    finally:
        await conn.close()
    out["counts"] = {k: len(v) for k, v in bulk.items()}
    return out
