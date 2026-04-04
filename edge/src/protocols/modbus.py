"""
Modbus TCP driver — polls holding registers from a PLC.

Tag address format: "40001"  → holding register 1 (FC03)
                    "10001"  → discrete input 1   (FC02)
                    "30001"  → input register 1   (FC04)
"""

import asyncio
from dataclasses import dataclass
from typing import Callable, Awaitable, Optional

import structlog
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

log = structlog.get_logger()

OnReading = Callable[[str, float], Awaitable[None]]


@dataclass
class ModbusTagConfig:
    tag_id: str
    address: int          # register address (0-indexed)
    register_type: str    # "holding", "input", "coil", "discrete"
    scale: float = 1.0
    offset: float = 0.0
    unit: str = ""
    poll_interval_ms: int = 1000


class ModbusDriver:
    def __init__(
        self,
        host: str,
        port: int = 502,
        tags: list[ModbusTagConfig] = None,
        on_reading: Optional[OnReading] = None,
    ) -> None:
        self._host = host
        self._port = port
        self._tags = tags or []
        self._on_reading = on_reading
        self._client = AsyncModbusTcpClient(host=host, port=port)
        self._running = False

    async def run(self) -> None:
        self._running = True
        log.info("modbus.starting", host=self._host, port=self._port, tags=len(self._tags))

        while self._running:
            if not self._client.connected:
                try:
                    await self._client.connect()
                    log.info("modbus.connected", host=self._host)
                except Exception as e:
                    log.warning("modbus.connect_failed", error=str(e))
                    await asyncio.sleep(5)
                    continue

            await asyncio.gather(
                *[self._poll_tag(tag) for tag in self._tags],
                return_exceptions=True,
            )

            min_interval = min((t.poll_interval_ms for t in self._tags), default=1000)
            await asyncio.sleep(min_interval / 1000)

    async def _poll_tag(self, tag: ModbusTagConfig) -> None:
        try:
            raw = await self._read_register(tag)
            if raw is None:
                return
            value = raw * tag.scale + tag.offset
            if self._on_reading:
                await self._on_reading(tag.tag_id, value)
        except Exception as e:
            log.warning("modbus.poll_error", tag_id=tag.tag_id, error=str(e))

    async def _read_register(self, tag: ModbusTagConfig) -> Optional[float]:
        if tag.register_type == "holding":
            result = await self._client.read_holding_registers(tag.address, count=1)
        elif tag.register_type == "input":
            result = await self._client.read_input_registers(tag.address, count=1)
        elif tag.register_type == "coil":
            result = await self._client.read_coils(tag.address, count=1)
        elif tag.register_type == "discrete":
            result = await self._client.read_discrete_inputs(tag.address, count=1)
        else:
            return None

        if result.isError():
            raise ModbusException(f"Error reading {tag.register_type}[{tag.address}]")

        registers = getattr(result, "registers", None) or getattr(result, "bits", None)
        return float(registers[0]) if registers else None

    async def stop(self) -> None:
        self._running = False
        self._client.close()
        log.info("modbus.stopped")
