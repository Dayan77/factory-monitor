"""Tool layer of the AI assistant (docs/ai_assistant.md §1, IA-1).

Every tool is an internal ASGI request (no network) to the API route listed in the catalog, with the user's own
`Authorization: Bearer <jwt>` — so the route's RBAC (`require_any`), tenant filter and row visibility (technician:
own WOs) apply exactly as on screen. Only GET is possible: the transport refuses any other method (IA-11).

The layer also: resolves period words with the plant calendar (IA-6), trims results (≤ 50 rows, `truncated` +
`total`), removes e-mail / phone / token (IA-10), adds server-computed helper fields (durations in minutes, KPI
reference targets documented in kpis.md — "contas são da ferramenta", IA-2) and builds the citation (label + URL of
the screen with the same filters).
"""
from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models import User
from ...auth import has_any
from ..oee.engine import load_calendar
from ..usage import queries as uq
from . import periods
from .catalog import BY_NAME, TOOLS
from .privacy import redact

STATUS_PT = {"running": "rodando", "stopped": "parada", "error": "em falha", "maintenance": "em manutenção",
             "unknown": "sem dados", "idle": "parada"}
DROP_KEYS_SUFFIX = ("_ids",)
DROP_KEYS = {"work_order_numbers", "backlog_remaining_by_wo", "task_list", "history", "labor", "replacements",
             "allowed_transitions", "usage_at_failure"}


class ReadOnlyTransport(httpx.ASGITransport):
    """ASGI transport that only lets GET through and records every request (audit + tests)."""

    def __init__(self, app, log: list):
        super().__init__(app=app)
        self.log = log

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.log.append((request.method, request.url.path))
        if request.method != "GET":
            return httpx.Response(405, json={"detail": "assistente é só leitura"})
        return await super().handle_async_request(request)


@dataclass
class ToolResult:
    name: str
    args: dict
    status: str = "ok"                    # ok | forbidden | not_found | invalid | error | unavailable | limit
    http_status: Optional[int] = None
    data: Any = None
    rows: Optional[int] = None
    total: Optional[int] = None
    truncated: bool = False
    citation: Optional[dict] = None       # {label, url}
    citation_id: Optional[int] = None
    duration_ms: int = 0
    message: str = ""
    ambiguous: bool = False

    def for_model(self) -> dict:
        """What the model sees (IA-9: data, never instructions)."""
        out = {"status": self.status, "citation": self.citation_id, "data": self.data}
        if self.truncated:
            out.update(truncated=True, total=self.total)
        if self.message:
            out["message"] = self.message
        return out

    def summary(self) -> str:
        if self.status != "ok":
            return self.message or self.status
        if self.rows is not None:
            return f"{self.rows} linha(s)" + (f" de {self.total}" if self.truncated else "")
        return "ok"


def tool_allowed(user: User, tool: dict) -> bool:
    return tool["roles"] == ["*"] or has_any(user, tool["roles"])


def tools_for(user: User) -> list[dict]:
    """IA-4: the model only receives the tools the user's roles may use (admin/superadmin pass as in the API)."""
    return [t for t in TOOLS if tool_allowed(user, t)]


def json_schema(tool: dict) -> dict:
    props = {}
    for k, v in tool["params"].items():
        p = dict(v)
        if k in ("period",):
            p["enum"] = list(periods.PERIODS)
            p["description"] = "Período (resolvido pelo servidor no calendário da planta)."
        if k in ("start", "stop", "completed_from", "completed_to"):
            p["description"] = "ISO 8601 UTC"
        props[k] = p
    return {"type": "object", "properties": props, "required": list(tool["required"]), "additionalProperties": False}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace("-", " ").replace("_", " ").split())


def _trim(value: Any, max_rows: int, depth: int = 0) -> Any:
    if isinstance(value, dict):
        return {k: _trim(v, max_rows, depth + 1) for k, v in value.items()
                if k not in DROP_KEYS and not k.endswith(DROP_KEYS_SUFFIX)}
    if isinstance(value, list):
        return [_trim(v, max_rows, depth + 1) for v in value[:max_rows]]
    return value


