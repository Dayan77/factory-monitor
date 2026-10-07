"""Worker process (Sprint 5b; Sprint 7): runs the predictive engine (Influx extraction, features, detectors, alerts)
and — with DETECTORS_IN_WORKER (default) — the downtime detector and the maintenance plan engine, outside the
API/ingestion process, so their CPU work never shares the event loop (and the GIL) with MQTT ingestion or the API.

    python -m src.worker            (cwd server/, same environment as the app)

Started and supervised by the app when PREDICTIVE_WORKER=process (default); with PREDICTIVE_WORKER=external run it
as its own service (e.g. a compose service `worker` with the server image and this command). WebSocket messages it
produces reach the browsers through the app (websocket/relay.py, Postgres NOTIFY).
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
    from .services.maintenance.engine import run_maintenance_engine
    from .services.oee.detector import run_downtime_detector
    from .services.predictive.engine import run_predictive_engine
    from .websocket.relay import enable_publisher

    if settings.worker_nice > 0:
        # Sprint 7 (docs/performance.md §2): detectors/predictive/rollups are background work — under CPU pressure
        # the API and the MQTT ingestion go first (their latency is user-facing); the detectors catch up (bounded)
        import os
        try:
            os.nice(settings.worker_nice)
        except OSError:  # pragma: no cover
            pass
    if settings.worker_metrics_port:
        from prometheus_client import start_http_server
        start_http_server(settings.worker_metrics_port + 100, addr="127.0.0.1")
    enable_publisher()
    coros = {}
    if settings.influx_downsampling:
        # Sprint 7: Influx retention + downsampling buckets/tasks (idempotent) and the history backfill
        from .services.influx_downsampling import run_rollup_maintainer
        coros["influx_rollups"] = run_rollup_maintainer
    if settings.predictive_enabled:
        coros["predictive_engine"] = run_predictive_engine
    if settings.detectors_in_worker:
        # Sprint 7: the downtime detector (bounded per-machine concurrency, chunked catch-up) and the plan engine
        coros["downtime_detector"] = run_downtime_detector
        coros["maintenance_engine"] = run_maintenance_engine
    tasks = [asyncio.create_task(fn(), name=name) for name, fn in coros.items()]
    if not tasks:
        log.info("worker.nothing_to_run")
        return
    gathered = asyncio.gather(*tasks)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, gathered.cancel)
        except NotImplementedError:  # pragma: no cover
            pass
    log.info("worker.started", roles=list(coros), tick_s=settings.predictive_tick_s,
             detector_concurrency=settings.detector_concurrency)
    try:
        await gathered
    except asyncio.CancelledError:
        pass
    finally:
        try:
            await asyncio.wait_for(influx.close(), timeout=5)
        except BaseException:  # noqa: BLE001
            pass
        log.info("worker.stopped")


if __name__ == "__main__":
    asyncio.run(main())
