"""
Factory Monitor — FastAPI application entry point.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from .config import settings
from .api.routes import (auth, lines, machines, tags, alerts, sensors, ai_chat, admin, cameras,
                         plants, positions, components, usage, public, production, oee, maintenance,
                         notifications, integration, users)
from .websocket.manager import ws_router
from .services.mqtt_subscriber import run_subscriber, cancel_inflight
from .services.oee.detector import run_downtime_detector
from .services.machine_status import run_offline_sweeper
from .services.influx import influx
from .services.usage.broadcaster import usage_broadcaster
from .services.maintenance.engine import run_maintenance_engine, run_webhook_worker

# LOG_LEVEL (default INFO): structlog printed every level, i.e. one DEBUG line per MQTT reading.
_level = logging.getLevelName(settings.log_level.upper())
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(
    _level if isinstance(_level, int) else logging.INFO))

log = structlog.get_logger()


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

    # Start MQTT subscriber as background task
    # Background tasks: MQTT ingestion + offline machine sweeper
    tasks = [
        asyncio.create_task(run_subscriber(), name="mqtt_subscriber"),
        asyncio.create_task(run_offline_sweeper(), name="machine_offline_sweeper"),
        asyncio.create_task(usage_broadcaster.run(), name="usage_ws_broadcaster"),
        asyncio.create_task(run_downtime_detector(), name="downtime_detector"),
        asyncio.create_task(run_maintenance_engine(), name="maintenance_engine"),
        asyncio.create_task(run_webhook_worker(), name="webhook_worker"),
    ]

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
app.include_router(maintenance.plans_router,    prefix="/api/maintenance-plans",   tags=["maintenance"])
app.include_router(maintenance.wo_router,       prefix="/api/work-orders",         tags=["maintenance"])
app.include_router(maintenance.failures_router, prefix="/api/failures",            tags=["maintenance"])
app.include_router(maintenance.codes_router,    prefix="/api/failure-codes",       tags=["maintenance"])
app.include_router(maintenance.mkpi_router,     prefix="/api/maintenance",         tags=["maintenance"])
app.include_router(notifications.router,        prefix="/api/notifications",       tags=["notifications"])
app.include_router(notifications.push_router,   prefix="/api/push",                tags=["notifications"])
app.include_router(integration.tokens_router,   prefix="/api/api-tokens",          tags=["integration"])
app.include_router(integration.webhooks_router, prefix="/api/webhooks",            tags=["integration"])

app.include_router(ws_router, prefix="/ws")


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": app.version, "env": settings.app_env}
