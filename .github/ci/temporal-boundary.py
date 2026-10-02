"""Serve each installed engine's real HTTP validators over recording ports.

The 418 sentinel proves the request passed the actual release's validators,
without confusing boundary acceptance with older query implementation bugs.
No timestamp validation is implemented by this fixture.
"""

from datetime import datetime
from typing import cast
from uuid import UUID

from fastapi import HTTPException
import uvicorn

from rememberstack.surfaces.http_api import build_api
from rememberstack.surfaces.http_api import GraphQueryPort
from rememberstack.surfaces.query_engine import QueryEngine

_DEPLOYMENT = UUID("74000000-0000-0000-0000-000000000001")
_CALLS: list[dict[str, str | None]] = []


def record(*, method: str, arguments: dict[str, object]) -> None:
    """Record the parsed clocks and refuse with a unique post-validation sentinel."""
    clocks: dict[str, str | None] = {"method": method}
    for name in ("valid_at", "believed_at"):
        value = arguments.get(name)
        assert value is None or isinstance(value, datetime)
        clocks[name] = value.isoformat() if value is not None else None
    _CALLS.append(clocks)
    raise HTTPException(status_code=418, detail="temporal-boundary-reached")


class Boundary:
    """Open only the fixture's admission and startup gates."""

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        """Permit startup without database access."""
        del deployment_id
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        """Permit the boundary probe without database access."""
        del deployment_id


class Engine:
    """Record relation lookup only; all other engine calls are unexpected."""

    def lookup_relations(self, **kwargs: object) -> None:
        """Record the lookup's actual parsed query arguments."""
        record(method="lookupRelations", arguments=kwargs)


class Graph:
    """Record graph calls after the real request models validate both clocks."""

    def neighborhood(self, **kwargs: object) -> None:
        """Record neighborhood's actual parsed body arguments."""
        record(method="graphNeighborhood", arguments=kwargs)

    def path(self, **kwargs: object) -> None:
        """Record path's actual parsed body arguments."""
        record(method="graphPath", arguments=kwargs)


boundary = Boundary()
app = build_api(
    engine=cast(QueryEngine, Engine()),
    deployment_id=_DEPLOYMENT,
    admission=boundary,
    readiness=boundary,
    graph=cast(GraphQueryPort, Graph()),
)


@app.get("/fixture/recordings")
def recordings() -> list[dict[str, str | None]]:
    """Expose parsed arguments, never keys or other request headers."""
    return _CALLS


if __name__ == "__main__":
    uvicorn.run(app=app, host="127.0.0.1", port=8001)
