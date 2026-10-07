"""Authorization policy of every HTTP route, as data (docs/security.md §2). Verified route by route, principal by
principal, by tests/test_security_authz_matrix.py — a route without an entry here fails that test.

Spec grammar (one string per "METHOD /path"):
  public                 anyone, no token (the demo viewer still only reads: its writes are refused by the guard)
  credential             public endpoint that checks a credential in the body (login, refresh, MFA, gateway
                         enrolment): 401 is its normal answer to a bad credential
  gateway                only the gateway device token (Authorization: Gateway gw_…)
  user                   any signed-in user of the tenant (every role); demo viewer read-only; API tokens
  manager | admin | superadmin    hierarchical minimum role (require_roles)
  any(r1|r2|…)           holders of any listed role, plus admin and superadmin (require_any)
  only(p1|p2|…)          exactly these principals (handler-specific rules)
Modifiers: +noapi (API tokens refused), +nodemo (demo viewer refused).

Derived principals:
  api_read / api_write   act as maintenance_planner of the tenant; read = GET only; never superadmin areas
  demo_viewer            account with roles manager + demo_viewer: GET only, never the blocked areas of
                         services/demo/guard.py (users, admin, gateways, integrations, settings, account …)
"""
from __future__ import annotations

import re

HUMAN_ORDER = ("operator", "technician", "production_supervisor", "maintenance_planner", "manager", "admin",
               "superadmin")
LEVEL = {"operator": 0, "technician": 0, "production_supervisor": 0, "maintenance_planner": 0, "manager": 1,
         "admin": 2, "superadmin": 3}
SAFE = {"GET", "HEAD", "OPTIONS"}

_SPEC = re.compile(r"^(?P<base>public|credential|gateway|user|manager|admin|superadmin|any\([a-z_|]+\)|only\([a-z_|]+\))"
                   r"(?P<mods>(\+[a-z]+)*)$")


def _parse(spec: str) -> tuple[str, set[str], set[str]]:
    m = _SPEC.match(spec)
    if not m:
        raise ValueError(f"invalid policy spec: {spec!r}")
    base = m.group("base")
    mods = {x for x in m.group("mods").split("+") if x}
    args: set[str] = set()
    if "(" in base:
        name, inner = base.split("(", 1)
        args = set(inner.rstrip(")").split("|"))
        base = name
    return base, args, mods


def _human_allowed(base: str, args: set[str], role: str) -> bool:
    if base == "user":
        return True
    if base in ("manager", "admin", "superadmin"):
        return LEVEL[role] >= LEVEL[base]
    if base == "any":
        return role in args or role in ("admin", "superadmin")
    return False


def accepts_401(spec: str) -> bool:
    return _parse(spec)[0] == "credential"


def allowed(spec: str, principal: str, method: str, path: str) -> bool:
    base, args, mods = _parse(spec)
    if base in ("public", "credential"):
        if principal == "demo_viewer":
            from ..demo.guard import block_reason
            return not block_reason(method, path)
        return True
    if base == "only":
        return principal in args
    if principal == "anon":
        return False
    if base == "gateway":
        return principal == "gateway"
    if principal == "gateway":
        return False
    if principal in ("api_read", "api_write"):
        if "noapi" in mods:
            return False
        if principal == "api_read" and method.upper() not in SAFE:
            return False
        return _human_allowed(base, args, "maintenance_planner")
    if principal == "demo_viewer":
        from ..demo.guard import block_reason
        if "nodemo" in mods or block_reason(method, path):
            return False
        return _human_allowed(base, args, "manager")
    if principal in LEVEL:
        return _human_allowed(base, args, principal)
    return False


# Routes addressed by a tenant-A id where tenant B legitimately gets 204 (idempotent no-op on its own data).
CROSS_TENANT_204_OK: set[str] = set()

