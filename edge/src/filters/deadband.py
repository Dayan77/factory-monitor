"""
Deadband filter — suppresses readings that changed less than N% from last value.
Prevents flooding the broker with noise from analog sensors.
"""


class DeadbandFilter:
    def __init__(self, deadband_pct: float = 0.5) -> None:
        self._deadband = deadband_pct / 100.0
        self._last: dict[str, float] = {}

    def should_publish(self, tag_id: str, value: float) -> bool:
        last = self._last.get(tag_id)
        if last is None:
            self._last[tag_id] = value
            return True

        if last == 0:
            changed = abs(value) > 0
        else:
            changed = abs(value - last) / abs(last) >= self._deadband

        if changed:
            self._last[tag_id] = value
        return changed
