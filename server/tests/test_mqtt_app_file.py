"""C1 — broker "app file" with the gateways' MQTT credentials (services/mqtt_app_file.py): format / order / header,
every line accepted by the broker's rules (infra/mosquitto/prod/render.sh, checked with POSIX grep -E like the
script), atomic rewrite only on change, enroll adds / revoke removes the line, suspended tenant leaves the file,
disabled when MQTT_APP_GATEWAYS_FILE is unset, a write failure never fails the request, `managed` flag."""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from conftest import _make_tenant
from usage_helpers import api, build_plant

USER_ERE = r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$"
TENANT_ERE = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
# = APP_HASH_RE of infra/mosquitto/prod/render.sh (tolerant check of the app file)
HASH_ERE = r"^\$(7\$[0-9]{1,7}|6)\$[A-Za-z0-9+/]+={0,2}\$[A-Za-z0-9+/]+={0,2}$"


def _grep_ok(pattern: str, value: str) -> bool:
    """Same check render.sh does: printf '%s' "$v" | grep -Eq '<ERE>'."""
    return subprocess.run(["grep", "-Eq", pattern], input=value.encode(), check=False).returncode == 0


def _assert_broker_accepts(content: str) -> list[str]:
    from src.services.mqtt_app_file import HEADER
    lines = content.split("\n")
    assert lines[0] == HEADER and content.endswith("\n")
    body = [ln for ln in lines[1:] if ln and not ln.startswith("#")]
    users = []
    for ln in body:
        parts = ln.split()
        assert len(parts) == 3 and " ".join(parts) == ln, ln
        user, tenant, hsh = parts
        if shutil.which("grep"):
            assert _grep_ok(USER_ERE, user) and _grep_ok(TENANT_ERE, tenant) and _grep_ok(HASH_ERE, hsh), ln
        users.append(user)
    assert users == sorted(users) and len(set(users)) == len(users)
    return body


@pytest.fixture
def app_file(tmp_path, monkeypatch):
    from src.config import settings
    path = tmp_path / "mqtt" / "gateways.app.conf"          # directory created on the first write
    monkeypatch.setattr(settings, "mqtt_app_gateways_file", str(path))
    return path


async def _tenant(label):
    from src.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, label)
        await s.commit()
    return t


async def _enrolled_gateway(client, t, label):
    p = await build_plant(client, t, label)
    h = t.headers("admin")
    gw = await api(client, "POST", "/api/gateways/", h, 201, json={"name": f"gw {label}", "plant_id": p.plant["id"]})
    code = (await api(client, "POST", f"/api/gateways/{gw['id']}/enroll-token", h))["code"]
    enr = await api(client, "POST", "/api/gateways/enroll", {}, json={"code": code})
    return gw, enr


def test_render_format_order_and_broker_rules(monkeypatch):
    from src.config import settings
    from src.services import mqtt_app_file as maf
    monkeypatch.setattr(settings, "mqtt_username", "fm-backend")
    t1, t2 = str(uuid.uuid4()), str(uuid.uuid4())
    good = "$7$101$c2FsdHNhbHQ=$aGFzaA=="
    rows = [("gw-bbbbbbbb-0002", t2, good), ("gw-aaaaaaaa-0001", t1, good),
            ("gw-cccccccc-0003", "not-a-uuid", good),           # tenant not a UUID → skipped
            ("x", t1, good),                                     # user too short → skipped
            ("gw-dddddddd-0004", t1, "plaintext"),               # not a mosquitto hash → skipped
            ("gw-aaaaaaaa-0001", t2, good),                      # repeated user → skipped
            ("fm-health", t1, good), ("fm-backend", t1, good)]   # reserved users → skipped
    content = maf.render(rows)
    body = _assert_broker_accepts(content)
    assert body == [f"gw-aaaaaaaa-0001 {t1} {good}", f"gw-bbbbbbbb-0002 {t2} {good}"]
    assert maf.render([]) == maf.HEADER + "\n"


