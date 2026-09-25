"""``remember setup`` entry selection per harness (D136 §6).

Each harness × situation gets one of four entry shapes; no written file ever
holds a literal key; unrelated configuration survives; a second run changes
nothing. The CLI-driven harnesses (Claude Code, Codex) run against fake
``claude``/``codex`` executables that record their arguments.
"""

from __future__ import annotations

import json
from pathlib import Path
import stat
import tomllib

import httpx
from pydantic import SecretStr
import pytest

from remember.cli import main
from remember.credentials import load_credentials
from remember.credentials import StoredCredentials
from remember.credentials import write_credentials
from remember.setup import ANTIGRAVITY_SUPPORT
from remember.setup import claude_code_command
from remember.setup import CLAUDE_DESKTOP_SUPPORT
from remember.setup import codex_block
from remember.setup import configure_codex
from remember.setup import configure_cursor
from remember.setup import CURSOR_SUPPORT
from remember.setup import Entry
from remember.setup import Plan
from remember.setup import RemoteSupport
from remember.setup import select_entry
from tests.surfaces.fake_issuer import FakeIssuer
from tests.surfaces.fake_issuer import ISSUER
from tests.surfaces.fake_issuer import make_key

LAUNCHER = "/opt/remember/bin/remember"
ENDPOINT = f"{ISSUER}/mcp"
LISTENER = "http://127.0.0.1:8765/mcp"
STORED_KEY = make_key(jti="key-stored")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A fixed launcher, no CI marker, Claude Desktop's file under tmp, and no
    real ``claude``/``codex`` on PATH (tests that need them add fakes)."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr("remember.setup.resolve_launcher", lambda: (LAUNCHER, ["mcp"]))
    monkeypatch.setattr(
        "remember.setup.get_claude_desktop_config_path",
        lambda: tmp_path / "desktop" / "claude_desktop_config.json",
    )


