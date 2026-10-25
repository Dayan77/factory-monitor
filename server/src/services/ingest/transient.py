"""Transient failures (say nothing about the reading itself) and the shared retry-with-backoff helper."""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional, TypeVar

import structlog

log = structlog.get_logger()
T = TypeVar("T")

RETRY_FIRST_S = 0.5
RETRY_MAX_S = 15.0


def cancelling() -> bool:
    """True while the current task is being cancelled. Libraries sometimes turn the CancelledError delivered in
    their cleanup into their own error (e.g. asyncpg: "connection is closed"): never retry / swallow those."""
    t = asyncio.current_task()
    return bool(t is not None and t.cancelling())


def is_transient(e: BaseException) -> bool:
    """Failures that say nothing about the reading itself: retrying later can succeed."""
    if isinstance(e, (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError)):
        return True
    try:
        from sqlalchemy.exc import DBAPIError, TimeoutError as PoolTimeout
        if isinstance(e, PoolTimeout):
            return True
        if isinstance(e, DBAPIError):
            # integrity / data errors are about the row, not the connection
            from sqlalchemy.exc import DataError, IntegrityError, ProgrammingError
            return not isinstance(e, (IntegrityError, DataError, ProgrammingError)) or bool(e.connection_invalidated)
    except ImportError:  # pragma: no cover
        pass
    try:
        import aiohttp
        if isinstance(e, aiohttp.ClientResponseError):
            return e.status >= 500 or e.status == 429
        if isinstance(e, aiohttp.ClientError):
            return True
    except ImportError:  # pragma: no cover
        pass
    try:
        from influxdb_client.client.exceptions import InfluxDBError
        if isinstance(e, InfluxDBError):
            status = getattr(getattr(e, "response", None), "status", None)
            return status is None or status >= 500 or status == 429
    except ImportError:  # pragma: no cover
        pass
    try:
        from influxdb_client.rest import ApiException
        if isinstance(e, ApiException):
            return e.status is None or e.status >= 500 or e.status == 429
    except ImportError:  # pragma: no cover
        pass
    return False


async def retry_transient(what: str, fn: Callable[[], Awaitable[T]], budget_s: float, *,
                          first_s: Optional[float] = None, max_s: Optional[float] = None,
                          on_retry: Optional[Callable[[int, BaseException], None]] = None, **log_kw) -> T:
    """Calls fn(); on a transient failure retries with exponential backoff for up to `budget_s`, then re-raises.
    Non-transient errors are raised at once."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget_s
    delay = RETRY_FIRST_S if first_s is None else first_s
    cap = RETRY_MAX_S if max_s is None else max_s
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except Exception as e:  # noqa: BLE001
            if cancelling():
                raise asyncio.CancelledError() from e
            if not is_transient(e) or loop.time() + delay > deadline:
                raise
            if on_retry is not None:
                on_retry(attempt, e)
            log.warning(f"{what}.retry", attempt=attempt, error=str(e)[:200], error_type=type(e).__name__,
                        retry_in_s=delay, **log_kw)
            await asyncio.sleep(delay)
            delay = min(delay * 2, cap)
