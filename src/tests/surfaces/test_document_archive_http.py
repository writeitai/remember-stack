"""`POST /documents/{doc_id}/archive` and `/unarchive` at the HTTP boundary.

The cascades themselves are proven against real PostgreSQL in
`tests/workers/test_lifecycle_reconciliation.py`. This pins the boundary: the
deployment the routes act on, the 404 an absent document gets, that a read or
ingest credential cannot archive, and that the public delete is gone (D159).
"""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from rememberstack.model import AuthenticatedContext
from rememberstack.model import DocumentArchive
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import DocumentUnarchive
from rememberstack.model import PerimeterCredential
from rememberstack.model.auth import PerimeterScope
from rememberstack.surfaces.http_api import _spend_gated_route
from rememberstack.surfaces.http_api import build_api

_DEPLOYMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
_DOC = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
_AT = datetime(2026, 9, 23, tzinfo=UTC)
_VERBS = ("archive", "unarchive")


class _UnusedEngine:
    """Query-engine placeholder; the archive routes never call it."""


class _Archive:
    """Record calls; answer, or report the document absent."""

    def __init__(self, *, absent: bool = False) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[str, UUID, UUID]] = []
        self._absent = absent

    def _record(self, *, verb: str, deployment_id: UUID, doc_id: UUID) -> None:
        self.calls.append((verb, deployment_id, doc_id))
        if self._absent:
            raise DocumentNotFoundError(str(doc_id))

    def archive_document(self, *, deployment_id: UUID, doc_id: UUID) -> DocumentArchive:
        """Record the call and return a fixed outcome."""
        self._record(verb="archive", deployment_id=deployment_id, doc_id=doc_id)
        return DocumentArchive(
            doc_id=doc_id,
            archived_at=_AT,
            claims_retired=3,
            relations_closed=1,
            observations_closed=0,
        )

    def unarchive_document(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> DocumentUnarchive:
        """Record the call and return a fixed outcome."""
        self._record(verb="unarchive", deployment_id=deployment_id, doc_id=doc_id)
        return DocumentUnarchive(doc_id=doc_id, claims_restored=3, facts_reopened=1)


class _ScopedAuth:
    """`read`, `ingest` and `write` bearer values map to those scopes."""

    def authenticate(self, *, credential: PerimeterCredential) -> AuthenticatedContext:
        """Return a context carrying the scope the credential names."""
        value = credential.value.get_secret_value().decode()
        return AuthenticatedContext(
            deployment_id=_DEPLOYMENT_ID, principal="agent", scope=PerimeterScope(value)
        )


def _client(archive: _Archive | None, *, auth: _ScopedAuth | None = None) -> TestClient:
    """Build an API exposing only the archive routes (when composed)."""
    return TestClient(
        build_api(
            engine=_UnusedEngine(),  # type: ignore[arg-type]
            deployment_id=_DEPLOYMENT_ID,
            archive=archive,
            auth=auth,
        )
    )


def test_archive_acts_in_the_deployment_it_serves() -> None:
    """The caller names the document, never the deployment."""
    archive = _Archive()
    response = _client(archive).post(f"/documents/{_DOC}/archive")

    assert response.status_code == 200, response.text
    assert archive.calls == [("archive", _DEPLOYMENT_ID, _DOC)]
    assert response.json() == {
        "doc_id": str(_DOC),
        "archived_at": "2026-09-23T00:00:00Z",
        "claims_retired": 3,
        "relations_closed": 1,
        "observations_closed": 0,
    }


def test_unarchive_acts_in_the_deployment_it_serves() -> None:
    """Unarchive answers what it restored."""
    archive = _Archive()
    response = _client(archive).post(f"/documents/{_DOC}/unarchive")

    assert response.status_code == 200, response.text
    assert archive.calls == [("unarchive", _DEPLOYMENT_ID, _DOC)]
    assert response.json() == {
        "doc_id": str(_DOC),
        "claims_restored": 3,
        "facts_reopened": 1,
    }


@pytest.mark.parametrize("verb", _VERBS)
def test_an_absent_document_is_a_stable_404(verb: str) -> None:
    """An unknown or forgotten document: the code, not prose."""
    response = _client(_Archive(absent=True)).post(f"/documents/{_DOC}/{verb}")

    assert response.status_code == 404
    assert response.json() == {"detail": "document_not_found"}


@pytest.mark.parametrize("verb", _VERBS)
def test_a_malformed_id_never_reaches_the_spine(verb: str) -> None:
    """422 at the boundary; nothing is looked up for a string that is no id."""
    archive = _Archive()
    response = _client(archive).post(f"/documents/not-a-uuid/{verb}")

    assert response.status_code == 422
    assert archive.calls == []


@pytest.mark.parametrize("verb", _VERBS)
def test_the_routes_do_not_exist_without_the_port(verb: str) -> None:
    """Absent services do not pretend to exist."""
    response = _client(None).post(f"/documents/{_DOC}/{verb}")

    assert response.status_code in {404, 405}


def test_the_public_delete_is_gone() -> None:
    """D159 removed `DELETE /documents/{doc_id}` outright."""
    archive = _Archive()
    response = _client(archive).delete(f"/documents/{_DOC}")

    assert response.status_code in {404, 405}
    assert archive.calls == []


@pytest.mark.parametrize("verb", _VERBS)
def test_only_a_write_credential_may_archive(verb: str) -> None:
    """A read or narrow ingest credential is refused before the port runs."""
    archive = _Archive()
    client = _client(archive, auth=_ScopedAuth())

    for scope in ("read", "ingest"):
        refused = client.post(
            f"/documents/{_DOC}/{verb}", headers={"Authorization": f"Bearer {scope}"}
        )
        assert refused.status_code == 403, scope
    assert archive.calls == []

    allowed = client.post(
        f"/documents/{_DOC}/{verb}", headers={"Authorization": "Bearer write"}
    )
    assert allowed.status_code == 200, allowed.text
    assert archive.calls == [(verb, _DEPLOYMENT_ID, _DOC)]


@pytest.mark.parametrize("verb", _VERBS)
def test_archive_is_not_a_chargeable_path(verb: str) -> None:
    """No spend lease is reserved: archive and unarchive start no pipeline work.

    The only provider call either can make is re-embedding touched entity
    profiles, which is recorded on the surface cost ledger instead.
    """
    assert _spend_gated_route(method="POST", path=f"/documents/{_DOC}/{verb}") is None