@pytest.fixture()
def issuer(monkeypatch: pytest.MonkeyPatch) -> FakeIssuer:
    """Route every ``httpx.Client`` to the fake issuer (selected through
    ``REMEMBER_ISSUER``); make polling instant."""
    monkeypatch.setenv("REMEMBER_ISSUER", ISSUER)
    fake = FakeIssuer()
    real_client = httpx.Client

    def patched(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = fake.transport()
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", patched)
    monkeypatch.setattr("remember.issuer.time.sleep", lambda _seconds: None)
    return fake


def _store_key() -> None:
    write_credentials(
        credentials=StoredCredentials(
            version=2, issuer=ISSUER, key=SecretStr(STORED_KEY), key_id="key-stored"
        )
    )


def _fake_cli(
    directory: Path, name: str, *, help_text: str, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """An executable ``name`` that prints ``help_text`` for ``--help`` and logs
    every other call (one line of arguments) to ``<name>.log``."""
    directory.mkdir(exist_ok=True)
    log = directory / f"{name}.log"
    script = directory / name
    script.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do [ "$a" = "--help" ] && { echo "'
        + help_text
        + '"; exit 0; }; done\n'
        f'echo "$(pwd -P)|$*" >> "{log}"\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{directory}:{Path('/usr/bin')}:{Path('/bin')}")
    return log


def _all_text(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in root.rglob("*")
        if path.is_file()
    )


# --- the selection rule ------------------------------------------------------------


def _plan(*, remote_url: str | None, key_header: bool, hosted: bool = True) -> Plan:
    return Plan(
        remote_url=remote_url,
        key_header=key_header,
        stdio_shape="stdio_bridge" if hosted else "stdio_engine",
        stdio_env={"REMEMBER_MCP_URL": ENDPOINT}
        if hosted
        else {"REMEMBER_API_URL": "http://127.0.0.1:8000"},
        launcher_cmd=LAUNCHER,
        launcher_args=("mcp",),
        hosted=hosted,
    )


HOSTED_INTERACTIVE = _plan(remote_url=ENDPOINT, key_header=False)
HOSTED_HEADLESS = _plan(remote_url=ENDPOINT, key_header=True)
SELF_HOSTED = _plan(remote_url=None, key_header=False, hosted=False)
LISTENER_OPEN = _plan(remote_url=LISTENER, key_header=False, hosted=False)
LISTENER_KEYED = _plan(remote_url=LISTENER, key_header=True, hosted=False)

CLAUDE_CODE = RemoteSupport(url=True)  # what a current `claude` probes as
CODEX = RemoteSupport(url=True, header_env=True)  # a current `codex`
UNPROBED = RemoteSupport()  # CLI missing or too old


@pytest.mark.parametrize(
    ("support", "plan", "shape"),
    [
        (CURSOR_SUPPORT, HOSTED_INTERACTIVE, "remote"),
        (CURSOR_SUPPORT, HOSTED_HEADLESS, "remote_key_header"),
        (CURSOR_SUPPORT, SELF_HOSTED, "stdio_engine"),
        (CURSOR_SUPPORT, LISTENER_OPEN, "remote"),
        (CURSOR_SUPPORT, LISTENER_KEYED, "remote_key_header"),
        (CLAUDE_CODE, HOSTED_INTERACTIVE, "remote"),
        (CLAUDE_CODE, HOSTED_HEADLESS, "stdio_bridge"),
        (CLAUDE_CODE, SELF_HOSTED, "stdio_engine"),
        (CLAUDE_CODE, LISTENER_OPEN, "remote"),
        (CLAUDE_CODE, LISTENER_KEYED, "stdio_engine"),
        (CODEX, HOSTED_INTERACTIVE, "remote"),
        (CODEX, HOSTED_HEADLESS, "remote_key_header"),
        (CODEX, SELF_HOSTED, "stdio_engine"),
        (CODEX, LISTENER_KEYED, "remote_key_header"),
        (UNPROBED, HOSTED_INTERACTIVE, "stdio_bridge"),
        (UNPROBED, HOSTED_HEADLESS, "stdio_bridge"),
        (UNPROBED, LISTENER_OPEN, "stdio_engine"),
        (CLAUDE_DESKTOP_SUPPORT, HOSTED_INTERACTIVE, "stdio_bridge"),
        (CLAUDE_DESKTOP_SUPPORT, HOSTED_HEADLESS, "stdio_bridge"),
        (CLAUDE_DESKTOP_SUPPORT, SELF_HOSTED, "stdio_engine"),
        (ANTIGRAVITY_SUPPORT, HOSTED_INTERACTIVE, "stdio_bridge"),
        (ANTIGRAVITY_SUPPORT, HOSTED_HEADLESS, "stdio_bridge"),
        (ANTIGRAVITY_SUPPORT, SELF_HOSTED, "stdio_engine"),
    ],
)
def test_selection_rule(support: RemoteSupport, plan: Plan, shape: str) -> None:
    entry = select_entry(plan, support)
    assert entry.shape == shape
    if entry.remote:
        assert entry.url == plan.remote_url and entry.command is None
    else:
        assert entry.command == LAUNCHER and entry.args == ("mcp",)
        assert entry.env == plan.stdio_env


# --- rendering per harness ---------------------------------------------------------


def test_codex_tables_per_shape() -> None:
    remote = tomllib.loads(codex_block(Entry(shape="remote", url=ENDPOINT)))
    assert remote == {"mcp_servers": {"remember": {"url": ENDPOINT}}}
    header = tomllib.loads(codex_block(Entry(shape="remote_key_header", url=ENDPOINT)))
    assert header["mcp_servers"]["remember"] == {
        "url": ENDPOINT,
        "bearer_token_env_var": "REMEMBER_API_KEY",
    }
    bridge = select_entry(HOSTED_INTERACTIVE, UNPROBED)
    assert tomllib.loads(codex_block(bridge))["mcp_servers"]["remember"] == {
        "command": LAUNCHER,
        "args": ["mcp"],
        "env": {"REMEMBER_MCP_URL": ENDPOINT},
    }


def test_claude_code_commands_per_shape() -> None:
    assert claude_code_command(Entry(shape="remote", url=ENDPOINT)) == [
        "claude", "mcp", "add", "--scope", "local",
        "--transport", "http", "remember", ENDPOINT,
    ]  # fmt: skip
    assert claude_code_command(select_entry(HOSTED_HEADLESS, CLAUDE_CODE)) == [
        "claude", "mcp", "add", "--scope", "local", "remember",
        "-e", f"REMEMBER_MCP_URL={ENDPOINT}", "--", LAUNCHER, "mcp",
    ]  # fmt: skip


# --- files: merge, idempotence, no clobbering -------------------------------------


def test_cursor_merge_keeps_other_servers_and_is_idempotent(tmp_path: Path) -> None:
    mcp_file = tmp_path / ".cursor" / "mcp.json"
    mcp_file.parent.mkdir()
    mcp_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "other": {"command": "other-server"},
                    "remember": {"command": "/stale", "env": {"REMEMBER_TOKEN": "x"}},
                },
                "theme": "dark",
            }
        ),
        encoding="utf-8",
    )
    mcp_file.chmod(0o600)
    entry = Entry(shape="remote_key_header", url=ENDPOINT)
    assert configure_cursor(cwd=tmp_path, entry=entry)
    first = mcp_file.read_text(encoding="utf-8")
    data = json.loads(first)
    assert data["theme"] == "dark"
    assert data["mcpServers"]["other"] == {"command": "other-server"}
    assert data["mcpServers"]["remember"] == {
        "url": ENDPOINT,
        "headers": {"Authorization": "Bearer ${env:REMEMBER_API_KEY}"},
    }
    assert mcp_file.stat().st_mode & 0o777 == 0o600
    before = mcp_file.stat().st_mtime_ns
    assert configure_cursor(cwd=tmp_path, entry=entry)
    assert mcp_file.read_text(encoding="utf-8") == first
    assert mcp_file.stat().st_mtime_ns == before


