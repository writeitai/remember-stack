"""D140 HTTP boundary: ingest's version key and period, and the period routes.

What the routes forward, what they refuse before reaching the engine, how the
engine's typed refusals map to status codes, and that a read or ingest
credential cannot change a document's effective time. The writes themselves
are proven against PostgreSQL in `tests/spine/test_d140_effective_time.py`.
"""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
from uuid import UUID
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from remember.models import DeclaredEffectivePeriod
from remember.models import EffectivePeriodInput
from remember.models import EffectivePeriodsSet
from remember.models import EffectiveTimeCleared
from rememberstack.model import AuthenticatedContext
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import DocumentUpload
from rememberstack.model import DocumentVersionNotFoundError
from rememberstack.model import EffectivePeriodConflictError
from rememberstack.model import EffectiveTimeNotSupportedError
from rememberstack.model import IngestedVersion
from rememberstack.model import IngestPrincipal
from rememberstack.model import PerimeterCredential
from rememberstack.model import VersionKeyConflictError
from rememberstack.model.auth import PerimeterScope
from rememberstack.surfaces.http_api import _spend_gated_route
from rememberstack.surfaces.http_api import build_api
from rememberstack.surfaces.route_scope import required_scope

_DEPLOYMENT_ID = UUID("14000000-0000-0000-0000-000000000001")
_DOC = UUID("14000000-0000-0000-0000-00000000000d")
_VERSION = UUID("14000000-0000-0000-0000-00000000000e")
_OTHER = UUID("14000000-0000-0000-0000-00000000000f")
_JAN = datetime(2026, 1, 1, tzinfo=UTC)
_JUL = datetime(2026, 7, 1, tzinfo=UTC)
_PERIODS_PATH = f"/documents/{_DOC}/versions/{_VERSION}/effective-periods"
_CLEAR_PATH = f"/documents/{_DOC}/effective-periods"


class _Boundary:
    """Admission and readiness that accept the served deployment."""

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        """Accept the configured deployment."""
        assert deployment_id == _DEPLOYMENT_ID
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        """Admission is open."""
        assert deployment_id == _DEPLOYMENT_ID


class _UnusedEngine:
    """Query-engine placeholder; these routes never call it."""


class _Ingest:
    """Record what reached E0; optionally refuse like the catalog would."""

    def __init__(self, *, refusal: Exception | None = None) -> None:
        """Start with no uploads."""
        self.observed: list[DocumentUpload] = []
        self.anonymous: list[DocumentUpload] = []
        self._refusal = refusal

    def ingest(
        self,
        *,
        deployment_id: UUID,
        upload: DocumentUpload,
        ingested_by: IngestPrincipal | None = None,
    ) -> IngestedVersion:
        """Record a one-shot upload."""
        _ = ingested_by
        self.anonymous.append(upload)
        return _receipt(deployment_id=deployment_id, version_key=None)

    def ingest_observed(
        self,
        *,
        deployment_id: UUID,
        source_kind: str,
        source_ref: str,
        upload: DocumentUpload,
        versioning_mode: str,
        source_modified_at: datetime | None,
        source_version_ref: str | None,
        sync_cycle_id: UUID | None,
        ingested_by: IngestPrincipal | None = None,
    ) -> IngestedVersion:
        """Record an observation of a lineage."""
        _ = (
            source_kind,
            source_ref,
            versioning_mode,
            source_modified_at,
            source_version_ref,
            sync_cycle_id,
            ingested_by,
        )
        self.observed.append(upload)
        if self._refusal is not None:
            raise self._refusal
        return _receipt(deployment_id=deployment_id, version_key=upload.version_key)


def _receipt(*, deployment_id: UUID, version_key: str | None) -> IngestedVersion:
    """A receipt for a newly created snapshot version."""
    return IngestedVersion(
        deployment_id=deployment_id,
        doc_id=_DOC,
        version_id=_VERSION,
        content_hash="h",
        created=True,
        mime="text/markdown",
        title="t",
        versioning_mode="snapshot",
        version_key=version_key,
    )


