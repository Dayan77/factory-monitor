"""
Driver `ueapd` — coleta dos boxes ueaPowerDriver (linha de LED driver: M1 "hipot", M2 "ats2") pela API HTTP do
serviço do próprio box (FastAPI :8003), SEM mudar nada no box. É o "coletor central" que o box já prevê
(ueapowerdriver docs/RASTREABILIDADE.md §5: mesma primitiva de pull do espelho cruzado).

Endpoints usados (somente leitura):
    GET /auto/trace/files                  → {"files": [{"file": "passes-YYYYMMDD.jsonl", "date": "YYYY-MM-DD",
                                              "size": bytes}, {"file": "manut-YYYYMMDD.jsonl", ...}]}
    GET /auto/trace/pull?file=&offset=     → {"data": "<linhas JSONL>", "next_offset": int, "eof": bool}
        (tail incremental por byte; até 1 MB por chamada, cortado na última linha completa quando há mais —
        no EOF a última linha pode vir incompleta: o driver só consome até o último "\\n")
    GET /auto/state                        → armed, gate{armed, mode_auto, safety_ok, emerg_ok, ok}, faults[{tag,
                                              label}], reconcile_pending, cadencia{ph}, line, ...
    (o cabeçalho HTTP `Date` das respostas mede o desvio do relógio do box — política de timestamp abaixo)

Arquivos (append-only, diários no fuso local do box, nome pela data de `finished_at`/`ts`):
    passes-*.jsonl  um PASSE fechado por linha: {line, pass_id, serial, label, started_at, finished_at (epoch s),
                    events: [{stage, t, ok (true|false|null), posto?, skip?, auto?, late?, retest?, detail?}],
                    verdict: {ok, reasons[]}}
    manut-*.jsonl   eventos de manutenção {ts, line, componente, tipo, detalhe, local?, dur_s?}; falha do CLP =
                    bordas `falha_clp` (subida, local = tag do CLP) e `falha_clp_fim` (descida, com dur_s)

Seletores de tag (`address` no mapa; papel conferido em edge/src/plant_map.py):
    state:run            run_state = 1 ⇔ armado ∧ gate.ok (MODO_AUTO ∧ SAFETY_OK ∧ EMERG_OK ∧ reset) ∧ sem
                         reconciliação pendente ∧ nenhuma falha do CLP ativa; senão 0
    state:fault          fault_state = código da falha do CLP ativa há mais tempo (0 = nenhuma). Código por tag
                         (`fault_codes` da fonte sobre DEFAULT_FAULT_CODES; tag desconhecida → por componente,
                         senão `default_fault_code`, 301). Bordas vêm do manut (instante exato, pega falha mais
                         curta que o polling) e o `faults` do /auto/state reconcilia (box sem trace, borda perdida).
    state:cadence        cadência sustentada do box (peças/h, `cadencia.ph`); sem valor quando o box não sabe
    passes:good|scrap|total   peças por veredito do passe fechado (ok true → boa, false → refugo), contador
                         acumulado no edge, instante = finished_at
    cycles:<estágio>[:P<n>]   ciclos de uso da estação (ou do posto n, 1-based): eventos do estágio com ok
                         true/false, sem `skip` (pulado / passagem) e sem `auto` (entrada sintética); retestes
                         contam (o testador rodou de novo); instante = t do evento
    measure:<estágio>:<chave>[.<sub>]   valor numérico do `detail` do evento (um por evento; hoje o box não grava
                         medições numéricas — ATS guarda só Pass/Fail + referência do .xlsx)

Contagem idempotente (nunca contar duas vezes, nunca perder): o offset de cada arquivo, os passes já contados
(hash de line+pass_id+serial+started_at+finished_at — o pass_id do box recomeça a cada reinício do serviço), os
totais e as falhas ativas são gravados NA MESMA transação SQLite (EdgeCounterStore) antes de publicar. Reler um
arquivo (offset perdido, arquivo truncado/recriado) não conta nada de novo. Totais são publicados como acumulado.
Primeiro contato com o box (`backfill`): "none" (padrão) começa no fim dos arquivos que já existem — o histórico
anterior não tem run_state e distorceria o OEE —, "latest_day" lê o dia mais recente inteiro, "all" tudo
(passes com mais de DEDUPE_DAYS dias são ignorados).

Timestamps (`timestamps` da fonte): "source" usa o instante gravado pelo box (finished_at / t / ts) e estima o
relógio do box pelo `Date` HTTP (resolução 1 s; use max_clock_skew_s ≥ 2); fora do limite o dispositivo é rebaixado
para "gateway" (edge/src/clock.py). "gateway": hora de leitura no gateway — um atraso de rede acumula passes no
mesmo instante (o servidor aceita: cada leitura é +1). Bordas de falha mais velhas que a última publicada da tag
(backlog depois de uma queda) não são republicadas no passado: o estado atual sai pela reconciliação.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable, Optional

import aiohttp
import structlog

from ..plant_map import MapTag, SourceConfig, parse_ueapd_selector

log = structlog.get_logger()

OnReading = Callable[..., Awaitable[None]]

# Tag de falha do CLP (line.py "faults" do box, M1 e M2) → código FactoryConnect (centena = categoria, docs/domain.md)
DEFAULT_FAULT_CODES = {
    "ERRO_TRANSFERENCIA": 101,   # transferência entre esteiras — mecânica (EQP-01)
    "ERRO_STOP": 102,            # stopper — mecânica (EQP-01)
    "SAFETY_CONVEYOR": 103,      # esteira (safety do transportador) — mecânica (EQP-01)
    "ERRO_CLAMP": 401,           # clamp pneumático — pneumática (EQP-03)
    "GRAVADORA_ERRO": 501,       # gravadora laser (MD-X), M1 — ferramental/dispositivo (EQP-05)
}
# fallback pelo `componente` do evento de manutenção (orchestrator._FAULT_COMP; "clp" = tag sem componente)
COMPONENT_FAULT_CODES = {"transferencia": 101, "stopper": 102, "esteira": 103, "clamp": 401,
                         "gravadora": 501, "laser": 501, "seguranca": 302, "leitor": 303, "clp": 301}
FAULT_EDGE_ON, FAULT_EDGE_OFF = "falha_clp", "falha_clp_fim"
DEDUPE_DAYS = 35
_FILE_RE = re.compile(r"^(passes|manut)-(\d{8})\.jsonl$")
MAX_CHUNKS_PER_POLL = 50          # ~50 MB por ciclo no máximo: o laço continua respondendo num backlog grande


class UeapdError(RuntimeError):
    pass


def _epoch(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _dt(epoch: Optional[float]) -> Optional[datetime]:
    return None if epoch is None else datetime.fromtimestamp(epoch, tz=timezone.utc)


def pass_key(rec: dict) -> str:
    """Identidade de um passe fechado (o pass_id sozinho recomeça a cada reinício do serviço do box)."""
    raw = json.dumps([rec.get("line"), rec.get("pass_id"), rec.get("serial"), rec.get("started_at"),
                      rec.get("finished_at")], separators=(",", ":"), default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def counted_event(ev: dict) -> bool:
    """Evento que é USO da estação: resultado real (ok bool), não pulado/passagem, não entrada sintética."""
    return isinstance(ev, dict) and isinstance(ev.get("ok"), bool) and not ev.get("skip") and not ev.get("auto")


def run_state_of(state: dict, faults_active: bool) -> int:
    gate = state.get("gate") or {}
    running = (bool(state.get("armed")) and bool(gate.get("ok")) and not state.get("reconcile_pending")
               and not faults_active)
    return 1 if running else 0


def _lookup(detail: Any, keys: list[str]) -> Optional[float]:
    cur = detail
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    if isinstance(cur, bool) or not isinstance(cur, (int, float)):
        return None
    return float(cur)


@dataclass
class _Bind:
    tag: MapTag
    kind: str
    stage: Optional[str]
    extra: Any


class UeapdDriver:
    """Um box ueaPowerDriver (uma fonte do mapa). Ver docstring do módulo."""

    def __init__(self, source: SourceConfig, tags: list[MapTag], store, on_reading: OnReading,
                 clock=None, session: Optional[aiohttp.ClientSession] = None,
                 now: Callable[[], float] = time.time) -> None:
        self.source = source
        self.sid = source.id
        self.base = source.base_url.rstrip("/")
        self.store = store
        self.on_reading = on_reading
        self.clock = clock
        self.poll_interval_s = source.poll_interval_s
        self.max_backoff_s = 30.0
        self._session = session
        self._own_session = session is None
        self._now = now
        self.fault_codes = {**DEFAULT_FAULT_CODES, **(source.fault_codes or {})}
        self._binds: list[_Bind] = []
        for t in tags:
            kind, stage, extra = parse_ueapd_selector(t.address)
            self._binds.append(_Bind(t, kind, stage, extra))
        self._counter_kinds = ("passes:good", "passes:scrap", "passes:total", "cycles")
        self._prefix = f"ueapd:{self.sid}:"
        self._totals: dict[str, int] = {}
        self._faults: dict[str, Optional[float]] = {}      # tag do CLP → início (epoch do box) — ativas
        self._fault_value: Optional[int] = None             # último código publicado
        self._fault_last_ts = 0.0                           # instante (box) do último código publicado por borda
        self._loaded = False
        self._running = False
        self.connected = False
        self._warned_line = False
        self._last_prune = 0.0
        # diagnóstico (testes / heartbeat)
        self.stats = {"polls": 0, "passes": 0, "duplicates": 0, "stale": 0, "bad_lines": 0, "fault_edges": 0,
                      "stale_fault_edges": 0, "errors": 0, "truncated_files": 0}

    # ── tags por tipo ───────────────────────────────────────────────────
    def _of(self, kind: str) -> list[_Bind]:
        return [b for b in self._binds if b.kind == kind]

    def _counter_binds(self) -> list[_Bind]:
        return [b for b in self._binds if b.kind in self._counter_kinds]

    # ── estado persistido ───────────────────────────────────────────────
    def _load(self) -> None:
        if self._loaded:
            return
        for b in self._counter_binds():
            self._totals[b.tag.key] = self.store.get(b.tag.key)
        raw = self.store.get_kv(self._prefix + "faults")
        if raw:
            try:
                doc = json.loads(raw)
                self._faults = {str(k): _epoch(v) for k, v in (doc.get("active") or {}).items()}
                self._fault_value = doc.get("value")
                self._fault_last_ts = float(doc.get("last_ts") or 0.0)
            except (ValueError, TypeError, AttributeError):
                log.warning("ueapd.fault_state_corrupt", source=self.sid)
        self._loaded = True

    def _faults_kv(self) -> dict[str, str]:
        return {self._prefix + "faults": json.dumps({"active": self._faults, "value": self._fault_value,
                                                     "last_ts": self._fault_last_ts})}

    # ── HTTP ────────────────────────────────────────────────────────────
    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.source.timeout_s))
            self._own_session = True
        return self._session

    async def _get(self, path: str, params: Optional[dict] = None) -> Any:
        async with self._http().get(f"{self.base}{path}", params=params) as r:
            recv = datetime.now(timezone.utc)
            if r.status != 200:
                raise UeapdError(f"GET {path} → HTTP {r.status}")
            body = await r.json(content_type=None)
            self._clock_sample(r.headers.get("Date"), recv)
            return body

    def _clock_sample(self, date_hdr: Optional[str], recv: datetime) -> None:
        """Desvio do relógio do box pelo cabeçalho Date (truncado ao segundo: +0,5 s centraliza o erro)."""
        if self.clock is None or not date_hdr:
            return
        try:
            box = parsedate_to_datetime(date_hdr)
        except (TypeError, ValueError):
            return
        if box.tzinfo is None:
            box = box.replace(tzinfo=timezone.utc)
        self.clock.stamp(box + timedelta(seconds=0.5), now=recv, tag_id=f"{self.sid}:Date")

    # ── ciclo ───────────────────────────────────────────────────────────
    async def poll_once(self) -> dict:
        """Uma volta: trace (passes + manut) incremental, depois o estado vivo. Retorna o resumo."""
        await asyncio.to_thread(self._load)
        files = (await self._get("/auto/trace/files") or {}).get("files") or []
        counted = await self._sync_files(files)
        state = await self._get("/auto/state")
        if not isinstance(state, dict):
            raise UeapdError("/auto/state: resposta inesperada")
        await self._apply_state(state)
        await self._report_totals()
        self.stats["polls"] += 1
        now = self._now()
        if now - self._last_prune > 3600:
            self._last_prune = now
            await asyncio.to_thread(self.store.prune_seen, self._prefix + "p:", now - DEDUPE_DAYS * 86400)
        return {"passes": counted, "state": state}

    async def _sync_files(self, files: list[dict]) -> int:
        listed = []
        for f in files:
            name = str((f or {}).get("file") or "")
            m = _FILE_RE.match(name)
            if m:
                try:
                    size = int(f.get("size") or 0)
                except (TypeError, ValueError):
                    size = 0
                listed.append((m.group(2), m.group(1), name, size))
        listed.sort()
        off_prefix = self._prefix + "off:"
        offsets = {k[len(off_prefix):]: int(v)
                   for k, v in (await asyncio.to_thread(self.store.kv_prefix, off_prefix)).items()}
        if await asyncio.to_thread(self.store.get_kv, self._prefix + "init") is None:
            offsets = self._initial_offsets(listed)
            kv = {off_prefix + n: str(o) for n, o in offsets.items()}
            kv[self._prefix + "init"] = self.source.backfill
            await asyncio.to_thread(self.store.commit, {}, kv, None)
            log.info("ueapd.first_contact", source=self.sid, backfill=self.source.backfill, files=len(listed))
        counted = 0
        chunks = 0
        for _day, kind, name, size in listed:
            off = offsets.get(name, 0)
            if size < off:
                # arquivo menor que o offset confirmado: recriado/truncado no box → relê do início; o dedupe
                # por passe impede contar de novo
                log.warning("ueapd.file_truncated", source=self.sid, file=name, size=size, offset=off)
                self.stats["truncated_files"] += 1
                off = 0
            while off < size and chunks < MAX_CHUNKS_PER_POLL:
                resp = await self._get("/auto/trace/pull", {"file": name, "offset": str(off)})
                chunks += 1
                data = str((resp or {}).get("data") or "")
                nl = data.rfind("\n")
                complete = data[:nl + 1] if nl >= 0 else ""
                if not complete:
                    if not resp.get("eof"):                 # linha maior que o limite do pull do box (1 MB)
                        log.error("ueapd.line_too_long", source=self.sid, file=name, offset=off)
                    break                                   # só linha incompleta (box gravando): próxima volta
                new_off = off + len(complete.encode("utf-8"))
                nxt = resp.get("next_offset")
                if complete == data and isinstance(nxt, int) and nxt != new_off:
                    log.warning("ueapd.offset_mismatch", source=self.sid, file=name, computed=new_off, box=nxt)
                    new_off = nxt
                counted += await self._process_chunk(kind, name, complete.splitlines(), new_off)
                off = new_off
                if resp.get("eof"):
                    break
        return counted

    def _initial_offsets(self, listed: list[tuple[str, str, str, int]]) -> dict[str, int]:
        mode = self.source.backfill
        if mode == "all":
            return {name: 0 for _, _, name, _ in listed}
        latest = max((d for d, _, _, _ in listed), default=None)
        return {name: (0 if mode == "latest_day" and day == latest else size) for day, _, name, size in listed}

    async def _process_chunk(self, kind: str, name: str, lines: list[str], new_off: int) -> int:
        recs = []
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except ValueError:
                self.stats["bad_lines"] += 1
                log.warning("ueapd.bad_line", source=self.sid, file=name)
                continue
            if isinstance(rec, dict):
                recs.append(rec)
        expect = self.source.line
        if expect:
            other = [r for r in recs if r.get("line") not in (None, expect)]
            if other and not self._warned_line:
                self._warned_line = True
                log.error("ueapd.unexpected_line", source=self.sid, expected=expect, got=other[0].get("line"),
                          hint="base_url aponta para outro box? registros de outra linha são ignorados")
            recs = [r for r in recs if r.get("line") in (None, expect)]

        inc: dict[str, int] = {}
        running = dict(self._totals)
        emissions: list[tuple[str, float, Optional[float]]] = []    # (tag_id, valor, epoch do box)
        seen_new: dict[str, float] = {}
        counted = 0
        kv: dict[str, str] = {self._prefix + "off:" + name: str(new_off)}

        def bump(b: _Bind, ts: Optional[float]) -> None:
            k = b.tag.key
            running[k] = running.get(k, 0) + 1
            inc[k] = inc.get(k, 0) + 1
            emissions.append((b.tag.tag_id, float(running[k]), ts))

        if kind == "passes":
            keys = [pass_key(r) for r in recs]
            already = await asyncio.to_thread(self.store.seen, [self._prefix + "p:" + k for k in keys])
            horizon = self._now() - DEDUPE_DAYS * 86400
            for rec, k in zip(recs, keys):
                full = self._prefix + "p:" + k
                if full in already or full in seen_new:
                    self.stats["duplicates"] += 1
                    continue
                fin = _epoch(rec.get("finished_at"))
                if fin is None or fin < horizon:
                    self.stats["stale"] += 1
                    continue
                seen_new[full] = fin
                counted += 1
                ok = (rec.get("verdict") or {}).get("ok")
                if ok is True:
                    for b in self._of("passes:good"):
                        bump(b, fin)
                elif ok is False:
                    for b in self._of("passes:scrap"):
                        bump(b, fin)
                if isinstance(ok, bool):
                    for b in self._of("passes:total"):
                        bump(b, fin)
                for ev in rec.get("events") or []:
                    if not isinstance(ev, dict):
                        continue
                    t_ev = _epoch(ev.get("t")) or fin
                    if counted_event(ev):
                        for b in self._of("cycles"):
                            if b.stage == ev.get("stage") and (b.extra is None or b.extra == ev.get("posto")):
                                bump(b, t_ev)
                    for b in self._of("measure"):
                        if b.stage == ev.get("stage") and ev.get("detail") is not None:
                            v = _lookup(ev.get("detail"), b.extra)
                            if v is not None:
                                emissions.append((b.tag.tag_id, v, t_ev))
            self.stats["passes"] += counted
        else:
            changed = False
            for rec in recs:
                em = self._fault_edge(rec)
                if em is not None:
                    changed = True
                    if em is not True:
                        emissions.extend(em)
            if changed:
                kv.update(self._faults_kv())

        totals = await asyncio.to_thread(self.store.commit, inc, kv, seen_new)   # persiste ANTES de publicar
        for k, v in totals.items():
            running[k] = v
        self._totals.update({k: running[k] for k in inc})
        for tag_id, value, ts in emissions:
            await self.on_reading(tag_id, value, _dt(ts), initial=True)
        return counted

    def _code_for(self, tag: str) -> int:
        if tag in self.fault_codes:
            return int(self.fault_codes[tag])
        return int(COMPONENT_FAULT_CODES.get(tag, self.source.default_fault_code))

    def _current_code(self) -> int:
        if not self._faults:
            return 0
        first = min(self._faults, key=lambda t: (self._faults[t] is None, self._faults[t] or 0.0, t))
        return self._code_for(first)

    def _fault_edge(self, rec: dict):
        """Aplica uma borda de falha do manut. None = não é borda; True = estado mudou sem publicar;
        lista de emissões = publicar o novo código no instante da borda."""
        tipo = rec.get("tipo")
        if tipo not in (FAULT_EDGE_ON, FAULT_EDGE_OFF):
            return None
        tag = str(rec.get("local") or rec.get("componente") or "")
        if not tag:
            return None
        ts = _epoch(rec.get("ts"))
        self.stats["fault_edges"] += 1
        if tipo == FAULT_EDGE_ON:
            if tag in self._faults:
                return True
            self._faults[tag] = ts
        else:
            if tag not in self._faults:
                return True
            self._faults.pop(tag, None)
        code = self._current_code()
        if code == self._fault_value:
            return True
        if ts is None or ts <= self._fault_last_ts:
            self.stats["stale_fault_edges"] += 1      # backlog: o estado atual sai pela reconciliação
            return True
        self._fault_value, self._fault_last_ts = code, ts
        return [(b.tag.tag_id, float(code), ts) for b in self._of("state:fault")]

    async def _apply_state(self, state: dict) -> None:
        if self.source.line and state.get("line") not in (None, self.source.line) and not self._warned_line:
            self._warned_line = True
            log.error("ueapd.unexpected_line", source=self.sid, expected=self.source.line, got=state.get("line"))
        live = {str(f.get("tag")) for f in state.get("faults") or [] if isinstance(f, dict) and f.get("tag")}
        now = self._now()
        changed = False
        for tag in live - set(self._faults):
            self._faults[tag] = now
            changed = True
        for tag in set(self._faults) - live:
            self._faults.pop(tag, None)
            changed = True
        code = self._current_code()
        if code != self._fault_value:
            self._fault_value = code
            changed = True
        if changed:
            await asyncio.to_thread(self.store.commit, {}, self._faults_kv(), None)
        for b in self._of("state:fault"):
            await self.on_reading(b.tag.tag_id, float(code))
        run = run_state_of(state, bool(self._faults))
        for b in self._of("state:run"):
            await self.on_reading(b.tag.tag_id, float(run))
        ph = _epoch((state.get("cadencia") or {}).get("ph"))
        if ph is not None:
            for b in self._of("state:cadence"):
                await self.on_reading(b.tag.tag_id, ph)

    async def _report_totals(self) -> None:
        """Re-oferece o acumulado (o deadband só republica no heartbeat de silêncio) — só com o box no ar."""
        for b in self._counter_binds():
            await self.on_reading(b.tag.tag_id, float(self._totals.get(b.tag.key, 0)))

    def totals(self) -> dict[str, int]:
        return dict(self._totals)

    async def run(self) -> None:
        self._running = True
        log.info("ueapd.starting", source=self.sid, base_url=self.base, tags=len(self._binds),
                 poll_interval_s=self.poll_interval_s, backfill=self.source.backfill)
        wait = self.poll_interval_s
        while self._running:
            t0 = time.monotonic()
            try:
                await self.poll_once()
                if not self.connected:
                    log.info("ueapd.connected", source=self.sid, base_url=self.base)
                self.connected = True
                wait = self.poll_interval_s
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — box fora / resposta ruim: tenta de novo, nunca derruba o gateway
                self.stats["errors"] += 1
                if self.connected or self.stats["errors"] == 1:
                    log.warning("ueapd.unreachable", source=self.sid, base_url=self.base,
                                error=f"{type(e).__name__}: {e}"[:200])
                self.connected = False
                wait = min(max(wait, self.poll_interval_s) * 2, self.max_backoff_s)
            await asyncio.sleep(max(0.0, wait - (time.monotonic() - t0)))

    async def stop(self) -> None:
        self._running = False
        self.connected = False
        if self._own_session and self._session is not None and not self._session.closed:
            await self._session.close()
