"""
Comandos puxados pelo gateway (docs/sprint6.md §4.2): o servidor nunca abre conexão para dentro da fábrica.

A resposta do heartbeat HTTP (edge/src/fleet.py) pode trazer

    "commands": [{"id": "<uuid>", "type": "opcua_browse", "source_id": "opcua-plc",
                  "root_node_id": "ns=2;s=..." | null, "max_depth": 3, "max_nodes": 2000, "expires_at": "..."}]

O gateway executa cada comando (um por vez, em segundo plano — o heartbeat e a leitura das tags não esperam) e
responde com o próprio token:

    POST {SERVER_URL}/api/gateways/{gateway_id}/commands/{id}/result
    Authorization: Gateway <token>
    {"nodes": [{"node_id", "browse_name", "display_name", "node_class", "data_type", "access_level",
                "parent_id"}], "truncated": false}
    ou {"nodes": [], "truncated": false, "error": "<motivo>"}

Regras (segurança e limites):
- só navega o `endpoint` da fonte `source_id` **já configurada no mapa deste gateway** (o servidor nunca manda
  um endpoint); credenciais ficam no gateway (`${VAR}` expandido do ambiente no endpoint
  `opc.tcp://${OPC_USER}:${OPC_PASS}@host:4840/`) e nunca vão no resultado nem em mensagens de erro;
- nunca lê o atributo Value: só a estrutura (NodeClass, BrowseName, DisplayName, DataType, AccessLevel);
- limites: `max_depth` ≤ 6, `max_nodes` ≤ 5000 (o menor entre o pedido e o teto), tempo total
  `GATEWAY_BROWSE_TIMEOUT_S` (padrão 60 s) — estourou nós ou tempo → resultado parcial com `truncated: true`;
  strings cortadas em 256 caracteres;
- quando a navegação começa na pasta Objects, os filhos do namespace 0 (`Server` i=2253 — diagnóstico do próprio
  servidor OPC UA, milhares de nós —, `Locations`, `Aliases`) são pulados: só os objetos do CLP aparecem;
- idempotente: o servidor re-entrega um comando ainda sem resposta; o gateway reconhece o `id`, não navega de novo
  e reenvia o resultado guardado se o POST anterior falhou (rede) — 409/410 do servidor = já encerrado.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

import aiohttp
import structlog

from .enrollment import base_url

log = structlog.get_logger()

MAX_DEPTH = 6
MAX_NODES = 5000
STR_MAX = 256
OBJECTS_FOLDER = "i=85"
BATCH = 100                         # nós por chamada Browse/Read
SUPPORTED = ("opcua_browse",)

_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/@\s]*@")


def redact(text: str) -> str:
    """Remove credenciais embutidas em URLs (`opc.tcp://user:pass@host` → `opc.tcp://***@host`)."""
    return _USERINFO.sub(r"\1***@", str(text))


def _s(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v)
    return s[:STR_MAX]


class CommandError(Exception):
    """Falha esperada de um comando (vira `error` no resultado; texto sem credenciais)."""


@dataclass
class BrowseCommand:
    id: str
    source_id: str
    root_node_id: Optional[str]
    max_depth: int
    max_nodes: int


def parse_command(raw: Any) -> tuple[Optional[str], Any]:
    """(id, BrowseCommand) ou (id, "mensagem de erro") para comando inválido/desconhecido; (None, None) sem id."""
    if not isinstance(raw, dict):
        return None, None
    cid = raw.get("id")
    if not isinstance(cid, str) or not cid.strip() or len(cid) > 64 or not re.fullmatch(r"[A-Za-z0-9_-]+", cid):
        return None, None
    ctype = raw.get("type")
    if ctype not in SUPPORTED:
        return cid, f"tipo de comando não suportado por este gateway: {str(ctype)[:40]!r}"
    sid = raw.get("source_id")
    if not isinstance(sid, str) or not sid:
        return cid, "comando sem source_id"
    root = raw.get("root_node_id")
    if root is not None and (not isinstance(root, str) or len(root) > 512):
        return cid, "root_node_id inválido"

    def _int(name: str, default: int, cap: int) -> int:
        v = raw.get(name, default)
        if v is None:
            v = default
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(name)
        return max(1, min(cap, v))

    try:
        depth = _int("max_depth", 3, MAX_DEPTH)
        nodes = _int("max_nodes", 2000, MAX_NODES)
    except ValueError as e:
        return cid, f"{e} inválido"
    return cid, BrowseCommand(cid, sid, root or None, depth, nodes)


# ── navegação OPC UA ─────────────────────────────────────────────────────────

async def browse_opcua(endpoint: str, root_node_id: Optional[str] = None, max_depth: int = 3,
                       max_nodes: int = 2000, timeout_s: float = 60.0) -> dict:
    """Navega o servidor OPC UA em largura a partir de `root_node_id` (padrão: pasta Objects), seguindo
    referências hierárquicas para frente. Retorna {"nodes": [...], "truncated": bool}. Nunca lê Value."""
    from asyncua import Client, ua

    max_depth = max(1, min(MAX_DEPTH, int(max_depth)))
    max_nodes = max(1, min(MAX_NODES, int(max_nodes)))
    deadline = time.monotonic() + max(1.0, timeout_s)
    nodes: list[dict] = []
    truncated = False
    root_txt = root_node_id or OBJECTS_FOLDER
    try:
        root = ua.NodeId.from_string(root_txt)
    except Exception:  # noqa: BLE001 — texto de NodeId inválido
        raise CommandError(f"root_node_id inválido: {_s(root_txt)!r}") from None

    def left() -> float:
        return deadline - time.monotonic()

    client = Client(url=endpoint, timeout=max(1.0, min(10.0, timeout_s)))
    try:
        await asyncio.wait_for(client.connect(), timeout=max(1.0, min(15.0, left())))
    except Exception as e:  # noqa: BLE001
        raise CommandError(f"não foi possível conectar à fonte OPC UA: {redact(type(e).__name__)}"
                           f"{': ' + redact(str(e))[:160] if str(e) else ''}") from None
    try:
        uac = client.uaclient
        # o nó raiz precisa existir (NodeClass legível)
        try:
            dv = (await asyncio.wait_for(uac.read_attributes([root], ua.AttributeIds.NodeClass),
                                         timeout=max(0.5, left())))[0]
            if not dv.StatusCode.is_good():
                raise CommandError(f"nó raiz não encontrado: {_s(root_txt)!r}")
        except CommandError:
            raise
        except asyncio.TimeoutError:
            raise CommandError("tempo esgotado lendo o nó raiz") from None

        skip_server = root_txt in (OBJECTS_FOLDER, "ns=0;i=85")
        seen = {root.to_string()}
        level = [root]
        variables: list[tuple[int, ua.NodeId]] = []    # (índice em nodes, NodeId)
        for depth in range(1, max_depth + 1):
            if not level or truncated:
                break
            nxt: list[ua.NodeId] = []
            for i in range(0, len(level), BATCH):
                if left() <= 0:
                    truncated = True
                    break
                chunk = level[i:i + BATCH]
                results = await asyncio.wait_for(_browse(uac, chunk), timeout=max(0.5, left()))
                for parent, refs in zip(chunk, results):
                    pid = parent.to_string()
                    for ref in refs:
                        if getattr(ref.NodeId, "ServerIndex", 0):
                            continue          # referência a outro servidor: fora do escopo
                        node_id = ua.NodeId(ref.NodeId.Identifier, ref.NodeId.NamespaceIndex,
                                            ref.NodeId.NodeIdType)
                        nid = node_id.to_string()
                        if nid in seen or (skip_server and depth == 1 and node_id.NamespaceIndex == 0):
                            continue          # Server, Locations, Aliases…: infraestrutura do próprio servidor
                        if len(nodes) >= max_nodes:
                            truncated = True
                            break
                        seen.add(nid)
                        cls = ref.NodeClass.name if hasattr(ref.NodeClass, "name") else str(ref.NodeClass)
                        nodes.append({
                            "node_id": _s(nid),
                            "browse_name": _s(ref.BrowseName.Name if ref.BrowseName else None),
                            "display_name": _s(ref.DisplayName.Text if ref.DisplayName else None),
                            "node_class": cls,
                            "data_type": None,
                            "access_level": None,
                            "parent_id": _s(pid),
                        })
                        if cls == "Variable":
                            variables.append((len(nodes) - 1, node_id))
                        if cls in ("Object", "Variable", "View") and depth < max_depth:
                            nxt.append(node_id)
                    if truncated:
                        break
                if truncated:
                    break
            level = nxt
        # atributos estruturais das variáveis (nunca Value)
        type_ids: dict[str, ua.NodeId] = {}
        for i in range(0, len(variables), BATCH):
            if left() <= 0:
                truncated = True
                break
            chunk = variables[i:i + BATCH]
            ids = [n for _, n in chunk]
            dts = await asyncio.wait_for(uac.read_attributes(ids, ua.AttributeIds.DataType),
                                         timeout=max(0.5, left()))
            als = await asyncio.wait_for(uac.read_attributes(ids, ua.AttributeIds.AccessLevel),
                                         timeout=max(0.5, left()))
            for (idx, _), dt, al in zip(chunk, dts, als):
                if dt.StatusCode.is_good() and dt.Value is not None and dt.Value.Value is not None:
                    tid = dt.Value.Value
                    key = tid.to_string()
                    type_ids[key] = tid
                    nodes[idx]["data_type"] = key
                if al.StatusCode.is_good() and al.Value is not None and isinstance(al.Value.Value, int):
                    nodes[idx]["access_level"] = int(al.Value.Value)
        names = await _type_names(uac, type_ids, left) if type_ids and left() > 0 else {}
        for n in nodes:
            if n["data_type"] in names:
                n["data_type"] = names[n["data_type"]]
    except CommandError:
        raise
    except asyncio.TimeoutError:
        truncated = True
    except Exception as e:  # noqa: BLE001
        if not nodes:
            raise CommandError(f"falha navegando a fonte OPC UA: {redact(type(e).__name__)}: "
                               f"{redact(str(e))[:160]}") from None
        log.warning("commands.browse_partial", error=redact(str(e))[:200], nodes=len(nodes))
        truncated = True
    finally:
        try:
            await asyncio.wait_for(client.disconnect(), timeout=5)
        except Exception:  # noqa: BLE001
            pass
    return {"nodes": nodes, "truncated": truncated}


async def _browse(uac, node_ids: list) -> list[list]:
    """Browse (referências hierárquicas, para frente, com subtipos) de vários nós, seguindo continuation points."""
    from asyncua import ua

    params = ua.BrowseParameters()
    params.View = ua.ViewDescription()
    params.RequestedMaxReferencesPerNode = 1000
    descs = []
    for nid in node_ids:
        d = ua.BrowseDescription()
        d.NodeId = nid
        d.BrowseDirection = ua.BrowseDirection.Forward
        d.ReferenceTypeId = ua.NodeId(ua.ObjectIds.HierarchicalReferences)
        d.IncludeSubtypes = True
        d.NodeClassMask = 0
        d.ResultMask = ua.BrowseResultMask.All
        descs.append(d)
    params.NodesToBrowse = descs
    results = await uac.browse(params)
    out: list[list] = []
    for res in results:
        refs = list(res.References or []) if res.StatusCode.is_good() else []
        cp = res.ContinuationPoint
        guard = 0
        while cp and guard < 50:
            guard += 1
            nparams = ua.BrowseNextParameters()
            nparams.ReleaseContinuationPoints = False
            nparams.ContinuationPoints = [cp]
            more = (await uac.browse_next(nparams))[0]
            refs.extend(more.References or [])
            cp = more.ContinuationPoint
        out.append(refs)
    return out


async def _type_names(uac, type_ids: dict, left: Callable[[], float]) -> dict[str, str]:
    from asyncua import ua

    keys = list(type_ids)
    names: dict[str, str] = {}
    for i in range(0, len(keys), BATCH):
        chunk = keys[i:i + BATCH]
        try:
            res = await asyncio.wait_for(uac.read_attributes([type_ids[k] for k in chunk], ua.AttributeIds.BrowseName),
                                         timeout=max(0.5, left()))
        except asyncio.TimeoutError:
            break
        for k, dv in zip(chunk, res):
            if dv.StatusCode.is_good() and dv.Value is not None and dv.Value.Value is not None:
                names[k] = _s(dv.Value.Value.Name) or k
    return names


# ── execução e resposta ──────────────────────────────────────────────────────

ResolveSource = Callable[[str], Optional[tuple[str, str]]]     # source_id → (protocol, endpoint) | None
BrowseFn = Callable[..., Awaitable[dict]]


class CommandRunner:
    """Executa os comandos recebidos no heartbeat e envia os resultados (ver docstring do módulo)."""

    def __init__(self, server_url: str, gateway_id: str, token: str, resolve_source: ResolveSource,
                 timeout_s: float = 60.0, http_timeout_s: float = 15.0, browse: Optional[BrowseFn] = None,
                 session: Optional[aiohttp.ClientSession] = None, max_queue: int = 10) -> None:
        self.base = base_url(server_url)
        self.gateway_id = gateway_id
        self._token = token
        self._resolve = resolve_source
        self.timeout_s = timeout_s
        self._http_timeout = http_timeout_s
        self._browse = browse or browse_opcua
        self._session = session
        self._own = session is None
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
        self._task: Optional[asyncio.Task] = None
        # id → None (na fila/rodando) | corpo do resultado ainda não aceito pelo servidor | "done"
        self._known: "OrderedDict[str, Any]" = OrderedDict()
        self.executed = 0

    def result_url(self, cmd_id: str) -> str:
        return f"{self.base}/api/gateways/{self.gateway_id}/commands/{cmd_id}/result"

    def submit(self, commands: Any) -> int:
        """Enfileira os comandos novos de um heartbeat. Retorna quantos foram aceitos (novos ou reenvio)."""
        if not isinstance(commands, list):
            return 0
        accepted = 0
        for raw in commands[:20]:
            cid, cmd = parse_command(raw)
            if cid is None:
                log.warning("commands.invalid", detail="comando sem id válido")
                continue
            state = self._known.get(cid, "new")
            if state == "done" or (state is None):
                continue                       # já respondido / em execução: o servidor só re-entregou
            if isinstance(state, dict):        # resultado pronto, envio anterior falhou → reenvia
                item = (cid, state)
            else:
                item = (cid, cmd)
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                log.warning("commands.queue_full", command_id=cid)
                continue
            self._known[cid] = None
            while len(self._known) > 200:
                self._known.popitem(last=False)
            accepted += 1
        if accepted:
            self._ensure_task()
        return accepted

    def _ensure_task(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="gateway_commands")

    async def _loop(self) -> None:
        while True:
            try:
                cid, item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                body = item if isinstance(item, dict) else await self.execute(item)
                await self._deliver(cid, body)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — um comando nunca derruba o gateway
                log.warning("commands.failed", command_id=cid, error=redact(str(e))[:200])
                self._known[cid] = "done"

    async def execute(self, cmd: Any) -> dict:
        """Corpo do resultado (nunca levanta: erro vira `error`)."""
        if isinstance(cmd, str):
            return {"nodes": [], "truncated": False, "error": cmd[:500]}
        src = self._resolve(cmd.source_id)
        if src is None:
            return {"nodes": [], "truncated": False,
                    "error": f"fonte {cmd.source_id!r} não está configurada neste gateway"}
        proto, endpoint = src
        if proto != "opcua":
            return {"nodes": [], "truncated": False,
                    "error": f"a fonte {cmd.source_id!r} não é OPC UA (navegação só em OPC UA)"}
        t0 = time.monotonic()
        try:
            out = await asyncio.wait_for(
                self._browse(endpoint, cmd.root_node_id, cmd.max_depth, cmd.max_nodes, self.timeout_s),
                timeout=self.timeout_s + 20.0)
        except CommandError as e:
            return {"nodes": [], "truncated": False, "error": redact(str(e))[:500]}
        except asyncio.TimeoutError:
            return {"nodes": [], "truncated": False, "error": "tempo esgotado navegando a fonte OPC UA"}
        except Exception as e:  # noqa: BLE001
            return {"nodes": [], "truncated": False,
                    "error": f"falha navegando a fonte OPC UA: {redact(type(e).__name__)}"}
        self.executed += 1
        nodes = list(out.get("nodes") or [])[:cmd.max_nodes]
        log.info("commands.browse_done", command_id=cmd.id, source_id=cmd.source_id, nodes=len(nodes),
                 truncated=bool(out.get("truncated")), elapsed_s=round(time.monotonic() - t0, 2))
        return {"nodes": nodes, "truncated": bool(out.get("truncated"))}

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._http_timeout),
                                                  trust_env=True)
            self._own = True
        return self._session

    async def _deliver(self, cid: str, body: dict) -> bool:
        sess = await self._sess()
        try:
            async with sess.post(self.result_url(cid), data=json.dumps(body, ensure_ascii=False),
                                 headers={"Authorization": f"Gateway {self._token}",
                                          "Content-Type": "application/json", "Accept": "application/json"}) as r:
                status = r.status
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            log.warning("commands.result_not_sent", command_id=cid, error=type(e).__name__,
                        hint="reenviado quando o servidor entregar o comando de novo")
            self._known[cid] = body
            return False
        if status in (200, 201, 202, 204, 404, 409, 410):
            # 404/409/410: comando desconhecido, já encerrado ou expirado — nada a reenviar
            self._known[cid] = "done"
            log.info("commands.result_sent", command_id=cid, status=status)
            return status < 300
        log.warning("commands.result_rejected", command_id=cid, status=status)
        self._known[cid] = body if status >= 500 else "done"
        return False

    async def drain(self, timeout: float = 30.0) -> None:
        """Espera a fila esvaziar (testes)."""
        if self._task is not None:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout)

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._own and self._session is not None and not self._session.closed:
            await self._session.close()
