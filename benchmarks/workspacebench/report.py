"""Deterministic paired-report reconstruction from raw receipts."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from benchmarks.workspacebench.errors import WorkspaceBenchError
from benchmarks.workspacebench.hashing import atomic_write_json
from benchmarks.workspacebench.models import PairedReport
from benchmarks.workspacebench.models import PairedRunManifest
from benchmarks.workspacebench.models import TaskResult
from benchmarks.workspacebench.protocol import PROTOCOL_EXPERIMENTAL_LABEL


def load_task_result(path: Path) -> TaskResult:
    """Load one arm result envelope."""
    try:
        return TaskResult.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as error:
        raise WorkspaceBenchError(f"invalid task result {path}: {error}") from error


def load_pair_manifest(path: Path) -> PairedRunManifest:
    """Load the paired-run manifest."""
    try:
        return PairedRunManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as error:
        raise WorkspaceBenchError(f"invalid pair manifest {path}: {error}") from error


def reconstruct_paired_report(
    *, pair: PairedRunManifest, native: TaskResult, memory: TaskResult
) -> PairedReport:
    """Build the paired report. Does not infer quality from a single task."""
    if native.arm != "native" or memory.arm != "memory":
        raise WorkspaceBenchError("paired report requires native and memory results")
    if native.task_id != pair.task_id or memory.task_id != pair.task_id:
        raise WorkspaceBenchError("paired results do not match the pair task id")
    if native.workspace_digest != pair.native_workspace_digest:
        raise WorkspaceBenchError("native workspace digest does not match the pair")
    if memory.workspace_digest != pair.memory_workspace_digest:
        raise WorkspaceBenchError("memory workspace digest does not match the pair")
    if native.protocol.protocol_fingerprint != pair.protocol_fingerprint:
        raise WorkspaceBenchError(
            "native protocol fingerprint does not match the pair protocol fingerprint"
        )
    if memory.protocol.protocol_fingerprint != pair.protocol_fingerprint:
        raise WorkspaceBenchError(
            "memory protocol fingerprint does not match the pair protocol fingerprint"
        )
    native_fp = (
        native.arm_execution_fingerprint or native.protocol.arm_execution_fingerprint
    )
    memory_fp = (
        memory.arm_execution_fingerprint or memory.protocol.arm_execution_fingerprint
    )
    if pair.native_arm_fingerprint and native_fp != pair.native_arm_fingerprint:
        raise WorkspaceBenchError(
            "native arm execution fingerprint does not match the pair"
        )
    if pair.memory_arm_fingerprint and memory_fp != pair.memory_arm_fingerprint:
        raise WorkspaceBenchError(
            "memory arm execution fingerprint does not match the pair"
        )
    return PairedReport(
        pair=pair,
        native=native,
        memory=memory,
        experimental_label=PROTOCOL_EXPERIMENTAL_LABEL,
    )


def write_paired_report(*, output: Path, report: PairedReport) -> Path:
    """Atomically persist the reconstructed report."""
    path = output / "paired-report.json"
    atomic_write_json(path=path, value=report)
    return path


def reconstruct_from_output_dir(output: Path) -> PairedReport:
    """Rebuild the paired report from ``pair.json`` and arm result files."""
    pair = load_pair_manifest(output / "pair.json")
    native = load_task_result(output / pair.native_result_relpath)
    memory = load_task_result(output / pair.memory_result_relpath)
    return reconstruct_paired_report(pair=pair, native=native, memory=memory)
