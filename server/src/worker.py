"""Predictive worker process (Sprint 5b): runs the predictive engine (Influx extraction, features, detectors,
alerts) outside the API/ingestion process, so its CPU work never shares the event loop (and the GIL) with MQTT
ingestion.

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
    from .services.predictive.engine import run_predictive_engine
    from .websocket.relay import enable_publisher

    enable_publisher()
    task = asyncio.create_task(run_predictive_engine(), name="predictive_engine")
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, task.cancel)
        except NotImplementedError:  # pragma: no cover
            pass
    log.info("worker.started", role="predictive", tick_s=settings.predictive_tick_s)
    try:
        await task
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
