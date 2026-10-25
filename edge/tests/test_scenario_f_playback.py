"""Cenário F (preditiva) na planta virtual: fidelidade do playback ao JSON (mediana por bloco do tempo de curso por
golpe, média ponderada da corrente só rodando, falha no instante certo), verdade de campo (GET /truth) e o caminho
real CLP (OPC UA, relógio do cenário) → gateway → MQTT com um tempo de curso por golpe."""

import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

import pytest
from aiohttp.test_utils import TestClient, TestServer

from edge.src import gateway as gw_mod
from edge.src.plant_map import parse_plant_map
from edge.src import provision as prov
from edge.tests.conftest import free_port
from edge.tests.test_provision import api_server  # noqa: F401 — fixture
from edge.virtual_plant import __main__ as cli
from edge.virtual_plant.control import PlantControl, build_app
from edge.virtual_plant.layout import build_plant_map, fault_category
from edge.virtual_plant.predictive import (
    OPCUA_MACHINES, PredictiveScenario, PredictiveScenarioModel, stroke_offset,
)
from edge.virtual_plant.runtime import VirtualPlantRuntime
from edge.virtual_plant.scenario import ScenarioClock, ScenarioError

REPO = Path(__file__).resolve().parents[2]
SCENARIO_F = REPO / "docs" / "scenarios" / "scenario_f_predictive.json"
DOC = json.loads(SCENARIO_F.read_text(encoding="utf-8"))
F1, F2 = DOC["cases"]["F1"], DOC["cases"]["F2"]
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
STROKE, COUNTER = "montagem.fixacao.b3.stroke_time", "montagem.fixacao.b3.cycles"
RUN, CUR = "montagem.transportador.motor_b.run", "montagem.transportador.motor_b.current"


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@pytest.fixture(scope="module")
def sc() -> PredictiveScenario:
    return PredictiveScenario.load(SCENARIO_F)


@pytest.fixture(scope="module")
def writes(sc):
    return list(sc.writes())


# ── F1: um tempo de curso por golpe, mediana do bloco = x_i ──────────────────

def test_f1_every_block_median_equals_scenario_series(sc, writes):
    """700 000 golpes: em cada bloco de 10 000 a mediana dos tempos de curso é exatamente o `x` do JSON (P-1),
    o bloco fecha quando o contador chega a i·10 000 e esse instante é installed_at + i · 12 h."""
    t_inst = (_ts(F1["installed_at"]) - sc.start).total_seconds()
    strokes = [(w.t, w.value) for w in writes if w.key == STROKE]
    counter = [(w.t, w.value) for w in writes if w.key == COUNTER]
    n_blocks, block = F1["generator"]["last_window"], F1["window"]["cycles"]
    assert len(strokes) == len(counter) == n_blocks * block == 700_000
    assert [v for _, v in counter] == list(range(1, 700_001))       # contador do CLP, sem pular
    assert all(a == b for (a, _), (b, _) in zip(strokes, counter))  # golpe e contador no mesmo instante
    expected = {w["i"]: w["x"] for w in F1["windows"]}
    for i in range(1, n_blocks + 1):
        vals = [v for _, v in strokes[(i - 1) * block:i * block]]
        assert median(vals) == expected[i], i
        assert counter[i * block - 1][1] == i * block
        assert counter[i * block - 1][0] == pytest.approx(t_inst + i * 12 * 3600)
    # ruído por golpe realista (desvio até 3 ms), não um valor constante por bloco
    b1 = [v for _, v in strokes[:block]]
    assert max(b1) - min(b1) == pytest.approx(6.0) and len(set(b1)) > 30
    # o pico do bloco 25 é o bloco inteiro (queda de pressão), não um golpe
    assert median(v for _, v in strokes[24 * block:25 * block]) == 212.0


