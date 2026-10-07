"""
Factory Monitor — FastAPI application entry point.
"""

import asyncio
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from .config import settings
from .api.routes import (auth, lines, machines, tags, alerts, sensors, ai_chat, admin, cameras,
                         plants, positions, components, usage, public, production, oee, maintenance,
                         notifications, integration, users, gateways, predictive)
from .api.routes import layout as layout_routes, onboarding as onboarding_routes, demo as demo_routes
from .services.demo.guard import DemoGuardMiddleware
from .services.demo.bot import run_demo_bot
from .api.routes import reports as reports_routes
from .services.reports.engine import run_report_engine
from .websocket.manager import ws_router
from .services.mqtt_subscriber import run_subscriber, cancel_inflight
from .services.oee.detector import run_downtime_detector, run_late_flusher
from .services.oee.lag import run_lag_sampler
from .services.machine_status import run_offline_sweeper
from .services.influx import influx
from .services.usage.broadcaster import usage_broadcaster
from .services.maintenance.engine import run_maintenance_engine, run_webhook_worker
from .services.predictive.engine import run_predictive_engine
from .websocket.relay import run_listener as ws_relay_listener
from .api.routes import account as account_routes
from .services.security import http as security_http
from .services.security.privacy import run_privacy_janitor

# LOG_LEVEL (default INFO): structlog printed every level, i.e. one DEBUG line per MQTT reading.
_level = logging.getLevelName(settings.log_level.upper())
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(
    _level if isinstance(_level, int) else logging.INFO))

log = structlog.get_logger()

SERVER_DIR = Path(__file__).resolve().parent.parent


async def supervise_worker(module: str = "src.worker", extra_env: Optional[dict] = None) -> None:
    """PREDICTIVE_WORKER=process: runs `python -m src.worker` (predictive engine + detectors) as a child process,
    restarts it if it dies, and stops it with the app (SIGTERM, then SIGKILL after 5 s). Sprint 7: also used for
    the ingestion shards (MQTT_INGEST=process: `python -m src.ingest_worker` with MQTT_SHARD_INDEX/COUNT)."""
    proc = None
    try:
        while True:
            proc = await asyncio.create_subprocess_exec(sys.executable, "-m", module, cwd=str(SERVER_DIR),
                                                        env={**os.environ, **(extra_env or {})})
            log.info("worker.spawned", pid=proc.pid)
            rc = await proc.wait()
            log.error("worker.exited", returncode=rc, restart_in_s=5)
            proc = None
            await asyncio.sleep(5)
    finally:
        if proc is not None and proc.returncode is None:
            try:
                proc.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=2)
            except (ProcessLookupError, asyncio.TimeoutError, asyncio.CancelledError):
                pass


async def shutdown_tasks(tasks: list[asyncio.Task], timeout_s: float) -> None:
    """Clean shutdown that always finishes (Sprint 2 review: uvicorn hung on exit while MQTT reading
    tasks were still pending). Background loops and in-flight MQTT readings are cancelled and waited
    for at most `timeout_s`; whatever is still running after that is abandoned (a reading not
    processed is re-counted by recompute / is a duplicate on re-delivery)."""
    for task in tasks:
        task.cancel()
    inflight = cancel_inflight()
    pending = [*tasks, *inflight]
    if pending:
        _, still = await asyncio.wait(pending, timeout=timeout_s)
        if still:
            log.warning("server.shutdown_abandoned_tasks", count=len(still),
                        names=[t.get_name() for t in list(still)[:10]])
    try:
        await asyncio.wait_for(influx.close(), timeout=timeout_s)
    except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
        log.warning("server.influx_close_failed", error=str(e)[:200])


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("server.starting", env=settings.app_env)

    # Background tasks (MQTT ingestion and the detectors below, inline or in worker processes)
    tasks = [
        asyncio.create_task(run_offline_sweeper(), name="machine_offline_sweeper"),
        asyncio.create_task(run_webhook_worker(), name="webhook_worker"),
        asyncio.create_task(run_privacy_janitor(), name="privacy_janitor"),
    ]
    if settings.reports_enabled:
        tasks.append(asyncio.create_task(run_report_engine(), name="report_engine"))
    if settings.demo_bot_enabled:
        tasks.append(asyncio.create_task(run_demo_bot(), name="demo_bot"))
    # Sprint 7: detectors (downtime, plans) run in the worker process with the predictive engine — never on the API
    # event loop — unless PREDICTIVE_WORKER=inline or DETECTORS_IN_WORKER=false
    # Sprint 7: MQTT ingestion in its own process(es) (MQTT_INGEST=process|external) — API and ingestion do not share
    # an event loop; N shards split the tags
    ingest = (settings.mqtt_ingest or "inline").lower()
    if ingest == "inline":
        tasks.append(asyncio.create_task(run_subscriber(), name="mqtt_subscriber"))
        tasks.append(asyncio.create_task(usage_broadcaster.run(), name="usage_ws_broadcaster"))
    elif ingest == "process":
        n = max(1, int(settings.mqtt_ingest_shards))
        for k in range(n):
            tasks.append(asyncio.create_task(supervise_worker("src.ingest_worker", {
                "MQTT_SHARD_INDEX": str(k), "MQTT_SHARD_COUNT": str(n)}), name=f"ingest_worker_{k}"))
    mode = (settings.predictive_worker or "process").lower()
    detectors_out = settings.detectors_in_worker and mode in ("process", "external")
    if detectors_out:
        tasks.append(asyncio.create_task(run_late_flusher(), name="downtime_late_flusher"))
    else:
        tasks.append(asyncio.create_task(run_downtime_detector(), name="downtime_detector"))
        tasks.append(asyncio.create_task(run_maintenance_engine(), name="maintenance_engine"))
    tasks.append(asyncio.create_task(run_lag_sampler(), name="detector_lag_sampler"))
    if settings.predictive_enabled and mode == "inline":
        tasks.append(asyncio.create_task(run_predictive_engine(), name="predictive_engine"))
    elif settings.predictive_enabled or detectors_out:
        if mode == "process":
            tasks.append(asyncio.create_task(supervise_worker(), name="predictive_worker"))
    if ingest != "inline" or (mode != "inline" and (settings.predictive_enabled or detectors_out)):
        # the workers (child or external services) send WebSocket messages through Postgres NOTIFY
        tasks.append(asyncio.create_task(ws_relay_listener(), name="ws_relay_listener"))

    yield

    await shutdown_tasks(tasks, timeout_s=settings.shutdown_timeout_s)
    log.info("server.shutdown")


