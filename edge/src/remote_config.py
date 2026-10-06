"""
Configuração remota do gateway: o mapa de dispositivos/tags vem do servidor (docs/gateways.md).

Ativada só quando `GATEWAY_CONFIG_URL` está definido; sem ele o gateway continua lendo o arquivo
`PLANT_MAP_PATH` (padrão inalterado).

    GET {GATEWAY_CONFIG_URL}/api/gateways/{gateway_id}/config
    Authorization: Gateway <GATEWAY_TOKEN>
    If-None-Match: "<etag da cópia local>"            (quando há cache)

`GATEWAY_CONFIG_URL` pode ser a URL base do servidor (`https://factoryconnect.tech`), a URL completa
(`.../api/gateways/gw-01/config`) ou um modelo com `{gateway_id}`. O `gateway_id` vem de `GATEWAY_ID` ou, na
falta, do mapa local (`PLANT_MAP_PATH`).

Resposta esperada (200, application/json):
    {"gateway_id": "<uuid>", "revision": "7", "config_version": 7, "plant_map": { ...mapa v1... }}
ou o próprio mapa v1 (edge/src/plant_map.py) no corpo. Cabeçalho `ETag` opcional (vira If-None-Match na próxima
partida; 304 = usar a cópia local). O mapa é validado (`parse_plant_map`) ANTES de ser aceito e gravado; o
`gateway_id` do mapa precisa ser o deste gateway.

Ordem de carga (nunca perder dado: o gateway sobe mesmo sem servidor):
    1. servidor respondeu 200 com mapa válido → usa e grava a cópia local (escrita atômica, sem o token);
    2. 304 → cópia local;
    3. qualquer falha (rede, timeout, 401/403, 5xx, JSON/mapa inválido, gateway_id diferente) → cópia local
       (`GATEWAY_CONFIG_CACHE`), com aviso;
    4. sem cópia local → arquivo `PLANT_MAP_PATH` (se definido);
    5. nada disso → RemoteConfigError (o gateway não sobe sem mapa).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import aiohttp
import structlog

from .plant_map import PlantMap, PlantMapError, load_plant_map, parse_plant_map

log = structlog.get_logger()

CACHE_VERSION = 1


class RemoteConfigError(RuntimeError):
    pass


def config_url(base: str, gateway_id: str) -> str:
    base = base.strip()
    if "{gateway_id}" in base:
        return base.replace("{gateway_id}", gateway_id)
    if base.rstrip("/").endswith("/config"):
        return base.rstrip("/")
    return f"{base.rstrip('/')}/api/gateways/{gateway_id}/config"


@dataclass
class LoadedConfig:
    plant_map: PlantMap
    origin: str                 # "remote" | "cache" | "file"
    revision: Optional[str] = None
    detail: str = ""
    config_version: Optional[int] = None    # sobe a cada mudança do mapa no servidor (docs/gateways.md)


def _version(raw: Any) -> Optional[int]:
    try:
        return None if raw is None or isinstance(raw, bool) else int(raw)
    except (TypeError, ValueError):
        return None


def _extract_map(body: Any) -> tuple[dict, Optional[str]]:
    """(mapa v1, revision) da resposta (contrato verificado também pelos testes do servidor)."""
    doc, revision, _ = extract_config(body)
    return doc, revision


def extract_config(body: Any) -> tuple[dict, Optional[str], Optional[int]]:
    """(mapa v1, revision, config_version) da resposta de GET /api/gateways/{id}/config."""
    if not isinstance(body, dict):
        raise PlantMapError("resposta não é um objeto JSON")
    if isinstance(body.get("plant_map"), dict):
        return body["plant_map"], body.get("revision"), _version(body.get("config_version"))
    if "tags" in body and "sources" in body:
        return body, body.get("revision"), _version(body.get("config_version"))
    raise PlantMapError("resposta sem 'plant_map' (nem um mapa v1 no corpo)")


def _validate(doc: dict, gateway_id: str, env: Optional[Mapping[str, str]]) -> PlantMap:
    if doc.get("gateway_id") and doc["gateway_id"] != gateway_id:
        raise PlantMapError(f"mapa é do gateway {doc['gateway_id']!r}, não de {gateway_id!r}")
    return parse_plant_map({**doc, "gateway_id": gateway_id}, env=env)


def read_cache(path: str | Path) -> Optional[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("remote_config.cache_unreadable", path=str(path), error=str(e))
        return None
    if not isinstance(data, dict) or not isinstance(data.get("plant_map"), dict):
        log.warning("remote_config.cache_invalid", path=str(path))
        return None
    return data


def write_cache(path: str | Path, doc: dict, gateway_id: str, url: str, etag: Optional[str],
                revision: Optional[str], config_version: Optional[int] = None) -> None:
    """Grava a cópia local de forma atômica (tmp + rename). Nunca grava o token."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"cache_version": CACHE_VERSION, "gateway_id": gateway_id, "source_url": url, "etag": etag,
               "revision": revision, "config_version": config_version,
               "fetched_at": datetime.now(timezone.utc).isoformat(), "plant_map": doc}
    fd, tmp = tempfile.mkstemp(prefix=".gwcfg-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


async def fetch_remote(session: aiohttp.ClientSession, url: str, token: str,
                       etag: Optional[str] = None) -> tuple[int, Any, Optional[str]]:
    headers = {"Authorization": f"Gateway {token}", "Accept": "application/json"}
    if etag:
        headers["If-None-Match"] = etag
    async with session.get(url, headers=headers) as r:
        if r.status == 304:
            return 304, None, etag
        text = await r.text()
        if r.status != 200:
            return r.status, text[:200], None
        try:
            return 200, json.loads(text), r.headers.get("ETag")
        except ValueError:
            raise PlantMapError("resposta não é JSON") from None


async def load_gateway_config(
    base_url: str,
    gateway_id: str,
    token: str,
    cache_path: str | Path,
    fallback_path: str = "",
    timeout_s: float = 10.0,
    env: Optional[Mapping[str, str]] = None,
    session: Optional[aiohttp.ClientSession] = None,
) -> LoadedConfig:
    """Mapa do gateway: servidor → cópia local → arquivo (ver docstring do módulo)."""
    if not gateway_id:
        raise RemoteConfigError("GATEWAY_CONFIG_URL exige GATEWAY_ID (ou um mapa local com gateway_id)")
    url = config_url(base_url, gateway_id)
    cache = read_cache(cache_path)
    if cache is not None and cache.get("gateway_id") not in (None, gateway_id):
        log.warning("remote_config.cache_other_gateway", cached=cache.get("gateway_id"), gateway_id=gateway_id)
        cache = None
    reason = ""
    if not token:
        reason = "GATEWAY_TOKEN vazio"
    else:
        own = session is None
        sess = session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_s), trust_env=True)
        try:
            status, body, etag = await fetch_remote(sess, url, token, (cache or {}).get("etag"))
            if status == 200:
                doc, revision, version = extract_config(body)
                pm = _validate(doc, gateway_id, env)
                write_cache(cache_path, doc, gateway_id, url, etag, revision, version)
                log.info("remote_config.loaded", url=url, revision=revision, config_version=version,
                         tags=len(pm.tags))
                return LoadedConfig(pm, "remote", revision, config_version=version)
            if status == 304 and cache is not None:
                pm = _validate(cache["plant_map"], gateway_id, env)
                log.info("remote_config.not_modified", url=url, revision=cache.get("revision"))
                return LoadedConfig(pm, "cache", cache.get("revision"), "304",
                                    config_version=_version(cache.get("config_version")))
            reason = f"HTTP {status}" + (" (token recusado)" if status in (401, 403) else "")
        except (aiohttp.ClientError, TimeoutError, OSError) as e:
            reason = f"servidor inacessível: {type(e).__name__}"
        except PlantMapError as e:
            reason = f"mapa remoto inválido: {e}"
        finally:
            if own:
                await sess.close()
    if cache is not None:
        try:
            pm = _validate(cache["plant_map"], gateway_id, env)
            log.warning("remote_config.using_cache", url=url, reason=reason, fetched_at=cache.get("fetched_at"))
            return LoadedConfig(pm, "cache", cache.get("revision"), reason,
                                config_version=_version(cache.get("config_version")))
        except PlantMapError as e:
            log.warning("remote_config.cache_invalid", error=str(e))
    if fallback_path:
        log.warning("remote_config.using_file", url=url, reason=reason, path=fallback_path)
        pm = load_plant_map(fallback_path, env=env)
        return LoadedConfig(pm, "file", None, reason)
    raise RemoteConfigError(f"sem configuração: {reason or 'falha'}; sem cópia local nem PLANT_MAP_PATH")


def local_gateway_id(path: str) -> str:
    """gateway_id do mapa local (sem validar o resto) — para a primeira busca remota."""
    if not path:
        return ""
    try:
        return str(json.loads(Path(path).read_text(encoding="utf-8")).get("gateway_id") or "")
    except (OSError, ValueError):
        return ""
