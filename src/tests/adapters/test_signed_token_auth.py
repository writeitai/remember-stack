"""The signed-key claim contract at the engine perimeter (D136 §7.2–§7.4).

Mostly refusals: a verifier that accepts good credentials is easy; the question
is whether it can be talked into accepting something it should not — a token
for another tenant or deployment, an OAuth token meant for a hosted MCP server,
a key that does not cover this project, or a permission it cannot bound. The
HTTP proofs pin what each scope reaches.
"""

from __future__ import annotations

from datetime import datetime
import time
from typing import Any
from uuid import UUID
from uuid import uuid4

from fastapi.testclient import TestClient
import jwt
import pytest

from rememberstack.adapters.managed.signed_token_auth import scope_for_permissions
from rememberstack.adapters.managed.signed_token_auth import SignedTokenUnusable
from rememberstack.adapters.managed.signed_token_auth import strip_credential_prefix
from rememberstack.adapters.selfhost.hashed_bearer_auth import digest_bearer_secret
from rememberstack.adapters.selfhost.hashed_bearer_auth import HashedBearerAuth
from rememberstack.model import DocumentUpload
from rememberstack.model import IngestedVersion
from rememberstack.model import IngestPrincipal
from rememberstack.model import IngestPrincipalKind
from rememberstack.model.auth import CredentialKind
from rememberstack.model.auth import PerimeterScope
from rememberstack.ports.auth import AuthPerimeterPort
from rememberstack.surfaces.http_api import build_api
from tests.signed_key_support import build_auth
from tests.signed_key_support import build_trust
from tests.signed_key_support import FakeIssuer
from tests.signed_key_support import ISSUER
from tests.signed_key_support import present
from tests.signed_key_support import ready_auth
from tests.signed_key_support import TENANT


class _OpenBoundary:
    """Admission and readiness for the perimeter-only HTTP proof."""

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        """Admit the configured deployment while the API composes."""
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        """Leave request admission open."""


class _UnusedEngine:
    """A query engine that a refused request must never reach."""

    def __getattr__(self, name: str) -> Any:
        """Turn an accidental handler call into a precise test failure."""
        raise AssertionError(f"ingest-scope request reached query engine method {name}")


class _EmptyOperations:
    """An operation surface where every unknown operation fails closed."""

    def __init__(self, *, deployment_id: UUID) -> None:
        """Bind the deployment identity checked during composition."""
        self.deployment_id = deployment_id

    def descriptors(self) -> tuple[object, ...]:
        """No descriptor means an operation requires WRITE."""
        return ()

    def run(self, **_kwargs: object) -> Any:
        """The ingest credential must be refused before dispatch."""
        raise AssertionError("ingest-scope request reached an assured operation")


class _RecordingIngest:
    """A real endpoint result with a counter proving the handler ran."""

    def __init__(self, *, deployment_id: UUID) -> None:
        """Bind the receipt to the served deployment."""
        self.deployment_id = deployment_id
        self.calls = 0
        self.last_principal: IngestPrincipal | None = None

    def ingest(
        self,
        *,
        deployment_id: UUID,
        upload: DocumentUpload,
        ingested_by: IngestPrincipal | None = None,
    ) -> IngestedVersion:
        """Record one accepted upload."""
        assert deployment_id == self.deployment_id
        assert upload.content == b"memory"
        self.calls += 1
        self.last_principal = ingested_by
        return IngestedVersion(
            deployment_id=deployment_id,
            doc_id=uuid4(),
            version_id=uuid4(),
            content_hash="0" * 64,
            created=True,
            mime="text/markdown",
            title=None,
            versioning_mode="snapshot",
        )

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
        """This proof exercises only the one-shot browser upload path."""
        raise AssertionError("unexpected observed-source ingest")


def _ingest_client(
    *, deployment_id: UUID, auth: AuthPerimeterPort
) -> tuple[TestClient, _RecordingIngest]:
    """Compose the real HTTP perimeter around one recording ingest port."""
    ingest = _RecordingIngest(deployment_id=deployment_id)
    boundary = _OpenBoundary()
    app = build_api(
        engine=_UnusedEngine(),  # type: ignore[arg-type]
        deployment_id=deployment_id,
        admission=boundary,  # type: ignore[arg-type]
        readiness=boundary,  # type: ignore[arg-type]
        surface=_EmptyOperations(deployment_id=deployment_id),  # type: ignore[arg-type]
        ingest=ingest,
        connectors=object(),  # type: ignore[arg-type]
        auth=auth,
        trusted_principal_source=True,
    )
    return TestClient(app), ingest


# --- the §7.2 table, row by row ---------------------------------------------


