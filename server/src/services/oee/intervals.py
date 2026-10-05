"""Interval arithmetic on half-open [start, end) datetime intervals (sorted, merged lists)."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

Interval = tuple[datetime, datetime]


def normalize(items: Iterable[Interval]) -> list[Interval]:
    """Sorted, non-empty, merged (touching intervals are merged)."""
    xs = sorted((a, b) for a, b in items if b > a)
    out: list[Interval] = []
    for a, b in xs:
        if out and a <= out[-1][1]:
            if b > out[-1][1]:
                out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def union(*lists: Iterable[Interval]) -> list[Interval]:
    allx: list[Interval] = []
    for xs in lists:
        allx.extend(xs)
    return normalize(allx)


def intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    """Both inputs normalized."""
    out: list[Interval] = []
    i = j = 0
    while i < len(a) and j < len(b):
        s = max(a[i][0], b[j][0])
        e = min(a[i][1], b[j][1])
        if e > s:
            out.append((s, e))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def subtract(a: list[Interval], b: list[Interval]) -> list[Interval]:
    """a − b, both normalized."""
    out: list[Interval] = []
    j = 0
    for s, e in a:
        cur = s
        while j < len(b) and b[j][1] <= cur:
            j += 1
        k = j
        while k < len(b) and b[k][0] < e:
            if b[k][0] > cur:
                out.append((cur, b[k][0]))
            cur = max(cur, b[k][1])
            if cur >= e:
                break
            k += 1
        if cur < e:
            out.append((cur, e))
    return out


def clip(a: list[Interval], start: datetime, end: datetime) -> list[Interval]:
    return intersect(a, [(start, end)]) if end > start else []


def total_s(a: Iterable[Interval]) -> float:
    return sum((e - s).total_seconds() for s, e in a)


def overlap_s(a: list[Interval], start: datetime, end: datetime) -> float:
    return total_s(clip(a, start, end))
