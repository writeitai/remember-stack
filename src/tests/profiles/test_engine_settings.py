"""D147: one deployment's engine settings choose its buckets' credentials."""

from dataclasses import replace
from uuid import UUID

from pydantic import SecretStr
import pytest
from sqlalchemy.pool import NullPool

from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters.selfhost import MinIOSettings
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
        ),
        database=DatabaseSettings(
            database_url=SecretStr("postgresql+psycopg://u:p@db.invalid:5432/project")
        ),
        object_store=_store("shared"),
        model_provider=OpenRouterSettings(api_key="child-key"),
        work_ledger=WorkLedgerSettings(),
        object_store_by_bucket={"fs-raw": _store("raw")},
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
    # Unset TLS settings leave libpq's defaults alone.
    assert "sslmode" not in seen[0] and "sslrootcert" not in seen[0]


def test_customer_sql_tls_settings_reach_libpq(monkeypatch) -> None:  # noqa: ANN001
    """``query_role_sslmode``/``sslrootcert`` are passed to the connection."""
    from pathlib import Path

    import sqlalchemy

    from rememberstack.profiles import selfhost

    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        selfhost.psycopg, "connect", lambda **kwargs: seen.append(kwargs)
    )
    engine = sqlalchemy.create_engine("postgresql+psycopg://u:p@pg.invalid/project_a")
    try:
        selfhost._query_role_connect_factory(  # noqa: SLF001
            engine=engine, sslmode="verify-full", sslrootcert=Path("/etc/umc/pg-ca.pem")
        )()
    finally:
        engine.dispose()
    assert seen[0]["sslmode"] == "verify-full"
    assert seen[0]["sslrootcert"] == "/etc/umc/pg-ca.pem"
    settings = SelfHostSettings(
        deployment_id=UUID(int=1),
        query_role_sslmode="verify-full",
        query_role_sslrootcert=Path("/etc/umc/pg-ca.pem"),
    )
    assert settings.query_role_sslmode == "verify-full"


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


@pytest.mark.parametrize(
    ("route", "backup", "message"),
    (
        ("typesafe", "none", "ROUTE=openrouter"),
        ("openrouter", "openai_decisions", "BACKUP must be none"),
    ),
)
def test_from_settings_refuses_jev_configs_that_bypass_zdr(
    monkeypatch: pytest.MonkeyPatch, route: str, backup: str, message: str
) -> None:
    """A ZDR deployment's unsafe Jev routing stops the profile at construction.

    Neither the direct TypeSafe route nor the native OpenAI backup can be held
    to zero data retention, so the profile refuses them before it serves
    readiness or takes a task, not when the first Jev task arrives.
    """
    from rememberstack.adapters.typesafe import ConfigurationError

    monkeypatch.setenv("REMEMBERSTACK_FACT_ADJUDICATION_ENGINE", "jev")
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_ROUTE", route)
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_BACKUP", backup)
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_API_KEY", "process-typesafe-key")
    monkeypatch.setenv("REMEMBERSTACK_OPENAI_DECISIONS_API_KEY", "native-key")
    settings = replace(
        _settings(), model_provider=OpenRouterSettings(api_key="child-key", zdr=True)
    )

    with pytest.raises(ConfigurationError, match=message):
        SelfHostProfile.from_settings(settings)


