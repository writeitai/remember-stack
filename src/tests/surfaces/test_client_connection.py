"""One connection resolver for the SDK and the CLI (D136 §8.2–§8.3).

Precedence, the stored-key origin rule, signed-key host resolution with its
cache and moved-deployment retry, typed rate limiting, and ``client.account``
— each run against the fake issuer and engines in :mod:`fake_issuer`.
"""

from __future__ import annotations

from collections.abc import Callable
import json
from uuid import UUID

import httpx
import pytest

from remember import AccountApiUnavailable
from remember import Client
from remember import ConnectorCreate
from remember import MemoryApiError
from remember import ProjectResolutionError
from remember import RateLimited
from remember import StoredKeyRefused
from remember.cli import _InternalOpsSettings
from remember.cli import main
from remember.connection import clear_host_cache
from remember.connection import HOST_CACHE_TTL_SECONDS
from remember.connection import resolve_connection
from remember.connection import resolve_project
from remember.credentials import config_dir
from remember.issuer import signed_key_claims
from tests.surfaces.fake_issuer import DEPLOYMENT_A
from tests.surfaces.fake_issuer import DEPLOYMENT_B
from tests.surfaces.fake_issuer import FakeIssuer
from tests.surfaces.fake_issuer import ISSUER
from tests.surfaces.fake_issuer import make_key


