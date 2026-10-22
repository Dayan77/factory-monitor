"""World for the authorization fuzz (test_security_authz_matrix.py): tenant A with one user per role, a demo viewer,
an enrolled gateway, read/write API tokens and one row of (almost) every resource type; tenant B with an admin and an
operator; a victim tenant for the superadmin routes. Every tenant-A name carries MARK so a leak into tenant B shows."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

HUMAN_ROLES = ("operator", "technician", "production_supervisor", "maintenance_planner", "manager", "admin",
               "superadmin")


@dataclass
class World:
    mark: str
    a_id: str
    b_id: str
    victim_tenant_id: str
    victim_tenant_user_id: str
    ids: dict = field(default_factory=dict)          # key -> tenant A id
    tokens: dict = field(default_factory=dict)       # principal -> Authorization header value
    users: dict = field(default_factory=dict)        # role -> user id (tenant A)
    markers: set = field(default_factory=set)        # strings that must never appear in a tenant B response

    def header(self, principal: str) -> dict:
        v = self.tokens.get(principal)
        return {"Authorization": v} if v else {}


def _tok(user_id: str, tenant_id: str, role: str, **extra) -> str:
    from src.auth.security import create_access_token
    return "Bearer " + create_access_token({"sub": user_id, "tenant_id": tenant_id, "role": role, **extra},
                                           ttl=timedelta(hours=2))


PASSWORD = "Ponte-Vermelha#4821"
_HASH: list = []


async def make_user(s, tenant_id: str, role: str, label: str, roles=None) -> str:
    from src.auth.security import hash_password
    from src.models import User, UserRole
    if not _HASH:
        _HASH.append(hash_password(PASSWORD))
    u = User(tenant_id=tenant_id, email=f"{label}.{uuid.uuid4().hex[:10]}@example.com", password_hash=_HASH[0],
             name=label, role=UserRole(role), roles=sorted(set(roles or [role]) | ({role} if role else set())))
    s.add(u)
    await s.flush()
    return u.id


async def fresh_token(role: str, tenant_id: str) -> str:
    """A throwaway user of `role` (self-destructive routes: logout-all, account deletion...)."""
    from src.db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        uid = await make_user(s, tenant_id, role, f"tmp-{role}")
        await s.commit()
    return _tok(uid, tenant_id, role)


async def build_world() -> World:
    from src.auth.deps import hash_api_token
    from src.db import AsyncSessionLocal
    from src.models import (AiConversation, AlertEvent, AlertRule, ApiToken, CalendarException, Camera, Component,
                            ComponentType, DowntimeEvent, FailureRecord, Gateway, GatewayCommand, Line, Machine,
                            MachineProduct, MaintenanceAbsence, MaintenancePlan, Notification, Plant, PlantLayout,
                            Position, PredictiveAlert, PredictiveMonitor, Product, ProductionRun, ReasonCode,
                            ReportExternalRecipient, ReportRun, ReportSchedule, Shift, Tag, Tenant, Webhook,
                            WorkOrder)
    from src.services import gateways as gs
    mark = "ZMARK" + uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as s:
        ta = Tenant(name=f"Tenant A {mark}", slug=f"fz-a-{uuid.uuid4().hex[:8]}", max_users=500, max_machines=500)
        tb = Tenant(name="Tenant B fuzz", slug=f"fz-b-{uuid.uuid4().hex[:8]}", max_users=500, max_machines=500)
        tv = Tenant(name="Tenant victim", slug=f"fz-v-{uuid.uuid4().hex[:8]}")
        s.add_all([ta, tb, tv])
        await s.flush()
        vu = await make_user(s, tv.id, "operator", "victim-tenant-user")
        w = World(mark=mark, a_id=ta.id, b_id=tb.id, victim_tenant_id=tv.id, victim_tenant_user_id=vu)
        for role in HUMAN_ROLES:
            uid = await make_user(s, ta.id, role, f"a-{role}-{mark}")
            w.users[role] = uid
            w.tokens[role] = _tok(uid, ta.id, role)
        demo = await make_user(s, ta.id, "manager", f"a-demo-{mark}", roles=["manager", "demo_viewer"])
        w.tokens["demo_viewer"] = _tok(demo, ta.id, "demo_viewer", demo_session=str(uuid.uuid4()))
        for role in ("admin", "operator"):
            uid = await make_user(s, tb.id, role, f"b-{role}")
            w.tokens[f"b_{role}"] = _tok(uid, tb.id, role)
        w.ids["user"] = await make_user(s, ta.id, "operator", f"a-victim-{mark}")

        plant = Plant(tenant_id=ta.id, name=f"Planta {mark}", timezone="America/Manaus")
        s.add(plant)
        await s.flush()
        line = Line(tenant_id=ta.id, plant_id=plant.id, name=f"Linha {mark}")
        s.add(line)
        await s.flush()
        mach = Machine(tenant_id=ta.id, line_id=line.id, name=f"Maquina {mark}", ideal_cycle_time_s=2.0)
        s.add(mach)
        await s.flush()
        tag = Tag(tenant_id=ta.id, machine_id=mach.id, name=f"Tag {mark}", protocol="mqtt",
                  address=f"plc/{mark}/temp")
        s.add(tag)
        await s.flush()
        rule = AlertRule(tenant_id=ta.id, tag_id=tag.id, name=f"Regra {mark}", condition="gt", threshold=80)
        cam = Camera(tenant_id=ta.id, name=f"Camera {mark}", rtsp_url="rtsp://127.0.0.1/none", machine_id=mach.id)
        pos = Position(tenant_id=ta.id, machine_id=mach.id, name=f"Posicao {mark}",
                       component_type=ComponentType.pneumatic_cylinder)
        s.add_all([rule, cam, pos])
        await s.flush()
        comp = Component(tenant_id=ta.id, component_type=ComponentType.pneumatic_cylinder, serial_number=f"SN-{mark}",
                         position_id=pos.id, last_position_id=pos.id, installed_at=now - timedelta(days=10))
        ev = AlertEvent(tenant_id=ta.id, rule_id=rule.id, triggered_at=now - timedelta(minutes=5), value_at_trigger=99)
        shift = Shift(tenant_id=ta.id, plant_id=plant.id, name=f"Turno {mark}", start_local="06:00",
                      end_local="14:00")
        exc = CalendarException(tenant_id=ta.id, plant_id=plant.id, date=date.today() + timedelta(days=30),
                                kind="holiday", note=mark)
        prod = Product(tenant_id=ta.id, code=f"P-{mark}", name=f"Produto {mark}")
        rc = ReasonCode(tenant_id=ta.id, code=f"RC{mark[-6:]}", category="breakdown", label=f"Motivo {mark}",
                        planned=False, oee_loss="availability")
        s.add_all([comp, ev, shift, exc, prod, rc])
        await s.flush()
        mp = MachineProduct(tenant_id=ta.id, machine_id=mach.id, product_id=prod.id, ideal_cycle_time_s=2.0)
        run = ProductionRun(tenant_id=ta.id, machine_id=mach.id, product_id=prod.id,
                            started_at=now - timedelta(hours=1))
        dt = DowntimeEvent(tenant_id=ta.id, machine_id=mach.id, start=now - timedelta(minutes=30),
                           end=now - timedelta(minutes=20), source="auto_run_state")
        fail = FailureRecord(tenant_id=ta.id, machine_id=mach.id, failed_at=now - timedelta(days=1), note=mark)
        plan = MaintenancePlan(tenant_id=ta.id, machine_id=mach.id, name=f"Plano {mark}", last_reset_at=now,
                               interval_days=30)
        wo = WorkOrder(tenant_id=ta.id, number=uuid.uuid4().int % 1_000_000 + 1_000_000, type="corrective", state="open",
                       machine_id=mach.id, title=f"OS {mark}")
        absn = MaintenanceAbsence(tenant_id=ta.id, user_id=w.users["technician"], date=date.today() + timedelta(days=3),
                                  hours=4, note=mark)
        note = Notification(tenant_id=ta.id, user_id=w.users["operator"], kind="test", title=f"Notif {mark}")
        conv = AiConversation(tenant_id=ta.id, user_id=w.users["manager"], title=f"Conversa {mark}")
        gw = Gateway(tenant_id=ta.id, plant_id=plant.id, name=f"Gateway {mark}")
        hook = Webhook(tenant_id=ta.id, url="https://example.com/hook", secret="s" * 24, events=["*"],
                       description=mark)
        lay = PlantLayout(tenant_id=ta.id, plant_id=plant.id, version=1)
        sched = ReportSchedule(tenant_id=ta.id, report_type="daily_production", scope_type="tenant",
                               name=f"Agenda {mark}")
        rrun = ReportRun(tenant_id=ta.id, report_type="daily_production", scope_type="tenant", period_key=mark,
                         format="pdf")
        ext = ReportExternalRecipient(tenant_id=ta.id, email=f"ext.{mark.lower()}@example.com", name=f"Ext {mark}",
                                      purpose="relatorio", legal_basis="contrato")
        s.add_all([mp, run, dt, fail, plan, wo, absn, note, conv, gw, hook, lay, sched, rrun, ext])
        await s.flush()
        mon = PredictiveMonitor(tenant_id=ta.id, position_id=pos.id, machine_id=mach.id, feature="stroke_time",
                                anchor_at=now - timedelta(days=5), warmup_windows=3,
                                window_cycles=500)
        s.add(mon)
        await s.flush()
        palert = PredictiveAlert(tenant_id=ta.id, monitor_id=mon.id, position_id=pos.id, machine_id=mach.id,
                                 feature="stroke_time", severity="warning", opened_at=now - timedelta(hours=2))
        cmd = GatewayCommand(tenant_id=ta.id, gateway_id=gw.id, type="opcua_browse", expires_at=now + timedelta(hours=1))
        s.add_all([palert, cmd])
        # gateway device token
        gtoken = gs.new_token()
        gw.token_hash, gw.token_prefix = gs.sha256(gtoken), gtoken[:12]
        gw.enrolled_at = now
        w.tokens["gateway"] = f"Gateway {gtoken}"
        # API tokens of the admin
        for scope in ("read", "write"):
            tok = "fct_" + uuid.uuid4().hex + uuid.uuid4().hex
            s.add(ApiToken(tenant_id=ta.id, name=f"api-{scope}-{mark}", prefix=tok[:12], token_hash=hash_api_token(tok),
                           scopes=["read"] if scope == "read" else ["read", "write"], created_by=w.users["admin"]))
            w.tokens[f"api_{scope}"] = f"Bearer {tok}"
        await s.flush()
        apitok = ApiToken(tenant_id=ta.id, name=f"victim-{mark}", prefix="fct_victim00", token_hash=uuid.uuid4().hex,
                          scopes=["read"], created_by=w.users["admin"])
        s.add(apitok)
        await s.flush()
        w.ids.update(plant=plant.id, line=line.id, machine=mach.id, tag=tag.id, rule=rule.id, camera=cam.id,
                     position=pos.id, component=comp.id, alert_event=ev.id, shift=shift.id, exc=exc.id,
                     product=prod.id, reason_code=rc.id, mp=mp.id, production_run=run.id, downtime=dt.id,
                     failure=fail.id, plan=plan.id, wo=wo.id, absence=absn.id, notification=note.id,
                     conversation=conv.id, gateway=gw.id, hook=hook.id, layout=lay.id, schedule=sched.id,
                     report_run=rrun.id, external=ext.id, monitor=mon.id, palert=palert.id, command=cmd.id,
                     api_token=apitok.id)
        await s.commit()
    w.markers = {v for v in w.ids.values()} | {w.a_id, mark} | {w.users[r] for r in HUMAN_ROLES}
    return w


# path parameter → world key (by route prefix when the name is ambiguous)
PARAM_KEYS = {
    "absence_id": "absence", "camera_id": "camera", "component_id": "component", "conversation_id": "conversation",
    "exc_id": "exc", "failure_id": "failure", "gateway_id": "gateway", "hook_id": "hook", "layout_id": "layout",
    "line_id": "line", "machine_id": "machine", "monitor_id": "monitor", "mp_id": "mp", "notification_id":
    "notification", "plan_id": "plan", "plant_id": "plant", "position_id": "position", "product_id": "product",
    "rule_id": "rule", "shift_id": "shift", "sid": "schedule", "tag_id": "tag", "token_id": "api_token",
    "wo_id": "wo", "alert_id": "palert", "code_id": "reason_code", "command_id": "command", "rid": "external",
    "user_id": "user",
}


def param_value(w: World, path: str, name: str) -> str:
    if name == "tenant_id":
        return w.victim_tenant_id if path.startswith("/api/admin") else w.a_id
    if name == "user_id" and path.startswith("/api/admin"):
        return w.victim_tenant_user_id
    if name == "run_id":
        return w.ids["report_run"] if path.startswith("/api/report-runs") else w.ids["production_run"]
    if name == "event_id":
        return w.ids["alert_event"] if path.startswith("/api/alerts") else w.ids["downtime"]
    if name == "name":
        return "tags.csv"
    if name == "token":
        return "nao-existe"
    key = PARAM_KEYS.get(name)
    if key and key in w.ids:
        return w.ids[key]
    return str(uuid.uuid4())


def fill_path(w: World, path: str) -> str:
    import re
    return re.sub(r"\{([a-z_]+)\}", lambda m: param_value(w, path, m.group(1)), path)


# ── request bodies from the OpenAPI schema (minimal valid-looking documents) ──────────────────────────────────

def _resolve(spec: dict, schema: dict) -> dict:
    while "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        schema = spec["components"]["schemas"][name]
    return schema


def example(spec: dict, schema: Optional[dict], w: World, key: str = "", depth: int = 0):
    if schema is None or depth > 6:
        return None
    schema = _resolve(spec, schema)
    for comb in ("anyOf", "oneOf", "allOf"):
        if comb in schema:
            opts = [o for o in schema[comb] if _resolve(spec, o).get("type") != "null"]
            return example(spec, opts[0] if opts else None, w, key, depth + 1)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "default" in schema and schema["default"] is not None:
        return schema["default"]
    t = schema.get("type")
    if t == "object" or "properties" in schema:
        props = schema.get("properties", {})
        return {k: example(spec, props[k], w, k, depth + 1) for k in schema.get("required", []) if k in props}
    if t == "array":
        n = schema.get("minItems", 0)
        return [example(spec, schema.get("items"), w, key, depth + 1) for _ in range(n)]
    if t == "integer":
        return max(1, schema.get("minimum", 1))
    if t == "number":
        return float(max(1, schema.get("minimum", 1)))
    if t == "boolean":
        return False
    if t == "string":
        fmt = schema.get("format")
        if fmt == "email":
            return f"fz.{uuid.uuid4().hex[:8]}@example.com"
        if fmt == "date-time":
            return datetime.now(timezone.utc).isoformat()
        if fmt == "date":
            return date.today().isoformat()
        if fmt in ("uri", "url"):
            return "https://example.com/x"
        if key.endswith("_id"):
            k = PARAM_KEYS.get(key)
            return w.ids.get(k, str(uuid.uuid4())) if k else str(uuid.uuid4())
        if "password" in key:
            return PASSWORD
        n = max(schema.get("minLength", 1), 8)
        return ("x" + uuid.uuid4().hex * 4)[:min(n, schema.get("maxLength", n) or n)]
    return None


def body_for(spec: dict, method: str, path: str, w: World):
    op = spec["paths"].get(path, {}).get(method.lower(), {})
    rb = op.get("requestBody")
    if not rb:
        return None, None
    content = rb.get("content", {})
    if "application/json" in content:
        return "json", example(spec, content["application/json"].get("schema"), w)
    if "multipart/form-data" in content:
        return "files", {"file": ("x.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")}
    return None, None
