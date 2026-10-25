"""
Heartbeat do gateway para a gestão de frota do servidor (docs/gateways.md).

    POST {SERVER_URL}/api/gateways/{gateway_id}/heartbeat
    Authorization: Gateway <token>
    {"version": "0.6.0", "uptime_s": 1234.5, "buffer_depth": 0, "errors": ["fonte modbus-plc desconectada"],
     "config_version": 7}
    → 200 {"status": "online|degraded", "config_version": 8, "config_changed": true,
           "commands": [{"id": "...", "type": "opcua_browse", ...}]}

`commands` (Sprint 6, docs/sprint6.md §4.2): comandos puxados — executados por edge/src/commands.py.

`config_changed = true` → o gateway busca a configuração de novo (ETag) e aplica o mapa novo **a quente**
(edge/src/gateway.py `apply_plant_map`), sem reiniciar e sem perder contadores de borda, buffer nem deadband.

Este heartbeat (HTTP, a cada `GATEWAY_HEARTBEAT_S`, padrão 60 s) é diferente do heartbeat MQTT
(`factory/{tenant}/gateways/{id}/heartbeat`, a cada `heartbeat_s` do mapa), que segue igual: o HTTP alimenta a tela
de frota (estado online/degradado/offline, versão, profundidade do buffer, erros) e avisa mudança de configuração.
Falha de rede no heartbeat nunca derruba o gateway (só vira um erro no próximo heartbeat que der certo).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Optional

import aiohttp
import structlog

from .enrollment import base_url

log = structlog.get_logger()


class HeartbeatError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class HeartbeatResult:
    status: Optional[str]
    config_version: Optional[int]
    config_changed: bool
    commands: list = field(default_factory=list)


def heartbeat_body(version: str, uptime_s: float, buffer_depth: int, errors: list[str],
                   config_version: Optional[int]) -> dict:
    return {"version": version, "uptime_s": round(max(0.0, uptime_s), 1), "buffer_depth": max(0, int(buffer_depth)),
            "errors": [str(e)[:200] for e in errors][:20], "config_version": config_version}


class FleetClient:
    def __init__(self, server_url: str, gateway_id: str, token: str, timeout_s: float = 10.0,
                 session: Optional[aiohttp.ClientSession] = None) -> None:
        self.url = f"{base_url(server_url)}/api/gateways/{gateway_id}/heartbeat"
        self._token = token
        self._timeout = timeout_s
        self._session = session
        self._own = session is None

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._timeout), trust_env=True)
            self._own = True
        return self._session

    async def heartbeat(self, body: dict) -> HeartbeatResult:
        sess = await self._sess()
        try:
            async with sess.post(self.url, json=body, headers={"Authorization": f"Gateway {self._token}",
                                                                "Accept": "application/json"}) as r:
                text = await r.text()
                if r.status != 200:
                    hint = " (token recusado: gateway revogado? cadastre de novo)" if r.status in (401, 403) else ""
                    raise HeartbeatError(f"heartbeat HTTP {r.status}{hint}", r.status)
                try:
                    data: Any = json.loads(text)
                except ValueError:
                    raise HeartbeatError("resposta do heartbeat não é JSON", r.status) from None
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            raise HeartbeatError(f"servidor inacessível: {type(e).__name__}") from None
        if not isinstance(data, dict):
            raise HeartbeatError("resposta do heartbeat não é um objeto")
        cv = data.get("config_version")
        return HeartbeatResult(status=data.get("status"),
                               config_version=int(cv) if isinstance(cv, int) and not isinstance(cv, bool) else None,
                               config_changed=bool(data.get("config_changed")),
                               commands=[c for c in (data.get("commands") or []) if isinstance(c, dict)][:20]
                               if isinstance(data.get("commands"), list) else [])

    async def close(self) -> None:
        if self._own and self._session is not None and not self._session.closed:
            await self._session.close()
