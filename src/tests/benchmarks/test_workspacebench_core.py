"""Synthetic Workspace-Bench hashing, isolation, receipts, and report proofs."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from pathlib import Path

from benchmarks.workspacebench.canary import evaluate_canary
from benchmarks.workspacebench.canary import write_fake_secret
from benchmarks.workspacebench.codex import CodexRuntimeEvent
from benchmarks.workspacebench.codex import compose_prompt
from benchmarks.workspacebench.codex import config_blob_for_canary
from benchmarks.workspacebench.codex import effective_mcp_launch
from benchmarks.workspacebench.codex import memory_arm_configuration
from benchmarks.workspacebench.codex import native_arm_configuration
from benchmarks.workspacebench.errors import WorkspaceBenchError
from benchmarks.workspacebench.hashing import require_path_topology
from benchmarks.workspacebench.hashing import tree_digest
from benchmarks.workspacebench.hashing import WorkspacePathError
from benchmarks.workspacebench.judge import JUDGE_REQUIRED_FLAGS
from benchmarks.workspacebench.judge import official_judge_command
from benchmarks.workspacebench.judge import prepare_judge_view
from benchmarks.workspacebench.mcp import access_binding_from_args
from benchmarks.workspacebench.mcp import McpDiscoveryError
from benchmarks.workspacebench.models import CloudDeploymentReceipt
from benchmarks.workspacebench.models import ReadinessPin
from benchmarks.workspacebench.overlay import apply_overlays
from benchmarks.workspacebench.overlay import load_overlay_manifest
from benchmarks.workspacebench.protocol import FAKE_CANARY_SECRET
from benchmarks.workspacebench.protocol import TARGET_OUTPUT_DIR
from benchmarks.workspacebench.receipts import validate_receipt_against_workspace
from benchmarks.workspacebench.report import reconstruct_paired_report
from benchmarks.workspacebench.task_contract import JUDGE_SCRIPT_RELPATH
from benchmarks.workspacebench.task_contract import local_task_contract
from benchmarks.workspacebench.workspace import agent_visible_metadata
from benchmarks.workspacebench.workspace import clone_pristine_workspace
from benchmarks.workspacebench.workspace import collect_outputs
from benchmarks.workspacebench.workspace import write_agent_task_view
import pytest

HIDDEN_SENTINEL = "HIDDEN_RUBRIC_SENTINEL"


def _synthetic_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    (workspace / "inputs").mkdir(parents=True)
    (workspace / TARGET_OUTPUT_DIR).mkdir()
    (workspace / "inputs" / "notes.txt").write_text("notes", encoding="utf-8")
    (workspace / "inputs" / "data.json").write_text("{}", encoding="utf-8")
    (workspace / "inputs" / "slides.ppt").write_bytes(b"PPT")
    (workspace / "inputs" / "legacy.xls").write_bytes(b"XLS")
    (workspace / "inputs" / "modern.xlsx").write_bytes(b"XLSX")
    return workspace


def _task_metadata() -> dict[str, object]:
    return {
        "id": "300",
        "task": "Build the inventory report.",
        "data_manifest": [
            {
                "stored_relpath": "data/notes.txt",
                "target_path": "inputs/notes.txt",
                "filename": "notes.txt",
            }
        ],
        "output_files": ["report.md", "table.csv", "result.json"],
        "rubrics": [HIDDEN_SENTINEL],
        "rubric_types": ["gold"],
        "ground_truth": {"answer": "hidden"},
        "file_dep_graph": {"edges": []},
        "reference_output": "must not leak",
    }


def test_task_dir_nested_under_upstream_is_allowed(tmp_path: Path) -> None:
    upstream = tmp_path / "Workspace-Bench"
    task_dir = upstream / "evaluation" / "tasks_lite" / "300"
    task_dir.mkdir(parents=True)
    workspace = tmp_path / "backend-developer-workspace"
    workspace.mkdir()
    output = tmp_path / "wb-runs" / "task-300" / "pair"
    require_path_topology(
        {
            "upstream": upstream,
            "task-dir": task_dir,
            "workspace": workspace,
            "output": output,
        }
    )


def test_unsafe_source_and_mutable_overlaps_are_rejected(tmp_path: Path) -> None:
    upstream = tmp_path / "Workspace-Bench"
    upstream.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task_dir = tmp_path / "task-300"
    task_dir.mkdir()
    output = tmp_path / "out"
    with pytest.raises(WorkspacePathError, match="overlap"):
        require_path_topology(
            {
                "upstream": upstream,
                "task-dir": task_dir,
                "workspace": workspace,
                "output": workspace / "nested-out",
            }
        )
    with pytest.raises(WorkspacePathError, match="overlap"):
        require_path_topology(
            {
                "upstream": upstream,
                "task-dir": workspace / "task",
                "workspace": workspace,
                "output": output,
            }
        )
    with pytest.raises(WorkspacePathError, match="overlap"):
        require_path_topology(
            {
                "upstream": upstream,
                "task-dir": task_dir,
                "workspace": upstream / "role-workspace",
                "output": output,
            }
        )
    with pytest.raises(WorkspacePathError, match="overlap"):
        require_path_topology(
            {
                "upstream": upstream,
                "task-dir": task_dir,
                "workspace": workspace,
                "output": upstream / "wb-out",
            }
        )


def test_tree_digest_rejects_escaping_symlinks(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "inside.txt").write_text("ok", encoding="utf-8")
    outside = tmp_path / "secret"
    outside.write_text("nope", encoding="utf-8")
    (workspace / "escape").symlink_to(outside)
    with pytest.raises(WorkspacePathError, match="escapes"):
        tree_digest(workspace)


def test_clone_preserves_byte_identical_digests(tmp_path: Path) -> None:
    source = _synthetic_workspace(tmp_path)
    first = tmp_path / "a"
    second = tmp_path / "b"
    digest_a = clone_pristine_workspace(source=source, destination=first)
    digest_b = clone_pristine_workspace(source=source, destination=second)
    assert digest_a == digest_b == tree_digest(source)


def test_staging_does_not_write_evaluator_metadata(tmp_path: Path) -> None:
    source = tmp_path / "task"
    (source / "data").mkdir(parents=True)
    (source / "data" / "notes.txt").write_text("n", encoding="utf-8")
    (source / "metadata.json").write_text(
        __import__("json").dumps(_task_metadata()), encoding="utf-8"
    )
    view = tmp_path / "view"
    visible = write_agent_task_view(source_task_dir=source, destination=view)
    assert "rubrics" not in visible
    assert "ground_truth" not in visible
    listed = agent_visible_metadata(_task_metadata())
    assert "rubrics" not in listed
    assert listed["id"] == "300"
    assert (view / "inputs" / "notes.txt").is_file()
    assert not (view / "metadata.json").exists()
    for path in view.rglob("*"):
        if path.is_file():
            assert HIDDEN_SENTINEL not in path.read_text(
                encoding="utf-8", errors="replace"
            )


def test_outputs_cannot_escape_through_dotdot_or_symlink(tmp_path: Path) -> None:
    workspace = _synthetic_workspace(tmp_path)
    metadata = {
        "output_files": ["../escape.md", "report.md"],
        "output_dir": TARGET_OUTPUT_DIR,
    }
    (workspace / TARGET_OUTPUT_DIR / "report.md").write_text("# ok", encoding="utf-8")
    outside = tmp_path / "escape.md"
    outside.write_text("leaked", encoding="utf-8")
    (workspace / TARGET_OUTPUT_DIR / "link.md").symlink_to(outside)
    manifest = collect_outputs(workspace=workspace, metadata=metadata)
    assert "report.md" in {item.relative_path for item in manifest.files}
    assert "../escape.md" in manifest.rejected
    leaked = collect_outputs(
        workspace=workspace,
        metadata={"output_files": ["link.md"], "output_dir": TARGET_OUTPUT_DIR},
    )
    assert "link.md" in leaked.rejected


def test_only_memory_arm_receives_instruction_and_mcp_allowlist() -> None:
    native = native_arm_configuration()
    memory = memory_arm_configuration(
        remember_bin=("/usr/bin/python", "-m", "remember"),
        api_origin="http://127.0.0.1:18000",
        enabled_tools=("resolve_entity", "facts_context"),
    )
    prompt = "Do the task."
    assert compose_prompt(task_prompt=prompt, arm=native) == prompt
    memory_prompt = compose_prompt(task_prompt=prompt, arm=memory)
    assert memory_prompt.endswith(prompt)
    assert "augmentation" in memory_prompt
    assert "`combined_context` returns `ContextBundle/v2`" in memory_prompt
    assert "`claims_and_sources` and `facts` child envelopes" in memory_prompt
    assert "`testimony_context`" not in memory_prompt
    assert "`fact_context`" not in memory_prompt
    assert "`answer_context`" not in memory_prompt
    assert native.mcp_enabled is False
    assert native.enabled_tools == ()
    assert memory.enabled_tools == ("resolve_entity", "facts_context")
    blob = config_blob_for_canary(arm=memory)
    command, args = effective_mcp_launch(arm=memory)
    assert command == "/usr/bin/python"
    assert args == (
        "-m",
        "remember",
        "mcp",
        "--read-only",
        "--api-url",
        "http://127.0.0.1:18000",
    )
    assert f"command={__import__('json').dumps(command)}" in blob
    assert "-m" in blob
    assert "remember" in blob
    assert "--read-only" in blob
    assert "token" not in blob.lower()
    native_blob = config_blob_for_canary(arm=native)
    assert "mcp_servers.remember" not in native_blob
    assert "mcp_servers." not in native_blob
    assert 'cli_auth_credentials_store="keyring"' in native_blob
    assert "--ignore-user-config" not in native_blob
    assert "--ignore-user-config" not in blob


def test_receipt_rejects_digest_mismatch_and_credential_material() -> None:
    receipt = CloudDeploymentReceipt(
        workspace_digest="a" * 64,
        rememberstack_revision="8fad369d341950b869dd2f3f8acbce4693b63cea",
        converter_router_configuration="pinned-router",
        component_generations={"e0": "1"},
        version_ids=("11111111-1111-1111-1111-111111111111",),
        readiness_requirements=ReadinessPin(
            pipeline=True, p1=True, live_graph=True, p3=False
        ),
        api_origin="http://127.0.0.1:18000",
        deployment_id="dep-1",
        sealed=True,
        attested_by="operator",
        attested_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )
    with pytest.raises(WorkspaceBenchError, match="digest"):
        validate_receipt_against_workspace(
            receipt=receipt,
            workspace_digest="b" * 64,
            api_origin="http://127.0.0.1:18000",
        )


def test_receipt_rejects_empty_version_ids_and_unready_plane() -> None:
    with pytest.raises(Exception, match="version_ids"):
        CloudDeploymentReceipt(
            workspace_digest="a" * 64,
            rememberstack_revision="rev",
            converter_router_configuration="pinned-router",
            component_generations={"e0": "1"},
            version_ids=(),
            readiness_requirements=ReadinessPin(
                pipeline=True, p1=True, live_graph=True, p3=False
            ),
            api_origin="https://remember.example.test",
            deployment_id="dep-1",
            sealed=True,
            attested_by="operator",
            attested_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
    with pytest.raises(Exception, match="pipeline, p1, and live_graph"):
        CloudDeploymentReceipt(
            workspace_digest="a" * 64,
            rememberstack_revision="rev",
            converter_router_configuration="pinned-router",
            component_generations={"e0": "1"},
            version_ids=("11111111-1111-1111-1111-111111111111",),
            readiness_requirements=ReadinessPin(
                pipeline=True, p1=False, live_graph=False, p3=False
            ),
            api_origin="https://remember.example.test",
            deployment_id="dep-1",
            sealed=True,
            attested_by="operator",
            attested_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
    with pytest.raises(Exception, match="four assured read"):
        CloudDeploymentReceipt(
            workspace_digest="a" * 64,
            rememberstack_revision="rev",
            converter_router_configuration="pinned-router",
            component_generations={"e0": "1"},
            version_ids=("11111111-1111-1111-1111-111111111111",),
            readiness_requirements=ReadinessPin(
                pipeline=True, p1=True, live_graph=False, p3=True
            ),
            api_origin="https://remember.example.test",
            deployment_id="dep-1",
            sealed=True,
            attested_by="operator",
            attested_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
        )


def test_ssh_tunnel_binding_allows_loopback_when_target_matches_receipt() -> None:
    receipt = CloudDeploymentReceipt(
        workspace_digest="a" * 64,
        rememberstack_revision="rev",
        converter_router_configuration="pinned-router",
        component_generations={"e0": "1"},
        version_ids=("11111111-1111-1111-1111-111111111111",),
        readiness_requirements=ReadinessPin(
            pipeline=True, p1=True, live_graph=True, p3=False
        ),
        api_origin="https://remember.example.test",
        deployment_id="dep-1",
        sealed=True,
        attested_by="operator",
        attested_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )
    binding = access_binding_from_args(
        api_origin="http://127.0.0.1:18000",
        receipt_origin=receipt.api_origin,
        access_mode="ssh_local_forward",
        canonical_origin="https://remember.example.test",
    )
    validate_receipt_against_workspace(
        receipt=receipt,
        workspace_digest="a" * 64,
        api_origin="http://127.0.0.1:18000",
        access=binding,
    )
    with pytest.raises(McpDiscoveryError):
        access_binding_from_args(
            api_origin="http://127.0.0.1:18000",
            receipt_origin=receipt.api_origin,
            access_mode="direct",
        )
    with pytest.raises(McpDiscoveryError, match="canonical origin"):
        access_binding_from_args(
            api_origin="http://127.0.0.1:18000",
            receipt_origin=receipt.api_origin,
            access_mode="ssh_local_forward",
            canonical_origin="https://other.example.test",
        )


def test_overlay_manifest_is_empty_and_does_not_mutate_source(tmp_path: Path) -> None:
    source = tmp_path / "upstream"
    source.mkdir()
    (source / "keep.txt").write_text("keep", encoding="utf-8")
    dest = tmp_path / "copy"
    manifest = load_overlay_manifest()
    assert manifest.overlays == ()
    apply_overlays(source_checkout=source, destination=dest, manifest=manifest)
    assert (source / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert (dest / "keep.txt").is_file()


def test_canary_fails_when_fake_secret_enters_trace(tmp_path: Path) -> None:
    artifacts = tmp_path / "out"
    artifacts.mkdir()
    secret_path = write_fake_secret(directory=tmp_path / "canary")
    arm = native_arm_configuration()
    clean = evaluate_canary(
        artifact_root=artifacts, arm=arm, events=(), fake_secret_path=secret_path
    )
    assert clean.passed is True
    assert clean.live_turn is False
    assert "structural" in clean.detail
    (artifacts / "trace.json").write_text(FAKE_CANARY_SECRET, encoding="utf-8")
    dirty = evaluate_canary(
        artifact_root=artifacts, arm=arm, events=(), fake_secret_path=secret_path
    )
    assert dirty.passed is False
    empty = tmp_path / "empty"
    empty.mkdir()
    command = evaluate_canary(
        artifact_root=empty,
        arm=arm,
        events=(
            CodexRuntimeEvent(
                item_type="CommandExecutionThreadItem",
                payload={"command": f"cat {secret_path}", "exit_code": 0},
            ),
        ),
        fake_secret_path=secret_path,
        live_turn=True,
    )
    assert command.credential_path_readable_by_command is True
    assert command.passed is False
    denied = evaluate_canary(
        artifact_root=empty,
        arm=arm,
        events=(
            CodexRuntimeEvent(
                item_type="CommandExecutionThreadItem",
                payload={
                    "command": f"cat {secret_path}",
                    "exit_code": 1,
                    "aggregated_output": "permission denied",
                },
            ),
        ),
        fake_secret_path=secret_path,
        final_response="cannot read the file",
        live_turn=True,
        turn_status="completed",
    )
    assert denied.passed is True
    empty_live = evaluate_canary(
        artifact_root=empty,
        arm=arm,
        events=(),
        fake_secret_path=secret_path,
        live_turn=True,
        turn_status="completed",
    )
    assert empty_live.passed is False
    assert "auth.json" not in config_blob_for_canary(arm=arm)


def test_report_reconstruction_checks_protocol_fingerprints(tmp_path: Path) -> None:
    from decimal import Decimal

    from benchmarks.workspacebench.models import ArmConfiguration
    from benchmarks.workspacebench.models import OutputManifest
    from benchmarks.workspacebench.models import PairedRunManifest
    from benchmarks.workspacebench.models import TaskResult
    from benchmarks.workspacebench.models import Timing
    from benchmarks.workspacebench.models import TokenUsage
    from benchmarks.workspacebench.models import TraceManifest
    from benchmarks.workspacebench.preflight import build_protocol_coordinates

    protocol = build_protocol_coordinates(
        workspace_digest="c" * 64, task_id="300", codex_cli_sha256="a" * 64
    )
    drifted_protocol = build_protocol_coordinates(
        workspace_digest="c" * 64, task_id="300", codex_cli_sha256="b" * 64
    )
    assert protocol.codex_cli_sha256 == "a" * 64
    assert protocol.protocol_fingerprint != drifted_protocol.protocol_fingerprint
    started = datetime(2026, 9, 7, tzinfo=timezone.utc)

    def result(arm: str, *, fingerprint: str | None = None) -> TaskResult:
        return TaskResult(
            attempt_id=f"{arm}-1",
            task_id="300",
            arm=arm,  # type: ignore[arg-type]
            protocol=protocol,
            workspace_digest="c" * 64,
            arm_configuration=ArmConfiguration(
                arm=arm,  # type: ignore[arg-type]
                mcp_enabled=arm == "memory",
            ),
            usage=TokenUsage(
                tokens_in=1, tokens_out=1, cost_usd=Decimal(0), model=protocol.model
            ),
            timing=Timing(
                started_at=started,
                finished_at=started,
                elapsed_ms=0,
                timeout_seconds=10,
                grace_seconds=1,
            ),
            failure_class="none",
            outputs=OutputManifest(output_root=TARGET_OUTPUT_DIR),
            traces=TraceManifest(
                raw_relpath="raw.jsonl", sanitized_relpath="san.jsonl"
            ),
            experimental_label=protocol.experimental_label,
            arm_execution_fingerprint=fingerprint,
        )

    pair = PairedRunManifest(
        pair_id="pair",
        task_id="300",
        arm_order=("native", "memory"),
        native_workspace_digest="c" * 64,
        memory_workspace_digest="c" * 64,
        native_result_relpath="native/result.json",
        memory_result_relpath="memory/result.json",
        experimental_label=protocol.experimental_label,
        protocol_fingerprint=protocol.protocol_fingerprint,
        native_arm_fingerprint="n" * 64,
        memory_arm_fingerprint="m" * 64,
    )
    first = reconstruct_paired_report(
        pair=pair,
        native=result("native", fingerprint="n" * 64),
        memory=result("memory", fingerprint="m" * 64),
    )
    second = reconstruct_paired_report(
        pair=pair,
        native=result("native", fingerprint="n" * 64),
        memory=result("memory", fingerprint="m" * 64),
    )
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.quality_inference == "not_inferred_from_single_task"
    with pytest.raises(WorkspaceBenchError, match="protocol fingerprint"):
        reconstruct_paired_report(
            pair=pair.model_copy(update={"protocol_fingerprint": "0" * 64}),
            native=result("native", fingerprint="n" * 64),
            memory=result("memory", fingerprint="m" * 64),
        )
    with pytest.raises(WorkspaceBenchError, match="protocol fingerprint"):
        reconstruct_paired_report(
            pair=pair,
            native=result("native", fingerprint="n" * 64).model_copy(
                update={"protocol": drifted_protocol}
            ),
            memory=result("memory", fingerprint="m" * 64),
        )


def test_judge_command_requires_task_dir_and_eval_yaml(tmp_path: Path) -> None:
    eval_root = tmp_path / "evaluation"
    script = eval_root / "src" / "agent_as_a_judge.py"
    script.parent.mkdir(parents=True)
    script.write_bytes(b"pin")
    eval_yaml = tmp_path / "judge.yaml"
    eval_yaml.write_text("baseUrl: x\nmodel: y\napiKey: z\n", encoding="utf-8")
    case = tmp_path / "native"
    case.mkdir()
    (case / "output").mkdir()
    pin_hash = __import__("hashlib").sha256(b"pin").hexdigest()
    import benchmarks.workspacebench.judge as judge_mod

    original = judge_mod.EXPECTED_UPSTREAM_FILE_SHA256[JUDGE_SCRIPT_RELPATH]
    judge_mod.EXPECTED_UPSTREAM_FILE_SHA256[JUDGE_SCRIPT_RELPATH] = pin_hash
    try:
        command = official_judge_command(
            eval_root=eval_root, task_dir=case, eval_yaml=eval_yaml
        )
        assert "--task-dir" in command
        assert "--eval-yaml" in command
        assert str(case) in command
        assert str(eval_yaml) in command
        for flag in JUDGE_REQUIRED_FLAGS:
            assert flag in command
        with pytest.raises(WorkspaceBenchError, match="eval yaml missing"):
            official_judge_command(
                eval_root=eval_root, task_dir=case, eval_yaml=tmp_path / "missing.yaml"
            )
    finally:
        judge_mod.EXPECTED_UPSTREAM_FILE_SHA256[JUDGE_SCRIPT_RELPATH] = original


def test_judge_view_copies_source_data_and_does_not_invent_empty_inputs(
    tmp_path: Path,
) -> None:
    source = tmp_path / "task"
    (source / "data").mkdir(parents=True)
    (source / "data" / "notes.txt").write_text("n", encoding="utf-8")
    (source / "metadata.json").write_text(
        __import__("json").dumps(_task_metadata()), encoding="utf-8"
    )
    case = tmp_path / "native-case"
    (case / "output").mkdir(parents=True)
    (case / "output" / "report.md").write_text("# r", encoding="utf-8")
    view = prepare_judge_view(
        case_dir=case, source_task_dir=source, candidate_id="candidate-a"
    )
    assert "native" not in view.name
    restored = (view / "original_task_metadata.json").read_text(encoding="utf-8")
    assert HIDDEN_SENTINEL in restored
    assert (view / "candidate_output" / "report.md").is_file()
    assert (view / "inputs" / "notes.txt").is_file()
    empty_source = tmp_path / "empty-task"
    empty_source.mkdir()
    (empty_source / "metadata.json").write_text(
        __import__("json").dumps({"id": "1"}), encoding="utf-8"
    )
    empty_case = tmp_path / "empty-case"
    empty_case.mkdir()
    empty_view = prepare_judge_view(
        case_dir=empty_case, source_task_dir=empty_source, candidate_id="candidate-b"
    )
    assert not (empty_view / "inputs").exists()


def test_local_task_contract_wraps_workdir_and_path_list(tmp_path: Path) -> None:
    contract = local_task_contract()
    work = tmp_path / "ws"
    work.mkdir()
    prompt = contract.wrap_prompt(
        task_prompt="Build the report.",
        work_dir=work,
        target_output_dir=TARGET_OUTPUT_DIR,
    )
    assert "Working Directory" in prompt
    assert str(work.resolve()) in prompt
    assert TARGET_OUTPUT_DIR in prompt
    assert "Python list" in prompt
    source = tmp_path / "task"
    (source / "data").mkdir(parents=True)
    (source / "data" / "notes.txt").write_text("n", encoding="utf-8")
    staged = tmp_path / "staged"
    staged.mkdir()
    created = contract.stage_manifest(
        source_task_dir=source, destination=staged, metadata=_task_metadata()
    )
    assert created
    assert (staged / "inputs" / "notes.txt").is_file()
    assert not (staged / "metadata.json").exists()


def test_manifest_dotdot_escape_is_rejected_before_upstream_copy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "task"
    (source / "data").mkdir(parents=True)
    (source / "data" / "notes.txt").write_text("n", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("leaked", encoding="utf-8")
    staged = tmp_path / "staged"
    staged.mkdir()
    contract = local_task_contract()
    with pytest.raises(WorkspacePathError, match="escapes"):
        contract.stage_manifest(
            source_task_dir=source,
            destination=staged,
            metadata={
                "id": "300",
                "data_manifest": [
                    {
                        "stored_relpath": "../secret.txt",
                        "target_path": "inputs/secret.txt",
                    }
                ],
            },
        )
    with pytest.raises(WorkspacePathError, match="escapes"):
        contract.stage_manifest(
            source_task_dir=source,
            destination=staged,
            metadata={
                "id": "300",
                "data_manifest": [
                    {
                        "stored_relpath": "data/notes.txt",
                        "target_path": "../outside.txt",
                    }
                ],
            },
        )


def test_office_skills_stage_as_direct_skill_children(tmp_path: Path) -> None:
    from benchmarks.workspacebench.office import stage_office_skills

    upstream = tmp_path / "upstream"
    for name in ("docx", "pdf"):
        skill = upstream / "evaluation/skills/office" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    workspace = tmp_path / "ws"
    (workspace / ".agents/skills/existing").mkdir(parents=True)
    (workspace / ".agents/skills/existing" / "SKILL.md").write_text(
        "# keep\n", encoding="utf-8"
    )
    stage_office_skills(upstream=upstream, workspace=workspace)
    assert (workspace / ".agents/skills/docx" / "SKILL.md").is_file()
    assert (workspace / ".agents/skills/pdf" / "SKILL.md").is_file()
    assert (workspace / ".agents/skills/existing" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "# keep\n"
    assert not (workspace / ".agents/skills/office" / "SKILL.md").exists()
    (workspace / ".agents/skills/docx" / "SKILL.md").write_text(
        "# different\n", encoding="utf-8"
    )
    with pytest.raises(WorkspaceBenchError, match="not identical"):
        stage_office_skills(upstream=upstream, workspace=workspace)


def test_wrong_model_canary_request_is_rejected() -> None:
    from pathlib import Path as _Path

    from benchmarks.workspacebench.canary import require_protocol_canary_request
    from benchmarks.workspacebench.codex import CodexTaskRequest
    from benchmarks.workspacebench.errors import LiveGateError

    request = CodexTaskRequest(
        prompt="x",
        workspace=_Path("/tmp"),
        model="canary",
        reasoning_effort="low",
        arm=native_arm_configuration(),
        timeout_seconds=1,
        grace_seconds=1,
    )
    with pytest.raises(LiveGateError, match="protocol model"):
        require_protocol_canary_request(request)