def test_server_hash_regex_is_the_broker_one():
    """S6: HASH_RE = render.sh APP_HASH_RE; a real enrollment hash passes; every rendered line matches it."""
    import re

    from src.services import gateways as gs
    from src.services import mqtt_app_file as maf
    render_sh = (Path(__file__).resolve().parents[2] / "infra" / "mosquitto" / "prod" / "render.sh")
    if render_sh.exists():
        m = re.search(r"^APP_HASH_RE='([^']+)'", render_sh.read_text(encoding="utf-8"), re.M)
        if m:
            assert m.group(1) == maf.HASH_RE.pattern == HASH_ERE
    t = str(uuid.uuid4())
    real = gs.mosquitto_hash("s3cret-password")
    rows = [("gw-aaaaaaaa-0001", t, real), ("gw-bbbbbbbb-0002", t, "$6$c2FsdA==$aGFzaA=="),
            ("gw-cccccccc-0003", t, "$7$101$c2FsdA==$aGFzaA==$extra"),       # broker would refuse → skipped
            ("gw-dddddddd-0004", t, "$7$abc$c2FsdA==$aGFzaA==")]
    body = [ln for ln in maf.render(rows).splitlines()[1:]]
    assert [ln.split()[0] for ln in body] == ["gw-aaaaaaaa-0001", "gw-bbbbbbbb-0002"]
    assert all(maf.HASH_RE.match(ln.split()[2]) for ln in body)
    if shutil.which("grep"):
        assert all(_grep_ok(HASH_ERE, ln.split()[2]) for ln in body)


def test_atomic_write_only_on_change(tmp_path):
    from src.services.mqtt_app_file import write_if_changed
    p = tmp_path / "sub" / "gateways.app.conf"
    assert write_if_changed(p, "a\n") is True
    st = p.stat()
    assert oct(st.st_mode & 0o777) == "0o644"
    assert write_if_changed(p, "a\n") is False
    st2 = p.stat()
    assert (st2.st_ino, st2.st_mtime_ns) == (st.st_ino, st.st_mtime_ns)       # untouched
    assert write_if_changed(p, "b\n") is True
    assert p.read_text() == "b\n" and p.stat().st_ino != st.st_ino             # replaced (rename), not rewritten
    assert sorted(os.listdir(p.parent)) == ["gateways.app.conf"]              # no temporary left behind


async def test_disabled_when_unset(client, tmp_path, monkeypatch):
    from src.config import settings
    from src.services import mqtt_app_file as maf
    monkeypatch.setattr(settings, "mqtt_app_gateways_file", "")
    assert await maf.sync() is None and await maf.sync_quietly() is None
    t = await _tenant("MAFOFF")
    gw, _ = await _enrolled_gateway(client, t, "MAFOFF")
    h = t.headers("admin")
    mq = await api(client, "GET", f"/api/gateways/{gw['id']}/mqtt", h)
    assert mq["managed"] is False and mq["gateways_conf_line"]
    assert (mq["sync_ok"], mq["sync_at"], mq["sync_error"]) == (None, None, None)
    rv = await api(client, "POST", f"/api/gateways/{gw['id']}/revoke", h)
    assert rv["mqtt_managed"] is False and "make mqtt-gateway-remove" in rv["mqtt_action"]
    assert list(tmp_path.iterdir()) == []


