"""Separate ASGI app for ``GET /ops/cost-export/v1``.

This module must not be imported by ``build_api``. The customer query app's
dependency list is perimeter + D74 admission and cannot host export.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hmac
import logging
import threading
import time
from typing import Callable
from uuid import UUID

from fastapi import FastAPI
from fastapi import Header
from fastapi import HTTPException
from fastapi import Query
from sqlalchemy.engine import Engine

from rememberstack.spine.cost_export import COST_EXPORT_DEFAULT_LIMIT
from rememberstack.spine.cost_export import COST_EXPORT_MAX_LIMIT
from rememberstack.spine.cost_export import CostExportConfigError
from rememberstack.spine.cost_export import CostExportCursorError
from rememberstack.spine.cost_export import CostExportPage
from rememberstack.spine.cost_export import CostExportReader
from rememberstack.spine.cost_export import CostExportSettings
from rememberstack.spine.cost_export import parse_cost_export_bind
from rememberstack.spine.cost_export import SqlCostExportReader

_logger = logging.getLogger(__name__)


class _TokenBucket:
    """In-process 1 req/s limiter. Surfaces cannot import the adapter bucket."""

    def __init__(
        self,
        *,
        rate_per_s: float = 1.0,
        capacity: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Start full; ``clock`` is injectable for deterministic tests."""
        self._rate_per_s = rate_per_s
        self._capacity = capacity
        self._clock = clock
        self._tokens = capacity
        self._refilled_at = clock()
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        """Take one token if available; never block."""
        with self._lock:
            now = self._clock()
            self._tokens = min(
                self._capacity,
                self._tokens + (now - self._refilled_at) * self._rate_per_s,
            )
            self._refilled_at = now
            if self._tokens < 1.0:
                return False
            self._tokens -= 1.0
            return True


def build_cost_export_app(
    *,
    reader: CostExportReader,
    deployment_id: UUID,
    token: str,
    bucket: _TokenBucket | None = None,
) -> FastAPI:
    """Build the export-only ASGI app. It never mounts customer query routes."""
    limiter = bucket if bucket is not None else _TokenBucket()
    app = FastAPI(
        title="RememberStack cost export",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.cost_export_bucket = limiter

    @app.get("/ops/cost-export/v1", response_model=CostExportPage)
    def cost_export_v1(
        authorization: str | None = Header(default=None),
        cursor: str | None = None,
        limit: int = Query(
            default=COST_EXPORT_DEFAULT_LIMIT, ge=1, le=COST_EXPORT_MAX_LIMIT
        ),
    ) -> CostExportPage:
        """Return one allowlisted page. Wrong token is 401 with no secret echo."""
        if not _bearer_matches(authorization=authorization, expected=token):
            raise HTTPException(status_code=401, detail="unauthorized")
        if not limiter.try_acquire():
            raise HTTPException(status_code=429, detail="rate limited")
        try:
            return reader.read_page(
                deployment_id=deployment_id, cursor=cursor, limit=limit
            )
        except CostExportCursorError as error:
            raise HTTPException(status_code=422, detail="malformed cursor") from error

    return app


@dataclass
class CostExportListener:
    """Daemon-thread uvicorn handle for the second bind."""

    bind: str
    thread: threading.Thread
    server: object

    def stop(self) -> None:
        """Ask the export server to exit and wait briefly for the thread."""
        server = self.server
        should_exit = getattr(server, "should_exit", None)
        if should_exit is not None:
            server.should_exit = True  # type: ignore[attr-defined]
        self.thread.join(timeout=5.0)


def attach_cost_export_listener(
    *,
    app: FastAPI,
    engine: Engine,
    deployment_id: UUID,
    settings: CostExportSettings | None = None,
) -> None:
    """Start the export listener from the customer app lifespan when bind is set.

    Constructing the customer app does not start export. A bind without a
    32-byte token, or a bind that fails, refuses to start the process.
    """
    resolved = (
        settings if settings is not None else CostExportSettings.model_validate({})
    )
    if resolved.http_bind() is None:
        return
    bind, token = resolved.require_http_credentials()
    reader = SqlCostExportReader(engine=engine)
    export_app = build_cost_export_app(
        reader=reader, deployment_id=deployment_id, token=token
    )
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(host: FastAPI) -> AsyncIterator[None]:
        """Start export after the customer app starts; stop it on shutdown."""
        listener = start_cost_export_listener(app=export_app, bind=bind)
        try:
            async with original(host):
                yield
        finally:
            listener.stop()

    app.router.lifespan_context = lifespan


def start_cost_export_listener(*, app: FastAPI, bind: str) -> CostExportListener:
    """Bind uvicorn to the export address in a daemon thread, or fail closed."""
    import uvicorn

    host, port, uds = parse_cost_export_bind(bind=bind)
    if uds is not None:
        config = uvicorn.Config(app, uds=uds, log_level="warning", access_log=False)
    elif host is not None and port is not None:
        config = uvicorn.Config(
            app, host=host, port=port, log_level="warning", access_log=False
        )
    else:
        raise CostExportConfigError("export bind did not parse to a listen address")
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run, name="rememberstack-cost-export", daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if server.started:
            return CostExportListener(bind=bind, thread=thread, server=server)
        if not thread.is_alive():
            raise CostExportConfigError(f"cost export failed to bind {bind}")
        time.sleep(0.02)
    server.should_exit = True
    thread.join(timeout=2.0)
    raise CostExportConfigError(f"cost export failed to become ready on {bind}")


def _bearer_matches(*, authorization: str | None, expected: str) -> bool:
    """Constant-time compare of the Bearer token; missing header is a miss."""
    if authorization is None:
        return False
    scheme, separator, value = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not value:
        return False
    return hmac.compare_digest(value.encode("utf-8"), expected.encode("utf-8"))
