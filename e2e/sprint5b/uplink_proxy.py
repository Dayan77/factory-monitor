"""Minimal TCP forwarder: python tcpproxy.py LISTEN_PORT TARGET_PORT. Killing it = uplink cut (connections dropped,
new ones refused); starting it again = uplink restored."""
import asyncio, sys
LP, TP = int(sys.argv[1]), int(sys.argv[2])
async def pipe(r, w):
    try:
        while (d := await r.read(65536)):
            w.write(d); await w.drain()
    except Exception:
        pass
    finally:
        w.close()
async def handle(cr, cw):
    try:
        sr, sw = await asyncio.open_connection("127.0.0.1", TP)
    except Exception:
        cw.close(); return
    await asyncio.gather(pipe(cr, sw), pipe(sr, cw))
async def main():
    s = await asyncio.start_server(handle, "127.0.0.1", LP)
    print("proxy", LP, "->", TP, flush=True)
    async with s:
        await s.serve_forever()
asyncio.run(main())