def test_from_settings_reports_the_checked_systemone_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness reports the same System One settings the profile checked."""
    monkeypatch.setenv("REMEMBERSTACK_FACT_ADJUDICATION_ENGINE", "jev")
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_ROUTE", "openrouter")
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_BACKUP", "none")
    settings = replace(
        _settings(), model_provider=OpenRouterSettings(api_key="child-key", zdr=True)
    )
    profile = SelfHostProfile.from_settings(settings)
    try:
        # A later change to the environment does not change what was checked.
        monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_ROUTE", "typesafe")
        bindings = profile._model_bindings()  # noqa: SLF001
    finally:
        profile.close()

    assert bindings["systemone_route"] == "openrouter"
    assert bindings["systemone_backup"] == "none"


def test_injected_openrouter_provider_supplies_the_retention_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ZDR provider injected without its settings still governs Jev.

    Omitting ``model_provider_settings`` must not read as "ZDR off": the
    profile takes the injected provider's own settings, so the direct
    TypeSafe route is refused for a ZDR deployment, and a provider whose
    settings cannot be known is refused for Jev altogether.
    """
    from unittest.mock import MagicMock

    import sqlalchemy

    from rememberstack.adapters import OpenRouterModelProvider
    from rememberstack.adapters.typesafe import ConfigurationError

    monkeypatch.setenv("REMEMBERSTACK_FACT_ADJUDICATION_ENGINE", "jev")
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_ROUTE", "typesafe")
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_BACKUP", "none")
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_API_KEY", "process-typesafe-key")
    engine = sqlalchemy.create_engine("postgresql+psycopg://unused@127.0.0.1:1/none")
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(api_key="child-key", zdr=True)
    )
    common: dict[str, object] = {
        "settings": SelfHostSettings(deployment_id=UUID(int=148)),
        "engine": engine,
        "raw_store": MagicMock(),
        "artifact_store": MagicMock(),
        "corpusfs_store": MagicMock(),
    }
    try:
        with pytest.raises(ConfigurationError, match="ROUTE=openrouter"):
            SelfHostProfile(model_provider=provider, **common)  # type: ignore[arg-type]
        with pytest.raises(ConfigurationError, match="OpenRouter settings"):
            SelfHostProfile(model_provider=MagicMock(), **common)  # type: ignore[arg-type]
        monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_ROUTE", "openrouter")
        # Readiness keeps its historical source (the environment) here.
        monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_API_KEY", "env-key")
        profile = SelfHostProfile(model_provider=provider, **common)  # type: ignore[arg-type]
        assert profile._model_bindings()["systemone_route"] == "openrouter"  # noqa: SLF001
    finally:
        provider._client.close()  # noqa: SLF001
        engine.dispose()


@pytest.mark.parametrize(
    "conflict",
    (
        {"zdr": False},
        {"api_key": "other-project-key"},
        {"base_url": "https://gateway.example/api/v1"},
    ),
)
def test_jev_refuses_settings_that_differ_from_the_injected_provider(
    monkeypatch: pytest.MonkeyPatch, conflict: dict[str, object]
) -> None:
    """Jev never uses another key, base URL or ZDR policy than the provider's."""
    from unittest.mock import MagicMock

    import sqlalchemy

    from rememberstack.adapters import OpenRouterModelProvider
    from rememberstack.adapters.typesafe import ConfigurationError

    monkeypatch.setenv("REMEMBERSTACK_FACT_ADJUDICATION_ENGINE", "jev")
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_ROUTE", "openrouter")
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_BACKUP", "none")
    engine = sqlalchemy.create_engine("postgresql+psycopg://unused@127.0.0.1:1/none")
    injected = OpenRouterSettings(api_key="child-key", zdr=True)
    provider = OpenRouterModelProvider(settings=injected)
    explicit = injected.model_copy(update=conflict)
    try:
        with pytest.raises(ConfigurationError, match="differ from the injected"):
            SelfHostProfile(
                settings=SelfHostSettings(deployment_id=UUID(int=149)),
                engine=engine,
                raw_store=MagicMock(),
                artifact_store=MagicMock(),
                corpusfs_store=MagicMock(),
                model_provider=provider,
                model_provider_settings=explicit,
            )
    finally:
        provider._client.close()  # noqa: SLF001
        engine.dispose()


@pytest.mark.parametrize("engine_name", ("prompt", "jev"))
def test_readiness_keeps_its_settings_source_when_settings_are_omitted(
    monkeypatch: pytest.MonkeyPatch, engine_name: str
) -> None:
    """Omitted settings still report the environment's bindings, as on main.

    Jev routing reads the injected provider's settings, but the serialized
    readiness bindings for a profile built without explicit settings keep
    their historical source in both engine modes.
    """
    from unittest.mock import MagicMock

    import sqlalchemy

    from rememberstack.adapters import OpenRouterModelProvider
    from rememberstack.profiles.selfhost import _model_bindings

    monkeypatch.setenv("REMEMBERSTACK_FACT_ADJUDICATION_ENGINE", engine_name)
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_ROUTE", "openrouter")
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_BACKUP", "none")
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_API_KEY", "env-key")
    monkeypatch.setenv("REMEMBERSTACK_OPENROUTER_MAX_COMPLETION_TOKENS", "32000")
    engine = sqlalchemy.create_engine("postgresql+psycopg://unused@127.0.0.1:1/none")
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(api_key="child-key", max_completion_tokens=8192)
    )
    try:
        profile = SelfHostProfile(
            settings=SelfHostSettings(deployment_id=UUID(int=150)),
            engine=engine,
            raw_store=MagicMock(),
            artifact_store=MagicMock(),
            corpusfs_store=MagicMock(),
            model_provider=provider,
        )
        bindings = profile._model_bindings()  # noqa: SLF001
    finally:
        provider._client.close()  # noqa: SLF001
        engine.dispose()

    assert bindings == _model_bindings()
    assert bindings["openrouter_max_completion_tokens"] == "32000"


