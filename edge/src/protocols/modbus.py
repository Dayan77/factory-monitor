"""
Modbus TCP driver — polls registers/bits from a PLC.

Register types (ModbusTagConfig.register_type):
    "holding"  → FC03 (4xxxx)    "input"    → FC04 (3xxxx)
    "coil"     → FC01 (0xxxx)    "discrete" → FC02 (1xxxx)

`address` is the 0-indexed protocol address (Modicon "40001" → holding, address 0).

Data types (ModbusTagConfig.data_type), registers only:
    "uint16" (default), "int16"                  → 1 register
    "uint32", "int32", "float32"                 → 2 consecutive registers
Inside each register the bytes are big-endian (Modbus spec). The order of the two
16-bit words varies by PLC vendor, so it is configurable via `word_order`:
    "big"    → high word first (ABCD; Schneider/Modicon default, most common)
    "little" → low word first  (CDAB; e.g. many Siemens/WAGO/Rockwell gateways)

Engineering value = raw * scale + offset.
"""

import asyncio
import struct
from dataclasses import dataclass, fields
from typing import Awaitable, Callable, Optional

import structlog
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

log = structlog.get_logger()

OnReading = Callable[[str, float], Awaitable[None]]

REGISTER_TYPES = ("holding", "input", "coil", "discrete")
BIT_TYPES = ("coil", "discrete")

# data_type → (register count, struct format for the big-endian byte string)
_DATA_TYPES: dict[str, tuple[int, str]] = {
    "uint16": (1, ">H"),
    "int16": (1, ">h"),
    "uint32": (2, ">I"),
    "int32": (2, ">i"),
    "float32": (2, ">f"),
}


@dataclass
class ModbusTagConfig:
    tag_id: str
    address: int          # register address (0-indexed)
    register_type: str    # "holding", "input", "coil", "discrete"
    scale: float = 1.0
    offset: float = 0.0
    unit: str = ""
    poll_interval_ms: int = 1000
    data_type: str = "uint16"   # see _DATA_TYPES; ignored for coil/discrete
    word_order: str = "big"     # "big" (high word first) | "little" (low word first)
    device_id: int = 1          # Modbus unit/slave id
    # Digital signals and counters must never be suppressed by the deadband filter.
    # None → automatic (True for coil/discrete). Set True for PLC counters.
    no_deadband: Optional[bool] = None

    def __post_init__(self) -> None:
        if self.register_type not in REGISTER_TYPES:
            raise ValueError(f"register_type inválido: {self.register_type!r}")
        if self.register_type not in BIT_TYPES and self.data_type not in _DATA_TYPES:
            raise ValueError(f"data_type inválido: {self.data_type!r}")
        if self.word_order not in ("big", "little"):
            raise ValueError(f"word_order inválido: {self.word_order!r}")

    @property
    def register_count(self) -> int:
        if self.register_type in BIT_TYPES:
            return 1
        return _DATA_TYPES[self.data_type][0]

    @property
    def bypass_deadband(self) -> bool:
        if self.no_deadband is not None:
            return self.no_deadband
        return self.register_type in BIT_TYPES

    @classmethod
    def from_dict(cls, data: "dict | ModbusTagConfig") -> "ModbusTagConfig":
        """Builds a config from a plain dict (e.g. MODBUS_TAGS JSON), ignoring unknown keys."""
        if isinstance(data, cls):
            return data
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def decode_registers(registers: list[int], data_type: str = "uint16", word_order: str = "big") -> float:
    """Decodes 1 or 2 raw 16-bit registers into a number (see module docstring)."""
    count, fmt = _DATA_TYPES[data_type]
    if len(registers) < count:
        raise ValueError(f"{data_type} precisa de {count} registradores, recebeu {len(registers)}")
    words = [r & 0xFFFF for r in registers[:count]]
    if count == 2 and word_order == "little":
        words.reverse()
    raw = b"".join(w.to_bytes(2, "big") for w in words)
    return float(struct.unpack(fmt, raw)[0])


class ModbusDriver:
    def __init__(
        self,
        host: str,
        port: int = 502,
        tags: Optional[list] = None,
        on_reading: Optional[OnReading] = None,
        reconnect_delay_s: float = 5.0,
    ) -> None:
        self._host = host
        self._port = port
        self._tags: list[ModbusTagConfig] = [ModbusTagConfig.from_dict(t) for t in (tags or [])]
        self._on_reading = on_reading
        self._reconnect_delay = reconnect_delay_s
        self._client = AsyncModbusTcpClient(host=host, port=port)
        self._running = False

    @property
    def tags(self) -> list[ModbusTagConfig]:
        return list(self._tags)

    async def connect(self) -> bool:
        if not self._client.connected:
            await self._client.connect()
        return bool(self._client.connected)

    async def run(self) -> None:
        self._running = True
        log.info("modbus.starting", host=self._host, port=self._port, tags=len(self._tags))

        while self._running:
            if not self._client.connected:
                try:
                    ok = await self.connect()
                except Exception as e:  # noqa: BLE001 — keep polling loop alive
                    ok = False
                    log.warning("modbus.connect_failed", error=str(e))
                if not ok:
                    await asyncio.sleep(self._reconnect_delay)
                    continue
                log.info("modbus.connected", host=self._host)

            await self.poll_once()

            min_interval = min((t.poll_interval_ms for t in self._tags), default=1000)
            await asyncio.sleep(min_interval / 1000)

    async def poll_once(self) -> dict[str, float]:
        """Polls every tag once; returns {tag_id: engineering value} for successful reads."""
        results = await asyncio.gather(*[self._poll_tag(tag) for tag in self._tags])
        return {tag.tag_id: v for tag, v in zip(self._tags, results) if v is not None}

    async def _poll_tag(self, tag: ModbusTagConfig) -> Optional[float]:
        try:
            value = await self.read_tag(tag)
            if value is not None and self._on_reading:
                await self._on_reading(tag.tag_id, value)
            return value
        except Exception as e:  # noqa: BLE001
            log.warning("modbus.poll_error", tag_id=tag.tag_id, error=str(e))
            return None

    async def read_tag(self, tag: ModbusTagConfig) -> Optional[float]:
        """Reads one tag and returns its engineering value (raw * scale + offset)."""
        raw = await self._read_raw(tag)
        if raw is None:
            return None
        return raw * tag.scale + tag.offset

    async def _read_raw(self, tag: ModbusTagConfig) -> Optional[float]:
        kw = {"count": tag.register_count, "device_id": tag.device_id}
        if tag.register_type == "holding":
            result = await self._client.read_holding_registers(tag.address, **kw)
        elif tag.register_type == "input":
            result = await self._client.read_input_registers(tag.address, **kw)
        elif tag.register_type == "coil":
            result = await self._client.read_coils(tag.address, **kw)
        else:
            result = await self._client.read_discrete_inputs(tag.address, **kw)

        if result.isError():
            raise ModbusException(f"Error reading {tag.register_type}[{tag.address}]")

        if tag.register_type in BIT_TYPES:
            bits = getattr(result, "bits", None)
            return float(bool(bits[0])) if bits else None

        registers = getattr(result, "registers", None)
        if not registers:
            return None
        return decode_registers(list(registers), tag.data_type, tag.word_order)

    async def stop(self) -> None:
        self._running = False
        self._client.close()
        log.info("modbus.stopped")
