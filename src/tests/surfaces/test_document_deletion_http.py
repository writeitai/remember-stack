"""`DELETE /documents/{doc_id}`: what it forwards, what it refuses, and to whom.

The cascade itself is proven against real PostgreSQL in
`tests/workers/test_lifecycle_reconciliation.py`. This pins the boundary: the
deployment the route acts on, the 404 an absent document gets, that a read or
ingest credential cannot delete, and that D74 admission still closes it.
"""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
from uuid import UUID

from fastapi.testclient import TestClient

from rememberstack.model import AuthenticatedContext
from rememberstack.model import DocumentDeletion
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import ForgetInProgressError
from rememberstack.model import PerimeterCredential
from rememberstack.model.auth import PerimeterScope
from rememberstack.surfaces.http_api import _spend_gated_route
from rememberstack.surfaces.http_api import build_api

_DEPLOYMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
_DOC = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
_AT = datetime(2026, 9, 23, tzinfo=UTC)


class _Boundary:
    """Admission/readiness; admission can be closed to model a hard-forget."""

    def __init__(self, *, forgetting: bool = False) -> None:
        """Open unless a forget is in progress."""
        self._forgetting = forgetting

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        """Accept the configured deployment."""
        assert deployment_id == _DEPLOYMENT_ID
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        """Refuse while a forget is in progress."""
        assert deployment_id == _DEPLOYMENT_ID
        if self._forgetting:
            raise ForgetInProgressError("forget in progress")


class _UnusedEngine:
    """Query-engine placeholder; the delete route never calls it."""


class _Deletion:
    """Record calls; answer, or report the document absent."""

    def __init__(self, *, absent: bool = False) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[UUID, UUID]] = []
        self._absent = absent

    def delete_document(self, *, deployment_id: UUID, doc_id: UUID) -> DocumentDeletion:
        """Record the call and return a fixed outcome."""
        self.calls.append((deployment_id, doc_id))
        if self._absent:
            raise DocumentNotFoundError(str(doc_id))
        return DocumentDeletion(
            doc_id=doc_id,
            deleted_at=_AT,
            claims_retired=3,
            relations_closed=1,
            observations_closed=0,
        )


class _ScopedAuth:
    """`read`, `ingest` and `write` bearer values map to those scopes."""

    def authenticate(self, *, credential: PerimeterCredential) -> AuthenticatedContext:
        """Return a context carrying the scope the credential names."""
        value = credential.value.get_secret_value().decode()
        return AuthenticatedContext(
            deployment_id=_DEPLOYMENT_ID, principal="agent", scope=PerimeterScope(value)
        )


def _client(
    deletion: _Deletion | None,
    *,
    auth: _ScopedAuth | None = None,
    forgetting: bool = False,
) -> TestClient:
    """Build an API exposing only the delete route (when composed)."""
    boundary = _Boundary(forgetting=forgetting)
    return TestClient(
        build_api(
            engine=_UnusedEngine(),  # type: ignore[arg-type]
            deployment_id=_DEPLOYMENT_ID,
            admission=boundary,
            readiness=boundary,
            deletion=deletion,
            auth=auth,
        )
    )


def test_the_route_deletes_in_the_deployment_it_serves() -> None:
    """The caller names the document, never the deployment."""
    deletion = _Deletion()
    response = _client(deletion).delete(f"/documents/{_DOC}")

    assert response.status_code == 200, response.text
    assert deletion.calls == [(_DEPLOYMENT_ID, _DOC)]
    body = response.json()
    assert body == {
        "doc_id": str(_DOC),
        "deleted_at": "2026-09-23T00:00:00Z",
        "claims_retired": 3,
        "relations_closed": 1,
        "observations_closed": 0,
    }


def test_an_absent_document_is_a_stable_404() -> None:
    """Unknown and already deleted are the same answer: the code, not prose."""
    response = _client(_Deletion(absent=True)).delete(f"/documents/{_DOC}")

    assert response.status_code == 404
    assert response.json() == {"detail": "document_not_found"}


def test_a_malformed_id_never_reaches_the_spine() -> None:
    """422 at the boundary; nothing is looked up for a string that is no id."""
    deletion = _Deletion()
    response = _client(deletion).delete("/documents/not-a-uuid")

    assert response.status_code == 422
    assert deletion.calls == []


def test_the_route_does_not_exist_without_the_port() -> None:
    """Absent services do not pretend to exist."""
    response = _client(None).delete(f"/documents/{_DOC}")

    assert response.status_code in {404, 405}


def test_only_a_write_credential_may_delete() -> None:
    """A read or narrow ingest credential is refused before the port runs."""
    deletion = _Deletion()
    client = _client(deletion, auth=_ScopedAuth())

    for scope in ("read", "ingest"):
        refused = client.delete(
            f"/documents/{_DOC}", headers={"Authorization": f"Bearer {scope}"}
        )
        assert refused.status_code == 403, scope
    assert deletion.calls == []

    allowed = client.delete(
        f"/documents/{_DOC}", headers={"Authorization": "Bearer write"}
    )
    assert allowed.status_code == 200, allowed.text
    assert deletion.calls == [(_DEPLOYMENT_ID, _DOC)]


def test_a_hard_forget_in_progress_closes_deletion_too() -> None:
    """D74's barrier is deployment-wide: no route is exempt, deletion included."""
    deletion = _Deletion()
    response = _client(deletion, forgetting=True).delete(f"/documents/{_DOC}")

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "forget_in_progress"}}
    assert deletion.calls == []


def test_a_forget_that_wins_the_fence_is_a_503_not_a_500() -> None:
    """Admission was open, but a forget was preparing when the delete took
    the D74 fence: the same stable negative as the admission check."""

    class _Fenced(_Deletion):
        def delete_document(
            self, *, deployment_id: UUID, doc_id: UUID
        ) -> DocumentDeletion:
            raise ForgetInProgressError("forget preparing")

    response = _client(_Fenced()).delete(f"/documents/{_DOC}")

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "forget_in_progress"}}


def test_deletion_is_not_a_chargeable_path() -> None:
    """No spend lease is reserved: removing a document starts no pipeline work.

    The only provider call it can make is re-embedding touched entity
    profiles, which is recorded on the surface cost ledger instead.
    """
    assert _spend_gated_route(method="DELETE", path=f"/documents/{_DOC}") is None
