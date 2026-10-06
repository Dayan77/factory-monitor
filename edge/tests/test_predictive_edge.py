"""Lado edge da preditiva (docs/predictive.md P-1): tempo de curso = uma leitura por ciclo (nunca deduplicada),
RMS/pico por janela a partir das amostras, deadband por tag; e o perfil lento do Cilindro B3 na planta ao vivo."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from edge.src import gateway as gw_mod
from edge.src.plant_map import PlantMapError, parse_plant_map
from edge.virtual_plant.model import PlantConfig, PlantModel

TENANT = "11111111-1111-1111-1111-111111111111"
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def publish(self, topic, payload, qos=0):
        self.sent.append((topic, json.loads(payload)))


@pytest.fixture
async def gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(gw_mod.settings, "buffer_db_path", str(tmp_path / "buf.db"))
    monkeypatch.setattr(gw_mod.settings, "tenant_id", TENANT)
    monkeypatch.setattr(gw_mod.settings, "deadband_pct", 1.0)
    monkeypatch.setattr(gw_mod.settings, "encryption_key", "")
    g = gw_mod.EdgeGateway()
    await g.buffer.initialize()
    g.client = FakeClient()
    g.publisher._client, g.publisher.is_connected = g.client, True
    yield g
    await g.buffer.close()


def _doc(tags: list[dict]) -> dict:
    return {"version": 1, "gateway_id": "gw-t", "tenant_id": TENANT,
            "sources": [{"id": "mb", "protocol": "modbus", "host": "127.0.0.1", "port": 1502},
                        {"id": "opc", "protocol": "opcua", "endpoint": "opc.tcp://127.0.0.1:1/x/",
                         "timestamps": "source", "max_clock_skew_s": "off"}],
            "tags": tags}


def _tag(tid, role, source="mb", address=0, position="Cilindro B3", **kw):
    t = {"key": f"k.{tid}", "tag_id": tid, "source": source, "semantic_role": role, "machine": "Estação de Fixação",
         "position": position, "address": address if source == "mb" else f"ns=2;s={tid}"}
    t.update(kw)
    return t


def _values(g, tid):
    return [p["value"] for topic, p in g.client.sent if topic.endswith(f"/{tid}")]


async def test_modbus_stroke_published_once_per_counter_advance(gateway):
    pm = parse_plant_map(_doc([_tag("cnt", "cycle_counter", address=0, counter_max=65535),
                               _tag("st", "stroke_time", address=1, unit="ms")]))
    gateway.load_plant_map(pm)
    assert gateway._stroke == {"st": "cnt"}
    h = gateway._handle_reading
    await h("st", 200.0)                     # contador ainda desconhecido: espera
    await h("cnt", 10.0)
    await h("st", 200.0)                     # 1º golpe visto
    await h("st", 200.0)                     # mesmo ciclo (poll repetido): não é golpe novo
    await h("st", 201.0)                     # valor mudou, contador não: espera o contador
    await h("cnt", 11.0)
    await h("st", 201.0)                     # golpe 11
    await h("cnt", 12.0)
    await h("st", 201.0)                     # golpe 12 com o MESMO tempo de curso: publicado
    assert _values(gateway, "st") == [200.0, 201.0, 201.0]
    assert _values(gateway, "cnt") == [10.0, 11.0, 12.0]


async def test_opcua_stroke_every_notification_is_a_cycle_and_never_refreshed(gateway):
    pm = parse_plant_map(_doc([_tag("st", "stroke_time", source="opc", unit="ms"),
                               _tag("run", "run_state", source="opc", position=None)]))
    gateway.load_plant_map(pm)
    assert gateway._stroke == {"st": None} and "st" not in gateway._refresh_tags
    h = gateway._handle_reading
    await h("st", 205.0, T0, initial=True)
    await h("st", 205.0, T0 + timedelta(seconds=4))       # golpe com o mesmo valor
    await h("st", 205.0, T0 + timedelta(seconds=8))
    await h("st", 206.5, T0 + timedelta(seconds=12))
    sent = [(p["ts"], p["value"]) for t, p in gateway.client.sent if t.endswith("/st")]
    assert [v for _, v in sent] == [205.0, 205.0, 205.0, 206.5]
    assert [ts for ts, _ in sent] == ["2026-10-01T12:00:00.000Z", "2026-10-01T12:00:04.000Z",
                                      "2026-10-01T12:00:08.000Z", "2026-10-01T12:00:12.000Z"]
    await h("st", 206.5, T0 + timedelta(seconds=13), initial=True)   # re-assinatura: valor antigo, não é golpe
    assert len(_values(gateway, "st")) == 4


async def test_modbus_stroke_without_counter_only_on_change(gateway):
    pm = parse_plant_map(_doc([_tag("st", "stroke_time", address=1, position="Cilindro X")]))
    gateway.load_plant_map(pm)
    assert gateway._stroke == {}
    for v in (200.0, 200.0, 201.0):
        await gateway._handle_reading("st", v)
    assert _values(gateway, "st") == [200.0, 201.0]


async def test_window_aggregates_rms_and_peak(gateway):
    pm = parse_plant_map(_doc([
        _tag("vrms", "analog", source="opc", position="Motor B", unit="mm/s", aggregate={"window_s": 1, "stat": "rms"}),
        _tag("vpk", "analog", source="opc", position="Motor B", unit="mm/s", aggregate={"window_s": 1, "stat": "peak"}),
        _tag("irms", "current", source="opc", position="Motor B", unit="A",
             aggregate={"window_s": 10, "stat": "rms"}, deadband_pct=0)]))
    gateway.load_plant_map(pm)
    samples = [3.0, -4.0, 3.0, -4.0]                    # RMS = √((9+16+9+16)/4) = 3,5355; pico = 4
    for i, v in enumerate(samples):
        ts = T0 + timedelta(milliseconds=250 * i)
        await gateway._handle_reading("vrms", v, ts)
        await gateway._handle_reading("vpk", v, ts)
    assert _values(gateway, "vrms") == [] and _values(gateway, "vpk") == []   # janela ainda aberta
    await gateway._handle_reading("vrms", 1.0, T0 + timedelta(seconds=1))      # 1ª amostra da janela seguinte
    await gateway._handle_reading("vpk", 1.0, T0 + timedelta(seconds=1))
    (rms,) = [p for t, p in gateway.client.sent if t.endswith("/vrms")]
    assert rms["value"] == pytest.approx(3.535534, abs=1e-6) and rms["ts"] == "2026-10-01T12:00:01.000Z"
    assert _values(gateway, "vpk") == [4.0]
    # corrente RMS por janela de 10 s (senoide amostrada: RMS = pico / √2)
    import math
    for k in range(200):
        await gateway._handle_reading("irms", 17.0 * math.sin(2 * math.pi * k / 20), T0 + timedelta(milliseconds=50 * k))
    await gateway._handle_reading("irms", 0.0, T0 + timedelta(seconds=10))
    (irms,) = _values(gateway, "irms")
    assert irms == pytest.approx(17.0 / math.sqrt(2), rel=1e-6)
    assert await gateway.refresh_once() == 0             # agregados nunca são re-oferecidos


def test_aggregate_and_deadband_validation():
    with pytest.raises(PlantMapError, match="aggregate só vale"):
        parse_plant_map(_doc([_tag("c", "cycle_counter", aggregate={"window_s": 1, "stat": "rms"})]))
    with pytest.raises(PlantMapError, match="aggregate inválido"):
        parse_plant_map(_doc([_tag("v", "analog", aggregate={"window_s": 0, "stat": "rms"})]))
    with pytest.raises(PlantMapError, match="aggregate inválido"):
        parse_plant_map(_doc([_tag("v", "analog", aggregate={"window_s": 1, "stat": "kurtosis"})]))
    with pytest.raises(PlantMapError, match="deadband_pct"):
        parse_plant_map(_doc([_tag("v", "analog", deadband_pct=-1)]))


async def test_per_tag_deadband_overrides_global(gateway):
    pm = parse_plant_map(_doc([_tag("i", "current", address=4, position="Motor B", deadband_pct=0.1),
                               _tag("j", "current", address=5, position="Motor B")]))
    gateway.load_plant_map(pm)
    for v in (12.0, 12.02, 12.05):          # +0,17 % e +0,25 %: passam na banda de 0,1 %; não na global de 1 %
        await gateway._handle_reading("i", v)
        await gateway._handle_reading("j", v)
    assert _values(gateway, "i") == [12.0, 12.02, 12.05]
    assert _values(gateway, "j") == [12.0]


def test_live_plant_b3_slow_profile_until_failure():
    """Planta ao vivo: B3 saudável até o início (golpes), depois deriva linear; a falha 447 (EQP-03) acontece
    quando o curso nominal chega a 120 % do novo — a verdade registra início e falha. O B3 tem gerador próprio:
    os outros sinais da planta não mudam com ele."""
    cfg = PlantConfig(b3_onset_strokes=40, b3_drift_ms_per_kstroke=500.0, wear_repair="manual")
    m = PlantModel(seed=7, faults=True, config=cfg, shifts=None)
    b3 = next(c for c in m.cell.cylinders if c.name == "B3")
    item = m.wear.items["montagem.fixacao.b3"]
    assert item.fail_at == 40 + 40.0 / 0.5                            # 40 ms de deriva a 0,5 ms/golpe
    while b3.strokes_since_install < 40:
        m.run_for(30)
    deg = next(d for d in m.degradations() if d["position"] == "montagem.fixacao.b3")
    assert deg["state"] in ("healthy", "degrading") and deg["failure_s"] is None
    while not item.failed:
        m.run_for(30)
        assert m.sim_time_s < 6 * 3600
    deg = next(d for d in m.degradations() if d["position"] == "montagem.fixacao.b3")
    assert deg["state"] == "failed" and deg["onset_s"] is not None and deg["failure_s"] >= deg["onset_s"]
    assert m.cell.fix_fault.code == 447 and deg["nominal_ms"] >= 240.0
    assert m.values()["montagem.fixacao.b3.cycles"] == b3.plc_counter
    # mesmos sorteios da célula com e sem o B3 degradando (gerador próprio)
    a = PlantModel(seed=42, faults=False)
    b = PlantModel(seed=42, faults=False, config=PlantConfig(b3_drift_ms_per_kstroke=9.0))
    a.run_for(1800)
    b.run_for(1800)
    va, vb = a.values(include_analog=False), b.values(include_analog=False)
    assert {k: v for k, v in va.items() if "b3" not in k} == {k: v for k, v in vb.items() if "b3" not in k}


def test_live_plant_motor_b_current_by_product_is_deterministic():
    m1, m2 = PlantModel(seed=3, faults=False), PlantModel(seed=3, faults=False)
    m1.run_for(900)
    for _ in range(9):
        m2.run_for(100)
        m2.values()                                   # exportar mais vezes não muda o valor (contrato P-11.5)
    k = "montagem.transportador.motor_b.current"
    v1, v2 = m1.values()[k], m2.values()[k]
    assert v1 == v2
    if m1.cell.running:
        assert abs(v1 - m1.config.motor_b_current_a[m1.cell.setup.product.product]) < 0.5


def test_modbus_address_in_server_format_is_accepted():
    """Tag.address do servidor ("unit=2;hr=10:uint32") vira unit_id/register_type/endereço/data_type no mapa."""
    pm = parse_plant_map(_doc([_tag("a", "cycle_counter", address="unit=2;hr=10:uint32"),
                               _tag("b", "run_state", address="unit=3;di=4", position=None),
                               _tag("c", "analog", address="unit=1;ir=7", position=None, register_type="holding")]))
    a, b, c = pm.tags
    assert (a.unit_id, a.register_type, a.address, a.data_type_plc) == (2, "holding", 10, "uint32")
    assert (b.unit_id, b.register_type, b.address) == (3, "discrete", 4)
    assert (c.register_type, c.address) == ("holding", 7)                  # campo explícito prevalece
    with pytest.raises(PlantMapError, match="inteiro"):
        parse_plant_map(_doc([_tag("x", "analog", address="unit=1;zz=7")]))