def test_f1_baseline_and_failure_instants(sc, writes):
    """Fim do bloco 21 = established_at da linha de base; falha 447 (EQP-03, PDE) no fim do bloco 70 e o
    cilindro para."""
    exp = DOC["expected"]["F1"]["baseline"]
    end21 = sc.start + timedelta(seconds=next(w.t for w in writes if w.key == COUNTER and w.value == 21 * 10_000))
    assert end21 == _ts(exp["established_at"])
    fault = [(w.t, w.value) for w in writes if w.key == "montagem.fixacao.fault_state"]
    t_fail = (_ts(F1["failure"]["failed_at"]) - sc.start).total_seconds()
    assert fault == [((_ts(F1["installed_at"]) - sc.start).total_seconds(), 0), (t_fail, 447)]
    assert fault_category(447)["reason_code"] == "EQP-03"
    last_stroke = max(w.t for w in writes if w.key == STROKE)
    assert last_stroke == pytest.approx(t_fail)                     # nenhum golpe depois da falha
    run = [(w.t, w.value) for w in writes if w.key == "montagem.fixacao.run_state"]
    assert run[-1] == (t_fail, False)


def test_stroke_offsets_are_symmetric_with_exact_center():
    offs = [stroke_offset(j) for j in range(10_000)]
    assert sorted(offs) == sorted(-o for o in offs)
    assert offs.count(0.0) >= 10_000 // 31 and max(offs) == pytest.approx(3.0)


# ── F2: corrente média só rodando = x_i; feriado sem rodar ─────────────────

def _time_weighted_running_mean(writes, day_start: float, day_end: float):
    """Média ponderada no tempo (amostra-e-segura) da corrente nos intervalos com run_state = 1 (P-1)."""
    run = [(w.t, w.value) for w in writes if w.key == RUN]
    cur = [(w.t, w.value) for w in writes if w.key == CUR]

    def held(series, t):
        v = None
        for ts, x in series:
            if ts > t:
                break
            v = x
        return v

    pts = sorted({day_start, day_end, *(t for t, _ in run + cur if day_start < t < day_end)})
    on_s = acc = 0.0
    for a, b in zip(pts, pts[1:]):
        if held(run, a):
            on_s += b - a
            acc += (b - a) * held(cur, a)
    return (acc / on_s if on_s else None), on_s / 3600


def test_f2_daily_running_mean_and_holiday(sc, writes):
    tc = sc.time_cases[0]
    f2w = [w for w in writes if w.key in (RUN, CUR)]
    assert tc.day_start(1) == (_ts("2027-01-04T04:00:00Z") - sc.start).total_seconds()   # 00:00 local (UTC−4)
    for w in F2["windows"]:
        i = w["i"]
        mean, running_h = _time_weighted_running_mean(f2w, tc.day_start(i), tc.day_start(i + 1))
        assert running_h == pytest.approx(w["running_h"]), i
        if w["x"] is None:
            assert mean is None and i == 47                          # feriado: janela inválida
        else:
            assert mean == pytest.approx(w["x"], abs=1e-12), i
    # o motor roda 02:00–22:00 local; parado a corrente vai a 0 (o servidor ignora: run_state = 0)
    iv = tc.run_interval(1)
    assert ((iv[0] - tc.day_start(1)) / 3600, (iv[1] - tc.day_start(1)) / 3600) == (2.0, 22.0)


def test_f2_second_baseline_window_dates(sc):
    """Fim do dia 21 = 1ª linha de base (25/01 04:00Z) e fim do dia 82 = 2ª (27/03 04:00Z), como no expected."""
    tc = sc.time_cases[0]
    for b in DOC["expected"]["F2"]["baselines"]:
        assert sc.start + timedelta(seconds=tc.day_start(b["to_i"] + 1)) == _ts(b["established_at"])


# ── modelo (runtime): rajada limitada, heartbeat, relógio, verdade ─────────

def test_model_burst_cap_never_loses_writes(sc):
    """Com o runtime (max_ticks), no máximo 25 golpes por passo — a fila de 64 por item OPC UA não transborda —
    e o modelo continua de onde parou: a sequência entregue é a mesma do JSON."""
    m = PredictiveScenarioModel(PredictiveScenario.load(SCENARIO_F, cases=["F1"]),
                                ScenarioClock(sc, "shifted", 0, now=NOW), heartbeat_s=0)
    got = []
    target = 0.0
    while len(got) < 2 * 5_000:
        target += 3600.0                    # 1 h simulada por passo ≈ 833 golpes pedidos
        m.advance_to(target, max_ticks=5000)
        ch = m.drain_changes()
        assert sum(1 for _, k, _ in ch if k == STROKE) <= 25
        got.extend((t, k, v) for t, k, v in ch if k in (STROKE, COUNTER))
    ref = [(w.t, w.key, w.value) for w in PredictiveScenario.load(SCENARIO_F, cases=["F1"]).writes()
           if w.key in (STROKE, COUNTER)][:len(got)]
    assert got == ref
    assert m.sim_time_s < target                                     # atrasou, mas sem perder nada


