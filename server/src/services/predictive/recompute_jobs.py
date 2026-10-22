"""Background predictive recompute (POST /api/predictive/recompute?background=true — Sprint 5b QA).

A full re-extraction (`reextract_all`) of a 70-block F1 monitor reads every block back from Influx: 94 s on a quiet
host, 352 s on a loaded one — longer than nginx's proxy_read_timeout (60 s) and any reasonable HTTP client timeout.
In background mode the request answers 202 at once with a job id; the job runs in its own database session and the
caller polls GET /api/predictive/recompute/jobs/{job_id} (same tenant only; another tenant's id answers 404).

One predictive recompute per tenant at a time in this process (sync or background): a second one answers 409 with
the running job id. Jobs live in memory (the result of a lost job is recomputed by running it again; recompute is
deterministic).
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import structlog

log = structlog.get_logger()


class RecomputeBusy(Exception):
    """A predictive recompute of this tenant is already running (str = its job id, or 'sync')."""


@dataclass
class PredictiveRecomputeJob:
    id: str
    tenant_id: str
    monitor_id: Optional[str]
    reextract_invalid: bool
    reextract_all: bool
    status: str = "running"            # running | done | failed
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    result: Optional[dict] = None
    error: Optional[str] = None

    def out(self) -> dict[str, Any]:
        return {"job_id": self.id, "status": self.status, "monitor_id": self.monitor_id,
                "reextract_invalid": self.reextract_invalid, "reextract_all": self.reextract_all,
                "started_at": self.started_at.isoformat(),
                "finished_at": self.finished_at.isoformat() if self.finished_at else None,
                "error": self.error, "result": self.result}


_jobs: dict[str, PredictiveRecomputeJob] = {}
_running: dict[str, str] = {}          # tenant_id -> job id ('sync' for a request running inline)
_tasks: set[asyncio.Task] = set()
_MAX_JOBS = 200


def get_job(job_id: str, tenant_id: str) -> Optional[PredictiveRecomputeJob]:
    job = _jobs.get(job_id)
    return job if job is not None and job.tenant_id == str(tenant_id) else None


class tenant_slot:
    """One predictive recompute per tenant at a time (in this process)."""

    def __init__(self, tenant_id: str, job_id: str = "sync") -> None:
        self.tenant_id, self.job_id = str(tenant_id), job_id

    def __enter__(self):
        if self.tenant_id in _running:
            raise RecomputeBusy(_running[self.tenant_id])
        _running[self.tenant_id] = self.job_id
        return self

    def __exit__(self, *exc):
        if _running.get(self.tenant_id) == self.job_id:
            _running.pop(self.tenant_id, None)
        return False


def start_job(tenant_id: str, monitor_id: Optional[str], reextract_invalid: bool, reextract_all: bool,
              run: Callable[[Any], Awaitable[dict]]) -> PredictiveRecomputeJob:
    """Schedules `run(db)` (returns the same body as the synchronous route) in its own session."""
    from ...db import AsyncSessionLocal

    tenant_id = str(tenant_id)
    job = PredictiveRecomputeJob(id=str(uuid.uuid4()), tenant_id=tenant_id, monitor_id=monitor_id,
                                 reextract_invalid=reextract_invalid, reextract_all=reextract_all)
    slot = tenant_slot(tenant_id, job.id)
    slot.__enter__()                   # raises RecomputeBusy before anything is scheduled
    if len(_jobs) >= _MAX_JOBS:        # forget the oldest finished jobs
        for jid in [j.id for j in sorted(_jobs.values(), key=lambda j: j.started_at)
                    if j.status != "running"][: len(_jobs) - _MAX_JOBS + 1]:
            _jobs.pop(jid, None)
    _jobs[job.id] = job

    async def _run() -> None:
        try:
            async with AsyncSessionLocal() as db:
                job.result = await run(db)
            job.status = "done"
        except Exception as e:  # noqa: BLE001
            job.status, job.error = "failed", f"{type(e).__name__}: {str(e)[:480]}"
            log.exception("predictive.recompute_failed", tenant_id=tenant_id[:8], job_id=job.id)
        finally:
            job.finished_at = datetime.now(timezone.utc)
            slot.__exit__(None, None, None)

    task = asyncio.create_task(_run(), name=f"predictive_recompute_{job.id[:8]}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job