POLICY: dict[str, str] = {
    # account
    "DELETE /api/account": "user+noapi",
    "GET /api/account/export": "user+noapi",
    "GET /api/account/me": "user+noapi",
    "PATCH /api/account/me": "user+noapi",
    "GET /api/account/mfa": "user+noapi",
    "POST /api/account/mfa/confirm": "user+noapi",
    "POST /api/account/mfa/disable": "user+noapi",
    "POST /api/account/mfa/enroll": "user+noapi",
    "POST /api/account/mfa/recovery-codes": "user+noapi",
    "POST /api/account/password": "user+noapi",
    "DELETE /api/account/sessions": "user+noapi",
    "GET /api/account/sessions": "user+noapi",
    "DELETE /api/account/sessions/{session_id}": "user+noapi",
    "GET /api/account/tenant-security": "admin",
    "PUT /api/account/tenant-security": "admin",
    # admin
    "GET /api/admin/tenants": "superadmin",
    "POST /api/admin/tenants": "superadmin",
    "DELETE /api/admin/tenants/{tenant_id}": "superadmin",
    "GET /api/admin/tenants/{tenant_id}": "superadmin",
    "PATCH /api/admin/tenants/{tenant_id}": "superadmin",
    "GET /api/admin/tenants/{tenant_id}/users": "superadmin",
    "POST /api/admin/tenants/{tenant_id}/users": "superadmin",
    "DELETE /api/admin/tenants/{tenant_id}/users/{user_id}": "superadmin",
    "PATCH /api/admin/tenants/{tenant_id}/users/{user_id}": "superadmin",
    # ai
    "GET /api/ai/audit": "admin",
    "POST /api/ai/chat": "user",
    "POST /api/ai/chat/stream": "user",
    "GET /api/ai/conversations": "user+noapi",
    "DELETE /api/ai/conversations/{conversation_id}": "user+noapi",
    "GET /api/ai/conversations/{conversation_id}": "user+noapi",
    "GET /api/ai/status": "user",
    # alerts
    "GET /api/alerts/events": "user",
    "POST /api/alerts/events/{event_id}/resolve": "user",
    "GET /api/alerts/rules": "user",
    "POST /api/alerts/rules": "manager",
    "DELETE /api/alerts/rules/{rule_id}": "admin",
    "PATCH /api/alerts/rules/{rule_id}/toggle": "manager",
    # api-tokens
    "GET /api/api-tokens/": "admin",
    "POST /api/api-tokens/": "admin",
    "DELETE /api/api-tokens/{token_id}": "admin",
    # auth
    "POST /api/auth/invitations/accept": "public",
    "GET /api/auth/invitations/{token}": "public",
    "POST /api/auth/login": "credential",
    "POST /api/auth/logout": "user+noapi",
    "POST /api/auth/logout-all": "user+noapi",
    "GET /api/auth/me": "user",
    "POST /api/auth/mfa/verify": "credential",
    "POST /api/auth/password-reset/confirm": "public",
    "POST /api/auth/password-reset/request": "public",
    "POST /api/auth/refresh": "credential",
    "POST /api/auth/register": "public",
    # calendar-exceptions
    "GET /api/calendar-exceptions/": "user",
    "POST /api/calendar-exceptions/": "admin",
    "DELETE /api/calendar-exceptions/{exc_id}": "admin",
    "GET /api/calendar-exceptions/{exc_id}": "user",
    "PATCH /api/calendar-exceptions/{exc_id}": "admin",
    # cameras
    "GET /api/cameras/": "user",
    "POST /api/cameras/": "admin",
    "DELETE /api/cameras/{camera_id}": "admin",
    "GET /api/cameras/{camera_id}": "user",
    "PATCH /api/cameras/{camera_id}": "manager",
    "GET /api/cameras/{camera_id}/hls/{file_path:path}": "user",
    # components
    "GET /api/components/": "user",
    "POST /api/components/": "admin",
    "GET /api/components/{component_id}": "user",
    "PATCH /api/components/{component_id}": "admin",
    "POST /api/components/{component_id}/remove": "admin",
    # demo
    "GET /api/demo/session": "user",
    # downtime
    "GET /api/downtime/": "user",
    "POST /api/downtime/": "user",
    "PATCH /api/downtime/{event_id}": "user",
    # failure-codes
    "GET /api/failure-codes/": "user",
    # failures
    "GET /api/failures/": "any(manager|maintenance_planner|technician|production_supervisor)",
    "POST /api/failures/": "any(manager|maintenance_planner|technician|production_supervisor)",
    "GET /api/failures/{failure_id}": "any(manager|maintenance_planner|technician|production_supervisor)",
    "PATCH /api/failures/{failure_id}": "any(manager|maintenance_planner|technician|production_supervisor)",
    # gateways
    "GET /api/gateways/": "manager",
    "POST /api/gateways/": "admin",
    "POST /api/gateways/enroll": "credential",
    "DELETE /api/gateways/{gateway_id}": "admin",
    "GET /api/gateways/{gateway_id}": "manager",
    "PATCH /api/gateways/{gateway_id}": "admin",
    "POST /api/gateways/{gateway_id}/browse": "admin",
    "GET /api/gateways/{gateway_id}/commands/{command_id}": "admin",
    "POST /api/gateways/{gateway_id}/commands/{command_id}/result": "gateway",
    "GET /api/gateways/{gateway_id}/config": "gateway",
    "GET /api/gateways/{gateway_id}/config-preview": "admin",
    "POST /api/gateways/{gateway_id}/enroll-token": "admin",
    "POST /api/gateways/{gateway_id}/heartbeat": "gateway",
    "GET /api/gateways/{gateway_id}/mqtt": "admin",
    "POST /api/gateways/{gateway_id}/revoke": "admin",
    "PUT /api/gateways/{gateway_id}/tags": "admin",
    # health
    "GET /api/health": "public",
    # layouts
    "GET /api/layouts/{layout_id}": "user",
    "PATCH /api/layouts/{layout_id}": "manager",
    "GET /api/layouts/{layout_id}/image": "user",
    "POST /api/layouts/{layout_id}/image": "manager",
    "PUT /api/layouts/{layout_id}/markers": "manager",
    "POST /api/layouts/{layout_id}/publish": "manager",
    # lines
    "GET /api/lines/": "user",
    "POST /api/lines/": "admin",
    "DELETE /api/lines/{line_id}": "admin",
    "GET /api/lines/{line_id}": "user",
    "PATCH /api/lines/{line_id}": "admin",
    "GET /api/lines/{line_id}/production": "user",
    # losses
    "GET /api/losses/pareto": "user",
    # machines
    "GET /api/machines/": "user",
    "POST /api/machines/": "admin",
    "DELETE /api/machines/{machine_id}": "admin",
    "GET /api/machines/{machine_id}": "user",
    "PATCH /api/machines/{machine_id}": "manager",
    "GET /api/machines/{machine_id}/products": "user",
    "POST /api/machines/{machine_id}/products": "admin",
    "DELETE /api/machines/{machine_id}/products/{mp_id}": "admin",
    "PATCH /api/machines/{machine_id}/products/{mp_id}": "admin",
    "GET /api/machines/{machine_id}/usage": "user",
    # maintenance-plans
    "GET /api/maintenance-plans/": "any(manager|maintenance_planner|technician|production_supervisor)",
    "POST /api/maintenance-plans/": "any(manager|maintenance_planner)",
    "POST /api/maintenance-plans/recompute": "any(manager|maintenance_planner)",
    "GET /api/maintenance-plans/status": "any(manager|maintenance_planner|technician|production_supervisor)",
    "DELETE /api/maintenance-plans/{plan_id}": "any(manager|maintenance_planner)",
    "GET /api/maintenance-plans/{plan_id}": "any(manager|maintenance_planner|technician|production_supervisor)",
    "PATCH /api/maintenance-plans/{plan_id}": "any(manager|maintenance_planner)",
    # maintenance
    "GET /api/maintenance/absences": "any(manager|maintenance_planner)",
    "POST /api/maintenance/absences": "any(manager|maintenance_planner)",
    "DELETE /api/maintenance/absences/{absence_id}": "any(manager|maintenance_planner)",
    "GET /api/maintenance/components": "any(manager|maintenance_planner|technician|production_supervisor)",
    "GET /api/maintenance/kpis": "any(manager|maintenance_planner|production_supervisor)",
    "GET /api/maintenance/reliability": "any(manager|maintenance_planner|production_supervisor)",
    "GET /api/maintenance/settings": "any(manager|maintenance_planner|technician|production_supervisor)",
    "PUT /api/maintenance/settings": "manager",
    # notifications
    "GET /api/notifications/": "user+noapi",
    "GET /api/notifications/preferences": "user+noapi",
    "PUT /api/notifications/preferences": "user+noapi",
    "POST /api/notifications/read-all": "user+noapi",
    "POST /api/notifications/{notification_id}/read": "user+noapi",
    # oee
    "GET /api/oee": "user",
    # onboarding
    "GET /api/onboarding": "admin",
    "PUT /api/onboarding": "admin",
    "GET /api/onboarding/": "admin",
    "PUT /api/onboarding/": "admin",
    "POST /api/onboarding/assets/import": "admin",
    "GET /api/onboarding/readiness": "admin",
    "POST /api/onboarding/tags/import": "admin",
    "GET /api/onboarding/templates/{name}": "admin",
    # overview
    "GET /api/overview": "user",
    # plants
    "GET /api/plants/": "user",
    "POST /api/plants/": "admin",
    "GET /api/plants/tree": "user",
    "DELETE /api/plants/{plant_id}": "admin",
    "GET /api/plants/{plant_id}": "user",
    "PATCH /api/plants/{plant_id}": "admin",
    "GET /api/plants/{plant_id}/layout/auto": "user",
    "GET /api/plants/{plant_id}/layout/published": "user",
    "GET /api/plants/{plant_id}/layouts": "user",
    "POST /api/plants/{plant_id}/layouts": "manager",
    # positions
    "GET /api/positions/": "user",
    "POST /api/positions/": "admin",
    "DELETE /api/positions/{position_id}": "admin",
    "GET /api/positions/{position_id}": "user",
    "PATCH /api/positions/{position_id}": "admin",
    "POST /api/positions/{position_id}/install": "admin",
    # predictive
    "GET /api/predictive/alerts": "any(manager|maintenance_planner|technician|production_supervisor)",
    "GET /api/predictive/alerts/{alert_id}": "any(manager|maintenance_planner|technician|production_supervisor)",
    "POST /api/predictive/alerts/{alert_id}/convert": "any(manager|maintenance_planner)",
    "POST /api/predictive/alerts/{alert_id}/transition": "any(manager|maintenance_planner|technician)",
    "GET /api/predictive/badges": "user",
    "POST /api/predictive/condition-plans": "any(manager|maintenance_planner)",
    "GET /api/predictive/evaluation": "any(manager|maintenance_planner)",
    "GET /api/predictive/features": "any(manager|maintenance_planner|technician|production_supervisor)",
    "GET /api/predictive/monitors": "any(manager|maintenance_planner|technician|production_supervisor)",
    "POST /api/predictive/monitors": "any(manager|maintenance_planner)",
    "GET /api/predictive/monitors/{monitor_id}": "any(manager|maintenance_planner|technician|production_supervisor)",
    "PATCH /api/predictive/monitors/{monitor_id}": "any(manager|maintenance_planner)",
    "POST /api/predictive/monitors/{monitor_id}/process": "any(manager|maintenance_planner)",
    "POST /api/predictive/monitors/{monitor_id}/rebaseline": "any(manager|maintenance_planner)",
    "GET /api/predictive/production-plan": "any(manager|maintenance_planner|technician|production_supervisor)",
    "PUT /api/predictive/production-plan": "any(manager|maintenance_planner)",
    "POST /api/predictive/recompute": "admin",
    "GET /api/predictive/recompute/jobs/{job_id}": "admin",
    "GET /api/predictive/rules": "any(manager|maintenance_planner|technician|production_supervisor)",
    "POST /api/predictive/rules": "any(manager|maintenance_planner)",
    "DELETE /api/predictive/rules/{rule_id}": "any(manager|maintenance_planner)",
    "PATCH /api/predictive/rules/{rule_id}": "any(manager|maintenance_planner)",
    # production-runs
    "GET /api/production-runs/": "user",
    "POST /api/production-runs/": "user",
    "DELETE /api/production-runs/{run_id}": "admin",
    "GET /api/production-runs/{run_id}": "user",
    "POST /api/production-runs/{run_id}/stop": "user",
    # products
    "GET /api/products/": "user",
    "POST /api/products/": "admin",
    "DELETE /api/products/{product_id}": "admin",
    "GET /api/products/{product_id}": "user",
    "PATCH /api/products/{product_id}": "admin",
    # public
    "POST /api/public/demo-session": "public",
    "GET /api/public/demo-status": "public",
    "POST /api/public/leads": "public",
    # push
    "POST /api/push/subscribe": "user+noapi",
    "POST /api/push/unsubscribe": "user+noapi",
    "GET /api/push/vapid-public-key": "user",
    # reason-codes
    "GET /api/reason-codes/": "user",
    "POST /api/reason-codes/": "admin",
    "PATCH /api/reason-codes/{code_id}": "admin",
    # reliability
    "GET /api/reliability": "user",
    # report-runs
    "GET /api/report-runs/": "user",
    "GET /api/report-runs/{run_id}": "user",
    "GET /api/report-runs/{run_id}/download": "user",
    # report-schedules
    "GET /api/report-schedules/": "manager",
    "POST /api/report-schedules/": "manager",
    "DELETE /api/report-schedules/{sid}": "manager",
    "GET /api/report-schedules/{sid}": "manager",
    "PATCH /api/report-schedules/{sid}": "manager",
    "GET /api/report-schedules/{sid}/history": "manager",
    "POST /api/report-schedules/{sid}/pause": "manager",
    "POST /api/report-schedules/{sid}/resume": "manager",
    "POST /api/report-schedules/{sid}/send-now": "manager",
    # reports
    "GET /api/reports/catalog": "user",
    "GET /api/reports/external-recipients": "admin",
    "POST /api/reports/external-recipients": "admin",
    "DELETE /api/reports/external-recipients/{rid}": "admin",
    "POST /api/reports/generate": "user",
    "GET /api/reports/line-targets": "user",
    "PUT /api/reports/line-targets": "manager",
    "GET /api/reports/reliability-life": "any(manager|maintenance_planner|production_supervisor)",
    "GET /api/reports/settings": "admin",
    "PUT /api/reports/settings": "admin",
    "GET /api/reports/shift": "user",
    "GET /api/reports/status": "user",
    "GET /api/reports/unsubscribe": "public",  # signed link in the e-mail
    "POST /api/reports/unsubscribe": "public",  # signed link in the e-mail
    # sensors
    "GET /api/sensors/history": "user",
    "GET /api/sensors/latest": "user",
    # shifts
    "GET /api/shifts/": "user",
    "POST /api/shifts/": "admin",
    "DELETE /api/shifts/{shift_id}": "admin",
    "GET /api/shifts/{shift_id}": "user",
    "PATCH /api/shifts/{shift_id}": "admin",
    # tags
    "GET /api/tags/": "user",
    "POST /api/tags/": "admin",
    "DELETE /api/tags/{tag_id}": "admin",
    "GET /api/tags/{tag_id}": "user",
    "PATCH /api/tags/{tag_id}": "admin",
    # usage
    "GET /api/usage/history": "user",
    "POST /api/usage/recompute": "admin",
    "GET /api/usage/recompute/jobs/{job_id}": "admin",
    # users
    "GET /api/users/": "any(manager|maintenance_planner|production_supervisor)",
    "POST /api/users/": "admin",
    "GET /api/users/invitations": "admin",
    "POST /api/users/invitations": "admin",
    "DELETE /api/users/invitations/{invitation_id}": "admin",
    "DELETE /api/users/{user_id}": "admin",
    "GET /api/users/{user_id}": "any(manager|maintenance_planner|production_supervisor)",
    "PATCH /api/users/{user_id}": "any(manager|maintenance_planner)",
    "POST /api/users/{user_id}/anonymize": "admin",
    "POST /api/users/{user_id}/mfa/reset": "admin",
    "POST /api/users/{user_id}/sessions/revoke": "admin",
    # webhooks
    "GET /api/webhooks/": "admin",
    "POST /api/webhooks/": "admin",
    "GET /api/webhooks/events": "admin",
    "DELETE /api/webhooks/{hook_id}": "admin",
    "GET /api/webhooks/{hook_id}": "admin",
    "PATCH /api/webhooks/{hook_id}": "admin",
    "GET /api/webhooks/{hook_id}/deliveries": "admin",
    # work-orders
    "GET /api/work-orders/": "user",
    "POST /api/work-orders/": "user",
    "POST /api/work-orders/emergency-call": "user",
    "GET /api/work-orders/{wo_id}": "user",
    "PATCH /api/work-orders/{wo_id}": "any(manager|maintenance_planner)",
    "POST /api/work-orders/{wo_id}/approve-draft": "user",
    "POST /api/work-orders/{wo_id}/labor": "any(manager|technician)",
    "POST /api/work-orders/{wo_id}/replacements": "user",
    "DELETE /api/work-orders/{wo_id}/replacements/{rep_id}": "any(manager|technician)",
    "PATCH /api/work-orders/{wo_id}/tasks": "any(manager|technician)",
    "POST /api/work-orders/{wo_id}/transition": "user",
    # /metrics
    "GET /metrics": "public",  # Prometheus; refused by nginx outside the internal network (infra/nginx/nginx.prod.conf)
}
