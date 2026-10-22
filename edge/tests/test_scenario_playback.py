"""Playback de cenário na planta virtual: linha do tempo fiel ao JSON, valores servidos (OPC UA/Modbus) nos
instantes do cenário e caminho real CLP → gateway (política de timestamp `source`) → MQTT."""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from asyncua import Client

from edge.src import gateway as gw_mod
from edge.src.plant_map import parse_plant_map
from edge.src.protocols.modbus import ModbusDriver, ModbusTagConfig
from edge.src.protocols.opcua import OpcUaDriver
from edge.tests.conftest import free_port
from edge.tests.test_plant_map import provisioned_doc
from edge.virtual_plant import __main__ as cli
from edge.virtual_plant.layout import addresses, fault_category
from edge.virtual_plant.runtime import VirtualPlantRuntime
from edge.virtual_plant.scenario import Scenario, ScenarioClock, ScenarioError, ScenarioModel

logging.getLogger("asyncua").setLevel(logging.ERROR)
REPO = Path(__file__).resolve().parents[2]
SCENARIO_C = REPO / "docs" / "scenarios" / "scenario_c_oee_shift.json"
DOC = json.loads(SCENARIO_C.read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
T0 = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _t(s: str) -> float:
    return (_ts(s) - T0).total_seconds()


@pytest.fixture(scope="module")
def sc() -> Scenario:
    return Scenario.load(SCENARIO_C)


# ── linha do tempo ────────────────────────────────────────────────────────────

def test_timeline_reproduces_every_signal_transition(sc):
    assert sc.machine_key == "usinagem.torno" and sc.duration_s == 8 * 3600
    run = [(w.t, w.value) for w in sc.writes if w.key == "usinagem.torno.run_state"]
    fault = [(w.t, w.value) for w in sc.writes if w.key == "usinagem.torno.fault_state"]
    exp_run, exp_fault, prev_r, prev_f = [], [], None, None
    for s in DOC["signals"]:
        if s["run_state"] != prev_r:
            exp_run.append((_t(s["ts"]), bool(s["run_state"])))
        if s["fault_state"] != prev_f:
            exp_fault.append((_t(s["ts"]), s["fault_state"]))
        prev_r, prev_f = s["run_state"], s["fault_state"]
    assert run == exp_run and len(run) == 32
    assert fault == exp_fault
    # cada falha do bloco events aparece com o código e a duração certos; o código mapeia no motivo esperado
    for e in DOC["events"]:
        if "fault_code" in e:
            a, b = _t(e["from_utc"]), _t(e["to_utc"])
            assert (a, e["fault_code"]) in fault and (b, 0) in fault
            if e.get("reason_code"):
                assert fault_category(e["fault_code"])["reason_code"] == e["reason_code"]


def test_parts_distributed_over_running_time(sc):
    intervals = sc.run_intervals()
    running_min = sum(b - a for a, b in intervals) / 60
    assert running_min == pytest.approx(DOC["expected"]["operating_min_run_state"])     # 349,5 min
    parts = sc.part_times()
    assert len(parts) == DOC["production"]["produced"] == 450
    assert sum(1 for _, s in parts if s) == DOC["production"]["scrap"] == 9
    assert all(any(a < t < b for a, b in intervals) for t, _ in parts)
    assert [t for t, _ in parts] == sorted(t for t, _ in parts)
    end = sc.values_at(sc.duration_s)
    assert end["usinagem.torno.parts_good"] == 441 and end["usinagem.torno.parts_scrap"] == 9
    assert end["usinagem.torno.product_code"] == 20                 # EIXO-20
    assert end["usinagem.torno.run_state"] is False                 # 18:00 fim do turno


def test_values_at_each_signal_timestamp(sc):
    for s in DOC["signals"]:
        t = _t(s["ts"])
        v = sc.values_at(t)
        assert v["usinagem.torno.run_state"] is bool(s["run_state"]), s["ts"]
        assert v["usinagem.torno.fault_state"] == s["fault_state"], s["ts"]
        assert v["usinagem.torno.spindle.run"] is bool(s["run_state"])
        if t > 0:
            before = sc.values_at(t - 0.5)
            assert before["usinagem.torno.run_state"] is not bool(s["run_state"]) or \
                before["usinagem.torno.fault_state"] != s["fault_state"]


def test_clock_shifts_whole_days_and_keeps_local_hour(sc):
    clock = ScenarioClock(sc, now=NOW)
    assert clock.shift_days == 2                           # fim 06/10 18:00Z → 04/10 18:00Z ≤ agora
    assert clock.device_time(0) == T0 - timedelta(days=2)
    assert clock.device_time(sc.duration_s) <= NOW
    assert clock.device_time(0).astimezone(timezone(timedelta(hours=-4))).hour == 6    # 06:00 em Manaus
    assert ScenarioClock(sc, shift_days=7, now=NOW).device_time(60) == T0 - timedelta(days=7) + timedelta(minutes=1)
    wall = ScenarioClock(sc, "wall", speed=60, now=NOW)
    assert wall.device_time(3600) == NOW + timedelta(minutes=1)
    with pytest.raises(ScenarioError):
        ScenarioClock(sc, "bogus")


def test_invalid_scenarios_are_rejected():
    bad = json.loads(json.dumps(DOC))
    bad["machine"]["name"] = "Máquina que não existe"
    with pytest.raises(ScenarioError, match="não existe"):
        Scenario(bad)
    bad = json.loads(json.dumps(DOC))
    bad["signals"][3], bad["signals"][4] = bad["signals"][4], bad["signals"][3]
    with pytest.raises(ScenarioError, match="ordem"):
        Scenario(bad)
    bad = json.loads(json.dumps(DOC))
    bad["production"]["good"] = 1
    with pytest.raises(ScenarioError, match="good"):
        Scenario(bad)


def test_cli_builds_scenario_model():
    args = cli.build_parser().parse_args(["--scenario", str(SCENARIO_C), "--speed", "60",
                                          "--scenario-shift-days", "3"])
    m = cli.make_model(args)
    assert isinstance(m, ScenarioModel) and m.clock.shift_days == 3
    m.run_for(8 * 3600)
    assert m.finished and m.values()["usinagem.torno.parts_good"] == 441


# ── valores servidos nos instantes do cenário ─────────────────────────────────

async def test_served_values_and_source_timestamps_at_scenario_timestamps(sc):
    """Tempo congelado e avançado à mão: em cada instante amostrado, o que o servidor OPC UA serve (valor e
    SourceTimestamp) e o que o Modbus serve = o que o cenário descreve."""
    clock = ScenarioClock(sc, now=NOW)
    model = ScenarioModel(sc, clock, heartbeat_s=0)
    rt = VirtualPlantRuntime(model, speed=1, opcua_port=free_port(), modbus_port=free_port(), status_port=None)
    a = {x.key: x for x in addresses()}
    probes = sorted({_t(s["ts"]) + d for s in DOC["signals"] for d in (0.0, 30.0)
                     if _t(s["ts"]) + d <= sc.duration_s} | {_t(e["from_utc"]) - 1 for e in DOC["events"]})
    async with rt:
        await rt.freeze()
        mb = ModbusDriver("127.0.0.1", rt.modbus_port, tags=[
            ModbusTagConfig("robo.run", a["montagem.robo.run_state"].register, "discrete", device_id=1),
            ModbusTagConfig("robo.product", a["montagem.robo.product_code"].register, "holding", device_id=1),
        ])
        assert await mb.connect()
        async with Client(url=rt.opcua_endpoint) as client:
            nodes = {k: client.get_node(f"ns=2;s=usinagem.torno.{k}")
                     for k in ("run_state", "fault_state", "parts_good", "parts_scrap", "product_code")}
            for t in probes:
                model.advance_to(t)
                await rt._push()
                exp = {**model.idle_values(), **sc.values_at(t)}
                for k, node in nodes.items():
                    dv = await node.read_data_value()
                    assert dv.Value.Value == exp[f"usinagem.torno.{k}"], (t, k)
                # SourceTimestamp do run_state = instante (deslocado) da última transição ≤ t
                last = max(w.t for w in sc.writes if w.key == "usinagem.torno.run_state" and w.t <= t)
                dv = await nodes["run_state"].read_data_value()
                assert dv.SourceTimestamp.replace(tzinfo=timezone.utc) == clock.device_time(last)
                vals = await mb.poll_once()
                assert vals["robo.run"] == 0.0 and vals["robo.product"] == 210    # demais máquinas paradas
        await mb.stop()


# ── caminho real: CLP virtual → gateway → MQTT ────────────────────────────────

class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def publish(self, topic, payload, qos=0):
        self.sent.append((topic, json.loads(payload)))


@pytest.fixture
async def gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(gw_mod.settings, "buffer_db_path", str(tmp_path / "buf.db"))
    monkeypatch.setattr(gw_mod.settings, "counter_db_path", str(tmp_path / "cnt.db"))
    monkeypatch.setattr(gw_mod.settings, "tenant_id", "")
    monkeypatch.setattr(gw_mod.settings, "gateway_id", "")
    monkeypatch.setattr(gw_mod.settings, "encryption_key", "")
    g = gw_mod.EdgeGateway()
    await g.buffer.initialize()
    g.client = FakeClient()
    g.publisher._client, g.publisher.is_connected = g.client, True
    yield g
    g._running = False
    for d in g.drivers:
        await d.stop()
    await g.buffer.close()
    if g.counter_store:
        g.counter_store.close()


async def test_full_path_playback_through_gateway_keeps_scenario_timestamps(sc, gateway):
    """Cenário C a 3600× (8 h em 8 s): o gateway publica cada transição de run_state/fault_state com o
    instante exato do cenário (deslocado em dias) e cada peça com o seu instante."""
    speed = 3600
    clock = ScenarioClock(sc, speed=speed)
    rt = VirtualPlantRuntime(ScenarioModel(sc, clock, heartbeat_s=0), speed=speed, opcua_port=free_port(),
                             modbus_port=free_port(), status_port=None, update_ms=50)
    pm = parse_plant_map(provisioned_doc(), env={
        "VIRTUAL_PLANT_HOST": "127.0.0.1", "VIRTUAL_PLANT_OPCUA_PORT": str(rt.opcua_port),
        "VIRTUAL_PLANT_MODBUS_PORT": str(rt.modbus_port), "VIRTUAL_PLANT_MAX_CLOCK_SKEW_S": "off"})
    key_of = {t.tag_id: t.key for t in pm.tags}
    gateway.load_plant_map(pm)
    assert gateway._clocks["opcua-plc"].max_skew_s is None
    for d in gateway.drivers:
        if hasattr(d, "_reconnect_delay"):
            d._reconnect_delay = 0.1
    await rt.start(paused=True)           # como a CLI: playback só depois que o gateway assina
    gateway._running = True
    tasks = [asyncio.create_task(d.run()) for d in gateway.drivers]
    try:
        n_opc = sum(1 for t in pm.tags if t.source == "opcua-plc")
        assert await rt.wait_for_subscribers(min_items=n_opc, settle_s=0.5, timeout=15)
        assert rt.model.sim_time_s == 0
        rt.resume()
        for _ in range(200):
            if rt.model.finished:
                break
            await asyncio.sleep(0.1)
        assert rt.model.finished
        await asyncio.sleep(1.0)
        assert await gateway.refresh_once() == 0     # relógio do cenário não é sintetizado (sem re-envio)
    finally:
        await rt.stop()
    gateway._running = False
    for d in gateway.drivers:
        await d.stop()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    pub: dict[str, list[tuple[datetime, float]]] = {}
    for topic, p in gateway.client.sent:
        if "/sensors/" in topic:
            pub.setdefault(key_of[topic.rsplit("/", 1)[1]], []).append((_ts(p["ts"]), p["value"]))

    shift = timedelta(days=clock.shift_days)
    exp_run = [(_ts(w_ts) - shift, float(r)) for w_ts, r in
               [(s["ts"], s["run_state"]) for s in DOC["signals"]]]
    # sem mudanças repetidas: run_state alterna a cada sinal no Cenário C
    assert pub["usinagem.torno.run_state"] == exp_run
    exp_fault, prev = [], None
    for s in DOC["signals"]:
        if s["fault_state"] != prev:
            exp_fault.append((_ts(s["ts"]) - shift, float(s["fault_state"])))
        prev = s["fault_state"]
    assert pub["usinagem.torno.fault_state"] == exp_fault
    good = pub["usinagem.torno.parts_good"]
    assert [v for _, v in good] == [float(i) for i in range(0, 442)]
    part_ts = [clock.device_time(t) for t, scrap in sc.part_times() if not scrap]
    assert [ts for ts, _ in good[1:]] == [t.replace(microsecond=t.microsecond // 1000 * 1000) for t in part_ts]
    assert pub["usinagem.torno.parts_scrap"][-1][1] == 9.0
    assert pub["usinagem.torno.product_code"][-1][1] == 20.0


def test_scenario_calendar_for_provisioning(sc, tmp_path):
    (shift,) = sc.shift_defs()
    assert (shift.name, shift.start_local, shift.end_local) == ("Turno A", "06:00", "14:00")
    assert [(b.start_local, b.end_local, b.reason_code) for b in shift.breaks] == [
        ("09:00", "09:10", "PLN-01"), ("11:00", "11:30", "PLN-01"), ("13:30", "13:40", "PLN-01")]
    out = tmp_path / "map.json"
    assert cli.main(["--write-map", str(out), "--scenario", str(SCENARIO_C)]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert [s["name"] for s in doc["assets"]["plant"]["shifts"]] == ["Turno A"]
    # PDOT do cenário = 50 min: o servidor provisionado com este mapa tem o mesmo PBT (430 min)
    mins = sum(int(b["end_local"][:2]) * 60 + int(b["end_local"][3:]) - int(b["start_local"][:2]) * 60
               - int(b["start_local"][3:]) for b in doc["assets"]["plant"]["shifts"][0]["breaks"])
    assert mins == DOC["expected"]["pdot_min"]


def test_model_heartbeat_rewrites_machine_tags_on_device_clock(sc):
    m = ScenarioModel(sc, ScenarioClock(sc, now=NOW), heartbeat_s=10)
    m.advance_to(2400 + 45)          # passa a micro-parada das 06:40:00–06:40:45
    ch = m.drain_changes()
    hb_run = [(t, v) for t, k, v in ch if k == "usinagem.torno.run_state"]
    # 1 batida a cada 10 s (+ as 2 transições), sempre com o valor vigente naquele instante
    assert len(hb_run) == 244 + 2
    assert (2400.0, False) in hb_run and (2410.0, False) in hb_run and (2445.0, True) in hb_run
    assert all(v == sc.values_at(t)["usinagem.torno.run_state"] for t, v in hb_run)
    keys = {k for _, k, _ in ch}
    assert "usinagem.torno.spindle.speed" not in {k for t, k, _ in ch if t % 10 == 0 and t not in (2400.0,)}
    assert {"usinagem.torno.fault_state", "usinagem.torno.parts_good", "usinagem.torno.product_code"} <= keys


async def test_device_heartbeat_keeps_long_stops_covered_through_gateway(sc, gateway):
    """Com heartbeat do dispositivo (relógio do cenário) e assinatura por timestamp, o gateway re-publica
    run_state/fault_state parados a cada ≤ 60 s de relógio do cenário (o servidor não vê "sem dados" na falha
    de 18 min) — e as transições continuam exatas."""
    speed = 1800             # 8 h em 16 s; mais rápido que isso a fila de 64 por item OPC UA pode transbordar
    clock = ScenarioClock(sc, speed=speed)
    model = ScenarioModel(sc, clock, heartbeat_s=30)
    rt = VirtualPlantRuntime(model, speed=speed, opcua_port=free_port(), modbus_port=free_port(),
                             status_port=None, update_ms=50)
    pm = parse_plant_map(provisioned_doc(), env={
        "VIRTUAL_PLANT_HOST": "127.0.0.1", "VIRTUAL_PLANT_OPCUA_PORT": str(rt.opcua_port),
        "VIRTUAL_PLANT_MODBUS_PORT": str(rt.modbus_port), "VIRTUAL_PLANT_MAX_CLOCK_SKEW_S": "off"})
    assert pm.sources["opcua-plc"].device_heartbeat
    key_of = {t.tag_id: t.key for t in pm.tags}
    gateway.load_plant_map(pm)
    gateway._running = True
    await rt.start(paused=True)
    tasks = [asyncio.create_task(d.run()) for d in gateway.drivers if isinstance(d, OpcUaDriver)]
    try:
        assert await rt.wait_for_subscribers(min_items=5, settle_s=0.5, timeout=15)
        rt.resume()
        for _ in range(400):
            if model.finished:
                break
            await asyncio.sleep(0.1)
        assert model.finished
        await asyncio.sleep(1.0)
    finally:
        await rt.stop()
        gateway._running = False
        for d in gateway.drivers:
            await d.stop()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    run = [(_ts(p["ts"]), p["value"]) for topic, p in gateway.client.sent
           if "/sensors/" in topic and key_of[topic.rsplit("/", 1)[1]] == "usinagem.torno.run_state"]
    stamps = [t for t, _ in run]
    assert stamps == sorted(stamps)
    gaps = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])]
    worst = max(range(len(gaps)), key=gaps.__getitem__)
    assert max(gaps) <= 60, (max(gaps), run[worst - 2:worst + 3])                                  # nunca "sem dados" (60 s)
    shift = timedelta(days=clock.shift_days)
    changes = [run[0]] + [x for p, x in zip(run, run[1:]) if x[1] != p[1]]
    assert changes == [(_ts(s["ts"]) - shift, float(s["run_state"])) for s in DOC["signals"]]
