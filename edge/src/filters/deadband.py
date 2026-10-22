"""
Deadband filter — suppresses readings that changed less than N% from the last
*published* value. Prevents flooding the broker with noise from analog sensors.

Digital signals and counters must NOT go through the deadband (a 0→1 edge or a
+1 count is always meaningful): call `should_publish(..., bypass=True)` for them,
which publishes on ANY change (exact comparison) instead of the % threshold.

Heartbeat (max silence): the server derives machine status from data — a machine
with no reading for MACHINE_OFFLINE_TIMEOUT_S (60 s) becomes 'unknown'
(server/src/services/machine_status.py). A healthy gateway reading a stable value
would otherwise publish nothing and look offline, so a tag that has not been
published for `max_silence_s` is re-published even if unchanged (bypass tags too).
Keep max_silence_s well below the server timeout (default 30 s).
"""

import math
import time
from typing import Callable, Optional


class DeadbandFilter:
    def __init__(
        self,
        deadband_pct: float = 0.5,
        max_silence_s: Optional[float] = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if deadband_pct < 0:
            raise ValueError("deadband_pct deve ser >= 0")
        if max_silence_s is not None and max_silence_s <= 0:
            raise ValueError("max_silence_s deve ser > 0 (ou None para desativar)")
        self._deadband = deadband_pct / 100.0
        self._max_silence = max_silence_s
        self._clock = clock
        self._last: dict[str, float] = {}
        self._last_ts: dict[str, float] = {}

    def should_publish(self, tag_id: str, value: float, bypass: bool = False,
                       now: Optional[float] = None, force: bool = False,
                       deadband_pct: Optional[float] = None) -> bool:
        """`now` (segundos): relógio da leitura para medir o silêncio (o gateway passa o timestamp da
        leitura, assim um dispositivo com relógio próprio — ex. playback acelerado — tem heartbeat no seu
        tempo); padrão = relógio do filtro. `force`: publica mesmo sem mudança (evento por ciclo, ex. tempo de
        curso de cada golpe). `deadband_pct`: banda desta tag (substitui a global)."""
        if isinstance(value, float) and math.isnan(value):
            return False  # never publish NaN (server rejects / corrupts aggregates)

        now = self._clock() if now is None else now
        last = self._last.get(tag_id)
        band = self._deadband if deadband_pct is None else deadband_pct / 100.0
        if last is None or force:
            changed = True
        elif bypass:
            changed = value != last
        elif last == 0:
            changed = value != 0
        elif band == 0:
            changed = value != last
        else:
            changed = abs(value - last) / abs(last) >= band

        if not changed and self._max_silence is not None:
            changed = now - self._last_ts.get(tag_id, now) >= self._max_silence

        if changed:
            self._last[tag_id] = value
            self._last_ts[tag_id] = now
        return changed

    def reset(self, tag_id: str | None = None) -> None:
        if tag_id is None:
            self._last.clear()
            self._last_ts.clear()
        else:
            self._last.pop(tag_id, None)
            self._last_ts.pop(tag_id, None)
