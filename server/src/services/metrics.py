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

# ── MQTT ingestion pipeline (Sprint 5b, services/ingest) ─────────────────────
from prometheus_client import Gauge  # noqa: E402

INGEST_QUEUE_DEPTH = Gauge("fm_ingest_queue_depth", "Readings accepted from the broker and not finished yet "
                           "(queued per tag + being processed + Influx write pending)")
INGEST_QUEUE_CAPACITY = Gauge("fm_ingest_queue_capacity", "Bound of fm_ingest_queue_depth (MQTT_MAX_INFLIGHT)")
INGEST_ACTIVE_TAGS = Gauge("fm_ingest_active_tags", "Tags with readings waiting in their ordered queue")
INGEST_UNACKED = Gauge("fm_ingest_unacked", "QoS 1 deliveries received and not acknowledged to the broker yet")
INGEST_LATENCY = Histogram("fm_ingest_latency_seconds", "Receive → reading finished (stored in Influx + usage), "
                           "every reading", buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 300))
INGEST_DEAD_LETTERS = Counter("fm_ingest_dead_letters", "Readings that could not be processed after retries "
                              "(stored in ingest_dead_letters), by stage", ["stage"])
INGEST_DROPPED = Counter("fm_ingest_dropped", "Readings lost (not processed and not even dead-lettered). Must be 0")
INGEST_RETRIES = Counter("fm_ingest_retries", "Transient failures retried while processing readings", ["stage"])
INFLUX_WRITE_BATCH = Histogram("fm_influx_write_batch_points", "Points per Influx write request",
                               buckets=(1, 10, 50, 100, 500, 1000, 2500, 5000))
INFLUX_WRITE_RETRIES = Counter("fm_influx_write_retries", "Influx write requests retried after a transient failure")
INGEST_LATE = Counter("fm_ingest_late_readings", "Readings written long after their source timestamp (gateway "
                      "buffer flush, backlog): the windows they touch are re-extracted")
for _s in ("lookup", "influx", "usage", "process"):
    INGEST_DEAD_LETTERS.labels(_s)
    INGEST_RETRIES.labels(_s)
INGEST_DROPPED.inc(0)
