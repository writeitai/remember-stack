"""Account route inventory exercised through the real SDK and CLI entry points."""

from __future__ import annotations

from dataclasses import dataclass
import json
from uuid import UUID

import httpx
from pydantic import SecretStr
import pytest

from remember import AccountApi
from remember import AccountApiUnavailable
from remember import AgentPermissionDisabled
from remember import Client
from remember import MemoryApiError
from remember import resolve_connection
from remember.cli import main
from remember.credentials import load_credentials
from remember.credentials import StoredCredentials
from remember.credentials import write_credentials
from tests.surfaces.fake_issuer import ISSUER
from tests.surfaces.fake_issuer import make_key

ACCOUNT = f"{ISSUER}/app/api"
TOKEN = make_key(projects="org:*", permissions=("account:read", "account:manage"))
NEW_SECRET = "rmb_fixture_one_time_secret"
CHECKOUT_URL = "https://payments.test/checkout/session"
SETTINGS_URL = f"{ISSUER}/app/settings/agent-permissions"
PROJECT = {
    "id": "p-notes",
    "org_id": "o1",
    "name": "Research",
    "monthly_subcap_credits": "25.00",
    "activation_state": "awaiting_payment",
    "deployment": None,
}
CHECKOUT = {
    "project": PROJECT,
    "checkout": {
        "checkout_session_id": "checkout-fixture",
        "url": CHECKOUT_URL,
        "amount_credits": "10.00",
        "client_secret": None,
    },
    "requires_human_action": True,
}
KEY = {"id": "k-notes", "name": "Research memory", "state": "active"}
CREATE_KEY_ARGS = {
    "name": "Research memory",
    "projects": ["p-notes"],
    "permissions": ["memory:read", "memory:write"],
    "default_project_id": "p-notes",
    "lifetime_days": 30,
}
DISABLED = {
    "code": "agent_permission_disabled",
    "message": "Project creation is disabled for API keys.",
    "action": "Turn on project creation in Settings → Agent permissions.",
    "settings_url": SETTINGS_URL,
}


@dataclass(frozen=True)
class AccountRoute:
    sdk: str
    options: dict[str, object]
    argv: tuple[str, ...]
    method: str
    path: str
    result: object
    body: dict[str, object] | None = None
    status: int = 200
    idempotent_post: bool = False


ROUTES = (
    AccountRoute(
        "list_projects",
        {},
        ("projects", "list"),
        "GET",
        "/projects",
        {"projects": [PROJECT], "project_limit": 4, "slots_used": 2, "is_owner": True},
    ),
    AccountRoute(
        "get_project",
        {"project_id": "p-notes"},
        ("projects", "show", "p-notes"),
        "GET",
        "/projects/p-notes",
        PROJECT,
    ),
    AccountRoute(
        "create_project",
        {"name": "Research", "monthly_subcap_credits": "25.00"},
        ("projects", "create", "--name", "Research", "--monthly-cap", "25.00"),
        "POST",
        "/projects",
        CHECKOUT,
        {"name": "Research", "monthly_subcap_credits": "25.00"},
        idempotent_post=True,
    ),
    AccountRoute(
        "set_project_spend_cap",
        {"project_id": "p-notes", "monthly_subcap_credits": "25.00"},
        ("projects", "set-cap", "p-notes", "--monthly-cap", "25.00"),
        "PUT",
        "/projects/p-notes/spend-cap",
        PROJECT,
        {"monthly_subcap_credits": "25.00"},
    ),
    AccountRoute(
        "start_activation_checkout",
        {"project_id": "p-notes"},
        ("projects", "activate", "p-notes"),
        "POST",
        "/projects/p-notes/activation-checkout",
        CHECKOUT,
        idempotent_post=True,
    ),
    AccountRoute("list_keys", {}, ("keys", "list"), "GET", "/keys", [KEY]),
    AccountRoute(
        "create_key",
        CREATE_KEY_ARGS,
        (
            "keys",
            "create",
            "--name",
            "Research memory",
            "--project",
            "p-notes",
            "--permission",
            "memory:read",
            "--permission",
            "memory:write",
            "--default-project",
            "p-notes",
            "--lifetime-days",
            "30",
        ),
        "POST",
        "/keys",
        {**KEY, "key": NEW_SECRET},
        CREATE_KEY_ARGS,
        status=201,
    ),
    AccountRoute(
        "revoke_key",
        {"key_id": "k-notes"},
        ("keys", "revoke", "k-notes"),
        "DELETE",
        "/keys/k-notes",
        {**KEY, "state": "revoked"},
    ),
)


