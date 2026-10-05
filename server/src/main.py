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
                         plants, positions, components, usage, public)
from .websocket.manager import ws_router
from .services.mqtt_subscriber import run_subscriber
from .services.machine_status import run_offline_sweeper
from .services.influx import influx
from .services.usage.broadcaster import usage_broadcaster

# LOG_LEVEL (default INFO): structlog printed every level, i.e. one DEBUG line per MQTT reading.
_level = logging.getLevelName(settings.log_level.upper())
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(
    _level if isinstance(_level, int) else logging.INFO))

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("server.starting", env=settings.app_env)

    # Start MQTT subscriber as background task
    # Background tasks: MQTT ingestion + offline machine sweeper
    tasks = [
        asyncio.create_task(run_subscriber(), name="mqtt_subscriber"),
        asyncio.create_task(run_offline_sweeper(), name="machine_offline_sweeper"),
        asyncio.create_task(usage_broadcaster.run(), name="usage_ws_broadcaster"),
    ]

    yield

    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
    await influx.close()

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

app.include_router(ws_router, prefix="/ws")


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": app.version, "env": settings.app_env}
