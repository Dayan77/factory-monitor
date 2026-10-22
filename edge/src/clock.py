"""
Política de timestamp por dispositivo (fonte do mapa da planta).

Problema (revisão do Sprint 2): mudanças OPC UA eram carimbadas com o SourceTimestamp do CLP e os
re-envios/polls com o relógio do gateway — a mesma tag misturava dois relógios. Com o relógio do CLP alguns
segundos à frente/atrás, a ordem e as durações de run_state/fault_state no servidor ficavam erradas (e o
servidor descarta leitura mais velha que a última aceita da tag).

Regra: **cada dispositivo usa UM relógio para todas as suas tags**, escolhido no mapa (`sources[].timestamps`):

| política  | mudança com timestamp do CLP          | leitura sem timestamp (poll, re-envio)      |
|-----------|----------------------------------------|---------------------------------------------|
| `gateway` | hora de recebimento no gateway         | hora de recebimento no gateway              |
| `source`  | SourceTimestamp do CLP                 | relógio do CLP estimado = agora + desvio medido na última mudança |

- `gateway` é o padrão (relógio do CLP não é confiável até alguém dizer que é). Modbus não traz timestamp,
  então é sempre `gateway`.
- `source` é para CLP com relógio sincronizado (NTP/PTP). O desvio (SourceTimestamp − recebimento) é medido
  em toda mudança. Atraso de entrega (fila OPC UA, intervalo de publicação, rajada depois de um blip de rede)
  só deixa o SourceTimestamp **mais velho** que o recebimento, nunca mais novo; por isso a estimativa do desvio é
  o **maior** desvio das mudanças recebidas nos últimos `SKEW_HORIZON_S` (a menos atrasada). O dispositivo é
  **rebaixado para `gateway` até o gateway reiniciar** (troca no máximo uma vez, erro
  `edge_gateway.clock_untrusted`) quando: o relógio está à frente mais que `max_clock_skew_s` (padrão 2 s) numa
  mudança (atraso não explica isso), ou atrás mais que isso em **todas** as mudanças de pelo menos
  `SKEW_CONFIRM_S` de recebimento (≥ `SKEW_WINDOW` mudanças) — uma rajada de notificações enfileiradas depois de
  um blip chega em ~1 s e não rebaixa. A leitura que revela o desvio já sai com a hora do gateway. Nada de
  alternar leitura a leitura.
- **Notificação inicial** de cada item monitorado (ao assinar/reassinar): traz o SourceTimestamp da última
  *mudança* do valor, que pode ser de horas atrás num CLP — não é medida de desvio. Na política `source` ela sai
  com esse timestamp (é quando o valor mudou) sem entrar na estimativa; só um timestamp **no futuro** além do
  limite conta como desvio. Na política `gateway`, hora do gateway.
- Na política `gateway`, desvio > `max_clock_skew_s` é só informativo: `edge_gateway.clock_skew` (no máximo
  um aviso a cada 10 min por dispositivo) e `clocks` no heartbeat.
- `source` com `max_clock_skew_s` desligado (`off`): o relógio do dispositivo é usado sem checagem e o gateway
  **não sintetiza** timestamps para ele (sem re-envio de heartbeat), porque não há como mapear o relógio do
  gateway no do dispositivo. É o modo do playback de cenário da planta virtual (relógio deslocado e acelerado).

Por cima de qualquer política o gateway garante timestamps estritamente crescentes por tag (+1 ms quando
necessário), já na resolução publicada (ms).
"""

from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

POLICIES = ("gateway", "source")
SKEW_WARN_INTERVAL_S = 600.0
SKEW_WINDOW = 5          # min. changes behind the limit before a "clock behind" demotion
SKEW_HORIZON_S = 120.0   # changes (receive time) used for the estimate: max offset = least delayed delivery
SKEW_CONFIRM_S = 30.0    # "behind" must persist this long — a burst of queued notifications lasts ~1 s