class FakeAccount:
    def __init__(self, result: object, *, status: int = 200) -> None:
        self.result = result
        self.status = status
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self.respond)

    def respond(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == httpx.URL(ISSUER).host
        if request.url.path == "/.well-known/oauth-authorization-server":
            assert "Authorization" not in request.headers
            return httpx.Response(
                200, json={"issuer": ISSUER, "remember_account_endpoint": ACCOUNT}
            )
        self.requests.append(request)
        return httpx.Response(self.status, json=self.result)

    def install_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        write_credentials(
            credentials=StoredCredentials(
                version=2, issuer=ISSUER, key=SecretStr(TOKEN)
            )
        )
        monkeypatch.setattr(
            "remember.cli._issuer_http", lambda: httpx.Client(transport=self.transport)
        )


def _assert_wire(route: AccountRoute, request: httpx.Request) -> None:
    assert request.method == route.method
    assert str(request.url) == f"{ACCOUNT}/v1/orgs/o1{route.path}"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    if route.body is None:
        assert request.content == b""
        assert "content-type" not in request.headers
    else:
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content) == route.body
    if route.idempotent_post:
        assert UUID(request.headers["idempotency-key"]).version == 4
    else:
        assert "idempotency-key" not in request.headers


@pytest.mark.parametrize("route", ROUTES, ids=lambda route: route.sdk)
def test_sdk_calls_account_route(route: AccountRoute) -> None:
    fake = FakeAccount(route.result, status=route.status)
    with Client(api_key=TOKEN, transport=fake.transport) as client:
        assert getattr(client.account, route.sdk)(**route.options) == route.result
    (request,) = fake.requests
    _assert_wire(route, request)


