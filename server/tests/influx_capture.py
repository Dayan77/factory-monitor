"""Test helper (Sprint 7): capture what the MQTT pipeline writes to InfluxDB without writing it.

The subscriber sends each batch with influx.write_many(records) (line-protocol strings, or dict records for values
the fast path does not cover); tests that used to patch influx.write per reading use capture_writes(), which patches
both and returns the readings as dicts {tenant_id, tag_id, machine_id, unit, value, ts}."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def parse_record(r) -> dict:
    if isinstance(r, dict):
        t = r["tags"]
        return {"tenant_id": t["tenant_id"], "tag_id": t["tag_id"], "machine_id": t.get("machine_id") or None,
                "unit": t.get("unit") or None, "value": r["fields"]["value"],
                "ts": datetime.fromisoformat(r["time"])}
    head, field, ns = r.rsplit(" ", 2)
    tags = dict(kv.split("=", 1) for kv in head.split(",")[1:])
    ns = int(ns)
    return {"tenant_id": tags["tenant_id"], "tag_id": tags["tag_id"], "machine_id": tags.get("machine_id"),
            "unit": tags.get("unit"), "value": float(field.split("=", 1)[1]),
            "ts": _EPOCH + timedelta(microseconds=ns // 1000)}


def capture_writes(monkeypatch, influx) -> list[dict]:
    seen: list[dict] = []

    def write_many(records):
        seen.extend(parse_record(r) for r in records)
        fut = asyncio.get_running_loop().create_future()
        fut.set_result(None)
        return fut

    async def write(**kw):
        seen.append({k: kw.get(k) for k in ("tenant_id", "tag_id", "machine_id", "unit", "value", "ts")})
    monkeypatch.setattr(influx, "write_many", write_many)
    monkeypatch.setattr(influx, "write", write)
    return seen
