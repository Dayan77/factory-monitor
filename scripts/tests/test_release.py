"""Processo de release: scripts/release/check_release.py, VERSION, CHANGELOG e o workflow de release/deploy."""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_release", ROOT / "scripts/release/check_release.py")
cr = importlib.util.module_from_spec(spec)
sys.modules["check_release"] = cr
spec.loader.exec_module(cr)

CL = """# Changelog

## [Unreleased]

## [1.1.0] - 2026-10-01

### Adicionado
- coisa nova importante

## [1.1.0-rc.1] - 2026-09-20

### Adicionado
- candidata

## [1.0.0] - 2026-09-01

### Adicionado
- primeira
"""


def test_repository_release_is_consistent():
    version = (ROOT / "VERSION").read_text().strip()
    errors, body = cr.check(version, (ROOT / "CHANGELOG.md").read_text(), f"v{version}")
    assert not errors, errors
    assert "### " in body


def test_tag_must_match_version():
    errors, _ = cr.check("1.1.0", CL, "v1.0.0")
    assert any("diferente" in e for e in errors)
    assert cr.check("1.1.0", CL, "v1.1.0")[0] == []


@pytest.mark.parametrize("bad", ["v1.0.0", "1.0", "01.0.0", "1.0.0+build", "latest"])
def test_version_must_be_semver(bad):
    assert cr.check(bad, CL)[0]


def test_version_needs_dated_non_empty_section():
    assert any("sem a seção" in e for e in cr.check("2.0.0", CL)[0])
    assert any("data" in e for e in cr.check("1.2.0", CL.replace("## [1.1.0] - 2026-10-01", "## [1.2.0]"))[0])
    empty = CL.replace("### Adicionado\n- coisa nova importante\n", "")
    assert any("vazia" in e for e in cr.check("1.1.0", empty)[0])


def test_changelog_order_and_duplicates():
    swapped = CL.replace("## [1.0.0]", "## [1.2.0]")
    assert any("fora de ordem" in e for e in cr.check("1.1.0", swapped)[0])
    dup = CL.replace("## [1.0.0]", "## [1.1.0]")
    assert any("repetida" in e for e in cr.check("1.1.0", dup)[0])


def test_notes_are_the_version_section(tmp_path):
    (tmp_path / "VERSION").write_text("1.1.0\n")
    (tmp_path / "CHANGELOG.md").write_text(CL)
    out = tmp_path / "notes.md"
    assert cr.main(["--root", str(tmp_path), "--tag", "v1.1.0", "--notes", str(out)]) == 0
    assert out.read_text().strip() == "### Adicionado\n- coisa nova importante"


def test_release_workflow_gates_scan_sbom_and_provenance():
    wf = (ROOT / ".github/workflows/release.yml").read_text()
    assert re.search(r"tags:\s*\n\s*- \"v\*\.\*\.\*\"", wf)
    assert "scripts/release/check_release.py" in wf
    assert "--exit-code 1" in wf and "HIGH,CRITICAL" in wf          # Trivy bloqueia antes do push
    assert "cyclonedx" in wf
    assert "provenance: mode=max" in wf and "attest-build-provenance" in wf
    for img in ("server", "web", "site", "edge"):
        assert f"name: {img}" in wf
    assert wf.index("Trivy") < wf.index("push: true"), "varre antes de publicar"


def test_deploy_workflow_deploys_a_tag_with_manual_approval():
    wf = (ROOT / ".github/workflows/deploy.yml").read_text()
    assert "workflow_run" not in wf, "deploy em produção nunca é automático"
    assert "workflow_dispatch" in wf and "version:" in wf
    assert re.search(r"environment:\s*\n\s*name: \$\{\{ inputs.target \}\}", wf)
    assert "scripts/remote_deploy.sh" in wf and "DEPLOY_TARGET" in wf
    assert "packages: read" in wf