@pytest.mark.parametrize("route", ROUTES, ids=lambda route: route.sdk)
def test_cli_calls_account_route(
    route: AccountRoute,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeAccount(route.result, status=route.status)
    fake.install_cli(monkeypatch)
    assert main([*route.argv, "--json"]) == 0
    out, err = capsys.readouterr()
    assert json.loads(out) == route.result
    assert not err
    if route.sdk == "create_key":
        assert out.count(NEW_SECRET) == 1
        stored = load_credentials()
        assert stored and stored.key and stored.key.get_secret_value() == TOKEN
    (request,) = fake.requests
    _assert_wire(route, request)


@pytest.mark.parametrize(
    "route",
    [route for route in ROUTES if route.idempotent_post],
    ids=lambda route: route.sdk,
)
def test_checkout_key_is_reused_when_supplied(
    route: AccountRoute,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeAccount(CHECKOUT)
    fake.install_cli(monkeypatch)
    with Client(api_key=TOKEN, transport=fake.transport) as client:
        getattr(client.account, route.sdk)(
            **route.options, idempotency_key="same-attempt"
        )
    assert main([*route.argv, "--idempotency-key", "same-attempt"]) == 0
    first, second = fake.requests
    assert (
        first.headers["idempotency-key"]
        == second.headers["idempotency-key"]
        == "same-attempt"
    )
    assert first.content == second.content
    out, err = capsys.readouterr()
    assert "Project: Research (p-notes)" in out
    assert f"Checkout: {CHECKOUT_URL}" in out
    assert "A human must complete payment at this URL." in out
    assert not err


def test_cli_clears_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeAccount({**PROJECT, "monthly_subcap_credits": None})
    fake.install_cli(monkeypatch)
    assert main(["projects", "set-cap", "p-notes", "--clear"]) == 0
    (request,) = fake.requests
    assert json.loads(request.content) == {"monthly_subcap_credits": None}


def test_cli_prints_key_secret_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeAccount({**KEY, "key": NEW_SECRET}, status=201)
    fake.install_cli(monkeypatch)
    assert main(["keys", "create", "--name", "Research memory", "--all-projects"]) == 0
    out, err = capsys.readouterr()
    assert out == f"{NEW_SECRET}\n"
    assert not err
    (request,) = fake.requests
    assert json.loads(request.content) == {
        "name": "Research memory",
        "projects": "org:*",
    }


def test_account_refusal_carries_redacted_action_and_settings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    action = f"{DISABLED['action']} {TOKEN}"
    fake = FakeAccount({"detail": {**DISABLED, "action": action}}, status=403)
    fake.install_cli(monkeypatch)
    with Client(api_key=TOKEN, transport=fake.transport) as client:
        with pytest.raises(AgentPermissionDisabled) as caught:
            client.account.create_project(name="Research")
    assert caught.value.status_code == 403
    assert caught.value.code == DISABLED["code"]
    assert caught.value.detail == DISABLED["message"]
    assert caught.value.action == f"{DISABLED['action']} [redacted]"
    assert caught.value.settings_url == SETTINGS_URL
    argv = ["projects", "create", "--name", "Research"]
    assert main(argv) == 1
    out, err = capsys.readouterr()
    assert not out and TOKEN not in err
    assert DISABLED["action"] in err and SETTINGS_URL in err
    assert main(["--json", *argv]) == 1
    out, err = capsys.readouterr()
    assert not out
    diagnostic = json.loads(err)["error"]
    assert diagnostic["code"] == DISABLED["code"]
    assert diagnostic["status_code"] == 403 and diagnostic["retryable"] is False
    assert (
        diagnostic["agent_action"] == f"{DISABLED['action']} [redacted]\n{SETTINGS_URL}"
    )


def test_owner_only_refusal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeAccount(
        {
            "detail": {
                "code": "owner_only",
                "message": "Project writes require an owner.",
            }
        },
        status=403,
    )
    fake.install_cli(monkeypatch)
    with Client(api_key=TOKEN, transport=fake.transport) as client:
        with pytest.raises(MemoryApiError) as caught:
            client.account.set_project_spend_cap(
                project_id="p-notes", monthly_subcap_credits=None
            )
    assert caught.value.status_code == 403 and caught.value.code == "owner_only"
    assert main(["projects", "set-cap", "p-notes", "--clear", "--json"]) == 1
    out, err = capsys.readouterr()
    assert not out
    diagnostic = json.loads(err)["error"]
    assert diagnostic["code"] == "owner_only"
    assert "organisation owner" in diagnostic["agent_action"]


def test_account_validation_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeAccount(
        {
            "detail": [
                {
                    "loc": ["body", "monthly_subcap_credits"],
                    "msg": "must be nonnegative",
                }
            ]
        },
        status=422,
    )
    fake.install_cli(monkeypatch)
    with Client(api_key=TOKEN, transport=fake.transport) as client:
        with pytest.raises(MemoryApiError, match="must be nonnegative") as caught:
            client.account.create_project(name="Research", monthly_subcap_credits="-1")
    assert caught.value.status_code == 422
    assert (
        main(
            [
                "projects",
                "create",
                "--name",
                "Research",
                "--monthly-cap",
                "-1",
                "--json",
            ]
        )
        == 1
    )
    out, err = capsys.readouterr()
    assert not out
    diagnostic = json.loads(err)["error"]
    assert diagnostic["status_code"] == 422
    assert "must be nonnegative" in diagnostic["detail"]


@pytest.mark.parametrize(
    "argv",
    [
        ("projects", "create"),
        ("projects", "set-cap", "p-notes"),
        ("projects", "set-cap", "p-notes", "--clear", "--monthly-cap", "25.00"),
        (
            "keys",
            "create",
            "--name",
            "Memory",
            "--all-projects",
            "--project",
            "p-notes",
        ),
    ],
)
def test_argument_validation_requires_no_account_call(
    argv: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeAccount({})
    fake.install_cli(monkeypatch)
    assert main([*argv, "--json"]) == 2
    out, err = capsys.readouterr()
    assert not out and not fake.requests
    assert json.loads(err)["error"]["code"] == "invalid_arguments"


def test_sdk_rejects_bad_retry_key_and_traversing_id() -> None:
    fake = FakeAccount({})
    with Client(api_key=TOKEN, transport=fake.transport) as client:
        for invalid in ("", "a" * 161, "bad\nheader", " attempt "):
            with pytest.raises(ValueError, match="Idempotency-Key"):
                client.account.create_project(name="Research", idempotency_key=invalid)
        with pytest.raises(ValueError, match="path segment"):
            client.account.start_activation_checkout(project_id="..")
    assert not fake.requests


def test_account_endpoint_must_share_issuer_origin() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{ISSUER}/.well-known/oauth-authorization-server"
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            json={
                "issuer": ISSUER,
                "remember_account_endpoint": "https://other.test/app/api",
            },
        )

    with Client(api_key=TOKEN, transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(AccountApiUnavailable, match="share the issuer origin"):
            client.account.create_project(name="Research")


def test_account_redirect_preserves_post_and_retry_key() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(
                200, json={"issuer": ISSUER, "remember_account_endpoint": ACCOUNT}
            )
        requests.append(request)
        if request.url.path.endswith("/activation-checkout"):
            return httpx.Response(302, headers={"Location": "/app/api/checkout"})
        assert request.url.path == "/app/api/checkout"
        return httpx.Response(200, json=CHECKOUT)

    with httpx.Client(
        transport=httpx.MockTransport(respond), follow_redirects=True
    ) as http:
        account = AccountApi(connection=resolve_connection(api_key=TOKEN), http=http)
        assert (
            account.start_activation_checkout(
                project_id="p-notes", idempotency_key="attempt"
            )
            == CHECKOUT
        )
    first, second = requests
    assert second.method == first.method == "POST"
    assert (
        second.headers["idempotency-key"]
        == first.headers["idempotency-key"]
        == "attempt"
    )
    assert second.content == first.content == b""


def test_cli_explicit_and_environment_key_override_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeAccount([])
    fake.install_cli(monkeypatch)
    env_key = make_key(jti="key-env")
    explicit_key = make_key(jti="key-explicit")
    monkeypatch.setenv("REMEMBER_API_KEY", env_key)
    monkeypatch.setenv("REMEMBER_ISSUER", "https://other.test")
    assert main(["keys", "list"]) == 0
    assert main(["keys", "list", "--api-key", f"Bearer {explicit_key}"]) == 0
    assert [r.headers["authorization"] for r in fake.requests] == [
        f"Bearer {env_key}",
        f"Bearer {explicit_key}",
    ]