def test_model_heartbeat_and_weekday_preserving_clock(sc):
    m = PredictiveScenarioModel(sc, ScenarioClock(sc, "shifted", sc.default_shift_days(NOW), now=NOW),
                                heartbeat_s=10)
    assert m.clock.shift_days % 7 == 0 and m.device_time(sc.duration_s) <= NOW
    d0 = m.device_time(0)
    assert d0.weekday() == sc.start.weekday() and (d0.hour, d0.minute) == (sc.start.hour, sc.start.minute)
    m.advance_to(4 * 86400)
    ch = m.drain_changes()
    run_hb = [t for t, k, _ in ch if k == RUN]
    gaps = [b - a for a, b in zip(run_hb, run_hb[1:])]
    assert max(gaps) <= 10.0 + 1e-9                                  # "estou vivo" a cada 10 s do cenário
    assert not any(k == STROKE and t % 10 == 0 and v is None for t, k, v in ch)


def test_truth_states_and_failure_times(sc):
    m = PredictiveScenarioModel(sc, ScenarioClock(sc, "shifted", 182, now=NOW), heartbeat_s=0)
    shift = timedelta(days=182)
    tr = m.truth()
    f1 = next(d for d in tr["degradations"] if d["case"] == "F1")
    f2 = next(d for d in tr["degradations"] if d["case"] == "F2")
    assert f1["state"] == "healthy" and f1["position"] == "montagem.fixacao.b3" and f1["failure_limit"] == 240.0
    assert _ts(f1["failure_at"]) == _ts(F1["failure"]["failed_at"]) - shift
    assert f1["scenario_failure_at"] == "2027-02-08T10:00:00+00:00"
    assert _ts(f1["onset_at"]) == _ts(F1["installed_at"]) + timedelta(hours=12 * 30) - shift
    assert f2["profile"] == "load_step_no_failure" and f2["failure_at"] is None and f2["invalid_days"] == [47]
    (fail,) = tr["failures"]
    assert (fail["fault_code"], fail["reason_code"], fail["failure_mode"], fail["occurred"]) == (447, "EQP-03", "PDE",
                                                                                                 False)
    assert tr["expected_evaluation"]["true_positives"] == 1
    # bloco 50 (dia 29/01 22:00Z): verdade = faltam 200 000 golpes (10 dias), como no rul_checkpoints
    m.advance_to((_ts(F1["installed_at"]) - sc.start).total_seconds() + 50 * 12 * 3600)
    f1 = next(d for d in m.truth()["degradations"] if d["case"] == "F1")
    assert f1["state"] == "degrading" and f1["now"]["true_rul_cycles"] == 200_000
    assert f1["now"]["true_rul_days"] == pytest.approx(10.0)
    assert f1["now"]["nominal"] == 220.0
    m.advance_to(sc.duration_s)
    tr = m.truth()
    assert next(d for d in tr["degradations"] if d["case"] == "F1")["state"] == "failed"
    assert tr["failures"][0]["occurred"] and m.finished


async def test_control_api_truth_endpoint_for_scenario_and_live_plant(sc):
    from edge.virtual_plant.model import PlantModel
    m = PredictiveScenarioModel(sc, ScenarioClock(sc, "shifted", 182, now=NOW), heartbeat_s=0)
    async with TestClient(TestServer(build_app(PlantControl(m)))) as client:
        r = await client.get("/truth")
        assert r.status == 200
        body = await r.json()
        assert body["kind"] == "scenario" and {d["case"] for d in body["degradations"]} == {"F1", "F2"}
        r = await client.get("/status")
        assert r.status == 200 and (await r.json())["scenario"]["kind"] == "predictive"
        r = await client.post("/repair", json={"machine": "montagem.fixacao"})
        assert r.status == 409                                       # playback: sem manutenção
    live = PlantModel(seed=42)
    live.run_for(600)
    async with TestClient(TestServer(build_app(PlantControl(live)))) as client:
        body = await (await client.get("/truth")).json()
        b3 = next(d for d in body["degradations"] if d["position"] == "montagem.fixacao.b3")
        assert b3["profile"] == "linear_after_onset" and b3["state"] == "healthy"
        assert b3["failure_usage"] == 110_000 and b3["failure_limit_ms"] == 240.0
        assert any(d["position"] == "montagem.transportador.motor_b" for d in body["degradations"])


