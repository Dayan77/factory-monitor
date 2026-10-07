"""scripts/security/audit_gate.py — portão de vulnerabilidades (pip-audit / npm audit + lista de aceites)."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("audit_gate", ROOT / "scripts/security/audit_gate.py")
ag = importlib.util.module_from_spec(spec)
sys.modules["audit_gate"] = ag          # dataclasses resolvem o módulo pelo nome
spec.loader.exec_module(ag)

PIP = {"dependencies": [
    {"name": "python-jose", "version": "3.5.0",
     "vulns": [{"id": "PYSEC-2099-1", "aliases": ["GHSA-aaaa-bbbb-cccc"], "fix_versions": [], "description": "x"}]},
    {"name": "starlette", "version": "0.46.2",
     "vulns": [{"id": "GHSA-zzzz-yyyy-xxxx", "aliases": ["CVE-2099-2"], "fix_versions": ["1.3.1"],
                "description": "path"}]},
    {"name": "ok", "version": "1.0", "vulns": []},
]}

NPM = {"vulnerabilities": {
    "vite": {"name": "vite", "severity": "high", "isDirect": True,
             "via": [{"source": 1, "name": "vite", "title": "fs.deny bypass", "severity": "high", "range": "<=6.4.2",
                      "url": "https://github.com/advisories/GHSA-fx2h-pf6j-xcff"},
                     {"source": 2, "name": "vite", "title": "map traversal", "severity": "moderate", "range": "<=6.4.1",
                      "url": "https://github.com/advisories/GHSA-4w7w-66w2-5vf9"}],
             "fixAvailable": {"name": "vite", "version": "8.3.3", "isSemVerMajor": True}},
    "axios": {"name": "axios", "severity": "critical", "isDirect": True,
              "via": [{"source": 3, "name": "axios", "title": "SSRF", "severity": "critical", "range": "<1.15",
                       "url": "https://github.com/advisories/GHSA-3p68-rc4w-qgx5"}], "fixAvailable": True},
    "vite-node": {"name": "vite-node", "severity": "high", "isDirect": False, "via": ["vite"], "fixAvailable": True},
}}


def entry(**kw):
    base = dict(id="GHSA-aaaa-bbbb-cccc", ecosystem="pip", package="python-jose", reason="r",
                expires=dt.date(2099, 1, 1), projects=["server"])
    base.update(kw)
    return ag.Entry(**base)


def test_pip_every_vulnerability_blocks_unless_accepted_by_id_or_alias():
    findings = ag.parse_pip(PIP)
    assert len(findings) == 2
    errors, warnings = ag.gate(findings, [entry()], "pip", "server")
    assert len(errors) == 1 and "starlette" in errors[0] and "1.3.1" in errors[0]
    assert any("aceito até 2099-01-01" in w and "python-jose" in w for w in warnings)


def test_acceptance_is_scoped_to_its_projects():
    errors, _ = ag.gate(ag.parse_pip(PIP), [entry()], "pip", "edge")
    assert any("python-jose" in e for e in errors), "aceite do server não vale para o edge"


def test_expired_acceptance_blocks():
    errors, _ = ag.gate(ag.parse_pip(PIP), [entry(expires=dt.date(2020, 1, 1))], "pip", "server")
    assert any("VENCIDO" in e and "python-jose" in e for e in errors)


def test_unused_entry_is_reported_as_stale():
    _, warnings = ag.gate([], [entry()], "pip", "server")
    assert any("sem uso" in w for w in warnings)


def test_npm_blocks_high_and_critical_only():
    findings = ag.parse_npm(NPM)
    ids = {f.main_id for f in findings}
    assert ids == {"GHSA-fx2h-pf6j-xcff", "GHSA-4w7w-66w2-5vf9", "GHSA-3p68-rc4w-qgx5"}   # string via ignored
    vite = entry(id="GHSA-fx2h-pf6j-xcff", ecosystem="npm", package="vite", projects=["web"])
    errors, warnings = ag.gate(findings, [vite], "npm", "web")
    assert len(errors) == 1 and "axios" in errors[0] and "critical" in errors[0]
    assert any("não bloqueante" in w and "GHSA-4w7w-66w2-5vf9" in w for w in warnings)


def test_npm_error_report_fails_loudly():
    with pytest.raises(ValueError):
        ag.parse_npm({"error": {"code": "ENOLOCK"}})


def test_cli_exit_codes(tmp_path):
    rep = tmp_path / "pip.json"
    rep.write_text(json.dumps(PIP))
    allow = tmp_path / "allow.toml"
    allow.write_text('[[advisory]]\nid = "GHSA-zzzz-yyyy-xxxx"\necosystem = "pip"\npackage = "starlette"\n'
                     'reason = "teste"\nexpires = 2099-01-01\n'
                     '[[advisory]]\nid = "CVE-2099-1"\necosystem = "pip"\npackage = "python-jose"\n'
                     'reason = "alias não casa"\nexpires = 2099-01-01\n')
    assert ag.main(["pip", str(rep), "--project", "server", "--allowlist", str(allow)]) == 1   # jose sem aceite
    allow.write_text(allow.read_text().replace("CVE-2099-1", "PYSEC-2099-1"))
    assert ag.main(["pip", str(rep), "--project", "server", "--allowlist", str(allow)]) == 0


def test_allowlist_entry_requires_reason_and_expiry(tmp_path):
    p = tmp_path / "a.toml"
    p.write_text('[[advisory]]\nid = "X"\necosystem = "pip"\npackage = "p"\n')
    with pytest.raises(ValueError, match="reason"):
        ag.load_allowlist(p)


def test_repository_allowlist_is_valid_and_every_entry_is_justified():
    entries = ag.load_allowlist(ag.DEFAULT_ALLOWLIST)
    assert entries
    for e in entries:
        assert len(e.reason) > 40, e.id
        assert e.scope in ("runtime", "dev"), e.id
        assert e.expires <= dt.date.today() + dt.timedelta(days=400), f"{e.id}: aceite longo demais (máx. ~1 ano)"
    raw = tomllib.loads(ag.DEFAULT_ALLOWLIST.read_text())
    assert len(raw["advisory"]) == len(entries)
