"""Sprint 5b — WebSocket relay between the predictive worker process and the app (Postgres NOTIFY fm_ws).

The worker has no sockets: manager.send_to_user / broadcast publish through NOTIFY, the app's listener delivers
to the local sockets of the right tenant/user. Big messages are shrunk below the NOTIFY limit."""
from __future__ import annotations

import asyncio
import json


class FakeWS:
    def __init__(self):
        self.got = []

    async def send_json(self, m):
        self.got.append(m)


async def test_messages_published_by_the_worker_reach_the_app_sockets_of_that_user_only():
    from src.websocket import relay
    from src.websocket.manager import ConnectionManager, manager
    a_user, b_user = FakeWS(), FakeWS()
    await manager.connect(a_user, "t-a", accept=False, user_id="u1")
    await manager.connect(b_user, "t-b", accept=False, user_id="u1")
    listener = asyncio.create_task(relay.run_listener())
    try:
        await asyncio.sleep(1.0)
        worker_side = ConnectionManager()
        worker_side.relay = relay.PgNotifyPublisher()
        await worker_side.send_to_user("t-a", "u1", {"type": "predictive_alert", "event": "opened",
                                                     "alert": {"id": "x1", "severity": "warning"}})
        await worker_side.broadcast("t-b", {"type": "sensor_reading", "value": 1})
        for _ in range(50):
            if a_user.got and b_user.got:
                break
            await asyncio.sleep(0.1)
    finally:
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)
        manager.disconnect(a_user, "t-a")
        manager.disconnect(b_user, "t-b")
    assert a_user.got == [{"type": "predictive_alert", "event": "opened", "alert": {"id": "x1", "severity": "warning"}}]
    assert b_user.got == [{"type": "sensor_reading", "value": 1}]       # tenant B's socket got only tenant B's


def test_big_messages_are_shrunk_below_the_notify_limit():
    from src.websocket import relay
    msg = {"type": "predictive_alert", "event": "opened",
           "alert": {"id": "a1", "severity": "critical", "explanation": "x" * 20000}}
    raw = relay.encode("user", "t", "u", msg)
    assert len(raw.encode()) < 8000
    body = json.loads(raw)
    assert body["m"] == {"type": "predictive_alert", "truncated": True, "event": "opened",
                         "alert": {"id": "a1", "severity": "critical"}}
