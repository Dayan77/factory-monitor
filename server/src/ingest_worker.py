"""MQTT ingestion worker process (Sprint 7, docs/performance.md).

    python -m src.ingest_worker            (cwd server/, same environment as the app)

Runs the MQTT subscriber + ingestion pipeline (Influx, usage accumulators, machine status, alert rules) outside the
API process, so API requests and ingestion never share one event loop / GIL: under load each one used to slow the
other (an /api/overview of 1 s held 130 readings/s of ingestion). With MQTT_SHARD_COUNT = N > 1, N of these processes
split the tags (crc32(tag_id) % N; order per tag kept) — see services/mqtt_subscriber.shard_of.

Started and supervised by the app when MQTT_INGEST=process (MQTT_INGEST_SHARDS processes); with
MQTT_INGEST=external run one per shard yourself. Live WebSocket messages (readings, usage, machine status) reach the
browsers through the app (websocket/relay.py, batched Postgres NOTIFY). Late-data notes of the downtime detector are
persisted here (the detector runs in src/worker.py).
"""
from __future__ import annotations

import asyncio
import logging
import signal

import structlog

from .config import settings

_level = logging.getLevelName(settings.log_level.upper())
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(
    _level if isinstance(_level, int) else logging.INFO))
log = structlog.get_logger()


async def main() -> None:
    from .services.influx import influx
    from .services.influx_downsampling import run_late_rollups
    from .services.mqtt_subscriber import run_subscriber
    from .services.oee.detector import run_late_flusher
    from .services.usage.broadcaster import usage_broadcaster
    from .websocket.relay import enable_publisher

    if settings.worker_metrics_port:
        from prometheus_client import start_http_server
        start_http_server(settings.worker_metrics_port + settings.mqtt_shard_index, addr="127.0.0.1")
    enable_publisher(batched=True)
    from .rules.engine import rule_tags
    from .services.usage.cache import run_invalidation_listener, topology_cache
    # caches with a long TTL: asset / rule changes of the API process arrive by NOTIFY (usage/cache.py)
    topology_cache.ttl_s = max(topology_cache.ttl_s, settings.ingest_cache_ttl_s)
    rule_tags.ttl_s = max(rule_tags.ttl_s, settings.ingest_cache_ttl_s)

    def invalidate() -> None:
        topology_cache.invalidate()
        rule_tags.invalidate()

    tasks = [asyncio.create_task(run_invalidation_listener(invalidate), name="cache_invalidation"),
             asyncio.create_task(run_subscriber(), name="mqtt_subscriber"),
             asyncio.create_task(usage_broadcaster.run(), name="usage_ws_broadcaster"),
             asyncio.create_task(run_late_flusher(), name="downtime_late_flusher"),
             asyncio.create_task(run_late_rollups(), name="influx_late_rollups")]
    gathered = asyncio.gather(*tasks)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, gathered.cancel)
        except NotImplementedError:  # pragma: no cover
            pass
    log.info("ingest_worker.started", shard=settings.mqtt_shard_index, shards=settings.mqtt_shard_count)
    try:
        await gathered
    except asyncio.CancelledError:
        pass
    finally:
        from .services.mqtt_subscriber import cancel_inflight
        pending = cancel_inflight()
        if pending:
            await asyncio.wait(pending, timeout=settings.shutdown_timeout_s)
        try:
            await asyncio.wait_for(influx.close(), timeout=5)
        except BaseException:  # noqa: BLE001
            pass
        log.info("ingest_worker.stopped")


if __name__ == "__main__":
    asyncio.run(main())
