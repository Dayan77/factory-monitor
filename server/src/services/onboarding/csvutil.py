"""CSV helpers of the onboarding imports: real CSV reader (quotes, doubled quotes, Excel BOM), delimiter
detected from the header (`;` — Excel pt-BR — or `,`), accent/case-insensitive names."""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from typing import Iterator, Optional


class CsvError(ValueError):
    pass


def norm(s: Optional[str]) -> str:
    """Accent/case/space-insensitive key for matching names ("Estação de Fixação" == "estacao de fixacao")."""
    s = unicodedata.normalize("NFKD", (s or "").strip()).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s)


def decode(data) -> str:
    if isinstance(data, str):
        return data.lstrip("﻿")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CsvError("O arquivo deve estar em UTF-8 (no Excel: Salvar como → CSV UTF-8).")


def rows(text: str, required: tuple[str, ...]) -> tuple[str, list[str], Iterator[tuple[int, dict]]]:
    """(delimiter, header, iterator of (line number, {column: value})). Header names are normalised."""
    text = decode(text)
    first = text.split("\n", 1)[0]
    if not first.strip():
        raise CsvError("Arquivo vazio ou sem cabeçalho.")
    delim = ";" if first.count(";") >= first.count(",") and ";" in first else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    header = [norm(h).replace(" ", "_") for h in next(reader)]
    missing = [c for c in required if c not in header]
    if missing:
        raise CsvError(f"Colunas obrigatórias ausentes no cabeçalho: {', '.join(missing)}.")

    def it():
        for values in reader:
            if not any((v or "").strip() for v in values):
                continue
            row = {h: (values[i].strip() if i < len(values) else "") for i, h in enumerate(header)}
            if len(values) > len(header):
                row["__extra__"] = values[len(header):]
            yield reader.line_num, row
    return delim, header, it()


def parse_int(v: str) -> Optional[int]:
    v = (v or "").strip()
    if not v:
        return None
    return int(v)


def parse_float(v: str) -> Optional[float]:
    v = (v or "").strip()
    if not v:
        return None
    if "," in v and "." not in v:
        v = v.replace(",", ".")          # decimal comma (pt-BR)
    return float(v)
