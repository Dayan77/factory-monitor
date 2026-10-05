"""Planta virtual — modelo determinístico (sem servidores)."""

import json
from pathlib import Path

import pytest

from edge.virtual_plant.layout import PLANTA_MANAUS, addresses, build_plant_map
from edge.virtual_plant.model import TICK_S, U16, PlantModel, in_shift, parse_shifts

EDGE = Path(__file__).resolve().parents[1]


def test_same_seed_same_numbers_regardless_of_stepping():
    a = PlantModel(seed=7)
    a.run_for(4 * 3600)
    b = PlantModel(seed=7)
    for _ in range(4):       # outro "relógio real": blocos diferentes, mesmo tempo simulado
        b.advance_to(b.sim_time_s + 1234.5)
    b.advance_to(4 * 3600)
    assert a.sim_time_s == b.sim_time_s == 4 * 3600
    assert a.snapshot() == b.snapshot()


def test_different_seed_different_numbers():
    a, b = PlantModel(seed=1), PlantModel(seed=2)
    a.run_for(3600)
    b.run_for(3600)
    assert a.snapshot()["values"] != b.snapshot()["values"]


def test_known_numbers_seed_42_one_hour():
    """Números de referência para o QA (mudar o modelo de propósito ⇒ atualizar aqui e avisar o QA)."""
    m = PlantModel(seed=42)
    m.run_for(3600)
    v = m.snapshot()["values"]
    assert v["usinagem.torno.parts_good"] == 72 and v["usinagem.torno.parts_scrap"] == 3
    assert v["usinagem.centro.parts_good"] == 34 and v["usinagem.centro.parts_scrap"] == 2
    assert v["montagem.transportador.parts_good"] == 85
    assert v["montagem.fixacao.a1.cycles"] == 12_086
    assert m.pulse_targets()["montagem.fixacao.a1.eos"] == 86


def test_cylinder_16bit_counter_rolls_over_at_65535():
    m = PlantModel(seed=42, faults=False)
    a3 = next(c for c in m.cell.cylinders if c.name == "A3")
    start = a3.plc_counter
    seen_top = False
    prev = start
    while a3.rollovers == 0:
        m.run_for(10)
        if a3.plc_counter >= 65_530:
            seen_top = True
        assert m.sim_time_s < 6 * 3600, "A3 deveria rolar em poucas horas simuladas"
        if a3.rollovers == 0:
            assert a3.plc_counter >= prev
        prev = a3.plc_counter
    assert seen_top
    assert a3.plc_counter < 1000                       # voltou para perto de 0
    assert a3.plc_counter == (start + a3.strokes) % U16  # nenhum golpe perdido na virada
    assert m.values()["montagem.fixacao.a3.cycles"] == a3.plc_counter
    assert max(c.plc_counter for c in m.cell.cylinders) <= 65_535


def test_stroke_time_increases_with_wear():
    m = PlantModel(seed=3, faults=False)
    a2 = next(c for c in m.cell.cylinders if c.name == "A2")
    first = a2.nominal_stroke_ms()
    m.run_for(10 * 3600)
    assert a2.nominal_stroke_ms() > first + 15         # degradação acelerada visível em horas
    others = [c for c in m.cell.cylinders if c.name != "A2"]
    assert all(c.nominal_stroke_ms() < 260 for c in others)


def test_faults_have_code_duration_and_stop_the_machine():
    m = PlantModel(seed=42)
    saw_fault = False
    for _ in range(int(8 * 3600 / 5)):
        m.run_for(5)
        v = m.values(include_analog=False)
        if v["usinagem.torno.fault_state"]:
            saw_fault = True
            assert v["usinagem.torno.run_state"] is False
            assert v["usinagem.torno.spindle.run"] is False
    assert saw_fault
    events = m.snapshot()["truth"]["faults"]
    assert events["usinagem.torno"], "torno deveria falhar em 8 h (MTBF 2 h)"
    for key, evs in events.items():
        for e in evs:
            assert e["duration_s"] >= 30
            assert e["code"] in __import__("edge.virtual_plant.layout", fromlist=["FAULT_CODES"]).FAULT_CODES[key]


def test_no_faults_flag():
    m = PlantModel(seed=42, faults=False)
    m.run_for(6 * 3600)
    assert all(not evs for evs in m.snapshot()["truth"]["faults"].values())


def test_shift_pattern_stops_production_outside_shift():
    assert parse_shifts("06:00-14:00,22:00-06:00") == [(21600, 50400), (79200, 21600)]
    shifts = parse_shifts("06:00-14:00")
    assert in_shift(shifts, 6 * 3600) and not in_shift(shifts, 14 * 3600) and not in_shift(shifts, 3 * 3600)
    assert in_shift(parse_shifts("22:00-06:00"), 2 * 3600)

    m = PlantModel(seed=5, faults=False, shifts="06:00-14:00", start_second_of_day=6 * 3600)
    m.run_for(8 * 3600)
    good_at_end_of_shift = m.values()["usinagem.torno.parts_good"]
    m.run_for(10)
    assert m.values()["usinagem.torno.run_state"] is False
    m.run_for(4 * 3600)
    assert m.values()["usinagem.torno.parts_good"] == good_at_end_of_shift
    tr = m.trackers["usinagem.torno.run_state"]
    assert tr.on_s == pytest.approx(8 * 3600, abs=TICK_S * 2)


def test_truth_counts_operating_time_and_starts():
    m = PlantModel(seed=42)
    m.run_for(3600)
    truth = m.snapshot()["truth"]["run_state"]
    sp = truth["usinagem.torno.spindle.run"]
    # eixo-árvore liga uma vez por peça (fase de corte)
    v = m.values()
    parts = v["usinagem.torno.parts_good"] + v["usinagem.torno.parts_scrap"]
    assert parts <= sp["starts"] <= parts + 2
    assert 0 < sp["on_s"] < 3600


def test_model_and_layout_agree():
    m = PlantModel(seed=1)
    keys = {a.key for a in addresses(PLANTA_MANAUS)}
    assert set(m.values()) == keys
    # Modbus: endereços únicos por unit/área
    seen = set()
    for a in addresses(PLANTA_MANAUS):
        if a.protocol == "modbus":
            width = 2 if a.data_type == "uint32" else 1
            for r in range(a.register, a.register + width):
                slot = (a.unit_id, a.register_type, r)
                assert slot not in seen
                seen.add(slot)


def test_committed_plant_map_matches_layout():
    """edge/config/plant.json é gerado do layout: regenere com --write-map se este teste falhar."""
    committed = json.loads((EDGE / "config" / "plant.json").read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(build_plant_map(), ensure_ascii=False))


def test_plant_map_covers_required_roles():
    doc = build_plant_map()
    roles = {t["semantic_role"] for t in doc["tags"]}
    assert {"cycle_counter", "cycle_pulse", "run_state", "fault_state", "part_counter_good",
            "part_counter_scrap", "stroke_time", "speed", "current"} <= roles
    cyl = [t for t in doc["tags"] if t["key"].startswith("montagem.fixacao.a") and t["key"].endswith(".cycles")]
    assert len(cyl) == 4 and all(t["counter_max"] == 65_535 and t["data_type_plc"] == "uint16" for t in cyl)
    pulse = [t for t in doc["tags"] if t["semantic_role"] == "cycle_pulse"]
    assert len(pulse) == 1 and pulse[0]["register_type"] == "discrete" and pulse[0]["counter_max"] is None
