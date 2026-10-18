"""
Contagem de borda no edge para tags `cycle_pulse` (docs/domain.md §3.2).

- `EdgeCounterStore`: contador acumulado por chave, persistido em SQLite (WAL). Monotônico, sobrevive a
  reinício do gateway. A chave é a `key` estável do mapa (não o tag_id), então reprovisionar não zera.
- `PulseEdgeCounter`: detecta borda de subida 0→1. A primeira amostra após (re)conexão só registra o
  nível (não conta), para não contar de novo um pulso que já estava alto antes da queda.
- `PulsePoller`: lê os bits Modbus em polling rápido (padrão 20 ms, agendamento sem deriva), persiste a
  contagem **antes** de publicar e publica sempre o **acumulado** (nunca delta): mensagem perdida ou
  repetida não altera o total. Também republica o acumulado a cada `report_every_s` (o deadband com
  heartbeat decide se vai para o broker). A amostragem só detecta bordas: gravar (fsync do SQLite, lento em
  cartão SD ou disco disputado) e publicar ficam numa tarefa escritora única, em ordem, para um commit lento
  não atrasar a próxima amostra e perder pulso.

Limite físico: o polling DEVE amostrar ≥ 2× por pulso (pulso de 50 ms → ≤ 25 ms). Pulsos durante a queda
do próprio edge são perdidos (a queda só da nuvem é coberta pelo buffer SQLite) — por isso, quando o CLP
tiver contador, prefira `cycle_counter`.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import structlog
from pymodbus.client import AsyncModbusTcpClient

from .plant_map import MapTag

log = structlog.get_logger()

OnCount = Callable[[str, float, Optional[datetime]], Awaitable[None]]


class EdgeCounterStore:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()   # a escritora do PulsePoller grava numa thread; leituras vêm do loop
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")   # contagem confirmada = contagem em disco
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS edge_counters (
                key        TEXT PRIMARY KEY,
                value      INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

    def get(self, key: str) -> int:
        with self._lock:
            return self._get(key)

    def _get(self, key: str) -> int:
        row = self._db.execute("SELECT value FROM edge_counters WHERE key = ?", (key,)).fetchone()
        return int(row[0]) if row else 0

    def add_many(self, increments: dict[str, int]) -> dict[str, int]:
        """Soma incrementos (≥ 0) numa transação; retorna os novos totais."""
        now = datetime.now(timezone.utc).isoformat()
        if any(n < 0 for n in increments.values()):
            raise ValueError("contador do edge é monotônico (incremento negativo)")
        with self._lock:
            with self._db:
                self._db.execute("BEGIN")
                for key, n in increments.items():
                    self._db.execute(
                        "INSERT INTO edge_counters (key, value, updated_at) VALUES (?, ?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = value + excluded.value, "
                        "updated_at = excluded.updated_at",
                        (key, n, now),
                    )
            return {k: self._get(k) for k in increments}

    def add(self, key: str, n: int = 1) -> int:
        return self.add_many({key: n})[key]

    def close(self) -> None:
        with self._lock:
            self._db.close()


class PulseEdgeCounter:
    def __init__(self) -> None:
        self.level: Optional[bool] = None

    def sample(self, level: bool) -> bool:
        """Retorna True numa borda de subida."""
        prev, self.level = self.level, bool(level)
        return prev is False and self.level

    def reset_level(self) -> None:
        self.level = None


class PulsePoller:
    """Polling rápido dos bits `cycle_pulse` de uma fonte Modbus."""

    def __init__(
        self,
        host: str,
        port: int,
        tags: list[MapTag],
        store: EdgeCounterStore,
        on_count: OnCount,
        interval_ms: int = 20,
        report_every_s: float = 1.0,
        reconnect_delay_s: float = 2.0,
        client: Optional[AsyncModbusTcpClient] = None,
    ) -> None:
        for t in tags:
            if not t.is_pulse or t.register_type not in ("coil", "discrete"):
                raise ValueError(f"PulsePoller só aceita cycle_pulse em coil/discrete ({t.key})")
        self.host, self.port = host, port
        self.tags = tags
        self.store = store
        self.on_count = on_count
        self.interval_s = interval_ms / 1000.0
        self.report_every_s = report_every_s
        self.reconnect_delay_s = reconnect_delay_s
        self._client = client or AsyncModbusTcpClient(host=host, port=port, timeout=1, retries=0)
        self._edges = {t.key: PulseEdgeCounter() for t in tags}
        self._running = False
        # bordas detectadas → escritora (persiste e publica em ordem); None = republicar o acumulado
        self._writes: asyncio.Queue[Optional[tuple[dict[str, int], datetime]]] = asyncio.Queue()
        self._writer: Optional[asyncio.Task] = None
        # diagnóstico
        self.polls = 0
        self.max_gap_s = 0.0
        self.overruns = 0

    @property
    def connected(self) -> bool:
        return bool(self._client.connected)

    def totals(self) -> dict[str, int]:
        return {t.key: self.store.get(t.key) for t in self.tags}

    async def _read_levels(self) -> dict[str, bool]:
        """Uma leitura por (unit_id, register_type) cobrindo todos os bits do grupo."""
        groups: dict[tuple[int, str], list[MapTag]] = {}
        for t in self.tags:
            groups.setdefault((t.unit_id, t.register_type), []).append(t)
        levels: dict[str, bool] = {}
        for (unit, rtype), tags in groups.items():
            lo = min(int(t.address) for t in tags)
            hi = max(int(t.address) for t in tags)
            fn = self._client.read_discrete_inputs if rtype == "discrete" else self._client.read_coils
            rr = await fn(lo, count=hi - lo + 1, device_id=unit)
            if rr.isError():
                raise IOError(f"erro lendo {rtype}[{lo}..{hi}] unit {unit}")
            for t in tags:
                levels[t.key] = bool(rr.bits[int(t.address) - lo])
        return levels

    async def poll_once(self, ts: Optional[datetime] = None) -> dict[str, int]:
        """Lê os bits e conta bordas; a escritora persiste e publica (ver `flush`). Retorna {key: incremento}."""
        ts = ts or datetime.now(timezone.utc)
        levels = await self._read_levels()
        self.polls += 1
        inc = {key: 1 for key, lvl in levels.items() if self._edges[key].sample(lvl)}
        if inc:
            self._ensure_writer()
            self._writes.put_nowait((inc, ts))
        return inc

    async def report_totals(self) -> None:
        """Republica o acumulado — pela escritora, para nunca sair um total menor depois de um maior."""
        self._ensure_writer()
        self._writes.put_nowait(None)

    async def flush(self) -> None:
        """Espera tudo o que já foi amostrado estar gravado e publicado."""
        if self._writer is not None and not self._writer.done():
            await self._writes.join()

    def _ensure_writer(self) -> None:
        if self._writer is None or self._writer.done():
            self._writer = asyncio.create_task(self._write_loop())

    async def _write_loop(self) -> None:
        carry: dict[str, int] = {}                   # incrementos de uma gravação que falhou: vão na próxima
        carry_ts: Optional[datetime] = None
        while True:
            items = [await self._writes.get()]
            while not self._writes.empty():          # disco lento: junta o que acumulou numa transação só
                items.append(self._writes.get_nowait())
            inc, ts, report = dict(carry), carry_ts, False
            for it in items:
                if it is None:
                    report = True
                    continue
                for k, n in it[0].items():
                    inc[k] = inc.get(k, 0) + n
                ts = it[1]
            persisted = not inc
            try:
                if inc:
                    totals = await asyncio.to_thread(self.store.add_many, inc)   # persiste antes de publicar
                    persisted = True
                    carry, carry_ts = {}, None
                    for t in self.tags:
                        if t.key in totals:
                            await self.on_count(t.tag_id, float(totals[t.key]), ts)
                if report:
                    now = datetime.now(timezone.utc)
                    for t in self.tags:
                        await self.on_count(t.tag_id, float(self.store.get(t.key)), now)
            except Exception as e:  # noqa: BLE001
                log.error("pulse_poller.write_failed", error=str(e), persisted=persisted,
                          pending=0 if persisted else sum(inc.values()))
                if not persisted:                    # não gravou: nenhum pulso descartado, tenta de novo
                    carry, carry_ts = inc, ts
                    await asyncio.sleep(0.5)
                    self._writes.put_nowait(None)
            finally:
                for _ in items:
                    self._writes.task_done()

    async def run(self) -> None:
        self._running = True
        log.info("pulse_poller.starting", host=self.host, port=self.port, tags=len(self.tags),
                 interval_ms=int(self.interval_s * 1000))
        next_report = 0.0
        while self._running:
            if not self._client.connected:
                try:
                    await self._client.connect()
                except Exception as e:  # noqa: BLE001
                    log.warning("pulse_poller.connect_failed", error=str(e))
                if not self._client.connected:
                    await asyncio.sleep(self.reconnect_delay_s)
                    continue
                for e in self._edges.values():
                    e.reset_level()
                log.info("pulse_poller.connected", host=self.host)
                await self.report_totals()
            next_t = time.monotonic()
            last = None
            while self._running and self._client.connected:
                now = time.monotonic()
                if last is not None:
                    self.max_gap_s = max(self.max_gap_s, now - last)
                last = now
                try:
                    await self.poll_once()
                except Exception as e:  # noqa: BLE001
                    log.warning("pulse_poller.poll_error", error=str(e))
                    self._client.close()
                    break
                if now >= next_report:
                    next_report = now + self.report_every_s
                    await self.report_totals()
                next_t += self.interval_s
                delay = next_t - time.monotonic()
                if delay < 0:
                    self.overruns += 1
                    next_t = time.monotonic()   # atrasou: ressincroniza sem rajada
                    delay = 0
                await asyncio.sleep(delay)

    async def stop(self) -> None:
        self._running = False
        self._client.close()
        try:
            await asyncio.wait_for(self.flush(), timeout=10.0)   # o que já foi contado vai para o disco
        except asyncio.TimeoutError:
            log.error("pulse_poller.flush_timeout", pending=self._writes.qsize())
        if self._writer is not None:
            self._writer.cancel()
            await asyncio.gather(self._writer, return_exceptions=True)
            self._writer = None