@pytest.mark.parametrize(
    ("before", "after"),
    (
        ({"ENGINE": "prompt"}, {"ENGINE": "jev"}),
        ({"ENGINE": "jev"}, {"ENGINE": "prompt"}),
        (
            {"ENGINE": "jev", "FALLBACK_TO_PROMPT": "false"},
            {"ENGINE": "jev", "FALLBACK_TO_PROMPT": "true"},
        ),
    ),
)
def test_fact_handlers_refuse_settings_changed_after_construction(
    monkeypatch: pytest.MonkeyPatch, before: dict[str, str], after: dict[str, str]
) -> None:
    """Neither fact handler is built on an engine or fallback never checked.

    The routing and zero-data-retention checks ran against the values read at
    construction; a later change (prompt to jev, jev to prompt, or turning on
    the prompt fallback) is refused before any client exists.
    """
    from unittest.mock import MagicMock

    import sqlalchemy

    from rememberstack.adapters.typesafe import ConfigurationError
    from rememberstack.model import PipelineStage

    def apply(values: dict[str, str]) -> None:
        monkeypatch.setenv("REMEMBERSTACK_FACT_ADJUDICATION_ENGINE", values["ENGINE"])
        monkeypatch.setenv(
            "REMEMBERSTACK_FACT_FALLBACK_TO_PROMPT",
            values.get("FALLBACK_TO_PROMPT", "false"),
        )

    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_ROUTE", "openrouter")
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_BACKUP", "none")
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_API_KEY", "process-typesafe-key")
    apply(before)
    engine = sqlalchemy.create_engine("postgresql+psycopg://unused@127.0.0.1:1/none")
    try:
        profile = SelfHostProfile(
            settings=SelfHostSettings(deployment_id=UUID(int=151)),
            engine=engine,
            raw_store=MagicMock(),
            artifact_store=MagicMock(),
            corpusfs_store=MagicMock(),
            model_provider=MagicMock(),
            model_provider_settings=OpenRouterSettings(api_key="child-key", zdr=True),
        )
        apply(after)
        for stage in (
            PipelineStage.NORMALIZE_RELATIONS,
            PipelineStage.ADJUDICATE_OBSERVATIONS,
        ):
            with pytest.raises(ConfigurationError, match="changed after this profile"):
                profile._handler(stage=stage)  # noqa: SLF001
    finally:
        engine.dispose()


def test_zdr_prompt_fallback_needs_the_deployments_openrouter_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prompt fallback cannot resend Jev content through another provider."""
    from unittest.mock import MagicMock

    import sqlalchemy

    from rememberstack.adapters.typesafe import ConfigurationError

    monkeypatch.setenv("REMEMBERSTACK_FACT_ADJUDICATION_ENGINE", "jev")
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_ROUTE", "openrouter")
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_BACKUP", "none")
    monkeypatch.setenv("REMEMBERSTACK_FACT_FALLBACK_TO_PROMPT", "true")
    engine = sqlalchemy.create_engine("postgresql+psycopg://unused@127.0.0.1:1/none")
    try:
        with pytest.raises(ConfigurationError, match="FALLBACK_TO_PROMPT"):
            SelfHostProfile(
                settings=SelfHostSettings(deployment_id=UUID(int=152)),
                engine=engine,
                raw_store=MagicMock(),
                artifact_store=MagicMock(),
                corpusfs_store=MagicMock(),
                model_provider=MagicMock(),
                model_provider_settings=OpenRouterSettings(
                    api_key="child-key", zdr=True
                ),
            )
    finally:
        engine.dispose()


def test_readiness_reports_the_checked_fact_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness describes the settings the handlers use, not a later env."""
    monkeypatch.setenv("REMEMBERSTACK_FACT_ADJUDICATION_ENGINE", "jev")
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_ROUTE", "openrouter")
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_BACKUP", "none")
    settings = replace(
        _settings(), model_provider=OpenRouterSettings(api_key="child-key", zdr=True)
    )
    profile = SelfHostProfile.from_settings(settings)
    try:
        monkeypatch.setenv("REMEMBERSTACK_FACT_ADJUDICATION_ENGINE", "prompt")
        bindings = profile._model_bindings()  # noqa: SLF001
    finally:
        profile.close()

    assert bindings["systemone_route"] == "openrouter"
    assert bindings["fact_adjudication"] == "jev-latest"