def test_a_key_is_accepted_with_its_exact_claim_set() -> None:
    issuer, auth = ready_auth()
    context = present(auth, issuer.credential(kind="key"))

    assert context.deployment_id == issuer.deployment_id
    assert context.subject == "person-1"
    assert context.scope is PerimeterScope.READ
    assert context.credential_kind is CredentialKind.KEY
    assert context.actor_id == f"keycred:{context.credential_id}"
    assert context.source is None


@pytest.mark.parametrize(
    "projects",
    [
        "org:*",
        None,
        ["other-project", "PROJECT"],
        ["PROJECT"] + [f"p{i}" for i in range(19)],
    ],
)
def test_a_key_covering_this_project_is_accepted(projects: object) -> None:
    issuer, auth = ready_auth()
    this = str(issuer.deployment_id)
    if projects is None:
        projects = [this]
    elif isinstance(projects, list):
        projects = [this if item == "PROJECT" else item for item in projects]
    assert present(auth, issuer.credential(kind="key", projects=projects))


def test_a_session_is_accepted_with_and_without_src() -> None:
    issuer, auth = ready_auth()

    derived = present(auth, issuer.credential(kind="session", src="mcp"))
    browser = present(auth, issuer.credential(kind="session"))

    assert derived.source == "mcp"
    assert browser.source is None
    for context in (derived, browser):
        assert context.credential_kind is CredentialKind.BROWSER
        assert context.subject == "person-1"
        assert context.actor_id == f"browsercred:{context.credential_id}"


def test_a_service_credential_names_no_person() -> None:
    issuer, auth = ready_auth()
    context = present(auth, issuer.credential(kind="service"))

    assert context.subject is None
    assert context.credential_kind is CredentialKind.DEPLOYMENT
    assert context.actor_id == f"dpcred:{context.credential_id}"


@pytest.mark.parametrize(
    ("kind", "overrides"),
    [
        # An OAuth token meant for the hosted MCP server is not a key here.
        ("key", {"aud": "https://remember.dev/mcp"}),
        ("session", {"aud": "https://remember.dev/mcp"}),
        ("service", {"aud": "https://remember.dev/mcp"}),
        # A key for another tenant, a session for another deployment.
        ("key", {"aud": "org:other-tenant"}),
        ("key", {"aud": "DEPLOYMENT"}),
        ("session", {"aud": str(uuid4())}),
        ("session", {"aud": f"org:{TENANT}"}),
        ("service", {"aud": f"org:{TENANT}"}),
        ("key", {"aud": [f"org:{TENANT}"]}),
        # Coverage.
        ("key", {"projects": [f"p{i}" for i in range(20)] + ["PROJECT"]}),
        ("key", {"projects": []}),
        ("key", {"projects": ["another-project"]}),
        ("key", {"projects": "org:other"}),
        ("key", {"projects": "PROJECT"}),
        ("key", {"projects": ["PROJECT", 7]}),
        ("session", {"projects": ["PROJECT", "another-project"]}),
        ("session", {"projects": "org:*"}),
        ("session", {"projects": []}),
        # Tenant and subject.
        ("key", {"org": "other-tenant"}),
        ("session", {"org": "other-tenant"}),
        ("service", {"sub": "dpcred:someone-else"}),
        ("service", {"sub": "person-1"}),
        ("key", {"sub": ""}),
        ("session", {"src": ""}),
        ("session", {"src": 7}),
        # Common claims.
        ("key", {"iss": "https://other-issuer.example.test"}),
        ("key", {"kind_claim": "browser"}),
        ("key", {"kind_claim": "deployment"}),
        ("key", {"jti": ""}),
        ("key", {"permissions": "memory:read"}),
        ("key", {"permissions": ["memory:read", 7]}),
    ],
)
def test_a_claim_outside_the_contract_is_refused(
    kind: str, overrides: dict[str, object]
) -> None:
    issuer, auth = ready_auth()
    this = str(issuer.deployment_id)

    def substitute(value: object) -> object:
        if value == "PROJECT":
            return this
        if value == "DEPLOYMENT":
            return this
        if isinstance(value, list):
            return [substitute(item) for item in value]
        return value

    claims: dict[str, Any] = {
        name: substitute(value) for name, value in overrides.items()
    }
    with pytest.raises(SignedTokenUnusable):
        present(auth, issuer.credential(kind=kind, **claims))


@pytest.mark.parametrize(
    ("kind", "claim"),
    [
        ("key", "org"),
        ("key", "projects"),
        ("session", "org"),
        ("session", "projects"),
        *[
            (kind, claim)
            for kind in ("key", "session", "service")
            for claim in (
                "iss",
                "aud",
                "sub",
                "permissions",
                "kind",
                "iat",
                "nbf",
                "exp",
                "jti",
            )
        ],
    ],
)
def test_a_missing_claim_is_refused(kind: str, claim: str) -> None:
    issuer, auth = ready_auth()
    with pytest.raises(SignedTokenUnusable):
        present(auth, issuer.credential(kind=kind, drop=(claim,)))


