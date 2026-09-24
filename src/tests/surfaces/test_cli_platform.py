"""Unit tests for the remember CLI grammar and the setup bootstrapper."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from pydantic import BaseModel
import pytest

from remember import Client
from remember import MemoryClient
from remember import RememberClient
from remember.cli import main
from remember.setup import configure_antigravity
from remember.setup import configure_codex
from remember.setup import configure_cursor
from remember.setup import resolve_launcher


def test_remember_client_import_and_alias() -> None:
    """The canonical Remember package exports RememberClient aliasing Client."""
    assert RememberClient is Client
    assert issubclass(RememberClient, MemoryClient)
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
    assert "facts_context" in rule_text
    assert "bitemporal" in rule_text
    assert "Resolve entities first" in rule_text
    assert "valid_from" in rule_text
    assert "asserted_at" in rule_text


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
    assert "Preferred Retrieval Flow" in skill_text
    assert "Entity resolution first (`resolve_entity`)" in skill_text
    assert "Fact layer first (`facts_context`)" in skill_text
    assert "valid_from" in skill_text
    assert "valid_precision" in skill_text
    assert "asserted_at" in skill_text
    assert (
        "Never confuse speech time (`asserted_at`) with real-world event validity"
        in skill_text
    )


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
    assert "found" in out
    assert code in (0, 1)


def test_ops_is_confined_to_internal_environments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI ops refuses outside internal container environments with exit 1."""
    dummy_dep = "74000000-0000-0000-0000-000000000001"
    res_ops = main(["ops", "inspect", "--deployment", dummy_dep])
    assert res_ops == 1
    err_ops = capsys.readouterr().err
    assert "remember ops' is confined to internal container environments" in err_ops


def test_query_free_text_dispatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI query defaults to facts_context and uses combined_context with --combined."""
    captured_calls: list[tuple[str, str]] = []

    class MockClient:
        def __enter__(self) -> "MockClient":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def facts_context(self, query: str) -> object:
            captured_calls.append(("fact", query))

            class MockEnvelope(BaseModel):
                operation: str
                facts: list[str]

            return MockEnvelope(operation="facts_context", facts=["test fact"])

        def combined_context(self, query: str) -> object:
            captured_calls.append(("answer", query))

            class MockBundle(BaseModel):
                operation: str
                query: str

            return MockBundle(operation="combined_context", query=query)

    monkeypatch.setattr("remember.cli._cli_memory_client", lambda args: MockClient())

    # 1. Bare free text query
    res = main(["query", "What were our decisions on auth?"])
    assert res == 0
    assert captured_calls[-1] == ("fact", "What were our decisions on auth?")
    assert "test fact" in capsys.readouterr().out

    # 2. Free text query with --combined
    res2 = main(["query", "How does indexing work?", "--combined"])
    assert res2 == 0
    assert captured_calls[-1] == ("answer", "How does indexing work?")
    assert "How does indexing work?" in capsys.readouterr().out


def test_query_free_text_with_preceding_flags(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Flags like --combined or --url can precede free-text query strings (Finding 8b)."""
    captured_calls: list[tuple[str, str]] = []

    class MockClient:
        def __enter__(self) -> "MockClient":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def facts_context(self, query: str) -> object:
            captured_calls.append(("fact", query))

            class MockEnvelope(BaseModel):
                operation: str
                facts: list[str]

            return MockEnvelope(operation="facts_context", facts=["test fact"])

        def combined_context(self, query: str) -> object:
            captured_calls.append(("answer", query))

            class MockBundle(BaseModel):
                operation: str
                query: str

            return MockBundle(operation="combined_context", query=query)

    monkeypatch.setattr("remember.cli._cli_memory_client", lambda args: MockClient())

    # Flag before query
    res = main(["query", "--combined", "How does indexing work?"])
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


def test_malformed_credentials_cli_error_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A malformed or version-1 credential file is refused cleanly: exit 1, no traceback."""
    config_dir = tmp_path / "cfg-malformed"
    config_dir.mkdir(parents=True, mode=0o700)
    cred_file = config_dir / "credentials.json"
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(config_dir))

    for body in ("{ broken: json, invalid", json.dumps({"version": 1})):
        cred_file.write_text(body, encoding="utf-8")
        cred_file.chmod(0o600)
        for argv in (["whoami"], ["documents", "list"]):
            assert main(argv) == 1
            err = capsys.readouterr().err
            assert "not a version-2 credential file" in err
            assert "remember login" in err
            assert "Traceback" not in err


def test_setup_self_hosted_tokenless_persists_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`setup --self-hosted` stores the engine URL; whoami reports it, key-free."""
    config_dir = tmp_path / "cfg-selfhosted"
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(config_dir))

    ret = main(["setup", "--self-hosted", "--dir", str(tmp_path), "--agent", "cursor"])
    assert ret == 0
    assert "Stored the engine URL" in capsys.readouterr().out

    stored = json.loads((config_dir / "credentials.json").read_text())
    assert stored["version"] == 2
    assert stored["api_url"] == "http://127.0.0.1:8000"
    assert stored["key"] is None

    assert main(["whoami"]) == 0
    assert "Self-hosted engine: http://127.0.0.1:8000" in capsys.readouterr().out

    entry = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert entry["mcpServers"]["remember"]["env"] == {
        "REMEMBER_API_URL": "http://127.0.0.1:8000"
    }


