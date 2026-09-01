"""Hashed Bearer perimeter adapter: issued-deployment BIND, not process identity."""

from uuid import UUID

from pydantic import SecretBytes
from pydantic import SecretStr
import pytest

from rememberstack.adapters.selfhost.hashed_bearer_auth import digest_bearer_secret
from rememberstack.adapters.selfhost.hashed_bearer_auth import HashedBearerAuth
from rememberstack.adapters.selfhost.hashed_bearer_auth import parse_bearer_bind
from rememberstack.model import PerimeterCredential
from rememberstack.profiles.selfhost import resolve_selfhost_api_auth
from rememberstack.profiles.selfhost import SelfHostSettings

_DEPLOYMENT_A = UUID("54000000-0000-0000-0000-00000000000a")
_DEPLOYMENT_B = UUID("54000000-0000-0000-0000-00000000000b")
_SECRET = "umc_dp_test-secret-not-for-production"


def _credential(*, secret: str) -> PerimeterCredential:
    return PerimeterCredential(
        scheme="Bearer", value=SecretBytes(secret.encode("utf-8"))
    )


def test_parse_bearer_bind_round_trip() -> None:
    """BIND is issued UUID plus hex SHA-256."""
    digest = digest_bearer_secret(secret=_SECRET)
    issued, parsed = parse_bearer_bind(bind=f"{_DEPLOYMENT_A}:{digest.hex()}")
    assert issued == _DEPLOYMENT_A
    assert parsed == digest


def test_parse_bearer_bind_rejects_bare_digest() -> None:
    """A digest without an issued UUID is not a conforming bind."""
    with pytest.raises(ValueError, match="issued_deployment_uuid"):
        parse_bearer_bind(bind=digest_bearer_secret(secret=_SECRET).hex())


def test_authenticate_returns_issued_uuid_not_caller_guess() -> None:
    """The adapter must not copy a process UUID into the context."""
    digest = digest_bearer_secret(secret=_SECRET)
    auth = HashedBearerAuth(issued_deployment_id=_DEPLOYMENT_A, digest=digest)
    context = auth.authenticate(credential=_credential(secret=_SECRET))
    assert context.deployment_id == _DEPLOYMENT_A
    assert context.principal == "bearer"


def test_unknown_secret_raises() -> None:
    """A miss is an authentication failure, not a wrong-deployment context."""
    digest = digest_bearer_secret(secret=_SECRET)
    auth = HashedBearerAuth(issued_deployment_id=_DEPLOYMENT_A, digest=digest)
    with pytest.raises(ValueError, match="unknown credential"):
        auth.authenticate(credential=_credential(secret="nope"))


def test_require_api_auth_without_bind_refuses_to_start() -> None:
    """Managed mode must not boot an open API when BIND is missing."""
    settings = SelfHostSettings(deployment_id=_DEPLOYMENT_B, require_api_auth=True)
    # The check now asks whether there is a perimeter at all, not whether
    # there is a BIND specifically: signing keys alone are enough (D59).
    with pytest.raises(RuntimeError, match="REQUIRE_API_AUTH"):
        resolve_selfhost_api_auth(settings=settings)


def test_empty_require_api_auth_string_is_false() -> None:
    """Compose may pass REQUIRE_API_AUTH=; that must not fail settings parse."""
    settings = SelfHostSettings.model_validate(
        {"deployment_id": _DEPLOYMENT_B, "require_api_auth": ""}
    )
    assert settings.require_api_auth is False
    assert resolve_selfhost_api_auth(settings=settings) is None


def test_open_quickstart_when_auth_env_unset() -> None:
    """D60: unset BIND and REQUIRE_API_AUTH=false leaves the perimeter open."""
    settings = SelfHostSettings(deployment_id=_DEPLOYMENT_B)
    assert resolve_selfhost_api_auth(settings=settings) is None


def test_token_must_match_bind() -> None:
    """TOKEN and BIND together are fail-closed if they disagree."""
    digest = digest_bearer_secret(secret=_SECRET)
    settings = SelfHostSettings(
        deployment_id=_DEPLOYMENT_A,
        api_bearer_bind=f"{_DEPLOYMENT_A}:{digest.hex()}",
        api_bearer_token=SecretStr("different-secret"),
    )
    with pytest.raises(RuntimeError, match="does not match"):
        resolve_selfhost_api_auth(settings=settings)


def test_token_only_binds_this_process_deployment() -> None:
    """Local TOKEN convenience uses SELFHOST_DEPLOYMENT_ID as the issued UUID."""
    settings = SelfHostSettings(
        deployment_id=_DEPLOYMENT_B, api_bearer_token=SecretStr(_SECRET)
    )
    auth = resolve_selfhost_api_auth(settings=settings)
    # The resolver may now return a signature adapter or a composite, so a test
    # that wants the digest adapter says so rather than assuming.
    assert isinstance(auth, HashedBearerAuth)
    assert auth.issued_deployment_id == _DEPLOYMENT_B
    context = auth.authenticate(credential=_credential(secret=_SECRET))
    assert context.deployment_id == _DEPLOYMENT_B