def test_codex_merge_keeps_other_tables_and_is_idempotent(tmp_path: Path) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        'model = "o3"\n\n'
        "[mcp_servers.remember]\n"
        'command = "/stale"\n\n'
        "[mcp_servers.remember.env]\n"
        'REMEMBER_TOKEN = "leaked"\n\n'
        "[mcp_servers.other]\n"
        'command = "other-server"\n',
        encoding="utf-8",
    )
    entry = Entry(shape="remote", url=ENDPOINT)
    assert configure_codex(cwd=tmp_path, entry=entry, hosted=True)
    first = config.read_text(encoding="utf-8")
    parsed = tomllib.loads(first)
    assert parsed["model"] == "o3"
    assert parsed["mcp_servers"]["other"] == {"command": "other-server"}
    assert parsed["mcp_servers"]["remember"] == {"url": ENDPOINT}
    assert "leaked" not in first
    assert configure_codex(cwd=tmp_path, entry=entry, hosted=True)
    assert config.read_text(encoding="utf-8") == first


# --- the command, hosted -----------------------------------------------------------


def test_hosted_interactive_entries_per_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issuer: FakeIssuer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Remote OAuth entries where the harness takes them, the bridge elsewhere."""
    _store_key()
    project = tmp_path / "project"
    for directory in (".cursor", ".agents", ".codex"):
        (project / directory).mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    claude_log = _fake_cli(
        bin_dir, "claude", help_text="--transport", monkeypatch=monkeypatch
    )
    _fake_cli(
        bin_dir, "codex", help_text="--bearer-token-env-var", monkeypatch=monkeypatch
    )
    (tmp_path / "desktop").mkdir()

    assert main(["setup", "--cloud", "--dir", str(project)]) == 0
    out = capsys.readouterr().out
    assert "codex mcp login remember" in out

    cursor = json.loads((project / ".cursor" / "mcp.json").read_text())
    assert cursor["mcpServers"]["remember"] == {"url": ENDPOINT}
    codex = tomllib.loads((project / ".codex" / "config.toml").read_text())
    assert codex["mcp_servers"]["remember"] == {"url": ENDPOINT}
    bridge = {
        "command": LAUNCHER,
        "args": ["mcp"],
        "env": {"REMEMBER_MCP_URL": ENDPOINT},
    }
    agy = json.loads((project / ".agents" / "mcp_config.json").read_text())
    assert agy["mcpServers"]["remember"] == bridge
    desktop = json.loads(
        (tmp_path / "desktop" / "claude_desktop_config.json").read_text()
    )
    assert desktop["mcpServers"]["remember"] == bridge
    calls = claude_log.read_text().splitlines()
    assert calls == [
        f"{project.resolve()}|mcp remove --scope local remember",
        f"{project.resolve()}|mcp add --scope local --transport http remember {ENDPOINT}",
    ]
    assert STORED_KEY not in _all_text(project) + _all_text(tmp_path / "desktop")


def test_hosted_headless_uses_key_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issuer: FakeIssuer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CI means headless: key header by variable reference, or the bridge; no
    sign-in is attempted even with no key anywhere."""
    monkeypatch.setenv("CI", "true")
    for directory in (".cursor", ".codex"):
        (tmp_path / directory).mkdir()
    bin_dir = tmp_path / "bin"
    claude_log = _fake_cli(
        bin_dir, "claude", help_text="--transport", monkeypatch=monkeypatch
    )
    _fake_cli(
        bin_dir, "codex", help_text="--bearer-token-env-var", monkeypatch=monkeypatch
    )

    assert main(["setup", "--cloud", "--issuer", ISSUER, "--dir", str(tmp_path)]) == 0
    assert "REMEMBER_API_KEY" in capsys.readouterr().out
    assert issuer.calls("/oauth/device") == []
    assert load_credentials() is None

    cursor = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert cursor["mcpServers"]["remember"]["headers"] == {
        "Authorization": "Bearer ${env:REMEMBER_API_KEY}"
    }
    codex = tomllib.loads((tmp_path / ".codex" / "config.toml").read_text())
    assert codex["mcp_servers"]["remember"]["bearer_token_env_var"] == (
        "REMEMBER_API_KEY"
    )
    assert claude_log.read_text().splitlines()[-1] == (
        f"{tmp_path.resolve()}|mcp add --scope local remember "
        f"-e REMEMBER_MCP_URL={ENDPOINT} -- {LAUNCHER} mcp"
    )


