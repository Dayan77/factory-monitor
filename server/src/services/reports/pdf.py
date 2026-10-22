"""PDF rendering with ReportLab (pure Python; docs/reports.md §3.1).

A4 portrait (landscape for weekly_oee and reliability_life); vector charts (reportlab.graphics); header with the
report name, scope and period in local time; footer on every page "gerado em … · dados até … · versão N · página
x/y" + link to the equivalent screen; warning banners at the top of the affected page; final page "Definições".
Undefined values are "—" with a numbered footnote carrying the reason; flags are numbered footnotes on the row.
"""
from __future__ import annotations

import io
import math
import os
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import reportlab
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, Line, Rect, String, Circle, PolyLine
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table as RLTable,
                                TableStyle)

from . import fmt
from .doc import NOTE_TEXT, Chart, Col, Doc, Para, Table, coverage_note

_FONT_DIR = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
FONT, FONT_B = "FCVera", "FCVeraBd"
_registered = False

INK = colors.HexColor("#1F2937")
MUTED = colors.HexColor("#6B7280")
BRAND = colors.HexColor("#1F3A5F")
GRID = colors.HexColor("#D1D5DB")
WARN_BG = colors.HexColor("#FFF4CE")
WARN_INK = colors.HexColor("#7A4B00")
OK_INK = colors.HexColor("#166534")
AMBER = colors.HexColor("#B45309")
GREEN = colors.HexColor("#15803D")
SERIES = [colors.HexColor(c) for c in ("#1F3A5F", "#2F855A", "#B7791F", "#9B2C2C", "#6B46C1")]
GREEK = set("βηΣΓδλΔ→")       # absent from Vera: drawn with the base-14 Symbol font (unicode → Symbol encoding)


