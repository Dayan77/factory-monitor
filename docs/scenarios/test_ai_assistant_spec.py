"""docs/ai_assistant.md — consistency of the AI assistant tool catalog (`ai-tools`) and examples (`ai-examples`).

- every tool is read-only (GET) in Sprint 5 and never takes tenant_id;
- every route of a tool marked `existing` really exists in the server (parsed from server/src/main.py prefixes +
  the route decorators), so the spec does not promise data the API cannot serve;
- the roles of a tool match the route's `require_any(...)` dependency (no tool wider than its screen);
- the 15 examples use only catalog tools and parameters, and the asker's role may use every tool called.

Run:  .venv/bin/python -m pytest docs/scenarios -q
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DOC = (ROOT / "docs" / "ai_assistant.md").read_text(encoding="utf-8")
ROUTES_DIR = ROOT / "server" / "src" / "api" / "routes"
MAIN = ROOT / "server" / "src" / "main.py"


def block(tag: str):
    return json.loads(DOC.split(f"```json {tag}", 1)[1].split("```", 1)[0])


TOOLS = {t["name"]: t for t in block("ai-tools")}
EXAMPLES = block("ai-examples")
ROLES = {"operator", "manager", "admin", "superadmin", "maintenance_planner", "technician", "production_supervisor"}


def server_routes() -> dict[tuple[str, str], str]:
    """(METHOD, full path) → the dependency text of the decorator (for role checks)."""
    out: dict[tuple[str, str], str] = {}
    main = MAIN.read_text(encoding="utf-8")
    for mod, router, prefix in re.findall(r'include_router\((\w+)\.(\w+),\s*prefix="([^"]*)"', main):
        f = ROUTES_DIR / f"{mod}.py"
        if not f.exists():
            continue
        src = f.read_text(encoding="utf-8")
        consts = dict(re.findall(r'^(\w+)\s*=\s*(\([^)]*\))', src, flags=re.M))
        for m in re.finditer(rf'@{router}\.(get|post|put|patch|delete)\("([^"]*)"([^\n]*)', src):
            deps = m.group(3)
            for name, val in consts.items():
                deps = deps.replace(f"*{name}", val)
            out[(m.group(1).upper(), prefix + m.group(2))] = deps
    return out


ROUTES = server_routes() if MAIN.exists() else {}


def test_catalog_shape():
    assert len(TOOLS) == len(block("ai-tools")), "duplicate tool names"
    required = {"resolve_asset", "get_oee", "get_losses_pareto", "get_downtime", "get_machine_status",
                "get_component_life", "get_maintenance_due", "get_work_orders", "get_reliability", "search_failures"}
    assert required <= TOOLS.keys()
    for t in TOOLS.values():
        assert t["status"] in ("existing", "sprint5")
        assert t["description"] and t["api"]
        assert set(t["required"]) <= t["params"].keys(), t["name"]
        assert t["roles"] == ["*"] or set(t["roles"]) <= ROLES, t["name"]


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_read_only_and_no_tenant_param(name):
    t = TOOLS[name]
    assert all(a["method"] == "GET" for a in t["api"]), "Sprint 5: only read tools (IA-11)"
    assert not any("tenant" in p for p in t["params"]), "tenant comes from the JWT (IA-4)"
    for p in t["params"].values():
        if p.get("type") == "integer" and "maximum" in p:
            assert p["maximum"] <= 50


@pytest.mark.skipif(not ROUTES, reason="server routes not available")
@pytest.mark.parametrize("name", sorted(n for n, t in TOOLS.items() if t["status"] == "existing"))
def test_existing_tool_routes_exist(name):
    for a in TOOLS[name]["api"]:
        assert (a["method"], a["path"]) in ROUTES, f"{name}: {a['method']} {a['path']} not in server routes"


@pytest.mark.skipif(not ROUTES, reason="server routes not available")
@pytest.mark.parametrize("name", sorted(n for n, t in TOOLS.items() if t["status"] == "existing"))
def test_tool_roles_match_route_dependency(name):
    """A tool's roles are exactly the route's `require_any(...)` roles; `*` ⇔ the route has no role guard
    (row-level visibility, if any, is applied by the route itself — IA-1/IA-4)."""
    for a in TOOLS[name]["api"]:
        deps = ROUTES[(a["method"], a["path"])]
        found = set(re.findall(r'"(\w+)"', deps)) if "require" in deps else set()
        want = set() if TOOLS[name]["roles"] == ["*"] else set(TOOLS[name]["roles"])
        assert found == want, (name, a["path"], found)


@pytest.mark.parametrize("ex", EXAMPLES, ids=lambda e: f"q{e['id']}")
def test_examples_use_catalog(ex):
    assert ex["role"] in ROLES
    calls = ex["calls"] + ex.get("follow_up", {}).get("calls", [])
    for c in calls:
        t = TOOLS[c["name"]]
        assert set(c["args"]) <= t["params"].keys(), (ex["id"], c["name"], set(c["args"]) - t["params"].keys())
        assert t["roles"] == ["*"] or ex["role"] in t["roles"] or ex["role"] in ("admin", "superadmin"), (
            ex["id"], c["name"], ex["role"])
        for k, v in c["args"].items():
            enum = t["params"][k].get("enum") or t["params"][k].get("items", {}).get("enum")
            if enum and not str(v).startswith("<"):
                vals = v if isinstance(v, list) else [v]
                assert set(vals) <= set(enum), (ex["id"], k, v)
    for f in ex["answer"].get("forbidden_tools", []):
        assert ex["role"] not in TOOLS[f]["roles"] and TOOLS[f]["roles"] != ["*"]


def test_examples_cover_the_spec():
    assert [e["id"] for e in EXAMPLES] == list(range(1, 16))
    reasons = {e["answer"]["refusal_reason"] for e in EXAMPLES}
    assert {"ambiguous", "insufficient_data", "no_permission", "write_not_allowed"} <= reasons
    assert {e["role"] for e in EXAMPLES} >= {"manager", "maintenance_planner", "technician", "production_supervisor",
                                             "operator"}
    # every example table row in the doc matches the JSON (same 15 questions)
    for e in EXAMPLES:
        assert f"\"{e['question']}\"" in DOC, e["id"]
    # answers that are not refusals must cite (IA-2)
    assert all(e["answer"]["must_cite"] for e in EXAMPLES if e["answer"]["refusal_reason"] is None)
