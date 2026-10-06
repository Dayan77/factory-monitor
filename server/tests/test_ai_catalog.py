"""The server's copy of the AI tool catalog equals docs/ai_assistant.md `ai-tools`; schemas sent to the model;
period words resolved with the plant calendar (IA-6)."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from ai_helpers import doc_block


def test_catalog_matches_spec():
    from src.services.ai.catalog import TOOLS
    assert TOOLS == doc_block("ai-tools")


def test_json_schemas_are_closed_and_read_only():
    from src.services.ai import tools
    for t in tools.TOOLS:
        sch = tools.json_schema(t)
        assert sch["additionalProperties"] is False and set(sch["required"]) <= set(sch["properties"])
        assert "tenant_id" not in sch["properties"]
        assert all(a["method"] == "GET" for a in t["api"])
        assert hasattr(tools.ToolExecutor, f"t_{t['name']}")


def test_periods_follow_the_plant_calendar():
    from src.services.ai import periods
    from src.services.oee.calendar import Calendar, ShiftDef
    tz = ZoneInfo("America/Manaus")
    cal = Calendar(tz, [ShiftDef("a", "Turno A", "06:00", "14:00", tuple(range(7)), ()),
                        ShiftDef("c", "Turno C", "22:00", "06:00", tuple(range(7)), ())])
    now = datetime(2026, 10, 7, 3, 0, tzinfo=timezone.utc)        # 06/10 23:00 local → inside Turno C of 06/10
    s, e = periods.resolve("today", cal, now)
    assert s == datetime(2026, 10, 6, 6, 0, tzinfo=tz) and e == now
    s, e = periods.resolve("yesterday", cal, now)
    assert s == datetime(2026, 10, 5, 6, 0, tzinfo=tz) and e == datetime(2026, 10, 6, 6, 0, tzinfo=tz)
    s, e = periods.resolve("last_week", cal, now)                 # 06/10/2026 is a Tuesday
    assert s == datetime(2026, 9, 28, 6, 0, tzinfo=tz) and e == datetime(2026, 10, 5, 6, 0, tzinfo=tz)
    s, e = periods.resolve("last_month", cal, now)
    assert s == datetime(2026, 9, 1, 6, 0, tzinfo=tz) and e == datetime(2026, 10, 1, 6, 0, tzinfo=tz)
    s, _ = periods.resolve("this_month", cal, now)
    assert s == datetime(2026, 10, 1, 6, 0, tzinfo=tz)
    s, e = periods.resolve("last_12_months", cal, now)
    assert s.astimezone(tz).date() == date(2025, 10, 6) and e.astimezone(tz).date() == date(2026, 10, 6)
    import pytest
    with pytest.raises(periods.PeriodError):
        periods.resolve("next_month", cal, now)