def _validate_args(tool: dict, args: Any) -> Optional[str]:
    if not isinstance(args, dict):
        return "argumentos devem ser um objeto"
    extra = set(args) - set(tool["params"])
    if extra:
        return f"parâmetros desconhecidos: {sorted(extra)}"
    if "tenant_id" in args:
        return "tenant_id não é parâmetro"
    for r in tool["required"]:
        if args.get(r) in (None, ""):
            return f"parâmetro obrigatório ausente: {r}"
    for k, v in args.items():
        spec = tool["params"][k]
        t = spec.get("type")
        if v is None:
            continue
        if t == "string" and not isinstance(v, str):
            return f"{k} deve ser texto"
        if t == "boolean" and not isinstance(v, bool):
            return f"{k} deve ser booleano"
        if t == "integer" and (not isinstance(v, int) or isinstance(v, bool)):
            return f"{k} deve ser inteiro"
        if t == "number" and not isinstance(v, (int, float)):
            return f"{k} deve ser número"
        if t == "array" and not isinstance(v, list):
            return f"{k} deve ser lista"
        enum = spec.get("enum") or spec.get("items", {}).get("enum")
        if enum:
            vals = v if isinstance(v, list) else [v]
            bad = [x for x in vals if x not in enum]
            if bad:
                return f"{k}: valor inválido {bad[0]!r} (use {', '.join(enum)})"
        if "maximum" in spec and isinstance(v, (int, float)) and v > spec["maximum"]:
            return f"{k} ≤ {spec['maximum']}"
    return None


def _iso(d: Optional[datetime]) -> Optional[str]:
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if d else None


def _dt(s: Any) -> Optional[datetime]:
    if not isinstance(s, str) or not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


