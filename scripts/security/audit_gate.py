#!/usr/bin/env python3
"""Portão de vulnerabilidades de dependências (CI: .github/workflows/security.yml).

Lê o JSON do pip-audit ou do `npm audit` e reprova quando houver vulnerabilidade que não está na lista de
advisories aceitos (.github/security/audit-allowlist.toml):

* pip  — toda vulnerabilidade reprova (o pip-audit não traz severidade);
* npm  — advisories high/critical reprovam; moderate/low só aparecem como aviso;
* entrada da lista vencida (``expires`` no passado) reprova: aceite de risco tem prazo e precisa ser revisto;
* entrada que não casa com nada vira aviso (limpe a lista quando a dependência for corrigida).

    pip-audit -r server/requirements.txt -f json -o pip-server.json
    python3 scripts/security/audit_gate.py pip pip-server.json --project server
    (cd web && npm audit --json > ../npm-web.json); python3 scripts/security/audit_gate.py npm npm-web.json --project web

Sem dependências fora da biblioteca padrão (Python >= 3.11, tomllib).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ALLOWLIST = ROOT / ".github/security/audit-allowlist.toml"
BLOCKING_NPM = {"high", "critical"}


@dataclass
class Finding:
    ids: set[str]                 # advisory id + aliases (GHSA-…, CVE-…, PYSEC-…)
    package: str
    version: str
    severity: str                 # pip: "unknown"
    title: str = ""
    fix: str = ""

    @property
    def main_id(self) -> str:
        for prefix in ("GHSA-", "PYSEC-", "CVE-"):
            for i in sorted(self.ids):
                if i.startswith(prefix):
                    return i
        return sorted(self.ids)[0] if self.ids else "?"


@dataclass
class Entry:
    id: str
    ecosystem: str
    package: str
    reason: str
    expires: dt.date
    projects: list[str] = field(default_factory=list)
    scope: str = ""

    def matches(self, f: Finding, ecosystem: str, project: str) -> bool:
        if self.ecosystem != ecosystem or self.package.lower() != f.package.lower():
            return False
        if self.projects and project not in self.projects:
            return False
        return self.id.upper() in {i.upper() for i in f.ids}


def load_allowlist(path: Path) -> list[Entry]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out = []
    for i, a in enumerate(data.get("advisory", [])):
        missing = [k for k in ("id", "ecosystem", "package", "reason", "expires") if not a.get(k)]
        if missing:
            raise ValueError(f"{path}: advisory #{i + 1} sem {', '.join(missing)}")
        exp = a["expires"]
        if isinstance(exp, str):
            exp = dt.date.fromisoformat(exp)
        if a["ecosystem"] not in ("pip", "npm"):
            raise ValueError(f"{path}: advisory {a['id']}: ecosystem deve ser pip ou npm")
        out.append(Entry(id=a["id"], ecosystem=a["ecosystem"], package=a["package"], reason=a["reason"],
                         expires=exp, projects=list(a.get("projects", [])), scope=a.get("scope", "")))
    return out


def parse_pip(doc: dict) -> list[Finding]:
    deps = doc["dependencies"] if isinstance(doc, dict) else doc
    out = []
    for d in deps:
        for v in d.get("vulns") or []:
            out.append(Finding(ids={v["id"], *v.get("aliases", [])}, package=d["name"], version=d.get("version", ""),
                               severity="unknown", title=(v.get("description") or "").strip().splitlines()[0][:120]
                               if v.get("description") else "", fix=", ".join(v.get("fix_versions") or [])))
    return out


def parse_npm(doc: dict) -> list[Finding]:
    """npm audit --json (lockfile v2/v3 report): one finding per advisory object, deduplicated by (id, package)."""
    if "error" in doc and "vulnerabilities" not in doc:
        raise ValueError(f"npm audit falhou: {doc['error']}")
    seen: dict[tuple[str, str], Finding] = {}
    for name, v in (doc.get("vulnerabilities") or {}).items():
        for via in v.get("via", []):
            if not isinstance(via, dict):
                continue                      # string = depende de outro pacote vulnerável (o advisory está nele)
            url = via.get("url", "")
            adv = url.rstrip("/").rsplit("/", 1)[-1] if url else str(via.get("source", "?"))
            key = (adv, via.get("name", name))
            if key in seen:
                continue
            fix = v.get("fixAvailable")
            fix_s = (f"{fix.get('name')}@{fix.get('version')}" + (" (major)" if fix.get("isSemVerMajor") else "")
                     if isinstance(fix, dict) else ("npm audit fix" if fix else "sem correção"))
            seen[key] = Finding(ids={adv}, package=via.get("name", name), version=via.get("range", ""),
                                severity=via.get("severity", v.get("severity", "unknown")),
                                title=via.get("title", ""), fix=fix_s)
    return list(seen.values())


def gate(findings: list[Finding], entries: list[Entry], ecosystem: str, project: str,
         today: dt.date | None = None) -> tuple[list[str], list[str]]:
    """→ (erros, avisos)."""
    today = today or dt.date.today()
    errors, warnings = [], []
    relevant = [e for e in entries if e.ecosystem == ecosystem and (not e.projects or project in e.projects)]
    used: set[int] = set()
    for f in sorted(findings, key=lambda f: (f.package, f.main_id)):
        blocking = ecosystem == "pip" or f.severity in BLOCKING_NPM
        hit = next((i for i, e in enumerate(relevant) if e.matches(f, ecosystem, project)), None)
        desc = f"{f.package} {f.version} {f.main_id} [{f.severity}] {f.title} (correção: {f.fix or 'nenhuma'})"
        if hit is not None:
            used.add(hit)
            e = relevant[hit]
            if e.expires < today:
                errors.append(f"aceite VENCIDO em {e.expires}: {desc} — revise .github/security/audit-allowlist.toml")
            else:
                warnings.append(f"aceito até {e.expires}: {desc} — {e.reason}")
        elif blocking:
            errors.append(f"vulnerabilidade não aceita: {desc}")
        else:
            warnings.append(f"não bloqueante: {desc}")
    for i, e in enumerate(relevant):
        if i not in used:
            warnings.append(f"entrada sem uso na lista ({e.ecosystem} {e.package} {e.id}): a dependência foi corrigida? "
                            "remova-a")
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ecosystem", choices=["pip", "npm"])
    ap.add_argument("report", type=Path, help="JSON do pip-audit (-f json) ou do npm audit --json")
    ap.add_argument("--project", required=True, help="server | edge | scripts | web | site")
    ap.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    ap.add_argument("--today", type=dt.date.fromisoformat, default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    doc = json.loads(a.report.read_text(encoding="utf-8"))
    findings = parse_pip(doc) if a.ecosystem == "pip" else parse_npm(doc)
    errors, warnings = gate(findings, load_allowlist(a.allowlist), a.ecosystem, a.project, a.today)
    gh = "GITHUB_ACTIONS" in __import__("os").environ
    for w in warnings:
        print(f"::warning title={a.ecosystem}:{a.project}::{w}" if gh else f"  aviso: {w}")
    for e in errors:
        print(f"::error title={a.ecosystem}:{a.project}::{e}" if gh else f"  ERRO: {e}")
    print(f"{a.ecosystem}:{a.project}: {len(findings)} advisories, {len(errors)} bloqueante(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
