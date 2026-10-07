"""Staging no MESMO VPS da produção (infra/docker-compose.staging.yml): nada compartilhado com a produção.

    .venv/bin/python -m pytest -q infra/tests/test_compose_staging.py
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="precisa de docker compose")


def _config(compose: str, env_file: str, *profiles: str) -> dict:
    cmd = ["docker", "compose", "--env-file", str(ROOT / env_file), "-f", str(ROOT / compose)]
    for p in profiles:
        cmd += ["--profile", p]
    return json.loads(subprocess.run(cmd + ["config", "--format", "json"], capture_output=True, text=True,
                                     check=True).stdout)


@pytest.fixture(scope="module")
def prod():
    return _config("infra/docker-compose.prod.yml", ".env.prod.example", "monitoring", "demo")


@pytest.fixture(scope="module")
def stg():
    return _config("infra/docker-compose.staging.yml", ".env.staging.example")


def _routers(cfg: dict) -> set[str]:
    out = set()
    for s in cfg["services"].values():
        for k in (s.get("labels") or {}):
            m = re.match(r"traefik\.http\.(routers|middlewares|services)\.([^.]+)\.", k)
            if m:
                out.add(f"{m.group(1)}:{m.group(2)}")
    return out


def _host_ports(cfg: dict) -> set[str]:
    return {f"{p.get('host_ip', '0.0.0.0')}:{p['published']}" for s in cfg["services"].values()
            for p in s.get("ports", []) if p.get("published")}


def test_nothing_shared_with_production(prod, stg):
    assert stg["name"] == "fm-staging" and prod["name"] != stg["name"]
    names = lambda c: {s["container_name"] for s in c["services"].values()}          # noqa: E731
    assert not names(prod) & names(stg)
    assert all(n.startswith("fm_stg_") for n in names(stg))
    vols = lambda c: {v["name"] for v in c["volumes"].values()}                       # noqa: E731
    assert not vols(prod) & vols(stg) and all(v.startswith("fm-staging_") for v in vols(stg))
    assert not _routers(prod) & _routers(stg), "router/middleware Traefik com o mesmo nome sobrescreve a produção"
    assert not _host_ports(prod) & _host_ports(stg)
    assert all(p.startswith("127.0.0.1:") for p in _host_ports(stg)), "staging não publica porta nenhuma"


def test_staging_routes_only_its_own_host_and_is_not_indexed(stg):
    labels = {k: v for s in stg["services"].values() for k, v in (s.get("labels") or {}).items()}
    rules = [v for k, v in labels.items() if k.endswith(".rule")]
    assert rules and all("Host(`staging.factoryconnect.tech`)" in r for r in rules)
    for r in ("fm-stg-api", "fm-stg-web"):
        assert "fm-stg-noindex" in labels[f"traefik.http.routers.{r}.middlewares"]
    assert labels["traefik.http.middlewares.fm-stg-noindex.headers.customresponseheaders.X-Robots-Tag"].startswith(
        "noindex")


def test_staging_runs_the_same_images_as_production(prod, stg):
    for name in ("server", "web"):
        assert stg["services"][name]["image"] == prod["services"][name]["image"]
    # banco e broker próprios, com credenciais do .env de staging
    assert stg["services"]["postgres"]["environment"]["POSTGRES_DB"] == "factory_monitor_staging"
    assert stg["services"]["server"]["environment"]["DEMO_BOT_ENABLED"] == "false"


def test_staging_env_example_never_reuses_production_values():
    p = dict(re.findall(r"^([A-Z_]+)=([^\s#]*)", (ROOT / ".env.prod.example").read_text(), re.M))
    s = dict(re.findall(r"^([A-Z_]+)=([^\s#]*)", (ROOT / ".env.staging.example").read_text(), re.M))
    for k in ("POSTGRES_DB", "POSTGRES_USER", "CORS_ORIGINS"):
        assert s.get(k) and s[k] != p.get(k), k
    for k in ("APP_SECRET_KEY", "POSTGRES_PASSWORD", "INFLUX_TOKEN", "MQTT_PASSWORD"):
        assert k in s, k
