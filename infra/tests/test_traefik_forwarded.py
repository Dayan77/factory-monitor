"""IP real do cliente atrás do Traefik (docker) + regra do backend (ProxyHeadersMiddleware).

Prova, com Traefik v3 e v2 de verdade, que um X-Forwarded-For forjado pelo cliente não vira o IP
usado no rate limit de leads:
  - Traefik sem forwardedHeaders (padrão do Easypanel) descarta o XFF do cliente e põe o IP real;
  - mesmo com forwardedHeaders.insecure=true (config ERRADA), o backend escolhe a entrada mais à
    direita que não é proxy confiável → continua sendo o IP real;
  - e documenta por que FORWARDED_ALLOW_IPS="*" é proibido (aí o forjado vence).
O backend aqui é um nginx que ecoa os cabeçalhos; a decisão do IP usa o mesmo middleware do
server/src/main.py (uvicorn ProxyHeadersMiddleware com trusted_hosts = FORWARDED_ALLOW_IPS).

    .venv/bin/python -m pytest -q infra/tests/test_traefik_forwarded.py
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
import uuid

import pytest

uvicorn_proxy = pytest.importorskip("uvicorn.middleware.proxy_headers")

IMAGES = ["traefik:v3.6.2", "traefik:v2.11.31"]
NGINX = "nginx:1.30.5-alpine"
SPOOF = "203.0.113.66"

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="precisa de docker")

ECHO_CONF = (
    "server { listen 80; location / { default_type application/json; "
    'return 200 "{\\"xff\\": \\"$http_x_forwarded_for\\", \\"real\\": \\"$http_x_real_ip\\", '
    '\\"peer\\": \\"$remote_addr\\"}"; } }'
)


def sh(*args, check=True, **kw):
    return subprocess.run(args, check=check, capture_output=True, text=True, **kw)


def ip_of(name: str, net: str) -> str:
    return sh("docker", "inspect", "-f", f'{{{{(index .NetworkSettings.Networks "{net}").IPAddress}}}}',
              name).stdout.strip()


def client_ip_seen_by_app(xff: str, peer: str, trusted: list[str]) -> str:
    """Mesmo cálculo do server: ProxyHeadersMiddleware(trusted_hosts=FORWARDED_ALLOW_IPS)."""
    seen = {}

    async def app(scope, receive, send):
        seen["client"] = scope["client"][0]

    mw = uvicorn_proxy.ProxyHeadersMiddleware(app, trusted_hosts=trusted)
    scope = {"type": "http", "client": (peer, 1234), "scheme": "http",
             "headers": [(b"x-forwarded-for", xff.encode())] if xff else []}
    asyncio.run(mw(scope, None, None))
    return seen["client"]


@pytest.fixture(scope="module", params=[(img, insecure) for img in IMAGES for insecure in (False, True)],
                ids=lambda p: f"{p[0].split(':')[1]}{'-insecure' if p[1] else ''}")
def stack(request):
    image, insecure = request.param
    tag = f"fmxff{uuid.uuid4().hex[:8]}"
    net = f"{tag}_net"
    names = [f"{tag}_backend", f"{tag}_traefik", f"{tag}_client"]
    sh("docker", "network", "create", net)
    try:
        sh("docker", "run", "-d", "--name", names[0], "--network", net, "--network-alias", "backend",
           "--entrypoint", "sh", NGINX, "-c",
           f"printf '%s' '{ECHO_CONF}' > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'")
        dyn = ("http:\n  routers:\n    app:\n      rule: \"PathPrefix(`/`)\"\n      entryPoints: [web]\n"
               "      service: app\n  services:\n    app:\n      loadBalancer:\n        servers:\n"
               "          - url: \"http://backend:80\"\n")
        args = ["--entryPoints.web.address=:80", "--providers.file.filename=/dyn.yml", "--log.level=ERROR"]
        if insecure:
            args.append("--entryPoints.web.forwardedHeaders.insecure=true")
        sh("docker", "run", "-d", "--name", names[1], "--network", net, "--network-alias", "proxy",
           "--entrypoint", "sh", image, "-c",
           f"printf '%s' '{dyn}' > /dyn.yml && exec traefik {' '.join(args)}")
        sh("docker", "run", "-d", "--name", names[2], "--network", net, "--entrypoint", "sleep", NGINX, "300")
        for _ in range(40):
            r = sh("docker", "exec", names[2], "curl", "-fsS", "-m", "2", "http://proxy/", check=False)
            if r.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise AssertionError(sh("docker", "logs", names[1], check=False).stderr)
        yield {"client": names[2], "net": net, "insecure": insecure,
               "traefik_ip": ip_of(names[1], net), "client_ip": ip_of(names[2], net)}
    finally:
        for n in names:
            sh("docker", "rm", "-f", n, check=False)
        sh("docker", "network", "rm", net, check=False)


def request(stack, *headers: str) -> dict:
    args = ["docker", "exec", stack["client"], "curl", "-fsS", "-m", "5"]
    for h in headers:
        args += ["-H", h]
    return json.loads(sh(*args, "http://proxy/").stdout)


@pytest.mark.parametrize("spoof_headers", [
    [f"X-Forwarded-For: {SPOOF}"],
    [f"X-Forwarded-For: {SPOOF}, 10.0.0.1"],
    [f"X-Real-IP: {SPOOF}", f"X-Forwarded-For: {SPOOF}"],
])
def test_spoofed_xff_never_becomes_the_client_ip(stack, spoof_headers):
    seen = request(stack, *spoof_headers)
    assert seen["peer"] == stack["traefik_ip"]
    entries = [e.strip() for e in seen["xff"].split(",")]
    # o Traefik sempre acrescenta o IP real do cliente como entrada mais à direita
    assert entries[-1] == stack["client_ip"]
    if not stack["insecure"]:
        # padrão (Easypanel sem forwardedHeaders): o XFF do cliente é descartado
        assert SPOOF not in seen["xff"] and seen["real"] == stack["client_ip"]
    # Produção: FORWARDED_ALLOW_IPS = rede/IP do Traefik
    trusted = [stack["traefik_ip"]]
    assert client_ip_seen_by_app(seen["xff"], seen["peer"], trusted) == stack["client_ip"]


def test_without_proxy_headers_client_ip_is_the_peer(stack):
    seen = request(stack)
    assert seen["xff"] == stack["client_ip"]
    assert client_ip_seen_by_app(seen["xff"], seen["peer"], [stack["traefik_ip"]]) == stack["client_ip"]


def test_trusting_everything_lets_the_spoof_win(stack):
    """Por que FORWARDED_ALLOW_IPS='*' é proibido (server/src/main.py recusa subir com ele)."""
    if not stack["insecure"]:
        pytest.skip("o Traefik padrão já descarta o XFF do cliente")
    seen = request(stack, f"X-Forwarded-For: {SPOOF}")
    assert client_ip_seen_by_app(seen["xff"], seen["peer"], ["*"]) == SPOOF


def test_uvicorn_never_trusts_every_proxy():
    """server/Dockerfile e compose: uvicorn sem proxy headers; a confiança fica no app (FORWARDED_ALLOW_IPS)."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "server/Dockerfile").read_text()
    cmd = next(line for line in dockerfile.splitlines() if line.startswith("CMD"))
    assert "--no-proxy-headers" in cmd and "forwarded-allow-ips" not in cmd
    dev = (root / "infra/docker-compose.yml").read_text()
    assert "uvicorn src.main:app" in dev and "--no-proxy-headers" in dev
    for f in ("infra/docker-compose.yml", "infra/docker-compose.prod.yml", ".env.example", ".env.prod.example"):
        text = (root / f).read_text()
        assert 'forwarded-allow-ips "*"' not in text and "FORWARDED_ALLOW_IPS=*" not in text
        assert "FORWARDED_ALLOW_IPS: \"*\"" not in text and "FORWARDED_ALLOW_IPS: '*'" not in text
