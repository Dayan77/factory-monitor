"""
Contagem de borda no edge para tags `cycle_pulse` (docs/domain.md §3.2).

- `EdgeCounterStore`: contador acumulado por chave, persistido em SQLite (WAL). Monotônico, sobrevive a
  reinício do gateway. A chave é a `key` estável do mapa (não o tag_id), então reprovisionar não zera.
- `PulseEdgeCounter`: detecta borda de subida 0→1. A primeira amostra após (re)conexão só registra o
  nível (não conta), para não contar de novo um pulso que já estava alto antes da queda.
- `PulsePoller`: lê os bits Modbus em polling rápido (padrão 20 ms, agendamento sem deriva), persiste a
  contagem **antes** de publicar e publica sempre o **acumulado** (nunca delta): mensagem perdida ou
  repetida não altera o total. Também republica o acumulado a cada `report_every_s` (o deadband com
  heartbeat decide se vai para o broker).

Limite físico: o polling DEVE amostrar ≥ 2× por pulso (pulso de 50 ms → ≤ 25 ms). Pulsos durante a queda
do próprio edge são perdidos (a queda só da nuvem é coberta pelo buffer SQLite) — por isso, quando o CLP
tiver contador, prefira `cycle_counter`.
"""

from __future__ import annotations

import asyncio
import sqlite3
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
        row = self._db.execute("SELECT value FROM edge_counters WHERE key = ?", (key,)).fetchone()
        return int(row[0]) if row else 0

    def add_many(self, increments: dict[str, int]) -> dict[str, int]:
        """Soma incrementos (≥ 0) numa transação; retorna os novos totais."""
        now = datetime.now(timezone.utc).isoformat()
        with self._db:
            self._db.execute("BEGIN")
            for key, n in increments.items():
                if n < 0:
                    raise ValueError("contador do edge é monotônico (incremento negativo)")
                self._db.execute(
                    "INSERT INTO edge_counters (key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = value + excluded.value, updated_at = excluded.updated_at",
                    (key, n, now),
                )
        return {k: self.get(k) for k in increments}

    def add(self, key: str, n: int = 1) -> int:
        return self.add_many({key: n})[key]

    def close(self) -> None:
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
        """Lê os bits, conta bordas, persiste e publica. Retorna {key: incremento}."""
        ts = ts or datetime.now(timezone.utc)
        levels = await self._read_levels()
        self.polls += 1
        inc = {key: 1 for key, lvl in levels.items() if self._edges[key].sample(lvl)}
        if inc:
            totals = self.store.add_many(inc)            # persiste antes de publicar
            for t in self.tags:
                if t.key in totals:
                    await self.on_count(t.tag_id, float(totals[t.key]), ts)
        return inc

    async def report_totals(self) -> None:
        now = datetime.now(timezone.utc)
        for t in self.tags:
            await self.on_count(t.tag_id, float(self.store.get(t.key)), now)

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
