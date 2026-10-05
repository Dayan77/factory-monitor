"""Carga e validação do mapa de dispositivos/tags do gateway."""

import copy
import json
import uuid
from pathlib import Path

import pytest

from edge.src.plant_map import PlantMapError, expand_env, load_plant_map, parse_plant_map

EDGE = Path(__file__).resolve().parents[1]
MAP = EDGE / "config" / "plant.json"


def provisioned_doc() -> dict:
    doc = json.loads(MAP.read_text(encoding="utf-8"))
    for t in doc["tags"]:
        t["tag_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, t["key"]))
    doc["tenant_id"] = "11111111-1111-1111-1111-111111111111"
    return doc


def test_committed_map_loads_with_env_defaults():
    pm = load_plant_map(MAP, env={})
    assert pm.gateway_id == "gw-planta-manaus-01"
    assert pm.pulse_poll_ms == 20 and pm.heartbeat_s == 10
    assert pm.sources["opcua-plc"].endpoint == "opc.tcp://localhost:4840/factoryconnect/virtual-plant/"
    assert pm.sources["modbus-plc"].host == "localhost" and pm.sources["modbus-plc"].port == 5020
    # sem provisionar: nenhuma tag publicável, todas listadas como ignoradas
    assert pm.tags == [] and len(pm.skipped) == len(json.loads(MAP.read_text())["tags"])


def test_env_override_for_docker():
    pm = load_plant_map(MAP, env={"VIRTUAL_PLANT_HOST": "virtual-plant", "VIRTUAL_PLANT_MODBUS_PORT": "1502"})
    assert pm.sources["opcua-plc"].endpoint.startswith("opc.tcp://virtual-plant:4840/")
    assert pm.sources["modbus-plc"].host == "virtual-plant" and pm.sources["modbus-plc"].port == 1502


def test_expand_env_requires_variable_without_default():
    assert expand_env({"a": ["${X:-1}", "${Y}"]}, {"Y": "2"}) == {"a": ["1", "2"]}
    with pytest.raises(PlantMapError):
        expand_env("${MISSING}", {})


def test_provisioned_map_roles_and_deadband():
    pm = parse_plant_map(provisioned_doc(), env={})
    assert not pm.skipped and pm.tenant_id
    by_key = {t.key: t for t in pm.tags}
    a1 = by_key["montagem.fixacao.a1.cycles"]
    assert (a1.unit_id, a1.register_type, a1.data_type_plc, a1.counter_max) == (2, "holding", "uint16", 65535)
    assert a1.bypass_deadband
    pulse = by_key["montagem.fixacao.a1.eos"]
    assert pulse.is_pulse and pulse.register_type == "discrete" and pulse.bypass_deadband
    for role_key in ("usinagem.torno.run_state", "usinagem.torno.fault_state", "usinagem.torno.parts_good",
                     "montagem.fixacao.a2.stroke_time", "montagem.robo.weld_cycles"):
        assert by_key[role_key].bypass_deadband, role_key
    assert not by_key["usinagem.torno.spindle.speed"].bypass_deadband
    assert not by_key["montagem.transportador.motor.current"].bypass_deadband
    assert len(pm.tags_for("modbus-plc", pulse=True)) == 1


@pytest.mark.parametrize("mutate, msg", [
    (lambda d: d["tags"][0].update(semantic_role="banana"), "semantic_role"),
    (lambda d: d["tags"][0].update(source="nope"), "source"),
    (lambda d: next(t for t in d["tags"] if t["source"] == "opcua-plc").update(semantic_role="cycle_pulse"),
     "cycle_pulse"),
    (lambda d: next(t for t in d["tags"] if t["semantic_role"] == "cycle_pulse").update(register_type="holding"),
     "cycle_pulse"),
    (lambda d: d["tags"][1].update(tag_id=d["tags"][0]["tag_id"]), "repetido"),
    (lambda d: d["tags"][1].update(key=d["tags"][0]["key"]), "duplicada"),
    (lambda d: d.update(version=2), "versão"),
    (lambda d: next(t for t in d["tags"] if t["source"] == "modbus-plc").update(address="40001"), "Modbus"),
    (lambda d: d["tags"][0].update(counter_max=-1), "counter_max"),
    (lambda d: d["sources"][0].update(timestamps="plc"), "timestamps"),
    (lambda d: d["sources"][1].update(timestamps="source"), "Modbus não traz timestamp"),
    (lambda d: d["sources"][0].update(max_clock_skew_s="-1"), "max_clock_skew_s"),
])
def test_invalid_maps_rejected(mutate, msg):
    doc = copy.deepcopy(provisioned_doc())
    mutate(doc)
    with pytest.raises(PlantMapError, match=msg):
        parse_plant_map(doc, env={})


def test_source_clock_policy_from_map_and_env():
    pm = parse_plant_map(provisioned_doc(), env={})
    opc, mb = pm.sources["opcua-plc"], pm.sources["modbus-plc"]
    assert (opc.timestamps, opc.max_clock_skew_s) == ("source", 2.0)
    assert (mb.timestamps, mb.max_clock_skew_s) == ("gateway", 2.0)
    pm = parse_plant_map(provisioned_doc(), env={"VIRTUAL_PLANT_TIMESTAMPS": "gateway",
                                                 "VIRTUAL_PLANT_MAX_CLOCK_SKEW_S": "5"})
    assert (pm.sources["opcua-plc"].timestamps, pm.sources["opcua-plc"].max_clock_skew_s) == ("gateway", 5.0)
    doc = provisioned_doc()
    for src in doc["sources"]:
        src.pop("timestamps", None)
        src.pop("max_clock_skew_s", None)
    pm = parse_plant_map(doc, env={})
    assert pm.sources["opcua-plc"].timestamps == "gateway"        # sem declarar: relógio não confiável
    # papéis do Sprint 3 aceitos e sem deadband
    by_key = {t.key: t for t in parse_plant_map(provisioned_doc(), env={}).tags}
    for k in ("usinagem.torno.product_code", "montagem.robo.starved", "montagem.fixacao.blocked"):
        assert by_key[k].bypass_deadband, k
