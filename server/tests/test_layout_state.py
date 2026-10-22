"""Sprint 6 §1.2 — visual state precedence of the plant map: the server function against the spec block
`s6-status-precedence` (7 rules, every MachineStatus mapped, fault during the break → failure, kpis S5-1)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

DOC = (Path(__file__).resolve().parents[2] / "docs" / "sprint6.md").read_text(encoding="utf-8")


def block(tag: str):
    m = re.search(rf"```json {re.escape(tag)}\n(.*?)\n```", DOC, re.S)
    assert m, tag
    return json.loads(m.group(1))


PREC = block("s6-status-precedence")


def test_order_and_status_map_match_the_spec():
    from src.models import MachineStatus
    from src.services.layout.state import MACHINE_STATUS_MAP, ORDER, visual_state
    assert list(ORDER) == PREC["order"]
    assert MACHINE_STATUS_MAP == PREC["machine_status_map"]
    assert {s.value for s in MachineStatus} == set(PREC["machine_status_map"])
    for st, vis in PREC["machine_status_map"].items():
        assert visual_state({"status": st}) == vis
        assert visual_state({"status": MachineStatus(st)}) == vis           # enum member accepted too


@pytest.mark.parametrize("case", PREC["cases"], ids=lambda c: c["id"])
def test_precedence_cases(case):
    from src.services.layout.state import visual_state
    assert visual_state(case["facts"]) == case["expected"]


def test_each_rule_beats_the_next_one():
    """All 7 rules: with every condition true, removing them one by one walks the order."""
    from src.services.layout.state import ORDER, visual_state
    facts = {"status": "unknown", "stale": True, "open_failure": True, "wo_in_progress_stops_machine": True,
             "in_break": True, "planned_stop": True, "open_pln": True, "off_shift": True, "open_downtime": True}
    steps = [
        ("offline", lambda f: f.update(status="running", stale=False)),
        ("failure", lambda f: f.update(open_failure=False)),
        ("maintenance", lambda f: f.update(wo_in_progress_stops_machine=False)),
        ("planned_stop", lambda f: f.update(in_break=False, planned_stop=False, open_pln=False)),
        ("off_shift", lambda f: f.update(off_shift=False)),
        ("stopped", lambda f: f.update(open_downtime=False)),
        ("running", lambda f: None),
    ]
    assert [s for s, _ in steps] == list(ORDER)
    for expected, drop in steps:
        assert visual_state(facts) == expected, facts
        drop(facts)
    assert visual_state({"status": "error"}) == "failure"
    assert visual_state({"status": "maintenance", "in_break": True}) == "maintenance"
    assert visual_state({"status": "stopped"}) == "stopped"
    assert visual_state({"status": "running", "stale": True}) == "offline"     # no reading > timeout


def test_describe_never_colour_only():
    from src.services.layout.state import ICONS, ORDER, describe
    now = datetime.now(timezone.utc)
    samples = {
        "offline": {"status": "unknown", "last_seen_at": now - timedelta(minutes=12)},
        "failure": {"status": "error", "open_failure": True, "failure_since": now, "failure_mode": "PDE"},
        "maintenance": {"status": "running", "wo_in_progress_stops_machine": True, "wo_label": "OS-123"},
        "planned_stop": {"status": "stopped", "in_break": True, "break_label": "refeição",
                         "break_until_local": "12:30", "break_until": now},
        "off_shift": {"status": "stopped", "off_shift": True},
        "stopped": {"status": "stopped", "open_downtime": True, "downtime_reason": "UNK-01", "downtime_since": now},
        "running": {"status": "running"},
    }
    texts = {}
    for st in ORDER:
        d = describe(samples[st])
        assert d["state"] == st and d["icon"] == ICONS[st] and d["text"]
        texts[st] = d["text"]
    assert texts["planned_stop"] == "pausa (refeição) até 12:30"
    assert texts["maintenance"] == "manutenção — OS-123"
    assert texts["stopped"] == "parada — sem motivo"
    assert texts["failure"] == "falha — PDE"
    assert describe(samples["offline"])["since"] is not None              # "sem dados há 12 min" (client)
