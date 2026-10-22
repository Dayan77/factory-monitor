"""Cabeçalhos de segurança e TLS dos nginx de produção — com nginx DE VERDADE (docker) e Chromium.

- web/nginx.conf + web/nginx/security-headers.conf (app, atrás do Traefik): CSP sem 'unsafe-inline' em script-src,
  frame-ancestors 'none', HSTS pronto para preload, nosniff, Referrer-Policy, Permissions-Policy — em TODA resposta
  (inclusive nos location{} com add_header próprio, onde o nginx descartaria os herdados), sem duplicar cabeçalho;
- site/nginx.conf renderizado por site/scripts/render-nginx.mjs: hash CSP de cada script inline do build; um
  script inline sem hash é BLOQUEADO pelo navegador;
- Chromium carrega as páginas (dist real quando existe, senão um build mínimo) sem nenhuma violação de CSP;
- infra/nginx/nginx.prod.conf (alternativa ao Traefik): nginx -t, só TLS 1.2/1.3, HSTS preload na borda.

    .venv/bin/python -m pytest -q infra/tests/test_security_headers.py
Portas 127.0.0.1:FM_TEST_HDR_PORT..+3 (padrão 8150-8153). Browser: PLAYWRIGHT_MODULE / CHROMIUM_PATH (opcional).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NGINX = "nginx:1.30.5-alpine"
BASE_PORT = int(os.environ.get("FM_TEST_HDR_PORT", "8150"))
pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="precisa de docker")

REQUIRED = {
    "strict-transport-security": re.compile(r"^max-age=(\d+); includeSubDomains; preload$"),
    "x-content-type-options": re.compile(r"^nosniff$"),
    "x-frame-options": re.compile(r"^DENY$"),
    "referrer-policy": re.compile(r"^strict-origin-when-cross-origin$"),
    "permissions-policy": re.compile(r"microphone=\(\).*geolocation=\(\)"),
    "content-security-policy": re.compile(r"frame-ancestors 'none'"),
}


def sh(*args, check=True, **kw):
    return subprocess.run(args, check=check, capture_output=True, text=True, **kw)


def fetch(url: str) -> tuple[int, dict[str, list[str]], str]:
    req = urllib.request.Request(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    try:
        r = opener.open(req, timeout=5)
    except urllib.error.HTTPError as e:   # 3xx/4xx também trazem cabeçalhos
        r = e
    headers: dict[str, list[str]] = {}
    for k, v in r.headers.items():
        headers.setdefault(k.lower(), []).append(v)
    return r.status if hasattr(r, "status") else r.code, headers, r.read().decode("utf-8", "replace")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def csp_directives(csp: str) -> dict[str, list[str]]:
    out = {}
    for part in csp.split(";"):
        bits = part.split()
        if bits:
            out[bits[0]] = bits[1:]
    return out


def assert_security_headers(url: str, headers: dict[str, list[str]]):
    for name, rx in REQUIRED.items():
        vals = headers.get(name, [])
        assert len(vals) == 1, f"{url}: {name} {'ausente' if not vals else 'duplicado: ' + repr(vals)}"
        assert rx.search(vals[0]), f"{url}: {name}={vals[0]!r}"
    assert int(REQUIRED["strict-transport-security"].match(headers["strict-transport-security"][0]).group(1)) \
        >= 31536000, "HSTS preload exige max-age >= 1 ano"
    d = csp_directives(headers["content-security-policy"][0])
    for k in ("default-src", "script-src", "object-src", "base-uri", "frame-ancestors"):
        assert k in d, f"{url}: CSP sem {k}"
    assert "'unsafe-inline'" not in d["script-src"] and "'unsafe-eval'" not in d["script-src"], d["script-src"]
    assert "*" not in d["script-src"] and "*" not in d.get("connect-src", []), url
    assert d["object-src"] == ["'none'"] and d["frame-ancestors"] == ["'none'"]


def wait_up(url: str, tries: int = 40):
    for _ in range(tries):
        try:
            fetch(url)
            return
        except Exception:  # noqa: BLE001
            time.sleep(0.25)
    raise AssertionError(f"nginx não respondeu: {url}")


class Nginx:
    def __init__(self, port: int, mounts: list[tuple[Path, str]], extra: list[str] | None = None):
        self.name = f"fmhdr_{uuid.uuid4().hex[:8]}"
        args = ["docker", "run", "-d", "--name", self.name, "-p", f"127.0.0.1:{port}:80"]
        for src, dst in mounts:
            args += ["-v", f"{src}:{dst}:ro"]
        sh(*args, *(extra or []), NGINX)
        self.base = f"http://127.0.0.1:{port}"
        try:
            wait_up(self.base + "/healthz")
        except AssertionError:
            logs = sh("docker", "logs", self.name, check=False)
            self.stop()
            raise AssertionError(logs.stdout + logs.stderr)

    def stop(self):
        sh("docker", "rm", "-f", self.name, check=False)


def playwright_available() -> bool:
    if shutil.which("node") is None:
        return False
    if os.environ.get("PLAYWRIGHT_MODULE") or Path("/opt/node-tools/node_modules/playwright/index.mjs").exists():
        return True
    return (ROOT / "site/node_modules/playwright").exists()


def browser_check(urls: list[str]) -> dict:
    r = sh("node", str(ROOT / "infra/tests/csp_browser.mjs"), *urls, cwd=ROOT / "site", check=False, timeout=180)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


# ── App (web) ────────────────────────────────────────────────────────────────────────────────────────────────


def web_dist(tmp: Path) -> Path:
    real = ROOT / "web/dist"
    if (real / "index.html").exists() and os.environ.get("FM_HDR_FIXTURE") != "1":
        return real
    d = tmp / "web-dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text('<!doctype html><html><head><script src="/theme-init.js"></script>'
                                  '<script type="module" src="/assets/app-abc123.js"></script></head>'
                                  '<body><div id="root"></div></body></html>')
    (d / "theme-init.js").write_text("document.documentElement.dataset.theme='dark';")
    (d / "assets/app-abc123.js").write_text("document.getElementById('root').textContent='app ok';")
    (d / "sw.js").write_text("self.addEventListener('fetch',()=>{});")
    (d / "manifest.webmanifest").write_text("{}")
    return d


@pytest.fixture(scope="module")
def web(tmp_path_factory):
    dist = web_dist(tmp_path_factory.mktemp("web"))
    n = Nginx(BASE_PORT, [(ROOT / "web/nginx.conf", "/etc/nginx/conf.d/default.conf"),
                          (ROOT / "web/nginx/security-headers.conf", "/etc/nginx/snippets/security-headers.conf"),
                          (dist, "/usr/share/nginx/html")])
    yield n, dist
    n.stop()


def test_web_every_response_carries_the_security_headers(web):
    n, dist = web
    asset = next((dist / "assets").glob("*.js")).name
    paths = ["/", "/login", "/factory/lines/x", "/sw.js", "/manifest.webmanifest", "/theme-init.js",
             f"/assets/{asset}", "/favicon.svg" if (dist / "favicon.svg").exists() else "/theme-init.js"]
    for p in paths:
        code, h, _ = fetch(n.base + p)
        assert code == 200, (p, code)
        assert_security_headers(p, h)
        assert "server" not in h or "nginx/" not in h["server"][0], "server_tokens off"


def test_web_csp_allows_only_what_the_app_uses(web):
    n, _ = web
    _, h, _ = fetch(n.base + "/")
    d = csp_directives(h["content-security-policy"][0])
    assert d["script-src"] == ["'self'"]
    assert d["connect-src"] == ["'self'", "wss://127.0.0.1"], d["connect-src"]   # wss://$host
    assert "blob:" in d["worker-src"] and "blob:" in d["media-src"]                 # hls.js
    assert "camera=(self)" in h["permissions-policy"][0]                           # leitor de código de barras


def test_web_cache_policy(web):
    n, dist = web
    asset = next((dist / "assets").glob("*.js")).name
    assert fetch(n.base + f"/assets/{asset}")[1]["cache-control"] == ["public, max-age=31536000, immutable"]
    for p in ("/", "/login", "/sw.js", "/theme-init.js"):
        assert fetch(n.base + p)[1]["cache-control"] == ["no-cache"], p


@pytest.mark.skipif(not playwright_available(), reason="precisa de node + Playwright")
def test_web_app_renders_in_chromium_without_csp_violations(web):
    n, _ = web
    res = browser_check([n.base + "/login", n.base + "/"])
    for url, r in res.items():
        assert not r["violations"] and not r["console"], (url, r)
        assert r["ok"], (url, r)


# ── Site público ─────────────────────────────────────────────────────────────────────────────────────────────


def site_fixture(tmp: Path) -> Path:
    d = tmp / "site-dist"
    (d / "_astro").mkdir(parents=True)
    (d / "index.html").write_text(
        '<!doctype html><html class="dark"><head><script>document.documentElement.classList.add("js")</script>'
        '<script type="application/ld+json">{"@type":"Organization"}</script></head>'
        '<body><main id="m">site</main><script type="module">document.getElementById("m").dataset.ok="1"</script>'
        "</body></html>")
    (d / "404.html").write_text("<!doctype html><title>404</title><p>não encontrada</p>")
    (d / "solucao.html").write_text("<!doctype html><p>solução</p>")
    return d


def render_site(dist: Path, out: Path) -> dict[str, list[str]]:
    sh("node", str(ROOT / "site/scripts/render-nginx.mjs"), "--dist", str(dist), "--out", str(out),
       "--connect-src", "https://app.factoryconnect.tech")
    hdr = (out / "security-headers.conf").read_text()
    assert "__" not in hdr, "placeholder não substituído"
    csp = re.search(r'Content-Security-Policy "([^"]+)"', hdr).group(1)
    return csp_directives(csp)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    if shutil.which("node") is None:
        pytest.skip("precisa de node (site/scripts/render-nginx.mjs)")
    tmp = tmp_path_factory.mktemp("site")
    real = ROOT / "site/dist"
    dist = real if (real / "index.html").exists() and os.environ.get("FM_HDR_FIXTURE") != "1" else site_fixture(tmp)
    csp = render_site(dist, tmp / "ngx")
    n = Nginx(BASE_PORT + 1, [(tmp / "ngx/default.conf", "/etc/nginx/conf.d/default.conf"),
                              (tmp / "ngx/security-headers.conf", "/etc/nginx/snippets/security-headers.conf"),
                              (dist, "/usr/share/nginx/html")])
    yield n, dist, csp
    n.stop()


def test_site_csp_hashes_every_inline_script_and_nothing_else(site):
    _, dist, csp = site
    hashes = [s for s in csp["script-src"] if s.startswith("'sha256-")]
    assert csp["script-src"][0] == "'self'" and len(hashes) == len(csp["script-src"]) - 1
    import base64
    import hashlib
    want = set()
    for f in dist.rglob("*.html"):
        for attrs, body in re.findall(r"<script\b([^>]*)>(.*?)</script>", f.read_text(), re.S):
            if "src=" in attrs or re.search(r"type=[\"']?application/(ld\+)?json", attrs):
                continue
            want.add("'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'")
    assert set(hashes) == want and want, "hash CSP diferente dos scripts inline do build"
    assert csp["connect-src"] == ["'self'", "https://app.factoryconnect.tech"]


def test_site_every_response_carries_the_security_headers(site):
    n, dist, _ = site
    for p in ("/", "/solucao", "/nao-existe"):
        code, h, _ = fetch(n.base + p)
        assert code in (200, 404), (p, code)
        assert_security_headers(p, h)
    code, h, _ = fetch(n.base + "/solucao.html")
    assert code == 301
    for f in [*dist.glob("_astro/*")][:1] + [*dist.glob("*.svg")][:1]:
        _, h, _ = fetch(n.base + "/" + str(f.relative_to(dist)))
        assert_security_headers(f.name, h)


@pytest.mark.skipif(not playwright_available(), reason="precisa de node + Playwright")
def test_site_renders_without_violations_and_unhashed_inline_script_is_blocked(site, tmp_path):
    n, dist, _ = site
    pages = ["/", "/contato"] if (dist / "contato.html").exists() else ["/"]
    res = browser_check([n.base + p for p in pages])
    for url, r in res.items():
        assert r["ok"] and not r["violations"] and not r["console"], (url, r)
    # mesmo CSP, página com um script inline que NÃO estava no build → o navegador bloqueia
    evil = tmp_path / "evil"
    shutil.copytree(dist, evil)
    (evil / "index.html").write_text((evil / "index.html").read_text().replace(
        "</body>", "<script>document.title='injetado'</script></body>", 1))
    ngx = tmp_path / "ngx"
    render_site(dist, ngx)                      # hashes do build original
    m = Nginx(BASE_PORT + 2, [(ngx / "default.conf", "/etc/nginx/conf.d/default.conf"),
                              (ngx / "security-headers.conf", "/etc/nginx/snippets/security-headers.conf"),
                              (evil, "/usr/share/nginx/html")])
    try:
        r = browser_check([m.base + "/"])[m.base + "/"]
        assert any("script-src" in v for v in r["violations"]), r
    finally:
        m.stop()


# ── nginx.prod.conf (borda TLS alternativa ao Traefik) ──────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def edge(tmp_path_factory):
    if shutil.which("openssl") is None or shutil.which("curl") is None:
        pytest.skip("precisa de openssl e curl")
    le = tmp_path_factory.mktemp("le")
    live = le / "live/factoryconnect.tech"
    live.mkdir(parents=True)
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=factoryconnect.tech",
       "-addext", "subjectAltName=DNS:factoryconnect.tech,DNS:www.factoryconnect.tech,DNS:app.factoryconnect.tech",
       "-keyout", str(live / "privkey.pem"), "-out", str(live / "fullchain.pem"))
    for f in live.iterdir():
        f.chmod(0o644)
    name = f"fmhdr_edge_{uuid.uuid4().hex[:8]}"
    port = BASE_PORT + 3
    hosts = ["--add-host", "server:127.0.0.1", "--add-host", "web:127.0.0.1", "--add-host", "site:127.0.0.1"]
    mounts = ["-v", f"{ROOT / 'infra/nginx/nginx.prod.conf'}:/etc/nginx/nginx.conf:ro", "-v", f"{le}:/etc/letsencrypt:ro"]
    t = sh("docker", "run", "--rm", *hosts, *mounts, NGINX, "nginx", "-t", check=False)
    assert t.returncode == 0, t.stderr
    sh("docker", "run", "-d", "--name", name, "-p", f"127.0.0.1:{port}:443", *hosts, *mounts, NGINX)
    yield port
    sh("docker", "rm", "-f", name, check=False)


def curl_headers(port: int, host: str, path: str = "/", *extra: str) -> tuple[int, str]:
    r = sh("curl", "--noproxy", "*", "-sk", "-o", "/dev/null", "-D", "-", *extra, "--resolve",
           f"{host}:{port}:127.0.0.1", f"https://{host}:{port}{path}", check=False)
    return r.returncode, r.stdout


def test_edge_tls_and_hsts(edge):
    for _ in range(40):
        rc, out = curl_headers(edge, "app.factoryconnect.tech")
        if rc == 0:
            break
        time.sleep(0.25)
    for host in ("app.factoryconnect.tech", "factoryconnect.tech", "www.factoryconnect.tech"):
        rc, out = curl_headers(edge, host, "/", "--tlsv1.2", "--tls-max", "1.2")
        assert rc == 0, (host, rc)
        hsts = [ln for ln in out.splitlines() if ln.lower().startswith("strict-transport-security:")]
        assert len(hsts) == 1 and "includeSubDomains; preload" in hsts[0], (host, out)
        assert re.search(r"^server: nginx\r?$", out, re.M | re.I), "server_tokens off (sem versão)"
    rc, _ = curl_headers(edge, "app.factoryconnect.tech", "/", "--tlsv1.3")
    assert rc == 0, "TLS 1.3"
    conf = (ROOT / "infra/nginx/nginx.prod.conf").read_text()
    assert re.search(r"^\s*ssl_protocols\s+TLSv1\.2 TLSv1\.3;", conf, re.M)
    assert "ssl_session_tickets off" in conf


def _labels(compose: str) -> dict[str, str]:
    out = {}
    for line in (ROOT / compose).read_text().splitlines():
        m = re.match(r'\s*-\s*"(traefik\.[^=]+)=(.*)"\s*$', line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


@pytest.mark.parametrize("compose", ["infra/docker-compose.prod.yml", "infra/docker-compose.staging.yml"])
def test_traefik_https_routers_use_tls_and_http_only_redirects(compose):
    """Produção/staging: o TLS termina no Traefik (certresolver); os routers https têm tls=true e os http só
    redirecionam. Os cabeçalhos de segurança vêm dos containers (testados acima) e do server (API)."""
    lab = _labels(compose)
    routers = {k.split(".")[3] for k in lab if k.startswith("traefik.http.routers.")}
    assert routers
    for r in routers:
        ep = lab[f"traefik.http.routers.{r}.entrypoints"]
        if ep == "https":
            assert lab.get(f"traefik.http.routers.{r}.tls") == "true", r
            assert lab.get(f"traefik.http.routers.{r}.tls.certresolver") == "letsencrypt", r
        else:
            assert ep == "http" and "redirect" in lab.get(f"traefik.http.routers.{r}.middlewares", ""), r
