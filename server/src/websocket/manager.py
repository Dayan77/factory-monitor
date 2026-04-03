"""
WebSocket connection manager.
Broadcasts real-time sensor readings to subscribed dashboard clients.
Each client subscribes to a tenant + optional list of tag IDs.
"""

import json
from collections import defaultdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import structlog

log = structlog.get_logger()

ws_router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        # tenant_id → set of WebSocket connections
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, ws: WebSocket, tenant_id: str) -> None:
        await ws.accept()
        self._connections[tenant_id].add(ws)
        log.info("ws.connected", tenant_id=tenant_id, total=len(self._connections[tenant_id]))

    def disconnect(self, ws: WebSocket, tenant_id: str) -> None:
        self._connections[tenant_id].discard(ws)

    async def broadcast(self, tenant_id: str, message: dict) -> None:
        dead = set()
        for ws in self._connections.get(tenant_id, set()):
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._connections[tenant_id].discard(ws)


manager = ConnectionManager()


@ws_router.websocket("/sensors/{tenant_id}")
async def sensor_stream(ws: WebSocket, tenant_id: str):
    await manager.connect(ws, tenant_id)
    try:
        while True:
            # Keep connection alive; server pushes via manager.broadcast()
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws, tenant_id)
        log.info("ws.disconnected", tenant_id=tenant_id)
