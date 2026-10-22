"""InfluxService against the real InfluxDB (unique tenant ids, cleaned up)."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from src.config import settings
from src.services.influx import influx
from src.services.validation import InvalidIdentifier


async def _delete_tenant_points(tenant_id: str) -> None:
    start = datetime(1970, 1, 1, tzinfo=timezone.utc)
    stop = datetime.now(timezone.utc) + timedelta(days=1)
    await influx._client.delete_api().delete(
        start=start, stop=stop, predicate=f'_measurement="sensors" AND tenant_id="{tenant_id}"',
        bucket=settings.influx_bucket, org=settings.influx_org,
    )


@pytest_asyncio.fixture
async def influx_tenant():
    tid = str(uuid.uuid4())
    yield tid
    await _delete_tenant_points(tid)


async def test_write_latest_and_history(influx_tenant):
    tag = str(uuid.uuid4())
    other_tag = str(uuid.uuid4())
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for i, v in enumerate([10.0, 20.0, 30.0]):
        await influx.write(tenant_id=influx_tenant, tag_id=tag, value=v, unit="°C",
                           ts=now - timedelta(minutes=3 - i))
    await influx.write(tenant_id=influx_tenant, tag_id=other_tag, value=7.5, ts=now - timedelta(seconds=30))

    latest = await influx.latest(influx_tenant, [tag, other_tag])
    assert latest == {tag: 30.0, other_tag: 7.5}

    rows = await influx.query_history(influx_tenant, tag, start=now - timedelta(minutes=10),
                                      stop=now + timedelta(seconds=1), window="1m")
    assert sorted(r["value"] for r in rows) == [10.0, 20.0, 30.0]
    assert all("timestamp" in r for r in rows)

    # Aggregation: one 10-minute window → mean of the three
    rows = await influx.query_history(influx_tenant, tag, start=now - timedelta(minutes=10),
                                      stop=now + timedelta(seconds=1), window="10m")
    assert [round(r["value"], 6) for r in rows] in ([20.0], [15.0, 30.0], [10.0, 25.0])


async def test_other_tenant_sees_nothing(influx_tenant):
    tag = str(uuid.uuid4())
    await influx.write(tenant_id=influx_tenant, tag_id=tag, value=1.0)
    other = str(uuid.uuid4())
    assert await influx.latest(other, [tag]) == {}
    assert await influx.query_history(other, tag) == []


async def test_naive_datetimes_are_treated_as_utc(influx_tenant):
    tag = str(uuid.uuid4())
    now = datetime.now(timezone.utc).replace(microsecond=0)
    await influx.write(tenant_id=influx_tenant, tag_id=tag, value=3.0, ts=now - timedelta(minutes=1))
    naive_start = (now - timedelta(minutes=5)).replace(tzinfo=None)
    rows = await influx.query_history(influx_tenant, tag, start=naive_start, window="1m")
    assert [r["value"] for r in rows] == [3.0]


INJECTIONS = [
    'abc',
    '") or true or ("',
    '00000000-0000-0000-0000-000000000000") |> drop(columns: ["x',
    '',
]


@pytest.mark.parametrize("bad", INJECTIONS)
async def test_service_rejects_non_uuid_ids(bad):
    good = str(uuid.uuid4())
    with pytest.raises(InvalidIdentifier):
        await influx.latest(good, [bad])
    with pytest.raises(InvalidIdentifier):
        await influx.latest(bad, [good])
    with pytest.raises(InvalidIdentifier):
        await influx.query_history(bad, good)
    with pytest.raises(InvalidIdentifier):
        await influx.query_history(good, bad)
    with pytest.raises(InvalidIdentifier):
        await influx.write(tenant_id=bad, tag_id=good, value=1)


@pytest.mark.parametrize("window", ["1m) |> drop()", "1 m", "", "0s", "m", "1y"])
async def test_service_rejects_bad_window(window):
    with pytest.raises(InvalidIdentifier):
        await influx.query_history(str(uuid.uuid4()), str(uuid.uuid4()), window=window)


async def test_sensor_endpoints(client, tenants):
    tag = str(uuid.uuid4())
    h = tenants.a.headers("operator")
    try:
        await influx.write(tenant_id=tenants.a.id, tag_id=tag, value=42.0)
        r = await client.get("/api/sensors/latest", params={"tag_ids": [tag]}, headers=h)
        assert r.status_code == 200 and r.json() == {tag: 42.0}
        r = await client.get("/api/sensors/history", params={"tag_id": tag, "window": "1m"}, headers=h)
        assert r.status_code == 200
        assert r.json()["tag_id"] == tag and [p["value"] for p in r.json()["points"]] == [42.0]
        # tenant B cannot read tenant A's series even knowing the tag id
        hb = tenants.b.headers("operator")
        assert (await client.get("/api/sensors/latest", params={"tag_ids": [tag]}, headers=hb)).json() == {}
        # injection attempts are rejected before reaching Flux
        bad = '") or true or ("'
        assert (await client.get("/api/sensors/latest", params={"tag_ids": [bad]}, headers=h)).status_code == 422
        assert (await client.get("/api/sensors/history", params={"tag_id": bad}, headers=h)).status_code == 422
        r = await client.get("/api/sensors/history", params={"tag_id": tag, "window": "1m)|>drop("}, headers=h)
        assert r.status_code == 422
    finally:
        await _delete_tenant_points(tenants.a.id)
