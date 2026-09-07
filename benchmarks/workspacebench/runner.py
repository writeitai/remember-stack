"""Run one Workspace-Bench arm against an already prepared isolated workspace."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from datetime import UTC
import json
from pathlib import Path
import sys
from typing import Any
from uuid import uuid4

from benchmarks.workspacebench.canary import evaluate_canary
from benchmarks.workspacebench.codex import CodexTaskRequest
from benchmarks.workspacebench.codex import CodexTaskTurn
from benchmarks.workspacebench.codex import CodexTurnRunner
from benchmarks.workspacebench.codex import compose_prompt
from benchmarks.workspacebench.codex import events_as_jsonable
from benchmarks.workspacebench.codex import native_arm_configuration
from benchmarks.workspacebench.codex import run_codex_task
from benchmarks.workspacebench.codex import subscription_cost
from benchmarks.workspacebench.env import sanitized_subprocess_env
from benchmarks.workspacebench.errors import LiveGateError
from benchmarks.workspacebench.errors import WorkspaceBenchError
from benchmarks.workspacebench.fingerprint import arm_execution_fingerprint
from benchmarks.workspacebench.hashing import atomic_write_json
from benchmarks.workspacebench.hashing import require_absolute_path
from benchmarks.workspacebench.hashing import require_output_outside_workspace
from benchmarks.workspacebench.hashing import tree_digest
from benchmarks.workspacebench.isolation import inspect_bundled_codex_cli
from benchmarks.workspacebench.models import AccountAttestation
from benchmarks.workspacebench.models import ArmConfiguration
from benchmarks.workspacebench.models import FailureClass
from benchmarks.workspacebench.models import ProtocolCoordinates
from benchmarks.workspacebench.models import TaskResult
from benchmarks.workspacebench.models import Timing
from benchmarks.workspacebench.models import TokenUsage
from benchmarks.workspacebench.models import TraceManifest
from benchmarks.workspacebench.preflight import build_protocol_coordinates
from benchmarks.workspacebench.protocol import DEFAULT_GRACE_SEC
from benchmarks.workspacebench.protocol import DEFAULT_TIMEOUT_SEC
from benchmarks.workspacebench.protocol import FAKE_CANARY_SECRET
from benchmarks.workspacebench.protocol import OWNER_ONLY_FILE_MODE
from benchmarks.workspacebench.protocol import PROTOCOL_EXPERIMENTAL_LABEL
from benchmarks.workspacebench.protocol import SDK_TIMEOUT_PARTIAL_EVENTS
from benchmarks.workspacebench.protocol import TARGET_OUTPUT_DIR
from benchmarks.workspacebench.supervisor import ProcessGroupRunner
from benchmarks.workspacebench.supervisor import run_process_group
from benchmarks.workspacebench.task_contract import collect_output_manifest
from benchmarks.workspacebench.task_contract import local_task_contract
from benchmarks.workspacebench.task_contract import TaskContract
from benchmarks.workspacebench.traces import classify_runtime_events
from benchmarks.workspacebench.traces import judge_execution_trace
from benchmarks.workspacebench.traces import redact_bodies
from benchmarks.workspacebench.traces import redact_credentials
from benchmarks.workspacebench.traces import write_jsonl
from benchmarks.workspacebench.workspace import assert_no_evaluation_metadata
from benchmarks.workspacebench.workspace import write_full_evaluator_metadata


def run_agent(
    *,
    workspace: Path,
    output: Path,
    arm: ArmConfiguration,
    task_id: str,
    task_prompt: str,
    execute: bool,
    timeout_seconds: float = DEFAULT_TIMEOUT_SEC,
    grace_seconds: float = DEFAULT_GRACE_SEC,
    turn_runner: CodexTurnRunner | None = None,
    supervisor: ProcessGroupRunner | None = None,
    fake_secret: str = FAKE_CANARY_SECRET,
    fake_secret_path: Path | None = None,
    now: datetime | None = None,
    task_metadata: Mapping[str, Any] | None = None,
    source_task_dir: Path | None = None,
    protocol: ProtocolCoordinates | None = None,
    account: AccountAttestation | None = None,
    contract: TaskContract | None = None,
    target_output_dir: str = TARGET_OUTPUT_DIR,
    case_dir: Path | None = None,
) -> TaskResult:
    """Run one arm and write a durable result envelope plus traces."""
    require_absolute_path(workspace, label="workspace")
    require_absolute_path(output, label="output")
    require_output_outside_workspace(workspace=workspace, output=output)
    output.mkdir(parents=True, exist_ok=True)
    started = now or datetime.now(tz=UTC)
    workspace_digest = tree_digest(workspace)
    bound_protocol = protocol or build_protocol_coordinates(
        workspace_digest=workspace_digest, task_id=task_id
    )
    helper = contract or local_task_contract()
    wrapped = helper.wrap_prompt(
        task_prompt=task_prompt, work_dir=workspace, target_output_dir=target_output_dir
    )
    prompt = compose_prompt(task_prompt=wrapped, arm=arm)
    request = CodexTaskRequest(
        prompt=prompt,
        workspace=workspace,
        model=bound_protocol.model,
        reasoning_effort=bound_protocol.reasoning_effort,
        arm=arm,
        timeout_seconds=timeout_seconds,
        grace_seconds=grace_seconds,
    )
    attempt_id = uuid4().hex
    raw_path = output / "raw-codex-trace.jsonl"
    sanitized_path = output / "sanitized-codex-trace.jsonl"
    scan_root = case_dir or output
    assert_no_evaluation_metadata(root=workspace)
    assert_no_evaluation_metadata(root=scan_root)
    if not execute and turn_runner is None:
        result = _envelope(
            attempt_id=attempt_id,
            task_id=task_id,
            arm=arm,
            protocol=bound_protocol,
            workspace_digest=workspace_digest,
            account=account,
            started=started,
            timeout_seconds=timeout_seconds,
            grace_seconds=grace_seconds,
            turn=CodexTaskTurn(
                status="not_executed",
                error_message="pass --execute to run a Codex subscription task",
                final_response=None,
                tokens_in=None,
                tokens_out=None,
                item_types=(),
                events=(),
            ),
            outputs=collect_output_manifest(
                workspace=workspace,
                case_output_dir=output / "output",
                metadata=task_metadata or {},
                last_text="",
                contract=helper,
                target_output_dir=target_output_dir,
            ),
            traces=_empty_traces(raw_path=raw_path, sanitized_path=sanitized_path),
            failure_class="not_executed",
            failure_detail="pass --execute to run a Codex subscription task",
            violations=(),
            memory=None,
        )
        _persist(output=output, result=result)
        return result

    if turn_runner is None:
        _require_live_runtime_pin(bound_protocol)
    runner = turn_runner or _supervised_runner(supervisor=supervisor)
    try:
        turn = runner(request=request)
    except WorkspaceBenchError as error:
        turn = CodexTaskTurn(
            status="failed",
            error_message=str(error),
            final_response=None,
            tokens_in=None,
            tokens_out=None,
            item_types=(),
            events=(),
        )
    event_maps = [
        {"item_type": event.item_type, "payload": event.payload}
        for event in turn.events
    ]
    violations, mcp_calls, memory = classify_runtime_events(
        events=event_maps,
        arm=arm.arm,
        enabled_tools=arm.enabled_tools,
        workspace=workspace,
        fake_secret=fake_secret,
    )
    raw_rows = [
        {
            "item_type": row["item_type"],
            "payload": redact_credentials(row["payload"], secrets=(fake_secret,)),
        }
        for row in events_as_jsonable(turn.events)
    ]
    sanitized_rows = [
        {
            "item_type": row["item_type"],
            "payload": redact_bodies(row["payload"], secrets=(fake_secret,)),
        }
        for row in events_as_jsonable(turn.events)
    ]
    write_jsonl(
        path=raw_path, rows=raw_rows, secrets=(fake_secret,), mode=OWNER_ONLY_FILE_MODE
    )
    write_jsonl(
        path=sanitized_path, rows=sanitized_rows, secrets=(fake_secret,), mode=None
    )
    canary = evaluate_canary(
        artifact_root=output,
        arm=arm,
        events=turn.events,
        secret=fake_secret,
        fake_secret_path=fake_secret_path,
        final_response=turn.final_response,
        kind="structural",
        live_turn=False,
    )
    if not canary.passed:
        violations = (*violations, canary.detail)
    outputs = collect_output_manifest(
        workspace=workspace,
        case_output_dir=output / "output",
        metadata=task_metadata or {},
        last_text=turn.final_response or "",
        contract=helper,
        target_output_dir=target_output_dir,
    )
    failure_class, detail = _classify_failure(
        turn=turn, violations=violations, outputs=outputs, canary_passed=canary.passed
    )
    traces = TraceManifest(
        raw_relpath=raw_path.name,
        sanitized_relpath=sanitized_path.name,
        item_types=turn.item_types,
        mcp_calls=mcp_calls,
        command_count=sum(
            1 for item in turn.item_types if item == "CommandExecutionThreadItem"
        ),
        file_change_count=sum(
            1 for item in turn.item_types if item == "FileChangeThreadItem"
        ),
    )
    if bound_protocol.arm_execution_fingerprint:
        arm_fp = bound_protocol.arm_execution_fingerprint
    else:
        arm_fp = arm_execution_fingerprint(
            base_protocol_fingerprint=bound_protocol.protocol_fingerprint, arm=arm
        )
    result_protocol = bound_protocol.model_copy(
        update={
            "workspace_digest": workspace_digest,
            "staged_workspace_digest": workspace_digest,
            "arm_execution_fingerprint": arm_fp,
            "sdk_timeout_partial_events": SDK_TIMEOUT_PARTIAL_EVENTS,
        }
    )
    result = _envelope(
        attempt_id=attempt_id,
        task_id=task_id,
        arm=arm,
        protocol=result_protocol,
        workspace_digest=workspace_digest,
        account=account,
        started=started,
        timeout_seconds=timeout_seconds,
        grace_seconds=grace_seconds,
        turn=turn,
        outputs=outputs,
        traces=traces,
        failure_class=failure_class,
        failure_detail=detail,
        violations=violations,
        memory=memory if arm.arm == "memory" else None,
        arm_execution_fingerprint=arm_fp,
    )
    _persist(output=output, result=result)
    if task_metadata is not None:
        post_metadata = dict(task_metadata)
        if source_task_dir is not None:
            post_metadata["__metadata_path"] = str(
                (source_task_dir / "metadata.json").resolve()
            )
        write_full_evaluator_metadata(
            path=output / "metadata.json", metadata=post_metadata
        )
        _write_agent_json(
            path=output / "agent.json",
            task_id=task_id,
            workspace=workspace,
            prompt=prompt,
            turn=turn,
            result=result,
            source_task_dir=source_task_dir,
        )
    return result


def supervised_turn_runner(
    *, supervisor: ProcessGroupRunner | None = None
) -> CodexTurnRunner:
    """External process-group supervisor around one Codex session child."""
    return _supervised_runner(supervisor=supervisor)


def _supervised_runner(*, supervisor: ProcessGroupRunner | None) -> CodexTurnRunner:
    def run(*, request: CodexTaskRequest) -> CodexTaskTurn:
        return _run_supervised(
            request=request, supervisor=supervisor or run_process_group
        )

    return run


def _run_supervised(
    *, request: CodexTaskRequest, supervisor: ProcessGroupRunner
) -> CodexTaskTurn:
    request_path = request.workspace.parent / f".{uuid4().hex}-codex-request.json"
    output_path = request.workspace.parent / f".{uuid4().hex}-codex-turn.json"
    try:
        atomic_write_json(
            path=request_path,
            value={
                "prompt": request.prompt,
                "workspace": str(request.workspace),
                "model": request.model,
                "reasoning_effort": request.reasoning_effort,
                "arm": request.arm.model_dump(mode="json"),
                "timeout_seconds": request.timeout_seconds,
                "grace_seconds": request.grace_seconds,
            },
        )
        argv = [
            sys.executable,
            "-m",
            "benchmarks.workspacebench.session",
            "--request",
            str(request_path),
            "--output",
            str(output_path),
        ]
        supervised = supervisor(
            argv=argv,
            cwd=request.workspace,
            timeout_seconds=request.timeout_seconds,
            grace_seconds=request.grace_seconds,
            env=sanitized_subprocess_env(),
        )
        if supervised.timed_out:
            partial = _load_turn(output_path)
            return CodexTaskTurn(
                status="timeout",
                error_message=(
                    "task session exceeded the wall-clock deadline. "
                    + SDK_TIMEOUT_PARTIAL_EVENTS
                ),
                final_response=partial.final_response if partial else None,
                tokens_in=partial.tokens_in if partial else None,
                tokens_out=partial.tokens_out if partial else None,
                item_types=partial.item_types if partial else (),
                events=partial.events if partial else (),
                timed_out=True,
            )
        loaded = _load_turn(output_path)
        if loaded is None:
            stderr = supervised.stderr.decode("utf-8", errors="replace")
            raise WorkspaceBenchError(
                stderr.strip() or "supervised Codex session produced no turn receipt"
            )
        return loaded
    finally:
        _unlink_quietly(request_path)
        _unlink_quietly(output_path)


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        return


def _load_turn(path: Path) -> CodexTaskTurn | None:
    from benchmarks.workspacebench.codex import CodexRuntimeEvent

    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    events = tuple(
        CodexRuntimeEvent(
            item_type=str(item.get("item_type")),
            payload=dict(item.get("payload") or {}),
        )
        for item in payload.get("events") or ()
        if isinstance(item, dict)
    )
    return CodexTaskTurn(
        status=str(payload.get("status") or "unknown"),
        error_message=(
            str(payload["error_message"])
            if payload.get("error_message") is not None
            else None
        ),
        final_response=(
            str(payload["final_response"])
            if payload.get("final_response") is not None
            else None
        ),
        tokens_in=payload.get("tokens_in")
        if isinstance(payload.get("tokens_in"), int)
        else None,
        tokens_out=(
            payload.get("tokens_out")
            if isinstance(payload.get("tokens_out"), int)
            else None
        ),
        item_types=tuple(str(item) for item in payload.get("item_types") or ()),
        events=events,
        timed_out=bool(payload.get("timed_out")),
    )


def _classify_failure(
    *, turn: CodexTaskTurn, violations: tuple[str, ...], outputs, canary_passed: bool
) -> tuple[FailureClass, str | None]:
    if not canary_passed:
        return "credential_isolation", "credential-isolation canary failed"
    if turn.timed_out:
        return "timeout", turn.error_message or "timed out"
    if violations:
        return "protocol_violation", "; ".join(violations)
    if outputs.rejected:
        return "invalid_output", "rejected output paths: " + ", ".join(outputs.rejected)
    if outputs.missing:
        return "missing_output", "missing output paths: " + ", ".join(outputs.missing)
    if turn.status != "completed":
        return "partial_artifact", turn.error_message or turn.status
    return "none", None


def _empty_traces(*, raw_path: Path, sanitized_path: Path) -> TraceManifest:
    write_jsonl(path=raw_path, rows=())
    write_jsonl(path=sanitized_path, rows=())
    return TraceManifest(
        raw_relpath=raw_path.name, sanitized_relpath=sanitized_path.name
    )


def _envelope(
    *,
    attempt_id: str,
    task_id: str,
    arm: ArmConfiguration,
    protocol,
    workspace_digest: str,
    account: AccountAttestation | None,
    started: datetime,
    timeout_seconds: float,
    grace_seconds: float,
    turn: CodexTaskTurn,
    outputs,
    traces: TraceManifest,
    failure_class: FailureClass,
    failure_detail: str | None,
    violations: tuple[str, ...],
    memory,
    arm_execution_fingerprint: str | None = None,
) -> TaskResult:
    finished = datetime.now(tz=UTC)
    elapsed = max(0, int((finished - started).total_seconds() * 1000))
    return TaskResult(
        attempt_id=attempt_id,
        task_id=task_id,
        arm=arm.arm,
        protocol=protocol,
        workspace_digest=workspace_digest,
        account=account,
        arm_configuration=arm,
        usage=TokenUsage(
            tokens_in=turn.tokens_in,
            tokens_out=turn.tokens_out,
            cost_usd=subscription_cost(),
            model=protocol.model,
        ),
        timing=Timing(
            started_at=started,
            finished_at=finished,
            elapsed_ms=elapsed,
            timeout_seconds=timeout_seconds,
            timed_out=turn.timed_out,
            grace_seconds=grace_seconds,
        ),
        failure_class=failure_class,
        failure_detail=failure_detail,
        outputs=outputs,
        traces=traces,
        memory=memory,
        protocol_violations=violations,
        experimental_label=PROTOCOL_EXPERIMENTAL_LABEL,
        arm_execution_fingerprint=arm_execution_fingerprint,
    )


def _persist(*, output: Path, result: TaskResult) -> None:
    atomic_write_json(path=output / "result.json", value=result)


def _write_agent_json(
    *,
    path: Path,
    task_id: str,
    workspace: Path,
    prompt: str,
    turn: CodexTaskTurn,
    result: TaskResult,
    source_task_dir: Path | None,
) -> None:
    payload = {
        "caseId": task_id,
        "name": task_id,
        "workDir": str(workspace.resolve()),
        "status": "passed" if result.failure_class == "none" else result.failure_class,
        "runnerStatus": turn.status,
        "partialOutputCollected": bool(result.outputs.files)
        and result.failure_class != "none",
        "durationMs": result.timing.elapsed_ms,
        "errorMessage": turn.error_message,
        "sourceTaskDir": None if source_task_dir is None else str(source_task_dir),
        "trace": {
            "prompt": {"system": None, "user": prompt},
            "lastText": turn.final_response,
            "executionTrace": judge_execution_trace(
                events_as_jsonable(turn.events), secrets=(FAKE_CANARY_SECRET,)
            ),
            "outputs": {
                "outputManifest": [
                    {
                        "sourcePath": item.relative_path,
                        "outputPath": item.relative_path,
                        "sizeBytes": item.size_bytes,
                    }
                    for item in result.outputs.files
                ]
            },
        },
    }
    atomic_write_json(path=path, value=payload, mode=OWNER_ONLY_FILE_MODE)


def _require_live_runtime_pin(protocol: ProtocolCoordinates) -> None:
    if protocol.codex_cli_sha256 is None:
        raise LiveGateError(
            "live Codex execution requires the observed CLI SHA-256 on the protocol"
        )
    observed = inspect_bundled_codex_cli(force=True)
    if observed.cli_sha256 != protocol.codex_cli_sha256:
        raise LiveGateError(
            "bundled Codex CLI SHA-256 changed between preflight and execution"
        )


def default_native_arm() -> ArmConfiguration:
    """Native-arm configuration with no Remember MCP."""
    return native_arm_configuration()


def live_sdk_turn_runner() -> CodexTurnRunner:
    """Direct in-process SDK runner. Prefer the supervised child for live tasks."""
    return run_codex_task
