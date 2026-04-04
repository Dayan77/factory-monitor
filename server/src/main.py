"""
Factory Monitor — FastAPI application entry point.
"""

import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from .config import settings
from .api.routes import auth, lines, machines, tags, alerts, sensors, ai_chat
from .websocket.manager import ws_router
from .services.mqtt_subscriber import run_subscriber

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("server.starting", env=settings.app_env)

    # Start MQTT subscriber as background task
    mqtt_task = asyncio.create_task(run_subscriber())

    yield

    mqtt_task.cancel()
    try:
        await mqtt_task
    except asyncio.CancelledError:
        pass

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

Instrumentator().instrument(app).expose(app, endpoint="/metrics")

app.include_router(auth.router,     prefix="/api/auth",     tags=["auth"])
app.include_router(lines.router,    prefix="/api/lines",    tags=["lines"])
app.include_router(machines.router, prefix="/api/machines", tags=["machines"])
app.include_router(tags.router,     prefix="/api/tags",     tags=["tags"])
app.include_router(alerts.router,   prefix="/api/alerts",   tags=["alerts"])
app.include_router(sensors.router,  prefix="/api/sensors",  tags=["sensors"])
app.include_router(ai_chat.router,  prefix="/api/ai",       tags=["ai"])

app.include_router(ws_router, prefix="/ws")


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": app.version, "env": settings.app_env}
