"""Sprint 7 — response cache of the hot read endpoints (services/response_cache.py, docs/performance.md §6).

Per user and query, short TTL, single flight; a write committed for the tenant drops its entries at once (the user
sees their own change); tenants never share an entry and another tenant's write does not touch them.
"""
from __future__ import annotations

import asyncio

import pytest

from usage_helpers import api, build_plant


@pytest.fixture
def cache_on(monkeypatch):
    from src.config import settings
    from src.services.response_cache import response_cache
    monkeypatch.setattr(settings, "api_cache_ttl_s", 60.0)
    response_cache.clear()
    yield response_cache
    response_cache.clear()


def _count(name, result):
    from src.services.response_cache import CACHE_REQUESTS
    return CACHE_REQUESTS.labels(name, result)._value.get()


async def test_overview_cached_per_user_and_dropped_by_the_tenants_own_write(client, tenants, cache_on):
    a, b = tenants.a, tenants.b
    pa = await build_plant(client, a, "RC-A")
    await build_plant(client, b, "RC-B")
    q = f"/api/overview?plant_id={pa.plant['id']}"
    hits, misses = _count("overview", "hit"), _count("overview", "miss")
    first = await api(client, "GET", q, a.headers("admin"))
    second = await api(client, "GET", q, a.headers("admin"))
    assert second == first and _count("overview", "hit") == hits + 1 and _count("overview", "miss") == misses + 1
    # another user of the same tenant has their own entry (RBAC / operator scope are per request)
    await api(client, "GET", q, a.headers("manager"))
    assert _count("overview", "miss") == misses + 2
    # tenant B never gets A's entry: same query is A's plant → 404 for B, not A's cached body
    r = await client.get(q, headers=b.headers("admin"))
    assert r.status_code == 404
    # a write of tenant B does not drop A's entries …
    await build_plant(client, b, "RC-B2")
    await api(client, "GET", q, a.headers("admin"))
    assert _count("overview", "hit") == hits + 2
    # … a write of tenant A does: the new line is in the very next answer
    ln = await api(client, "POST", "/api/lines/", a.headers("admin"), 201,
                   json={"name": "Linha nova RC", "plant_id": pa.plant["id"]})
    third = await api(client, "GET", q, a.headers("admin"))
    assert ln["id"] in {x["line_id"] for x in third["lines"]} and ln["id"] not in {x["line_id"] for x in first["lines"]}


async def test_concurrent_identical_requests_share_one_computation(client, tenants, cache_on, monkeypatch):
    from src.api.routes import oee as oee_routes
    pa = await build_plant(client, tenants.a, "RC-SF")
    calls = {"n": 0}
    real = oee_routes.load_calendar

    async def counting(*args, **kw):
        calls["n"] += 1
        await asyncio.sleep(0.2)
        return await real(*args, **kw)
    monkeypatch.setattr(oee_routes, "load_calendar", counting)
    q = f"/api/overview?plant_id={pa.plant['id']}"
    res = await asyncio.gather(*[client.get(q, headers=tenants.a.headers("admin")) for _ in range(5)])
    assert all(r.status_code == 200 for r in res) and len({r.text for r in res}) == 1
    assert calls["n"] == 1


async def test_cache_off_by_default_in_tests_recomputes(client, tenants, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "api_cache_ttl_s", 0.0)
    pa = await build_plant(client, tenants.a, "RC-OFF")
    miss = _count("overview", "miss")
    for _ in range(2):
        await api(client, "GET", f"/api/overview?plant_id={pa.plant['id']}", tenants.a.headers("admin"))
    assert _count("overview", "miss") == miss                # bypassed entirely
