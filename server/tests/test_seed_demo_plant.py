"""scripts/seed_dev.py demo plant: built from the virtual plant map, idempotent, provision-compatible."""

import sys
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import seed_dev  # noqa: E402

MAP = Path(__file__).resolve().parents[2] / "edge" / "config" / "plant.json"

DOC = {
    "sources": [{"id": "opcua-plc", "protocol": "opcua"}, {"id": "modbus-plc", "protocol": "modbus"}],
    "assets": {"plant": {"name": "Planta Manaus", "timezone": "America/Manaus", "address": "Manaus"},
               "lines": [{"name": "Linha de Montagem", "output_machine": "Estação de Fixação", "machines": [{
                   "name": "Estação de Fixação", "model": "Dispositivo", "serial_number": "VP-FIX",
                   "positions": [
                       {"name": "Cilindro A1", "component_type": "pneumatic_cylinder", "cycles_per_part": 1,
                        "counter_tag": "montagem.fixacao.a1.eos",
                        "component": {"component_type": "pneumatic_cylinder", "manufacturer": "Festo",
                                      "model": "DSBC", "serial_number": "SEED-A1",
                                      "nominal_life": {"cycles": 8000000, "hours": None, "starts": None}}},
                       {"name": "Servo", "component_type": "servo_motor", "cycles_per_part": 1, "counter_tag": None,
                        "component": {"component_type": "servo_motor", "manufacturer": "Siemens", "model": "1FK7",
                                      "serial_number": "SEED-SV", "nominal_life": {"hours": 25000}}}]}]}]},
    "tags": [
        {"key": "montagem.fixacao.a1.eos", "name": "A1 fim de curso", "line": "Linha de Montagem",
         "machine": "Estação de Fixação", "position": "Cilindro A1", "semantic_role": "cycle_pulse",
         "data_type": "bool", "unit": "ciclos", "counter_max": None, "source": "modbus-plc", "address": 0,
         "unit_id": 2, "register_type": "discrete"},
        {"key": "montagem.fixacao.a1.cycles", "name": "A1 ciclos", "line": "Linha de Montagem",
         "machine": "Estação de Fixação", "position": "Cilindro A1", "semantic_role": "cycle_counter",
         "data_type": "int", "unit": "ciclos", "counter_max": 65535, "source": "modbus-plc", "address": 3,
         "unit_id": 2, "register_type": "holding", "data_type_plc": "uint16"},
        {"key": "montagem.fixacao.parts_good", "name": "Peças boas", "line": "Linha de Montagem",
         "machine": "Estação de Fixação", "position": None, "semantic_role": "part_counter_good",
         "data_type": "int", "unit": "pcs", "counter_max": 4294967295, "source": "opcua-plc",
         "address": "ns=2;s=montagem.fixacao.parts_good"},
    ],
}


def test_tag_address_format_matches_provision():
    assert seed_dev.tag_address({"address": "ns=2;s=x"}) == "ns=2;s=x"
    assert seed_dev.tag_address({"address": 3, "unit_id": 2, "register_type": "holding",
                                 "data_type_plc": "uint16"}) == "unit=2;hr=3:uint16"
    assert seed_dev.tag_address({"address": 0, "unit_id": 2, "register_type": "discrete"}) == "unit=2;di=0"


def test_load_plant_map_switches(tmp_path):
    assert seed_dev.load_plant_map({"SEED_DEMO_PLANT": "0"}) is None
    assert seed_dev.load_plant_map({"SEED_PLANT_MAP": str(tmp_path / "missing.json")}) is None
    if MAP.exists():
        doc = seed_dev.load_plant_map({"SEED_PLANT_MAP": str(MAP)})
        assert doc["assets"]["plant"]["name"] == "Planta Manaus"


async def test_seed_demo_plant_idempotent(tenants, db):
    from src.models import Component, Line, Plant, Position, Tag
    from src.db import AsyncSessionLocal

    async with AsyncSessionLocal() as s:
        first = await seed_dev.seed_demo_plant(s, tenants.b.id, DOC)
    async with AsyncSessionLocal() as s:
        second = await seed_dev.seed_demo_plant(s, tenants.b.id, DOC)
    assert first["created"] > 0 and second["created"] == 0 and first["plant_id"] == second["plant_id"]
    plant = await db.get(Plant, first["plant_id"])
    assert plant.timezone == "America/Manaus" and plant.tenant_id == tenants.b.id
    line = (await db.execute(select(Line).where(Line.plant_id == plant.id))).scalar_one()
    a1 = (await db.execute(select(Position).where(Position.name == "Cilindro A1",
                                                  Position.tenant_id == tenants.b.id))).scalar_one()
    pulse = (await db.execute(select(Tag).where(Tag.name == "A1 fim de curso", Tag.tenant_id == tenants.b.id))).scalar_one()
    assert a1.counter_tag_id == pulse.id and pulse.position_id == a1.id and pulse.address == "unit=2;di=0"
    assert pulse.semantic_role.value == "cycle_pulse" and pulse.protocol.value == "modbus_tcp"
    assert line.output_machine_id == a1.machine_id
    comps = (await db.execute(select(Component).where(Component.tenant_id == tenants.b.id,
                                                      Component.serial_number.in_(["SEED-A1", "SEED-SV"])))).scalars().all()
    assert {c.component_type.value for c in comps} == {"pneumatic_cylinder", "servo"}
    assert all(c.position_id is not None and c.installed_at is not None for c in comps)
    assert next(c for c in comps if c.serial_number == "SEED-A1").nominal_life == {"cycles": 8000000}
    n = (await db.execute(select(func.count()).select_from(Tag).where(Tag.tenant_id == tenants.b.id,
                                                                     Tag.machine_id == a1.machine_id))).scalar_one()
    assert n == 3



