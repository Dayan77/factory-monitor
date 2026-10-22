"""Sprint 7 public/owner documents: docs/overview.md, docs/release_notes_v1.md and the status of docs/roadmap.md.

Checks:
  * every relative link resolves (file and #anchor) — these documents are sent to prospects and customers;
  * the overview covers the sections the owner asked for (problem, measures, connection, screens per role, KPIs and
    norms, security, deployment, validation, what still needs a pilot);
  * no fabricated customer claim: the fictitious cases removed from the landing page never come back, and the
    overview / release notes say explicitly that there is no customer result yet;
  * the release notes have "Limitações conhecidas" naming the open product gaps that the pilot checklist assumes
    (OPC UA certificate, operator PIN) and the open audit item A-19b in plain words;
  * roadmap: every delivered (✅) item carries its sprint (S1 … S7), the pilot is NOT marked delivered and the
    honest pending list exists;
  * no credentials.

Run:  .venv/bin/python -m pytest docs/scenarios/test_public_docs_s7.py -q
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
NEW = ["overview.md", "release_notes_v1.md", "roadmap.md"]
FICTITIOUS = ["Metalúrgica Fonseca", "Plásticos Norte", "Alimentos BomDia"]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def slug(heading: str) -> str:
    s = re.sub(r"[`*_]", "", heading.strip().lower())
    s = "".join(ch for ch in s if ch.isalnum() or ch in " -" or unicodedata.category(ch).startswith("L"))
    return s.replace(" ", "-")


def anchors(text: str) -> set[str]:
    ids = set(re.findall(r'<a id="([^"]+)"></a>', text))
    return ids | {slug(h) for h in re.findall(r"^#{1,6} (.+)$", text, re.M)}


@pytest.mark.parametrize("name", NEW)
def test_relative_links_resolve(name):
    text = read(DOCS / name)
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if re.match(r"[a-z]+:", target):
            continue
        path, _, frag = target.partition("#")
        dest = (DOCS / path).resolve() if path else (DOCS / name)
        assert dest.exists(), f"{name}: broken link {target}"
        if frag and dest.suffix == ".md":
            assert frag in anchors(read(dest)), f"{name}: anchor #{frag} not found in {dest.name}"


def test_overview_covers_the_requested_sections():
    text = read(DOCS / "overview.md")
    heads = [h.lower() for h in re.findall(r"^## (.+)$", text, re.M)]
    for needle in ["problema", "o que o factoryconnect faz", "como conecta", "telas por papel", "normas",
                   "segurança", "implantação", "validado", "piloto real"]:
        assert any(needle in h for h in heads), f"overview.md: missing section about '{needle}'"
    for role in ["Operador", "Supervisor", "Planejador", "Técnico", "Gerente", "Administrador"]:
        assert role in text
    for norm in ["ISO 22400", "ISO 14224", "LGPD"]:
        assert norm in text
    for proof in ["Cenários de ouro", "Planta virtual", "audit_kpis.md"]:
        assert proof in text


@pytest.mark.parametrize("name", ["overview.md", "release_notes_v1.md", "roadmap.md"])
def test_no_fabricated_customer_claims(name):
    text = read(DOCS / name)
    for line in text.splitlines():
        if any(fake in line for fake in FICTITIOUS):   # only the roadmap may name them, to record their removal
            assert name == "roadmap.md" and "removidos" in line, f"{name}: fictitious case quoted: {line.strip()}"
    assert not re.search(r"(?i)resultados reais|clientes satisfeitos|aumento (médio )?de \d+ ?%", text)


def test_no_customer_result_is_claimed_explicitly():
    assert "não tem cliente em produção nem case publicado" in read(DOCS / "overview.md")
    assert "Não existe hoje nenhum resultado de ganho" in read(DOCS / "overview.md")
    assert "ainda vai acontecer" in read(DOCS / "release_notes_v1.md")


def test_release_notes_known_limitations():
    text = read(DOCS / "release_notes_v1.md")
    lim = text.split("## Limitações conhecidas", 1)
    assert len(lim) == 2
    body = lim[1].split("\n## ", 1)[0]
    for gap in ["Basic256Sha256", "PIN", "Atraso da leitura ao vivo", "SAP PM", "Inglês", "14 dias"]:
        assert gap in body, f"release notes: limitation '{gap}' missing"
    # A-19b (live map cadence glitch) was fixed in Sprint 7b: it must no longer be sold as a limitation
    assert "Cadência ao vivo do mapa" not in body, "release notes: stale A-19b limitation still listed"


def test_roadmap_delivered_items_carry_their_sprint_and_pilot_is_open():
    text = read(DOCS / "roadmap.md")
    for line in text.splitlines():
        if re.match(r"\s*- \[x\]", line):
            assert re.search(r"\bS[1-7]\b", line), f"delivered item without sprint: {line.strip()}"
        if re.match(r"\s*- \[ \]", line):
            assert "✅" not in line, f"open item marked ✅: {line.strip()}"
    pilot = [ln for ln in text.splitlines() if "**Piloto real**" in ln]
    assert pilot and all(ln.lstrip().startswith("- [ ]") for ln in pilot)
    assert "## Pendências honestas" in text
    for gap in ["Piloto em fábrica real", "Cases de cliente", "Go-live", "OPC UA com certificados", "Inglês"]:
        assert gap in text
    # consistency with docs/audit_kpis.md: once A-19b is marked fixed there, the roadmap must not list it as open
    audit = read(DOCS / "audit_kpis.md")
    if re.search(r"### A-19b .*corrigido", audit):
        pend = text.split("## Pendências honestas", 1)[1]
        assert not re.search(r"^\s*\d+\. \*\*A-19b\*\*", pend, re.M), "roadmap: A-19b still listed as pending"
        assert "ressalva aberta A-19b" not in text and "exceto **A-19b**" not in text, \
            "roadmap: A-19b still described as open"


@pytest.mark.parametrize("name", NEW)
def test_no_credentials(name):
    text = read(DOCS / name)
    assert not re.search(r"Admin@1234|Super@1234|fm_dev_password|dev-token-change-me", text)
    assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text), "no IP addresses in public documents"
