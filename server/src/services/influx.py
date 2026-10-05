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

    def __init__(self) -> None:
        self._client_obj: Optional[InfluxDBClientAsync] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

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
        writer = self._client.write_api()
        await writer.write(bucket=settings.influx_bucket, record=record)

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