class ToolExecutor:
    """Runs catalog tools for one user and one chat turn."""

    def __init__(self, user: User, bearer: str, db: AsyncSession, context: Optional[dict] = None,
                 app=None, now: Optional[datetime] = None):
        self.user, self.bearer, self.db = user, bearer, db
        self.context = {k: v for k, v in (context or {}).items() if v not in (None, "")}
        self.now = now or datetime.now(timezone.utc)
        self.requests: list[tuple[str, str]] = []
        self._app = app
        self._tree: Optional[list] = None
        self._cals: dict[str, Any] = {}
        self.results: list[ToolResult] = []
        self._window: Optional[tuple] = None
        self.today_date: Optional[date] = None

    # ── internal API ──────────────────────────────────────────────────────
    @property
    def app(self):
        if self._app is None:
            from ...main import app
            self._app = app
        return self._app

    async def get(self, path: str, params: Optional[dict] = None) -> tuple[int, Any]:
        clean = []
        for k, v in (params or {}).items():
            if v is None:
                continue
            for x in (v if isinstance(v, list) else [v]):
                clean.append((k, str(x).lower() if isinstance(x, bool) else str(x)))
        async with httpx.AsyncClient(transport=ReadOnlyTransport(self.app, self.requests),
                                     base_url="http://assistant.internal", timeout=settings.ai_turn_timeout_s) as c:
            r = await c.get(path, params=clean, headers={"Authorization": f"Bearer {self.bearer}"})
        try:
            body = r.json()
        except ValueError:
            body = None
        return r.status_code, body

    async def tree(self) -> list:
        if self._tree is None:
            st, body = await self.get("/api/plants/tree")
            self._tree = body if st == 200 and isinstance(body, list) else []
        return self._tree

    async def plant_of(self, args: dict) -> Optional[dict]:
        tree = await self.tree()
        want_plant = args.get("plant_id") or (args.get("id") if args.get("scope") == "plant" else None)
        want_line = args.get("line_id") or (args.get("id") if args.get("scope") == "line" else None)
        want_machine = args.get("machine_id") or (args.get("id") if args.get("scope") == "machine" else None)
        for p in tree:
            if want_plant and p["id"] == want_plant:
                return p
            for ln in p.get("lines", []):
                if want_line and ln["id"] == want_line:
                    return p
                if want_machine and any(m["id"] == want_machine for m in ln.get("machines", [])):
                    return p
        ctx_plant = self.context.get("plant_id")
        for p in tree:
            if ctx_plant and p["id"] == ctx_plant:
                return p
        return tree[0] if tree else None

    async def calendar(self, plant: Optional[dict]):
        if plant is None:
            return None
        if plant["id"] not in self._cals:
            tz = uq.tzinfo(plant.get("timezone"))
            self._cals[plant["id"]] = await load_calendar(self.db, self.user.tenant_id, plant["id"], tz)
        return self._cals[plant["id"]]

    async def tz(self, args: Optional[dict] = None) -> ZoneInfo:
        p = await self.plant_of(args or {})
        return uq.tzinfo(p.get("timezone") if p else None)

    async def window(self, args: dict) -> tuple[Optional[datetime], Optional[datetime], Optional[dict]]:
        """(start, stop, plant) from period | start/stop (IA-6)."""
        plant = await self.plant_of(args)
        if args.get("period"):
            cal = await self.calendar(plant)
            if cal is None:
                raise periods.PeriodError("nenhuma planta cadastrada para resolver o período")
            s, e = periods.resolve(args["period"], cal, self.now)
        else:
            s, e = _dt(args.get("start")), _dt(args.get("stop"))
        self._window = (s, e)
        return s, e, plant

    async def today(self, args: Optional[dict] = None) -> date:
        plant = await self.plant_of(args or {})
        return periods.today_of(await self.calendar(plant), self.now)

    def names(self) -> dict[str, str]:
        out = {}
        for p in self._tree or []:
            out[p["id"]] = p["name"]
            for ln in p.get("lines", []):
                out[ln["id"]] = ln["name"]
                for m in ln.get("machines", []):
                    out[m["id"]] = m["name"]
        return out

    # ── dispatcher ────────────────────────────────────────────────────────
    async def run(self, name: str, args: Any) -> ToolResult:
        t0 = time.monotonic()
        tool = BY_NAME.get(name)
        res = ToolResult(name=name, args=args if isinstance(args, dict) else {})
        self._window = None
        if tool is None:
            res.status, res.message = "invalid", f"ferramenta inexistente: {name}"
        else:
            err = _validate_args(tool, args)
            if err:
                res.status, res.message = "invalid", err
            else:
                try:
                    await getattr(self, f"t_{name}")(res, dict(args))
                except periods.PeriodError as e:
                    res.status, res.message = "invalid", str(e)
                except httpx.HTTPError as e:            # pragma: no cover — in-process ASGI
                    res.status, res.message = "error", f"falha interna: {type(e).__name__}"
            if res.status == "ok" and not tool_allowed(self.user, tool):
                # defence in depth: a forged call of a tool not offered to the role (the route answered anyway)
                res.status, res.message, res.data = "forbidden", "seu perfil não tem acesso a esta informação", None
        if res.status == "ok" and isinstance(res.data, dict) and self._window and any(self._window):
            # the period the server resolved (IA-6) is part of the result: the answer may quote it
            res.data.setdefault("periodo_consultado", {"start": _iso(self._window[0]), "stop": _iso(self._window[1])})
        res.data = redact(_trim(res.data, settings.ai_max_rows)) if res.data is not None else None
        res.duration_ms = int((time.monotonic() - t0) * 1000)
        self.results.append(res)
        return res

    def _http(self, res: ToolResult, st: int, body: Any, what: str) -> bool:
        res.http_status = st
        if st == 200:
            return True
        if st in (401, 403):
            res.status, res.message = "forbidden", f"seu perfil não tem acesso a {what}"
        elif st == 404:
            res.status, res.message = "not_found", f"não encontrei {what}"
        elif st == 422:
            detail = body.get("detail") if isinstance(body, dict) else None
            res.status, res.message = "invalid", f"parâmetros inválidos para {what}: {str(detail)[:200]}"
        elif st == 405:
            res.status, res.message = "unavailable", f"{what}: rota indisponível"
        else:
            res.status, res.message = "error", f"erro {st} ao consultar {what}"
        return False

    def _rows(self, res: ToolResult, rows: list, limit: Optional[int] = None) -> list:
        lim = min(limit or settings.ai_max_rows, settings.ai_max_rows)
        res.total = len(rows)
        res.truncated = len(rows) > lim
        out = rows[:lim]
        res.rows = len(out)
        return out

    def _cite(self, res: ToolResult, label: str, path: str, params: Optional[dict] = None) -> None:
        q = urlencode({k: v for k, v in (params or {}).items() if v not in (None, "", [])}, doseq=True)
        res.citation = {"label": label, "url": f"{path}?{q}" if q else path}

    # ── tools ─────────────────────────────────────────────────────────────
    async def t_resolve_asset(self, res: ToolResult, a: dict) -> None:
        q, kind = _norm(a["query"]), a.get("kind") or "any"
        tree = await self.tree()
        st, positions = await self.get("/api/positions/")
        positions = positions if st == 200 and isinstance(positions, list) else []
        cands = []
        mline = {}
        for p in tree:
            cands.append({"kind": "plant", "id": p["id"], "name": p["name"], "plant_id": p["id"], "path": p["name"]})
            for ln in p.get("lines", []):
                cands.append({"kind": "line", "id": ln["id"], "name": ln["name"], "plant_id": p["id"],
                              "path": f"{p['name']} › {ln['name']}"})
                for m in ln.get("machines", []):
                    mline[m["id"]] = (p, ln, m)
                    cands.append({"kind": "machine", "id": m["id"], "name": m["name"], "plant_id": p["id"],
                                  "line_id": ln["id"], "path": f"{ln['name']} › {m['name']}"})
        for pos in positions:
            p, ln, m = mline.get(pos.get("machine_id"), (None, None, None))
            cands.append({"kind": "position", "id": pos["id"], "name": pos["name"],
                          "plant_id": p["id"] if p else None, "line_id": ln["id"] if ln else None,
                          "machine_id": pos.get("machine_id"),
                          "path": f"{m['name']} › {pos['name']}" if m else pos["name"]})
        if kind != "any":
            cands = [c for c in cands if c["kind"] == kind]
        qtok = set(q.split())

        def score(c):
            n = _norm(c["name"])
            if n == q:
                return 3
            ntok = set(n.split())
            if qtok and qtok <= ntok:
                return 2
            if q and q in n:
                return 1
            return 0
        scored = [(score(c), c) for c in cands]
        best = max((s for s, _ in scored), default=0)
        matches = [c for s, c in scored if s == best and s > 0]
        res.ambiguous = len(matches) > 1
        res.data = {"query": a["query"], "matches": self._rows(res, matches, 20), "ambiguous": res.ambiguous}
        if not matches:
            res.message = f"nenhum ativo encontrado para '{a['query']}'"

    async def t_get_machine_status(self, res: ToolResult, a: dict) -> None:
        tree = await self.tree()
        plant = await self.plant_of(a)
        if plant is None:
            res.status, res.message = "not_found", "não encontrei a planta"
            return
        st, ov = await self.get("/api/overview", {"plant_id": plant["id"]})
        if not self._http(res, st, ov, "o estado das máquinas"):
            return
        st2, open_dt = await self.get("/api/downtime/", {"open": True, "limit": 500})
        open_by_m = {}
        if st2 == 200 and isinstance(open_dt, list):
            for e in open_dt:
                open_by_m.setdefault(e["machine_id"], e)
        err = {m["machine_id"]: m for m in ov.get("machines_in_error", [])}
        rows = []
        for ln in plant.get("lines", []):
            if a.get("line_id") and ln["id"] != a["line_id"]:
                continue
            for m in ln.get("machines", []):
                if a.get("machine_id") and m["id"] != a["machine_id"]:
                    continue
                ev = open_by_m.get(m["id"])
                since = (err.get(m["id"], {}).get("since") or (ev or {}).get("start")) if m["status"] != "running" else None
                sd = _dt(since)
                rows.append({"machine_id": m["id"], "machine_name": m["name"], "line_name": ln["name"],
                             "status": m["status"], "status_pt": STATUS_PT.get(m["status"], m["status"]),
                             "since": since, "stopped_for_min": round((self.now - sd).total_seconds() / 60)
                             if sd else None, "last_seen_at": m.get("last_seen_at"),
                             "fault_code": err.get(m["id"], {}).get("fault_code"),
                             "open_downtime_reason": (ev or {}).get("effective_reason_code")})
        if a.get("machine_id") and not rows and not any(
                m["id"] == a["machine_id"] for p in tree for ln in p.get("lines", []) for m in ln.get("machines", [])):
            res.status, res.message = "not_found", "não encontrei a máquina"
            return
        rows.sort(key=lambda r: (r["status"] == "running", r["line_name"], r["machine_name"]))
        res.data = {"plant_name": plant["name"], "generated_at": _iso(self.now), "machines": self._rows(res, rows),
                    "gateways": ov.get("gateways", [])}
        if a.get("machine_id"):
            self._cite(res, f"Estado · {rows[0]['machine_name']}", f"/factory/machines/{a['machine_id']}")
        else:
            self._cite(res, f"Estado das máquinas · {plant['name']}", "/factory", {"plant_id": plant["id"]})

    async def _oee_like(self, res: ToolResult, a: dict, path: str, extra: dict, label: str, tab: Optional[str]):
        s, e, plant = await self.window(a)
        params = {"scope": a["scope"], "id": a["id"], "start": _iso(s), "stop": _iso(e), **extra}
        st, body = await self.get(path, params)
        names = self.names()
        what = {"machine": "a máquina", "line": "a linha", "plant": "a planta"}[a["scope"]]
        if not self._http(res, st, body, what if st == 404 else label):
            return None
        tz = uq.tzinfo(plant.get("timezone") if plant else None)
        ps = s or _dt(body.get("period_start") if isinstance(body, dict) else None)
        pe = e or _dt(body.get("period_end") if isinstance(body, dict) else None)
        when = (f"{ps.astimezone(tz):%d/%m/%Y %H:%M}–{pe.astimezone(tz):%d/%m/%Y %H:%M}" if ps and pe else "")
        cite = {"scope": a["scope"], "id": a["id"], "start": _iso(ps), "stop": _iso(pe)}
        if tab:
            cite["tab"] = tab
        else:
            cite["group_by"] = extra.get("group_by") or "none"
        self._cite(res, f"{label} · {names.get(a['id'], a['scope'])} · {when}", "/oee", cite)
        return body

    async def t_get_oee(self, res: ToolResult, a: dict) -> None:
        extra = {"group_by": a.get("group_by") or "none"}
        if a.get("compare_to") and a["compare_to"] != "none":
            extra["compare_to"] = a["compare_to"]
        body = await self._oee_like(res, a, "/api/oee", extra, "OEE", None)
        if body is None:
            return
        keep = ("scope", "id", "period_start", "period_end", "granularity", "availability", "performance", "quality",
                "oee", "teep", "reason", "data_coverage", "product_unknown", "counts_prorated", "calendar_default",
                "pot_s", "pdot_s", "pbt_s", "adot_s", "apt_s", "nodata_s", "micro_stop_count", "micro_stop_loss_s",
                "speed_loss_s", "quality_loss_s", "good", "scrap", "produced", "ideal_cycle_time_suspect")
        out = {k: body.get(k) for k in keep if k in body}
        out["scope_name"] = self.names().get(a["id"])
        pts = [{k: p.get(k) for k in ("label", "t", "end", "availability", "performance", "quality", "oee", "pbt_s",
                                      "good", "scrap", "instances") if p.get(k) is not None or k in ("oee",)}
               for p in body.get("points", [])]
        out["points"] = self._rows(res, pts) if pts else []
        if not pts:
            res.rows = None
        if body.get("previous"):
            out["previous"] = {k: body["previous"].get(k) for k in keep if k in body["previous"]}
            out["delta"] = body.get("delta")
        res.data = out

    async def t_get_losses_pareto(self, res: ToolResult, a: dict) -> None:
        body = await self._oee_like(res, a, "/api/losses/pareto", {"by": a.get("by") or "code"}, "Pareto de perdas",
                                    "pareto")
        if body is None:
            return
        rows = [{**r, "duration_min": round(r["duration_s"] / 60, 1)} for r in body if r.get("duration_s")]
        res.data = {"rows": self._rows(res, rows)}

    async def t_get_downtime(self, res: ToolResult, a: dict) -> None:
        if not a.get("line_id") and not a.get("machine_id") and self.context.get("line_id"):
            a["line_id"] = self.context["line_id"]
        s, e, plant = await self.window(a)
        params = {"machine_id": a.get("machine_id"), "line_id": a.get("line_id"), "start": _iso(s), "stop": _iso(e),
                  "open": a.get("open"), "unjustified": a.get("unjustified"),
                  "limit": min(a.get("limit") or settings.ai_max_rows, settings.ai_max_rows) + 1}
        st, body = await self.get("/api/downtime/", params)
        if not self._http(res, st, body, "as paradas"):
            return
        rows = []
        for ev in body:
            dur = ev.get("duration_s")
            if dur is None and _dt(ev.get("start")):
                dur = (self.now - _dt(ev["start"])).total_seconds()
            rows.append({"id": ev["id"], "machine_name": ev.get("machine_name"), "start": ev["start"],
                         "end": ev.get("end"), "open": ev.get("end") is None, "duration_min": round(dur / 60, 1)
                         if dur is not None else None, "reason_code": ev.get("effective_reason_code"),
                         "unjustified": ev.get("unjustified"), "fault_code": ev.get("fault_code"),
                         "planned": ev.get("planned"), "comment_do_operador": ev.get("comment")})
        res.data = {"rows": self._rows(res, rows, a.get("limit"))}
        self._cite(res, f"Paradas · {self.names().get(a.get('line_id') or a.get('machine_id'), 'todas')}", "/stops",
                   {"line_id": a.get("line_id"), "machine_id": a.get("machine_id"), "start": _iso(s), "stop": _iso(e),
                    "unjustified": "true" if a.get("unjustified") else None})

    async def t_get_shift_report(self, res: ToolResult, a: dict) -> None:
        day = a["productive_day"]
        if day == "today":
            day = (await self.today(a)).isoformat()
        st, body = await self.get("/api/reports/shift", {"line_id": a["line_id"], "productive_day": day,
                                                         "shift_id": a.get("shift_id")})
        if not self._http(res, st, body, "a linha" if st == 404 else "o relatório de turno"):
            return
        body = dict(body)
        body["downtime"] = [{k: d.get(k) for k in ("machine_name", "start", "end", "duration_s",
                                                   "effective_reason_code", "unjustified")}
                            for d in body.get("downtime", [])]
        res.data = body
        self._cite(res, f"Relatório de turno · {self.names().get(a['line_id'], 'linha')} · {day}", "/reports/shift",
                   {"line_id": a["line_id"], "day": day, "shift_id": a.get("shift_id")})

    async def t_get_component_life(self, res: ToolResult, a: dict) -> None:
        st, body = await self.get("/api/maintenance/components", {"plant_id": a.get("plant_id"),
                                                                  "line_id": a.get("line_id"),
                                                                  "min_life": a.get("min_life")})
        if not self._http(res, st, body, "a vida dos componentes"):
            return
        rows = sorted(body, key=lambda r: (r.get("alert") != "nominal_reached", -(r.get("life_consumed_pct") or 0)))
        res.data = {"rows": self._rows(res, rows),
                    "reference": {"attention_at_life": 0.8, "nominal_reached_at_life": 1.0}}
        self._cite(res, "Componentes · % de vida", "/maintenance/components")

    async def t_get_maintenance_due(self, res: ToolResult, a: dict) -> None:
        st, body = await self.get("/api/maintenance-plans/status", {k: a.get(k) for k in
                                                                    ("plant_id", "line_id", "machine_id", "state")})
        if not self._http(res, st, body, "os planos de manutenção"):
            return
        res.data = {"rows": self._rows(res, list(body))}
        self._cite(res, "Próximas manutenções", "/maintenance/upcoming")

    async def t_get_work_orders(self, res: ToolResult, a: dict) -> None:
        params = dict(a)
        for k in ("from", "to"):
            if params.get(k) in ("today", "<today>", "hoje"):
                params[k] = (await self.today(a)).isoformat()
        params["limit"] = min(a.get("limit") or 500, 500)
        st, body = await self.get("/api/work-orders/", params)
        if not self._http(res, st, body, "as ordens de serviço"):
            return
        keep = ("id", "number", "title", "type", "urgency", "state", "position_name", "machine_name", "line_name",
                "assignee_name", "scheduled_for", "estimated_hours", "labor_hours", "remaining_hours", "overdue",
                "requested_at", "completed_at", "description")
        rows = [{k: w.get(k) for k in keep if k in w} for w in body]
        for r in rows:
            if "description" in r:
                r["descricao_do_usuario"] = r.pop("description")
            r["link"] = f"/maintenance/work-orders?wo={r['id']}"
        rows = self._rows(res, rows, a.get("limit"))
        res.data = {"rows": rows}
        if len(rows) == 1:
            self._cite(res, f"OS {rows[0].get('number')}", "/maintenance/work-orders", {"wo": rows[0]["id"]})
        else:
            self._cite(res, "Ordens de serviço", "/maintenance/work-orders",
                       {"state": a.get("state"), "overdue": "true" if a.get("overdue") else None,
                        "assignee_id": a.get("assignee_id"), "type": a.get("type")})

    async def t_get_maintenance_kpis(self, res: ToolResult, a: dict) -> None:
        s, e, _ = await self.window(a)
        st, body = await self.get("/api/maintenance/kpis", {"plant_id": a.get("plant_id"), "start": _iso(s),
                                                            "stop": _iso(e)})
        if not self._http(res, st, body, "os indicadores de manutenção"):
            return
        # documented reference values (kpis.md §3.1–3.3) — the model compares against these, not memory
        res.data = {**body, "reference": {"pmp_target": 0.85, "pmp_world_class": 0.90, "schedule_compliance_target": 0.90,
                                          "backlog_weeks_healthy_min": 2, "backlog_weeks_healthy_max": 4}}
        self._cite(res, "Indicadores de manutenção", "/maintenance/kpis",
                   {"start": body.get("period_start"), "stop": body.get("period_end")})

    async def t_get_reliability(self, res: ToolResult, a: dict) -> None:
        s, e, _ = await self.window(a)
        gb = a.get("group_by") or "position"
        st, body = await self.get("/api/maintenance/reliability", {"group_by": gb, "start": _iso(s), "stop": _iso(e),
                                                                   "plant_id": a.get("plant_id"),
                                                                   "line_id": a.get("line_id")})
        if not self._http(res, st, body, "os indicadores de confiabilidade"):
            return
        if isinstance(body, dict) and isinstance(body.get("rows"), list):
            body = {**body, "rows": self._rows(res, body["rows"])}
        res.data = {**(body if isinstance(body, dict) else {"rows": body}),
                    "reference": {"small_sample_below_failures": 3}}
        self._cite(res, f"Confiabilidade por {gb}", "/maintenance/reliability", {"group_by": gb})

    async def t_search_failures(self, res: ToolResult, a: dict) -> None:
        s, e, _ = await self.window(a)
        local = a.get("failure_mode") or a.get("q")
        params = {k: a.get(k) for k in ("machine_id", "line_id", "plant_id", "position_id", "component_id", "severity",
                                        "open")}
        params.update(start=_iso(s), stop=_iso(e), limit=500 if local else min(a.get("limit") or 50, 50))
        st, body = await self.get("/api/failures/", params)
        if not self._http(res, st, body, "as falhas"):
            return
        rows = list(body)
        if a.get("failure_mode"):
            rows = [f for f in rows if (f.get("failure_mode") or "").upper() == a["failure_mode"].upper()]
        if a.get("q"):
            q = _norm(a["q"])
            rows = [f for f in rows if q in _norm(f.get("note") or "")]
        keep = ("id", "machine_name", "position_name", "failed_at", "restored_at", "restore_duration_s", "severity",
                "failure_mode", "mechanism", "cause", "detection_method", "status", "work_order_id", "open", "note")
        out = []
        for f in rows:
            r = {k: f.get(k) for k in keep}
            if r.get("restore_duration_s") is not None:
                r["restore_duration_min"] = round(r["restore_duration_s"] / 60, 1)
            r["nota_do_tecnico"] = r.pop("note")
            out.append(r)
        out = self._rows(res, out, a.get("limit"))
        res.data = {"rows": out}
        if len(out) == 1:
            self._cite(res, f"Falha · {out[0].get('position_name') or out[0].get('machine_name')}",
                       "/maintenance/failures", {"f": out[0]["id"]})
        else:
            self._cite(res, "Falhas registradas", "/maintenance/failures",
                       {"position_id": a.get("position_id"), "machine_id": a.get("machine_id")})

    async def t_get_predictive_alerts(self, res: ToolResult, a: dict) -> None:
        st, body = await self.get("/api/predictive/alerts", {k: a.get(k) for k in
                                                             ("state", "severity", "line_id", "position_id")})
        if not self._http(res, st, body, "os alertas preditivos"):
            return
        rows = body if isinstance(body, list) else body.get("items", body.get("rows", []))
        order = {"critical": 0, "warning": 1, "watch": 2}
        rows = sorted(rows, key=lambda r: order.get(r.get("severity"), 3))
        res.data = {"rows": self._rows(res, rows)}
        if len(rows) == 1 and rows[0].get("id"):
            self._cite(res, "Alerta preditivo", "/maintenance/predictive", {"alert": rows[0]["id"]})
        else:
            self._cite(res, "Alertas preditivos", "/maintenance/predictive")

    async def t_get_condition_trend(self, res: ToolResult, a: dict) -> None:
        st, mon = await self.get("/api/predictive/monitors", {"position_id": a["position_id"]})
        if not self._http(res, st, mon, "o monitoramento de condição"):
            return
        s, e, _ = await self.window(a)
        mons = mon if isinstance(mon, list) else mon.get("items", mon.get("rows", []))
        if a.get("feature"):
            mons = [m for m in mons if m.get("feature") in (None, a["feature"])]
        feature = a.get("feature") or (mons[0].get("feature") if mons else None)
        series = None
        if feature:
            st2, ser = await self.get("/api/predictive/features", {"position_id": a["position_id"], "feature": feature,
                                                                   "start": _iso(s), "stop": _iso(e)})
            if st2 == 200 and isinstance(ser, dict) and isinstance(ser.get("series"), list):
                # GET /api/predictive/features → {position_id, series: [{feature, unit, baselines, bands, limits, points}]}
                series = []
                for sr in ser["series"]:
                    pts = sr.get("points") or []
                    series.append({**{k: v for k, v in sr.items() if k != "points"},
                                   "points": pts[-settings.ai_max_rows:], "total_points": len(pts)})
            elif st2 == 200:
                pts = ser if isinstance(ser, list) else ser.get("points", ser.get("rows", []))
                series = {"points": pts[-settings.ai_max_rows:], "total_points": len(pts)}
        res.data = {"monitors": mons[: settings.ai_max_rows], "series": series}
        res.rows = len(mons)
        self._cite(res, "Condição da posição", "/maintenance/predictive",
                   {"position_id": a["position_id"], "feature": feature})