class _EffectiveTime:
    """Record period writes; answer, or refuse with a configured error."""

    def __init__(self, *, refusal: Exception | None = None) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[str, UUID, UUID, object]] = []
        self._refusal = refusal

    def set_effective_periods(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        version_id: UUID,
        periods: tuple[EffectivePeriodInput, ...],
    ) -> EffectivePeriodsSet:
        """Record the replacement set and echo it back as declared."""
        self.calls.append(("set", deployment_id, doc_id, (version_id, periods)))
        if self._refusal is not None:
            raise self._refusal
        return EffectivePeriodsSet(
            doc_id=doc_id,
            version_id=version_id,
            periods=tuple(
                DeclaredEffectivePeriod(
                    period_id=uuid4(),
                    effective_from=period.effective_from,
                    effective_until=period.effective_until,
                    declared_at=_JUL,
                )
                for period in periods
            ),
            declared=len(periods),
            retracted=0,
        )

    def clear_effective_time(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> EffectiveTimeCleared:
        """Record the clear and report one retracted declaration."""
        self.calls.append(("clear", deployment_id, doc_id, None))
        if self._refusal is not None:
            raise self._refusal
        return EffectiveTimeCleared(doc_id=doc_id, retracted=1, cleared_at=_JUL)


class _ScopedAuth:
    """`read`, `ingest` and `write` bearer values map to those scopes."""

    def authenticate(self, *, credential: PerimeterCredential) -> AuthenticatedContext:
        """Return a context carrying the scope the credential names."""
        value = credential.value.get_secret_value().decode()
        return AuthenticatedContext(
            deployment_id=_DEPLOYMENT_ID, principal="agent", scope=PerimeterScope(value)
        )


def _client(
    *,
    ingest: _Ingest | None = None,
    effective_time: _EffectiveTime | None = None,
    auth: _ScopedAuth | None = None,
) -> TestClient:
    """An API exposing only the composed ingest and period routes."""
    boundary = _Boundary()
    return TestClient(
        build_api(
            engine=_UnusedEngine(),  # type: ignore[arg-type]
            deployment_id=_DEPLOYMENT_ID,
            admission=boundary,
            readiness=boundary,
            ingest=ingest,
            effective_time=effective_time,
            auth=auth,
        )
    )


def _ingest_params(**extra: str) -> dict[str, str]:
    """Query parameters for one lineage observation."""
    return {
        "filename": "policy.md",
        "mime": "text/markdown",
        "source_kind": "intranet",
        "source_ref": "policy/travel",
        **extra,
    }


def test_ingest_forwards_the_version_key_and_period() -> None:
    """The three D140 parameters reach E0 on the upload and the key comes back."""
    ingest = _Ingest()
    response = _client(ingest=ingest).post(
        "/ingest",
        params=_ingest_params(
            version_key="edition-2",
            effective_from="2026-01-01T00:00:00Z",
            effective_until="2026-07-01T00:00:00Z",
        ),
        content=b"# Travel",
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 200, response.text
    upload = ingest.observed[0]
    assert (upload.version_key, upload.effective_from, upload.effective_until) == (
        "edition-2",
        _JAN,
        _JUL,
    )
    assert response.json()["version_key"] == "edition-2"


@pytest.mark.parametrize(
    "extra", [{"version_key": "edition-1"}, {"effective_from": "2026-01-01T00:00:00Z"}]
)
def test_version_keys_and_periods_require_a_lineage(extra: dict[str, str]) -> None:
    """A one-shot upload's lineage is its bytes; neither field means anything."""
    ingest = _Ingest()
    params = {"filename": "policy.md", "mime": "text/markdown", **extra}
    response = _client(ingest=ingest).post(
        "/ingest", params=params, content=b"# Travel"
    )

    assert response.status_code == 422
    assert "source_kind/source_ref" in response.text
    assert ingest.anonymous == [] and ingest.observed == []


@pytest.mark.parametrize(
    ("extra", "fragment"),
    [
        (
            {"effective_from": "2026-01-01T00:00:00Z", "versioning_mode": "living"},
            "effective_time_requires_snapshot",
        ),
        ({"effective_until": "2026-07-01T00:00:00Z"}, "earlier effective_from"),
        (
            {
                "effective_from": "2026-07-01T00:00:00Z",
                "effective_until": "2026-07-01T00:00:00Z",
            },
            "earlier effective_from",
        ),
        ({"effective_from": "2026-01-01T00:00:00"}, "timezone-aware UTC"),
        ({"effective_from": "2026-01-01T00:00:00+02:00"}, "timezone-aware UTC"),
    ],
)
def test_malformed_periods_are_refused_before_e0(
    extra: dict[str, str], fragment: str
) -> None:
    """Living mode, an end without an earlier start, and non-UTC instants are 422."""
    ingest = _Ingest()
    response = _client(ingest=ingest).post(
        "/ingest", params=_ingest_params(**extra), content=b"# Travel"
    )

    assert response.status_code == 422
    assert fragment in response.text
    assert ingest.observed == []


@pytest.mark.parametrize(
    ("refusal", "status", "code"),
    [
        (
            VersionKeyConflictError(version_key="edition-1", version_id=_OTHER),
            409,
            "version_key_conflict",
        ),
        (
            EffectivePeriodConflictError(effective_from=_JAN, version_id=_OTHER),
            409,
            "effective_period_conflict",
        ),
        (
            EffectiveTimeNotSupportedError("living"),
            422,
            "effective_time_requires_snapshot",
        ),
    ],
)
def test_ingest_maps_the_catalogs_refusals(
    refusal: Exception, status: int, code: str
) -> None:
    """A conflict names the version that owns the key or the start."""
    response = _client(ingest=_Ingest(refusal=refusal)).post(
        "/ingest",
        params=_ingest_params(
            version_key="edition-1", effective_from="2026-01-01T00:00:00Z"
        ),
        content=b"# Travel",
    )

    assert response.status_code == status
    detail = response.json()["detail"]
    assert detail["code"] == code
    if status == 409:
        assert detail["version_id"] == str(_OTHER)


def test_set_effective_periods_forwards_the_complete_set() -> None:
    """The version's set is forwarded in the served deployment, in order."""
    effective_time = _EffectiveTime()
    response = _client(effective_time=effective_time).put(
        _PERIODS_PATH,
        json={
            "periods": [
                {"effective_from": "2026-01-01T00:00:00Z"},
                {
                    "effective_from": "2027-01-01T00:00:00Z",
                    "effective_until": "2027-06-30T00:00:00Z",
                },
            ]
        },
    )

    assert response.status_code == 200, response.text
    kind, deployment_id, doc_id, (version_id, periods) = effective_time.calls[0]  # type: ignore[misc]
    assert (kind, deployment_id, doc_id, version_id) == (
        "set",
        _DEPLOYMENT_ID,
        _DOC,
        _VERSION,
    )
    assert [period.effective_from for period in periods] == [
        _JAN,
        datetime(2027, 1, 1, tzinfo=UTC),
    ]
    assert response.json()["declared"] == 2


def test_an_empty_set_is_a_valid_replacement() -> None:
    """Leaving a version with no in-force period is an ordinary request."""
    effective_time = _EffectiveTime()
    response = _client(effective_time=effective_time).put(
        _PERIODS_PATH, json={"periods": []}
    )

    assert response.status_code == 200, response.text
    assert response.json()["periods"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"periods": [{"effective_from": "2026-01-01T00:00:00Z"}] * 2},
        {
            "periods": [
                {
                    "effective_from": "2026-07-01T00:00:00Z",
                    "effective_until": "2026-01-01T00:00:00Z",
                }
            ]
        },
        {"periods": [{"effective_from": "2026-01-01T00:00:00"}]},
        {"periods": [{"from": "2026-01-01T00:00:00Z"}]},
    ],
)
def test_malformed_period_sets_never_reach_the_catalog(body: dict[str, object]) -> None:
    """Duplicate starts, reversed ends, naive instants and unknown fields are 422."""
    effective_time = _EffectiveTime()
    response = _client(effective_time=effective_time).put(_PERIODS_PATH, json=body)

    assert response.status_code == 422
    assert effective_time.calls == []