def test_setup_self_hosted_key_goes_to_the_credential_file_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The self-hosted key lands in the owner-only file, never in a harness file."""
    config_dir = tmp_path / "cfg-selfhosted-key"
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(config_dir))
    argv = ["setup", "--self-hosted", "--dir", str(tmp_path), "--agent", "cursor"]
    assert main([*argv, "--api-key", "shared-secret-value"]) == 0
    capsys.readouterr()
    stored = config_dir / "credentials.json"
    assert json.loads(stored.read_text())["key"] == "shared-secret-value"
    assert stored.stat().st_mode & 0o777 == 0o600
    assert "shared-secret-value" not in (tmp_path / ".cursor" / "mcp.json").read_text()


def test_setup_cloud_refuses_a_pasted_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Hosted keys come from `remember login`, not a setup flag."""
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(tmp_path / "cfg"))
    code = main(["setup", "--cloud", "--api-key", "x", "--dir", str(tmp_path)])
    assert code == 1
    assert "remember login" in capsys.readouterr().err


def test_malformed_url_cli_error_boundary(capsys: pytest.CaptureFixture[str]) -> None:
    """Malformed URL raises httpx.InvalidURL caught by top-level CLI error boundary."""
    code = main(["operations", "list", "--api-url", "::::"])
    assert code == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "Traceback" not in err


def test_invalid_env_zero_traceback_cli_error_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed environment configuration exits 1 with actionable diagnostic and zero traceback."""
    import subprocess
    import sys

    src_dir = str(Path(__file__).parents[2])
    monkeypatch.setenv("PYTHONPATH", src_dir)
    monkeypatch.setenv("REMEMBER_INTERNAL_OPS", "banana")

    proc = subprocess.run(
        [sys.executable, "-m", "remember", "--help"], capture_output=True, text=True
    )
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    assert "error: Invalid environment configuration" in proc.stderr
    assert "INTERNAL_OPS" in proc.stderr.upper()


def test_doctor_honors_explicit_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`remember doctor --api-key` probes the engine with that key."""
    recorded: list[httpx.Request] = []
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"build_revision": "abc"})

    def mock_client(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", mock_client)
    monkeypatch.chdir(tmp_path)
    main(
        [
            "doctor",
            "--api-url",
            "http://127.0.0.1:8000",
            "--api-key",
            "explicit-probe-secret",
        ]
    )
    assert "Engine reachable and authenticated" in capsys.readouterr().out
    assert recorded[0].url == "http://127.0.0.1:8000/deployment"
    assert recorded[0].headers["Authorization"] == "Bearer explicit-probe-secret"


def test_query_result_dict_export_type_parity() -> None:
    """QueryResultDict in remember and remember.models are identical class."""
    import remember
    from remember.models import QueryResultDict

    assert remember.QueryResultDict is QueryResultDict
    instance = QueryResultDict(
        {"rows": [{"a": 1}], "columns": ["a"], "truncated": False}
    )
    assert isinstance(instance, remember.QueryResultDict)
    assert instance.rows == [{"a": 1}]
    assert instance.columns == ["a"]
    assert instance.truncated is False


def test_setup_dry_run_does_not_claim_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """remember setup --dry-run must not output '[✓] Stored' or '[✓] Configured'."""
    config_dir = tmp_path / "cfg-dryrun"
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(config_dir))

    code = main(["setup", "--self-hosted", "--dry-run", "--dir", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "[✓] Stored" not in out
    assert "Mode: DRY RUN (no files will be written)" in out


def test_setup_waits_for_a_concurrent_login_and_keeps_its_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Setup reads, checks and writes under the login lock, so a key that a
    concurrent login stores is never overwritten unrevoked."""
    import threading

    from pydantic import SecretStr

    from remember.credentials import credential_lock
    from remember.credentials import load_credentials
    from remember.credentials import StoredCredentials
    from remember.credentials import write_credentials

    locked = threading.Event()
    release = threading.Event()

    def concurrent_login() -> None:
        with credential_lock():
            locked.set()
            release.wait(5)
            write_credentials(
                credentials=StoredCredentials(
                    version=2,
                    issuer="https://issuer.test",
                    key=SecretStr("rmb_login-key"),
                )
            )

    login = threading.Thread(target=concurrent_login)
    login.start()
    assert locked.wait(5)
    result: list[int] = []
    setup = threading.Thread(
        target=lambda: result.append(
            main(
                ["setup", "--self-hosted", "--dir", str(tmp_path), "--agent", "cursor"]
            )
        )
    )
    setup.start()
    setup.join(0.5)
    assert setup.is_alive()  # blocked on the lock the login holds
    release.set()
    login.join(5)
    setup.join(5)
    assert result == [1]
    assert "run `remember logout`" in capsys.readouterr().err
    stored = load_credentials()
    assert stored is not None and stored.issuer == "https://issuer.test"
