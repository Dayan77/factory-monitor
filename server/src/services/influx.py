"""
InfluxDB service — writes and queries time-series sensor data.
Measurement: "sensors"
Tags:        tenant_id, tag_id, machine_id, unit
Field:       value (float)
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync

from ..config import settings
from .validation import ensure_flux_duration, ensure_uuid


def _rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class InfluxService:
    """Lazily creates the async client inside the running event loop.

    InfluxDBClientAsync binds an aiohttp session to the loop that exists when
    it is constructed, so it must not be created at import time (importing the
    app outside a loop — tests, scripts — used to crash).
    """

    # Writes are coalesced: while one HTTP write is in flight, the readings that arrive meanwhile are queued
    # and sent together as the next request (up to WRITE_BATCH_MAX points). A lone write goes out at once (no
    # added latency); under load (gateway buffer flush, many tags) one request carries many points instead of
    # one request per reading, which was the MQTT ingestion bottleneck.
    WRITE_BATCH_MAX = 5000

    def __init__(self) -> None:
        self._client_obj: Optional[InfluxDBClientAsync] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pending: list = []
        self._draining = False
        self._queue_loop: Optional[asyncio.AbstractEventLoop] = None
        self._drain_task: Optional[asyncio.Task] = None
        self.write_requests = 0          # HTTP write requests sent (observability / tests)

    @property
    def _client(self) -> InfluxDBClientAsync:
        loop = asyncio.get_running_loop()
        if self._client_obj is None or self._loop is not loop:
            self._client_obj = InfluxDBClientAsync(
                url=settings.influx_url,
                token=settings.influx_token,
                org=settings.influx_org,
            )
            self._loop = loop
        return self._client_obj

    async def write(
        self,
        tenant_id: str,
        tag_id: str,
        value: float,
        machine_id: Optional[str] = None,
        unit: Optional[str] = None,
        ts: Optional[datetime] = None,
    ) -> None:
        tenant_id = ensure_uuid(tenant_id, "tenant_id")
        tag_id = ensure_uuid(tag_id, "tag_id")
        record = {
            "measurement": "sensors",
            "tags": {
                "tenant_id":  tenant_id,
                "tag_id":     tag_id,
                "machine_id": machine_id or "",
                "unit":       unit or "",
            },
            "fields":    {"value": value},
            "time":      (ts or datetime.now(timezone.utc)).isoformat(),
        }
        await self._enqueue(record)

    async def _enqueue(self, record: dict) -> None:
        loop = asyncio.get_running_loop()
        if self._queue_loop is not loop:
            self._pending, self._draining, self._queue_loop = [], False, loop
        fut = loop.create_future()
        self._pending.append((record, fut))
        if not self._draining:
            self._draining = True
            self._drain_task = loop.create_task(self._drain(), name="influx_write_drain")
        await fut

    async def _send(self, records: list) -> None:
        self.write_requests += 1
        await self._client.write_api().write(bucket=settings.influx_bucket, record=records)

    async def _drain(self) -> None:
        live: list = []
        try:
            while self._pending:
                batch = self._pending[:self.WRITE_BATCH_MAX]
                self._pending = self._pending[self.WRITE_BATCH_MAX:]
                live = [(r, f) for r, f in batch if not f.done()]     # a caller that gave up (timeout) is skipped
                if not live:
                    continue
                try:
                    await self._send([r for r, _ in live])
                    results = [None] * len(live)
                except Exception as e:  # noqa: BLE001
                    if len(live) == 1:
                        results = [e]
                    else:
                        # one bad point must not fail its neighbours: retry one by one (idempotent in Influx)
                        results = []
                        for r, _ in live:
                            try:
                                await self._send([r])
                                results.append(None)
                            except Exception as e1:  # noqa: BLE001
                                results.append(e1)
                for (_, f), res in zip(live, results):
                    if f.done():
                        continue
                    if res is None:
                        f.set_result(None)
                    else:
                        f.set_exception(res)
        finally:
            self._draining = False
            if asyncio.current_task() is not None and asyncio.current_task().cancelling():
                for _, f in live + self._pending:     # shutdown: nobody waits forever on a dropped write
                    if not f.done():
                        f.cancel()
                self._pending = []

    async def query_history(
        self,
        tenant_id: str,
        tag_id: str,
        start: Optional[datetime] = None,
        stop: Optional[datetime] = None,
        window: str = "1m",
    ) -> list[dict]:
        # Everything interpolated into Flux is validated first (no injection).
        tenant_id = ensure_uuid(tenant_id, "tenant_id")
        tag_id = ensure_uuid(tag_id, "tag_id")
        window = ensure_flux_duration(window, "window")
        if start is None:
            start = datetime.now(timezone.utc) - timedelta(hours=1)
        if stop is None:
            stop = datetime.now(timezone.utc)

        flux = f"""
        from(bucket: "{settings.influx_bucket}")
          |> range(start: {_rfc3339(start)}, stop: {_rfc3339(stop)})
          |> filter(fn: (r) => r["_measurement"] == "sensors")
          |> filter(fn: (r) => r["tenant_id"] == "{tenant_id}")
          |> filter(fn: (r) => r["tag_id"] == "{tag_id}")
          |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)
          |> yield(name: "mean")
        """
        tables = await self._client.query_api().query(flux, org=settings.influx_org)

        rows = []
        for table in tables:
            for record in table.records:
                rows.append({"timestamp": record.get_time().isoformat(), "value": record.get_value()})
        return rows

    async def latest(self, tenant_id: str, tag_ids: list[str]) -> dict[str, float]:
        """Returns the most recent value for each tag_id."""
        tenant_id = ensure_uuid(tenant_id, "tenant_id")
        tag_ids = [ensure_uuid(t, "tag_id") for t in tag_ids]
        if not tag_ids:
            return {}
        ids_filter = " or ".join(f'r["tag_id"] == "{t}"' for t in tag_ids)
        flux = f"""
        from(bucket: "{settings.influx_bucket}")
          |> range(start: -5m)
          |> filter(fn: (r) => r["_measurement"] == "sensors")
          |> filter(fn: (r) => r["tenant_id"] == "{tenant_id}")
          |> filter(fn: (r) => {ids_filter})
          |> last()
        """
        tables = await self._client.query_api().query(flux, org=settings.influx_org)

        result = {}
        for table in tables:
            for record in table.records:
                result[record.values.get("tag_id")] = record.get_value()
        return result

    async def close(self) -> None:
        if self._client_obj is not None:
            await self._client_obj.close()
            self._client_obj = None
            self._loop = None


influx = InfluxService()
