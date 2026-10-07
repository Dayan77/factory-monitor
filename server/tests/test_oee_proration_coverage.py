"""QA sprint 6: OEE 196–262 % on the map of a newly onboarded tenant. An hour bucket whose parts were all made
in the part of the hour with data (first readings at 07:28) was spread uniformly over the whole hour, so a window
ending at 07:35 got 35/60 of the hour's parts while its APT only had the 7 covered minutes → P 2.06. Parts are now
spread over the covered part of the hour only (kpis.md C20); an hour without any span keeps the old rule."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.services.oee import kpi
from src.services.oee.calendar import Calendar, ShiftDef
from src.services.oee.engine import MachineData, MachineInfo, ProductPri, components

UTC = timezone.utc
TZ = ZoneInfo("UTC")


def T(hm: str) -> datetime:
    h, m = hm.split(":")
    return datetime(2026, 10, 7, int(h), int(m), tzinfo=UTC)


def _md(spans, parts=38.0) -> MachineData:
    info = MachineInfo("m", "t", "M", "l", "p", TZ, "running", 120, 40.0, {})
    cal = Calendar(TZ, [ShiftDef(None, "24 h", "00:00", "00:00", tuple(range(7)))], default=True)
    return MachineData(info, cal, events=[], spans=spans, micro=[],
                       production={T("07:00"): {"total": parts, "good": parts, "scrap": 0.0}}, runs=[],
                       pri=ProductPri(fallback_pri=40.0))


def test_partly_covered_hour_is_spread_over_its_covered_part_only():
    md = _md([(T("07:28"), T("09:00"))])
    now = T("09:00")
    c = components(md, T("02:00"), T("07:35"), now)
    r = kpi.oee(c)
    assert abs(c.produced - 38 * 7 / 32) < 1e-6                    # 7 of the 32 covered minutes
    assert r["performance"] <= 1.0 and r["oee"] <= 1.0 and not r["ideal_cycle_time_suspect"]
    assert c.counts_prorated is True
    # adjacent windows still add up to the hour (no double count, nothing lost)
    rest = components(md, T("07:35"), T("08:00"), now)
    assert abs(c.produced + rest.produced - 38) < 1e-6
    # whole hour: exact
    assert components(md, T("07:00"), T("08:00"), now).produced == 38


def test_hour_without_spans_keeps_the_elapsed_time_rule():
    md = _md([])
    c = components(md, T("07:00"), T("07:30"), T("09:00"))
    assert abs(c.produced - 19) < 1e-6


def test_current_hour_is_spread_over_covered_time_until_now():
    md = _md([(T("07:28"), T("07:44"))], parts=20)
    c = components(md, T("07:00"), T("07:36"), T("07:44"))
    assert abs(c.produced - 20 * 8 / 16) < 1e-6
