"""/ws/sensors/{tenant_id} requires an access JWT of that tenant (tenant isolation on the realtime channel)."""
import time
import uuid

import pytest
from jose import jwt
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


@pytest.fixture
def wsc(app):
    # No `with`: the lifespan (MQTT subscriber / sweeper) is not started.
    return TestClient(app)


def _close_code(wsc, url, **kw) -> int:
    with pytest.raises(WebSocketDisconnect) as exc:
        with wsc.websocket_connect(url, **kw) as ws:
            ws.receive_text()
    return exc.value.code


def _wait_registered(tenant_id, timeout=5.0):
    """The socket is accepted before auth runs; wait until the handler registered it."""
    from src.websocket.manager import manager
    deadline = time.monotonic() + timeout
    while not manager._connections.get(tenant_id):
        assert time.monotonic() < deadline, "socket never registered"
        time.sleep(0.01)


def _broadcast_and_receive(ws, tenant_id, msg):
    from src.websocket.manager import manager
    _wait_registered(tenant_id)
    ws.portal.call(manager.broadcast, tenant_id, msg)
    return ws.receive_json()


def test_no_token_is_rejected_4401(wsc, tenants):
    assert _close_code(wsc, f"/ws/sensors/{tenants.a.id}") == 4401


@pytest.mark.parametrize("token", ["garbage", "a.b.c"])
def test_invalid_token_is_rejected_4401(wsc, tenants, token):
    assert _close_code(wsc, f"/ws/sensors/{tenants.a.id}?token={token}") == 4401


def test_expired_and_refresh_tokens_are_rejected_4401(wsc, tenants):
    from src.auth.security import create_refresh_token
    from src.config import settings

    user = tenants.a.users["operator"]
    claims = {"sub": user["id"], "tenant_id": tenants.a.id, "role": "operator"}
    expired = jwt.encode({**claims, "exp": int(time.time()) - 10},
                         settings.app_secret_key, algorithm=settings.jwt_algorithm)
    assert _close_code(wsc, f"/ws/sensors/{tenants.a.id}?token={expired}") == 4401
    refresh = create_refresh_token(claims)
    assert _close_code(wsc, f"/ws/sensors/{tenants.a.id}?token={refresh}") == 4401


def test_token_of_unknown_user_is_rejected_4401(wsc, tenants):
    from src.auth.security import create_access_token
    tok = create_access_token({"sub": str(uuid.uuid4()), "tenant_id": tenants.a.id, "role": "admin"})
    assert _close_code(wsc, f"/ws/sensors/{tenants.a.id}?token={tok}") == 4401


def test_other_tenant_token_is_rejected_4403(wsc, tenants):
    tok_b = tenants.b.users["admin"]["token"]
    assert _close_code(wsc, f"/ws/sensors/{tenants.a.id}?token={tok_b}") == 4403
    # forging the tenant_id claim does not help: the user's tenant comes from the DB
    from src.auth.security import create_access_token
    forged = create_access_token({"sub": tenants.b.users["admin"]["id"], "tenant_id": tenants.a.id,
                                  "role": "admin"})
    assert _close_code(wsc, f"/ws/sensors/{tenants.a.id}?token={forged}") == 4403
    assert _close_code(wsc, f"/ws/sensors/not-a-uuid?token={tok_b}") == 4403


def test_own_tenant_token_receives_only_own_tenant_messages(wsc, tenants):
    tok = tenants.a.users["operator"]["token"]
    with wsc.websocket_connect(f"/ws/sensors/{tenants.a.id}?token={tok}") as ws:
        from src.websocket.manager import manager
        _wait_registered(tenants.a.id)
        ws.portal.call(manager.broadcast, tenants.b.id, {"type": "sensor_reading", "tag_id": "b"})
        msg = _broadcast_and_receive(ws, tenants.a.id, {"type": "sensor_reading", "tag_id": "a"})
        assert msg["tag_id"] == "a"   # B's message never reached A's socket


def test_bearer_subprotocol_is_accepted_and_echoed(wsc, tenants):
    tok = tenants.a.users["manager"]["token"]
    with wsc.websocket_connect(f"/ws/sensors/{tenants.a.id}", subprotocols=["bearer", tok]) as ws:
        assert ws.accepted_subprotocol == "bearer"
        msg = _broadcast_and_receive(ws, tenants.a.id, {"type": "machine_status", "machine_id": "m"})
        assert msg["machine_id"] == "m"


def test_superadmin_can_watch_any_tenant(wsc, tenants):
    tok = tenants.superadmin["token"]
    with wsc.websocket_connect(f"/ws/sensors/{tenants.b.id}?token={tok}") as ws:
        msg = _broadcast_and_receive(ws, tenants.b.id, {"type": "sensor_reading", "tag_id": "x"})
        assert msg["tag_id"] == "x"


def test_connection_is_closed_4401_when_token_expires(wsc, tenants):
    from src.config import settings
    user = tenants.a.users["operator"]
    short = jwt.encode({"sub": user["id"], "tenant_id": tenants.a.id, "role": "operator",
                        "exp": int(time.time()) + 2},
                       settings.app_secret_key, algorithm=settings.jwt_algorithm)
    with wsc.websocket_connect(f"/ws/sensors/{tenants.a.id}?token={short}") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
        assert exc.value.code == 4401
