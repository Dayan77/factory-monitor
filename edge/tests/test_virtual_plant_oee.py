"""Planta virtual — caminhos de OEE (Sprint 3): calendário, setup, perda de velocidade, micro-paradas, falhas
com categoria, starved/blocked, refugo e tag de produto. Tudo determinístico (seed + tempo simulado)."""

import pytest

from edge.virtual_plant.layout import (
    FAULT_CATEGORIES, FAULT_CODES, PLANTA_MANAUS, STANDARD_SHIFTS, Break, ShiftDef, addresses, address_string,
    build_plant_map, fault_category,
)
from edge.virtual_plant.model import TICK_S, PlantConfig, PlantModel, ShiftCalendar

H = 3600


def quiet(**kw) -> PlantConfig:
    """Sem micro-paradas e sem setup (isola um efeito por teste)."""
    micro = kw.pop("micro", 0)
    cfg = PlantConfig(setups=kw.pop("setups", False), **kw)
    return cfg.with_micro_rate(micro)


def sample(m: PlantModel, until_s: float, every_s: float = 5.0):
    while m.sim_time_s < until_s:
        m.run_for(every_s)
        yield m.values(include_analog=False)


# ── calendário ────────────────────────────────────────────────────────────────

def _hm(h, m=0):
    return h * H + m * 60


@pytest.mark.parametrize("plant_s,production,shift,in_break", [
    (_hm(6), True, "Turno A", False),
    (_hm(8, 29), True, "Turno A", False),
    (_hm(8, 30), False, "Turno A", True),       # café 08:30–08:40
    (_hm(8, 40), True, "Turno A", False),
    (_hm(11, 0), False, "Turno A", True),       # refeição 10:30–11:30
    (_hm(13, 59), True, "Turno A", False),
    (_hm(14), True, "Turno B", False),
    (_hm(18, 30), False, "Turno B", True),
    (_hm(22), True, "Turno C", False),
    (_hm(24 + 0, 35), False, "Turno C", True),  # café 00:30 do dia seguinte (turno começou ontem)
    (_hm(24 + 2, 30), False, "Turno C", True),  # refeição 02:00–03:00
    (_hm(24 + 5, 59), True, "Turno C", False),
    (_hm(24 + 6), True, "Turno A", False),
])
def test_standard_calendar(plant_s, production, shift, in_break):
    st = ShiftCalendar(STANDARD_SHIFTS).at(plant_s)
    assert (st.production, st.shift, st.in_break) == (production, shift, in_break)
    if in_break:
        assert st.break_reason == "PLN-01"


def test_calendar_weekdays_and_unscheduled_time():
    weekday_only = (ShiftDef("Adm", "08:00", "17:00", (0, 1, 2, 3, 4), (Break("12:00", "13:00"),)),)
    cal = ShiftCalendar(weekday_only, start_weekday=0)        # dia 0 = segunda
    assert cal.at(_hm(9)).production and not cal.at(_hm(7)).production
    assert cal.at(_hm(12, 30)).in_break
    assert not cal.at(5 * 86400 + _hm(9)).production           # sábado: sem turno
    with pytest.raises(ValueError):
        ShiftCalendar((ShiftDef("X", "06:00", "14:00", (0,), (Break("15:00", "15:10"),)),))


def test_each_shift_has_1h_meal_and_two_10min_coffees():
    for sh in STANDARD_SHIFTS:
        durs = sorted((int(b.end_local[:2]) * 60 + int(b.end_local[3:]) - int(b.start_local[:2]) * 60
                       - int(b.start_local[3:])) % 1440 for b in sh.breaks)
        assert durs == [10, 10, 60] and all(b.reason_code == "PLN-01" for b in sh.breaks)


def test_machines_stop_during_breaks_and_run_otherwise():
    m = PlantModel(seed=1, faults=False, config=quiet())
    for v in sample(m, 24 * H, 60):
        st = m.cal_state
        assert v["usinagem.torno.fault_state"] == 0
        if st.in_break:
            assert v["usinagem.torno.run_state"] is False and v["montagem.robo.run_state"] is False
            assert v["usinagem.centro.run_state"] is False
            assert not v["montagem.robo.starved"] and not v["montagem.robo.blocked"]
    # 24 h − 3 turnos × (60 + 10 + 10) min de pausa = 20 h rodando, sem falha/micro/setup
    on = m.snapshot()["truth"]["run_state"]["usinagem.torno.run_state"]["on_s"]
    assert on == pytest.approx(20 * H, abs=3 * TICK_S * 3)


