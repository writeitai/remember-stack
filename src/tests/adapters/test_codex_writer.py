"""WP-6.4 stock-Codex sandbox and declared-output boundary proofs."""

from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from rememberstack.adapters import CodexCLIWriterAdapter
from rememberstack.adapters import CodexWriterAdapterSettings
from rememberstack.model import KnowledgeWriterSessionRequest
from rememberstack.model import PublishedMounts


def _request() -> KnowledgeWriterSessionRequest:
    """Build one isolated writer request with corpus and standalone K locators."""
    deployment_id = uuid4()
    return KnowledgeWriterSessionRequest(
        session_id=uuid4(),
        model="configured-codex-model",
        prompt="Write the declared Plane-K files.",
        timeout_seconds=30,
        input_files={"bundle/evidence.json": "{}\n"},
        mounts=PublishedMounts(
            deployment_id=deployment_id,
            p3="mount://p3",
            knowledge="mount://knowledge",
            read_only=True,
        ),
    )


def test_adapter_disables_network_and_returns_only_declared_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real CLI argv is sandboxed and undeclared writer edits are discarded."""
    observed: dict[str, object] = {}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed["kwargs"] = kwargs
        writer_workdir = Path(command[command.index("--cd") + 1])
        (writer_workdir / "prose.md").write_text("Prose.\n", encoding="utf-8")
        (writer_workdir / "citations.json").write_text("[]\n", encoding="utf-8")
        (writer_workdir / "summary.md").write_text(
            "First sentence. Second sentence.\n", encoding="utf-8"
        )
        (writer_workdir / "suggestions.json").write_text("[]\n", encoding="utf-8")
        (writer_workdir / "undeclared.txt").write_text("discard me", encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                '{"type":"turn.completed","usage":'
                '{"input_tokens":12,"cached_input_tokens":3,"output_tokens":5}}\n'
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    request = _request()

    result = CodexCLIWriterAdapter(
        settings=CodexWriterAdapterSettings(executable="codex-test")
    ).run_session(request=request)

    command = observed["command"]
    assert isinstance(command, list)
    assert command[:2] == ["codex-test", "exec"]
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--ephemeral" in command
    assert "--sandbox" in command
    assert "workspace-write" in command
    assert "sandbox_workspace_write.network_access=false" in command
    assert "sandbox_workspace_write.exclude_tmpdir_env_var=true" in command
    assert "sandbox_workspace_write.exclude_slash_tmp=true" in command
    assert "shell_environment_policy.inherit=none" in command
    assert Path(command[command.index("--cd") + 1]).name == "output"
    assert "--search" not in command
    assert observed["kwargs"] == {
        "input": request.prompt,
        "capture_output": True,
        "text": True,
        "check": False,
        "timeout": request.timeout_seconds,
    }
    assert set(result.output_files) == set(request.sandbox.accepted_output_paths)
    assert "undeclared.txt" not in result.output_files
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.tokens == 17
    assert '\\"type\\":\\"turn.completed\\"' in result.transcript


def test_adapter_returns_timeout_transcript_before_workspace_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out process still yields a complete terminal transcript for archiving."""

    def fake_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(
            cmd="codex", timeout=30, output=b"partial stdout", stderr=b"partial stderr"
        )

    monkeypatch.setattr(subprocess, "run", fake_timeout)

    result = CodexCLIWriterAdapter(
        settings=CodexWriterAdapterSettings(executable="codex-test")
    ).run_session(request=_request())

    assert result.timed_out is True
    assert result.exit_code is None
    assert "partial stdout" in result.transcript
    assert "partial stderr" in result.transcript


def test_hostile_declared_output_link_cannot_erase_completed_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A writer symlink is omitted so the compiler can archive and reject the session."""

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        writer_workdir = Path(command[command.index("--cd") + 1])
        (writer_workdir / "prose.md").symlink_to(
            writer_workdir.parent / "bundle/evidence.json"
        )
        (writer_workdir / "citations.json").write_text("[]\n", encoding="utf-8")
        (writer_workdir / "summary.md").write_text(
            "First sentence. Second sentence.\n", encoding="utf-8"
        )
        return subprocess.CompletedProcess(
            command, 0, stdout='{"type":"turn.completed"}\n', stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = CodexCLIWriterAdapter(
        settings=CodexWriterAdapterSettings(executable="codex-test")
    ).run_session(request=_request())

    assert result.exit_code == 0
    assert "output/prose.md" not in result.output_files
    assert "turn.completed" in result.transcript