def _fonts() -> None:
    global _registered
    if _registered:
        return
    pdfmetrics.registerFont(TTFont(FONT, os.path.join(_FONT_DIR, "Vera.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_B, os.path.join(_FONT_DIR, "VeraBd.ttf")))
    _registered = True


def rich(text: str) -> str:
    """Escape for Paragraph markup; Greek letters (absent from Vera) go through the Symbol font."""
    out = []
    for ch in escape(text or ""):
        if ch in GREEK:
            out.append(f'<font face="Symbol">{ch}</font>')
        else:
            out.append(ch)
    return "".join(out)


def _styles() -> dict:
    base = ParagraphStyle("p", fontName=FONT, fontSize=8.5, leading=11, textColor=INK)
    return {
        "p": base,
        "small": ParagraphStyle("small", parent=base, fontSize=7.5, leading=9.5, textColor=MUTED),
        "warn": ParagraphStyle("warn", parent=base, textColor=WARN_INK, backColor=WARN_BG, borderPadding=3),
        "ok": ParagraphStyle("ok", parent=base, textColor=OK_INK),
        "h1": ParagraphStyle("h1", parent=base, fontName=FONT_B, fontSize=15, leading=19, textColor=BRAND),
        "h2": ParagraphStyle("h2", parent=base, fontName=FONT_B, fontSize=11.5, leading=15, textColor=BRAND,
                             spaceBefore=4, spaceAfter=4),
        "h3": ParagraphStyle("h3", parent=base, fontName=FONT_B, fontSize=9, leading=12, spaceBefore=6, spaceAfter=2),
        "cell": ParagraphStyle("cell", parent=base, fontSize=7.5, leading=9),
        "head": ParagraphStyle("head", parent=base, fontName=FONT_B, fontSize=7.5, leading=9, textColor=colors.white),
        "banner": ParagraphStyle("banner", parent=base, fontName=FONT_B, textColor=WARN_INK, backColor=WARN_BG,
                                 borderPadding=4, spaceAfter=6),
    }


def fmt_cell(col: Col, value, tz: ZoneInfo) -> str:
    if value is None:
        return fmt.DASH
    k = col.kind
    if k == "pct":
        return fmt.pct(value)
    if k == "pp":
        return fmt.pp(value)
    if k == "int":
        return fmt.count(value)
    if k in ("num0", "num1", "num2", "num6"):
        return fmt.number(value, {"num0": 0, "num1": 1, "num2": 2, "num6": 6}[k]) + (f" {col.unit}" if col.unit else "")
    if k == "min":
        return fmt.minutes(value)
    if k == "hours":
        return fmt.hours(value, col.unit or "h")
    if k == "datetime":
        return fmt.local_dt(value, tz, "%d/%m %H:%M")
    if k == "date":
        s = str(value)[:10]
        return f"{s[8:10]}/{s[5:7]}/{s[0:4]}" if len(s) == 10 else s
    if isinstance(value, bool):
        return "sim" if value else "não"
    return str(value)


class _Notes:
    """Numbered footnotes of one table (reasons of '—' and row flags)."""

    def __init__(self):
        self.items: list[str] = []

    def mark(self, text: str) -> int:
        if text not in self.items:
            self.items.append(text)
        return self.items.index(text) + 1


def _widths(t: Table, tz: ZoneInfo, width: float) -> list[float]:
    """Natural width of every column (longest cell, longest header word); numbers never wrap: when the table is
    too wide, free-text columns shrink first (down to their longest header word), then everything scales."""
    pad = 8.0
    nat, minw, shrink = [], [], []
    for c in t.columns:
        head = max((pdfmetrics.stringWidth(w, FONT_B, 7.5) for w in c.label.split()), default=20) + pad
        cells = [pdfmetrics.stringWidth(fmt_cell(c, r.get(c.key), tz), FONT, 7.5) + pad + 6 for r in t.rows[:300]]
        n = max([head, 24.0, *cells])
        nat.append(n)
        minw.append(head if c.kind == "text" else n)
        shrink.append(c.kind == "text")
    total = sum(nat)
    if total <= width:
        return [w * width / total for w in nat]
    excess = total - width
    room = sum(n - m for n, m, s_ in zip(nat, minw, shrink) if s_)
    if room > 0:
        k = min(1.0, excess / room)
        nat = [n - (n - m) * k if s_ else n for n, m, s_ in zip(nat, minw, shrink)]
    total = sum(nat)
    return [w * width / total for w in nat]


def table_flow(t: Table, tz: ZoneInfo, st: dict, width: float) -> list:
    out = [Paragraph(rich(t.title), st["h3"])]
    if not t.rows:
        out.append(Paragraph(rich(t.empty_text), st["small"]))
        return out
    notes = _Notes()
    head = [Paragraph(rich(c.label), st["head"]) for c in t.columns]
    data = [head]
    styles = [("BACKGROUND", (0, 0), (-1, 0), BRAND), ("GRID", (0, 0), (-1, -1), 0.25, GRID),
              ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2),
              ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("LEFTPADDING", (0, 0), (-1, -1), 3),
              ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
    for i, row in enumerate(t.rows, start=1):
        cells = []
        reasons = row.get("_reason") or {}
        flags = row.get("_flag") or {}
        row_marks = [notes.mark(NOTE_TEXT.get(n, n)) for n in (row.get("_notes") or [])]
        cov = coverage_note(row.get("_coverage"))
        if cov:
            row_marks.append(notes.mark(cov))
            styles.append(("BACKGROUND", (0, i), (-1, i), WARN_BG))
        for j, c in enumerate(t.columns):
            txt = rich(fmt_cell(c, row.get(c.key), tz))
            if row.get(c.key) is None and reasons.get(c.key):
                txt += f"<super>{notes.mark(fmt.reason_pt(reasons[c.key]))}</super>"
            if j == 0 and row_marks:
                txt += "<super>" + ",".join(str(m) for m in sorted(set(row_marks))) + "</super>"
            fl = flags.get(c.key)
            if fl:
                # reference mark (never colour only): green = within the reference, amber = outside it
                txt = (f'<font color="#15803D"><b>{txt}</b></font>' if fl == "green"
                       else f'<font color="#B45309"><b>{txt} (!)</b></font>')
            cells.append(Paragraph(txt, st["cell"]))
        data.append(cells)
        if row.get("current"):
            styles.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#E8F0FB")))
    tbl = RLTable(data, colWidths=_widths(t, tz, width), repeatRows=1)
    tbl.setStyle(TableStyle(styles))
    out.append(tbl)
    if notes.items:
        out.append(Paragraph(" · ".join(f"<super>{k}</super> {rich(txt)}" for k, txt in enumerate(notes.items, 1)),
                             st["small"]))
    if t.note:
        out.append(Paragraph(rich(t.note), st["small"]))
    return out


# ── charts (vector) ────────────────────────────────────────────────────────

def line_chart(ch: Chart, width: float, height: float = 52 * mm) -> Drawing:
    d = Drawing(width, height)
    xs = ch.data.get("x", [])
    series = ch.data.get("series", {})
    lp = LinePlot()
    lp.x, lp.y, lp.width, lp.height = 30, 22, width - 130, height - 36
    data, names = [], []
    for name, vals in series.items():
        pts = [(i, v * 100) for i, v in enumerate(vals) if v is not None]
        if pts:
            data.append(pts)
            names.append(name)
    if not data:
        d.add(String(10, height / 2, "Sem dados para o gráfico.", fontName=FONT, fontSize=8, fillColor=MUTED))
        return d
    lp.data = data
    lp.xValueAxis.valueMin, lp.xValueAxis.valueMax = -0.5, max(len(xs) - 0.5, 0.5)
    lp.xValueAxis.valueSteps = list(range(len(xs)))
    labels = ch.data.get("x_labels") or [str(x) for x in xs]
    lp.xValueAxis.labelTextFormat = lambda v: (labels[int(v)] if 0 <= int(v) < len(labels) else "")
    lp.xValueAxis.labels.fontName = FONT
    lp.xValueAxis.labels.fontSize = 6
    lp.yValueAxis.valueMin, lp.yValueAxis.valueMax = 0, max(100, max(v for s in data for _, v in s) * 1.05)
    lp.yValueAxis.labels.fontName = FONT
    lp.yValueAxis.labels.fontSize = 6
    lp.yValueAxis.labelTextFormat = "%d %%"
    from reportlab.graphics.widgets.markers import makeMarker
    for i in range(len(data)):
        lp.lines[i].strokeColor = SERIES[i % len(SERIES)]
        lp.lines[i].strokeWidth = 1.6 if i == 0 else 1.0
        lp.lines[i].symbol = makeMarker("FilledCircle", size=3, fillColor=SERIES[i % len(SERIES)],
                                        strokeColor=SERIES[i % len(SERIES)])
    d.add(lp)
    hl = ch.data.get("highlight")
    if hl is not None and len(xs) > 1:
        x = lp.x + lp.width * (hl + 0.5) / (len(xs))
        d.add(Line(x, lp.y, x, lp.y + lp.height, strokeColor=GRID, strokeDashArray=[2, 2]))
    for k, (name, ref) in enumerate((ch.data.get("refs") or {}).items()):
        y = lp.y + lp.height * (ref * 100 - lp.yValueAxis.valueMin) / (lp.yValueAxis.valueMax - lp.yValueAxis.valueMin)
        d.add(Line(lp.x, y, lp.x + lp.width, y, strokeColor=AMBER, strokeWidth=0.6, strokeDashArray=[3, 2]))
        d.add(String(lp.x + lp.width + 4, y - 2, name, fontName=FONT, fontSize=6, fillColor=AMBER))
    for i, name in enumerate(names):
        y = height - 14 - i * 10
        d.add(Rect(width - 92, y, 8, 3, fillColor=SERIES[i % len(SERIES)], strokeColor=None))
        d.add(String(width - 80, y - 1, name, fontName=FONT, fontSize=7, fillColor=INK))
    return d


def weibull_chart(ch: Chart, width: float, height: float = 62 * mm) -> Drawing:
    """Weibull paper: x = ln t, y = ln(−ln(1 − F)); points = adjusted ranks; line = MLE (not a regression)."""
    d = Drawing(width, height)
    pts = [p for p in ch.data.get("points", []) if p.get("x") is not None and p.get("y") is not None]
    line = ch.data.get("line", [])
    xs = [p["x"] for p in pts] + [p["x"] for p in line]
    ys = [p["y"] for p in pts] + [p["y"] for p in line]
    if not xs:
        d.add(String(10, height / 2, "Sem pontos de falha.", fontName=FONT, fontSize=8, fillColor=MUTED))
        return d
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys + [math.log(-math.log(0.9))]), max(ys)
    padx = (x1 - x0) * 0.08 or 0.5
    pady = (y1 - y0) * 0.08 or 0.5
    x0, x1, y0, y1 = x0 - padx, x1 + padx, y0 - pady, y1 + pady
    L, B, W, H = 46, 24, width - 66, height - 36

    def X(v):
        return L + W * (v - x0) / (x1 - x0)

    def Y(v):
        return B + H * (v - y0) / (y1 - y0)
    d.add(Rect(L, B, W, H, strokeColor=GRID, fillColor=None, strokeWidth=0.5))
    for fv in (0.01, 0.05, 0.1, 0.3, 0.5, 0.632, 0.9, 0.99):
        yy = math.log(-math.log(1 - fv))
        if y0 <= yy <= y1:
            d.add(Line(L, Y(yy), L + W, Y(yy), strokeColor=GRID, strokeWidth=0.3))
            d.add(String(L - 4, Y(yy) - 2, f"{fv * 100:g}%", fontName=FONT, fontSize=5.5, fillColor=MUTED,
                         textAnchor="end"))
    for k in range(6):
        tick = x0 + (x1 - x0) * (k + 0.5) / 6
        d.add(Line(X(tick), B, X(tick), B + H, strokeColor=GRID, strokeWidth=0.3))
        d.add(String(X(tick), B - 9, fmt.number(math.exp(tick), 0), fontName=FONT, fontSize=5.5,
                     fillColor=MUTED, textAnchor="middle"))
    if len(line) >= 2:
        d.add(PolyLine([v for p in line for v in (X(p["x"]), Y(p["y"]))], strokeColor=SERIES[0], strokeWidth=1.2))
    for p in pts:
        d.add(Circle(X(p["x"]), Y(p["y"]), 2.2, fillColor=SERIES[3], strokeColor=None))
    unit = ch.data.get("unit") or ""
    d.add(String(L + W / 2, 4, f"idade ({unit}, escala log)", fontName=FONT, fontSize=6.5, fillColor=INK,
                 textAnchor="middle"))
    d.add(String(L, B + H + 4, "F(t) — probabilidade acumulada de falha (ranks de Johnson / Bernard); "
                 "reta = MLE", fontName=FONT, fontSize=6.5, fillColor=INK))
    return d


def bars_ci_chart(ch: Chart, width: float, height: float = 55 * mm) -> Drawing:
    d = Drawing(width, height)
    labels, vals = ch.data.get("labels", []), ch.data.get("values", [])
    lo, hi = ch.data.get("lo", []), ch.data.get("hi", [])
    if not vals:
        return d
    top = max([v for v in hi if v is not None] + vals) * 1.1
    L, B, W, H = 40, 30, width - 60, height - 44
    n = len(vals)
    bw = W / n * 0.5
    d.add(Line(L, B, L + W, B, strokeColor=INK, strokeWidth=0.5))
    for i, v in enumerate(vals):
        cx = L + W * (i + 0.5) / n
        h = H * v / top
        d.add(Rect(cx - bw / 2, B, bw, h, fillColor=SERIES[0], strokeColor=None))
        if lo[i] is not None and hi[i] is not None:
            y_lo, y_hi = B + H * lo[i] / top, B + H * hi[i] / top
            d.add(Line(cx, y_lo, cx, y_hi, strokeColor=SERIES[3], strokeWidth=1))
            d.add(Line(cx - 4, y_lo, cx + 4, y_lo, strokeColor=SERIES[3], strokeWidth=1))
            d.add(Line(cx - 4, y_hi, cx + 4, y_hi, strokeColor=SERIES[3], strokeWidth=1))
        d.add(String(cx, B + h + 3, fmt.number(v, 0), fontName=FONT, fontSize=6, fillColor=INK, textAnchor="middle"))
        d.add(String(cx, B - 10, labels[i][:28], fontName=FONT, fontSize=6, fillColor=INK, textAnchor="middle"))
    d.add(String(L, height - 8, f"B10 ({ch.data.get('unit') or ''}) com IC 90 %", fontName=FONT, fontSize=7,
                 fillColor=INK))
    return d


def chart_flow(ch: Chart, width: float, st: dict) -> list:
    fn = {"line": line_chart, "weibull": weibull_chart, "bars_ci": bars_ci_chart}.get(ch.kind)
    if fn is None:
        return []
    return [KeepTogether([Paragraph(rich(ch.title), st["h3"]), fn(ch, width)])]


# ── document ───────────────────────────────────────────────────────────────

def render(doc: Doc) -> bytes:
    _fonts()
    st = _styles()
    tz = ZoneInfo(doc.tz)
    pagesize = landscape(A4) if doc.landscape else A4
    buf = io.BytesIO()
    margin = 14 * mm
    width = pagesize[0] - 2 * margin
    footer = (f"gerado em {fmt.local_dt(doc.generated_at, tz)} · dados até {fmt.local_dt(doc.data_until, tz)} · "
              f"versão {doc.version}" + (f" — {doc.supersedes_text}" if doc.supersedes_text else ""))
    header = f"{doc.title} — {doc.scope_label}"

    def make_story() -> list:
        story: list = []
        story.append(Paragraph(rich(doc.title), st["h1"]))
        story.append(Paragraph(rich(f"{doc.scope_label} · {doc.period_text}"), st["p"]))
        story.append(Spacer(1, 4))
        for b in doc.banners:
            story.append(Paragraph(rich(b), st["banner"]))
        if doc.summary_table:
            story += table_flow(doc.summary_table, tz, st, width)
        for o in doc.omitted:
            story.append(Paragraph(rich(f"Seção omitida: {o}"), st["small"]))
        for sec in doc.sections:
            if sec.title == "Dados":
                continue                                  # raw data only in the XLSX
            if sec.page_break:
                story.append(PageBreak())
            story.append(Paragraph(rich(sec.title), st["h2"]))
            for b in sec.banners:
                story.append(Paragraph(rich(b), st["banner"]))
            for blk in sec.blocks:
                if isinstance(blk, Table):
                    story += table_flow(blk, tz, st, width)
                elif isinstance(blk, Para):
                    story.append(Paragraph(rich(blk.text), st.get(blk.style, st["p"])))
                elif isinstance(blk, Chart):
                    story += chart_flow(blk, width, st)
                story.append(Spacer(1, 3))
        story.append(PageBreak())
        story.append(Paragraph("Definições", st["h2"]))
        defs = [[Paragraph("Indicador", st["head"]), Paragraph("Fórmula", st["head"]), Paragraph("Referência", st["head"])]]
        for k, f, r in doc.definitions:
            defs.append([Paragraph(rich(k), st["cell"]), Paragraph(rich(f), st["cell"]), Paragraph(rich(r), st["cell"])])
        t = RLTable(defs, colWidths=[width * 0.22, width * 0.53, width * 0.25], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), BRAND), ("GRID", (0, 0), (-1, -1), 0.25, GRID),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(t)
        for line in doc.readme:
            if line:
                story.append(Paragraph(rich(line), st["small"]))
        story.append(Spacer(1, 6))
        story.append(Paragraph(rich("Indicador indefinido aparece como “—” com o motivo; nunca como 0. Frações em "
                                    "percentual com 1 casa (arredondamento meio-para-cima do valor bruto da API)."),
                               st["small"]))
        return story

    pages = {"n": 0}

    def on_page(canvas, d, final=False):
        canvas.saveState()
        canvas.setFont(FONT, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(margin, pagesize[1] - 9 * mm, header[:150])
        canvas.drawString(margin, 8 * mm, footer[:170])
        canvas.drawRightString(pagesize[0] - margin, 8 * mm, f"página {d.page}/{pages['n'] or '?'}")
        if doc.link:
            canvas.drawString(margin, 4.5 * mm, f"Ver no app: {doc.link}"[:170])
            canvas.linkURL(doc.link, (margin, 3.5 * mm, margin + 120 * mm, 7 * mm), relative=0)
        canvas.restoreState()

    def build(target):
        t = SimpleDocTemplate(target, pagesize=pagesize, leftMargin=margin, rightMargin=margin,
                              topMargin=14 * mm, bottomMargin=14 * mm, title=doc.title, author="FactoryConnect",
                              subject=f"{doc.scope_label} — {doc.period_label}", creator="FactoryConnect reports")
        t.build(make_story(), onFirstPage=on_page, onLaterPages=on_page)
        return t
    # two passes: the first counts the pages for "página x/y"
    first = io.BytesIO()
    t1 = build(first)
    pages["n"] = t1.page
    build(buf)
    return buf.getvalue()