def test_the_old_scope_claim_grants_nothing() -> None:
    """``scope`` is not part of the contract: without ``permissions`` it is refused."""
    issuer, auth = ready_auth()
    with pytest.raises(SignedTokenUnusable):
        present(auth, issuer.credential(scope="write", drop=("permissions",)))
    context = present(auth, issuer.credential(scope="write", permissions=()))
    assert context.scope is None


def test_an_expired_credential_is_refused_and_leeway_is_thirty_seconds() -> None:
    issuer, auth = ready_auth()
    now = int(time.time())
    with pytest.raises(SignedTokenUnusable):
        present(auth, issuer.credential(iat=now - 600, nbf=now - 600, exp=now - 60))
    assert present(auth, issuer.credential(iat=now - 600, nbf=now - 600, exp=now - 10))
    with pytest.raises(SignedTokenUnusable):
        present(auth, issuer.credential(nbf=now + 120))


def test_a_credential_signed_by_another_key_is_refused() -> None:
    issuer, auth = ready_auth()
    stranger = FakeIssuer(deployment_id=issuer.deployment_id)
    with pytest.raises(SignedTokenUnusable):
        present(auth, stranger.credential())


def test_an_unknown_kid_is_refused_rather_than_searched() -> None:
    issuer, auth = ready_auth()
    issuer.private["k9"] = issuer.private["k1"]
    with pytest.raises(SignedTokenUnusable):
        present(auth, issuer.credential(kid="k9"))


def test_an_unsigned_credential_is_refused() -> None:
    """The `alg: none` family: the algorithm is named by us, never by the token."""
    issuer, auth = ready_auth()
    now = int(time.time())
    unsigned = jwt.encode(
        {
            "iss": ISSUER,
            "aud": f"org:{TENANT}",
            "org": TENANT,
            "projects": "org:*",
            "sub": "person-1",
            "kind": "key",
            "permissions": ["memory:write"],
            "iat": now,
            "nbf": now,
            "exp": now + 300,
            "jti": uuid4().hex,
        },
        key="",
        algorithm="none",
        headers={"kid": "k1"},
    )
    with pytest.raises(SignedTokenUnusable):
        present(auth, f"rmb_{unsigned}")


def test_a_revocation_document_is_not_a_credential() -> None:
    issuer, auth = ready_auth()
    with pytest.raises(SignedTokenUnusable):
        present(auth, issuer.revocation())


# --- the prefix --------------------------------------------------------------


@pytest.mark.parametrize("prefix", ["rmb_", "abc_", "X_", ""])
def test_any_letters_only_prefix_is_stripped(prefix: str) -> None:
    issuer, auth = ready_auth()
    assert present(auth, issuer.credential(prefix=prefix))


@pytest.mark.parametrize("prefix", ["umc_dp_", "rmb_rmb_", "rm1_", "_", "rmb-"])
def test_a_prefix_that_is_not_letters_only_is_refused(prefix: str) -> None:
    issuer, auth = ready_auth()
    with pytest.raises(SignedTokenUnusable):
        present(auth, issuer.credential(prefix=prefix))


def test_strip_credential_prefix() -> None:
    assert strip_credential_prefix(presented="eyJabc") == "eyJabc"
    assert strip_credential_prefix(presented="rmb_eyJabc") == "eyJabc"
    with pytest.raises(SignedTokenUnusable):
        strip_credential_prefix(presented="no-separator")


# --- permissions → scope (§7.4) ----------------------------------------------


@pytest.mark.parametrize(
    ("permissions", "scope"),
    [
        (["memory:read"], PerimeterScope.READ),
        (["memory:write"], PerimeterScope.WRITE),
        (["memory:ingest"], PerimeterScope.INGEST),
        (["memory:read", "memory:write"], PerimeterScope.WRITE),
        (["memory:read", "memory:ingest", "memory:write"], PerimeterScope.WRITE),
        (["memory:read", "account:read", "account:manage"], PerimeterScope.READ),
        (["memory:read", "memory:read"], PerimeterScope.READ),
        (["account:read", "billing:view"], None),
        ([], None),
    ],
)
def test_permissions_map_to_one_scope(
    permissions: list[str], scope: PerimeterScope | None
) -> None:
    assert scope_for_permissions(permissions=permissions) is scope


@pytest.mark.parametrize(
    "permissions",
    [
        ["memory:admin"],
        ["memory:write", "memory:delete"],
        ["memory:read", "memory:ingest"],
        "memory:read",
        None,
        [1],
    ],
)
def test_unknown_or_conflicting_memory_permissions_are_refused(
    permissions: object,
) -> None:
    with pytest.raises(SignedTokenUnusable):
        scope_for_permissions(permissions=permissions)