# ── produto e setup ───────────────────────────────────────────────────────────

def test_setup_once_per_shift_changes_product_code():
    m = PlantModel(seed=4, faults=False, config=quiet(setups=True))
    codes = []
    for v in sample(m, 24 * H, 30):
        codes.append(v["usinagem.torno.product_code"])
    setups = m.snapshot()["truth"]["events"]["usinagem.torno"]["setups"]
    # uma troca por turno, 1 h após o início do turno (06:00 = tempo simulado 0)
    assert [s["start_s"] for s in setups] == [1 * H, 9 * H, 17 * H]
    assert all(s["duration_s"] == 900 and s["reason_code"] == "SET-01" for s in setups)
    assert [(s["from_product"], s["to_product"]) for s in setups] == [
        ("EIXO-20", "EIXO-32"), ("EIXO-32", "EIXO-20"), ("EIXO-20", "EIXO-32")]
    assert codes[0] == 20 and set(codes) == {20, 32}
    # centro: 75 min após o início; célula: 105 min
    ev = m.snapshot()["truth"]["events"]
    assert [s["start_s"] for s in ev["usinagem.centro"]["setups"]] == [4500, 4500 + 8 * H, 4500 + 16 * H]
    assert [s["start_s"] for s in ev["montagem.robo"]["setups"]] == [6300, 6300 + 8 * H, 6300 + 16 * H]


def test_machine_stopped_without_fault_during_setup():
    m = PlantModel(seed=4, faults=False, config=quiet(setups=True))
    m.run_for(H + 1)
    v = m.values()
    assert v["usinagem.torno.run_state"] is False and v["usinagem.torno.fault_state"] == 0
    assert v["usinagem.torno.product_code"] == 32          # receita muda no início do setup
    good = v["usinagem.torno.parts_good"]
    m.run_for(890)
    assert m.values()["usinagem.torno.parts_good"] == good
    m.run_for(20)
    assert m.values()["usinagem.torno.run_state"] is True


def test_dual_fixture_product_makes_two_parts_per_cycle():
    m = PlantModel(seed=4, faults=False, shifts=None, config=quiet())
    m.centro.setup.index = 1                                # FLG-B: 150 s/ciclo, 2 peças
    m.centro.cycle_len = m.centro._new_cycle_len()
    m.run_for(2 * H)
    v = m.values()
    total = v["usinagem.centro.parts_good"] + v["usinagem.centro.parts_scrap"]
    assert total % 2 == 0 and 2 * (2 * H) // (150 * 1.07) <= total <= 2 * (2 * H) // 150


# ── perda de velocidade ───────────────────────────────────────────────────────

@pytest.mark.parametrize("loss_pct", [0.0, 5.0, 15.0])
def test_speed_loss_is_configurable(loss_pct):
    m = PlantModel(seed=9, faults=False, shifts=None, config=quiet(speed_loss_pct=loss_pct))
    m.run_for(10 * H)
    v = m.values()
    parts = v["usinagem.torno.parts_good"] + v["usinagem.torno.parts_scrap"]
    avg_cycle = 10 * H / parts
    # ciclo real = 45 × (1 + loss + U(0, 2 %)) → média ≈ 45 × (1 + loss + 1 %)
    assert 45 * (1 + loss_pct / 100) <= avg_cycle <= 45 * (1 + loss_pct / 100 + 0.02) + 0.5


# ── micro-paradas ─────────────────────────────────────────────────────────────

