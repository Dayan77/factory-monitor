"""MQTT ingestion pipeline (Sprint 5b): bounded per-tag ordered queues, fixed worker pool, batched Influx writes
with retry, in-order acknowledgement to the broker, dead letters and late-data watermarks."""
