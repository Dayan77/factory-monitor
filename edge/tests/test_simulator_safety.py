"""The simulator and the config app must never target production by default."""

import json
from pathlib import Path

from edge.src import simulator

EDGE = Path(__file__).resolve().parents[1]


def test_simulator_defaults_to_local_stack(monkeypatch):
    for var in ("SIM_SERVER_URL", "SIM_BROKER_HOST", "SIM_BROKER_PORT"):
        monkeypatch.delenv(var, raising=False)
    args = simulator.build_parser().parse_args([])
    assert args.server == "http://localhost:8000"
    assert args.broker == "localhost"
    assert args.port == 1883


def test_simulator_explicit_target_still_possible(monkeypatch):
    monkeypatch.setenv("SIM_BROKER_HOST", "broker.lan")
    args = simulator.build_parser().parse_args(["--server", "http://10.0.0.5:8000"])
    assert args.server == "http://10.0.0.5:8000" and args.broker == "broker.lan"


def test_no_production_host_in_edge_defaults():
    import edge.app as app

    for cfg in (app.DEFAULT_CONFIG, json.loads((EDGE / "config.json").read_text())):
        assert "factoryconnect" not in json.dumps(cfg)
        assert cfg["server_url"].startswith("http://localhost")
        assert cfg["mqtt_host"] == "localhost"
