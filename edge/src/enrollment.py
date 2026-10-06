"""
Cadastro (enrollment) do gateway na frota do servidor (docs/gateways.md).

Primeira partida de um gateway novo na fábrica:

    ENROLL_CODE=ABCD-EFGH-JKLM  SERVER_URL=https://app.factoryconnect.tech  python -m src.gateway

1. sem credencial salva em `GATEWAY_CREDENTIALS_PATH` (padrão `/data/gateway_credentials.json` na imagem), o gateway
   chama `POST {SERVER_URL}/api/gateways/enroll {"code": ENROLL_CODE}`;
2. a resposta `{gateway_id, tenant_id, token, mqtt: {username, password, topic_prefix}, ...}` é gravada no disco com
   permissão **0600** (diretório 0700; escrita atômica tmp + rename). O código é de uso único: depois disso o
   `ENROLL_CODE` é ignorado (pode sair do ambiente);
3. o gateway passa a usar `gateway_id` + `token` na configuração remota (`GET /api/gateways/{id}/config`) e no
   heartbeat (`POST /api/gateways/{id}/heartbeat`), e o usuário/senha MQTT do gateway (`MQTT_USERNAME`/
   `MQTT_PASSWORD` no ambiente têm precedência).

Segurança:
- `SERVER_URL` precisa ser `https://` (o token e a senha MQTT viajam na resposta). `http://` só para localhost ou com
  `ALLOW_INSECURE_HTTP=true` (rede de testes).
- O token e a senha nunca vão para o log; o arquivo é recusado (com correção para 0600) se outros usuários puderem
  ler. Token revogado no servidor (401 na config/heartbeat): apague o arquivo e cadastre de novo com um código novo.
- Código inválido/expirado (401) ou bloqueado (429) é erro definitivo (`EnrollmentError(retry=False)`); servidor
  fora do ar/5xx → o gateway tenta de novo com espera crescente (5 s … 60 s).

Linha de comando (cadastro manual, sem subir o gateway):
    python -m edge.src.enrollment --server https://app.factoryconnect.tech --code ABCD-EFGH-JKLM \\
        [--credentials /data/gateway_credentials.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

import aiohttp
import structlog

log = structlog.get_logger()

CREDENTIALS_VERSION = 1
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class EnrollmentError(RuntimeError):
    def __init__(self, message: str, retry: bool = False) -> None:
        super().__init__(message)
        self.retry = retry


@dataclass
class Credentials:
    gateway_id: str
    token: str
    server_url: str
    tenant_id: str = ""
    mqtt_username: str = ""
    mqtt_password: str = ""
    topic_prefix: str = ""
    enrolled_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    version: int = CREDENTIALS_VERSION

    def redacted(self) -> dict:
        d = asdict(self)
        d["token"] = (self.token[:8] + "…") if self.token else ""
        d["mqtt_password"] = "***" if self.mqtt_password else ""
        return d


def base_url(url: str) -> str:
    """URL base do servidor (aceita também a URL da config, `.../api/gateways/...`)."""
    url = url.strip().rstrip("/")
    i = url.find("/api/")
    return url[:i] if i >= 0 else url


def check_server_url(url: str, allow_insecure: bool = False) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise EnrollmentError(f"SERVER_URL inválida: {url!r} (use https://host)")
    if parts.scheme == "http" and parts.hostname not in LOCAL_HOSTS and not allow_insecure:
        raise EnrollmentError("SERVER_URL em http:// expõe o token do gateway: use https:// "
                              "(ou ALLOW_INSECURE_HTTP=true só em rede de testes)")
    return base_url(url)


# ── arquivo de credenciais ────────────────────────────────────────────────────

def save_credentials(path: str | Path, creds: Credentials) -> None:
    """Grava com permissão 0600 (diretório criado 0700), atômico: nunca existe um arquivo legível por outros."""
    path = Path(path)
    if not path.parent.exists():
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".gwcred-", dir=str(path.parent))   # mkstemp cria com 0600
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(creds), f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_credentials(path: str | Path) -> Optional[Credentials]:
    """Credencial salva (None = não cadastrado). Arquivo legível por grupo/outros é corrigido para 0600 com aviso."""
    path = Path(path)
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        log.warning("enrollment.credentials_permissions_fixed", path=str(path), mode=oct(st.st_mode & 0o777))
        os.chmod(path, 0o600)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise EnrollmentError(f"arquivo de credenciais ilegível ({path}): {e}") from None
    if not isinstance(raw, dict) or not raw.get("gateway_id") or not raw.get("token"):
        raise EnrollmentError(f"arquivo de credenciais inválido ({path}): sem gateway_id/token")
    known = set(Credentials.__dataclass_fields__)
    return Credentials(**{k: v for k, v in raw.items() if k in known})


# ── cadastro ───────────────────────────────────────────────────────────────────

def _parse_response(body: Any, server: str) -> Credentials:
    if not isinstance(body, dict) or not body.get("gateway_id") or not body.get("token"):
        raise EnrollmentError("resposta do cadastro sem gateway_id/token", retry=False)
    mqtt = body.get("mqtt") or {}
    return Credentials(gateway_id=str(body["gateway_id"]), token=str(body["token"]), server_url=server,
                       tenant_id=str(body.get("tenant_id") or ""), mqtt_username=str(mqtt.get("username") or ""),
                       mqtt_password=str(mqtt.get("password") or ""),
                       topic_prefix=str(mqtt.get("topic_prefix") or ""))


async def enroll(server_url: str, code: str, *, allow_insecure: bool = False, timeout_s: float = 15.0,
                 session: Optional[aiohttp.ClientSession] = None) -> Credentials:
    """Troca o código de cadastro pelas credenciais do gateway (não grava nada)."""
    server = check_server_url(server_url, allow_insecure)
    code = (code or "").strip()
    if not code:
        raise EnrollmentError("ENROLL_CODE vazio")
    own = session is None
    sess = session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_s), trust_env=True)
    try:
        async with sess.post(f"{server}/api/gateways/enroll", json={"code": code},
                             headers={"Accept": "application/json"}) as r:
            text = await r.text()
            if r.status == 200:
                try:
                    return _parse_response(json.loads(text), server)
                except ValueError:
                    raise EnrollmentError("resposta do cadastro não é JSON", retry=True) from None
            if r.status in (400, 401, 403, 404, 422):
                raise EnrollmentError(f"cadastro recusado (HTTP {r.status}): código inválido, expirado ou já usado "
                                      "— gere um novo código no servidor", retry=False)
            if r.status == 429:
                raise EnrollmentError("cadastro bloqueado por excesso de tentativas (HTTP 429)", retry=False)
            raise EnrollmentError(f"servidor respondeu HTTP {r.status} no cadastro", retry=True)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
        raise EnrollmentError(f"servidor inacessível no cadastro: {type(e).__name__}", retry=True) from None
    finally:
        if own:
            await sess.close()


async def ensure_enrolled(credentials_path: str, server_url: str = "", code: str = "", *,
                          allow_insecure: bool = False, retry_delays: tuple[float, ...] = (5, 10, 20, 40, 60),
                          max_attempts: Optional[int] = None,
                          session: Optional[aiohttp.ClientSession] = None) -> Optional[Credentials]:
    """Credencial salva, ou cadastra com `code` (tentando de novo enquanto o erro for transitório).
    None = gateway sem cadastro (modo legado: mapa em arquivo + TENANT_ID/GATEWAY_TOKEN do ambiente)."""
    creds = load_credentials(credentials_path) if credentials_path else None
    if creds is not None:
        if code:
            log.info("enrollment.already_enrolled", gateway_id=creds.gateway_id,
                     hint="ENROLL_CODE ignorado (uso único); pode removê-lo do ambiente")
        return creds
    if not code:
        return None
    if not server_url:
        raise EnrollmentError("ENROLL_CODE exige SERVER_URL (URL do servidor FactoryConnect)")
    attempt = 0
    while True:
        attempt += 1
        try:
            creds = await enroll(server_url, code, allow_insecure=allow_insecure, session=session)
            break
        except EnrollmentError as e:
            if not e.retry or (max_attempts is not None and attempt >= max_attempts):
                raise
            delay = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
            log.warning("enrollment.retry", error=str(e), attempt=attempt, retry_in_s=delay)
            await asyncio.sleep(delay)
    save_credentials(credentials_path, creds)
    log.info("enrollment.done", gateway_id=creds.gateway_id, tenant_id=creds.tenant_id, path=credentials_path,
             mqtt_username=creds.mqtt_username or None)
    return creds


def main(argv: Optional[list[str]] = None) -> int:
    env = os.environ.get
    p = argparse.ArgumentParser(prog="enrollment", description="Cadastra este gateway no servidor FactoryConnect")
    p.add_argument("--server", default=env("SERVER_URL", ""), help="URL do servidor (https://...)")
    p.add_argument("--code", default=env("ENROLL_CODE", ""), help="código de cadastro (XXXX-XXXX-XXXX)")
    p.add_argument("--credentials", default=env("GATEWAY_CREDENTIALS_PATH", "/data/gateway_credentials.json"))
    p.add_argument("--allow-insecure-http", action="store_true",
                   default=env("ALLOW_INSECURE_HTTP", "").lower() in ("1", "true", "yes"))
    a = p.parse_args(argv)
    try:
        if load_credentials(a.credentials) is not None:
            print(f"já cadastrado ({a.credentials}); apague o arquivo para cadastrar de novo", file=sys.stderr)
            return 1
        creds = asyncio.run(ensure_enrolled(a.credentials, a.server, a.code, allow_insecure=a.allow_insecure_http,
                                            max_attempts=1))
    except EnrollmentError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2
    if creds is None:
        print("erro: informe --code", file=sys.stderr)
        return 2
    print(json.dumps(creds.redacted(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
