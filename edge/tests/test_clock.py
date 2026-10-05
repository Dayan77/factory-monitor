"""DeviceClock (edge/src/clock.py): skew estimate robust to delivery delay; initial notifications."""
from datetime import datetime, timedelta, timezone

from edge.src.clock import SKEW_WINDOW, DeviceClock

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


class Log:
    def __init__(self):
        self.events = []

    def error(self, ev, **kw):
        self.events.append(("error", ev, kw))

    def warning(self, ev, **kw):
        self.events.append(("warning", ev, kw))


def test_initial_notification_hours_old_is_not_skew():
    """QA: a value unchanged since plant start has an old SourceTimestamp; it must not demote the device."""
    log = Log()
    c = DeviceClock("plc", "source", 2.0, log=log)
    old = NOW - timedelta(hours=5)
    assert c.stamp(old, NOW, "run", initial=True) == old       # the value's real change time
    assert not c.demoted and c.offset_s is None and log.events == []
    live = NOW + timedelta(seconds=1) - timedelta(milliseconds=300)
    assert c.stamp(live, NOW + timedelta(seconds=1), "run") == live
    assert c.effective_policy == "source"


def test_initial_notification_in_the_future_is_skew():
    c = DeviceClock("plc", "source", 2.0, log=Log())
    c.stamp(NOW + timedelta(seconds=30), NOW, "run", initial=True)
    assert c.demoted


def test_gateway_policy_initial_uses_gateway_time():
    c = DeviceClock("plc", "gateway", 2.0)
    assert c.stamp(NOW - timedelta(hours=3), NOW, "run", initial=True) == NOW


def test_burst_of_queued_changes_after_a_blip_does_not_demote():
    """Queued notifications arrive 3–5 s after their SourceTimestamp: delivery delay, not a clock error —
    the estimate is the least delayed recent change."""
    log = Log()
    c = DeviceClock("plc", "source", 2.0, log=log)
    t = NOW
    c.stamp(t - timedelta(milliseconds=150), t, "run")                      # live, in bounds
    for k, delay in enumerate((5.0, 4.8, 4.6, 4.4, 4.2, 4.0, 3.8, 3.6, 3.4, 3.2, 3.0, 2.6)):
        rcv = t + timedelta(seconds=10 + k * 0.01)
        src = rcv - timedelta(seconds=delay)
        assert c.stamp(src, rcv, "run") == src                             # still the device clock
    assert not c.demoted and log.events == []


def test_clock_consistently_behind_is_demoted_once():
    log = Log()
    c = DeviceClock("plc", "source", 2.0, log=log)
    for k in range(SKEW_WINDOW - 1):
        rcv = NOW + timedelta(seconds=10 * k)
        assert c.stamp(rcv - timedelta(seconds=37.7), rcv, "run") == rcv - timedelta(seconds=37.7)
    assert not c.demoted                                   # 30 s of evidence needed, not one burst
    rcv = NOW + timedelta(seconds=40)
    out = c.stamp(rcv - timedelta(seconds=37.7), rcv, "run")
    assert c.demoted and out == rcv
    assert [e[1] for e in log.events] == ["edge_gateway.clock_untrusted"]
    assert log.events[0][2]["skew_s"] == -37.7
    # later readings: gateway time; no further errors
    rcv = NOW + timedelta(minutes=1)
    assert c.stamp(rcv - timedelta(seconds=0.1), rcv, "run") == rcv and len(log.events) == 1


def test_clock_ahead_is_demoted_immediately():
    c = DeviceClock("plc", "source", 2.0, log=Log())
    assert c.stamp(NOW + timedelta(seconds=5), NOW, "run") == NOW and c.demoted
