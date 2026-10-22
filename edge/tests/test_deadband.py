import math

import pytest

from edge.src.filters.deadband import DeadbandFilter


def test_first_reading_always_published():
    f = DeadbandFilter(1.0)
    assert f.should_publish("t", 100.0) is True


def test_small_change_suppressed_large_change_published():
    f = DeadbandFilter(1.0)  # 1 %
    f.should_publish("t", 100.0)
    assert f.should_publish("t", 100.5) is False
    assert f.should_publish("t", 100.99) is False
    assert f.should_publish("t", 101.0) is True


def test_reference_is_last_published_value_not_last_sample():
    """Slow drift must eventually publish (compares with last *published*, not last sample)."""
    f = DeadbandFilter(1.0)
    f.should_publish("t", 100.0)
    published = [f.should_publish("t", 100.0 + 0.3 * i) for i in range(1, 5)]
    # 100.3, 100.6, 100.9 suppressed; 101.2 ≥ 1 % from 100.0 → published
    assert published == [False, False, False, True]


def test_negative_values_and_decrease():
    f = DeadbandFilter(10.0)
    f.should_publish("t", -50.0)
    assert f.should_publish("t", -52.0) is False
    assert f.should_publish("t", -56.0) is True
    assert f.should_publish("t", -50.0) is True  # back down 10.7 %


def test_from_zero_any_change_published():
    f = DeadbandFilter(5.0)
    f.should_publish("t", 0.0)
    assert f.should_publish("t", 0.0) is False
    assert f.should_publish("t", 0.001) is True


def test_tags_are_independent():
    f = DeadbandFilter(1.0)
    f.should_publish("a", 100.0)
    assert f.should_publish("b", 100.2) is True
    assert f.should_publish("a", 100.2) is False


def test_bypass_publishes_every_change_but_not_repeats():
    """Digital signals/counters: no % threshold, but an unchanged value isn't re-sent."""
    f = DeadbandFilter(50.0)
    f.should_publish("cnt", 1000.0)
    assert f.should_publish("cnt", 1001.0, bypass=True) is True    # +0.1 % still sent
    assert f.should_publish("cnt", 1001.0, bypass=True) is False
    f.should_publish("di", 0.0, bypass=True)
    assert f.should_publish("di", 1.0, bypass=True) is True
    assert f.should_publish("di", 0.0, bypass=True) is True


def test_nan_never_published():
    f = DeadbandFilter(1.0)
    assert f.should_publish("t", math.nan) is False
    assert f.should_publish("t", 5.0) is True


def test_zero_deadband_publishes_any_change():
    f = DeadbandFilter(0.0)
    f.should_publish("t", 10.0)
    assert f.should_publish("t", 10.0000001) is True


def test_negative_deadband_rejected():
    with pytest.raises(ValueError):
        DeadbandFilter(-1)


def test_reset():
    f = DeadbandFilter(10.0)
    f.should_publish("t", 100.0)
    f.reset("t")
    assert f.should_publish("t", 100.1) is True


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_max_silence_republishes_stable_analog_value():
    """Stable value inside the deadband must still be sent periodically (server offline timeout)."""
    clk = _Clock()
    f = DeadbandFilter(0.5, max_silence_s=30.0, clock=clk)
    assert f.should_publish("temp", 70.0) is True
    clk.t += 10
    assert f.should_publish("temp", 70.2) is False      # 0.29 % < 0.5 %
    clk.t += 19.9
    assert f.should_publish("temp", 69.9) is False
    clk.t += 0.1                                         # 30 s since last publish
    assert f.should_publish("temp", 70.1) is True
    clk.t += 1
    assert f.should_publish("temp", 70.1) is False      # heartbeat timer restarted


def test_max_silence_applies_to_bypass_tags():
    """Counters/digital tags that don't move must also heartbeat."""
    clk = _Clock()
    f = DeadbandFilter(0.5, max_silence_s=30.0, clock=clk)
    assert f.should_publish("cnt", 42.0, bypass=True) is True
    clk.t += 29
    assert f.should_publish("cnt", 42.0, bypass=True) is False
    clk.t += 1
    assert f.should_publish("cnt", 42.0, bypass=True) is True


def test_real_change_restarts_silence_timer():
    clk = _Clock()
    f = DeadbandFilter(1.0, max_silence_s=30.0, clock=clk)
    f.should_publish("t", 100.0)
    clk.t += 20
    assert f.should_publish("t", 105.0) is True
    clk.t += 20                                          # 40 s since first, 20 s since last
    assert f.should_publish("t", 105.0) is False


def test_max_silence_default_below_server_offline_timeout():
    from edge.src.config import EdgeSettings
    assert DeadbandFilter()._max_silence is not None
    assert EdgeSettings().deadband_max_silence_s < 60  # server MACHINE_OFFLINE_TIMEOUT_S default


def test_max_silence_can_be_disabled_and_validated():
    clk = _Clock()
    f = DeadbandFilter(1.0, max_silence_s=None, clock=clk)
    f.should_publish("t", 100.0)
    clk.t += 10_000
    assert f.should_publish("t", 100.0) is False
    with pytest.raises(ValueError):
        DeadbandFilter(1.0, max_silence_s=0)