@pytest.mark.parametrize(
    ("refusal", "status", "detail"),
    [
        (DocumentNotFoundError("absent"), 404, "document_not_found"),
        (DocumentVersionNotFoundError("absent"), 404, "version_not_found"),
        (
            EffectivePeriodConflictError(effective_from=_JAN, version_id=_OTHER),
            409,
            "effective_period_conflict",
        ),
        (
            EffectiveTimeNotSupportedError("living"),
            422,
            "effective_time_requires_snapshot",
        ),
    ],
)
def test_period_writes_map_the_catalogs_refusals(
    refusal: Exception, status: int, detail: str
) -> None:
    """Each typed refusal keeps its meaning on the wire."""
    client = _client(effective_time=_EffectiveTime(refusal=refusal))
    response = client.put(
        _PERIODS_PATH, json={"periods": [{"effective_from": "2026-01-01T00:00:00Z"}]}
    )

    assert response.status_code == status
    assert detail in response.text


def test_clear_effective_time_acts_on_the_served_deployment() -> None:
    """Leaving effective time names the document, never the deployment."""
    effective_time = _EffectiveTime()
    response = _client(effective_time=effective_time).delete(_CLEAR_PATH)

    assert response.status_code == 200, response.text
    assert effective_time.calls == [("clear", _DEPLOYMENT_ID, _DOC, None)]
    assert response.json() == {
        "doc_id": str(_DOC),
        "retracted": 1,
        "cleared_at": "2026-07-01T00:00:00Z",
    }