@pytest.fixture()
def issuer(monkeypatch: pytest.MonkeyPatch) -> FakeIssuer:
    """A fake issuer every ``httpx.Client`` the CLI creates talks to."""
    fake = FakeIssuer()
    real_client = httpx.Client

    def patched(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = fake.transport()
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", patched)
    return fake


def _store(payload: dict[str, object]) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "credentials.json"
    path.write_text(json.dumps({"version": 2, **payload}))
    path.chmod(0o600)


def _sdk(
    issuer: FakeIssuer,
    *,
    api_key: str | None = None,
    api_url: str | None = None,
    project: str | None = None,
) -> None:
    with Client(
        api_key=api_key, base_url=api_url, project=project, transport=issuer.transport()
    ) as client:
        client.list_operations()


def _cli(
    issuer: FakeIssuer,
    *,
    api_key: str | None = None,
    api_url: str | None = None,
    project: str | None = None,
) -> None:
    argv = ["operations", "list"]
    for flag, value in (
        ("--api-key", api_key),
        ("--api-url", api_url),
        ("--project", project),
    ):
        if value is not None:
            argv += [flag, value]
    code = main(argv)
    if code == 2:
        raise StoredKeyRefused(detail="cli exit 2")
    if code != 0:
        raise MemoryApiError(detail=f"cli exit {code}")


ENTRY_POINTS: dict[str, Callable[..., None]] = {"sdk": _sdk, "cli": _cli}


def _last_engine_call(issuer: FakeIssuer) -> tuple[str, str | None]:
    request = issuer.engine_requests()[-1]
    return str(request.url), request.headers.get("Authorization")


# --- precedence --------------------------------------------------------------


@pytest.mark.parametrize("entry", sorted(ENTRY_POINTS))
@pytest.mark.parametrize(
    ("explicit", "environment", "stored", "expected_url", "expected_auth"),
    [
        # nothing anywhere → the default local engine, no key
        ({}, {}, None, "http://127.0.0.1:8000/operations", None),
        # the credential file alone
        (
            {},
            {},
            {"api_url": "http://file.test", "key": "file-key"},
            "http://file.test/operations",
            "Bearer file-key",
        ),
        # environment beats the file
        (
            {},
            {"REMEMBER_API_URL": "http://env.test", "REMEMBER_API_KEY": "env-key"},
            {"api_url": "http://file.test", "key": "file-key"},
            "http://env.test/operations",
            "Bearer env-key",
        ),
        # explicit beats the environment; `Bearer <key>` is accepted
        (
            {"api_url": "http://explicit.test", "api_key": "Bearer explicit-key"},
            {"REMEMBER_API_URL": "http://env.test", "REMEMBER_API_KEY": "env-key"},
            {"api_url": "http://file.test", "key": "file-key"},
            "http://explicit.test/operations",
            "Bearer explicit-key",
        ),
        # an environment key with the file's URL (each setting falls through alone)
        (
            {},
            {"REMEMBER_API_KEY": "env-key"},
            {"api_url": "http://file.test", "key": "file-key"},
            "http://file.test/operations",
            "Bearer env-key",
        ),
    ],
)
def test_one_precedence_for_sdk_and_cli(
    entry: str,
    explicit: dict[str, str],
    environment: dict[str, str],
    stored: dict[str, object] | None,
    expected_url: str,
    expected_auth: str | None,
    issuer: FakeIssuer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    if stored is not None:
        _store(stored)
    ENTRY_POINTS[entry](issuer, **explicit)
    assert _last_engine_call(issuer) == (expected_url, expected_auth)


@pytest.mark.parametrize("entry", sorted(ENTRY_POINTS))
def test_removed_variable_names_have_no_effect(
    entry: str, issuer: FakeIssuer, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "REMEMBER_TOKEN",
        "REMEMBER_API_AUTHORIZATION",
        "REMEMBERSTACK_API_AUTHORIZATION",
        "REMEMBER_DATA_PLANE_URL",
        "REMEMBERSTACK_API_URL",
        "REMEMBER_TOKEN_HOST",
        "REMEMBER_CONTROL_PLANE_URL",
        "REMEMBERSTACK_TOKEN_HOST",
        "REMEMBER_CLOUD_TOKEN",
        "REMEMBER_CLOUD_ORG",
        "REMEMBER_CLOUD_URL",
    ):
        monkeypatch.setenv(name, "http://legacy.test" if "URL" in name else "legacy")
    ENTRY_POINTS[entry](issuer)
    assert _last_engine_call(issuer) == ("http://127.0.0.1:8000/operations", None)


@pytest.mark.parametrize("entry", sorted(ENTRY_POINTS))
def test_bare_unprefixed_names_are_never_read(
    entry: str, issuer: FakeIssuer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only ``REMEMBER_*`` names configure a client; generic names do not."""
    configured_dir = config_dir()
    monkeypatch.setenv("API_URL", "http://leak:1")
    monkeypatch.setenv("API_AUTHORIZATION", "Bearer leaked")
    monkeypatch.setenv("TOKEN_HOST", "http://leak:1")
    monkeypatch.setenv("CONFIG_DIR", "/nonexistent-leak")
    monkeypatch.setenv("INTERNAL_OPS", "true")
    monkeypatch.setenv("REMEMBER_API_KEY", "secret")
    ENTRY_POINTS[entry](issuer)
    assert _last_engine_call(issuer) == (
        "http://127.0.0.1:8000/operations",
        "Bearer secret",
    )
    assert config_dir() == configured_dir
    assert _InternalOpsSettings.model_validate({}).internal_ops is False


@pytest.mark.parametrize("entry", sorted(ENTRY_POINTS))
@pytest.mark.parametrize(
    ("explicit", "environment", "stored_default", "expected_query"),
    [
        ({}, {}, None, None),
        ({}, {}, "notes", "notes"),
        ({}, {"REMEMBER_PROJECT": "p-notes"}, "docs", "p-notes"),
        ({"project": "docs"}, {"REMEMBER_PROJECT": "p-notes"}, "notes", "docs"),
    ],
)
def test_project_precedence_reaches_the_issuer(
    entry: str,
    explicit: dict[str, str],
    environment: dict[str, str],
    stored_default: str | None,
    expected_query: str | None,
    issuer: FakeIssuer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    key = make_key()
    _store({"issuer": ISSUER, "key": key, "default_project": stored_default})
    ENTRY_POINTS[entry](issuer, **explicit)
    resolution = issuer.calls("/api/v1/keys/self/project")[-1]
    assert resolution.url.params.get("project") == expected_query
    assert resolution.headers["Authorization"] == f"Bearer {key}"


def test_the_file_is_not_read_when_everything_is_explicit() -> None:
    """A stale or broken file cannot break a caller who supplied every setting."""
    _store({"legacy": True})
    connection = resolve_connection(api_key="k", api_url="http://explicit.test")
    assert connection.stored is None
    assert connection.key_source == "explicit"


# --- stored-key origin rule --------------------------------------------------


@pytest.mark.parametrize("entry", sorted(ENTRY_POINTS))
def test_stored_self_hosted_key_is_not_sent_to_another_engine(
    entry: str, issuer: FakeIssuer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store({"api_url": "http://file.test", "key": "file-key"})
    monkeypatch.setenv("REMEMBER_API_URL", "http://elsewhere.test")
    with pytest.raises(StoredKeyRefused):
        ENTRY_POINTS[entry](issuer)
    assert issuer.engine_requests() == []

    # Same origin as the stored URL (another path) is still the stored engine.
    ENTRY_POINTS[entry](issuer, api_url="http://file.test/prefix")
    assert _last_engine_call(issuer) == (
        "http://file.test/prefix/operations",
        "Bearer file-key",
    )


@pytest.mark.parametrize("entry", sorted(ENTRY_POINTS))
def test_explicit_key_goes_where_its_caller_directs(
    entry: str, issuer: FakeIssuer
) -> None:
    _store({"api_url": "http://file.test", "key": "file-key"})
    ENTRY_POINTS[entry](issuer, api_key="mine", api_url="http://elsewhere.test")
    assert _last_engine_call(issuer) == (
        "http://elsewhere.test/operations",
        "Bearer mine",
    )


@pytest.mark.parametrize("entry", sorted(ENTRY_POINTS))
def test_stored_signed_key_only_reaches_issuer_resolved_hosts(
    entry: str, issuer: FakeIssuer, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = make_key()
    _store({"issuer": ISSUER, "key": key})
    # The deployment the issuer resolves for the key: allowed.
    monkeypatch.setenv("REMEMBER_API_URL", DEPLOYMENT_A)
    ENTRY_POINTS[entry](issuer)
    assert _last_engine_call(issuer) == (f"{DEPLOYMENT_A}/operations", f"Bearer {key}")
    # Any other host: refused, and the key never leaves.
    monkeypatch.setenv("REMEMBER_API_URL", "https://attacker.test")
    with pytest.raises(StoredKeyRefused):
        ENTRY_POINTS[entry](issuer)
    assert all(
        request.url.host != "attacker.test" for request in issuer.engine_requests()
    )


# --- host resolution ---------------------------------------------------------


def test_signed_key_resolves_its_deployment(issuer: FakeIssuer) -> None:
    key = make_key()
    with Client(api_key=key, transport=issuer.transport()) as client:
        client.list_operations()
    assert _last_engine_call(issuer) == (f"{DEPLOYMENT_A}/operations", f"Bearer {key}")
    with Client(api_key=key, project="notes", transport=issuer.transport()) as client:
        client.list_operations()
    assert _last_engine_call(issuer) == (f"{DEPLOYMENT_B}/operations", f"Bearer {key}")


def test_construction_makes_no_network_call(issuer: FakeIssuer) -> None:
    Client(api_key=make_key(), transport=issuer.transport()).close()
    assert issuer.requests == []


def test_project_outside_the_key_is_refused(issuer: FakeIssuer) -> None:
    key = make_key(projects=["p-docs"])
    with Client(api_key=key, project="p-other", transport=issuer.transport()) as client:
        with pytest.raises(ProjectResolutionError, match="does not cover"):
            client.list_operations()
    assert issuer.engine_requests() == []


def test_org_wide_key_covers_any_project(issuer: FakeIssuer) -> None:
    key = make_key(projects="org:*")
    with Client(api_key=key, project="p-other", transport=issuer.transport()) as client:
        client.list_operations()
    assert _last_engine_call(issuer)[0] == f"{DEPLOYMENT_B}/operations"


def test_unknown_project_names_project_and_issuer(issuer: FakeIssuer) -> None:
    with Client(
        api_key=make_key(), project="nope", transport=issuer.transport()
    ) as client:
        with pytest.raises(ProjectResolutionError) as caught:
            client.list_operations()
    assert "'nope'" in str(caught.value) and ISSUER in str(caught.value)


@pytest.mark.parametrize(
    "api_url", ["http://dp.example.test", "ftp://dp.example.test", "not a url"]
)
def test_insecure_resolved_url_is_refused(issuer: FakeIssuer, api_url: str) -> None:
    issuer.projects[None] = ("p-docs", "docs", api_url)
    with Client(api_key=make_key(), transport=issuer.transport()) as client:
        with pytest.raises(ProjectResolutionError):
            client.list_operations()
    assert issuer.engine_requests() == []


def test_unreachable_issuer_never_falls_back_to_localhost(issuer: FakeIssuer) -> None:
    issuer.metadata_status = 503
    with Client(api_key=make_key(), transport=issuer.transport()) as client:
        with pytest.raises(ProjectResolutionError, match=ISSUER):
            client.list_operations()
    assert issuer.engine_requests() == []


def test_host_cache_ttl(issuer: FakeIssuer) -> None:
    key = make_key()
    claims = signed_key_claims(key)
    assert claims is not None
    now = [1000.0]
    http = issuer.http()
    for _ in range(3):
        resolve_project(
            key=key, claims=claims, project=None, http=http, clock=lambda: now[0]
        )
    assert len(issuer.calls("/api/v1/keys/self/project")) == 1
    now[0] += HOST_CACHE_TTL_SECONDS + 1
    resolve_project(
        key=key, claims=claims, project=None, http=http, clock=lambda: now[0]
    )
    assert len(issuer.calls("/api/v1/keys/self/project")) == 2
    # Cached per project: another project is its own entry.
    resolve_project(
        key=key, claims=claims, project="notes", http=http, clock=lambda: now[0]
    )
    assert len(issuer.calls("/api/v1/keys/self/project")) == 3


def _moved(issuer: FakeIssuer, first_outcome: int | Exception) -> None:
    """The first request to dp-a fails; the issuer now answers dp-b."""
    issuer.engines[DEPLOYMENT_A] = [first_outcome]

    def move() -> None:
        for name in (None, "p-docs", "docs"):
            issuer.projects[name] = ("p-docs", "docs", DEPLOYMENT_B)

    # Move once the first resolution has been served.
    original = issuer.handle

    def handle(request: httpx.Request) -> httpx.Response:
        response = original(request)
        if request.url.path == "/api/v1/keys/self/project":
            move()
        return response

    issuer.handle = handle  # type: ignore[method-assign]


@pytest.mark.parametrize(
    "first_outcome",
    [421, 404, httpx.ConnectError("refused"), httpx.ConnectTimeout("slow")],
    ids=["421", "non-engine-404", "connection-error", "connect-timeout"],
)
def test_moved_deployment_is_re_resolved_and_retried_once(
    issuer: FakeIssuer, first_outcome: int | Exception
) -> None:
    _moved(issuer, first_outcome)
    with Client(api_key=make_key(), transport=issuer.transport()) as client:
        assert client.list_operations() == ()
    hosts = [request.url.host for request in issuer.engine_requests()]
    assert hosts == ["dp-a.test", "dp-b.test"]
    assert len(issuer.calls("/api/v1/keys/self/project")) == 2


def _write(client: Client, kind: str) -> None:
    if kind == "ingest":
        client.ingest(content=b"# note", filename="note.md")
    elif kind == "delete":
        client.delete_document(doc_id=UUID(int=7))
    else:
        client.add_connector(connector=ConnectorCreate(kind="k", name="n"))


@pytest.mark.parametrize("write", ["ingest", "delete", "connector"])
@pytest.mark.parametrize(
    "failure",
    [httpx.ReadError("reset"), httpx.WriteError("broken pipe"), 404],
    ids=["read-error", "write-error", "non-engine-404"],
)
def test_a_write_that_may_have_arrived_is_never_repeated(
    issuer: FakeIssuer, write: str, failure: int | Exception
) -> None:
    _moved(issuer, failure)
    with Client(api_key=make_key(), transport=issuer.transport()) as client:
        with pytest.raises(MemoryApiError):
            _write(client, write)
    assert [r.url.host for r in issuer.engine_requests()] == ["dp-a.test"]


@pytest.mark.parametrize("write", ["ingest", "delete", "connector"])
@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("refused"), httpx.ConnectTimeout("slow"), 421],
    ids=["connect-error", "connect-timeout", "421"],
)
def test_a_write_that_never_arrived_moves_with_the_deployment(
    issuer: FakeIssuer, write: str, failure: int | Exception
) -> None:
    _moved(issuer, failure)
    with Client(api_key=make_key(), transport=issuer.transport()) as client:
        with pytest.raises(MemoryApiError):  # the fake engine 404s the write
            _write(client, write)
    assert [r.url.host for r in issuer.engine_requests()] == ["dp-a.test", "dp-b.test"]


def test_reads_retry_after_any_network_error(issuer: FakeIssuer) -> None:
    _moved(issuer, httpx.ReadError("reset"))
    with Client(api_key=make_key(), transport=issuer.transport()) as client:
        assert client.list_operations() == ()
    assert [r.url.host for r in issuer.engine_requests()] == ["dp-a.test", "dp-b.test"]


def test_re_resolution_keeps_the_first_project(issuer: FakeIssuer) -> None:
    """A moved-host retry asks for the same project id, not the new default."""
    issuer.engines[DEPLOYMENT_A] = [421]
    original = issuer.handle

    def handle(request: httpx.Request) -> httpx.Response:
        response = original(request)
        if request.url.path == "/api/v1/keys/self/project":
            issuer.projects[None] = ("p-notes", "notes", DEPLOYMENT_B)
            issuer.projects["p-docs"] = ("p-docs", "docs", "https://dp-c.test")
        return response

    issuer.handle = handle  # type: ignore[method-assign]
    with Client(api_key=make_key(), transport=issuer.transport()) as client:
        assert client.list_operations() == ()
    resolutions = issuer.calls("/api/v1/keys/self/project")
    assert [r.url.params.get("project") for r in resolutions] == [None, "p-docs"]
    assert [r.url.host for r in issuer.engine_requests()] == ["dp-a.test", "dp-c.test"]


def test_concurrent_first_requests_share_one_resolution(issuer: FakeIssuer) -> None:
    """Two threads racing the first request resolve once and agree.

    The issuer's default changes between what the two threads would each see;
    the first resolution must win for both (project id and URL together).
    """
    import threading

    second_started = threading.Event()
    original = issuer.handle

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/keys/self/project":
            second_started.wait(0.5)  # let the other thread reach resolution
            response = original(request)
            issuer.projects[None] = ("p-notes", "notes", DEPLOYMENT_B)
            return response
        return original(request)

    issuer.handle = handle  # type: ignore[method-assign]
    client = Client(api_key=make_key(), transport=issuer.transport())
    errors: list[BaseException] = []

    def call() -> None:
        if threading.current_thread().name == "second":
            second_started.set()
        try:
            client.list_operations()
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    first = threading.Thread(target=call, name="first")
    second = threading.Thread(target=call, name="second")
    first.start()
    second.start()
    first.join(5)
    second.join(5)
    client.close()
    assert errors == []
    assert len(issuer.calls("/api/v1/keys/self/project")) == 1
    assert [r.url.host for r in issuer.engine_requests()] == ["dp-a.test"] * 2
    assert client._route is not None
    assert client._route._pinned == ("p-docs", DEPLOYMENT_A)


def test_read_timeout_is_not_a_moved_deployment(issuer: FakeIssuer) -> None:
    _moved(issuer, httpx.ReadTimeout("slow"))
    with Client(api_key=make_key(), transport=issuer.transport()) as client:
        with pytest.raises(MemoryApiError):
            client.list_operations()
    assert len(issuer.engine_requests()) == 1


def test_engine_404_does_not_re_resolve(issuer: FakeIssuer) -> None:
    with Client(api_key=make_key(), transport=issuer.transport()) as client:
        with pytest.raises(MemoryApiError) as caught:
            client.connector_status(connector_id=UUID(int=1))
    assert caught.value.status_code == 404
    assert len(issuer.calls("/api/v1/keys/self/project")) == 1


def test_same_url_after_re_resolution_surfaces_the_original_error(
    issuer: FakeIssuer,
) -> None:
    issuer.engines[DEPLOYMENT_A] = [421]
    with Client(api_key=make_key(), transport=issuer.transport()) as client:
        with pytest.raises(MemoryApiError) as caught:
            client.list_operations()
    assert caught.value.status_code == 421
    assert len(issuer.engine_requests()) == 1


def test_retry_happens_once_never_in_a_loop(issuer: FakeIssuer) -> None:
    _moved(issuer, 421)
    issuer.engines[DEPLOYMENT_B] = [421, 421]
    with Client(api_key=make_key(), transport=issuer.transport()) as client:
        with pytest.raises(MemoryApiError):
            client.list_operations()
    assert len(issuer.engine_requests()) == 2


def test_configured_url_is_never_re_resolved(issuer: FakeIssuer) -> None:
    issuer.engines["http://file.test"] = [421]
    with Client(
        api_key="k", base_url="http://file.test", transport=issuer.transport()
    ) as client:
        with pytest.raises(MemoryApiError):
            client.list_operations()
    assert issuer.calls("/.well-known/oauth-authorization-server") == []


# --- rate limiting (D136 §7.6) -------------------------------------------------


def test_admission_refusal_is_a_typed_rate_limited(issuer: FakeIssuer) -> None:
    issuer.engines["http://engine.test"] = [429]
    with Client(
        api_key="k", base_url="http://engine.test", transport=issuer.transport()
    ) as client:
        with pytest.raises(RateLimited) as caught:
            client.list_operations()
    assert caught.value.status_code == 429
    assert caught.value.code == "rate_limited"
    assert caught.value.retry_after == 7.0
    assert isinstance(caught.value, MemoryApiError)
    assert len(issuer.engine_requests()) == 1  # never retried by the client


# --- client.account ------------------------------------------------------------


def test_account_needs_an_issuer(issuer: FakeIssuer) -> None:
    with Client(api_key="shared-secret", transport=issuer.transport()) as client:
        with pytest.raises(AccountApiUnavailable):
            client.account.whoami()


def test_account_needs_an_advertised_account_api(issuer: FakeIssuer) -> None:
    issuer.account_endpoint = False
    with Client(api_key=make_key(), transport=issuer.transport()) as client:
        with pytest.raises(AccountApiUnavailable, match="remember_account_endpoint"):
            client.account.whoami()


def test_account_calls_the_issuer_with_the_same_key(issuer: FakeIssuer) -> None:
    key = make_key(permissions=("memory:read", "account:read"))
    with Client(api_key=key, transport=issuer.transport()) as client:
        assert client.account.whoami()["role"] == "OWNER"
    call = issuer.calls("/api/v1/keys/self")[-1]
    assert str(call.url) == f"{ISSUER}/api/v1/keys/self"
    assert call.headers["Authorization"] == f"Bearer {key}"
    assert issuer.engine_requests() == []


def test_cloud_client_is_gone() -> None:
    import remember

    assert not hasattr(remember, "CloudClient")
    with pytest.raises(ImportError):
        exec("from remember.client import CloudClient")  # noqa: S102


def test_malformed_signed_key_is_not_treated_as_a_shared_secret() -> None:
    with pytest.raises(ValueError, match="signed key"):
        resolve_connection(api_key="rmb_eyJnotjson.eyJ.x", api_url="http://x.test")


def test_clear_host_cache_is_idempotent() -> None:
    clear_host_cache()
    clear_host_cache()
