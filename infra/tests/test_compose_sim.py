"""Profile `sim` (planta virtual + gateway): playback de cenário de ouro ligado pelo compose.

    .venv/bin/python -m pytest -q infra/tests/test_compose_sim.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="precisa de docker compose")


def _config(**env) -> dict:
    out = subprocess.run(["docker", "compose", "--env-file", str(ROOT / ".env.example"), "-f",
                          str(ROOT / "infra/docker-compose.yml"), "--profile", "sim", "config", "--format", "json"],
                         capture_output=True, text=True, check=True, env={**os.environ, **env}).stdout
    return json.loads(out)["services"]


def test_scenario_playback_is_wired():
    svc = _config(VP_SCENARIO="/scenarios/scenario_c_oee_shift.json", VIRTUAL_PLANT_MAX_CLOCK_SKEW_S="off")
    vp, prov, gw = svc["virtual-plant"], svc["provision-sim"], svc["gateway-sim"]
    assert vp["environment"]["VP_SCENARIO"] == "/scenarios/scenario_c_oee_shift.json"
    for s in (vp, prov):
        mounts = {(v["source"], v["target"]) for v in s["volumes"] if v["type"] == "bind"}
        assert (str(ROOT / "docs/scenarios"), "/scenarios") in mounts
    assert gw["environment"]["VIRTUAL_PLANT_MAX_CLOCK_SKEW_S"] == "off"
    assert prov["environment"]["VP_SCENARIO"] == "/scenarios/scenario_c_oee_shift.json"
    assert (ROOT / "docs/scenarios/scenario_c_oee_shift.json").exists()


def test_default_sim_keeps_the_normal_plant_and_clock_check():
    svc = _config()
    assert svc["virtual-plant"]["environment"]["VP_SCENARIO"] == ""
    assert svc["gateway-sim"]["environment"]["VIRTUAL_PLANT_MAX_CLOCK_SKEW_S"] == "2"


@pytest.mark.parametrize("scenario", ["", "docs/scenarios/scenario_c_oee_shift.json"])
def test_provision_command_runs_the_right_steps(tmp_path, scenario):
    """Runs the provision-sim shell command with `python` stubbed: with a scenario it first writes the
    scenario map (calendar of the scenario) and provisions from it."""
    # `config --format json` keeps the compose escape "$$" (the container sees "$")
    cmd = _config(VP_SCENARIO=scenario)["provision-sim"]["command"][0].replace("$$", "$")
    calls = tmp_path / "calls"
    stub = tmp_path / "python"
    stub.write_text(f'#!/bin/sh\necho "$*" >> {calls}\n')
    stub.chmod(0o755)
    subprocess.run(["/bin/sh", "-c", cmd], check=True, env={**os.environ, "VP_SCENARIO": scenario,
                                                            "PATH": f"{tmp_path}:{os.environ['PATH']}"})
    lines = calls.read_text().splitlines()
    if scenario:
        assert lines[0] == f"-m virtual_plant --write-map /data/plant.scenario.json --scenario {scenario}"
        assert "--map /data/plant.scenario.json" in lines[1]
    else:
        assert len(lines) == 1 and lines[0].startswith("-m src.provision") and "--map" not in lines[0]


def test_scenario_map_can_be_written(tmp_path):
    edge_py = ROOT / ".venv-edge/bin/python"
    if not edge_py.exists():
        pytest.skip("precisa do venv do edge")
    out = tmp_path / "plant.scenario.json"
    subprocess.run([str(edge_py), "-m", "edge.virtual_plant", "--write-map", str(out), "--scenario",
                    "docs/scenarios/scenario_c_oee_shift.json"], cwd=ROOT, check=True, capture_output=True)
    doc = json.loads(out.read_text())
    assert doc["tags"] and "VIRTUAL_PLANT_MAX_CLOCK_SKEW_S" in out.read_text()
