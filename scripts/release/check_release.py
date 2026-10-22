#!/usr/bin/env python3
"""Confere a consistência de uma release e extrai as notas do CHANGELOG (workflow Release; `make release-check`).

    python3 scripts/release/check_release.py                 # VERSION é SemVer e tem seção no CHANGELOG
    python3 scripts/release/check_release.py --tag v0.7.0    # e a tag bate com VERSION
    python3 scripts/release/check_release.py --notes out.md  # grava as notas da versão (corpo da GitHub Release)

Regras: VERSION = X.Y.Z[-pre] (SemVer 2.0, sem "v"); tag = "v" + VERSION; CHANGELOG.md no formato Keep a
Changelog com "## [X.Y.Z] - AAAA-MM-DD" e a seção não vazia; versões em ordem decrescente e sem repetição.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$")
HEADING = re.compile(r"^## \[([^\]]+)\](?: - (\d{4}-\d{2}-\d{2}))?\s*$", re.M)


def _key(v: str) -> tuple:
    m = SEMVER.match(v)
    major, minor, patch, pre = int(m[1]), int(m[2]), int(m[3]), m[4]
    # pré-release vem antes da versão final (1.0.0-rc.1 < 1.0.0)
    return (major, minor, patch, 0 if pre else 1, pre or "")


def sections(changelog: str) -> list[tuple[str, str | None, str]]:
    heads = list(HEADING.finditer(changelog))
    out = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(changelog)
        out.append((h[1], h[2], changelog[h.end():end].strip()))
    return out


def check(version: str, changelog: str, tag: str | None = None) -> tuple[list[str], str]:
    errors: list[str] = []
    if not SEMVER.match(version):
        errors.append(f"VERSION '{version}' não é SemVer X.Y.Z[-pre]")
        return errors, ""
    if tag is not None and tag != f"v{version}":
        errors.append(f"tag '{tag}' diferente de v{version} (arquivo VERSION)")
    secs = sections(changelog)
    released = [(v, d, b) for v, d, b in secs if v.lower() != "unreleased"]
    bad = [v for v, _, _ in released if not SEMVER.match(v)]
    errors += [f"CHANGELOG: versão inválida '{v}'" for v in bad]
    vers = [v for v, _, _ in released if v not in bad]
    if len(set(vers)) != len(vers):
        errors.append("CHANGELOG: versão repetida")
    if vers != sorted(vers, key=_key, reverse=True):
        errors.append("CHANGELOG: versões fora de ordem (mais nova primeiro)")
    sec = next(((d, b) for v, d, b in released if v == version), None)
    if sec is None:
        errors.append(f"CHANGELOG.md sem a seção '## [{version}] - AAAA-MM-DD'")
        return errors, ""
    date, body = sec
    if not date:
        errors.append(f"CHANGELOG: a seção {version} precisa da data (## [{version}] - AAAA-MM-DD)")
    if not re.search(r"^### ", body, re.M) or len(body) < 20:
        errors.append(f"CHANGELOG: a seção {version} está vazia")
    return errors, body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag")
    ap.add_argument("--notes", type=Path)
    ap.add_argument("--root", type=Path, default=ROOT)
    a = ap.parse_args(argv)
    version = (a.root / "VERSION").read_text().strip()
    errors, body = check(version, (a.root / "CHANGELOG.md").read_text(encoding="utf-8"), a.tag)
    for e in errors:
        print(f"✗ {e}", file=sys.stderr)
    if errors:
        return 1
    if a.notes:
        a.notes.write_text(body + "\n", encoding="utf-8")
    print(f"✓ release {version} consistente" + (f" com a tag {a.tag}" if a.tag else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
