"""IA-2 — number and citation verifier.

Every numeric value of the answer (KPI, count, duration, date, time, % of life, hours) must come from a tool result
of THIS turn and its line must carry a citation marker `[n]` that points at an existing citation. Numbers are read
in pt-BR format (`71,4 %`, `1.234,5`, `05/10`, `14:30`) and normalised against the results: ratios ↔ percent,
seconds ↔ minutes ↔ hours, ISO datetimes ↔ local dates/times of the plant, rounded to the decimals written.

Exceptions: numbers present in the user's question, list markers, citation markers, numbers inside URLs/links and
inside identifiers (`A1`, `EQP-01`, `OS-0042`).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
URL = re.compile(r"(https?://\S+|/[a-z][\w\-/]*\?\S*|/[a-z][\w\-]*(?:/[\w\-{}]+)+)")
CITE = re.compile(r"\[(\d{1,2})\]")
LIST_MARK = re.compile(r"^(\s*)(\d{1,2})[.)]\s", re.M)
DATE = re.compile(r"(?<![\w/.,])(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?![\w/])")
TIME = re.compile(r"(?<![\w:.,])(\d{1,2}):(\d{2})(?![\w:])")
NUM = re.compile(r"(?<![\w\-/.,])[−-]?(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?(?![\w])")
ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
ISO_D = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DIGITS_IN_STR = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass
class Sources:
    values: set[float] = field(default_factory=set)
    dates: set[tuple[int, int, int]] = field(default_factory=set)
    times: set[tuple[int, int]] = field(default_factory=set)

    def add_value(self, v: float) -> None:
        if v is None or isinstance(v, bool) or not math.isfinite(v):
            return
        for x in (v, v * 100, v / 60, v / 3600, v * 60, v / 86400, v / 24):
            self.values.add(abs(x))

    def add_dt(self, d: datetime, tz: ZoneInfo) -> None:
        loc = d.astimezone(tz)
        self.dates.add((loc.day, loc.month, loc.year))
        self.times.add((loc.hour, loc.minute))

    def add_date(self, d: date) -> None:
        self.dates.add((d.day, d.month, d.year))

    def add_text(self, s: str, tz: ZoneInfo) -> None:
        if ISO_DT.match(s):
            try:
                self.add_dt(datetime.fromisoformat(s.replace("Z", "+00:00")), tz)
                return
            except ValueError:
                pass
        if ISO_D.match(s):
            try:
                self.add_date(date.fromisoformat(s))
                return
            except ValueError:
                pass
        for m in DATE.finditer(s):
            self.dates.add((int(m.group(1)), int(m.group(2)), _year(m.group(3))))
        for m in TIME.finditer(s):
            self.times.add((int(m.group(1)), int(m.group(2))))
        for m in DIGITS_IN_STR.finditer(s):
            try:
                self.values.add(abs(float(m.group(0).replace(",", "."))))
            except ValueError:
                pass


def _year(s: Optional[str]) -> int:
    if not s:
        return 0
    y = int(s)
    return y + 2000 if y < 100 else y


def collect(obj: Any, tz: ZoneInfo, out: Optional[Sources] = None) -> Sources:
    out = out or Sources()
    if isinstance(obj, dict):
        for v in obj.values():
            collect(v, tz, out)
    elif isinstance(obj, list):
        out.add_value(float(len(obj)))
        for v in obj:
            collect(v, tz, out)
    elif isinstance(obj, bool) or obj is None:
        pass
    elif isinstance(obj, (int, float)):
        out.add_value(float(obj))
    elif isinstance(obj, str):
        out.add_text(obj, tz)
    elif isinstance(obj, datetime):
        out.add_dt(obj, tz)
    elif isinstance(obj, date):
        out.add_date(obj)
    return out


@dataclass
class Finding:
    raw: str
    line: str
    reason: str          # ungrounded | uncited | bad_citation


def _strip(text: str) -> str:
    text = LINK.sub(lambda m: m.group(1), text)        # link labels stay, URLs go
    text = URL.sub(" ", text)
    return text


def _num_ok(raw_int: str, raw_dec: Optional[str], src: Sources) -> bool:
    x = float(raw_int.replace(".", "") + ("." + raw_dec if raw_dec else ""))
    d = len(raw_dec) if raw_dec else 0
    tol = 0.5 * 10 ** (-d) + 1e-9
    return any(abs(v - x) <= tol for v in src.values)


def check(reply: str, src: Sources, question: str = "", n_citations: int = 0) -> list[Finding]:
    """Findings that make the answer unverified (empty list = verified)."""
    q = _strip(question or "")
    q_nums = {m.group(0).lstrip("−-") for m in NUM.finditer(q)}
    q_dates = {(int(m.group(1)), int(m.group(2))) for m in DATE.finditer(q)}
    out: list[Finding] = []
    for m in CITE.finditer(reply):
        if not 1 <= int(m.group(1)) <= n_citations:
            out.append(Finding(m.group(0), m.group(0), "bad_citation"))
    for line in reply.splitlines():
        body = _strip(line)
        cited = bool(CITE.search(body))
        body = CITE.sub(" ", body)
        body = LIST_MARK.sub(r"\1 ", body)
        nums_here = False
        for m in DATE.finditer(body):
            dd, mm, yy = int(m.group(1)), int(m.group(2)), _year(m.group(3))
            nums_here = True
            if (dd, mm) in q_dates:
                continue
            if not any(s[0] == dd and s[1] == mm and (yy == 0 or s[2] in (0, yy)) for s in src.dates):
                out.append(Finding(m.group(0), line, "ungrounded"))
        body = DATE.sub(" ", body)
        for m in TIME.finditer(body):
            nums_here = True
            if (int(m.group(1)), int(m.group(2))) not in src.times and m.group(0) not in q:
                out.append(Finding(m.group(0), line, "ungrounded"))
        body = TIME.sub(" ", body)
        for m in NUM.finditer(body):
            raw = m.group(0).lstrip("−-")
            nums_here = True
            if raw in q_nums:
                continue
            if not _num_ok(m.group(1), m.group(2), src):
                out.append(Finding(raw, line, "ungrounded"))
        if nums_here and not cited and not _only_question_numbers(body, q_nums):
            out.append(Finding(line.strip()[:80], line, "uncited"))
    return out


def _only_question_numbers(body: str, q_nums: set[str]) -> bool:
    found = [m.group(0).lstrip("−-") for m in NUM.finditer(body)]
    return bool(found) and all(f in q_nums for f in found) and not DATE.search(body) and not TIME.search(body)


def has_numbers(text: str) -> bool:
    body = CITE.sub(" ", _strip(text))
    body = LIST_MARK.sub(r"\1 ", body)
    return bool(NUM.search(body) or DATE.search(body) or TIME.search(body))


def describe(findings: Iterable[Finding]) -> str:
    parts = []
    for f in findings:
        if f.reason == "ungrounded":
            parts.append(f"'{f.raw}' não aparece em nenhum resultado de ferramenta deste turno")
        elif f.reason == "uncited":
            parts.append(f"linha com número sem citação [n]: '{f.raw}'")
        else:
            parts.append(f"citação inexistente {f.raw}")
    return "; ".join(dict.fromkeys(parts))
