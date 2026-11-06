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


def test_customer_sql_can_bypass_the_pooler(monkeypatch) -> None:  # noqa: ANN001
    """The query role connects to the configured host and port, else the URL's."""
    import sqlalchemy

    from rememberstack.profiles import selfhost

    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        selfhost.psycopg, "connect", lambda **kwargs: seen.append(kwargs)
    )
    engine = sqlalchemy.create_engine(
        "postgresql+psycopg://u:p@pgbouncer.invalid:6432/project_a"
    )
    try:
        selfhost._query_role_connect_factory(engine=engine)()  # noqa: SLF001
        selfhost._query_role_connect_factory(  # noqa: SLF001
            engine=engine, host="postgres.invalid", port=5432
        )()
    finally:
        engine.dispose()
    assert [(item["host"], item["port"], item["dbname"]) for item in seen] == [
        ("pgbouncer.invalid", 6432, "project_a"),
        ("postgres.invalid", 5432, "project_a"),
    ]
    assert seen[1]["user"] == "rememberstack_query_project_a"


def test_setup_hands_alembic_a_url_encoded_password_verbatim() -> None:
    """A password with ``@`` and ``%`` reaches Alembic intact and is never parsed."""
    from pathlib import Path

    from sqlalchemy.engine import URL

    from rememberstack.profiles.selfhost import _migration_config

    url = URL.create(
        "postgresql+psycopg",
        username="admin",
        password="p@ss%25w0rd%",
        host="db.invalid",
        database="project_a",
    ).render_as_string(hide_password=False)
    assert "%40" in url and "%25" in url  # the encoding that used to break

    config = _migration_config(path=Path("alembic.ini"), database_url=url)

    # What setup's env.py reads: the option and the interpolated section.
    assert config.get_main_option("sqlalchemy.url") == url
    section = config.get_section(config.config_ini_section) or {}
    assert section["sqlalchemy.url"] == url
