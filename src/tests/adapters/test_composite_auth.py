"""Several ways to prove yourself, one perimeter, one refusal.

These also cover the wiring, because an adapter nobody instantiates is an
adapter that does not exist: the profile must actually build the composite when
both kinds of material are configured.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import SecretBytes
from pydantic import SecretStr
from pydantic import ValidationError
import pytest

from rememberstack.adapters.managed.composite_auth import CompositeAuth
from rememberstack.adapters.managed.signed_token_auth import SignedTokenAuth
from rememberstack.adapters.selfhost.hashed_bearer_auth import digest_bearer_secret
from rememberstack.adapters.selfhost.hashed_bearer_auth import HashedBearerAuth
from rememberstack.model import PerimeterCredential
from rememberstack.model.auth import PerimeterScope
from rememberstack.profiles.selfhost import resolve_selfhost_api_auth
from rememberstack.profiles.selfhost import resolve_selfhost_perimeter_trust
from rememberstack.profiles.selfhost import SelfHostSettings
from tests.signed_key_support import build_trust
from tests.signed_key_support import FakeIssuer
from tests.signed_key_support import ISSUER
from tests.signed_key_support import JWKS_URL
from tests.signed_key_support import MemoryStateStore
from tests.signed_key_support import present
from tests.signed_key_support import ready_auth
from tests.signed_key_support import REVOCATION_URL
from tests.signed_key_support import TENANT

_SECRET = "a-shared-self-host-secret"


def _credential(*, secret: str) -> PerimeterCredential:
    """Present a secret the way the perimeter would."""
    return PerimeterCredential(
        scheme="Bearer", value=SecretBytes(secret.encode("utf-8"))
    )


def _signed_settings(**overrides: Any) -> dict[str, Any]:
    return {
        "api_key_issuer": ISSUER,
        "api_key_tenant_id": TENANT,
        "api_signing_keys_url": JWKS_URL,
        "api_revocation_url": REVOCATION_URL,
        **overrides,
    }


def test_both_credential_kinds_reach_the_same_deployment() -> None:
    """The point of a composite: one set of routes, several callers."""
    issuer, signed_auth = ready_auth()
    deployment = issuer.deployment_id
    composite = CompositeAuth(
        adapters=(
            HashedBearerAuth(
                issued_deployment_id=deployment,
                digest=digest_bearer_secret(secret=_SECRET),
            ),
            signed_auth,
        )
    )

    shared = composite.authenticate(credential=_credential(secret=_SECRET))
    assert shared.deployment_id == deployment
    # A shared secret carries no subject and is unrestricted.
    assert shared.subject is None
    assert shared.scope is PerimeterScope.WRITE

    signed = present(composite, issuer.credential())
    assert signed.deployment_id == deployment
    assert signed.subject == "person-1"
    assert signed.scope is PerimeterScope.READ


def test_a_credential_no_adapter_accepts_is_one_refusal() -> None:
    """The refusal says nothing about which adapter came closest."""
    issuer, signed_auth = ready_auth()
    composite = CompositeAuth(
        adapters=(
            HashedBearerAuth(
                issued_deployment_id=issuer.deployment_id,
                digest=digest_bearer_secret(secret=_SECRET),
            ),
            signed_auth,
        )
    )

    with pytest.raises(ValueError, match="unknown credential"):
        composite.authenticate(credential=_credential(secret="not-either-of-them"))


def test_a_composite_needs_an_adapter() -> None:
    """An empty composite would authenticate nothing and say so confusingly."""
    with pytest.raises(ValueError):
        CompositeAuth(adapters=())


def test_the_profile_composes_when_both_are_configured() -> None:
    """Wiring, not just parts: an adapter nobody builds does not exist."""
    settings = SelfHostSettings(
        deployment_id=uuid4(), api_bearer_token=SecretStr(_SECRET), **_signed_settings()
    )
    trust = resolve_selfhost_perimeter_trust(
        settings=settings, store=MemoryStateStore()
    )

    auth = resolve_selfhost_api_auth(settings=settings, trust=trust)

    assert isinstance(auth, CompositeAuth)
    assert auth.authenticate(credential=_credential(secret=_SECRET)).scope is (
        PerimeterScope.WRITE
    )


def test_an_issuer_alone_is_a_perimeter() -> None:
    """A managed host may have no shared secret at all."""
    deployment = uuid4()
    issuer = FakeIssuer(deployment_id=deployment)
    settings = SelfHostSettings(
        deployment_id=deployment, require_api_auth=True, **_signed_settings()
    )
    trust = build_trust(issuer=issuer)

    auth = resolve_selfhost_api_auth(settings=settings, trust=trust)

    assert isinstance(auth, SignedTokenAuth)
    issuer.revocation()
    trust.refresh()
    assert present(auth, issuer.credential()).subject == "person-1"


def test_the_project_id_defaults_to_the_deployment_id() -> None:
    deployment = uuid4()
    issuer = FakeIssuer(deployment_id=deployment)
    trust = build_trust(issuer=issuer)
    issuer.revocation()
    trust.refresh()

    default = resolve_selfhost_api_auth(
        settings=SelfHostSettings(deployment_id=deployment, **_signed_settings()),
        trust=trust,
    )
    assert present(default, issuer.credential(projects=[str(deployment)]))

    configured = resolve_selfhost_api_auth(
        settings=SelfHostSettings(
            deployment_id=deployment, **_signed_settings(api_key_project_id="p-7")
        ),
        trust=trust,
    )
    assert present(configured, issuer.credential(projects=["p-7"]))


def test_an_issuer_without_a_trust_source_refuses_to_start() -> None:
    settings = SelfHostSettings(deployment_id=uuid4(), **_signed_settings())
    with pytest.raises(RuntimeError, match="trust"):
        resolve_selfhost_api_auth(settings=settings)


@pytest.mark.parametrize(
    "overrides",
    [
        {"api_key_tenant_id": None},
        {"api_signing_keys_url": None},
        {"api_revocation_url": None},
        {"api_revocation_url": "file:///etc/revocation"},
        {"api_signing_keys_url": "jwks.json"},
        {"api_key_issuer": None},
    ],
)
def test_incomplete_signed_key_settings_refuse_to_start(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        SelfHostSettings(deployment_id=uuid4(), **_signed_settings(**overrides))


def test_blank_signed_key_settings_are_unset() -> None:
    """Compose interpolates unset variables as empty strings."""
    settings = SelfHostSettings(
        deployment_id=uuid4(),
        api_key_issuer="",
        api_key_tenant_id="",
        api_key_project_id="",
        api_signing_keys_url="",
        api_revocation_url="",
    )
    assert settings.api_key_issuer is None
    assert resolve_selfhost_api_auth(settings=settings) is None


def test_require_api_auth_refuses_when_nothing_is_configured() -> None:
    """The check asks whether there is a perimeter, not which one."""
    settings = SelfHostSettings(deployment_id=uuid4(), require_api_auth=True)

    with pytest.raises(RuntimeError, match="REQUIRE_API_AUTH"):
        resolve_selfhost_api_auth(settings=settings)


def test_the_quickstart_still_has_no_perimeter() -> None:
    """Configuring nothing is still the open OSS quickstart (D60)."""
    assert (
        resolve_selfhost_api_auth(settings=SelfHostSettings(deployment_id=uuid4()))
        is None
    )


def test_the_app_lifespan_loads_and_refreshes_the_trust() -> None:
    """Start-up loads the persisted row and refreshes; shutdown stops the loop."""
    import time

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from rememberstack.profiles.selfhost import attach_perimeter_trust_refresh

    issuer = FakeIssuer(deployment_id=uuid4())
    store = MemoryStateStore()
    issuer.revocation()
    build_trust(issuer=issuer, store=store).refresh()  # a previous process
    issuer.fail = True
    trust = build_trust(issuer=issuer, store=store)
    app = FastAPI()
    attach_perimeter_trust_refresh(app=app, trust=trust)

    assert trust.current() is None
    trust.refresh_s = 0.01
    with TestClient(app):
        current = trust.current()
        assert current is not None and current[1].seq == 1  # loaded at start-up
        issuer.fail = False
        issuer.revocation()
        deadline = time.monotonic() + 5
        while (current := trust.current()) is not None and current[1].seq < 2:
            assert time.monotonic() < deadline
            time.sleep(0.01)