def parse_max_skew(raw) -> Optional[float]:
    """'2' / 2 → 2.0; 'off' / 'none' / '' / None → None (sem checagem)."""
    if raw is None:
        return None
    if isinstance(raw, str):
        txt = raw.strip().lower()
        if txt in ("", "off", "none", "null", "false", "disabled"):
            return None
        raw = txt
    value = float(raw)
    if value <= 0:
        raise ValueError(f"max_clock_skew_s deve ser > 0 ou 'off' ({raw!r})")
    return value


class DeviceClock:
    """Decide o timestamp de cada leitura de um dispositivo (ver docstring do módulo)."""

    def __init__(self, source_id: str, policy: str = "gateway", max_skew_s: Optional[float] = 2.0,
                 log=None, monotonic: Callable[[], float] = time.monotonic) -> None:
        if policy not in POLICIES:
            raise ValueError(f"política de timestamp inválida: {policy!r} (use gateway|source)")
        self.source_id = source_id
        self.policy = policy
        self.max_skew_s = max_skew_s
        self.demoted = False
        self.offset_s: Optional[float] = None     # estimated SourceTimestamp − receive time (see module doc)
        self._samples: deque[tuple[datetime, float]] = deque(maxlen=512)    # (receive time, offset)
        self._log = log
        self._monotonic = monotonic
        self._last_warn: Optional[float] = None

    @property
    def effective_policy(self) -> str:
        return "gateway" if self.policy == "gateway" or self.demoted else "source"

    @property
    def can_synthesize(self) -> bool:
        """O gateway pode carimbar uma leitura sem timestamp do dispositivo (re-envio/heartbeat)?"""
        return self.effective_policy == "gateway" or self.max_skew_s is not None

    def stamp(self, device_ts: Optional[datetime], now: Optional[datetime] = None,
              tag_id: Optional[str] = None, initial: bool = False) -> datetime:
        now = now or datetime.now(timezone.utc)
        if device_ts is None:
            if self.effective_policy == "source" and self.offset_s is not None:
                return now + timedelta(seconds=self.offset_s)
            return now
        if device_ts.tzinfo is None:
            device_ts = device_ts.replace(tzinfo=timezone.utc)
        skew = (device_ts - now).total_seconds()
        ahead = self.max_skew_s is not None and skew > self.max_skew_s
        if initial and not ahead:
            # last change of a value that may not have changed for hours: not a skew sample
            return device_ts if self.effective_policy == "source" else now
        self._samples.append((now, skew))
        horizon = now - timedelta(seconds=SKEW_HORIZON_S)
        while len(self._samples) > 1 and self._samples[0][0] < horizon:
            self._samples.popleft()
        est = max(v for _, v in self._samples)
        self.offset_s = est
        span = (self._samples[-1][0] - self._samples[0][0]).total_seconds()
        behind = (self.max_skew_s is not None and est < -self.max_skew_s
                  and len(self._samples) >= SKEW_WINDOW and span >= SKEW_CONFIRM_S)
        out_of_bounds = ahead or behind
        if self.effective_policy == "source":
            if not out_of_bounds:
                return device_ts
            self.demoted = True
            self._emit("error", "edge_gateway.clock_untrusted", tag_id, est if behind else skew,
                       "relógio do CLP fora de sincronia: dispositivo passa a usar o horário do gateway até "
                       "reiniciar (sincronize o CLP via NTP ou use timestamps=gateway)")
            return now
        if self.max_skew_s is not None and abs(skew) > self.max_skew_s:
            t = self._monotonic()
            if self._last_warn is None or t - self._last_warn >= SKEW_WARN_INTERVAL_S:
                self._last_warn = t
                self._emit("warning", "edge_gateway.clock_skew", tag_id, skew,
                           "relógio do CLP fora de sincronia; usando o horário do gateway")
        return now

    def _emit(self, level: str, event: str, tag_id: Optional[str], skew: float, hint: str) -> None:
        if self._log is None:
            return
        getattr(self._log, level)(event, source=self.source_id, tag_id=tag_id, skew_s=round(skew, 3),
                                  max_skew_s=self.max_skew_s, policy=self.policy, hint=hint)

    def status(self) -> dict:
        return {"policy": self.effective_policy, "configured": self.policy, "max_skew_s": self.max_skew_s,
                "skew_s": None if self.offset_s is None else round(self.offset_s, 3)}
