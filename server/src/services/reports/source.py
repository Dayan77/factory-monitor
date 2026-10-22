"""Where every number of a report comes from (docs/reports.md §0.1, §0.5).

A report is a composition of responses of the existing API for the same scope and period — never a second KPI
implementation. `ApiSource` makes internal ASGI GET requests (no network) to the API routes with a short-lived JWT
of the **viewer** (the user who exports, or each recipient of a scheduled report), so the route's RBAC
(`require_any`), tenant filter and row visibility (operator: no WO hours) apply exactly as on screen. Every call
is recorded (path, params, status) so the test suite can replay it and compare the numbers.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from ...auth.security import create_access_token
from ...models import User


class SafeGetTransport(httpx.ASGITransport):
    def __init__(self, app, log: list):
        super().__init__(app=app)
        self.log = log

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            return httpx.Response(405, json={"detail": "relatórios só leem a API"})
        import time
        t0 = time.perf_counter()
        resp = await super().handle_async_request(request)
        self.log.append({"path": request.url.path, "query": str(request.url.query, "ascii") if isinstance(
            request.url.query, bytes) else str(request.url.query), "status": resp.status_code,
            "ms": round((time.perf_counter() - t0) * 1000, 1)})
        return resp


def iso(d: Optional[datetime]) -> Optional[str]:
    if d is None:
        return None
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class ApiSource:
    def __init__(self, viewer: User, app=None, token: Optional[str] = None):
        self.viewer = viewer
        self._app = app
        self.calls: list[dict] = []
        self.token = token or create_access_token({"sub": viewer.id, "tenant_id": viewer.tenant_id,
                                                   "role": viewer.role.value if hasattr(viewer.role, "value")
                                                   else str(viewer.role), "purpose": "report"})
        self._client: Optional[httpx.AsyncClient] = None
        # Sprint 7 (performance): responses already fetched (prefetch) by (path, params) — a report asks for
        # the same API response at most once, and independent ones are fetched concurrently
        self._memo: dict[tuple, Any] = {}

    @property
    def app(self):
        if self._app is None:
            from ...main import app
            self._app = app
        return self._app

    async def __aenter__(self) -> "ApiSource":
        self._client = httpx.AsyncClient(transport=SafeGetTransport(self.app, self.calls),
                                         base_url="http://reports.internal", timeout=300)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _clean(params: Optional[dict]) -> list[tuple[str, str]]:
        clean: list[tuple[str, str]] = []
        for k, v in (params or {}).items():
            if v is None:
                continue
            for x in (v if isinstance(v, (list, tuple)) else [v]):
                if isinstance(x, datetime):
                    x = iso(x)
                clean.append((k, str(x).lower() if isinstance(x, bool) else str(x)))
        return clean

    async def get(self, path: str, params: Optional[dict] = None) -> tuple[int, Any]:
        clean = self._clean(params)
        return await self._get_clean(path, clean, hand_out=True)

    async def _get_clean(self, path: str, clean: list[tuple[str, str]], hand_out: bool) -> tuple[int, Any]:
        key = (path, tuple(clean))
        hit = self._memo.get(key)
        if hit is None:
            fut = asyncio.get_running_loop().create_future()
            self._memo[key] = hit = [fut, False]
            try:
                res = await self._fetch(path, clean)
            except BaseException as e:
                self._memo.pop(key, None)
                fut.set_exception(e)
                fut.exception()                    # retrieved: no "never retrieved" warning
                raise
            fut.set_result(res)
        else:
            res = await asyncio.shield(hit[0])
        if not hand_out:
            return res
        if hit[1]:                                 # handed out before: the builder may have changed it
            import copy
            return res[0], copy.deepcopy(res[1])
        hit[1] = True
        return res

    async def prefetch(self, requests: list[tuple[str, Optional[dict]]], concurrency: int = 4) -> None:
        """Fetches independent API responses concurrently (at most `concurrency` at once); the builder's own
        get/get_ok calls with the same (path, params) then return them. Errors are left for that call to raise."""
        sem = asyncio.Semaphore(max(1, concurrency))

        async def one(path: str, params: Optional[dict]) -> None:
            async with sem:
                try:
                    await self._get_clean(path, self._clean(params), hand_out=False)
                except Exception:  # noqa: BLE001 — raised again (uncached) by the builder's own call
                    pass
        await asyncio.gather(*(one(p, q) for p, q in requests))

    def cached_status(self, path: str, params: Optional[dict] = None) -> Optional[int]:
        """HTTP status of a response already fetched (prefetch), None if not fetched (or still in flight)."""
        hit = self._memo.get((path, tuple(self._clean(params))))
        if hit is None or not hit[0].done() or hit[0].exception() is not None:
            return None
        return hit[0].result()[0]

    async def _fetch(self, path: str, clean: list[tuple[str, str]]) -> tuple[int, Any]:
        own = self._client is None
        client = self._client or httpx.AsyncClient(transport=SafeGetTransport(self.app, self.calls),
                                                   base_url="http://reports.internal", timeout=300)
        try:
            r = await client.get(path, params=clean, headers={"Authorization": f"Bearer {self.token}"})
        finally:
            if own:
                await client.aclose()
        try:
            body = r.json()
        except ValueError:
            body = None
        return r.status_code, body

    async def get_ok(self, path: str, params: Optional[dict] = None) -> Any:
        """200 → body; 403 → Forbidden (section omitted); anything else → ReportDataError."""
        st, body = await self.get(path, params)
        if st == 200:
            return body
        if st == 403:
            raise Forbidden(path)
        raise ReportDataError(f"GET {path} → {st}: {str(body)[:300]}")


class Forbidden(Exception):
    """The viewer may not see this data (the section is omitted, never filled with something else)."""


class ReportDataError(Exception):
    pass
