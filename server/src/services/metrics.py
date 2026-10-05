"""Prometheus metrics of the backend pipeline (contract: docs/operations.md §8.2).

Label values are created up front so every series exists from the first scrape (a counter that never
moved still reads 0 — the "no MQTT messages" alert can tell 'nothing arrived' from 'not instrumented').
"""
from prometheus_client import Counter, Histogram

MQTT_RESULTS = ("ok", "rejected", "invalid", "error")
MQTT_MESSAGES = Counter("fm_mqtt_messages", "MQTT readings handled, by result", ["result"])
for _r in MQTT_RESULTS:
    MQTT_MESSAGES.labels(_r)

# end of processing − source ts of the reading (live readings; edge-backlog replays are excluded)
MQTT_LAG = Histogram("fm_mqtt_processing_lag_seconds", "Delay from the reading's source timestamp to the end "
                     "of its processing (live readings)",
                     buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 900))

DOWNTIME_KINDS = ("stop", "micro_stop")
DOWNTIME_EVENTS = Counter("fm_downtime_events", "Stops found by the downtime detector, by kind", ["kind"])
for _k in DOWNTIME_KINDS:
    DOWNTIME_EVENTS.labels(_k)