def test_invalid_case_selection(sc):
    with pytest.raises(ScenarioError):
        PredictiveScenario.load(SCENARIO_F, cases=["F9"])


def test_write_map_serves_scenario_machines_on_opcua(tmp_path):
    out = tmp_path / "plant.f.json"
    assert cli.main(["--write-map", str(out), "--scenario", str(SCENARIO_F)]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    tags = {t["key"]: t for t in doc["tags"]}
    for k in (STROKE, COUNTER, RUN, CUR, "montagem.fixacao.fault_state", "montagem.transportador.motor_b.vibration"):
        assert tags[k]["source"] == "opcua-plc" and tags[k]["address"] == f"ns=2;s={k}"
    assert tags["montagem.fixacao.a1.eos"]["source"] == "modbus-plc"   # pulso continua em bit Modbus
    assert tags["montagem.robo.run_state"]["source"] == "modbus-plc"   # máquinas fora do cenário: inalteradas
    pm = parse_plant_map({**doc, "tags": [{**t, "tag_id": f"00000000-0000-0000-0000-{i:012d}"}
                                          for i, t in enumerate(doc["tags"])]}, env={})
    assert len(pm.tags) == len(doc["tags"])
    # o mapa padrão (planta ao vivo) não muda: B3/Motor B em Modbus, acrescentados no fim
    live = {t["key"]: t for t in build_plant_map()["tags"]}
    assert live[STROKE]["source"] == "modbus-plc" and live[COUNTER]["data_type_plc"] == "uint32"


async def test_write_map_installs_scenario_components_on_playback_clock(tmp_path, api_server):  # noqa: F811
    """A linha de base da preditiva começa na instalação do componente (P-2): o mapa do cenário F leva a data de
    instalação do Cilindro B3 / Motor B no relógio do playback, e o provisionamento instala com ela."""
    out = tmp_path / "plant.f.json"
    assert cli.main(["--write-map", str(out), "--scenario", str(SCENARIO_F), "--scenario-shift-days", "182"]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    comps = {(m["name"], p["name"]): p["component"] for ln in doc["assets"]["lines"] for m in ln["machines"]
             for p in m["positions"]}
    shift = timedelta(days=182)
    assert _ts(comps[("Estação de Fixação", "Cilindro B3")]["installed_at"]) == _ts(F1["installed_at"]) - shift
    assert _ts(comps[("Transportador de Saída", "Motor B")]["installed_at"]) == \
        _ts("2027-01-04T04:00:00Z") - shift                         # dia 1, 00:00 local
    assert "installed_at" not in comps[("Estação de Fixação", "Cilindro A1")]
    fake, url = await api_server()
    await prov.provision(url, "admin@x", "secret", doc, log=lambda *a, **k: None)
    b3 = next(c for c in fake.db["components"].values() if c["serial_number"] == "VP-FIX-B3-0001")
    assert _ts(b3["installed_at"]) == _ts(F1["installed_at"]) - shift
    a1 = next(c for c in fake.db["components"].values() if c["serial_number"] == "VP-FIX-A1-0001")
    assert a1["installed_at"] is None                                # sem data no mapa = agora (servidor)


# ── caminho real: planta virtual (OPC UA) → gateway → MQTT ─────────────────

def _mini_doc() -> dict:
    """F1 encolhido (blocos de 10 golpes, 6 blocos, golpe a cada 6 s) para o teste de ponta a ponta."""
    d = copy.deepcopy(DOC)
    f1 = d["cases"]["F1"]
    f1["window"]["cycles"] = 10
    g = f1["generator"]
    g.update(cycles_per_day=14_400, hours_per_window=10 * 6 / 3600, last_window=6, drift_after_window=3)
    f1["windows"] = [{"i": i, "x": x} for i, x in enumerate([201.0, 200.0, 200.0, 202.0, 204.0, 206.0], 1)]
    f1["failure"]["window"] = 6
    f1["failure"]["failed_at"] = (_ts(f1["installed_at"]) + timedelta(seconds=6 * 60)).isoformat()
    del d["cases"]["F2"]
    return d


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
    await g._stop_drivers()
    await g.buffer.close()
    if g.counter_store:
        g.counter_store.close()


async def test_playback_through_gateway_publishes_every_stroke_with_scenario_time(gateway):
    """Cada golpe vira UMA leitura de tempo de curso no MQTT (inclusive golpes com o mesmo valor do anterior),
    com o instante do golpe no relógio do cenário; a mediana por bloco publicada = x do cenário; a falha 447
    sai no fim do último bloco."""
    doc = _mini_doc()
    sc = PredictiveScenario(doc, cases=["F1"])
    speed = 120                                   # golpe a cada 6 s → 20 golpes/s reais
    clock = ScenarioClock(sc, "shifted", sc.default_shift_days(), speed=speed)
    model = PredictiveScenarioModel(sc, clock, heartbeat_s=0)
    rt = VirtualPlantRuntime(model, speed=speed, opcua_port=free_port(), modbus_port=free_port(),
                             status_port=None, update_ms=50)
    mdoc = build_plant_map(opcua_machines=OPCUA_MACHINES)
    for i, t in enumerate(mdoc["tags"]):
        t["tag_id"] = f"00000000-0000-0000-0000-{i:012d}"
    mdoc["tenant_id"] = "11111111-1111-1111-1111-111111111111"
    pm = parse_plant_map(mdoc, env={"VIRTUAL_PLANT_HOST": "127.0.0.1", "VIRTUAL_PLANT_OPCUA_PORT": str(rt.opcua_port),
                                    "VIRTUAL_PLANT_MODBUS_PORT": str(rt.modbus_port),
                                    "VIRTUAL_PLANT_MAX_CLOCK_SKEW_S": "off"})
    want = {STROKE, COUNTER, "montagem.fixacao.fault_state", "montagem.fixacao.run_state"}
    pm.tags = [t for t in pm.tags if t.key in want]
    key_of = {t.tag_id: t.key for t in pm.tags}
    gateway.load_plant_map(pm)
    assert set(gateway._stroke.values()) == {None}                  # OPC UA: uma notificação = um golpe
    await rt.start(paused=True)
    gateway._running = True
    gateway._start_drivers()
    try:
        assert await rt.wait_for_subscribers(min_items=len(want), settle_s=0.5, timeout=15)
        rt.resume()
        for _ in range(300):
            if model.finished:
                break
            await asyncio.sleep(0.1)
        assert model.finished
        await asyncio.sleep(1.0)
    finally:
        await rt.stop()
        gateway._running = False
        await gateway._stop_drivers()
    pub: dict[str, list[tuple[datetime, float]]] = {}
    for topic, p in gateway.client.sent:
        if "/sensors/" in topic:
            pub.setdefault(key_of[topic.rsplit("/", 1)[1]], []).append((_ts(p["ts"]), p["value"]))
    expected = [w for w in sc.writes() if w.key == STROKE]
    strokes = pub[STROKE]
    initial = 1 if len(strokes) == len(expected) + 1 else 0       # valor inicial do item ao assinar (0,0)
    assert len(strokes) - initial == len(expected) == 60
    assert [v for _, v in strokes[initial:]] == [w.value for w in expected]
    assert [t for t, _ in strokes[initial:]] == [clock.device_time(w.t) for w in expected]
    assert any(a[1] == b[1] for a, b in zip(strokes[initial:], strokes[initial + 1:]))   # repetidos publicados
    for i, x in enumerate([201.0, 200.0, 200.0, 202.0, 204.0, 206.0]):
        assert median(v for _, v in strokes[initial + 10 * i:initial + 10 * (i + 1)]) == x
    counts = [v for _, v in pub[COUNTER]]
    assert counts[-1] == 60.0 and counts == sorted(counts)
    fault = pub["montagem.fixacao.fault_state"]
    assert fault[-1] == (clock.device_time(sc.cycle_cases[0].block_end(6)), 447.0)