def test_hosted_old_codex_falls_back_to_the_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, issuer: FakeIssuer
) -> None:
    _store_key()
    _fake_cli(tmp_path / "bin", "codex", help_text="--url", monkeypatch=monkeypatch)
    assert main(["setup", "--cloud", "--agent", "codex", "--dir", str(tmp_path)]) == 0
    codex = tomllib.loads((tmp_path / ".codex" / "config.toml").read_text())
    assert codex["mcp_servers"]["remember"]["env"] == {"REMEMBER_MCP_URL": ENDPOINT}


def test_hosted_without_a_key_signs_in_first(
    tmp_path: Path, issuer: FakeIssuer, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["setup", "--cloud", "--issuer", ISSUER, "--agent", "cursor"]
    assert main([*argv, "--dir", str(tmp_path)]) == 0
    assert "ABCD-EFGH" in capsys.readouterr().out
    stored = load_credentials()
    assert stored is not None and stored.key_id == "key-new"
    cursor = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert cursor["mcpServers"]["remember"] == {"url": ENDPOINT}
    assert "key-new" not in _all_text(tmp_path)


def test_hosted_dry_run_writes_nothing_and_never_prints_the_key(
    tmp_path: Path, issuer: FakeIssuer, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["setup", "--cloud", "--issuer", ISSUER, "--dry-run", "--dir", str(tmp_path)]
    assert main([*argv, "--agent", "cursor"]) == 0
    out = capsys.readouterr().out
    assert "Would run `remember login`" in out
    assert issuer.calls("/oauth/device") == []
    assert not (tmp_path / ".cursor").exists()

    _store_key()
    assert main([*argv, "--agent", "agy", "--headless"]) == 0
    out = capsys.readouterr().out
    assert f'"REMEMBER_MCP_URL": "{ENDPOINT}"' in out
    assert STORED_KEY not in out
    assert not (tmp_path / ".agents").exists()


def test_hosted_refuses_an_issuer_without_an_mcp_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issuer: FakeIssuer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _store_key()
    monkeypatch.setattr(FakeIssuer, "metadata", lambda self: {"issuer": ISSUER})
    assert main(["setup", "--cloud", "--agent", "cursor", "--dir", str(tmp_path)]) == 1
    assert "remember_mcp_endpoint" in capsys.readouterr().err
    assert not (tmp_path / ".cursor").exists()


# --- the command, self-hosted ------------------------------------------------------


def test_self_hosted_listener_entries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--mcp-url`` implies self-hosted: remote entries where taken (with the
    key header when the engine has a key), the stdio engine entry elsewhere."""
    argv = ["setup", "--mcp-url", LISTENER, "--dir", str(tmp_path)]
    assert main([*argv, "--agent", "cursor"]) == 0
    cursor = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert cursor["mcpServers"]["remember"] == {"url": LISTENER}

    assert main([*argv, "--agent", "cursor", "--api-key", "engine-secret"]) == 0
    assert main([*argv, "--agent", "agy", "--api-key", "engine-secret"]) == 0
    cursor = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert cursor["mcpServers"]["remember"]["headers"] == {
        "Authorization": "Bearer ${env:REMEMBER_API_KEY}"
    }
    agy = json.loads((tmp_path / ".agents" / "mcp_config.json").read_text())
    assert agy["mcpServers"]["remember"]["env"] == {
        "REMEMBER_API_URL": "http://127.0.0.1:8000"
    }
    assert "engine-secret" not in _all_text(tmp_path)
    capsys.readouterr()


def test_self_hosted_flag_conflicts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = ["setup", "--agent", "cursor", "--dir", str(tmp_path)]
    assert main([*base, "--cloud", "--api-url", "http://127.0.0.1:9"]) == 1
    assert "drop --cloud" in capsys.readouterr().err
    assert main([*base, "--mcp-url", "http://example.com/mcp"]) == 1
    assert "--mcp-url must be https" in capsys.readouterr().err
    assert main([*base, "--self-hosted", "--issuer", ISSUER]) == 1
    assert "--issuer is for" in capsys.readouterr().err
    assert not (tmp_path / ".cursor").exists()


# --- review fixes ------------------------------------------------------------------


def test_engine_a_entry_never_sends_engine_b_stored_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setup for A, then for B: A's entry (REMEMBER_API_URL=A) must not carry
    B's stored key to A; `remember mcp` engine mode refuses it."""
    from io import StringIO

    from remember.mcp_engine import serve_stdio

    engine_a, engine_b = "http://127.0.0.1:8001", "http://127.0.0.1:8002"
    base = ["setup", "--agent", "cursor"]
    project_a, project_b = tmp_path / "a", tmp_path / "b"
    assert (
        main(
            [
                *base,
                "--api-url",
                engine_a,
                "--api-key",
                "key-a",
                "--dir",
                str(project_a),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                *base,
                "--api-url",
                engine_b,
                "--api-key",
                "key-b",
                "--dir",
                str(project_b),
            ]
        )
        == 0
    )
    entry = json.loads((project_a / ".cursor" / "mcp.json").read_text())
    monkeypatch.setenv(
        "REMEMBER_API_URL", entry["mcpServers"]["remember"]["env"]["REMEMBER_API_URL"]
    )

    sent: list[httpx.Request] = []
    real_client = httpx.Client

    def patched(*args: object, **kwargs: object) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return httpx.Response(200, json={"build_revision": "x", "tools": []})

        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", patched)
    output = StringIO()
    lines = "\n".join(
        json.dumps(message)
        for message in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
    )
    monkeypatch.setattr(
        "remember.mcp_engine.serve_stdio",
        lambda *, server: serve_stdio(
            server=server, input_stream=StringIO(lines + "\n"), output_stream=output
        ),
    )
    main(["mcp"])
    assert all(
        "key-b" not in request.headers.get("Authorization", "") for request in sent
    )
    assert "is not sent to" in output.getvalue()


def test_cloud_with_a_foreign_environment_key_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issuer: FakeIssuer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("REMEMBER_API_KEY", make_key(iss="https://other.test"))
    argv = ["setup", "--cloud", "--issuer", ISSUER, "--agent", "cursor"]
    assert main([*argv, "--dir", str(tmp_path)]) == 1
    assert "is not a key from https://issuer.test" in capsys.readouterr().err
    monkeypatch.setenv("REMEMBER_API_KEY", "a-shared-secret")
    assert main([*argv, "--dir", str(tmp_path)]) == 1
    assert not (tmp_path / ".cursor").exists()


def test_cloud_with_an_unrelated_stored_key_signs_in(
    tmp_path: Path, issuer: FakeIssuer, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stored self-hosted entry is no key for the issuer: login runs."""
    write_credentials(
        credentials=StoredCredentials(
            version=2, api_url="http://127.0.0.1:8000", key=SecretStr("engine-secret")
        )
    )
    argv = ["setup", "--cloud", "--agent", "cursor", "--dir", str(tmp_path)]
    assert main([*argv, "--issuer", ISSUER]) == 0
    assert "ABCD-EFGH" in capsys.readouterr().out
    stored = load_credentials()
    assert stored is not None and stored.issuer == ISSUER


def test_one_unreadable_harness_file_does_not_stop_the_others(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_bytes(b"\xff\xfe not utf-8")
    (tmp_path / ".agents").mkdir()
    assert main(["setup", "--self-hosted", "--dir", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "Cursor" in err and "Traceback" not in err
    assert (tmp_path / ".agents" / "mcp_config.json").is_file()


def test_a_symlinked_config_file_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "dotfiles-mcp.json"
    target.write_text('{"mcpServers": {}}', encoding="utf-8")
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").symlink_to(target)
    argv = ["setup", "--self-hosted", "--agent", "cursor", "--dir", str(tmp_path)]
    assert main(argv) == 1
    assert "symbolic link" in capsys.readouterr().err
    assert target.read_text(encoding="utf-8") == '{"mcpServers": {}}'
    assert (tmp_path / ".cursor" / "mcp.json").is_symlink()


def test_codex_table_in_another_spelling_is_not_duplicated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    original = '[mcp_servers."remember"]\ncommand = "/stale"\n'
    config.write_text(original, encoding="utf-8")
    argv = ["setup", "--self-hosted", "--agent", "codex", "--dir", str(tmp_path)]
    assert main(argv) == 1
    assert "edit [mcp_servers.remember] manually" in capsys.readouterr().err
    assert config.read_text(encoding="utf-8") == original


def test_cloud_chooses_the_issuer_before_looking_at_the_stored_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stored key from another issuer never selects that issuer: --cloud
    goes to the default issuer and signs in there."""
    default = FakeIssuer(base="https://remember.dev")
    default.issued_key = make_key(jti="key-dev", iss="https://remember.dev")
    real_client = httpx.Client

    def patched(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = default.transport()
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", patched)
    monkeypatch.setattr("remember.issuer.time.sleep", lambda _seconds: None)
    write_credentials(
        credentials=StoredCredentials(
            version=2,
            issuer="https://other.test",
            key=SecretStr(make_key(iss="https://other.test")),
        )
    )
    argv = ["setup", "--cloud", "--agent", "cursor", "--dir", str(tmp_path)]
    assert main(argv) == 0
    assert "Backend: https://remember.dev" in capsys.readouterr().out
    assert default.calls("/oauth/device")
    stored = load_credentials()
    assert stored is not None and stored.issuer == "https://remember.dev"
    cursor = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert cursor["mcpServers"]["remember"] == {"url": "https://remember.dev/mcp"}


def test_codex_non_table_mcp_servers_fails_only_codex(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text("mcp_servers = 123\n", encoding="utf-8")
    (tmp_path / ".cursor").mkdir()
    argv = ["setup", "--self-hosted", "--dir", str(tmp_path)]
    assert main(argv) == 1
    err = capsys.readouterr().err
    assert "'mcp_servers' must be a table" in err and "Traceback" not in err
    assert config.read_text(encoding="utf-8") == "mcp_servers = 123\n"
    assert (tmp_path / ".cursor" / "mcp.json").is_file()
