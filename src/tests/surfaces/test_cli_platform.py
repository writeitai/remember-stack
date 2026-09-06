"""Unit tests for the unified Remember platform CLI and setup bootstrapper (D108)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from pydantic import BaseModel
from pydantic import SecretStr
import pytest

from remember import RememberClient
from rememberstack.surfaces.cli import main
from rememberstack.surfaces.credentials import ControlPlaneCredentials
from rememberstack.surfaces.credentials import CredentialFile
from rememberstack.surfaces.credentials import ProjectCredentials
from rememberstack.surfaces.credentials import write_credentials
from rememberstack.surfaces.sdk import MemoryClient
from rememberstack.surfaces.setup import configure_antigravity
from rememberstack.surfaces.setup import configure_codex
from rememberstack.surfaces.setup import configure_cursor
from rememberstack.surfaces.setup import resolve_launcher


def test_remember_client_import_and_alias() -> None:
    """The canonical Remember package exports RememberClient aliasing MemoryClient."""
    assert RememberClient is MemoryClient
    client = RememberClient(base_url="http://localhost:8000")
    assert hasattr(client, "open_query")
    assert hasattr(client, "query_sql")
    client.close()


def test_resolve_launcher_finds_remember_or_uvx() -> None:
    """Durable launcher resolution returns canonical absolute paths and arguments."""
    cmd, args = resolve_launcher()
    assert Path(cmd).is_absolute()
    assert args in (["mcp"], ["remember", "mcp"])


def test_setup_cursor_configuration(tmp_path: Path) -> None:
    """Cursor configuration writes .cursor/mcp.json and .cursor/rules/remember.mdc."""
    cmd, args = "/usr/local/bin/remember", ["mcp"]
    ok = configure_cursor(
        cwd=tmp_path, launcher_cmd=cmd, launcher_args=args, dry_run=False
    )
    assert ok is True

    mcp_file = tmp_path / ".cursor" / "mcp.json"
    rule_file = tmp_path / ".cursor" / "rules" / "remember.mdc"

    assert mcp_file.is_file()
    assert rule_file.is_file()

    mcp_data = json.loads(mcp_file.read_text(encoding="utf-8"))
    assert mcp_data["mcpServers"]["remember"]["command"] == cmd
    assert mcp_data["mcpServers"]["remember"]["args"] == args

    rule_text = rule_file.read_text(encoding="utf-8")
    assert "fact_context" in rule_text
    assert "bitemporal" in rule_text


def test_setup_antigravity_configuration(tmp_path: Path) -> None:
    """Antigravity configuration writes mcp_config.json and SKILL.md."""
    cmd, args = "/Users/dev/.local/bin/uvx", ["remember", "mcp"]
    ok = configure_antigravity(
        cwd=tmp_path, launcher_cmd=cmd, launcher_args=args, dry_run=False
    )
    assert ok is True

    mcp_file = tmp_path / ".agents" / "mcp_config.json"
    skill_file = tmp_path / ".agents" / "skills" / "remember" / "SKILL.md"

    assert mcp_file.is_file()
    assert skill_file.is_file()

    mcp_data = json.loads(mcp_file.read_text(encoding="utf-8"))
    assert mcp_data["mcpServers"]["remember"]["command"] == cmd

    skill_text = skill_file.read_text(encoding="utf-8")
    assert "name: remember" in skill_text


def test_setup_codex_configuration(tmp_path: Path) -> None:
    """Codex configuration writes .codex/config.toml."""
    cmd, args = "/usr/local/bin/remember", ["mcp"]
    ok = configure_codex(
        cwd=tmp_path, launcher_cmd=cmd, launcher_args=args, dry_run=False
    )
    assert ok is True

    config_file = tmp_path / ".codex" / "config.toml"
    assert config_file.is_file()
    content = config_file.read_text(encoding="utf-8")
    assert "[mcp_servers.remember]" in content
    assert cmd in content


def test_setup_codex_configuration_strips_legacy_plaintext_token(
    tmp_path: Path,
) -> None:
    """Existing plaintext tokens in config.toml are stripped on reconfiguration."""
    config_dir = tmp_path / ".codex"
    config_dir.mkdir(parents=True, exist_ok=True)
    legacy_toml = (
        "# User settings\n"
        "[mcp_servers.remember]\n"
        'command = "/legacy/path/remember"\n'
        'args = ["mcp"]\n'
        "[mcp_servers.remember.env]\n"
        'REMEMBER_TOKEN = "umc_dp_plaintext_secret"\n'
        'OTHER_VAR = "keep_me"\n'
    )
    (config_dir / "config.toml").write_text(legacy_toml, encoding="utf-8")

    cmd, args = "/usr/local/bin/remember", ["mcp"]
    ok = configure_codex(
        cwd=tmp_path, launcher_cmd=cmd, launcher_args=args, dry_run=False
    )
    assert ok is True

    new_content = (config_dir / "config.toml").read_text(encoding="utf-8")
    assert "umc_dp_plaintext_secret" not in new_content
    assert "/legacy/path/remember" not in new_content
    assert "/usr/local/bin/remember" in new_content


def test_setup_cli_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI setup --dry-run prints plan without writing files."""
    code = main(["setup", "--dry-run"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Mode: DRY RUN" in out
    assert "[dry-run]" in out


def test_doctor_cli_reports_health(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI doctor checks binaries, credentials, data plane, and harnesses."""
    code = main(["doctor"])
    out = capsys.readouterr().out
    assert "Remember Doctor" in out
    assert "binary found" in out or "not found" in out
    assert code in (0, 1)


def test_whoami_self_hosted(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI whoami --self-hosted prints honest local engine notice."""
    code = main(["whoami", "--self-hosted"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Identity: self-hosted (local)" in out
    assert "cloud-managed services on remember.dev" in out


def test_balance_self_hosted(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI balance --self-hosted rejects cloud billing with exit 1."""
    code = main(["balance", "--self-hosted"])
    assert code == 1
    err = capsys.readouterr().err
    assert "cloud-managed services on remember.dev" in err


def test_projects_list_self_hosted(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI projects list --self-hosted prints single local namespace and exits 0."""
    code = main(["projects", "list", "--self-hosted"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Self-hosted engine operates in a single local project namespace" in out


def test_projects_create_self_hosted(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI projects create --self-hosted rejects multi-tenant creation with exit 1."""
    code = main(["projects", "create", "my-project", "--self-hosted"])
    assert code == 1
    err = capsys.readouterr().err
    assert (
        "Multi-tenant project provisioning is not supported on a self-hosted engine"
        in err
    )


def test_members_list_self_hosted(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI members list --self-hosted rejects team seat management with exit 1."""
    code = main(["members", "list", "--self-hosted"])
    assert code == 1
    err = capsys.readouterr().err
    assert "cloud-managed services on remember.dev" in err


def test_members_invite_self_hosted(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI members invite --self-hosted rejects team invitations with exit 1."""
    code = main(["members", "invite", "teammate@example.com", "--self-hosted"])
    assert code == 1
    err = capsys.readouterr().err
    assert "cloud-managed services on remember.dev" in err


def test_review_and_budget_retirement(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI review, budget, and ops print clear retirement notices and exit 1."""
    dummy_dep = "74000000-0000-0000-0000-000000000001"
    res_review = main(["review", "list", "--deployment", dummy_dep])
    assert res_review == 1
    err_review = capsys.readouterr().err
    assert "remember review' is retired" in err_review

    res_budget = main(["budget", "inspect", "--deployment", dummy_dep])
    assert res_budget == 1
    err_budget = capsys.readouterr().err
    assert "remember budget' is retired" in err_budget

    res_ops = main(["ops", "inspect", "--deployment", dummy_dep])
    assert res_ops == 1
    err_ops = capsys.readouterr().err
    assert "remember ops' is confined to internal container environments" in err_ops


def test_structured_credentials_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test whoami, projects, switch, and balance with structured credentials."""
    config_dir = tmp_path / "remember-cfg"
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(config_dir))

    pid_1 = "0191-proj-alpha"
    pid_2 = "0191-proj-beta"
    cred = CredentialFile(
        version=1,
        control_plane=ControlPlaneCredentials(
            url="https://api.remember.dev",
            access_token=SecretStr("umc_usr_test123"),
            org_id="0191-org-test",
            user_id="0191-usr-test",
            email="developer@example.com",
        ),
        active_project_id=pid_1,
        projects={
            pid_1: ProjectCredentials(
                name="production",
                data_plane_url="https://alpha.dp.remember.dev",
                data_plane_token=SecretStr("umc_dp_alpha"),
            ),
            pid_2: ProjectCredentials(
                name="staging",
                data_plane_url="https://beta.dp.remember.dev",
                data_plane_token=SecretStr("umc_dp_beta"),
            ),
        },
    )
    write_credentials(credential=cred)
    assert cred.control_plane is not None
    org_id = cred.control_plane.org_id

    recorded_requests: list[httpx.Request] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        url_path = request.url.path
        if url_path in ("/v1/projects", f"/v1/orgs/{org_id}/projects"):
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "projects": [
                            {
                                "id": pid_1,
                                "name": "production",
                                "activation_state": "ready",
                            },
                            {
                                "id": pid_2,
                                "name": "staging",
                                "activation_state": "ready",
                            },
                        ]
                    },
                )
            elif request.method == "POST":
                payload = json.loads(request.content.decode("utf-8"))
                return httpx.Response(
                    201,
                    json={
                        "project": {
                            "id": "0191-proj-test",
                            "name": payload.get("name", "test-project"),
                            "deployment": {
                                "data_plane_hostname": "test.dp.remember.dev"
                            },
                        },
                        "checkout": {
                            "checkout_url": "https://billing.stripe.com/checkout/123"
                        },
                    },
                )
        elif url_path in ("/v1/billing/balance", f"/v1/orgs/{org_id}/billing/status"):
            return httpx.Response(200, json={"balance": "24.50", "status": "Active"})
        elif url_path in ("/v1/org/members", f"/v1/orgs/{org_id}/members"):
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "email": "developer@example.com",
                            "role": "admin",
                            "status": "active",
                        }
                    ],
                )
            elif request.method == "POST":
                return httpx.Response(201, json={"status": "invited"})
        elif url_path in ("/v1/org/members/invite", f"/v1/orgs/{org_id}/invites"):
            return httpx.Response(201, json={"status": "invited"})
        return httpx.Response(404, json={"error": "not found"})

    orig_client = httpx.Client

    def mock_client(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(handle_request)
        return orig_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("rememberstack.surfaces.cli.httpx.Client", mock_client)

    # 1. Whoami
    assert main(["whoami"]) == 0
    out_whoami = capsys.readouterr().out
    assert "developer@example.com" in out_whoami
    assert pid_1 in out_whoami

    # 2. Projects list
    assert main(["projects", "list"]) == 0
    out_projects = capsys.readouterr().out
    assert "production" in out_projects
    assert "staging" in out_projects
    assert "*" in out_projects  # active marker

    # Test parent-level flags on projects:
    assert (
        main(["projects", "--api-url", "https://custom.dp.remember.dev", "list"]) == 0
    )
    out_projects_parent = capsys.readouterr().out
    assert "production" in out_projects_parent

    # 3. Projects create (administrative command directed to web console)
    assert main(["projects", "create", "test-project"]) == 1
    err_create = capsys.readouterr().err
    assert "https://remember.dev/app/projects" in err_create
    assert "test-project" in err_create

    # 4. Switch
    assert main(["switch", "staging"]) == 0
    out_switch = capsys.readouterr().out
    assert "Switched active project to 'staging'" in out_switch

    # Verify whoami now shows staging as active
    assert main(["whoami"]) == 0
    out_whoami_new = capsys.readouterr().out
    assert pid_2 in out_whoami_new

    # 5. Members list
    assert main(["members", "list"]) == 1
    err_members = capsys.readouterr().err
    assert "https://remember.dev/app/team" in err_members

    # 6. Members invite
    assert main(["members", "invite", "colleague@example.com"]) == 1
    err_invite = capsys.readouterr().err
    assert "https://remember.dev/app/team" in err_invite
    assert "colleague@example.com" in err_invite

    # 7. Balance
    assert main(["balance"]) == 0
    out_balance = capsys.readouterr().out
    assert "Current balance: €24.50 [Active]" in out_balance

    # Audience isolation verification:
    # 1. Balance request to control plane carried the control plane bearer token
    # 2. Never was any data-plane token sent to control plane
    assert len(recorded_requests) >= 1
    for req in recorded_requests:
        assert req.headers.get("authorization") == "Bearer umc_usr_test123"
        content_str = req.content.decode("utf-8", errors="ignore")
        assert "umc_dp_alpha" not in content_str
        assert "umc_dp_beta" not in content_str


def test_control_plane_error_handling_and_audience_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Control plane HTTP errors and unauthenticated state handle gracefully."""
    config_dir = tmp_path / "remember-cfg-err"
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(config_dir))

    # Case A: Not authenticated at all with control plane
    # balance prints guidance pointing to cloud console and exits 1
    assert main(["balance"]) == 1
    assert "https://remember.dev/app/billing" in capsys.readouterr().err

    # projects list when no credentials at all exits 1 with login guidance
    assert main(["projects", "list"]) == 1
    assert "Run 'remember login'" in capsys.readouterr().err

    # members list prints guidance pointing to cloud console and exits 1
    assert main(["members", "list"]) == 1
    assert "https://remember.dev/app/team" in capsys.readouterr().err

    # projects create and members invite provide console guidance and exit 1
    assert main(["projects", "create", "fail-proj"]) == 1
    assert "https://remember.dev/app/projects" in capsys.readouterr().err

    assert main(["members", "invite", "test@example.com"]) == 1
    assert "https://remember.dev/app/team" in capsys.readouterr().err

    # Case B: Authenticated, but control plane returns 401 Unauthorized
    cred = CredentialFile(
        version=1,
        control_plane=ControlPlaneCredentials(
            url="https://api.remember.dev",
            access_token=SecretStr("umc_usr_expired"),
            org_id="0191-org-test",
            user_id="0191-usr-test",
            email="developer@example.com",
        ),
        active_project_id="0191-proj-alpha",
        projects={
            "0191-proj-alpha": ProjectCredentials(
                name="production",
                data_plane_url="https://alpha.dp.remember.dev",
                data_plane_token=SecretStr("umc_dp_alpha"),
            )
        },
    )
    write_credentials(credential=cred)

    def mock_err_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": "unauthorized", "detail": "Token expired"}
        )

    orig_client = httpx.Client

    def mock_err_client(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(mock_err_handler)
        return orig_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("rememberstack.surfaces.cli.httpx.Client", mock_err_client)

    assert main(["balance"]) == 1
    err_balance = capsys.readouterr().err
    assert "HTTP 401" in err_balance


def test_data_plane_token_origin_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ambient data-plane tokens are strictly bound to the stored origin (Finding 2)."""
    config_dir = tmp_path / "remember-cfg-origin"
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(config_dir))

    cred = CredentialFile(
        version=1,
        active_project_id="prj_alpha",
        projects={
            "prj_alpha": ProjectCredentials(
                name="production",
                data_plane_url="https://alpha.dp.remember.dev",
                data_plane_token=SecretStr("umc_dp_alpha_secret"),
            )
        },
    )
    write_credentials(credential=cred)

    # 1. SDK: programmatic client strictly follows D92 and never reads ambient credential file
    client_alpha = RememberClient(api_url="https://alpha.dp.remember.dev")
    assert "Authorization" not in client_alpha._client.headers
    client_alpha.close()

    client_unrelated = RememberClient(api_url="https://unrelated.example.com")
    assert "Authorization" not in client_unrelated._client.headers
    client_unrelated.close()

    # 2. CLI: _cli_memory_client resolves ambient project credentials for matching origin
    import argparse

    from rememberstack.surfaces.cli import _cli_memory_client

    cli_matching = _cli_memory_client(
        argparse.Namespace(api_url="https://alpha.dp.remember.dev", token=None)
    )
    assert (
        cli_matching._client.headers.get("Authorization")
        == "Bearer umc_dp_alpha_secret"
    )
    cli_matching.close()

    # 3. Scheme downgrade: CLI client pointing to http when stored is https does not send token
    cli_downgrade = _cli_memory_client(
        argparse.Namespace(api_url="http://alpha.dp.remember.dev", token=None)
    )
    assert "Authorization" not in cli_downgrade._client.headers
    cli_downgrade.close()

    # 4. Explicit REMEMBER_DATA_PLANE_URL override prevents ambient token injection to other host in CLI
    monkeypatch.setenv("REMEMBER_DATA_PLANE_URL", "https://unrelated.example.com")
    cli_env_override = _cli_memory_client(argparse.Namespace())
    assert "Authorization" not in cli_env_override._client.headers
    cli_env_override.close()
    monkeypatch.delenv("REMEMBER_DATA_PLANE_URL")

    # 5. CLI: requests to unrelated origin do not leak ambient project token
    sent_headers: list[httpx.Headers] = []

    def mock_transport_fn(request: httpx.Request) -> httpx.Response:
        sent_headers.append(request.headers)
        return httpx.Response(
            200,
            json={
                "request_id": "00000000-0000-0000-0000-000000000000",
                "deployment_id": "00000000-0000-0000-0000-000000000000",
                "surface_manifest_hash": "0" * 64,
                "query_hash": "0" * 64,
                "limits": {
                    "row_cap": 100,
                    "byte_cap": 1000000,
                    "statement_timeout_ms": 5000,
                    "analytical_tier": False,
                },
                "execution_started_at": "2026-09-01T00:00:00Z",
                "elapsed_ms": 1.0,
                "termination_reason": "completed",
                "columns": [],
                "rows": [],
            },
        )

    orig_client = httpx.Client

    def mock_data_plane_client(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(mock_transport_fn)
        return orig_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "rememberstack.surfaces.cli.httpx.Client", mock_data_plane_client
    )

    code = main(["query", "sql", "SELECT 1", "--url", "https://unrelated.example.com"])
    assert code == 0
    assert len(sent_headers) == 1
    assert "authorization" not in sent_headers[0]


def test_doctor_token_origin_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Doctor never sends ambient project tokens to unverified or foreign origins."""
    config_dir = tmp_path / "remember-cfg-doctor"
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(config_dir))

    cred = CredentialFile(
        version=1,
        active_project_id="prj_alpha",
        projects={
            "prj_alpha": ProjectCredentials(
                name="production",
                data_plane_url="https://alpha.dp.remember.dev",
                data_plane_token=SecretStr("umc_dp_alpha_secret"),
            )
        },
    )
    write_credentials(credential=cred)

    sent_doctor_headers: list[httpx.Headers] = []

    def mock_doctor_transport(request: httpx.Request) -> httpx.Response:
        sent_doctor_headers.append(request.headers)
        return httpx.Response(
            200, json={"deployment_id": "00000000-0000-0000-0000-000000000000"}
        )

    orig_client = httpx.Client

    def mock_doc_client(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(mock_doctor_transport)
        return orig_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("rememberstack.surfaces.cli.httpx.Client", mock_doc_client)

    # 1. Unrelated origin: token omitted
    main(["doctor", "--url", "https://unrelated.example.com"])
    assert len(sent_doctor_headers) >= 1
    assert "authorization" not in sent_doctor_headers[0]

    # 2. Matching origin: token attached
    sent_doctor_headers.clear()
    main(["doctor", "--url", "https://alpha.dp.remember.dev"])
    assert len(sent_doctor_headers) >= 1
    assert sent_doctor_headers[0].get("authorization") == "Bearer umc_dp_alpha_secret"


def test_query_free_text_dispatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI query defaults to fact_context for free text and answer_context with --answer."""
    captured_calls: list[tuple[str, str]] = []

    class MockClient:
        def __enter__(self) -> "MockClient":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def fact_context(self, query: str) -> object:
            captured_calls.append(("fact", query))

            class MockEnvelope(BaseModel):
                operation: str
                facts: list[str]

            return MockEnvelope(operation="fact_context", facts=["test fact"])

        def answer_context(self, query: str) -> object:
            captured_calls.append(("answer", query))

            class MockBundle(BaseModel):
                operation: str
                query: str

            return MockBundle(operation="answer_context", query=query)

    monkeypatch.setattr(
        "rememberstack.surfaces.cli._cli_memory_client", lambda args: MockClient()
    )

    # 1. Bare free text query
    res = main(["query", "What were our decisions on auth?"])
    assert res == 0
    assert captured_calls[-1] == ("fact", "What were our decisions on auth?")
    assert "test fact" in capsys.readouterr().out

    # 2. Free text query with --answer
    res2 = main(["query", "How does indexing work?", "--answer"])
    assert res2 == 0
    assert captured_calls[-1] == ("answer", "How does indexing work?")
    assert "How does indexing work?" in capsys.readouterr().out


def test_query_free_text_with_preceding_flags(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Flags like --answer or --url can precede the free-text query string (Finding 8b)."""
    captured_calls: list[tuple[str, str]] = []

    class MockClient:
        def __enter__(self) -> "MockClient":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def fact_context(self, query: str) -> object:
            captured_calls.append(("fact", query))

            class MockEnvelope(BaseModel):
                operation: str
                facts: list[str]

            return MockEnvelope(operation="fact_context", facts=["test fact"])

        def answer_context(self, query: str) -> object:
            captured_calls.append(("answer", query))

            class MockBundle(BaseModel):
                operation: str
                query: str

            return MockBundle(operation="answer_context", query=query)

    monkeypatch.setattr(
        "rememberstack.surfaces.cli._cli_memory_client", lambda args: MockClient()
    )

    # Flag before query
    res = main(["query", "--answer", "How does indexing work?"])
    assert res == 0
    assert captured_calls[-1] == ("answer", "How does indexing work?")

    # URL flag before query
    res2 = main(
        [
            "query",
            "--api-url",
            "http://localhost:8000",
            "What were our decisions on auth?",
        ]
    )
    assert res2 == 0
    assert captured_calls[-1] == ("fact", "What were our decisions on auth?")


def test_logout_revokes_all_stored_project_and_control_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Logout revokes data-plane tokens for all projects and control plane before unlinking (Finding 4)."""
    config_dir = tmp_path / "remember-cfg-logout-multi"
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(config_dir))

    pid_1 = "0191-proj-alpha"
    pid_2 = "0191-proj-beta"
    cred = CredentialFile(
        version=1,
        control_plane=ControlPlaneCredentials(
            url="https://api.remember.dev",
            access_token=SecretStr("umc_usr_test123"),
            org_id="0191-org-test",
            user_id="0191-usr-test",
            email="developer@example.com",
        ),
        active_project_id=pid_1,
        projects={
            pid_1: ProjectCredentials(
                name="production",
                data_plane_url="https://alpha.dp.remember.dev",
                data_plane_token=SecretStr("umc_dp_alpha_secret"),
            ),
            pid_2: ProjectCredentials(
                name="staging",
                data_plane_url="https://beta.dp.remember.dev",
                data_plane_token=SecretStr("umc_dp_beta_secret"),
            ),
        },
    )
    write_credentials(credential=cred)

    revoked_tokens: list[str] = []

    def mock_revoke_transport(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            revoked_tokens.append(auth[len("Bearer ") :])
        return httpx.Response(200)

    orig_client = httpx.Client

    def mock_revoke_client(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(mock_revoke_transport)
        return orig_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("rememberstack.surfaces.cli.httpx.Client", mock_revoke_client)

    code = main(["logout"])
    assert code == 0
    # Both project tokens and the control plane token should have been revoked
    assert "umc_dp_alpha_secret" in revoked_tokens
    assert "umc_dp_beta_secret" in revoked_tokens
    assert "umc_usr_test123" in revoked_tokens
    # Credential file unlinked
    from rememberstack.surfaces.credentials import load_credentials

    assert load_credentials() is None


def test_doctor_warns_on_non_executable_configured_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Doctor verifies that configured agent harness commands are executable (Finding 8d)."""
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir(parents=True)
    mcp_config = cursor_dir / "mcp.json"
    mcp_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "remember": {
                        "command": "/nonexistent/binary/path/remember",
                        "args": ["mcp"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    main(["doctor"])
    out = capsys.readouterr().out
    assert "not found or not executable" in out


def test_login_does_not_revoke_predecessor_for_different_deployment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Authenticating a different project/deployment preserves existing project tokens (Finding 4)."""
    from uuid import UUID

    from tests.surfaces.test_login import _API
    from tests.surfaces.test_login import _grant_handler
    from tests.surfaces.test_login import _mock_client
    from tests.surfaces.test_login import _stored
    from tests.surfaces.test_login import _token_body
    from tests.surfaces.test_login import _TOKEN_HOST

    config_dir = tmp_path / "remember-cfg-login-diff"
    monkeypatch.setenv("REMEMBERSTACK_CONFIG_DIR", str(config_dir))

    # Existing deployment 1
    dep_1 = UUID("74000000-0000-0000-0000-000000000001")
    write_credentials(credential=_stored(deployment_id=dep_1))

    # New login returns deployment 2
    dep_2 = UUID("74000000-0000-0000-0000-000000000002")
    calls: list[str] = []
    _mock_client(
        monkeypatch,
        _grant_handler(token_body=_token_body(deployment_id=str(dep_2)), calls=calls),
    )

    assert main(["login", "--token-host", _TOKEN_HOST, "--api-url", _API]) == 0
    # Calls should only be authorize and token, NOT revoke!
    assert calls == ["authorize", "token"]


def test_self_hosted_logout_never_contacts_cloud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Self-hosted tokens are unlinked locally and NEVER transmitted to Remember Cloud (Finding 1)."""
    config_dir = tmp_path / "remember-cfg-sh-logout"
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(config_dir))

    # Setup self-hosted token
    assert (
        main(
            [
                "setup",
                "--self-hosted",
                "--token",
                "local_secret_token_12345",
                "--agent",
                "codex",
                "--dir",
                str(tmp_path),
            ]
        )
        == 0
    )

    cloud_requests: list[httpx.Request] = []

    def mock_cloud_transport(request: httpx.Request) -> httpx.Response:
        cloud_requests.append(request)
        return httpx.Response(200)

    orig_client = httpx.Client

    def mock_client_check(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(mock_cloud_transport)
        return orig_client(*args, **kwargs)

    monkeypatch.setattr("rememberstack.surfaces.cli.httpx.Client", mock_client_check)

    # Logout
    assert main(["logout"]) == 0
    # ZERO network requests should have been made to cloud!
    assert len(cloud_requests) == 0
    from rememberstack.surfaces.credentials import load_credentials

    assert load_credentials() is None


def test_multi_project_relogin_revokes_correct_predecessor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-logging into project A after switching revokes project A's old token (Finding 3)."""
    from uuid import UUID

    from tests.surfaces.test_login import _API
    from tests.surfaces.test_login import _mock_client
    from tests.surfaces.test_login import _token_body
    from tests.surfaces.test_login import _TOKEN_HOST

    config_dir = tmp_path / "remember-cfg-multi-relogin"
    monkeypatch.setenv("REMEMBERSTACK_CONFIG_DIR", str(config_dir))

    dep_a = UUID("74000000-0000-0000-0000-000000000001")
    dep_b = UUID("74000000-0000-0000-0000-000000000002")

    calls: list[str] = []
    current_token_body: dict[str, dict[str, object]] = {
        "val": _token_body(
            deployment_id=str(dep_a),
            access_token="token_a_1",
            token_id=str(UUID("11111111-1111-1111-1111-111111111111")),
        )
    }

    def dynamic_handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/v1/device/authorize":
            calls.append("authorize")
            return httpx.Response(
                200,
                json={
                    "device_code": "DEVICE-SECRET",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": f"{_TOKEN_HOST}/device",
                    "verification_uri_complete": f"{_TOKEN_HOST}/device?c=1",
                    "interval": 1,
                    "expires_in": 90,
                },
            )
        if p == "/v1/device/token":
            calls.append("token")
            return httpx.Response(200, json=current_token_body["val"])
        if p == "/v1/api-tokens/self":
            calls.append("revoke")
            return httpx.Response(200)
        return httpx.Response(404)

    _mock_client(monkeypatch, dynamic_handler)

    # 1. Login A
    assert main(["login", "--token-host", _TOKEN_HOST, "--api-url", _API]) == 0
    assert calls == ["authorize", "token"]

    # 2. Login B
    calls.clear()
    current_token_body["val"] = _token_body(
        deployment_id=str(dep_b),
        access_token="token_b_1",
        token_id=str(UUID("22222222-2222-2222-2222-222222222222")),
    )
    assert main(["login", "--token-host", _TOKEN_HOST, "--api-url", _API]) == 0
    assert calls == ["authorize", "token"]

    # 3. Switch back to project A
    assert main(["switch", str(dep_a)]) == 0

    # 4. Re-login project A: Must revoke token_a_1!
    calls.clear()
    current_token_body["val"] = _token_body(
        deployment_id=str(dep_a),
        access_token="token_a_2",
        token_id=str(UUID("33333333-3333-3333-3333-333333333333")),
    )
    assert main(["login", "--token-host", _TOKEN_HOST, "--api-url", _API]) == 0
    assert calls == ["authorize", "token", "revoke"]


def test_revocation_recovery_preserves_non_active_project_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pending revocation journal entries matching any active project are dropped, not revoked."""
    from uuid import UUID

    from rememberstack.surfaces.cli import _retry_pending_revocation
    from rememberstack.surfaces.credentials import append_pending_revocation
    from rememberstack.surfaces.credentials import load_pending_revocations
    from rememberstack.surfaces.credentials import PendingRevocation

    config_dir = tmp_path / "remember-cfg-recov-proj"
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(config_dir))

    dep_a = UUID("11111111-1111-1111-1111-111111111111")
    dep_b = UUID("22222222-2222-2222-2222-222222222222")
    tid_a = UUID("aaaa0000-0000-0000-0000-000000000000")
    tid_b = UUID("bbbb0000-0000-0000-0000-000000000000")

    # Credential file where B is active, but A is still a valid configured project
    cred = CredentialFile(
        version=1,
        api_url="https://b.dp.remember.dev",
        token_host="https://api.remember.dev",
        deployment_id=dep_b,
        token_id=tid_b,
        access_token=SecretStr("token_b"),
        active_project_id=str(dep_b),
        projects={
            str(dep_a): ProjectCredentials(
                name="project-a",
                data_plane_url="https://a.dp.remember.dev",
                data_plane_token=SecretStr("token_a"),
                deployment_id=dep_a,
                token_host="https://api.remember.dev",
                token_id=tid_a,
            ),
            str(dep_b): ProjectCredentials(
                name="project-b",
                data_plane_url="https://b.dp.remember.dev",
                data_plane_token=SecretStr("token_b"),
                deployment_id=dep_b,
                token_host="https://api.remember.dev",
                token_id=tid_b,
            ),
        },
    )
    write_credentials(credential=cred)

    # Journal has pending revocation for tid_a (e.g. leftover from an interrupted flow)
    append_pending_revocation(
        pending=PendingRevocation(
            version=1,
            token_host="https://api.remember.dev",
            token_id=tid_a,
            access_token=SecretStr("token_a"),
        )
    )
    journal_before = load_pending_revocations()
    assert len(journal_before.entries) == 1

    # Mock HTTP client to verify NO revocation requests are made to the cloud
    revoked_calls: list[str] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/api-tokens/self":
            revoked_calls.append(request.headers.get("authorization", ""))
            return httpx.Response(200)
        return httpx.Response(404)

    orig_client = httpx.Client

    def mock_client_factory(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(mock_handler)
        return orig_client(*args, **kwargs)

    monkeypatch.setattr("rememberstack.surfaces.cli.httpx.Client", mock_client_factory)

    # Trigger revocation recovery
    _retry_pending_revocation()

    # Zero remote calls should have been made because tid_a belongs to active Project A!
    assert len(revoked_calls) == 0

    # The entry should have been dropped safely from the journal
    journal_after = load_pending_revocations()
    assert len(journal_after.entries) == 0


def test_harness_ambient_setup_allows_dynamic_project_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ambient cloud setup does not pin URL in harness configs, enabling seamless project switching."""
    import argparse

    from rememberstack.surfaces.cli import _cli_memory_client

    config_dir = tmp_path / "remember-cfg-dyn-switch"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(config_dir))

    pid_1 = "0191-proj-alpha"
    pid_2 = "0191-proj-beta"
    cred = CredentialFile(
        version=1,
        api_url="https://alpha.dp.remember.dev",
        token_host="https://api.remember.dev",
        access_token=SecretStr("umc_dp_alpha"),
        active_project_id=pid_1,
        projects={
            pid_1: ProjectCredentials(
                name="production",
                data_plane_url="https://alpha.dp.remember.dev",
                data_plane_token=SecretStr("umc_dp_alpha"),
            ),
            pid_2: ProjectCredentials(
                name="staging",
                data_plane_url="https://beta.dp.remember.dev",
                data_plane_token=SecretStr("umc_dp_beta"),
            ),
        },
    )
    write_credentials(credential=cred)

    # 1. Run setup with ambient credentials
    assert (
        main(["setup", "--cloud", "--agent", "cursor", "--dir", str(workspace_dir)])
        == 0
    )

    # Verify .cursor/mcp.json does NOT pin REMEMBER_DATA_PLANE_URL in env
    cursor_file = workspace_dir / ".cursor" / "mcp.json"
    assert cursor_file.is_file()
    cursor_cfg = json.loads(cursor_file.read_text(encoding="utf-8"))
    assert "remember" in cursor_cfg["mcpServers"]
    server_entry = cursor_cfg["mcpServers"]["remember"]
    assert server_entry["args"][-1] == "mcp"
    # env should be absent or empty, allowing dynamic credential lookup
    assert (
        "env" not in server_entry
        or "REMEMBER_DATA_PLANE_URL" not in server_entry.get("env", {})
    )

    # 2. Before switch: resolved client points to Alpha
    args = argparse.Namespace(api_url=None, token=None)
    client_1 = _cli_memory_client(args=args)
    assert str(client_1._client.base_url) == "https://alpha.dp.remember.dev"
    assert client_1._client.headers.get("authorization") == "Bearer umc_dp_alpha"

    # 3. Switch to staging (Beta)
    assert main(["switch", "staging"]) == 0

    # 4. After switch: resolved client automatically points to Beta without touching workspace config!
    client_2 = _cli_memory_client(args=args)
    assert str(client_2._client.base_url) == "https://beta.dp.remember.dev"
    assert client_2._client.headers.get("authorization") == "Bearer umc_dp_beta"


def test_setup_with_token_overwrites_active_project_and_journals_revocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setup --token replacing an active OAuth project journals predecessor to pending-revocation.json."""
    from uuid import UUID

    from rememberstack.surfaces.cli import _retry_pending_revocation
    from rememberstack.surfaces.credentials import load_credentials
    from rememberstack.surfaces.credentials import load_pending_revocations

    config_dir = tmp_path / "remember-cfg-setup-revocation"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(config_dir))

    pid = "0191-proj-prod"
    tid_initial = UUID("11111111-1111-1111-1111-111111111111")
    initial_token = "umc_oauth_initial_token"
    replacement_token = "umc_manual_replacement_token"

    cred = CredentialFile(
        version=1,
        api_url="https://prod.dp.remember.dev",
        token_host="https://api.remember.dev",
        token_id=tid_initial,
        access_token=SecretStr(initial_token),
        active_project_id=pid,
        projects={
            pid: ProjectCredentials(
                name="production",
                data_plane_url="https://prod.dp.remember.dev",
                data_plane_token=SecretStr(initial_token),
                token_id=tid_initial,
                token_host="https://api.remember.dev",
            )
        },
    )
    write_credentials(credential=cred)

    # Run setup --token to replace the token
    rc = main(
        [
            "setup",
            "--cloud",
            "--agent",
            "cursor",
            "--dir",
            str(workspace_dir),
            "--token",
            replacement_token,
        ]
    )
    assert rc == 0

    # Verify credentials updated
    updated_cred = load_credentials()
    assert updated_cred is not None
    assert updated_cred.access_token is not None
    assert updated_cred.access_token.get_secret_value() == replacement_token
    assert updated_cred.token_id != tid_initial
    assert updated_cred.projects is not None
    active_proj = updated_cred.projects[pid]
    assert active_proj.data_plane_token.get_secret_value() == replacement_token
    assert active_proj.token_id is not None
    assert active_proj.token_id == updated_cred.token_id
    assert active_proj.token_id != tid_initial

    # Verify predecessor was journaled to pending-revocation.json
    journal = load_pending_revocations()
    assert len(journal.entries) == 1
    entry = journal.entries[0]
    assert entry.token_id == tid_initial
    assert entry.access_token.get_secret_value() == initial_token
    assert entry.token_host == "https://api.remember.dev"

    # Now verify revocation succeeds via retry
    revoked_calls: list[str] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/api-tokens/self":
            revoked_calls.append(request.headers.get("authorization", ""))
            return httpx.Response(200)
        return httpx.Response(404)

    orig_client = httpx.Client

    def mock_client_factory(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(mock_handler)
        return orig_client(*args, **kwargs)

    monkeypatch.setattr("remember.cli.httpx.Client", mock_client_factory)
    monkeypatch.setattr("rememberstack.surfaces.cli.httpx.Client", mock_client_factory)

    _retry_pending_revocation()

    assert len(revoked_calls) == 1
    assert revoked_calls[0] == f"Bearer {initial_token}"
    assert len(load_pending_revocations().entries) == 0


def test_setup_handles_malformed_cursor_json_gracefully(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed existing .cursor/mcp.json prints concise remediation and exits 1 without traceback."""
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir(parents=True)
    (cursor_dir / "mcp.json").write_text(
        "{malformed_json: true, broken", encoding="utf-8"
    )

    exit_code = main(
        ["setup", "--self-hosted", "--dir", str(tmp_path), "--agent", "cursor"]
    )
    assert exit_code == 1

    captured = capsys.readouterr()
    assert "error: Existing" in captured.err
    assert "contains invalid JSON" in captured.err
    assert "Traceback" not in captured.err


def test_setup_explicit_requested_harness_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a requested harness fails to register, setup returns exit code 1."""
    import shutil

    orig_which = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda cmd: "/usr/local/bin/claude" if cmd == "claude" else orig_which(cmd),
    )
    monkeypatch.setattr("remember.setup.configure_claude_code", lambda **kwargs: False)
    monkeypatch.setattr(
        "remember.setup.configure_claude_desktop", lambda **kwargs: False
    )

    exit_code = main(
        ["setup", "--self-hosted", "--dir", str(tmp_path), "--agent", "claude"]
    )
    assert exit_code == 1

    captured = capsys.readouterr()
    assert "error: Failed to configure Claude harness" in captured.err


def test_setup_cursor_invalid_structure(tmp_path: Path) -> None:
    """Cursor configuration rejects JSON arrays at root or for mcpServers."""
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    mcp_file = cursor_dir / "mcp.json"

    # Root is list
    mcp_file.write_text("[]", encoding="utf-8")
    with pytest.raises(
        RuntimeError, match="has invalid structure: expected JSON object at root"
    ):
        configure_cursor(
            cwd=tmp_path, launcher_cmd="/usr/bin/remember", launcher_args=["mcp"]
        )

    # mcpServers is list
    mcp_file.write_text('{"mcpServers": []}', encoding="utf-8")
    with pytest.raises(
        RuntimeError, match="has invalid structure: 'mcpServers' must be a JSON object"
    ):
        configure_cursor(
            cwd=tmp_path, launcher_cmd="/usr/bin/remember", launcher_args=["mcp"]
        )


def test_setup_claude_desktop_invalid_structure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude Desktop configuration rejects JSON arrays at root or for mcpServers."""
    from remember.setup import configure_claude_desktop

    config_file = tmp_path / "claude_desktop_config.json"
    monkeypatch.setattr(
        "remember.setup.get_claude_desktop_config_path", lambda: config_file
    )

    # Root is list
    config_file.write_text("[]", encoding="utf-8")
    with pytest.raises(
        RuntimeError, match="has invalid structure: expected JSON object at root"
    ):
        configure_claude_desktop(
            launcher_cmd="/usr/bin/remember", launcher_args=["mcp"]
        )

    # mcpServers is list
    config_file.write_text('{"mcpServers": []}', encoding="utf-8")
    with pytest.raises(
        RuntimeError, match="has invalid structure: 'mcpServers' must be a JSON object"
    ):
        configure_claude_desktop(
            launcher_cmd="/usr/bin/remember", launcher_args=["mcp"]
        )


def test_setup_antigravity_invalid_structure(tmp_path: Path) -> None:
    """Antigravity configuration rejects JSON arrays at root or for mcpServers."""
    agents_dir = tmp_path / ".agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    mcp_file = agents_dir / "mcp_config.json"

    # Root is list
    mcp_file.write_text("[]", encoding="utf-8")
    with pytest.raises(
        RuntimeError, match="has invalid structure: expected JSON object at root"
    ):
        configure_antigravity(
            cwd=tmp_path, launcher_cmd="/usr/bin/remember", launcher_args=["mcp"]
        )

    # mcpServers is list
    mcp_file.write_text('{"mcpServers": []}', encoding="utf-8")
    with pytest.raises(
        RuntimeError, match="has invalid structure: 'mcpServers' must be a JSON object"
    ):
        configure_antigravity(
            cwd=tmp_path, launcher_cmd="/usr/bin/remember", launcher_args=["mcp"]
        )


def test_setup_codex_invalid_toml(tmp_path: Path) -> None:
    """Codex configuration rejects invalid TOML content with clean error."""
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    config_file = codex_dir / "config.toml"

    config_file.write_text("this is not [ valid toml", encoding="utf-8")
    with pytest.raises(RuntimeError, match="contains invalid TOML"):
        configure_codex(
            cwd=tmp_path, launcher_cmd="/usr/bin/remember", launcher_args=["mcp"]
        )


def test_multi_project_manual_rotation_preserves_token_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manual setup rotation across multiple projects does not borrow or corrupt token UUIDs."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from uuid import uuid4

    from pydantic import SecretStr

    from remember.credentials import CredentialFile
    from remember.credentials import load_credentials
    from remember.credentials import load_pending_revocations
    from remember.credentials import ProjectCredentials
    from remember.credentials import write_credentials

    uuid_a = uuid4()
    uuid_b = uuid4()
    init_cred = CredentialFile(
        version=1,
        api_url="https://a.dp.remember.dev",
        token_host="a.dp.remember.dev",
        access_token=SecretStr("token-a-initial"),
        token_id=uuid_a,
        active_project_id="proj_a",
        projects={
            "proj_a": ProjectCredentials(
                name="Project A",
                data_plane_url="https://a.dp.remember.dev",
                data_plane_token=SecretStr("token-a-initial"),
                token_host="a.dp.remember.dev",
                token_id=uuid_a,
            ),
            "proj_b": ProjectCredentials(
                name="Project B",
                data_plane_url="https://b.dp.remember.dev",
                data_plane_token=SecretStr("token-b-initial"),
                token_host="b.dp.remember.dev",
                token_id=uuid_b,
            ),
        },
    )
    write_credentials(credential=init_cred)

    # 1. Rotate A manually using setup --token
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir(parents=True)
    assert (
        main(
            [
                "setup",
                "--token",
                "token-a-v2",
                "--url",
                "https://a.dp.remember.dev",
                "--dir",
                str(tmp_path),
                "--agent",
                "cursor",
            ]
        )
        == 0
    )
    cred_after_rot1 = load_credentials()
    assert cred_after_rot1 is not None
    assert cred_after_rot1.projects is not None
    token_id_a_v2 = cred_after_rot1.projects["proj_a"].token_id
    assert token_id_a_v2 is not None
    assert token_id_a_v2 != uuid_a
    assert cred_after_rot1.token_id == token_id_a_v2

    # 2. Switch A -> B -> A
    assert main(["switch", "proj_b"]) == 0
    cred_b = load_credentials()
    assert cred_b is not None
    assert cred_b.token_id == uuid_b

    assert main(["switch", "proj_a"]) == 0
    cred_a = load_credentials()
    assert cred_a is not None
    assert cred_a.token_id == token_id_a_v2
    assert cred_a.token_id != uuid_b  # Did NOT borrow or preserve Project B's UUID!

    # 3. Rotate A again
    assert (
        main(
            [
                "setup",
                "--token",
                "token-a-v3",
                "--url",
                "https://a.dp.remember.dev",
                "--dir",
                str(tmp_path),
                "--agent",
                "cursor",
            ]
        )
        == 0
    )
    cred_a_v3 = load_credentials()
    assert cred_a_v3 is not None
    assert cred_a_v3.projects is not None
    token_id_a_v3 = cred_a_v3.projects["proj_a"].token_id
    assert token_id_a_v3 is not None
    assert token_id_a_v3 not in (uuid_a, uuid_b, token_id_a_v2)
    assert cred_a_v3.token_id == token_id_a_v3

    # 4. Verify revocation journal entries do not borrow B's UUID
    revocations = load_pending_revocations().entries
    journaled_ids = [r.token_id for r in revocations]
    assert uuid_b not in journaled_ids, (
        "Project B's token_id was erroneously journaled as A's predecessor!"
    )
    assert uuid_a in journaled_ids
    assert token_id_a_v2 in journaled_ids
