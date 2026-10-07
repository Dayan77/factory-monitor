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


_ESC_TAG = str.maketrans({",": r"\,", "=": r"\=", " ": r"\ ", "\n": r"\n", "\t": r"\t", "\r": r"\r"})
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _tag_value(v: str) -> str:
    out = str(v).translate(_ESC_TAG)
    return out + " " if out.endswith("\\") else out


def line_protocol(tenant_id: str, tag_id: str, machine_id: str, unit: str, value: float, ts: datetime
                  ) -> Optional[str]:
    """Sprint 7 (scale): the line-protocol of one reading, built directly — byte-for-byte what the client library
    produces from the equivalent dict record (tags sorted, empty tags left out, the same escaping, whole floats
    without ".0", nanosecond timestamp), at a fraction of its CPU cost (the dict → Point conversion was a hot spot
    at 2 400 readings/s). None: a value the fast path does not cover (non-finite) → the caller uses the dict."""
    import math
    v = float(value)
    if not math.isfinite(v):
        return None
    sv = str(v)
    if sv.endswith(".0"):
        sv = sv[:-2]
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    d = ts - _EPOCH
    ns = (d.days * 86400 + d.seconds) * 1_000_000_000 + d.microseconds * 1000
    tags = []
    if machine_id:
        tags.append("machine_id=" + _tag_value(machine_id))
    tags.append("tag_id=" + _tag_value(tag_id))
    tags.append("tenant_id=" + _tag_value(tenant_id))
    if unit:
        tags.append("unit=" + _tag_value(unit))
    return f"sensors,{','.join(tags)} value={sv} {ns}"


class _GroupWaiter:
    """The future-like handle _drain settles for each record of a write_many() group: the group's future completes
    when its last record is settled (an exception if any record failed)."""

    __slots__ = ("fut", "left", "error")

    def __init__(self, fut: "asyncio.Future", n: int) -> None:
        self.fut, self.left, self.error = fut, n, None

    def done(self) -> bool:
        return self.fut.done()

    def _settle(self) -> None:
        self.left -= 1
        if self.left <= 0 and not self.fut.done():
            if self.error is None:
                self.fut.set_result(None)
            else:
                self.fut.set_exception(self.error)

    def set_result(self, _value) -> None:
        self._settle()

    def set_exception(self, e: BaseException) -> None:
        if self.error is None:
            self.error = e
        self._settle()

    def cancel(self) -> None:
        self.fut.cancel()


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
        line = line_protocol(tenant_id, tag_id, machine_id or "", unit or "", value,
                             ts or datetime.now(timezone.utc))
        if line is not None:
            await self._enqueue(line)
            return
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

    def write_many(self, records: list) -> "asyncio.Future":
        """Sprint 7 (scale): queues several records (line-protocol strings from line_protocol() or dict records) and
        returns ONE future that completes when all of them are written — or fails with the first error if any of
        them could not be (the others may be stored: Influx writes are idempotent, a replay is harmless). One future
        per MQTT batch instead of one task + one future per reading."""
        loop = asyncio.get_running_loop()
        if self._queue_loop is not loop:
            self._pending, self._draining, self._queue_loop = [], False, loop
        fut = loop.create_future()
        if not records:
            fut.set_result(None)
            return fut
        waiter = _GroupWaiter(fut, len(records))
        self._pending.extend((r, waiter) for r in records)
        if not self._draining:
            self._draining = True
            self._drain_task = loop.create_task(self._drain(), name="influx_write_drain")
        return fut

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

    async def _send_retrying(self, records: list) -> None:
        """One batch; a transient failure (Influx restarting, network, 5xx/429) is retried with backoff for up to
        settings.mqtt_transient_retry_s — the whole batch, as one request (no per-reading retry storm). Callers
        wait meanwhile, so an Influx outage turns into backpressure (the MQTT pipeline stops acknowledging)."""
        from .ingest.transient import retry_transient
        from .metrics import INFLUX_WRITE_RETRIES

        await retry_transient("influx.write", lambda: self._send(records), settings.mqtt_transient_retry_s,
                              on_retry=lambda n, e: INFLUX_WRITE_RETRIES.inc(), points=len(records))

    async def _drain(self) -> None:
        from .ingest.transient import is_transient
        from .metrics import INFLUX_WRITE_BATCH
        live: list = []
        try:
            while self._pending:
                batch = self._pending[:self.WRITE_BATCH_MAX]
                self._pending = self._pending[self.WRITE_BATCH_MAX:]
                live = [(r, f) for r, f in batch if not f.done()]     # a caller that gave up (timeout) is skipped
                if not live:
                    continue
                INFLUX_WRITE_BATCH.observe(len(live))
                try:
                    await self._send_retrying([r for r, _ in live])
                    results = [None] * len(live)
                except Exception as e:  # noqa: BLE001
                    if len(live) == 1 or is_transient(e):
                        results = [e] * len(live)          # retry budget exhausted: every caller learns it
                    else:
                        # one bad point must not fail its neighbours: retry one by one (idempotent in Influx)
                        results = []
                        for r, _ in live:
                            try:
                                await self._send_retrying([r])
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
                live = []
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

        # Sprint 7 (docs/performance.md §5): long ranges read the 1-minute / hourly aggregates for the covered part
        from . import influx_downsampling as ds
        cov = await ds.read_coverage(cached=True) if settings.influx_downsampling else None
        rows = []
        for src, a, b in ds.plan_history(start, stop, ds.duration_s(window), cov):
            rows += await self._history_part(src, tenant_id, tag_id, a, b, window)
        return rows

    async def _history_part(self, src: str, tenant_id: str, tag_id: str, start: datetime, stop: datetime,
                            window: str) -> list[dict]:
        if src == "raw":
            flux = f"""
            from(bucket: "{settings.influx_bucket}")
              |> range(start: {_rfc3339(start)}, stop: {_rfc3339(stop)})
              |> filter(fn: (r) => r["_measurement"] == "sensors")
              |> filter(fn: (r) => r["tenant_id"] == "{tenant_id}")
              |> filter(fn: (r) => r["tag_id"] == "{tag_id}")
              |> filter(fn: (r) => r["_field"] == "value")
              |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)
              |> yield(name: "mean")
            """
        else:
            # exact mean of the window from the aggregates: Σsum / Σcount
            flux = f"""
            from(bucket: "{settings.influx_bucket}_{src}")
              |> range(start: {_rfc3339(start)}, stop: {_rfc3339(stop)})
              |> filter(fn: (r) => r["_measurement"] == "sensors")
              |> filter(fn: (r) => r["tenant_id"] == "{tenant_id}")
              |> filter(fn: (r) => r["tag_id"] == "{tag_id}")
              |> filter(fn: (r) => r["_field"] == "sum" or r["_field"] == "count")
              |> group(columns: ["_field"])
              |> aggregateWindow(every: {window}, fn: sum, createEmpty: false)
              |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
              |> filter(fn: (r) => r.count > 0.0)
              |> map(fn: (r) => ({{_time: r._time, _value: r.sum / r.count}}))
              |> yield(name: "mean")
            """
        tables = await self._client.query_api().query(flux, org=settings.influx_org)
        rows = []
        for table in tables:
            for record in table.records:
                rows.append({"timestamp": record.get_time().isoformat(), "value": record.get_value()})
        rows.sort(key=lambda r: r["timestamp"])
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
