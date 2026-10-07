"""docs/manual/ — the customer-facing user manual (Sprint 6).

Checks that keep the manual publishable on the public site's docs section and consistent with the specification:
  * one file per role + index + KPI page, each with front matter (title, description, ordem, papel);
  * every relative link resolves (file and #anchor), the index links every page;
  * every role page is task-oriented ("Como …" sections) and has "Erros comuns" and "Perguntas frequentes";
  * every KPI section cites docs/kpis.md (or predictive.md / reports.md / domain.md) with a § that exists there;
  * the worked numbers quoted in the manual are the ones of the spec's test blocks (kpis.md §6, reports.md §7),
    formatted as the UI does (pt-BR, S3-5);
  * no credentials, no placeholders.

Run:  .venv/bin/python -m pytest docs/scenarios/test_manual_docs.py -q
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
MANUAL = DOCS / "manual"
ROLE_PAGES = {"operador.md": "operator", "supervisor.md": "production_supervisor",
              "planejador.md": "maintenance_planner", "tecnico.md": "technician", "gerente.md": "manager",
              "administrador.md": "admin"}
PAGES = ["README.md", "indicadores.md", *ROLE_PAGES]


def read(name: str) -> str:
    return (MANUAL / name).read_text(encoding="utf-8")


def front_matter(text: str) -> dict:
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, "missing front matter"
    out = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip().strip('"')
    return out


def slug(heading: str) -> str:
    """GitHub-style heading anchor (also what Astro/remark-slug produce)."""
    s = re.sub(r"[`*_]", "", heading.strip().lower())
    s = "".join(ch for ch in s if ch.isalnum() or ch in " -" or unicodedata.category(ch).startswith("L"))
    return s.replace(" ", "-")


def anchors(text: str) -> set[str]:
    ids = set(re.findall(r'<a id="([^"]+)"></a>', text))
    ids |= {slug(h) for h in re.findall(r"^#{1,6} (.+)$", text, re.M)}
    return ids


def links(text: str) -> list[str]:
    body = re.sub(r"```.*?```", "", text, flags=re.S)
    return re.findall(r"\]\(([^)\s]+)\)", body)


def block(path: Path, tag: str):
    m = re.search(rf"```json {re.escape(tag)}\n(.*?)\n```", path.read_text(encoding="utf-8"), re.S)
    assert m, f"block {tag} not found in {path.name}"
    return json.loads(m.group(1))


def pct(x: float) -> str:
    """S3-5: one decimal, half-up, comma, non-breaking space not required in the docs (plain space)."""
    v = math.floor(x * 1000 + 0.5) / 10
    return f"{v:.1f}".replace(".", ",") + " %"


# ── structure ────────────────────────────────────────────────────────────────

def test_all_pages_exist_with_front_matter():
    seen_order = set()
    for name in PAGES:
        fm = front_matter(read(name))
        assert fm.get("title") and fm.get("description"), name
        assert fm.get("ordem", "").isdigit(), name
        assert fm["ordem"] not in seen_order, f"duplicate ordem in {name}"
        seen_order.add(fm["ordem"])
        if name in ROLE_PAGES:
            assert fm.get("papel") == ROLE_PAGES[name], name


def test_index_links_every_page():
    idx = read("README.md")
    for name in PAGES[1:]:
        assert f"]({name})" in idx, f"README.md does not link {name}"


@pytest.mark.parametrize("name", PAGES)
def test_links_resolve(name):
    text = read(name)
    for href in links(text):
        if re.match(r"^[a-z]+://", href) or href.startswith("mailto:"):
            continue
        path, _, frag = href.partition("#")
        target = (MANUAL / path).resolve() if path else (MANUAL / name).resolve()
        assert target.exists(), f"{name}: broken link {href}"
        if frag:
            assert target.suffix == ".md", f"{name}: anchor into non-markdown {href}"
            assert frag in anchors(target.read_text(encoding="utf-8")), f"{name}: missing anchor {href}"


@pytest.mark.parametrize("name", list(ROLE_PAGES))
def test_role_pages_are_task_oriented(name):
    text = read(name)
    tasks = re.findall(r"^## Como .+$", text, re.M)
    assert len(tasks) >= 4, f"{name}: only {len(tasks)} 'Como …' tasks"
    assert re.search(r"^## Erros comuns$", text, re.M), name
    faq = text.split("## Perguntas frequentes", 1)
    assert len(faq) == 2, f"{name}: no FAQ"
    assert len(re.findall(r"^\*\*[^*].+\*\*$", faq[1], re.M)) >= 4, f"{name}: FAQ with fewer than 4 questions"
    assert "indicadores.md#" in text or name == "administrador.md", f"{name}: does not link the KPI page"


def test_required_tasks_are_covered():
    """The tasks named in the Sprint 6 assignment, each in the page of the role that does it."""
    required = {
        "operador.md": ["Como justificar uma parada", "Como chamar a manutenção", "Como trocar o produto"],
        "supervisor.md": ["Como ler o OEE e o Pareto", "Como tratar paradas sem motivo", "Como cadastrar o tablet"],
        "planejador.md": ["Como programar a semana", "Como interpretar um alerta preditivo e a RUL",
                          "Como aprovar ou descartar um rascunho"],
        "tecnico.md": ["Como executar uma preventiva", "Como atender um chamado emergencial",
                       "Como trocar um componente"],
        "gerente.md": ["Como ler o OEE e o Pareto", "Como ler o Weibull", "Como ler a gestão da manutenção"],
        "administrador.md": ["Como implantar uma fábrica nova", "Como importar tags por CSV",
                             "Como cadastrar usuários", "Como configurar os relatórios agendados",
                             "Como cuidar dos gateways"],
    }
    for name, titles in required.items():
        text = read(name)
        for t in titles:
            assert re.search(rf"^## {re.escape(t)}", text, re.M), f"{name}: missing task '{t}'"


# ── KPI page: references and numbers ─────────────────────────────────────────

SPEC = {"kpis.md": DOCS / "kpis.md", "domain.md": DOCS / "domain.md", "predictive.md": DOCS / "predictive.md",
        "reports.md": DOCS / "reports.md"}


def test_every_kpi_section_cites_the_spec():
    text = read("indicadores.md")
    sections = re.split(r"\n(?=### )", text)
    kpi_sections = [s for s in sections if s.startswith("### ")]
    assert len(kpi_sections) >= 25
    for s in kpi_sections:
        title = s.splitlines()[0]
        refs = re.findall(r"(kpis\.md|domain\.md|predictive\.md|reports\.md) (§[\d.]+|P-\d+)", s)
        assert refs, f"KPI section without a spec reference: {title}"
        for doc, sec in refs:
            spec = SPEC[doc].read_text(encoding="utf-8")
            if sec.startswith("§"):
                num = sec[1:].rstrip(".")
                assert re.search(rf"^#{{2,3}} {re.escape(num)}[ .]", spec, re.M), f"{title}: {doc} {sec} not found"
            else:
                assert re.search(rf"^## {re.escape(sec)} ", spec, re.M), f"{title}: {doc} {sec} not found"


def test_quoted_examples_match_the_spec_cases():
    """The worked examples of the manual are the spec's test cases, formatted like the UI (S3-5)."""
    cases = {c["id"]: c for c in block(DOCS / "kpis.md", "kpi-test-cases")}
    kpi_page = read("indicadores.md")
    e1 = cases["oee_single_shift"]["expected"]
    for k in ("availability", "performance", "quality", "oee"):
        assert pct(e1[k]) in kpi_page, f"E1 {k} {pct(e1[k])} not quoted"
    e2 = cases["oee_two_shifts_mix"]["expected"]
    day = pct(e2["oee"])
    shifts = [pct(x) for x in e2["shift_oee"]]
    wrong = pct(e2["wrong_mean_of_shift_oee"])
    for page in ("indicadores.md", "supervisor.md", "gerente.md"):
        t = read(page)
        assert day in t and all(s in t for s in shifts), f"{page}: E2 numbers"
        assert wrong in t or page == "supervisor.md", f"{page}: should show the wrong mean as a counter-example"
    assert pct(cases["teep_day"]["expected"]["teep"]) in kpi_page
    pareto = cases["loss_pareto"]["expected"]["items"]
    for code, minutes, share, acc in pareto:
        assert pct(share) in kpi_page and pct(acc) in kpi_page, code
    assert pct(cases["pmp"]["expected"]["pmp"]) in kpi_page
    assert pct(cases["schedule_compliance"]["expected"]["compliance"]) in kpi_page
    assert pct(cases["availability_operational"]["expected"]["ao"]) in kpi_page
    assert pct(cases["availability_inherent"]["expected"]["ai"]) in kpi_page
    assert "2,0 semanas" in kpi_page and "180 h" in kpi_page and "60 min" in kpi_page and "9 400 h" in kpi_page
    rul = cases["rul_projection"]["expected"]
    assert f"{rul['days']} dias" in kpi_page and "14/12/2026" in kpi_page
    plan = cases["plan_due_whichever_first"]["expected"]
    assert f"{plan['days']} dias (20/10/2026)" in kpi_page


def test_weibull_numbers_match_reports_block():
    w = {c["id"]: c for c in block(DOCS / "reports.md", "report-weibull-cases")}["weibull_cylinders"]["expected"]
    t = read("indicadores.md")
    fmt = lambda v: f"{round(v):,}".replace(",", " ")  # noqa: E731
    assert f"{w['beta']:.2f}".replace(".", ",") in t
    assert f"{w['beta_lo']:.2f}".replace(".", ",") in t and f"{w['beta_hi']:.2f}".replace(".", ",") in t
    for k in ("eta", "mttf", "b10"):
        assert fmt(w[k]) in t, k
    assert fmt(w["simple_mttf"]) in t
    assert "36,1 %" in t and "1,4 %" in t          # p_fail 0.361250 / 0.013788


# ── hygiene ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", PAGES)
def test_no_credentials_or_placeholders(name):
    text = read(name)
    for bad in ("Admin@1234", "Super@1234", "dev-token", "fm_dev_password", "TODO", "FIXME", "lorem", "XXX "):
        assert bad not in text, f"{name}: contains {bad!r}"
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", re.sub(r"```.*?```", "", text, flags=re.S)), \
        f"{name}: IP address outside a code example"