async def test_seed_demo_production_from_virtual_plant_map(db):
    """Fase 2 seed from the virtual plant map: 3 shifts with 1 h meal + 2 × 10 min coffee, products +
    PRI per (machine, product), bottleneck, fault → reason maps, open runs only for single-product machines.
    Idempotent."""
    import json
    from conftest import _make_tenant
    from src.db import AsyncSessionLocal
    from src.models import Line, Machine, MachineProduct, Product, ProductionRun, Shift
    from src.services.oee.calendar import span_minutes
    doc = json.loads(MAP.read_text(encoding="utf-8"))
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, "SEED3")
        await s.commit()
    async with AsyncSessionLocal() as s:
        await seed_dev.seed_demo_plant(s, t.id, doc)
    async with AsyncSessionLocal() as s:
        first = await seed_dev.seed_demo_production(s, t.id, doc)
    async with AsyncSessionLocal() as s:
        second = await seed_dev.seed_demo_production(s, t.id, doc)
    n_machines = sum(len(ln["machines"]) for ln in doc["assets"]["lines"])
    single = {m["name"] for ln in doc["assets"]["lines"] for m in ln["machines"]
              if len(seed_dev._machine_products(ln, m)) == 1}
    assert len(single) < n_machines                        # the virtual plant changes product (SET-01)
    assert first["shifts"] == 3 and first["products"] >= 2 and first["runs"] == len(single)
    assert second == {"shifts": 0, "products": 0, "machine_products": 0, "runs": 0}
    shifts = (await db.execute(select(Shift).where(Shift.tenant_id == t.id).order_by(Shift.start_local))).scalars().all()
    assert [(x.start_local, x.end_local) for x in shifts] == [("06:00", "14:00"), ("14:00", "22:00"), ("22:00", "06:00")]
    for x in shifts:
        assert set(x.weekdays) >= {0, 1, 2, 3, 4, 5}
        assert sorted(span_minutes(b["start_local"], b["end_local"]) for b in x.breaks) == [10, 10, 60]
    machines = {m.name: m for m in (await db.execute(select(Machine).where(Machine.tenant_id == t.id))).scalars()}
    prods = {p.id: p.code for p in (await db.execute(select(Product).where(Product.tenant_id == t.id))).scalars()}
    mps = (await db.execute(select(MachineProduct).where(MachineProduct.tenant_id == t.id))).scalars().all()
    default = {mp.machine_id: (prods[mp.product_id], mp.ideal_cycle_time_s) for mp in mps if mp.is_default}
    assert default[machines["Torno CNC"].id] == ("EIXO-20", 45)
    assert len(default) == n_machines                      # every machine has a default product + PRI
    assert machines["Torno CNC"].ideal_cycle_time_s == 45
    assert all(m.fault_code_map and all(v.startswith("EQP-") for v in m.fault_code_map.values())
               for m in machines.values())
    usinagem = (await db.execute(select(Line).where(Line.tenant_id == t.id, Line.name == "Linha de Usinagem"))).scalar_one()
    assert usinagem.bottleneck_machine_id == machines["Centro de Usinagem"].id      # 90 s > 45 s
    # open runs only where the product never changes; a machine whose PLC changes product (SET-01 →
    # product_code) gets none, so its OEE says "produto não informado" instead of using the wrong PRI
    runs = (await db.execute(select(ProductionRun).where(ProductionRun.tenant_id == t.id))).scalars().all()
    assert {r.machine_id for r in runs} == {machines[n].id for n in single} and all(r.ended_at is None for r in runs)
    assert machines["Centro de Usinagem"].id not in {r.machine_id for r in runs}
    one = {**doc, "assets": {**doc["assets"], "lines": [{**ln, "machines": [
        {**m, "products": m["products"][:1]} for m in ln["machines"]]} for ln in doc["assets"]["lines"]]}}
    from conftest import _make_tenant as _mk
    async with AsyncSessionLocal() as s:
        t1 = await _mk(s, "SEED3B")
        await s.commit()
    async with AsyncSessionLocal() as s:
        await seed_dev.seed_demo_plant(s, t1.id, one)
    async with AsyncSessionLocal() as s:
        assert (await seed_dev.seed_demo_production(s, t1.id, one))["runs"] == n_machines   # single product


