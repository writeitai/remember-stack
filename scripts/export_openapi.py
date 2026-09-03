#!/usr/bin/env python3
"""Export the deployment query API's OpenAPI document without a server.

## Why this exists

The API a deployment serves is the contract every client codes against — the
CLI, the MCP surface, and any UI that lists documents or searches them. Until
now that contract was only discoverable by reading `http_api.py`, so consumers
restated it by hand and drifted from it silently. Publishing the schema makes
the contract machine-readable, so a client can be generated from it rather than
transcribed.

## Why the app's own schema route stays off

`build_api` sets `openapi_url=None`: the schema endpoint is not gated by the
auth perimeter, so serving it would publish the surface to unauthenticated
callers. That is a deliberate refusal, and this script does not undo it — it
builds the app in-process and asks FastAPI for the same document offline. No
server listens, no port opens, and nothing about the running deployment
changes.

## Which ports are supplied, and why not all of them

Routes mount conditionally: `documents=None` means no `GET /documents`,
`ingest=None` means no `POST /ingest`. Two opposite mistakes follow, and the
export has made both.

Supplying too few publishes a schema missing those routes while looking
perfectly valid, so a generated client simply lacks the endpoints and nothing
fails until someone calls one.

Supplying too many is worse, because it is not visible at all: the document
advertises routes that answer 404 on every deployment the shipped self-host
profile builds. `connectors` is exactly that case — the port exists and a
programmatic deployment could compose it, but the shipped profile never does,
so `/connectors` is not part of the API this schema describes.

So this composes the ports the self-host profile names, and no others.

That correspondence is not machine-checked, and the reason is worth stating
rather than leaving as an omission. Which capabilities a deployment actually
composes is decided at run time from its settings — `auth` resolves to `None`
for the open quickstart — so the profile names capabilities a given deployment
may not have, and no reading of source can tell the two apart. An AST check
that tried was defeated by six different ways of reaching the same function
before it was abandoned.

What guards this instead is `_SURFACE` in `test_openapi_export.py`: a frozen
route set that makes any change to the published surface a visible edit someone
has to justify. That is what would have caught the phantom `/connectors`
routes — a person reading the diff, not a machine.

The stubs never run. FastAPI reads route signatures and response models to
build the document; the port objects are only needed for the app to compose.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any
from uuid import UUID
from uuid import uuid4


class _Boundary:
    """Admission and readiness, both open, so the app composes.

    `build_api` calls `ensure_ready` while composing, so this one really does
    run. It asserts the deployment it was built for, which keeps it honest
    about being a boundary rather than a shrug.
    """

    def __init__(self, *, deployment_id: UUID) -> None:
        """Bind the deployment this boundary answers for."""
        self._deployment_id = deployment_id

    def assert_available(self, *, deployment_id: UUID) -> None:
        """Admit; D74 admission state is not part of the schema."""
        assert deployment_id == self._deployment_id

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        """Report a completed replay so `build_api` proceeds."""
        assert deployment_id == self._deployment_id
        return ()


class _Unused:
    """A port the export never calls.

    FastAPI reads route signatures and response models to build the document,
    so a port only has to exist for the app to compose. Any actual call means
    the export strayed from reading the surface into exercising it, and says so
    rather than silently answering None.

    `deployment_id` is real, because `build_api` compares it against the one it
    serves and refuses a mismatch: one deployment is one trust domain (D50).
    """

    def __init__(self, *, deployment_id: UUID | None = None) -> None:
        """Bind the deployment id `build_api` checks, when a port carries one."""
        self.deployment_id = deployment_id

    def __getattr__(self, name: str) -> Any:
        """Answer every attribute, so composition never fails on a missing one."""

        def _never(*_args: object, **_kwargs: object) -> Any:
            raise AssertionError(
                f"{name} was called during schema export; the export builds the "
                f"app and reads its routes, and must never invoke a port"
            )

        return _never


def build_document(*, deployment_id: UUID | None = None) -> dict[str, Any]:
    """The OpenAPI document for the complete deployment surface."""
    from rememberstack.surfaces.http_api import build_api

    served = deployment_id or uuid4()
    boundary = _Boundary(deployment_id=served)
    scoped = _Unused(deployment_id=served)
    app = build_api(
        engine=_Unused(),  # type: ignore[arg-type]
        deployment_id=served,
        admission=boundary,  # type: ignore[arg-type]
        readiness=boundary,  # type: ignore[arg-type]
        surface=scoped,  # type: ignore[arg-type]
        open_query=scoped,  # type: ignore[arg-type]
        # Supplied so the document declares the bearer scheme a guarded
        # deployment requires. Without it a generated client would be built
        # against a contract that never mentions credentials.
        auth=_Unused(),  # type: ignore[arg-type]
        spend_lease=_Unused(),  # type: ignore[arg-type]
        ingest=_Unused(),  # type: ignore[arg-type]
        pipeline_readiness=_Unused(),  # type: ignore[arg-type]
        documents=_Unused(),  # type: ignore[arg-type]
        graph=_Unused(),  # type: ignore[arg-type]
        build_info=_Unused(),  # type: ignore[arg-type]
    )
    return app.openapi()


def _routes(document: dict[str, Any]) -> set[tuple[str, str]]:
    """(METHOD, path) pairs the document declares."""
    found: set[tuple[str, str]] = set()
    for path, operations in document.get("paths", {}).items():
        for method in operations:
            if method.lower() in {"get", "post", "put", "patch", "delete"}:
                found.add((method.upper(), path))
    return found


def main(argv: list[str] | None = None) -> int:
    """Write the schema, refusing to write one missing a required route."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument(
        "--require-route",
        action="append",
        default=[],
        metavar="METHOD:/path",
        help=(
            "Fail unless the document declares this route. Guards against an "
            "export that silently drops a surface because a port went missing."
        ),
    )
    args = parser.parse_args(argv)

    document = build_document()
    declared = _routes(document)
    missing = []
    for requirement in args.require_route:
        method, _, path = requirement.partition(":")
        if (method.upper(), path) not in declared:
            missing.append(requirement)
    if missing:
        print(
            "refusing to write a schema missing required routes: "
            + ", ".join(sorted(missing)),
            file=sys.stderr,
        )
        print(f"declared: {sorted(declared)}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.output} with {len(declared)} routes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