# --- through HTTP -----------------------------------------------------------


def _upload(client: TestClient, bearer: str, **headers: str) -> Any:
    return client.post(
        "/ingest?filename=memory.md&mime=text/markdown",
        content=b"memory",
        headers={
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/octet-stream",
            **headers,
        },
    )


def test_an_ingest_credential_reaches_only_the_ingest_route() -> None:
    """The narrow D62 upload credential uploads, but cannot read or configure."""
    issuer, auth = ready_auth()
    client, ingest = _ingest_client(deployment_id=issuer.deployment_id, auth=auth)
    ingest_bearer = issuer.credential(kind="session", permissions=("memory:ingest",))
    headers = {"Authorization": f"Bearer {ingest_bearer}"}

    accepted = _upload(client, ingest_bearer)
    assert accepted.status_code == 200, accepted.text
    assert ingest.calls == 1

    refused = (
        client.post(
            "/connectors",
            json={"kind": "watched-directory", "name": "standing pull"},
            headers=headers,
        ),
        client.post(f"/connectors/{uuid4()}/pause", headers=headers),
        client.get("/search/claims", params={"query": "secret"}, headers=headers),
        client.get("/search/chunks", params={"query": "secret"}, headers=headers),
        client.post("/operations/anything", json={}, headers=headers),
    )
    assert [response.status_code for response in refused] == [403] * len(refused)

    read_response = _upload(client, issuer.credential(permissions=("memory:read",)))
    assert read_response.status_code == 403
    write_response = _upload(client, issuer.credential(permissions=("memory:write",)))
    assert write_response.status_code == 200, write_response.text
    assert ingest.calls == 2


def test_a_credential_without_memory_permission_authenticates_and_is_denied() -> None:
    issuer, auth = ready_auth()
    client, ingest = _ingest_client(deployment_id=issuer.deployment_id, auth=auth)
    bearer = issuer.credential(permissions=("account:read", "account:manage"))

    assert _upload(client, bearer).status_code == 403
    response = client.get(
        "/search/claims",
        params={"query": "x"},
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert response.status_code == 403
    assert ingest.calls == 0


def test_a_refused_credential_is_an_opaque_401() -> None:
    issuer, auth = ready_auth()
    client, ingest = _ingest_client(deployment_id=issuer.deployment_id, auth=auth)
    response = _upload(
        client,
        issuer.credential(
            permissions=("memory:write",), aud="https://remember.dev/mcp"
        ),
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "perimeter authentication failed"}
    assert ingest.calls == 0


def test_only_write_may_assert_ingest_attribution() -> None:
    """Direct browser upload does not grant authority to name its creator."""
    issuer, auth = ready_auth()
    client, ingest = _ingest_client(deployment_id=issuer.deployment_id, auth=auth)
    attribution = {
        "X-Ingest-Principal-Kind": "user",
        "X-Ingest-Principal-Ref": "user:asserted",
    }

    narrow = issuer.credential(kind="session", permissions=("memory:ingest",))
    assert _upload(client, narrow, **attribution).status_code == 200
    assert ingest.last_principal is None

    write = issuer.credential(kind="session", permissions=("memory:write",), src="mcp")
    assert _upload(client, write, **attribution).status_code == 200
    assert ingest.last_principal == IngestPrincipal(
        kind=IngestPrincipalKind.USER, external_ref="user:asserted"
    )


def test_the_shared_secret_retains_attribution_authority() -> None:
    """The self-host shared secret is unrestricted."""
    deployment_id = uuid4()
    secret = "shared-secret"
    auth = HashedBearerAuth(
        issued_deployment_id=deployment_id, digest=digest_bearer_secret(secret=secret)
    )
    client, ingest = _ingest_client(deployment_id=deployment_id, auth=auth)

    response = _upload(
        client,
        secret,
        **{
            "X-Ingest-Principal-Kind": "service",
            "X-Ingest-Principal-Ref": "service:control-plane",
        },
    )
    assert response.status_code == 200, response.text
    assert ingest.last_principal == IngestPrincipal(
        kind=IngestPrincipalKind.SERVICE, external_ref="service:control-plane"
    )


def test_a_project_id_other_than_the_deployment_id_is_matched() -> None:
    issuer = FakeIssuer(deployment_id=uuid4())
    issuer.revocation()
    trust = build_trust(issuer=issuer)
    trust.refresh()
    auth = build_auth(issuer=issuer, trust=trust, project_id="proj_42")

    assert present(auth, issuer.credential(projects=["proj_42"]))
    assert present(auth, issuer.credential(kind="session", projects=["proj_42"]))
    with pytest.raises(SignedTokenUnusable):
        present(auth, issuer.credential(projects=[str(issuer.deployment_id)]))
