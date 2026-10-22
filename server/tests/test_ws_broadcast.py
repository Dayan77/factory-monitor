"""ConnectionManager.broadcast while clients connect/disconnect concurrently."""
import asyncio

from src.websocket.manager import ConnectionManager


class SlowWS:
    def __init__(self, mgr, tenant, joiner=None, fail=False):
        self.mgr, self.tenant, self.joiner, self.fail, self.got = mgr, tenant, joiner, fail, []

    async def accept(self, subprotocol=None):
        pass

    async def send_json(self, msg):
        await asyncio.sleep(0)                 # yields: other clients (dis)connect meanwhile
        if self.joiner is not None:
            await self.mgr.connect(self.joiner, self.tenant)   # set grows mid-iteration
        if self.fail:
            raise ConnectionError("gone")
        self.got.append(msg)


async def test_broadcast_survives_connect_and_disconnect_during_send():
    mgr, t = ConnectionManager(), "t1"
    late = SlowWS(mgr, t)
    a = SlowWS(mgr, t, joiner=late)
    b = SlowWS(mgr, t)
    dead = SlowWS(mgr, t, fail=True)
    for ws in (a, b, dead):
        await mgr.connect(ws, t)
    await mgr.broadcast(t, {"n": 1})           # used to raise "Set changed size during iteration"
    assert a.got == [{"n": 1}] and b.got == [{"n": 1}]
    assert dead not in mgr._connections[t] and late in mgr._connections[t]
    a.joiner = None
    await mgr.broadcast(t, {"n": 2})
    assert late.got == [{"n": 2}] and a.got[-1] == {"n": 2}