def test_clearing_an_absent_document_is_404() -> None:
    """An unknown and a deleted document are both absent."""
    client = _client(effective_time=_EffectiveTime(refusal=DocumentNotFoundError("x")))
    response = client.delete(_CLEAR_PATH)

    assert response.status_code == 404
    assert response.json()["detail"] == "document_not_found"


def test_the_routes_exist_only_when_composed() -> None:
    """Without the port there is no period surface to call."""
    client = _client()

    assert client.delete(_CLEAR_PATH).status_code in {404, 405}
    assert client.put(_PERIODS_PATH, json={"periods": []}).status_code in {404, 405}


@pytest.mark.parametrize("scope", ["read", "ingest"])
def test_only_full_write_authority_changes_effective_time(scope: str) -> None:
    """A read or ingest credential is refused before the catalog is reached."""
    effective_time = _EffectiveTime()
    client = _client(effective_time=effective_time, auth=_ScopedAuth())
    headers = {"Authorization": f"Bearer {scope}"}

    assert client.delete(_CLEAR_PATH, headers=headers).status_code == 403
    assert (
        client.put(_PERIODS_PATH, json={"periods": []}, headers=headers).status_code
        == 403
    )
    assert effective_time.calls == []
    assert (
        client.delete(
            _CLEAR_PATH, headers={"Authorization": "Bearer write"}
        ).status_code
        == 200
    )


def test_period_routes_are_writes_and_hold_no_spend_lease() -> None:
    """Management of declared metadata, like deletion: write scope, no lease."""
    for method, path in (("PUT", _PERIODS_PATH), ("DELETE", _CLEAR_PATH)):
        assert required_scope(method=method, path=path) == PerimeterScope.WRITE
        assert _spend_gated_route(method=method, path=path) is None
