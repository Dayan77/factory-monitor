"""Planta virtual — desgaste e falhas por uso (edge/virtual_plant/wear.py). Sem servidores."""

import math
import random

import pytest

from edge.virtual_plant.layout import WEAR_FAULT_CODES, fault_category
from edge.virtual_plant.model import PlantConfig, PlantModel
from edge.virtual_plant.wear import weibull_life

H = 3600


def quiet(**kw) -> PlantConfig:
    """Sem setups nem micro-paradas: só o desgaste mexe na produção."""
    return PlantConfig(setups=False, **kw).with_micro_rate(0)


def plant(seed=42, **kw) -> PlantModel:
    """24 h sem calendário, sem falhas aleatórias, com falhas por desgaste."""
    cfg = quiet(**{k: kw.pop(k) for k in list(kw) if k in ("wear_repair", "wear_failures")})
    m = PlantModel(seed=seed, faults=True, shifts=None, config=cfg, **kw)
    for fp in (m.torno.fault, m.centro.fault, *m.cell.faults):   # só desgaste: sem falhas aleatórias
        fp._run_left = math.inf
    return m


def only(m: PlantModel, *ids: str) -> PlantModel:
    """Só estes itens falham (manual: uma ferramenta quebrada pararia o centro → célula sem peça)."""
    for k, it in m.wear.items.items():
        if k not in ids:
            it.fail_at = math.inf
    return m


def run_until(m: PlantModel, cond, step_s=10, limit_s=48 * H):
    end = m.sim_time_s + limit_s
    while not cond():
        assert m.sim_time_s < end, "condição não aconteceu no limite"
        m.run_for(step_s)


def test_weibull_life_conditional_on_age():
    rng = random.Random(1)
    lives = [weibull_life(rng, 1000.0, 2.0, age=900.0) for _ in range(500)]
    assert min(lives) > 900.0                           # sobreviveu até a idade atual
    assert weibull_life(random.Random(5), 10.0, 3.0) == weibull_life(random.Random(5), 10.0, 3.0)


def test_lives_are_deterministic_per_seed_and_component():
    a, b, c = PlantModel(seed=7), PlantModel(seed=7), PlantModel(seed=8)
    la = {k: it.life for k, it in a.wear.items.items()}
    assert la == {k: it.life for k, it in b.wear.items.items()}
    assert la != {k: it.life for k, it in c.wear.items.items()}
    # a vida de um componente não depende dos outros nem da configuração da planta
    d = PlantModel(seed=7, shifts=None, config=quiet())
    assert la == {k: it.life for k, it in d.wear.items.items()}


def test_wear_status_identical_regardless_of_stepping():
    a = plant(seed=3)
    a.run_for(20 * H)
    b = plant(seed=3)
    for _ in range(7):
        b.advance_to(b.sim_time_s + 2777.7)
    b.advance_to(20 * H)
    assert a.snapshot()["truth"]["wear"] == b.snapshot()["truth"]["wear"]
    assert a.snapshot()["truth"]["faults"] == b.snapshot()["truth"]["faults"]


def test_seal_stroke_time_drifts_and_leaks_when_remaining_life_runs_out():
    m = only(plant(seed=42, wear_repair="manual"), "montagem.fixacao.a2")
    a2 = m.wear.items["montagem.fixacao.a2"]
    cyl = next(c for c in m.cell.cylinders if c.name == "A2")
    first = cyl.nominal_stroke_ms()
    predicted = a2.usage + a2.remaining                 # oráculo: ciclos em que a vedação vaza
    run_until(m, lambda: a2.failed, step_s=5)
    assert cyl.nominal_stroke_ms() > first + 10         # tempo de curso subiu com os ciclos
    ind = m.wear.stroke_indicators(a2.id)
    assert ind["drift_ms"] >= ind["leak_threshold_ms"] - 0.05
    f = a2.failures[0]
    assert f["code"] == 444 and fault_category(444)["reason_code"] == "EQP-03"
    assert predicted <= f["usage"] <= predicted + 2     # falha exatamente quando o uso chega à vida
    v = m.values(include_analog=False)
    assert v["montagem.fixacao.fault_state"] == 444
    m.run_for(1)
    v = m.values(include_analog=False)
    assert v["montagem.fixacao.run_state"] is False and v["montagem.robo.run_state"] is False
    # manual: continua parada até alguém agir
    m.run_for(6 * H)
    assert m.values(include_analog=False)["montagem.fixacao.fault_state"] == 444
    ev = m.snapshot()["truth"]["faults"]["montagem.fixacao"][-1]
    assert ev["source"] == "wear" and ev["item"] == a2.id and ev["duration_s"] is None


