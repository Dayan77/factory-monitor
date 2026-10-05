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

