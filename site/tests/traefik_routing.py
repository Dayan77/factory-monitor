#!/usr/bin/env python3
"""Testa de verdade o roteamento de domínios de infra/docker-compose.prod.yml com Traefik.

Sobe um Traefik local (v3 e v2) lendo os labels REAIS de site/web/server do compose de produção
(web e server viram stubs nginx que ecoam a requisição; o site serve site/dist com o nginx gerado por
site/scripts/render-nginx.mjs, como no build da imagem)
e confere: apex → site, www → apex, rotas antigas do app no apex → app.factoryconnect.tech
(mesmo caminho/query, POST → 308), app → web/api e HTTP → HTTPS.

Uso (precisa de docker e de `npm run build` antes):  python3 site/tests/traefik_routing.py
Portas: TRAEFIK_HTTP_PORT (8180) e TRAEFIK_HTTPS_PORT (8143).
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "site"
HTTP_PORT = int(os.environ.get("TRAEFIK_HTTP_PORT", "8180"))
HTTPS_PORT = int(os.environ.get("TRAEFIK_HTTPS_PORT", "8143"))
IMAGES = os.environ.get("TRAEFIK_IMAGES", "traefik:v3.6.2,traefik:v2.11.31").split(",")
PROJECT = "fmroutingtest"
NET = "fmroutingtest_net"


def sh(*args, **kw):
    r = subprocess.run(args, capture_output=True, text=True, **kw)
    if r.returncode:
        raise SystemExit(f"falhou ({r.returncode}): {' '.join(map(str, args))}\n{r.stderr.strip()}")
    return r


def prod_config(tmp: Path) -> dict:
    # .env de exemplo com valores fictícios só para a interpolação
    env = tmp / "prod.env"
    lines = []
    for line in (ROOT / ".env.prod.example").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            lines.append(f"{k.strip()}={v.strip() or 'dummy'}")
    env.write_text("\n".join(lines) + "\n")
    # O compose de produção também lê ../.env (env_file). No CI e em clones novos ele não existe:
    # usa o mesmo .env fictício só durante o `config` e remove em seguida (nunca sobrescreve um .env real).
    root_env = ROOT / ".env"
    created = not root_env.exists()
    if created:
        root_env.write_text(env.read_text())
    try:
        out = sh("docker", "compose", "--env-file", str(env), "-f", str(ROOT / "infra/docker-compose.prod.yml"),
                 "config", "--format", "json").stdout
    finally:
        if created:
            root_env.unlink(missing_ok=True)
    return json.loads(out)


def stub(name: str) -> list[str]:
    conf = ("server { listen 80; location / { default_type text/plain; "
            f'return 200 "{name} $$request_method $$request_uri\\n"; }} }}')
    return ["sh", "-c", f"printf '%s' '{conf}' > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]


def test_compose(cfg: dict, image: str, ngx: Path) -> dict:
    def labels(svc: str, port80: bool = False) -> dict:
        # `config --format json` já devolve "$" escapado como "$$" — reaproveitado sem mexer.
        l = dict(cfg["services"][svc]["labels"])
        l["traefik.docker.network"] = NET
        if port80:
            l = {k: ("80" if k.endswith("loadbalancer.server.port") else v) for k, v in l.items()}
        return l

    return {
        "name": PROJECT,
        "services": {
            "traefik": {
                "image": image,
                "command": ["--providers.docker=true", "--providers.docker.exposedbydefault=false",
                            "--entrypoints.http.address=:80", "--entrypoints.https.address=:443", "--log.level=ERROR"],
                "volumes": ["/var/run/docker.sock:/var/run/docker.sock:ro"],
                "ports": [f"{HTTP_PORT}:80", f"{HTTPS_PORT}:443"],
                "networks": [NET],
            },
            "site": {
                "image": "nginx:1.30.5-alpine",
                "volumes": [f"{ngx / 'default.conf'}:/etc/nginx/conf.d/default.conf:ro",
                            f"{ngx / 'security-headers.conf'}:/etc/nginx/snippets/security-headers.conf:ro",
                            f"{SITE / 'dist'}:/usr/share/nginx/html:ro"],
                "labels": labels("site"), "networks": [NET],
            },
            "web": {"image": "nginx:1.30.5-alpine", "command": stub("WEB"), "labels": labels("web"), "networks": [NET]},
            "server": {"image": "nginx:1.30.5-alpine", "command": stub("API"), "labels": labels("server", True), "networks": [NET]},
        },
        "networks": {NET: {"name": NET}},
    }


def probe(host: str, path: str, method: str = "GET", https: bool = True) -> tuple[int, str, str]:
    port, scheme = (HTTPS_PORT, "https") if https else (HTTP_PORT, "http")
    r = subprocess.run(
        ["curl", "--noproxy", "*", "-sk", "-X", method, "-o", "-", "-w", "\n%{http_code} %{redirect_url}",
         "--resolve", f"{host}:{port}:127.0.0.1", f"{scheme}://{host}:{port}{path}"],
        capture_output=True, text=True,
    )
    body, _, tail = r.stdout.rpartition("\n")
    code, _, loc = tail.partition(" ")
    return int(code or 0), urllib.parse.unquote(loc.strip()), body


APP = "https://app.factoryconnect.tech"
CASES = [
    # (host, path, method, https, expected status, expected Location or body fragment)
    ("factoryconnect.tech", "/", "GET", True, 200, "raio-x"),
    ("factoryconnect.tech", "/solucao", "GET", True, 200, "Solução"),
    ("factoryconnect.tech", "/aircraft", "GET", True, 404, "não encontrada"),       # /ai só casa como /ai ou /ai/...
    ("factoryconnect.tech", "/administracao", "GET", True, 404, "não encontrada"),  # idem /admin
    ("factoryconnect.tech", "/login", "GET", True, 301, f"{APP}/login"),
    ("factoryconnect.tech", "/register", "GET", True, 301, f"{APP}/register"),
    ("factoryconnect.tech", "/dashboard?x=1", "GET", True, 301, f"{APP}/dashboard?x=1"),
    ("factoryconnect.tech", "/factory/machines/abc", "GET", True, 301, f"{APP}/factory/machines/abc"),
    ("factoryconnect.tech", "/machines", "GET", True, 301, f"{APP}/machines"),
    ("factoryconnect.tech", "/lines", "GET", True, 301, f"{APP}/lines"),
    ("factoryconnect.tech", "/sensors", "GET", True, 301, f"{APP}/sensors"),
    ("factoryconnect.tech", "/cameras", "GET", True, 301, f"{APP}/cameras"),
    ("factoryconnect.tech", "/alerts", "GET", True, 301, f"{APP}/alerts"),
    ("factoryconnect.tech", "/ai", "GET", True, 301, f"{APP}/ai"),
    ("factoryconnect.tech", "/admin/users", "GET", True, 301, f"{APP}/admin/users"),
    ("factoryconnect.tech", "/api/auth/login", "POST", True, 308, f"{APP}/api/auth/login"),
    ("factoryconnect.tech", "/ws/sensors/t1", "GET", True, 301, f"{APP}/ws/sensors/t1"),
    ("www.factoryconnect.tech", "/planos?a=b", "GET", True, 301, "https://factoryconnect.tech/planos?a=b"),
    ("www.factoryconnect.tech", "/", "GET", True, 301, "https://factoryconnect.tech/"),
    ("app.factoryconnect.tech", "/", "GET", True, 200, "WEB GET /"),
    ("app.factoryconnect.tech", "/login", "GET", True, 200, "WEB GET /login"),
    ("app.factoryconnect.tech", "/api/public/leads", "POST", True, 200, "API POST /api/public/leads"),
    ("app.factoryconnect.tech", "/ws/sensors/t1", "GET", True, 200, "API GET /ws/sensors/t1"),
    ("factoryconnect.tech", "/contato", "GET", False, 301, "https://factoryconnect.tech/contato"),
    ("www.factoryconnect.tech", "/", "GET", False, 301, "https://www.factoryconnect.tech/"),
    ("app.factoryconnect.tech", "/dashboard", "GET", False, 301, "https://app.factoryconnect.tech/dashboard"),
]


def main() -> int:
    if not (SITE / "dist" / "index.html").exists():
        print("site/dist não existe — rode `npm run build` em site/ antes.")
        return 2
    failures = 0
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cfg = prod_config(tmp)
        ngx = tmp / "nginx"
        sh("node", str(SITE / "scripts/render-nginx.mjs"), "--dist", str(SITE / "dist"), "--out", str(ngx))
        for image in IMAGES:
            print(f"\n[{image}]")
            cf = tmp / "compose.json"
            cf.write_text(json.dumps(test_compose(cfg, image, ngx)))
            try:
                sh("docker", "compose", "-f", str(cf), "up", "-d")
                for _ in range(40):  # espera o Traefik carregar os routers
                    if probe("app.factoryconnect.tech", "/")[0] == 200 and probe("factoryconnect.tech", "/login")[0] == 301:
                        break
                    time.sleep(0.5)
                for host, path, method, https, status, expect in CASES:
                    code, loc, body = probe(host, path, method, https)
                    ok = code == status and (loc == expect if 300 <= status < 400 else expect in body)
                    failures += not ok
                    scheme = "https" if https else "http"
                    got = loc if 300 <= code < 400 else body.strip()[:40].replace("\n", " ")
                    print(f"  {'✓' if ok else '✗'} {method} {scheme}://{host}{path} → {code} {got if not ok or 300 <= code < 400 else ''}")
            finally:
                subprocess.run(["docker", "compose", "-f", str(cf), "down", "-t", "1"], capture_output=True)
    print(f"\n{'OK' if not failures else f'{failures} falha(s)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
