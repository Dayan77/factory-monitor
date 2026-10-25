"""Roteiro da demonstração pública (docs/sprint6.md §5, edge/virtual_plant/demo.py).

Turnos 3 × 8 h todos os dias no fuso America/Sao_Paulo, falhas algumas vezes por turno, um setup por turno,
micro-paradas, desgaste do Cilindro B3 até o alerta preditivo e reinício do desgaste com a troca (/replace).
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import aiohttp
import pytest

from edge.tests.conftest import free_port
from edge.virtual_plant.__main__ import build_parser, make_model
from edge.virtual_plant.control import PlantControl
from edge.virtual_plant.demo import (B3_ITEM, DemoHealth, DemoSettings, _b3, clock_drift_s, local_start,
                                     make_demo_model, restore_state, save_state)
from edge.virtual_plant.model import PlantConfig, PlantModel
from edge.virtual_plant.runtime import VirtualPlantRuntime

logging.getLogger("asyncua").setLevel(logging.ERROR)
# quarta-feira 07/10/2026 06:00 em São Paulo (UTC−3, sem horário de verão)
T0 = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)


def test_demo_clock_is_sao_paulo_wall_time():
    assert local_start("America/Sao_Paulo", T0) == (6 * 3600, 2, T0)
    m = make_demo_model(42, DemoSettings(), now=T0)
    assert m.cal_state.shift == "Turno A"                      # 06:00 local → turno A
    assert abs(clock_drift_s(m, T0)) < 1
    m.run_for(3600)
    assert abs(clock_drift_s(m, T0 + timedelta(hours=1))) < 1
    assert clock_drift_s(m, T0 + timedelta(hours=1, minutes=10)) == pytest.approx(-600, abs=1)
    # virada do dia e todos os dias da semana (turnos em ALL_DAYS)
    m.run_for(20 * 3600)                                       # quinta 03:00 local
    assert abs(clock_drift_s(m, T0 + timedelta(hours=21))) < 1
    assert m.cal_state.shift == "Turno C"


def test_demo_settings_from_env_and_validation():
    d = DemoSettings.from_env({"DEMO_FAULT_MTBF_SCALE": "3", "DEMO_B3_STROKES_PER_CYCLE": "8"})
    assert (d.fault_mtbf_scale, d.b3_strokes_per_cycle, d.tz) == (3.0, 8, "America/Sao_Paulo")
    with pytest.raises(ValueError):
        DemoSettings.from_env({"DEMO_B3_STROKES_PER_CYCLE": "0"})
    with pytest.raises(Exception):
        DemoSettings.from_env({"DEMO_TZ": "Lua/Base_Alfa"})


def test_demo_flag_is_opt_in():
    assert build_parser().parse_args([]).demo is False      # simulador nunca é a demo por padrão
    args = build_parser().parse_args(["--demo"])
    assert getattr(make_model(args), "demo", None) is not None


def test_default_plant_unchanged_by_demo_knobs():
    """b3_strokes_per_cycle=1 e fault_mtbf_scale=1 (padrões) = mesmo modelo de antes (determinismo)."""
    a = PlantModel(seed=7)
    b = PlantModel(seed=7, config=PlantConfig(b3_strokes_per_cycle=1, fault_mtbf_scale=1.0))
    a.run_for(4 * 3600)
    b.run_for(4 * 3600)
    assert a.snapshot() == b.snapshot()
    assert _b3(a).strokes_at == (0.5,)


def test_one_day_of_demo_has_faults_setups_and_micro_stops_every_shift():
    m = make_demo_model(42, DemoSettings(), now=T0)
    m.run_for(24 * 3600)
    snap = m.snapshot()["truth"]
    starts = {"Turno A": 0, "Turno B": 8 * 3600, "Turno C": 16 * 3600}   # tempo simulado (partida às 06:00)
    faults = [e for evs in snap["faults"].values() for e in evs if (e.get("duration_s") or 0) >= 120]
    for name, a in starts.items():
        b = a + 8 * 3600
        n = sum(1 for e in faults if a <= e["start_s"] < b)
        assert 1 <= n <= 8, (name, n)                                    # "algumas vezes por turno"
        for key, ev in snap["events"].items():
            setups = [e for e in ev["setups"] if a <= e["start_s"] < b]
            assert len(setups) == 1, (name, key, setups)                 # um setup por turno
        micro = sum(1 for ev in snap["events"].values() for e in ev["micro_stops"] if a <= e["start_s"] < b)
        assert micro >= 3, (name, micro)
    assert all(e["code"] for e in faults)                                # falha sempre com código


def test_b3_strokes_many_times_per_cycle_and_wears_until_replaced():
    ds = DemoSettings(b3_onset_strokes=2000, b3_drift_ms_per_kstroke=10.0)
    m = make_demo_model(42, ds, now=T0)
    c = _b3(m)
    assert len(c.strokes_at) == 12
    cycles0 = m.cell.cycles_done
    m.run_for(2 * 3600)
    assert c.strokes >= 11 * (m.cell.cycles_done - cycles0)
    while c.nominal_stroke_ms() < 222 and m.sim_time_s < 48 * 3600:  # deriva > 110 % da base (alerta)
        m.run_for(600)
    assert c.stroke_ms > 215 and not m.wear.items[B3_ITEM].failed
    # demo-bot do servidor: POST /replace {"position": "montagem.fixacao.b3"} (ref = chave da tag sem o sinal)
    out = PlantControl(m).replace({"position": "montagem.fixacao.b3", "reset_counter": False,
                                   "reason": "preventive_replacement"})
    assert out["item"] == B3_ITEM and out["installed"]["install_no"] == 1
    assert c.strokes_since_install == 0 and c.stroke_ms == pytest.approx(200.0, abs=0.1)
    counter = c.plc_counter
    m.run_for(3600)
    assert c.plc_counter > counter                                       # contador do CLP não zera na troca
    assert c.nominal_stroke_ms() == pytest.approx(200.0)                 # saudável de novo até o onset


def test_wear_state_survives_restart(tmp_path):
    ds = DemoSettings(b3_onset_strokes=2000, b3_drift_ms_per_kstroke=10.0)
    m = make_demo_model(42, ds, now=T0)
    m.run_for(6 * 3600)
    c = _b3(m)
    assert c.degraded_strokes() > 0
    path = tmp_path / "state.json"
    saved = save_state(path, m)
    assert json.loads(path.read_text())["b3"]["strokes_since_install"] == c.strokes_since_install
    m2 = make_demo_model(42, ds, now=T0 + timedelta(hours=6))
    assert restore_state(path, m2) == saved
    c2 = _b3(m2)
    assert (c2.strokes_since_install, c2.plc_counter) == (c.strokes_since_install, c.plc_counter)
    assert c2.stroke_ms == pytest.approx(c.nominal_stroke_ms(), abs=0.06)
    assert m2.wear.items[B3_ITEM].fail_at == m.wear.items[B3_ITEM].fail_at
    # arquivo ausente ou corrompido: cilindro novo, sem quebrar a partida
    assert restore_state(tmp_path / "nada.json", make_demo_model(42, ds, now=T0)) is None
    (tmp_path / "ruim.json").write_text("{")
    assert restore_state(tmp_path / "ruim.json", make_demo_model(42, ds, now=T0)) is None


def test_health_reports_stall_and_clock_drift():
    m = make_demo_model(42, DemoSettings(), now=T0)
    t = [0.0]
    h = DemoHealth(m, DemoSettings(), clock=lambda: t[0])
    ok, body = h.check(0.0, now=T0)
    assert ok and body["mode"] == "demo" and body["shift"] == "Turno A"
    t[0] = 40.0                                                          # tempo simulado parado há 40 s
    ok, body = h.check(0.0, now=T0)
    assert not ok and "tempo simulado parado" in body["problems"][0]
    m.run_for(1)
    ok, body = h.check(0.0, now=T0 + timedelta(hours=1))                 # relógio 1 h atrás (fuso/DST)
    assert not ok and any("fora da hora" in p for p in body["problems"]) and h.must_restart(T0 + timedelta(hours=1))
    assert not h.must_restart(T0 + timedelta(seconds=1))


async def test_demo_runtime_health_endpoint_and_control_api():
    m = make_demo_model(42, DemoSettings())
    h = DemoHealth(m, DemoSettings())
    status, control = free_port(), free_port()
    rt = VirtualPlantRuntime(m, speed=1, opcua_port=free_port(), modbus_port=free_port(), status_port=status,
                             update_ms=50, control_port=control, control_token="tok", health_fn=h.check)
    async with rt, aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{status}/health") as r:
            body = await r.json()
            assert r.status == 200 and body["ok"] is True and body["mode"] == "demo"
            assert abs(body["clock_drift_s"]) < 5
        async with s.post(f"http://127.0.0.1:{control}/replace",
                          json={"position": "montagem.fixacao.b3", "reason": "preventive_replacement"}) as r:
            assert r.status == 200 and (await r.json())["item"] == B3_ITEM
        await rt.freeze()                                                # laço parado → 503
        async with s.get(f"http://127.0.0.1:{status}/health") as r:
            assert r.status == 503 and "laço de simulação parado" in (await r.json())["problems"]