async def test_enroll_adds_and_revoke_removes_the_line(client, app_file):
    from src.services import mqtt_app_file as maf
    a, b = await _tenant("MAFA"), await _tenant("MAFB")
    gwa, enra = await _enrolled_gateway(client, a, "MAFA")
    gwb, enrb = await _enrolled_gateway(client, b, "MAFB")
    ha = a.headers("admin")
    content = app_file.read_text(encoding="utf-8")
    body = _assert_broker_accepts(content)
    mqa = await api(client, "GET", f"/api/gateways/{gwa['id']}/mqtt", ha)
    mqb = await api(client, "GET", f"/api/gateways/{gwb['id']}/mqtt", b.headers("admin"))
    assert mqa["managed"] is True and mqb["managed"] is True
    assert mqa["sync_ok"] is True and mqa["sync_error"] is None and mqa["sync_at"]
    # each line carries its own tenant (→ ACL factory/<tenant>/#): A's user is bound to A, B's to B
    assert mqa["gateways_conf_line"] in body and mqb["gateways_conf_line"] in body
    assert mqa["gateways_conf_line"].split()[:2] == [enra["mqtt"]["username"], a.id]
    assert mqb["gateways_conf_line"].split()[:2] == [enrb["mqtt"]["username"], b.id]
    assert (await api(client, "GET", f"/api/gateways/{gwa['id']}", ha))["mqtt_managed"] is True

    # nothing changed → not rewritten
    st = app_file.stat()
    assert await maf.sync() is False
    assert (app_file.stat().st_ino, app_file.stat().st_mtime_ns) == (st.st_ino, st.st_mtime_ns)

    # revoke: the line disappears (A only), the action says it is automatic
    rv = await api(client, "POST", f"/api/gateways/{gwa['id']}/revoke", ha)
    assert rv["mqtt_revoked"] is True and rv["mqtt_managed"] is True
    assert "automaticamente" in rv["mqtt_action"] and "mqtt-gateway-remove" not in rv["mqtt_action"]
    assert "a linha manual tem prioridade" in rv["mqtt_action"]
    mq = await api(client, "GET", f"/api/gateways/{gwa['id']}/mqtt", ha)
    assert mq["revoked"] is True and mq["managed"] is True and mq["gateways_conf_line"] is None
    body = _assert_broker_accepts(app_file.read_text(encoding="utf-8"))
    assert all(not ln.startswith(enra["mqtt"]["username"] + " ") for ln in body)
    assert mqb["gateways_conf_line"] in body
    # delete (after revoke) keeps the file consistent
    await api(client, "DELETE", f"/api/gateways/{gwa['id']}", ha, 204)
    assert mqb["gateways_conf_line"] in app_file.read_text(encoding="utf-8")


async def test_suspended_tenant_leaves_the_file(client, app_file, tenants):
    t = await _tenant("MAFS")
    gw, enr = await _enrolled_gateway(client, t, "MAFS")
    user = enr["mqtt"]["username"]
    assert f"\n{user} {t.id} " in app_file.read_text(encoding="utf-8")
    sa = {"Authorization": f"Bearer {tenants.superadmin['token']}"}
    await api(client, "PATCH", f"/api/admin/tenants/{t.id}", sa, json={"is_active": False})
    assert f"\n{user} " not in app_file.read_text(encoding="utf-8")
    await api(client, "PATCH", f"/api/admin/tenants/{t.id}", sa, json={"is_active": True})
    assert f"\n{user} {t.id} " in app_file.read_text(encoding="utf-8")


async def test_write_failure_never_fails_the_request_and_is_reported(client, tmp_path, monkeypatch):
    """I5: `managed` is configuration; sync_ok / sync_at / sync_error (and the Prometheus gauge) tell what really
    happened at the last attempt — unwritable path → false + short error, fixed → true."""
    from prometheus_client import REGISTRY

    from src.config import settings
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setattr(settings, "mqtt_app_gateways_file", str(blocker / "gateways.app.conf"))
    t = await _tenant("MAFERR")
    h = t.headers("admin")
    gw, enr = await _enrolled_gateway(client, t, "MAFERR")          # enroll still 200
    assert enr["token"]
    mq = await api(client, "GET", f"/api/gateways/{gw['id']}/mqtt", h)
    assert mq["managed"] is True and mq["sync_ok"] is False and mq["sync_at"]
    assert mq["sync_error"] and len(mq["sync_error"]) <= 160 and "$7$" not in mq["sync_error"]
    assert REGISTRY.get_sample_value("fm_mqtt_app_file_sync_ok") == 0
    failures = REGISTRY.get_sample_value("fm_mqtt_app_file_sync_failures_total")
    assert failures and failures >= 1

    good = tmp_path / "ok" / "gateways.app.conf"
    monkeypatch.setattr(settings, "mqtt_app_gateways_file", str(good))
    rv = await api(client, "POST", f"/api/gateways/{gw['id']}/revoke", h)    # revoke still 200, sync now works
    assert rv["mqtt_revoked"] is True and good.exists()
    mq = await api(client, "GET", f"/api/gateways/{gw['id']}/mqtt", h)
    assert mq["sync_ok"] is True and mq["sync_error"] is None
    assert REGISTRY.get_sample_value("fm_mqtt_app_file_sync_ok") == 1
