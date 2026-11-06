"""D147: one deployment's engine settings choose its buckets' credentials."""

from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.pool import NullPool

from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters.selfhost import MinIOSettings
from rememberstack.adapters.selfhost import ObjectStoreForgetManifestStore
from rememberstack.profiles import EngineSettings
from rememberstack.profiles import SelfHostProfile
from rememberstack.profiles import SelfHostSettings
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.settings import DatabaseSettings


def _store(key: str) -> MinIOSettings:
    return MinIOSettings(
        endpoint_url="https://s3.example.invalid",
        access_key=SecretStr(key),
        secret_key=SecretStr(f"{key}-secret"),
    )


def _settings() -> EngineSettings:
    return EngineSettings(
        deployment=SelfHostSettings(
            deployment_id=UUID(int=147),
            raw_bucket_name="fs-raw",
            artifacts_bucket_name="fs-artifacts",
            corpusfs_bucket_name="fs-corpus",
            forget_manifest_bucket_name="fs-forget",
        ),
        database=DatabaseSettings(
            database_url=SecretStr("postgresql+psycopg://u:p@db.invalid:5432/project")
        ),
        object_store=_store("shared"),
        model_provider=OpenRouterSettings(api_key="child-key"),
        work_ledger=WorkLedgerSettings(),
        object_store_by_bucket={"fs-raw": _store("raw"), "fs-forget": _store("forget")},
    )


def _access_key(store: object) -> str:
    client = store._client  # type: ignore[attr-defined]  # noqa: SLF001
    return client._request_signer._credentials.access_key  # noqa: SLF001


def test_each_bucket_uses_its_own_credential_or_the_shared_one() -> None:
    settings = _settings()
    assert _access_key(settings.object_store_for(bucket="fs-raw")) == "raw"
    assert _access_key(settings.object_store_for(bucket="fs-corpus")) == "shared"


def test_from_settings_keeps_no_state_on_local_disk_and_takes_engine_options() -> None:
    profile = SelfHostProfile.from_settings(
        _settings(), engine_options={"poolclass": NullPool}
    )
    try:
        assert isinstance(profile._engine.pool, NullPool)  # noqa: SLF001
        assert _access_key(profile._raw_store) == "raw"  # noqa: SLF001
        assert _access_key(profile._artifact_store) == "shared"  # noqa: SLF001
        manifests = profile._forget_manifest_store()  # noqa: SLF001
        assert isinstance(manifests, ObjectStoreForgetManifestStore)
        assert _access_key(manifests._store) == "forget"  # noqa: SLF001
    finally:
        profile.close()