async def test_seed_demo_production_fallback_without_map_data(db):
    """A map without shifts/products (older edge) → Mon–Sat shifts and one product per line."""
    from conftest import _make_tenant
    from src.db import AsyncSessionLocal
    from src.models import Shift
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, "SEED4")
        await s.commit()
    doc = {**DOC, "assets": {**DOC["assets"], "lines": [
        {**DOC["assets"]["lines"][0], "name": "Linha de Montagem",
         "machines": [{**DOC["assets"]["lines"][0]["machines"][0], "ideal_cycle_time_s": 40.0}]}]}}
    async with AsyncSessionLocal() as s:
        await seed_dev.seed_demo_plant(s, t.id, doc)
    async with AsyncSessionLocal() as s:
        st = await seed_dev.seed_demo_production(s, t.id, doc)
    assert st == {"shifts": 3, "products": 1, "machine_products": 1, "runs": 1}
    shifts = (await db.execute(select(Shift).where(Shift.tenant_id == t.id))).scalars().all()
    assert all(x.weekdays == [0, 1, 2, 3, 4, 5] for x in shifts)


async def test_seed_demo_maintenance(client, monkeypatch):
    """Sprint 4: planner / on-call technician / supervisor, plans on the Planta Manaus positions, historical
    WOs and failures through the state machine; drafts by the plan engine; idempotent; skipped in production."""
    import json
    import uuid
    from src.db import AsyncSessionLocal
    from src.models import FailureRecord, MaintenancePlan, Tenant, User, WorkOrder, WorkOrderHistory
    from src.services.maintenance import core
    if not MAP.exists():
        import pytest
        pytest.skip("virtual plant map not generated")
    sfx = uuid.uuid4().hex[:6]
    for var in ("PLANNER", "TECH", "SUPERVISOR"):
        monkeypatch.setattr(seed_dev, f"{var}_EMAIL", f"{var.lower()}-{sfx}@example.com")
    doc = json.loads(MAP.read_text(encoding="utf-8"))
    async with AsyncSessionLocal() as db:
        t = Tenant(name="Seed M", slug=f"seed-m-{sfx}")
        db.add(t)
        await db.commit()
        st = await seed_dev.seed_demo_plant(db, t.id, doc)
        out = await seed_dev.seed_demo_maintenance(db, t.id, st["plant_id"])
        assert out["users"] == 3 and out["plans"] == len(seed_dev.DEMO_PLANS) and out["work_orders"] == 6
        again = await seed_dev.seed_demo_maintenance(db, t.id, st["plant_id"])
        assert again == {"users": 0, "plans": 0, "work_orders": 0, "failures": 0, "failures_restored": 0}
        users = (await db.execute(select(User).where(User.tenant_id == t.id))).scalars().all()
        tech = next(u for u in users if u.role.value == "technician")
        assert tech.on_call and len(tech.line_ids) == 1
        plans = (await db.execute(select(MaintenancePlan).where(MaintenancePlan.tenant_id == t.id))).scalars().all()
        assert {p.state for p in plans} >= {"ok", "due_soon", "overdue"}
        wos = (await db.execute(select(WorkOrder).where(WorkOrder.tenant_id == t.id))).scalars().all()
        assert {w.state for w in wos} >= {"closed", "completed", "on_hold", "scheduled", "draft"}
        for p in plans:
            open_wos = [w for w in wos if w.plan_id == p.id and w.state in core.OPEN_STATES]
            assert len(open_wos) == (1 if (p.consumed or 0) >= 0.8 else 0), p.name
        hist = (await db.execute(select(func.count()).select_from(WorkOrderHistory).where(
            WorkOrderHistory.tenant_id == t.id))).scalar_one()
        assert hist >= 20
        assert (await db.execute(select(func.count()).select_from(FailureRecord).where(
            FailureRecord.tenant_id == t.id))).scalar_one() == 2
        # Sprint 7b: no seeded historical failure is left open (it showed as a permanent "falha há 40 d")
        assert (await db.execute(select(func.count()).select_from(FailureRecord).where(
            FailureRecord.tenant_id == t.id, FailureRecord.restored_at.is_(None)))).scalar_one() == 0
        # a database seeded by the old script (open incipient record) is repaired on the next seed run
        from datetime import datetime, timedelta, timezone
        old_f = FailureRecord(tenant_id=t.id, machine_id=wos[0].machine_id, severity="incipient",
                              failed_at=datetime.now(timezone.utc) - timedelta(days=40), status="complete",
                              note="tempo de curso subindo (seed)")
        manual = FailureRecord(tenant_id=t.id, machine_id=wos[0].machine_id, severity="critical",
                               failed_at=datetime.now(timezone.utc) - timedelta(days=40), status="draft",
                               note="registrada pelo cliente")
        db.add_all([old_f, manual])
        await db.commit()
        rep = await seed_dev.seed_demo_maintenance(db, t.id, st["plant_id"])
        assert rep["failures_restored"] == 1
        await db.refresh(old_f)
        await db.refresh(manual)
        assert old_f.restored_at is not None and manual.restored_at is None      # only the seed's own records
    monkeypatch.setenv("SEED_ENFORCE_PROD_CREDENTIALS", "1")
    async with AsyncSessionLocal() as db:
        assert (await seed_dev.seed_demo_maintenance(db, t.id, st["plant_id"]))["users"] == 0