app = FastAPI(
    title="Factory Monitor API",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# Public demo (Sprint 6): demo_viewer tokens are read-only on every route, blocked from admin areas, and their JSON
# responses never carry an e-mail. Inside CORS so its 403/429 still get the CORS headers.
app.add_middleware(DemoGuardMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Sprint 7 (docs/security.md §3): request size limit, security headers, JSON errors without stack traces, exact CORS
security_http.install(app)

# Real client IP/scheme behind Traefik/nginx (used by the per-IP lead rate limit). Added last =
# outermost, so every other middleware and route sees the corrected request.client.
# Run uvicorn with --no-proxy-headers: this is the single place proxy headers are trusted.
_trusted_proxies = [h.strip() for h in settings.forwarded_allow_ips.split(",") if h.strip()]
if "*" in _trusted_proxies:
    raise RuntimeError("FORWARDED_ALLOW_IPS='*' permite falsificar o IP do cliente; use os IPs/CIDRs do proxy")
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=_trusted_proxies)

Instrumentator().instrument(app).expose(app, endpoint="/metrics")

app.include_router(auth.router,     prefix="/api/auth",     tags=["auth"])
app.include_router(lines.router,    prefix="/api/lines",    tags=["lines"])
app.include_router(machines.router, prefix="/api/machines", tags=["machines"])
app.include_router(tags.router,     prefix="/api/tags",     tags=["tags"])
app.include_router(alerts.router,   prefix="/api/alerts",   tags=["alerts"])
app.include_router(sensors.router,  prefix="/api/sensors",  tags=["sensors"])
app.include_router(ai_chat.router,  prefix="/api/ai",       tags=["ai"])
app.include_router(admin.router,    prefix="/api/admin",    tags=["admin"])
app.include_router(cameras.router,  prefix="/api/cameras",  tags=["cameras"])
app.include_router(plants.router,     prefix="/api/plants",     tags=["plants"])
app.include_router(positions.router,  prefix="/api/positions",  tags=["positions"])
app.include_router(components.router, prefix="/api/components", tags=["components"])
app.include_router(usage.router,      prefix="/api/usage",      tags=["usage"])
app.include_router(public.router,     prefix="/api/public",     tags=["public"])
app.include_router(production.shifts_router,   prefix="/api/shifts",              tags=["production"])
app.include_router(production.calendar_router, prefix="/api/calendar-exceptions", tags=["production"])
app.include_router(production.products_router, prefix="/api/products",            tags=["production"])
app.include_router(production.machine_products_router, prefix="/api/machines",    tags=["production"])
app.include_router(production.runs_router,     prefix="/api/production-runs",     tags=["production"])
app.include_router(production.reasons_router,  prefix="/api/reason-codes",        tags=["production"])
app.include_router(oee.downtime_router,        prefix="/api/downtime",            tags=["oee"])
app.include_router(oee.kpi_router,             prefix="/api",                     tags=["oee"])

app.include_router(users.router,                prefix="/api/users",               tags=["users"])
app.include_router(account_routes.router,       prefix="/api/account",             tags=["account"])
app.include_router(maintenance.plans_router,    prefix="/api/maintenance-plans",   tags=["maintenance"])
app.include_router(maintenance.wo_router,       prefix="/api/work-orders",         tags=["maintenance"])
app.include_router(maintenance.failures_router, prefix="/api/failures",            tags=["maintenance"])
app.include_router(maintenance.codes_router,    prefix="/api/failure-codes",       tags=["maintenance"])
app.include_router(maintenance.mkpi_router,     prefix="/api/maintenance",         tags=["maintenance"])
app.include_router(notifications.router,        prefix="/api/notifications",       tags=["notifications"])
app.include_router(notifications.push_router,   prefix="/api/push",                tags=["notifications"])
app.include_router(integration.tokens_router,   prefix="/api/api-tokens",          tags=["integration"])
app.include_router(integration.webhooks_router, prefix="/api/webhooks",            tags=["integration"])
app.include_router(gateways.router,             prefix="/api/gateways",            tags=["gateways"])
app.include_router(predictive.router,           prefix="/api/predictive",          tags=["predictive"])
app.include_router(layout_routes.plants_router,  prefix="/api/plants",              tags=["layout"])
app.include_router(layout_routes.layouts_router, prefix="/api/layouts",             tags=["layout"])
app.include_router(onboarding_routes.router,     prefix="/api/onboarding",          tags=["onboarding"])
app.include_router(demo_routes.router,           prefix="/api/demo",                tags=["demo"])

app.include_router(reports_routes.router,           prefix="/api/reports",           tags=["reports"])
app.include_router(reports_routes.runs_router,      prefix="/api/report-runs",       tags=["reports"])
app.include_router(reports_routes.schedules_router, prefix="/api/report-schedules",  tags=["reports"])

app.include_router(ws_router, prefix="/ws")


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": app.version, "env": settings.app_env}
