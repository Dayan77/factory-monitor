"""Format-independent report document (rendered to PDF by pdf.py and to XLSX by xlsx.py).

Values in tables are the **raw** API values (fractions, seconds, counts); presentation (percent with 1 decimal,
minutes, "—" + reason) is applied by the renderers, so PDF, XLSX and the JSON preview carry the same numbers.

Row conventions (dict keys starting with "_"):
  _reason: {col_key: reason}   undefined value → "—" with the reason (never 0)
  _notes:  [note_code, …]      numbered footnotes on the row (ideal_cycle_time_suspect, product_unknown, …)
  _flag:   {col_key: "green"|"amber"}  reference marks (monthly maintenance)
  _coverage: fraction          data_coverage < 0.95 → "dados incompletos (cobertura X %)" on the row
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional

COVERAGE_MIN = 0.95

NOTE_TEXT = {
    "ideal_cycle_time_suspect": "desempenho > 100 %: tempo de ciclo ideal suspeito (S3-2)",
    "product_unknown": "produto desconhecido em parte do período (S3-3)",
    "counts_prorated": "contagem horária rateada na borda do período (D7)",
    "calendar_default": "planta sem turnos cadastrados: calendário implícito de 24 h (D6)",
    "estimated": "tempo de operação estimado (sem run_state)",
    "low_confidence": "amostra pequena: com poucas falhas o β estimado tende a sair alto",
    "insufficient_failures": "menos de 3 falhas: sem ajuste de Weibull",
    "outside_schedule": "produção fora do calendário — cadastrar turno extra? (S3-7)",
    "backlog_snapshot": "backlog é a fotografia no momento da geração (S4-7)",
    "as_good_as_new": "componente reparado e reinstalado conta como unidade nova (hipótese 'tão bom quanto novo')",
    "predictive_failures": "inclui remoções por defeito confirmado em OS preditiva (vida levemente conservadora)",
}


def coverage_note(cov: Optional[float]) -> Optional[str]:
    if cov is None or cov >= COVERAGE_MIN:
        return None
    from .fmt import pct_plain
    return f"dados incompletos (cobertura {pct_plain(cov)})"


@dataclass
class Col:
    key: str
    label: str
    kind: str = "text"        # text | pct | pp | num0 | num1 | num2 | num6 | int | min | hours | datetime | date | flag
    unit: str = ""
    xlsx_label: Optional[str] = None    # raw column name in the data sheet (e.g. duracao_min)


@dataclass
class Table:
    title: str
    columns: list[Col]
    rows: list[dict]
    sheet: Optional[str] = None          # XLSX data sheet (None = only on the PDF / summary)
    empty_text: str = "Nenhum registro no período."
    note: Optional[str] = None


@dataclass
class Para:
    text: str
    style: str = "p"                     # p | warn | small | ok


@dataclass
class Chart:
    kind: str                            # line | bars_ci | weibull
    title: str
    data: dict = field(default_factory=dict)


@dataclass
class Section:
    title: str
    blocks: list = field(default_factory=list)
    page_break: bool = False
    banners: list[str] = field(default_factory=list)


@dataclass
class Doc:
    report_type: str
    title: str
    scope_label: str
    period_label: str
    period_text: str
    tz: str
    landscape: bool = False
    generated_at: Optional[datetime] = None
    data_until: Optional[datetime] = None
    version: int = 1
    supersedes_text: Optional[str] = None
    link: Optional[str] = None
    banners: list[str] = field(default_factory=list)
    summary_table: Optional[Table] = None   # "Resumo" sheet = first page of the PDF
    sections: list[Section] = field(default_factory=list)
    definitions: list[tuple[str, str, str]] = field(default_factory=list)   # (KPI, formula, reference)
    readme: list[str] = field(default_factory=list)
    headline: list[tuple[str, str]] = field(default_factory=list)          # e-mail body: (label, text)
    summary: dict = field(default_factory=dict)                             # raw headline numbers
    sources: list[dict] = field(default_factory=list)                       # API calls the numbers came from
    omitted: list[str] = field(default_factory=list)                        # sections omitted by permission

    def tables(self) -> list[Table]:
        out = [self.summary_table] if self.summary_table else []
        for s in self.sections:
            out += [b for b in s.blocks if isinstance(b, Table)]
        return out

    def to_json(self) -> dict:
        def conv(v: Any):
            if isinstance(v, datetime):
                return v.isoformat()
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items()}
            if isinstance(v, (list, tuple)):
                return [conv(x) for x in v]
            return v
        d = asdict(self)
        for s, sec in zip(d["sections"], self.sections):
            s["blocks"] = [{"type": type(b).__name__.lower(), **conv(asdict(b))} for b in sec.blocks]
        return conv(d)