def test_auto_repair_replaces_component_after_drawn_duration():
    m = plant(seed=42)                                  # padrão: plantão troca automaticamente
    a2 = m.wear.items["montagem.fixacao.a2"]
    run_until(m, lambda: a2.failed, step_s=5)
    start = a2.failures[0]["start_s"]
    run_until(m, lambda: not a2.failed, step_s=5)
    f = a2.failures[0]
    assert f["action"] == "auto_replace" and 25 * 60 - 5 <= f["end_s"] - start <= 50 * 60 + 5
    assert a2.install_no == 1 and a2.usage < 1000
    assert a2.installs[0]["reason"] == "failure" and a2.installs[0]["usage_at_removal"] >= f["usage"]
    cyl = next(c for c in m.cell.cylinders if c.name == "A2")
    assert cyl.nominal_stroke_ms() < cyl.base_ms + 1    # vedação nova: curso volta ao nominal
    ev = m.snapshot()["truth"]["faults"]["montagem.fixacao"][-1]
    assert ev["duration_s"] >= 25 * 60 - 1 and ev["code"] == 444
    m.run_for(H)
    assert m.values(include_analog=False)["montagem.fixacao.fault_state"] == 0


def test_failure_times_derive_from_usage_mcbf_reproducible():
    """Mesma seed ⇒ mesmas falhas no mesmo uso; a vida restante prevista é o oráculo do servidor (MCBF)."""
    def failures(seed):
        m = plant(seed=seed)
        m.run_for(40 * H)
        return [(k, f["usage"], f["start_s"]) for k, it in m.wear.items.items() for f in it.failures]
    a = failures(11)
    assert a and a == failures(11)
    assert any(k.endswith(".tool") for k, *_ in a)       # ferramentas falham várias vezes em 40 h
    m = plant(seed=11)
    tool = m.wear.items["usinagem.torno.tool"]
    life = tool.life
    run_until(m, lambda: tool.failures, step_s=5)
    assert tool.failures[0]["usage"] == pytest.approx(math.ceil(life), abs=1)


def test_tool_wear_raises_spindle_current_and_fails_with_5xx():
    m = plant(seed=5)
    tool = m.wear.items["usinagem.torno.tool"]
    run_until(m, lambda: tool.wear_frac > 0.8 and m.torno.cutting, step_s=1)
    worn = sum(m._analog()["usinagem.torno.spindle.current"] for _ in range(50)) / 50
    run_until(m, lambda: tool.failures and not tool.failed and m.torno.cutting, step_s=1)
    new = sum(m._analog()["usinagem.torno.spindle.current"] for _ in range(50)) / 50
    assert worn > new * 1.1
    assert tool.failures[0]["code"] == 523 and 523 in WEAR_FAULT_CODES["usinagem.torno"]


def test_bearing_current_and_vibration_rise_with_hours_then_trip_2xx():
    m = only(plant(seed=2, wear_repair="manual"), "montagem.transportador.motor")
    mot = m.wear.items["montagem.transportador.motor"]
    # motor novo com vida curta para o teste: 2 h de operação
    m.wear.replace(mot.id)
    mot.life = mot.fail_at = 2.0
    run_until(m, lambda: m.cell.running, step_s=1)
    def sample():
        vals = [m._analog() for _ in range(40)]
        return (sum(v["montagem.transportador.motor.current"] for v in vals) / 40,
                sum(v["montagem.transportador.motor.vibration"] for v in vals) / 40)
    i0, v0 = sample()
    hours0 = mot.usage
    run_until(m, lambda: mot.usage > hours0 + 1.8 and m.cell.running, step_s=1)
    i1, v1 = sample()
    assert i1 > i0 * 1.2 and v1 > v0 + 3
    run_until(m, lambda: mot.failed, step_s=1)
    assert m.values(include_analog=False)["montagem.transportador.fault_state"] == 252
    assert mot.status()["starts_since_install"] >= 1
    assert fault_category(252)["category"] == "electrical"


def test_robot_axis_fails_by_cycles_with_1xx_code():
    m = only(plant(seed=4, wear_repair="manual"), "montagem.robo.axis1")
    ax = m.wear.items["montagem.robo.axis1"]
    assert m.wear.status()[ax.id]["indicators"]["population_failure_probability"] > 0
    ax.life = ax.fail_at = ax.usage + 14 * 30           # 30 ciclos da célula
    run_until(m, lambda: ax.failed, step_s=1, limit_s=4 * H)
    assert m.values(include_analog=False)["montagem.robo.fault_state"] == 132
    assert ax.failures[0]["usage"] >= ax.life


