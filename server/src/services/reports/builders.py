"""Report content (docs/reports.md §1): each builder composes API responses (via `ApiSource`, with the viewer's
permissions) into a `Doc`. Nothing here computes a KPI: values are copied from the API; the only arithmetic is
adding additive quantities the API returns (minutes, hours, counts — kpis.md §1.6 "somas fecham, razões não") and
the reference OEE of the 4 previous same weekdays, aggregated by §1.6 (Σ PRI·GQ / Σ PBT from the API components).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from ..oee.calendar import Calendar
from . import fmt, periods as P, weibull
from .catalog import BY_CODE
from .doc import Chart, Col, Doc, Para, Section, Table, coverage_note
from .source import ApiSource, Forbidden

UNPLANNED_SCHEDULED = "corretiva programada"
WO_TYPE_PT = {"preventive": "preventiva", "predictive": "preditiva", "improvement": "melhoria",
              "corrective_deferred": UNPLANNED_SCHEDULED, "corrective_emergency": "corretiva emergencial"}
REF_PMP, REF_COMPLIANCE, REF_BACKLOG = 0.85, 0.90, (2.0, 4.0)

DEF_OEE = [
    ("OEE", "Σ PRI·GQ / PBT — soma dos componentes, nunca média de percentuais", "kpis.md §1.5, §1.6 (ISO 22400)"),
    ("Disponibilidade (A)", "APT / PBT", "kpis.md §1.2"),
    ("Desempenho (P)", "Σ PRI·PQ / APT", "kpis.md §1.3"),
    ("Qualidade (Q)", "Σ PRI·GQ / Σ PRI·PQ", "kpis.md §1.4"),
    ("Cadência realizada", "PQ / PBT (peças/h)", "kpis.md §1.9"),
    ("Pareto de perdas", "minutos de perda dentro do PBT; % sobre o total de perdas", "kpis.md §1.10"),
    ("Cobertura de dados", "tempo com dados / POT; < 95 % → faixa 'dados incompletos'", "domain.md §4.1"),
]
DEF_MAINT = [
    ("PMP", "horas de trabalho planejado / horas totais de OS concluídas", "kpis.md §3.1 (ref. ≥ 85 %)"),
    ("Cumprimento da programação", "OS no prazo / OS programadas congeladas", "kpis.md §3.2, S4-6 (ref. ≥ 90 %)"),
    ("Backlog", "horas restantes / capacidade semanal da equipe", "kpis.md §3.3, S4-7 (ref. 2–4 semanas)"),
    ("Ao", "(T_req − D_eq) / T_req", "kpis.md §2.6"),
    ("MTBF", "tempo de operação / nº de falhas (critical + degraded), janela de 12 meses", "kpis.md §2.1, S4-8 (ISO 14224)"),
    ("MTTR", "Σ (restored_at − failed_at) / nº de falhas restabelecidas", "kpis.md §2.3 (ISO 14224)"),
]
DEF_WEIBULL = [
    ("Weibull 2P", "F(t) = 1 − exp(−(t/η)^β); MLE com censura à direita", "reports.md §2.2 (IEC 61649)"),
    ("IC 90 %", "matriz de Fisher, transformação log, z = 1,644854", "reports.md §2.3"),
    ("MTTF Weibull", "η·Γ(1 + 1/β)", "reports.md §2.4"),
    ("B10", "η·(−ln 0,9)^(1/β): idade em que 10 % falharam", "reports.md §2.4"),
    ("P(falha em 30 dias)", "1 − R(a + u·30) / R(a), u = ritmo dos últimos 14 dias", "reports.md §2.5"),
    ("MTTF simples", "média das idades na falha (ignora suspensões)", "kpis.md §2.4"),
]


@dataclass
class BuildCtx:
    tenant_id: str
    report_type: str
    scope_type: str
    scope_id: Optional[str]
    period: P.Period
    tz: ZoneInfo
    cal: Optional[Calendar]
    now: datetime
    viewer: Any
    params: dict = field(default_factory=dict)
    plant: Optional[dict] = None             # tree entry of the plant (lines → machines)
    line: Optional[dict] = None
    targets: dict = field(default_factory=dict)   # line_id → {"oee", "parts_per_shift"}
    app_url: str = ""
    observations: Optional[str] = None


# ── helpers ────────────────────────────────────────────────────────────────

def _flags(o: dict) -> list[str]:
    return [k for k in ("ideal_cycle_time_suspect", "product_unknown", "counts_prorated", "calendar_default")
            if o.get(k)]


def _oee_row(o: dict, **extra) -> dict:
    row = {k: o.get(k) for k in ("oee", "availability", "performance", "quality", "produced", "good", "scrap",
                                 "cadence_planned_per_hour", "data_coverage", "pbt_s", "apt_s", "adot_s")}
    row.update(extra)
    reason = o.get("reason")
    row["_reason"] = {k: reason for k in ("oee", "availability", "performance", "quality") if o.get(k) is None and reason}
    row["_notes"] = _flags(o)
    row["_coverage"] = o.get("data_coverage")
    return row


def _visible_lines(ctx: BuildCtx, lines: list[dict]) -> list[dict]:
    """§5.1 'linhas do usuário': User.line_ids empty = every line."""
    ids = {str(x) for x in (getattr(ctx.viewer, "line_ids", None) or [])}
    return [ln for ln in lines if not ids or ln["id"] in ids]


def _period_text(ctx: BuildCtx) -> str:
    per, tz = ctx.period, ctx.tz
    if per.start is None:
        return f"{per.label} ({ctx.tz.key})"
    a, b = per.start.astimezone(tz), per.end.astimezone(tz)
    kind = {"daily_production": "Dia produtivo", "shift": "Turno", "weekly_oee": "Semana (dias produtivos)",
            "monthly_maintenance": "Mês (dias produtivos)"}.get(ctx.report_type, "Período")
    return f"{kind} {per.label} ({a:%H:%M %d/%m} – {b:%H:%M %d/%m}, {tz.key})"


def _new_doc(ctx: BuildCtx, scope_label: str) -> Doc:
    c = BY_CODE[ctx.report_type]
    return Doc(report_type=ctx.report_type, title=c["title"], scope_label=scope_label,
               period_label=ctx.period.label, period_text=_period_text(ctx), tz=ctx.tz.key,
               landscape=c["landscape"], link=f"{ctx.app_url.rstrip('/')}{c['screen']}" if ctx.app_url else c["screen"])


def _loc(ts, tz) -> Optional[datetime]:
    t = fmt.parse_ts(ts)
    return t.astimezone(tz) if t else None


def _mins(s: Optional[float]) -> Optional[float]:
    return None if s is None else s / 60.0


async def _reason_labels(src: ApiSource) -> dict[str, str]:
    try:
        rows = await src.get_ok("/api/reason-codes/")
    except Exception:  # noqa: BLE001 — labels only
        return {}
    return {r["code"]: r["label"] for r in rows}


def _coverage_banner(doc_or_sec, cov: Optional[float], where: str = "") -> None:
    note = coverage_note(cov)
    if note:
        doc_or_sec.banners.append(f"{note}{' — ' + where if where else ''}")


OEE_COLS = [Col("oee", "OEE", "pct"), Col("availability", "A", "pct"), Col("performance", "P", "pct"),
            Col("quality", "Q", "pct")]


# ── daily_production (§1.1) ────────────────────────────────────────────────

async def build_daily_production(src: ApiSource, ctx: BuildCtx) -> Doc:
    plant, per = ctx.plant, ctx.period
    doc = _new_doc(ctx, plant["name"])
    ps, pe = per.start, per.end
    labels = await _reason_labels(src)
    lines = _visible_lines(ctx, plant["lines"])
    q = {"start": ps, "stop": pe, "group_by": "shift"}
    plant_oee = await src.get_ok("/api/oee", {"scope": "plant", "id": plant["id"], **q})
    doc.sources.append({"path": "/api/oee", "params": {"scope": "plant", "id": plant["id"], **q}, "key": "plant"})
    has_target = any(ctx.targets.get(ln["id"], {}).get("oee") for ln in lines)
    cover_rows, line_oee = [], {}
    for ln in lines:
        o = await src.get_ok("/api/oee", {"scope": "line", "id": ln["id"], **q})
        line_oee[ln["id"]] = o
        doc.sources.append({"path": "/api/oee", "params": {"scope": "line", "id": ln["id"], **q}, "key": ln["id"]})
        ref = await _same_weekday_reference(src, ctx, "line", ln["id"])
        row = _oee_row(o, name=ln["name"], line_id=ln["id"])
        row["ref_oee"] = ref
        row["delta_pp"] = (o["oee"] - ref) if (o.get("oee") is not None and ref is not None) else None
        if row["delta_pp"] is None:
            row["_reason"]["delta_pp"] = "insufficient_data"
        if has_target:
            row["target_oee"] = ctx.targets.get(ln["id"], {}).get("oee")
        if (o.get("produced_outside_schedule") or {}).get("total"):
            row["_notes"].append("outside_schedule")
        cover_rows.append(row)
        _coverage_banner(doc, o.get("data_coverage"), ln["name"])
    prow = _oee_row(plant_oee, name=f"{plant['name']} (planta, §1.6)", line_id=None)
    pref = await _same_weekday_reference(src, ctx, "plant", plant["id"])
    prow["ref_oee"] = pref
    prow["delta_pp"] = (plant_oee["oee"] - pref) if (plant_oee.get("oee") is not None and pref is not None) else None
    if prow["delta_pp"] is None:
        prow["_reason"]["delta_pp"] = "insufficient_data"
    if has_target:
        prow["target_oee"] = None
    cols = [Col("name", "Linha"), *OEE_COLS, Col("produced", "PQ", "int"), Col("good", "GQ", "int"),
            Col("scrap", "SQ", "int"), Col("cadence_planned_per_hour", "Cadência (peças/h)", "num1"),
            Col("delta_pp", "Δ OEE vs. 4 sem.", "pp")]
    if has_target:
        cols.append(Col("target_oee", "Meta OEE", "pct"))
    cols.append(Col("data_coverage", "Cobertura", "pct"))
    doc.summary_table = Table("Capa da planta — OEE do dia por linha", cols, cover_rows + [prow], sheet="Resumo",
                              note="OEE da planta = soma dos componentes das linhas (kpis.md §1.6), nunca média.")
    doc.summary = {"plant_oee": plant_oee.get("oee"), "lines": {ln["id"]: line_oee[ln["id"]].get("oee") for ln in lines}}
    doc.headline.append(("OEE da planta", fmt.with_reason(fmt.pct(plant_oee.get("oee")), plant_oee.get("reason"))))
    for ln in lines:
        doc.headline.append((f"OEE {ln['name']}", fmt.pct(line_oee[ln["id"]].get("oee"))))

    shift_rows, pareto_rows, stop_rows, unj_rows, planned_rows = [], [], [], [], []
    biggest = None
    for ln in lines:
        o = line_oee[ln["id"]]
        sec = Section(ln["name"] if ln["name"].lower().startswith("linha") else f"Linha {ln['name']}",
                      page_break=True)
        _coverage_banner(sec, o.get("data_coverage"))
        rows = []
        for pt in o.get("points", []):
            r = {k: pt.get(k) for k in ("label", "oee", "availability", "performance", "quality", "good", "scrap",
                                        "pbt_s")}
            r["line"] = ln["name"]
            r["_reason"] = {k: "no_planned_time" for k in ("oee",) if pt.get(k) is None and not pt.get("pbt_s")}
            rows.append(r)
        total = {"label": "Total do dia (soma dos turnos)", "line": ln["name"],
                 **{k: o.get(k) for k in ("oee", "availability", "performance", "quality", "good", "scrap", "pbt_s")},
                 "_reason": {k: o.get("reason") for k in ("oee",) if o.get(k) is None and o.get("reason")},
                 "_notes": _flags(o), "_coverage": o.get("data_coverage")}
        shift_rows += rows
        sec.blocks.append(Table("OEE por turno (S3-8: o total do dia é a soma dos turnos)",
                                [Col("label", "Turno"), *OEE_COLS, Col("good", "Boas", "int"),
                                 Col("scrap", "Refugo", "int"), Col("pbt_s", "PBT", "min", xlsx_label="pbt_min")],
                                rows + [total]))
        par = await src.get_ok("/api/losses/pareto", {"scope": "line", "id": ln["id"], "start": ps, "stop": pe})
        top = [r for r in par if r.get("duration_s", 0) > 0][:5]
        for r in top:
            pareto_rows.append({"line": ln["name"], "label": r["label"], "reason_code": r.get("reason_code"),
                                "duration_s": r["duration_s"], "pct": r.get("pct"), "events": r.get("events")})
            if biggest is None or r["duration_s"] > biggest[1]:
                biggest = (f"{r['label']} ({ln['name']})", r["duration_s"])
        sec.blocks.append(Table("Top-5 perdas (Pareto, kpis.md §1.10)",
                                [Col("label", "Perda"), Col("reason_code", "Código"),
                                 Col("duration_s", "Minutos", "min", xlsx_label="duracao_min"),
                                 Col("pct", "% das perdas", "pct")],
                                [{"label": r["label"], "reason_code": r.get("reason_code"), "duration_s": r["duration_s"],
                                  "pct": r.get("pct")} for r in top]))
        evs = await src.get_ok("/api/downtime/", {"line_id": ln["id"], "start": ps, "stop": pe, "limit": 5000})
        evs = [e for e in evs if e.get("productive_day") == per.first_day.isoformat()]
        evs.sort(key=lambda e: e["start"])

        def ev_row(e):
            dur = e.get("duration_s")
            if dur is None and e.get("end") is None:
                dur = (min(pe, ctx.now) - fmt.parse_ts(e["start"])).total_seconds()
            code = e.get("effective_reason_code")
            return {"line": ln["name"], "start": e["start"], "end": e.get("end"), "machine": e.get("machine_name"),
                    "reason_code": code, "reason": labels.get(code, code), "duration_s": dur,
                    "unjustified": e.get("unjustified"), "planned": e.get("planned"), "source": e.get("source")}

        long_ = [ev_row(e) for e in evs if not e.get("planned") and ((e.get("duration_s") or 0) >= 600 or e.get("end") is None)]
        unj = [ev_row(e) for e in evs if e.get("unjustified")]
        plan = [ev_row(e) for e in evs if e.get("planned")]
        stop_rows += long_
        unj_rows += unj
        planned_rows += plan
        ev_cols = [Col("start", "Início", "datetime", xlsx_label="inicio"), Col("end", "Fim", "datetime", xlsx_label="fim"),
                   Col("machine", "Máquina"), Col("reason", "Motivo efetivo (D2)"),
                   Col("duration_s", "Duração", "min", xlsx_label="duracao_min")]
        sec.blocks.append(Table("Paradas ≥ 10 min", ev_cols, long_))
        unj_total = sum(r["duration_s"] or 0 for r in unj)
        sec.blocks.append(Para(f"Paradas não justificadas: {len(unj)} — total {fmt.minutes(unj_total)}",
                               "warn" if unj else "ok"))
        if unj:
            sec.blocks.append(Table("Paradas não justificadas (D2)", ev_cols, unj))
        sec.blocks.append(Table("Paradas planejadas declaradas (S3-4)", ev_cols, plan))
        out_s = o.get("produced_outside_schedule") or {}
        if out_s.get("total"):
            sec.blocks.append(Para(f"Produção fora do calendário (S3-7): {fmt.count(out_s['total'])} peças "
                                   f"({fmt.count(out_s.get('good'))} boas) — cadastrar turno extra?", "warn"))
        doc.sections.append(sec)
    if biggest:
        doc.headline.append(("Maior perda do dia", f"{biggest[0]}: {fmt.minutes(biggest[1])}"))
        doc.summary["biggest_loss_s"] = biggest[1]

    # 3. maintenance of the day — only for viewers with maintenance.view (the API answers 403 otherwise)
    try:
        fails = await src.get_ok("/api/failures/", {"plant_id": plant["id"], "start": ps, "stop": pe, "limit": 2000})
        line_ids = {ln["id"] for ln in lines}
        fails = [f for f in fails if f.get("line_id") in line_ids]
        wos = await src.get_ok("/api/work-orders/", {"plant_id": plant["id"], "type": "corrective", "limit": 2000})
        em = []
        for w in wos:
            if w.get("urgency") == "deferred" or w.get("line_id") not in line_ids:
                continue
            c_at, d_at = fmt.parse_ts(w.get("created_at")), fmt.parse_ts(w.get("completed_at"))
            opened = c_at is not None and ps <= c_at < pe
            closed = d_at is not None and ps <= d_at < pe
            if opened or closed:
                em.append({"number": w.get("number"), "machine": w.get("machine_name"), "state": w.get("state"),
                           "created_at": w.get("created_at"), "completed_at": w.get("completed_at"),
                           "opened_today": opened, "closed_today": closed, "title": w.get("title")})
        sec = Section("Manutenção do dia", page_break=True)
        sec.blocks.append(Table("Falhas do dia (por failed_at; inclui falhas sem parada — kpis S5-1)",
                                [Col("failed_at", "Falha em", "datetime", xlsx_label="falha"),
                                 Col("machine_name", "Máquina"), Col("position_name", "Posição"),
                                 Col("severity", "Severidade"), Col("failure_mode", "Modo ISO 14224"),
                                 Col("restore_duration_s", "Restabelecimento", "min", xlsx_label="restabelecimento_min"),
                                 Col("with_stop", "Parou a produção")],
                                [{**{k: f.get(k) for k in ("failed_at", "machine_name", "position_name", "severity",
                                                           "failure_mode", "restore_duration_s")},
                                  "with_stop": "sim" if f.get("downtime_event_id") else "não",
                                  "_reason": {"restore_duration_s": "no_restored_failures"}
                                  if f.get("restore_duration_s") is None else {}} for f in fails],
                                sheet="Falhas"))
        sec.blocks.append(Table("OS emergenciais abertas/fechadas no dia",
                                [Col("number", "OS"), Col("machine", "Máquina"), Col("title", "Título"),
                                 Col("state", "Estado"), Col("created_at", "Aberta em", "datetime", xlsx_label="aberta"),
                                 Col("completed_at", "Concluída em", "datetime", xlsx_label="concluida")],
                                em, sheet="OS"))
        doc.sections.append(sec)
        doc.summary["failures"] = len(fails)
    except Forbidden:
        doc.omitted.append("Manutenção do dia (requer maintenance.view)")

    data_tables = [
        Table("OEE por turno", [Col("line", "linha"), Col("label", "turno"), *OEE_COLS, Col("good", "boas", "int"),
                                Col("scrap", "refugo", "int"), Col("pbt_s", "pbt_min", "min", xlsx_label="pbt_min")],
              shift_rows, sheet="OEE_por_turno"),
        Table("Pareto", [Col("line", "linha"), Col("label", "perda"), Col("reason_code", "codigo"),
                         Col("duration_s", "duracao_min", "min", xlsx_label="duracao_min"), Col("pct", "pct", "pct"),
                         Col("events", "eventos", "int")], pareto_rows, sheet="Pareto"),
        Table("Paradas", [Col("line", "linha"), Col("start", "inicio", "datetime", xlsx_label="inicio"),
                          Col("end", "fim", "datetime", xlsx_label="fim"), Col("machine", "maquina"),
                          Col("reason_code", "motivo"), Col("reason", "motivo_descricao"),
                          Col("duration_s", "duracao_min", "min", xlsx_label="duracao_min"),
                          Col("unjustified", "nao_justificada"), Col("planned", "planejada")],
              sorted(stop_rows + [r for r in unj_rows if r not in stop_rows] + planned_rows, key=lambda r: r["start"]),
              sheet="Paradas"),
    ]
    doc.sections.append(Section("Dados", data_tables, page_break=True))
    doc.definitions = DEF_OEE
    return doc


async def _same_weekday_reference(src: ApiSource, ctx: BuildCtx, scope: str, sid: str) -> Optional[float]:
    """OEE of the 4 previous same weekdays, aggregated by §1.6 (Σ PRI·GQ / Σ PBT of the API components)."""
    if ctx.cal is None or ctx.period.first_day is None:
        return None
    num = den = 0.0
    for k in range(1, 5):
        dp = P.day_period(ctx.cal, ctx.period.first_day - timedelta(days=7 * k))
        o = await src.get_ok("/api/oee", {"scope": scope, "id": sid, "start": dp.start, "stop": dp.end,
                                          "group_by": "shift"})
        if o.get("oee") is None:
            continue
        num += o.get("net_productive_s") or 0.0
        den += o.get("pbt_s") or 0.0
    return num / den if den > 0 else None


# ── shift (§1.2) ───────────────────────────────────────────────────────────

async def build_shift(src: ApiSource, ctx: BuildCtx) -> Doc:
    ln, per = ctx.line, ctx.period
    doc = _new_doc(ctx, f"{ctx.plant['name']} › {ln['name']}")
    labels = await _reason_labels(src)
    params = {"line_id": ln["id"], "productive_day": per.first_day.isoformat()}
    if per.shift_id:
        params["shift_id"] = per.shift_id
    sr = await src.get_ok("/api/reports/shift", params)
    doc.sources.append({"path": "/api/reports/shift", "params": params, "key": "shift"})
    o = sr.get("oee") or {}
    _coverage_banner(doc, o.get("data_coverage"))
    doc.summary_table = Table(f"Resumo — {per.label}", [Col("name", "Linha"), *OEE_COLS,
                                                        Col("produced", "PQ", "int"), Col("good", "GQ", "int"),
                                                        Col("scrap", "SQ", "int"),
                                                        Col("cadence_planned_per_hour", "Cadência (peças/h)", "num1"),
                                                        Col("data_coverage", "Cobertura", "pct")],
                              [_oee_row(o, name=ln["name"])] if o else [], sheet="Resumo",
                              empty_text=fmt.reason_pt(sr.get("reason")) or "Sem dados.")
    doc.summary = {"oee": o.get("oee")}
    doc.headline.append((f"OEE {ln['name']} — {per.label}", fmt.with_reason(fmt.pct(o.get("oee")), o.get("reason"))))
    sec = Section("Turno")
    sec.blocks.append(Table("Produção por máquina", [Col("machine_name", "Máquina"), *OEE_COLS,
                                                     Col("good", "Boas", "int"), Col("scrap", "Refugo", "int")],
                            sr.get("production", [])))
    top = [r for r in sr.get("pareto", []) if r.get("duration_s", 0) > 0]
    sec.blocks.append(Table("Pareto de perdas", [Col("label", "Perda"), Col("duration_s", "Minutos", "min"),
                                                  Col("pct", "%", "pct")], top[:10]))
    if top:
        doc.headline.append(("Maior perda", f"{top[0]['label']}: {fmt.minutes(top[0]['duration_s'])}"))
    evs = [{"start": e["start"], "end": e.get("end"), "machine": e.get("machine_name"),
            "reason": labels.get(e.get("effective_reason_code"), e.get("effective_reason_code")),
            "duration_s": e.get("duration_s"), "unjustified": e.get("unjustified")} for e in sr.get("downtime", [])]
    ev_cols = [Col("start", "Início", "datetime"), Col("end", "Fim", "datetime"), Col("machine", "Máquina"),
               Col("reason", "Motivo efetivo"), Col("duration_s", "Duração", "min")]
    sec.blocks.append(Table("Paradas do turno", ev_cols, evs, sheet="Paradas"))
    doc.sections.append(sec)
    pend = Section("Pendências para o próximo turno")
    open_evs = await src.get_ok("/api/downtime/", {"line_id": ln["id"], "open": True})
    pend.blocks.append(Table("Paradas abertas", ev_cols, [
        {"start": e["start"], "end": None, "machine": e.get("machine_name"),
         "reason": labels.get(e.get("effective_reason_code"), e.get("effective_reason_code")),
         "duration_s": None, "_reason": {"duration_s": "no_data"}} for e in open_evs]))
    unj = [e for e in evs if e.get("unjustified")]
    pend.blocks.append(Table("Paradas sem motivo", ev_cols, unj))
    wos = await src.get_ok("/api/work-orders/", {"line_id": ln["id"], "type": "corrective",
                                                 "state": ["open", "approved", "scheduled", "in_progress", "on_hold"]})
    em = [{"number": w.get("number"), "machine": w.get("machine_name"), "title": w.get("title"),
           "state": w.get("state")} for w in wos if w.get("urgency") != "deferred"]
    pend.blocks.append(Table("OS emergenciais abertas na linha", [Col("number", "OS"), Col("machine", "Máquina"),
                                                                    Col("title", "Título"), Col("state", "Estado")], em))
    try:
        alerts = await src.get_ok("/api/predictive/alerts", {"line_id": ln["id"], "state": ["open", "acknowledged"]})
        al = [{"machine": a.get("machine_name"), "position": a.get("position_name"), "feature": a.get("feature_label"),
               "severity": a.get("severity_label") or a.get("severity"), "opened_at": a.get("opened_at")}
              for a in alerts if a.get("severity") in ("warning", "critical")]
        pend.blocks.append(Table("Alertas preditivos warning/critical abertos",
                                 [Col("machine", "Máquina"), Col("position", "Posição"), Col("feature", "Sinal"),
                                  Col("severity", "Severidade"), Col("opened_at", "Aberto em", "datetime")], al))
    except Forbidden:
        doc.omitted.append("Alertas preditivos (requer acesso à preditiva)")
    doc.sections.append(pend)
    obs = ctx.observations or ctx.params.get("observations")
    doc.sections.append(Section("Observações do supervisor", [Para(obs or "—")]))
    doc.definitions = DEF_OEE
    return doc


# ── weekly_oee (§1.3) ──────────────────────────────────────────────────────

async def build_weekly_oee(src: ApiSource, ctx: BuildCtx) -> Doc:
    per, cal = ctx.period, ctx.cal
    if ctx.scope_type == "line":
        scope, sid, label = "line", ctx.line["id"], f"{ctx.plant['name']} › {ctx.line['name']}"
        machines = ctx.line["machines"]
    else:
        scope, sid, label = "plant", ctx.plant["id"], ctx.plant["name"]
        machines = [m for ln in _visible_lines(ctx, ctx.plant["lines"]) for m in ln["machines"]]
    doc = _new_doc(ctx, label)
    ps, pe = per.start, per.end
    trend = []
    for k in range(11, -1, -1):
        wk = P.week_period(cal, per.first_day - timedelta(days=7 * k))
        o = await src.get_ok("/api/oee", {"scope": scope, "id": sid, "start": wk.start, "stop": wk.end,
                                          "group_by": "shift"})
        r = _oee_row(o, week=wk.label, week_key=wk.key, week_short=f"{wk.first_day:%d/%m}", current=(k == 0))
        trend.append(r)
    cur = trend[-1]
    _coverage_banner(doc, cur.get("data_coverage"))
    doc.summary_table = Table("Resumo da semana", [Col("week", "Semana"), *OEE_COLS, Col("good", "Boas", "int"),
                                                   Col("scrap", "Refugo", "int"), Col("data_coverage", "Cobertura", "pct")],
                              [cur], sheet="Resumo")
    doc.summary = {"oee": cur.get("oee"), "trend": [t.get("oee") for t in trend]}
    doc.headline.append((f"OEE da semana — {label}", fmt.with_reason(fmt.pct(cur.get("oee")),
                                                                    (cur.get("_reason") or {}).get("oee"))))
    s1 = Section("Tendência — últimas 12 semanas")
    s1.blocks.append(Chart("line", "OEE, A, P, Q por semana", {
        "x": [t["week"] for t in trend], "x_labels": [t["week_short"] for t in trend], "highlight": len(trend) - 1,
        "series": {n: [t.get(k) for t in trend] for n, k in (("OEE", "oee"), ("A", "availability"),
                                                              ("P", "performance"), ("Q", "quality"))}}))
    s1.blocks.append(Table("OEE por semana", [Col("week", "Semana"), *OEE_COLS, Col("data_coverage", "Cobertura", "pct")],
                           trend, sheet="OEE_por_semana"))
    doc.sections.append(s1)

    prev_wk = P.week_period(cal, per.first_day - timedelta(days=7))
    par = await src.get_ok("/api/losses/pareto", {"scope": scope, "id": sid, "start": ps, "stop": pe})
    par_prev = await src.get_ok("/api/losses/pareto", {"scope": scope, "id": sid, "start": prev_wk.start,
                                                       "stop": prev_wk.end})
    prev_by = {(r.get("reason_code"), r["label"]): r["duration_s"] for r in par_prev}
    prows = []
    for r in [x for x in par if x.get("duration_s", 0) > 0][:10]:
        pv = prev_by.get((r.get("reason_code"), r["label"]), 0.0)
        prows.append({"label": r["label"], "reason_code": r.get("reason_code"), "duration_s": r["duration_s"],
                      "pct": r.get("pct"), "prev_s": pv, "delta_s": r["duration_s"] - pv})
    s2 = Section("Pareto da semana", page_break=True)
    s2.blocks.append(Table("Top-10 perdas por motivo (Δ vs. semana anterior)",
                           [Col("label", "Perda"), Col("reason_code", "Código"),
                            Col("duration_s", "Minutos", "min", xlsx_label="duracao_min"), Col("pct", "%", "pct"),
                            Col("delta_s", "Δ min", "min", xlsx_label="delta_min")], prows, sheet="Pareto"))
    by_machine, m_rows, m_par = [], [], {}
    for m in machines:
        mo = await src.get_ok("/api/oee", {"scope": "machine", "id": m["id"], "start": ps, "stop": pe,
                                           "group_by": "shift"})
        mp = await src.get_ok("/api/losses/pareto", {"scope": "machine", "id": m["id"], "start": ps, "stop": pe})
        m_par[m["id"]] = mp
        loss = sum(r.get("duration_s", 0.0) for r in mp)
        by_machine.append({"machine": m["name"], "duration_s": loss})
        m_rows.append({"machine": m["name"], "micro_stop_count": mo.get("micro_stop_count"),
                       "micro_stops_per_apt_hour": mo.get("micro_stops_per_apt_hour"),
                       "micro_stop_loss_s": mo.get("micro_stop_loss_s"),
                       "_reason": {"micro_stops_per_apt_hour": "no_planned_time"}
                       if mo.get("micro_stops_per_apt_hour") is None else {}})
    by_machine.sort(key=lambda r: -r["duration_s"])
    s2.blocks.append(Table("Perdas por máquina (top-10)", [Col("machine", "Máquina"),
                                                           Col("duration_s", "Minutos de perda", "min",
                                                               xlsx_label="perda_min")], by_machine[:10]))
    doc.sections.append(s2)

    s3 = Section("Comparação de turnos e produtos", page_break=True)
    sn = await src.get_ok("/api/oee", {"scope": scope, "id": sid, "start": ps, "stop": pe, "group_by": "shift_name"})
    s3.blocks.append(Table("OEE por turno (soma das instâncias, §1.6)",
                           [Col("label", "Turno"), *OEE_COLS, Col("good", "Boas", "int"), Col("scrap", "Refugo", "int"),
                            Col("instances", "Instâncias", "int")], sn.get("points", []), sheet="OEE_por_turno"))
    s3.blocks.append(Para("Comparação por produto: a API de OEE ainda não separa o OEE por produto; a seção fica "
                          "vazia em vez de mostrar uma estimativa.", "small"))
    doc.sections.append(s3)

    s4 = Section("Micro-paradas e disciplina de apontamento")
    s4.blocks.append(Table("Micro-paradas por máquina (kpis.md §1.8)",
                           [Col("machine", "Máquina"), Col("micro_stop_count", "Nº", "int"),
                            Col("micro_stops_per_apt_hour", "Nº por hora de APT", "num2"),
                            Col("micro_stop_loss_s", "Minutos", "min", xlsx_label="micro_min")], m_rows,
                           sheet="Micro_paradas"))
    q = {"start": ps, "stop": pe, "limit": 5000}
    q.update({"line_id": ctx.line["id"]} if scope == "line" else {})
    evs = await src.get_ok("/api/downtime/", q)
    mids = {m["id"] for m in machines}
    evs = [e for e in evs if e["machine_id"] in mids]
    unpl = [e for e in evs if not e.get("planned") and e.get("duration_s")]
    tot = sum(e["duration_s"] for e in unpl)
    just = sum(e["duration_s"] for e in unpl if not e.get("unjustified"))
    plan = [e for e in evs if e.get("planned")]
    disc = {"unplanned_s": tot, "justified_s": just, "justified_pct": (just / tot) if tot > 0 else None,
            "planned_count": len(plan), "planned_s": sum(e.get("duration_s") or 0 for e in plan),
            "_reason": {"justified_pct": "no_data"} if tot <= 0 else {}}
    s4.blocks.append(Table("Disciplina de apontamento (D2, S3-4)",
                           [Col("unplanned_s", "Parada não planejada", "min"),
                            Col("justified_s", "Justificada", "min"), Col("justified_pct", "% justificada", "pct"),
                            Col("planned_count", "Planejadas declaradas", "int"),
                            Col("planned_s", "Minutos planejados", "min")], [disc]))
    doc.sections.append(s4)

    s5 = Section("3 perdas a atacar")
    attack = []
    for r in [x for x in par if x.get("duration_s", 0) > 0][:3]:
        main, best = None, 0.0
        for m in machines:
            for mr in m_par.get(m["id"], []):
                if (mr.get("reason_code"), mr["label"]) == (r.get("reason_code"), r["label"]) and mr["duration_s"] > best:
                    main, best = m["name"], mr["duration_s"]
        attack.append({"label": r["label"], "duration_s": r["duration_s"], "machine": main, "machine_s": best,
                       "_reason": {"machine": "no_data"} if main is None else {}})
    s5.blocks.append(Table("Maiores perdas da semana (regra: 3 maiores em minutos; sem texto gerado)",
                           [Col("label", "Perda"), Col("duration_s", "Minutos", "min"),
                            Col("machine", "Máquina principal"), Col("machine_s", "Minutos na máquina", "min")], attack))
    if attack:
        doc.headline.append(("Maior perda", f"{attack[0]['label']}: {fmt.minutes(attack[0]['duration_s'])}"))
    doc.sections.append(s5)
    doc.definitions = DEF_OEE
    return doc


# ── monthly_maintenance (§1.4) ─────────────────────────────────────────────

def _month_bounds(tz: ZoneInfo, y: int, m: int) -> tuple[datetime, datetime]:
    first = date(y, m, 1)
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return P.midnight(first, tz), P.midnight(nxt, tz)


def _shift_month(y: int, m: int, k: int) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + k
    return idx // 12, idx % 12 + 1


def _wo_bucket(w: dict) -> str:
    t = w.get("type")
    if t == "corrective":
        return "corrective_deferred" if w.get("urgency") == "deferred" else "corrective_emergency"
    return t or "other"


async def build_monthly_maintenance(src: ApiSource, ctx: BuildCtx) -> Doc:
    plant, per, tz, cal = ctx.plant, ctx.period, ctx.tz, ctx.cal
    doc = _new_doc(ctx, plant["name"])
    y, m = per.first_day.year, per.first_day.month
    ms_, me_ = _month_bounds(tz, y, m)
    hist = []
    kpis_ok = True
    for k in range(-12, 1):
        yy, mm = _shift_month(y, m, k)
        s, e = _month_bounds(tz, yy, mm)
        mp = P.month_period(cal, yy, mm)
        row = {"month": f"{yy}-{mm:02d}", "label": mp.label, "current": k == 0, "_reason": {}, "_flag": {}, "_notes": []}
        try:
            kp = await src.get_ok("/api/maintenance/kpis", {"plant_id": plant["id"], "start": s, "stop": e})
            row.update(pmp=kp.get("pmp"), schedule_compliance=kp.get("schedule_compliance"),
                       total_hours=kp.get("total_hours"), planned_hours=kp.get("planned_hours"))
            if kp.get("pmp") is None:
                row["_reason"]["pmp"] = kp.get("pmp_reason")
            if kp.get("schedule_compliance") is None:
                row["_reason"]["schedule_compliance"] = kp.get("compliance_reason")
            if k == 0:
                row["backlog_weeks"] = kp.get("backlog_weeks")
                row["backlog_hours"] = kp.get("backlog_hours")
                row["_notes"].append("backlog_snapshot")
                if kp.get("backlog_weeks") is None:
                    row["_reason"]["backlog_weeks"] = kp.get("backlog_weeks_reason")
                doc.summary["kpis"] = kp
            else:
                row["backlog_weeks"] = None
                row["_reason"]["backlog_weeks"] = "snapshot_only"
        except Forbidden:
            kpis_ok = False
            break
        o = await src.get_ok("/api/oee", {"scope": "plant", "id": plant["id"], "start": mp.start, "stop": mp.end})
        row["ao"] = o.get("availability_operational")
        if row["ao"] is None:
            row["_reason"]["ao"] = o.get("reason") or "no_planned_time"
        row["_coverage"] = o.get("data_coverage")
        rs = _shift_month(yy, mm, -12)
        rstart = _month_bounds(tz, *rs)[1]     # S4-8: 12 months ending at the end of month (yy, mm)
        rel = await src.get_ok("/api/reliability", {"scope": "plant", "id": plant["id"], "start": rstart, "stop": e})
        row["mtbf_h"], row["mttr_min"] = rel.get("mtbf_h"), (rel.get("mttr_s") / 60 if rel.get("mttr_s") is not None else None)
        row["failures"] = rel.get("failures")
        if row["mtbf_h"] is None:
            row["_reason"]["mtbf_h"] = rel.get("reason") or "no_failures"
        if row["mttr_min"] is None:
            row["_reason"]["mttr_min"] = "no_failures" if not rel.get("failures") else "no_restored_failures"
        row["_flag"] = {"pmp": fmt.flag_min(row.get("pmp"), REF_PMP),
                        "schedule_compliance": fmt.flag_min(row.get("schedule_compliance"), REF_COMPLIANCE),
                        "backlog_weeks": fmt.flag_range(row.get("backlog_weeks"), *REF_BACKLOG)}
        hist.append(row)
    if not kpis_ok:
        doc.omitted.append("Indicadores de gestão da manutenção (requer maintenance.kpis)")
        doc.summary_table = Table("Indicadores do mês", [Col("x", "—")], [], empty_text="Sem permissão para os "
                                  "indicadores de gestão da manutenção (maintenance.kpis).")
        doc.definitions = DEF_MAINT
        return doc
    cur = hist[-1]
    _coverage_banner(doc, cur.get("_coverage"))
    kcols = [Col("label", "Mês"), Col("pmp", "PMP (ref. ≥ 85 %)", "pct"),
             Col("schedule_compliance", "Cumprimento (ref. ≥ 90 %)", "pct"),
             Col("backlog_weeks", "Backlog (2–4 sem.)", "num1", unit="semanas"), Col("ao", "Ao", "pct"),
             Col("mtbf_h", "MTBF (12 m)", "hours", unit="h"), Col("mttr_min", "MTTR (12 m)", "num1", unit="min")]
    doc.summary_table = Table(f"Indicadores do mês — {per.label}", kcols, [cur], sheet="Resumo")
    doc.summary.update({k: cur.get(k) for k in ("pmp", "schedule_compliance", "backlog_weeks", "ao", "mtbf_h",
                                                 "mttr_min")})
    doc.summary["flags"] = cur["_flag"]
    for lab, key, txt in (("PMP", "pmp", fmt.pct), ("Cumprimento", "schedule_compliance", fmt.pct),
                          ("Backlog", "backlog_weeks", lambda v: f"{fmt.number(v, 1)} semanas" if v is not None else fmt.DASH),
                          ("MTBF", "mtbf_h", lambda v: fmt.hours(v)), ("MTTR", "mttr_min", lambda v: fmt.hours(v, "min")),
                          ("Ao", "ao", fmt.pct)):
        doc.headline.append((lab, fmt.with_reason(txt(cur.get(key)), cur["_reason"].get(key))))
    s1 = Section("Indicadores do mês e dos 12 meses anteriores")
    s1.blocks.append(Chart("line", "PMP e cumprimento da programação", {
        "x": [h["month"] for h in hist], "x_labels": [f"{h['month'][5:7]}/{h['month'][2:4]}" for h in hist],
        "highlight": len(hist) - 1,
        "series": {"PMP": [h.get("pmp") for h in hist], "Cumprimento": [h.get("schedule_compliance") for h in hist]},
        "refs": {"PMP ≥ 85 %": REF_PMP, "Cumprimento ≥ 90 %": REF_COMPLIANCE}}))
    s1.blocks.append(Table("Histórico (backlog só no fechamento — S4-7 é uma fotografia)", kcols, hist,
                           sheet="Indicadores"))
    doc.sections.append(s1)

    s2 = Section("Horas de OS por tipo", page_break=True)
    wos = await src.get_ok("/api/work-orders/", {"plant_id": plant["id"], "completed_from": ms_, "completed_to": me_,
                                                 "limit": 2000})
    hours_by: dict[str, float] = defaultdict(float)
    for w in wos:
        if w.get("state") in ("completed", "closed") and w.get("labor_hours") is not None:
            hours_by[_wo_bucket(w)] += float(w["labor_hours"])
    hrows = [{"type": WO_TYPE_PT[k], "hours": hours_by.get(k, 0.0)} for k in WO_TYPE_PT]
    hrows.append({"type": "total", "hours": sum(hours_by.get(k, 0.0) for k in WO_TYPE_PT)})
    kp = doc.summary.get("kpis") or {}
    s2.blocks.append(Table("Horas apontadas em OS concluídas no mês", [Col("type", "Tipo"), Col("hours", "Horas", "num1",
                                                                                                unit="h")],
                           hrows, sheet="OS"))
    s2.blocks.append(Para(f"Horas de OS preditivas sem defeito (falso alarme, predictive.md P-9), à parte: "
                          f"{fmt.hours(kp.get('predictive_no_defect_hours'))}", "small"))
    doc.summary["hours_by_type"] = dict(hours_by)
    doc.sections.append(s2)

    s3 = Section("Maus atores (top-10)", page_break=True)
    rel_m = await src.get_ok("/api/maintenance/reliability", {"group_by": "machine", "plant_id": plant["id"],
                                                               "start": ms_, "stop": me_})
    fails = await src.get_ok("/api/failures/", {"plant_id": plant["id"], "start": ms_, "stop": me_, "limit": 2000})
    evs = []
    for ln in plant["lines"]:
        evs += await src.get_ok("/api/downtime/", {"line_id": ln["id"], "start": ms_, "stop": me_, "limit": 5000})
    eqp_s: dict[str, float] = defaultdict(float)
    for e in evs:
        if (e.get("effective_reason_code") or "").startswith("EQP-") and e.get("duration_s"):
            eqp_s[e["machine_id"]] += e["duration_s"]
    modes: dict[str, Counter] = defaultdict(Counter)
    pos_modes: dict[str, Counter] = defaultdict(Counter)
    for f in fails:
        if f.get("failure_mode"):
            modes[f["machine_id"]][f["failure_mode"]] += 1
            if f.get("position_id"):
                pos_modes[f["position_id"]][f["failure_mode"]] += 1
    bad = []
    for r in rel_m.get("rows", []):
        mid = r.get("machine_id")
        bad.append({"machine": r.get("machine_name"), "failures": r.get("n") or 0,
                    "eqp_s": eqp_s.get(mid, 0.0), "mode": (modes[mid].most_common(1)[0][0] if modes[mid] else None),
                    # kpis S5-1c item 6 (audit A-6): computed by the API from the calendar, not a proxy
                    "failures_outside_production": r.get("failures_outside_production") or 0,
                    "_reason": {"mode": "no_data"} if not modes[mid] else {}})
    bad = [b for b in bad if b["failures"] or b["eqp_s"]]
    bad.sort(key=lambda b: (-b["failures"], -b["eqp_s"]))
    s3.blocks.append(Table("Máquinas por nº de falhas e minutos de parada EQP-*",
                           [Col("machine", "Máquina"), Col("failures", "Falhas", "int"),
                            Col("eqp_s", "Parada EQP-*", "min", xlsx_label="eqp_min"),
                            Col("mode", "Modo dominante (ISO 14224)"),
                            Col("failures_outside_production", "Falhas sem tempo produtivo", "int")], bad[:10],
                           sheet="Falhas"))
    rel_p = await src.get_ok("/api/maintenance/reliability", {"group_by": "position", "plant_id": plant["id"],
                                                               "start": ms_, "stop": me_})
    prow = [{"position": r.get("position_name"), "machine": r.get("machine_name"), "failures": r.get("n") or 0,
             "mode": (pos_modes[r["position_id"]].most_common(1)[0][0] if pos_modes.get(r.get("position_id")) else None)}
            for r in rel_p.get("rows", []) if r.get("n")]
    prow.sort(key=lambda r: -r["failures"])
    s3.blocks.append(Table("Posições por nº de falhas", [Col("position", "Posição"), Col("machine", "Máquina"),
                                                         Col("failures", "Falhas", "int"),
                                                         Col("mode", "Modo dominante")], prow[:10]))
    doc.sections.append(s3)

    s4 = Section("Preditiva")
    try:
        ev = await src.get_ok("/api/predictive/evaluation", {"start": ms_, "stop": me_})
        outcomes = Counter((r.get("outcome") or r.get("status") or "—") for r in ev.get("alert_outcomes", []))
        s4.blocks.append(Table("Alertas do mês (predictive.md P-10)",
                               [Col("alerts", "Alertas", "int"), Col("true_positives", "Verdadeiros", "int"),
                                Col("false_alarms", "Falsos alarmes", "int"), Col("pending", "Pendentes", "int"),
                                Col("precision", "Precisão", "pct"), Col("detection_rate", "Detecção", "pct"),
                                Col("lead_time_h_median", "Antecedência mediana", "hours", unit="h")],
                               [{**{k: ev.get(k) for k in ("alerts", "true_positives", "false_alarms", "pending",
                                                           "precision", "detection_rate", "lead_time_h_median")},
                                 "_reason": {k: v for k, v in (("precision", ev.get("precision_reason")),
                                                               ("detection_rate", ev.get("detection_reason")),
                                                               ("lead_time_h_median", "no_data"))
                                             if ev.get(k) is None}}]))
        if outcomes:
            s4.blocks.append(Table("Desfecho dos alertas", [Col("outcome", "Desfecho"), Col("n", "Nº", "int")],
                                   [{"outcome": k, "n": v} for k, v in sorted(outcomes.items())]))
    except Forbidden:
        doc.omitted.append("Preditiva (requer planejador ou gerente)")
    doc.sections.append(s4)

    s5 = Section("Componentes e planos no fim do mês")
    comps = await src.get_ok("/api/maintenance/components", {"plant_id": plant["id"], "min_life": 0.8})
    s5.blocks.append(Table("Componentes com ≥ 80 % de vida", [Col("machine_name", "Máquina"),
                                                               Col("position_name", "Posição"),
                                                               Col("manufacturer", "Fabricante"), Col("model", "Modelo"),
                                                               Col("life_consumed_pct", "% de vida", "pct"),
                                                               Col("projected_end_date", "Fim projetado", "date")],
                           comps, sheet="Componentes"))
    plans = await src.get_ok("/api/maintenance-plans/status", {"plant_id": plant["id"], "state": "overdue"})
    s5.blocks.append(Table("Planos vencidos (overdue)", [Col("plan_name", "Plano"), Col("machine_name", "Máquina"),
                                                         Col("position_name", "Posição"),
                                                         Col("consumed", "Consumido", "pct")], plans))
    doc.sections.append(s5)
    doc.definitions = DEF_MAINT
    return doc


# ── reliability_life (§1.5) ────────────────────────────────────────────────

async def build_reliability_life(src: ApiSource, ctx: BuildCtx) -> Doc:
    per = ctx.period
    label = ctx.plant["name"] if ctx.plant else "Todas as plantas"
    doc = _new_doc(ctx, label)
    params = {"cutoff": per.end}
    if ctx.plant:
        params["plant_id"] = ctx.plant["id"]
    if ctx.params.get("component_type"):
        params["component_type"] = ctx.params["component_type"]
    data = await src.get_ok("/api/reports/reliability-life", params)
    doc.sources.append({"path": "/api/reports/reliability-life", "params": params, "key": "life"})
    gcols = [Col("label", "Fabricante / modelo"), Col("n", "N", "int"), Col("r", "Falhas (r)", "int"),
             Col("failures_from_predictive", "Falhas via OS preditiva", "int"),
             Col("suspensions_removed", "Susp. removidas", "int"), Col("suspensions_in_service", "Em serviço", "int"),
             Col("simple_mttf", "MTTF simples", "num0"), Col("beta", "β", "num2"), Col("beta_ci", "β IC 90 %"),
             Col("eta", "η", "num0"), Col("eta_ci", "η IC 90 %"), Col("mttf", "MTTF Weibull", "num0"),
             Col("b10", "B10", "num0"), Col("b10_ci", "B10 IC 90 %"), Col("nominal_life", "Vida nominal", "num0"),
             Col("b10_over_nominal", "B10 / nominal", "num2"), Col("pattern_label", "Padrão de β")]
    xcols = [Col("component_type", "tipo"), Col("manufacturer", "fabricante"), Col("model", "modelo"),
             Col("measure", "medida"), Col("n", "n", "int"), Col("r", "r", "int"),
             Col("failures_from_predictive", "falhas_os_preditiva", "int"),
             Col("suspensions_removed", "suspensoes_removidas", "int"),
             Col("suspensions_in_service", "em_servico", "int"), Col("simple_mttf", "mttf_simples", "num6"),
             Col("beta", "beta", "num6"), Col("beta_lo", "beta_ic90_inf", "num6"), Col("beta_hi", "beta_ic90_sup", "num6"),
             Col("eta", "eta", "num6"), Col("eta_lo", "eta_ic90_inf", "num6"), Col("eta_hi", "eta_ic90_sup", "num6"),
             Col("mttf", "mttf_weibull", "num6"), Col("b10", "b10", "num6"), Col("b10_lo", "b10_ic90_inf", "num6"),
             Col("b10_hi", "b10_ic90_sup", "num6"), Col("nominal_life", "vida_nominal", "num6"),
             Col("b10_over_nominal", "b10_sobre_nominal", "num6"), Col("pattern", "padrao"),
             Col("low_confidence", "baixa_confianca"), Col("reason", "motivo")]
    ucols = [Col("component_type", "tipo"), Col("manufacturer", "fabricante"), Col("model", "modelo"),
             Col("serial_number", "serie"), Col("machine_name", "maquina"), Col("position_name", "posicao"),
             Col("measure", "medida"), Col("age", "idade", "num6"), Col("delta", "delta", "int"),
             Col("removal_reason", "motivo_remocao"), Col("installed_at", "instalado", "datetime", xlsx_label="instalado"),
             Col("removed_at", "removido", "datetime", xlsx_label="removido"), Col("in_service", "em_servico"),
             Col("excluded_reason", "excluida_motivo")]
    all_groups, all_units = [], []
    for t in data.get("types", []):
        sec = Section(f"{t['label']} — medida: {t.get('unit') or 'sem medida'}", page_break=True)
        rows = []
        for g in t["groups"]:
            row = {**{k: g.get(k) for k in ("component_type", "manufacturer", "model", "measure", "n", "r",
                                            "failures_from_predictive",
                                            "suspensions_removed", "suspensions_in_service", "simple_mttf", "beta",
                                            "beta_lo", "beta_hi", "eta", "eta_lo", "eta_hi", "mttf", "b10", "b10_lo",
                                            "b10_hi", "nominal_life", "b10_over_nominal", "pattern", "low_confidence",
                                            "reason")},
                   "label": f"{g['manufacturer']} / {g['model']}", "type_label": t["label"],
                   "beta_ci": _ci(g.get("beta_lo"), g.get("beta_hi"), 2),
                   "eta_ci": _ci(g.get("eta_lo"), g.get("eta_hi"), 0),
                   "b10_ci": _ci(g.get("b10_lo"), g.get("b10_hi"), 0),
                   "pattern_label": weibull.PATTERN_LABEL.get(g.get("pattern")) if g.get("pattern") else None,
                   "_notes": list(g.get("notes", [])), "_reason": {}}
            if g.get("beta") is None:
                for k in ("beta", "eta", "mttf", "b10", "b10_over_nominal", "pattern_label", "beta_ci", "eta_ci", "b10_ci"):
                    row["_reason"][k] = g.get("reason")
            if g.get("simple_mttf") is None:
                row["_reason"]["simple_mttf"] = "no_failures"
            if g.get("nominal_life") is None:
                row["_reason"]["nominal_life"] = "no_nominal_life"
                row["_reason"].setdefault("b10_over_nominal", "no_nominal_life")
            rows.append(row)
            all_groups.append(row)
            for u in g.get("units", []):
                all_units.append({**u, "delta": 1 if u.get("failed") else 0})
        for u in t.get("excluded", []):
            all_units.append({**u, "delta": None})
        sec.blocks.append(Table("Grupos (fabricante, modelo) — Weibull 2P com censura à direita", gcols, rows))
        for g, row in zip(t["groups"], rows):
            if g.get("beta") is None:
                continue
            pat = g.get("pattern")
            txt = (f"{row['label']}: β = {fmt.number(g['beta'], 2)} ({row['beta_ci']}) → "
                   f"{weibull.PATTERN_TEXT.get(pat) if pat else '—'}")
            if g.get("below_nominal"):
                txt += " Vida real abaixo da nominal (B10 / nominal < 0,8)."
            if g.get("low_confidence"):
                txt += " Aviso: amostra pequena — com poucas falhas o β estimado tende a sair alto."
            sec.blocks.append(Para(txt, "warn" if g.get("low_confidence") or g.get("below_nominal") else "p"))
            sec.blocks.append(Chart("weibull", f"Papel de Weibull — {row['label']}",
                                    {"points": g.get("plot", []), "line": g.get("fit_line", []), "unit": t.get("unit"),
                                     "beta": g["beta"], "eta": g["eta"]}))
        fitted = [g for g in t["groups"] if g.get("b10") is not None]
        if fitted:
            sec.blocks.append(Chart("bars_ci", "B10 com IC 90 % por grupo", {
                "labels": [f"{g['manufacturer']} / {g['model']}" for g in fitted],
                "values": [g["b10"] for g in fitted], "lo": [g.get("b10_lo") for g in fitted],
                "hi": [g.get("b10_hi") for g in fitted], "unit": t.get("unit")}))
        for c in t.get("comparison", []):
            sec.blocks.append(Para(f"{c['a'].split('|', 1)[1].replace('|', ' / ')} × "
                                   f"{c['b'].split('|', 1)[1].replace('|', ' / ')}: {c['text']}", "small"))
        if t.get("excluded"):
            sec.blocks.append(Para(f"Unidades excluídas do ajuste: {len(t['excluded'])} "
                                   f"({', '.join(sorted({fmt.reason_pt(u['excluded_reason']) for u in t['excluded']}))})",
                                   "small"))
        doc.sections.append(sec)
    risk = data.get("in_service_risk", [])
    rsec = Section("Em serviço: maior probabilidade de falhar nos próximos 30 dias", page_break=True)
    rsec.blocks.append(Table("P(falha em 30 dias) = 1 − R(a + u·30) / R(a) (§2.5)",
                             [Col("machine_name", "Máquina"), Col("position_name", "Posição"),
                              Col("manufacturer", "Fabricante"), Col("model", "Modelo"),
                              Col("age", "Idade", "num0"), Col("rate_per_day", "Ritmo/dia", "num0"),
                              Col("p_fail", "P(falha 30 d)", "pct")],
                             [{**r, "_reason": {"p_fail": r.get("p_reason")} if r.get("p_fail") is None else {}}
                              for r in risk], sheet="Em_servico_risco"))
    doc.sections.append(rsec)
    doc.summary_table = Table("Resumo por grupo", [Col("type_label", "Tipo"), *gcols], all_groups, sheet="Resumo")
    doc.sections.append(Section("Dados", [Table("Weibull_grupos", xcols, all_groups, sheet="Weibull_grupos"),
                                          Table("Weibull_unidades", ucols, all_units, sheet="Weibull_unidades")],
                                page_break=True))
    doc.readme += [data.get("method", ""), "Componente reparado e reinstalado começa uma nova unidade "
                   "(hipótese 'tão bom quanto novo').",
                   "Falha = removido com removal_reason = failure (inclui defeito confirmado em OS preditiva); "
                   "qualquer outra remoção e todo componente em serviço é suspensão (censura à direita).",
                   "Diferença entre marcas só é 'significativa' quando os IC 90 % de η não se sobrepõem."]
    doc.summary = {"groups": len(all_groups), "fitted": sum(1 for g in all_groups if g.get("beta") is not None)}
    doc.headline.append(("Grupos analisados", str(len(all_groups))))
    doc.headline.append(("Grupos com ajuste de Weibull", str(doc.summary["fitted"])))
    if risk and risk[0].get("p_fail") is not None:
        r0 = risk[0]
        doc.headline.append(("Maior risco em 30 dias", f"{r0.get('machine_name')} / {r0.get('position_name')}: "
                                                       f"{fmt.pct_plain(r0['p_fail'])}"))
    doc.definitions = DEF_WEIBULL
    return doc


def _ci(lo, hi, dec) -> Optional[str]:
    if lo is None or hi is None:
        return None
    return f"{fmt.number(lo, dec)} – {fmt.number(hi, dec)}"


BUILDERS = {"daily_production": build_daily_production, "shift": build_shift, "weekly_oee": build_weekly_oee,
            "monthly_maintenance": build_monthly_maintenance, "reliability_life": build_reliability_life}
