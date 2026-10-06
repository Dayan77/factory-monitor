"""docs/pilot.md — the before × after example (`pilot-before-after`) recomputed with the kpis.md §1 rules,
and the cross-references the plan relies on actually exist.

Run:  .venv/bin/python -m pytest docs/scenarios -q
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DOC = (ROOT / "docs" / "pilot.md").read_text(encoding="utf-8")


def block(tag: str):
    m = re.search(rf"```json {re.escape(tag)}\n(.*?)\n```", DOC, re.S)
    assert m, tag
    return json.loads(m.group(1))


def oee(p):
    apt = p["pbt_min"] - p["adot_min"]
    return {"availability": apt / p["pbt_min"], "performance": p["ideal_produced_min"] / apt,
            "quality": p["ideal_good_min"] / p["ideal_produced_min"], "oee": p["ideal_good_min"] / p["pbt_min"]}


def test_before_after_example():
    c = block("pilot-before-after")
    exp = c["expected"]
    b, a = oee(c["baseline"]), oee(c["after"])
    for k in ("availability", "performance", "quality", "oee"):
        assert math.isclose(b[k], exp["baseline"][k], abs_tol=1e-6), k
        assert math.isclose(a[k], exp["after"][k], abs_tol=1e-6), k
        # A × P × Q = OEE (consistency of the components)
    assert math.isclose(a["availability"] * a["performance"] * a["quality"], a["oee"], rel_tol=1e-9)
    # comparability rule §8.2: PBT within ±10 %
    assert abs(c["after"]["pbt_min"] / c["baseline"]["pbt_min"] - 1) <= 0.10
    gain_pp = (a["oee"] - b["oee"]) * 100
    assert math.isclose(gain_pp, exp["gain_pp"], abs_tol=1e-6)
    extra = (c["after"]["ideal_good_min"] - c["baseline"]["ideal_good_min"]) / (c["ideal_cycle_time_s"] / 60)
    assert math.isclose(extra, exp["extra_good_parts_per_week"], abs_tol=1e-6)
    red = 1 - c["after"]["top_loss"]["min"] / c["baseline"]["top_loss"]["min"]
    assert math.isclose(red, exp["top_loss_reduction"])
    assert math.isclose((c["manual_oee_before"] - b["oee"]) * 100, exp["measurement_gap_pp"], abs_tol=1e-6)
    # targets of the §8 table: +5 pp OEE, −30 % top loss
    assert exp["meets_oee_target"] is (gain_pp >= 5)
    assert exp["meets_top_loss_target"] is (red >= 0.30)
    # the prose quotes the same numbers
    assert "+6,15 pp" in DOC and "≈ 830 peças" in DOC and "−50 %" in DOC and "**0,659600**" in DOC and "**0,721110**" in DOC


def test_cross_references_exist():
    ops = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    for sec in ("## 4. Backup", "## 6. MQTT", "## 8. Observabilidade"):
        assert sec in ops, sec
    s6 = (ROOT / "docs" / "sprint6.md").read_text(encoding="utf-8")
    assert "### 4.1 CSV de tags" in s6 and "## 3. PWA do operador" in s6
    rep = (ROOT / "docs" / "reports.md").read_text(encoding="utf-8")
    assert "### 1.3 `weekly_oee`" in rep and "### 5.1 Quem recebe" in rep
    gw = (ROOT / "docs" / "gateways.md").read_text(encoding="utf-8")
    assert "uso único, 24 h" in gw and "8883" not in DOC.split("## 5.", 1)[0]
    # the plan never asks for inbound access to the factory
    sec5 = DOC.split("## 5. Checklist", 1)[1].split("## 6.", 1)[0]
    assert "conexões **de saída**" in sec5 and "Nenhuma porta aberta de fora para dentro" in sec5