def test_replace_resets_wear_and_optionally_plc_counter():
    m = plant(seed=42)
    m.run_for(H)
    a3 = next(c for c in m.cell.cylinders if c.name == "A3")
    before = a3.plc_counter
    out = m.wear.replace("montagem.fixacao.a3")
    assert out["removed"]["usage_at_removal"] > 2_000_000 and not out["repaired_failure"]
    assert a3.plc_counter == before and m.wear.items["montagem.fixacao.a3"].usage == 0
    m.wear.replace("Estação de Fixação / Cilindro A3", reset_counter=True)
    assert a3.plc_counter == 0 and m.values()["montagem.fixacao.a3.cycles"] == 0
    m.wear.replace("montagem.robo.axis2", reset_counter=True)
    assert m.values()["montagem.robo.axis2.cycles"] == 0
    assert m.values()["montagem.robo.axis1.cycles"] > 0
    with pytest.raises(KeyError):
        m.wear.replace("montagem.fixacao.a9")


def test_replace_of_failed_component_ends_failure():
    m = only(plant(seed=42, wear_repair="manual"), "montagem.fixacao.a2")
    a2 = m.wear.items["montagem.fixacao.a2"]
    run_until(m, lambda: a2.failed, step_s=5)
    out = m.wear.replace(a2.id)
    assert out["repaired_failure"] and out["removed"]["reason"] == "failure"
    assert m.values(include_analog=False)["montagem.fixacao.fault_state"] == 0
    assert a2.failures[0]["action"] == "replace"
    ev = m.snapshot()["truth"]["faults"]["montagem.fixacao"][-1]
    assert ev["duration_s"] is not None and ev["duration_s"] >= 0
    m.run_for(600)
    assert m.cell.running or m.cell.stop_reason != "fault"


def test_palliative_repair_fails_again_soon():
    m = only(plant(seed=42, wear_repair="manual"), "montagem.fixacao.a2")
    a2 = m.wear.items["montagem.fixacao.a2"]
    run_until(m, lambda: a2.failed, step_s=5)
    out = m.wear.repair("montagem.fixacao")
    assert out["cleared_code"] == 444 and out["wear_item"]["action"] == "palliative_repair"
    assert m.values(include_analog=False)["montagem.fixacao.fault_state"] == 0
    run_until(m, lambda: a2.failed, step_s=5, limit_s=8 * H)
    assert len(a2.failures) == 2 and a2.install_no == 0
    assert a2.failures[1]["usage"] - a2.failures[0]["usage"] < 1000


def test_forced_machine_failure_until_repair_without_consuming_random_draws():
    b = PlantModel(seed=42)
    b.run_for(H)
    state = b.rng.getstate()
    ev = b.inject_fault("montagem.robo", 331, source="forced")
    assert b.rng.getstate() == state                    # injetar não consome sorteios da planta
    assert ev["duration_s"] is None and b.values(include_analog=False)["montagem.robo.fault_state"] == 331
    b.run_for(600)
    assert b.values(include_analog=False)["montagem.robo.fault_state"] == 331
    assert b.cell.running is False
    assert b.clear_fault("montagem.robo") == 331
    assert ev["duration_s"] == 600.0
    b.run_for(60)
    assert b.values(include_analog=False)["montagem.robo.fault_state"] == 0


def test_no_faults_disables_wear_failures_but_keeps_drift():
    m = PlantModel(seed=42, faults=False, shifts=None, config=quiet())
    m.run_for(30 * H)
    assert all(not it.failures for it in m.wear.items.values())
    a2 = m.wear.items["montagem.fixacao.a2"]
    assert a2.usage > a2.life                           # passou da vida, mas sem falha (desligada)


def test_wear_truth_in_snapshot():
    m = PlantModel(seed=42)
    m.run_for(600)
    w = m.snapshot()["truth"]["wear"]
    assert set(w) >= {"montagem.fixacao.a1", "montagem.robo.axis6", "montagem.transportador.motor",
                      "usinagem.centro.coolant", "usinagem.torno.tool", "usinagem.centro.tool"}
    a2 = w["montagem.fixacao.a2"]
    assert a2["mode"] == "seal_leak" and a2["measure"] == "cycles" and a2["failure_code"] == 444
    assert a2["remaining"] == a2["fails_at_usage"] - a2["usage_since_install"]
    assert a2["reason_code"] == "EQP-03" and a2["indicators"]["leak_threshold_ms"] > 0
    assert w["montagem.transportador.motor"]["measure"] == "operating_hours"


def test_wear_codes_are_in_layout_and_plant_map():
    m = PlantModel(seed=1)
    from edge.virtual_plant.layout import build_plant_map
    doc = build_plant_map()
    fcm = {f"{ln['name']}/{mm['name']}": mm["fault_code_map"] for ln in doc["assets"]["lines"] for mm in ln["machines"]}
    for it in m.wear.items.values():
        assert it.code in WEAR_FAULT_CODES[it.machine], it.id
        assert str(it.code) in doc["fault_codes"][it.machine]
        name = m.machine_name(it.machine)
        assert any(k.endswith("/" + name) and fcm[k][str(it.code)] == fault_category(it.code)["reason_code"]
                   for k in fcm)
