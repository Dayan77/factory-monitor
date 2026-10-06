"""docs/sprint6.md — consistency of the Sprint 6 specification with the code it builds on.

- visual-state precedence (§1.2): every MachineStatus of the server model is mapped, and the cases follow the table;
- example layout (§1.1): normalised coordinates, one marker per machine, names of the virtual plant;
- tag CSV (§4.1): the example is read with a real CSV reader, converted to a gateway map v1 and accepted by the
  edge's own parser (edge/src/plant_map.py); the invalid row fails with the parser's message;
- the semantic roles listed in §4.1 are exactly the server's and the edge's;
- readiness checks have unique ids.

Run:  .venv/bin/python -m pytest docs/scenarios -q
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import uuid
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DOC = (ROOT / "docs" / "sprint6.md").read_text(encoding="utf-8")
LAYOUT_PY = (ROOT / "edge" / "virtual_plant" / "layout.py").read_text(encoding="utf-8")


def block(tag: str):
    m = re.search(rf"```json {re.escape(tag)}\n(.*?)\n```", DOC, re.S)
    assert m, f"block {tag} not found"
    return json.loads(m.group(1))


def enum_values(path: str, cls: str) -> set[str]:
    src = (ROOT / path).read_text(encoding="utf-8")
    body = src.split(f"class {cls}", 1)[1].split("\n\n", 1)[0]
    return set(re.findall(r'^\s+\w+\s*=\s*"(\w+)"', body, re.M))


# ── §1.2 precedence ──────────────────────────────────────────────────────

PREC = block("s6-status-precedence")


def visual_state(f: dict) -> str:
    """The table of §1.2, first true rule wins."""
    st = f.get("status")
    rules = [
        ("offline", st == "unknown" or f.get("stale")),
        ("failure", f.get("open_failure") or st == "error"),
        ("maintenance", st == "maintenance" or f.get("wo_in_progress_stops_machine")),
        ("planned_stop", f.get("in_break") or f.get("planned_stop") or f.get("open_pln")),
        ("off_shift", f.get("off_shift")),
        ("stopped", f.get("open_downtime") or st == "stopped"),
        ("running", st == "running"),
    ]
    assert [r[0] for r in rules] == PREC["order"]
    return next(name for name, cond in rules if cond)


def test_every_machine_status_is_mapped():
    statuses = enum_values("server/src/models/machine.py", "MachineStatus")
    assert statuses == set(PREC["machine_status_map"]), statuses
    assert set(PREC["machine_status_map"].values()) <= set(PREC["order"])
    for st, vis in PREC["machine_status_map"].items():
        assert visual_state({"status": st}) == vis


@pytest.mark.parametrize("case", PREC["cases"], ids=lambda c: c["id"])
def test_precedence_cases(case):
    assert visual_state(case["facts"]) == case["expected"]


def test_precedence_table_matches_order():
    rows = re.findall(r"^\| (\d) \| `(\w+)` \|", DOC, re.M)
    assert [name for _, name in sorted(rows, key=lambda r: int(r[0]))] == PREC["order"]


# ── §1.1 layout ──────────────────────────────────────────────────────────

def test_layout_example():
    lay = block("s6-layout-example")
    vp_names = set(re.findall(r'name="([^"]+)"', LAYOUT_PY))
    assert lay["plant"] in vp_names
    seen = set()
    for m in lay["markers"]:
        assert 0.0 <= m["x"] <= 1.0 and 0.0 <= m["y"] <= 1.0, m
        assert 0 <= m.get("rotation_deg", 0) < 360
        assert m.get("size", "m") in {"s", "m", "l"}
        if m["kind"] == "machine":
            assert m["machine"] in vp_names, m["machine"]
            assert m["machine"] not in seen, "machine placed twice"
            seen.add(m["machine"])
        elif m["kind"] == "line_label":
            assert m["line"] in vp_names
        else:
            assert m["kind"] == "area_label" and m["label"]


# ── §4.1 tag CSV through the edge parser ─────────────────────────────────

def _edge():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from edge.src import plant_map                      # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"edge code not importable: {exc}")
    return plant_map


CSV_SPEC = block("s6-tags-csv")


def csv_to_map(lines: list[str], sources: list[dict]) -> dict:
    rows = list(csv.DictReader(io.StringIO("\n".join(lines)), delimiter=";"))
    protos = {s["id"]: s["protocol"] for s in sources}
    tags = []
    for r in rows:
        t = {"key": r["chave"], "tag_id": str(uuid.uuid5(uuid.NAMESPACE_URL, r["chave"])),
             "semantic_role": r["papel"], "source": r["fonte"], "machine": r["maquina"],
             "position": r["posicao"] or None, "unit": r["unidade"] or ""}
        if protos[r["fonte"]] == "modbus":
            t["address"] = int(r["endereco"])
            for col, key, conv in (("unit_id", "unit_id", int), ("tipo_registro", "register_type", str),
                                   ("tipo_dado", "data_type_plc", str), ("ordem_palavras", "word_order", str)):
                if r[col]:
                    t[key] = conv(r[col])
        else:
            t["address"] = r["endereco"]
        if r["contador_max"]:
            t["counter_max"] = int(r["contador_max"])
        for col, key, conv in (("escala", "scale", float), ("offset", "offset", float),
                               ("intervalo_ms", "poll_interval_ms", int)):
            if r[col]:
                t[key] = conv(r[col])
        tags.append(t)
    return {"version": 1, "gateway_id": "gw-test", "sources": sources, "tags": tags}


def test_tags_csv_example_is_accepted_by_the_edge_parser():
    pm = _edge()
    doc = csv_to_map(CSV_SPEC["csv"], CSV_SPEC["sources"])
    parsed = pm.parse_plant_map(doc, env={})
    assert len(parsed.tags) == len(CSV_SPEC["csv"]) - 1 and parsed.skipped == []
    vp_names = set(re.findall(r'name="([^"]+)"', LAYOUT_PY)) | set(re.findall(r'name=f"Cilindro \{name\}"', LAYOUT_PY))
    for t in doc["tags"]:
        assert t["machine"] in vp_names, t["machine"]
    opc = [t for t in parsed.tags if t.source == "opcua-plc"]
    assert opc and all(isinstance(t.address, str) and t.address.startswith("ns=2;s=") for t in opc)


def test_invalid_csv_row_fails_with_the_edge_message():
    pm = _edge()
    lines = [CSV_SPEC["csv"][0], CSV_SPEC["invalid_csv_row"]]
    with pytest.raises(pm.PlantMapError, match=re.escape(CSV_SPEC["invalid_error_contains"])):
        pm.parse_plant_map(csv_to_map(lines, CSV_SPEC["sources"]), env={})


def test_semantic_roles_listed_match_server_and_edge():
    sec = DOC.split("### 4.1 CSV de tags", 1)[1].split("###", 1)[0]
    row = next(line for line in sec.splitlines() if line.startswith("| `papel`"))
    listed = set(re.findall(r"`(\w+)`", row)) - {"papel"}
    server = enum_values("server/src/models/tag.py", "SemanticRole")
    assert listed == server
    pm = _edge()
    assert listed == set(pm.SEMANTIC_ROLES)
    header = next(csv.reader([CSV_SPEC["csv"][0]], delimiter=";"))
    documented = set(re.findall(r"`(\w+)`", sec.split("Regras:", 1)[0]))
    assert set(header) <= documented, set(header) - documented


def test_readiness_checks():
    checks = block("s6-readiness-checks")
    ids = [c["id"] for c in checks]
    assert len(ids) == len(set(ids))
    assert all(isinstance(c["required"], bool) and c["rule"] for c in checks)
    assert {"gateway_online", "data_flowing", "output_machine_per_line"} <= set(ids)
