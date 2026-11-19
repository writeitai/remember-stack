"""Prepare a fresh, fully guarded serving run from original paid ingestion."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import ValidationError

from benchmarks.locomo import runner
from benchmarks.locomo.model import RetainedIngestionOrigin
from benchmarks.locomo.model import RunConfiguration
from benchmarks.locomo.model import RunState
from benchmarks.locomo.protocol import ADAPTER_VERSION
from benchmarks.locomo.protocol import DEFAULT_PROTOCOL_KEY
from benchmarks.locomo.protocol import protocol_for_key
from remember.client import MemoryClient
from rememberstack.model import ReadinessRequirements

_ORIGIN = Path("provenance/retained-ingestion")


def validate_origin(*, run_dir: Path, configuration: RunConfiguration) -> None:
    """Reject a changed origin or a fork whose inputs differ from paid processing."""
    origin = configuration.retained_ingestion
    if origin is None:
        return
    root = run_dir / _ORIGIN
    try:
        original_config = (root / "run.json").read_bytes()
        original_state = (root / "state.json").read_bytes()
        source = RunConfiguration.model_validate_json(original_config)
        state = RunState.model_validate_json(original_state)
    except (OSError, ValidationError) as error:
        raise runner.BenchmarkRunError(
            "retained ingestion provenance is missing or invalid"
        ) from error
    if (
        hashlib.sha256(original_config).hexdigest() != origin.configuration_sha256
        or hashlib.sha256(original_state).hexdigest() != origin.state_sha256
    ):
        raise runner.BenchmarkRunError("retained ingestion provenance bytes changed")
    if (
        source.retained_ingestion is not None
        or source.repository_revision != origin.processing_revision
        or source.protocol_fingerprint != origin.protocol_fingerprint
        or state.protocol_fingerprint != origin.protocol_fingerprint
        or origin.sample_id not in state.readiness
    ):
        raise runner.BenchmarkRunError("retained ingestion origin identity changed")
    for field in (
        "protocol_name",
        "adapter_version",
        "dataset_commit",
        "dataset_sha256",
        "tier",
        "manifest_sha256",
        "item_ids_sha256",
        "documents_sha256",
        "image_inputs_sha256",
        "sample_ids",
        "execution_profile",
        "document_binding_generation",
    ):
        if getattr(source, field) != getattr(configuration, field):
            raise runner.ExecutionGuardError(
                f"retained ingestion {field} differs from current execution"
            )


def prepare_reused_run(
    *,
    source_run: Path,
    output: Path,
    sample_id: str,
    execute: bool,
    client: MemoryClient,
) -> RunConfiguration:
    """Attest existing data and derive a new execution identity without model calls."""
    if not execute:
        raise runner.ExecutionGuardError("reuse-ingestion requires --execute")
    if runner._repository_dirty():
        raise runner.ExecutionGuardError("reuse-ingestion requires a clean worktree")
    if (
        output.resolve() == source_run.resolve()
        or source_run.resolve() in output.resolve().parents
    ):
        raise runner.ExecutionGuardError(
            "retained output must be outside its original run"
        )
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise runner.ExecutionGuardError("retained output must be an empty directory")
    original_files = {
        name: (source_run / name).read_bytes() for name in ("run.json", "state.json")
    }
    source = runner.load_recorded_run(run_dir=source_run)
    if any(
        (source_run / name).read_bytes() != data
        for name, data in original_files.items()
    ):
        raise runner.ExecutionGuardError(
            "original ingestion checkpoint changed while loading"
        )
    if source.configuration.retained_ingestion is not None:
        raise runner.ExecutionGuardError(
            "reuse the original processing run, not an earlier scoring fork"
        )
    if source.configuration.adapter_version != ADAPTER_VERSION:
        raise runner.ExecutionGuardError(
            "original document adapter differs from current code"
        )
    if sample_id not in source.configuration.sample_ids:
        raise runner.ExecutionGuardError("sample is absent from the original run")
    records = tuple(
        row for row in source.state.ingests.values() if row.sample_id == sample_id
    )
    checkpointed = source.state.readiness.get(sample_id)
    if (
        not records
        or checkpointed is None
        or not runner._readiness_matches_protocol(
            readiness=checkpointed,
            version_ids={row.version_id for row in records},
            repository_revision=source.configuration.repository_revision,
            protocol_name=source.configuration.protocol_name,
        )
    ):
        raise runner.ExecutionGuardError(
            "original ingestion lacks complete compatible readiness"
        )
    current = protocol_for_key(DEFAULT_PROTOCOL_KEY)
    if source.configuration.execution_profile != current.execution_profile:
        raise runner.ExecutionGuardError(
            "original processing generations or model profile differs from current code"
        )
    revision = runner._repository_revision()
    live = client.pipeline_readiness(
        version_ids=tuple(row.version_id for row in records),
        require=ReadinessRequirements(pipeline=True, p1=True, live_graph=True, p3=True),
    )
    if not runner._readiness_matches_protocol(
        readiness=live,
        version_ids={row.version_id for row in records},
        repository_revision=revision,
        protocol_name=current.name,
    ):
        raise runner.ExecutionGuardError(
            "current serving runtime lacks matching processing readiness"
        )
    origin = RetainedIngestionOrigin(
        sample_id=sample_id,
        processing_revision=source.configuration.repository_revision,
        protocol_fingerprint=source.configuration.protocol_fingerprint,
        configuration_sha256=hashlib.sha256(original_files["run.json"]).hexdigest(),
        state_sha256=hashlib.sha256(original_files["state.json"]).hexdigest(),
    )
    configuration = runner.prepare_run(
        dataset_path=Path(source.configuration.dataset_path),
        tier=source.configuration.tier,
        output=output,
        protocol=DEFAULT_PROTOCOL_KEY,
        retrieval_budget=source.configuration.retrieval_budget,
        images=source_run / "image-inputs"
        if source.configuration.image_inputs_sha256 is not None
        else None,
        retained_ingestion=origin,
    )
    root = output / _ORIGIN
    root.mkdir(parents=True)
    for name, data in original_files.items():
        (root / name).write_bytes(data)
    validate_origin(run_dir=output, configuration=configuration)
    derived = runner._load_run(run_dir=output)
    runner._require_current_query_surface(context=derived, client=client)
    runner._require_exact_live_ingests(
        client=client,
        expected_surface_manifest_hash=configuration.surface_manifest_hash,
        expected=records,
    )
    # Per-version processing rows/clocks must be identical. Only the serving
    # build and fully qualified projection capabilities may change on upgrade.
    old_versions = {
        str(row.version_id): row.model_dump(mode="json")
        for row in checkpointed.versions
    }
    if {
        str(row.version_id): row.model_dump(mode="json") for row in live.versions
    } != old_versions:
        raise runner.ExecutionGuardError("retained document processing rows changed")
    derived.state.ingests = {row.source_ref: row for row in records}
    derived.state.readiness[sample_id] = live
    runner._save_state(run_dir=output, state=derived.state)
    if any(
        (source_run / name).read_bytes() != data
        for name, data in original_files.items()
    ):
        raise runner.ExecutionGuardError(
            "original ingestion checkpoint changed during preparation"
        )
    # The returned run passes all ordinary current-build/surface guards.
    runner._load_run(run_dir=output)
    return configuration