def test_micro_stops_rate_and_duration_below_threshold():
    m = PlantModel(seed=11, faults=False, shifts=None, config=PlantConfig(setups=False).with_micro_rate(6))
    for v in sample(m, 10 * H, 5):
        if not v["usinagem.torno.run_state"]:
            assert v["usinagem.torno.fault_state"] == 0
    ev = m.snapshot()["truth"]["events"]["usinagem.torno"]["micro_stops"]
    on_h = m.snapshot()["truth"]["run_state"]["usinagem.torno.run_state"]["on_s"] / H
    assert all(15 <= e["duration_s"] <= 110 < 120 for e in ev)
    # Poisson(6/h × horas em operação): ±4σ
    lam = 6 * on_h
    assert abs(len(ev) - lam) <= 4 * lam ** 0.5
    # tempo parado = soma das micro-paradas
    stopped = 10 * H - on_h * H
    assert stopped == pytest.approx(sum(e["duration_s"] for e in ev), abs=len(ev) * TICK_S + 1)


# ── falhas, categorias e motivos ─────────────────────────────────────────────

def test_fault_code_ranges_map_to_categories_and_reasons():
    assert fault_category(101)["reason_code"] == "EQP-01"
    assert fault_category(204)["reason_code"] == "EQP-02"
    assert fault_category(305)["reason_code"] == "EQP-04"      # igual ao Cenário C
    assert fault_category(441)["reason_code"] == "EQP-03"
    assert fault_category(520)["reason_code"] == "EQP-05"
    with pytest.raises(ValueError):
        fault_category(42)
    for key, codes in FAULT_CODES.items():
        assert len({c // 100 for c in codes}) >= 3, f"{key}: códigos de poucas categorias"
    assert {v["reason_code"] for v in FAULT_CATEGORIES.values()} == {f"EQP-0{i}" for i in range(1, 6)}


def test_fault_events_carry_category_and_stop_machine():
    m = PlantModel(seed=42)
    m.run_for(24 * H)
    faults = m.snapshot()["truth"]["faults"]
    n = 0
    for key, evs in faults.items():
        for e in evs:
            n += 1
            assert e["code"] in FAULT_CODES[key]
            assert e["reason_code"] == fault_category(e["code"])["reason_code"]
            assert e["duration_s"] >= 30
    assert n >= 20
    cats = {e["category"] for evs in faults.values() for e in evs}
    assert len(cats) >= 4


def test_known_event_schedule_seed_42_first_shift():
    """Agenda de eventos de referência para o QA (mudar o modelo de propósito ⇒ atualizar e avisar o QA)."""
    m = PlantModel(seed=42)
    m.run_for(8 * H)
    snap = m.snapshot()
    tr = snap["truth"]
    assert [(e["code"], e["reason_code"]) for e in tr["faults"]["usinagem.torno"]] == [
        (102, "EQP-01"), (205, "EQP-02"), (305, "EQP-04"), (102, "EQP-01")]
    assert len(tr["events"]["usinagem.torno"]["micro_stops"]) == 13
    assert tr["events"]["usinagem.torno"]["setups"][0]["start_s"] == 3600.0
    v = snap["values"]
    assert (v["usinagem.torno.parts_good"], v["usinagem.torno.parts_scrap"]) == (391, 6)
    assert (v["montagem.transportador.parts_good"], v["montagem.transportador.parts_scrap"]) == (430, 9)


def test_event_schedule_independent_of_stepping():
    a = PlantModel(seed=99)
    a.run_for(9 * H)
    b = PlantModel(seed=99)
    while b.sim_time_s < 9 * H:
        b.advance_to(min(9 * H, b.sim_time_s + 777.7))
    assert a.snapshot()["truth"] == b.snapshot()["truth"]


# ── starved / blocked na célula ───────────────────────────────────────────────

def test_downstream_fault_blocks_upstream_and_upstream_fault_starves_downstream():
    m = PlantModel(seed=3, faults=False, config=quiet())
    m.run_for(60)
    cell = m.cell
    cell.conv_fault.code, cell.conv_fault.remaining_s = 251, 300.0     # transportador (jusante) em falha
    m.run_for(1)
    v = m.values()
    assert v["montagem.transportador.fault_state"] == 251
    assert v["montagem.fixacao.blocked"] and v["montagem.robo.blocked"]
    assert not v["montagem.transportador.starved"] and not v["montagem.transportador.blocked"]
    assert v["montagem.robo.fault_state"] == 0 and v["montagem.robo.run_state"] is False
    m.run_for(300)
    assert not any(v for k, v in m.values().items() if k.endswith((".starved", ".blocked")))

    cell.fix_fault.code, cell.fix_fault.remaining_s = 441, 200.0       # fixação (montante) em falha
    m.run_for(1)
    v = m.values()
    assert v["montagem.robo.starved"] and v["montagem.transportador.starved"]
    assert not v["montagem.fixacao.starved"] and not v["montagem.fixacao.blocked"]


def test_cell_starved_when_upstream_center_stops_longer_than_buffer():
    """Setup do centro (20 min) > buffer (15 min): a célula fica starved de 5400 s até o centro voltar
    (5700 s) e repor 60 s de buffer a 2 s/s (5730 s)."""
    m = PlantModel(seed=3, faults=False, config=quiet(setups=True))
    m.run_for(2 * H)
    ev = m.snapshot()["truth"]["events"]
    for k in ("montagem.fixacao", "montagem.robo", "montagem.transportador"):
        st = ev[k]["starved"]
        assert len(st) == 1
        assert st[0]["start_s"] == pytest.approx(5400, abs=0.2)
        assert st[0]["duration_s"] == pytest.approx(330, abs=0.3)
        assert ev[k]["blocked"] == []


def test_scrap_rate_per_machine():
    m = PlantModel(seed=21, faults=False, shifts=None, config=quiet())
    m.run_for(24 * H)
    v = m.values()
    for key, rate in (("usinagem.torno", 0.02), ("usinagem.centro", 0.015), ("montagem.transportador", 0.015)):
        good, scrap = v[f"{key}.parts_good"], v[f"{key}.parts_scrap"]
        n = good + scrap
        assert abs(scrap / n - rate) <= 4 * (rate * (1 - rate) / n) ** 0.5, key


# ── layout / mapa ─────────────────────────────────────────────────────────────

def test_every_machine_exposes_product_code_and_cell_flow_tags():
    keys = {a.key: a for a in addresses()}
    for line, mach in PLANTA_MANAUS.machines():
        assert f"{line.key}.{mach.key}.product_code" in keys
        assert any(mp.is_default for mp in mach.products)
    for k in ("montagem.fixacao", "montagem.robo", "montagem.transportador"):
        assert keys[f"{k}.starved"].register_type == "discrete"
        assert keys[f"{k}.blocked"].register_type == "discrete"
    # sinais novos acrescentados no fim: endereços antigos não mudam
    assert address_string(keys["montagem.fixacao.a3.cycles"]) == "unit=2;hr=5:uint16"
    assert address_string(keys["montagem.transportador.parts_good"]) == "unit=3;hr=2:uint32"
    assert address_string(keys["montagem.robo.product_code"]) == "unit=1;hr=15:uint16"
    m = PlantModel(seed=1)
    assert m.values()["usinagem.torno.product_code"] == 20


def test_plant_map_carries_shifts_products_and_fault_map():
    doc = build_plant_map()
    plant = doc["assets"]["plant"]
    assert [s["name"] for s in plant["shifts"]] == ["Turno A", "Turno B", "Turno C"]
    assert plant["shifts"][2]["start_local"] == "22:00" and plant["shifts"][2]["end_local"] == "06:00"
    assert all(len(s["breaks"]) == 3 and s["weekdays"] == [0, 1, 2, 3, 4, 5, 6] for s in plant["shifts"])
    assert {p["code"] for p in doc["assets"]["products"]} >= {"EIXO-20", "FLG-B", "SUP-100"}
    torno = doc["assets"]["lines"][0]["machines"][0]
    assert torno["products"][0] == {"product": "EIXO-20", "ideal_cycle_time_s": 45.0, "parts_per_cycle": 1,
                                    "is_default": True}
    assert torno["fault_code_map"]["305"] == "EQP-04"
    assert doc["product_codes"]["20"] == "EIXO-20"
    assert doc["fault_codes"]["usinagem.torno"]["101"]["reason_code"] == "EQP-01"
    # papéis de borda vão em signal_role; semantic_role só com valores que o servidor conhece
    roles = {t.get("signal_role") for t in doc["tags"]}
    assert {"product_code", "starved", "blocked"} <= roles
    assert all(t["semantic_role"] not in ("product_code", "starved", "blocked") for t in doc["tags"])
