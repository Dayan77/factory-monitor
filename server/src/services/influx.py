"""
InfluxDB service — writes and queries time-series sensor data.
Measurement: "sensors"
Tags:        tenant_id, tag_id, machine_id, unit
Field:       value (float)
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync

from ..config import settings


class InfluxService:
    def __init__(self) -> None:
        self._client = InfluxDBClientAsync(
            url=settings.influx_url,
            token=settings.influx_token,
            org=settings.influx_org,
        )

    async def write(
        self,
        tenant_id: str,
        tag_id: str,
        value: float,
        machine_id: Optional[str] = None,
        unit: Optional[str] = None,
        ts: Optional[datetime] = None,
    ) -> None:
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
        if start is None:
            start = datetime.now(timezone.utc) - timedelta(hours=1)
        if stop is None:
            stop = datetime.now(timezone.utc)

        flux = f"""
        from(bucket: "{settings.influx_bucket}")
          |> range(start: {start.isoformat()}, stop: {stop.isoformat()})
          |> filter(fn: (r) => r["measurement"] == "sensors")
          |> filter(fn: (r) => r["tenant_id"] == "{tenant_id}")
          |> filter(fn: (r) => r["tag_id"] == "{tag_id}")
          |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)
          |> yield(name: "mean")
        """
        async with self._client.query_api() as q:
            tables = await q.query(flux, org=settings.influx_org)

        rows = []
        for table in tables:
            for record in table.records:
                rows.append({"timestamp": record.get_time().isoformat(), "value": record.get_value()})
        return rows

    async def latest(self, tenant_id: str, tag_ids: list[str]) -> dict[str, float]:
        """Returns the most recent value for each tag_id."""
        ids_filter = " or ".join(f'r["tag_id"] == "{t}"' for t in tag_ids)
        flux = f"""
        from(bucket: "{settings.influx_bucket}")
          |> range(start: -5m)
          |> filter(fn: (r) => r["measurement"] == "sensors")
          |> filter(fn: (r) => r["tenant_id"] == "{tenant_id}")
          |> filter(fn: (r) => {ids_filter})
          |> last()
        """
        async with self._client.query_api() as q:
            tables = await q.query(flux, org=settings.influx_org)

        result = {}
        for table in tables:
            for record in table.records:
                result[record.values.get("tag_id")] = record.get_value()
        return result

    async def close(self) -> None:
        await self._client.close()


influx = InfluxService()
