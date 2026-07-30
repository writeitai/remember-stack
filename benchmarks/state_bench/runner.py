"""Prepare runs, load manifests, and plan parallel evaluation cells."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Iterable
from typing import Mapping

from benchmarks.state_bench.model import ArmKey
from benchmarks.state_bench.model import Domain
from benchmarks.state_bench.model import MatrixCell
from benchmarks.state_bench.model import MatrixPlan
from benchmarks.state_bench.model import RunConfiguration
from benchmarks.state_bench.model import SubProtocol
from benchmarks.state_bench.model import TaskManifest
from benchmarks.state_bench.model import Tier
from benchmarks.state_bench.model import TrajectoryDocument
from benchmarks.state_bench.protocol import ADAPTER_VERSION
from benchmarks.state_bench.protocol import ARM_SUB_PROTOCOLS
from benchmarks.state_bench.protocol import DEFAULT_RECIPE_NAME
from benchmarks.state_bench.protocol import DEFAULT_TOP_K
from benchmarks.state_bench.protocol import DOMAINS
from benchmarks.state_bench.protocol import OFFICIAL_NUM_RUNS
from benchmarks.state_bench.protocol import PROTOCOL_NAME
from benchmarks.state_bench.protocol import RENDER_FORMAT_VERSION
from benchmarks.state_bench.protocol import SINGLE_DOMAIN_ARMS
from benchmarks.state_bench.protocol import STATE_BENCH_COMMIT
from benchmarks.state_bench.protocol import STATE_BENCH_PROTOCOL_ID
from benchmarks.state_bench.protocol import STATE_BENCH_VERSION
from benchmarks.state_bench.protocol import TIER_TASKS_PER_DOMAIN
from benchmarks.state_bench.trajectories import assert_no_test_leakage
from benchmarks.state_bench.trajectories import list_train_task_ids
from benchmarks.state_bench.trajectories import serialize_trajectory_document
from benchmarks.state_bench.trajectories import TrajectoryError

MANIFEST_DIR = Path(__file__).resolve().parent / "manifests"
REPO_ROOT = Path(__file__).resolve().parents[2]


class BenchmarkRunError(ValueError):
    """User-facing prepare/plan failure."""


def load_manifest(*, tier: Tier) -> TaskManifest:
    """Load a committed tier manifest."""
    path = MANIFEST_DIR / f"{tier}.json"
    if not path.is_file():
        raise BenchmarkRunError(f"missing manifest: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    domains = {
        domain: tuple(task_ids) for domain, task_ids in dict(raw["domains"]).items()
    }
    return TaskManifest(
        version=int(raw["version"]),
        tier=raw["tier"],
        protocol=raw["protocol"],
        state_bench_version=raw["state_bench_version"],
        state_bench_commit=raw["state_bench_commit"],
        domains=domains,
        task_counts={key: int(value) for key, value in raw["task_counts"].items()},
        item_ids_sha256=raw["item_ids_sha256"],
    )


def validate_manifest(manifest: TaskManifest) -> None:
    """Check tier sizes, domain set, and content hash."""
    if manifest.protocol != PROTOCOL_NAME:
        raise BenchmarkRunError(
            f"manifest protocol {manifest.protocol!r} != {PROTOCOL_NAME!r}"
        )
    if manifest.state_bench_commit != STATE_BENCH_COMMIT:
        raise BenchmarkRunError(
            "manifest state_bench_commit does not match protocol pin "
            f"{STATE_BENCH_COMMIT}"
        )
    expected_n = TIER_TASKS_PER_DOMAIN[manifest.tier]
    for domain in DOMAINS:
        ids = manifest.domains.get(domain)
        if ids is None:
            raise BenchmarkRunError(f"manifest missing domain {domain}")
        if len(ids) != expected_n:
            raise BenchmarkRunError(
                f"{manifest.tier}/{domain}: expected {expected_n} tasks, got {len(ids)}"
            )
        if len(set(ids)) != len(ids):
            raise BenchmarkRunError(f"duplicate task ids in {domain}")
    blob = json.dumps(
        {domain: list(ids) for domain, ids in manifest.domains.items()},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()
    if digest != manifest.item_ids_sha256:
        raise BenchmarkRunError("manifest item_ids_sha256 mismatch")


def repository_revision(*, cwd: Path | None = None) -> str:
    """Return git HEAD of the adapter repository (repo root by default)."""
    root = cwd or REPO_ROOT
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise BenchmarkRunError(f"cannot read git revision: {error}") from error
    return completed.stdout.strip()


def assert_clean_worktree(*, cwd: Path | None = None) -> None:
    """Refuse prepare when the adapter tree is dirty (D78 integrity rule)."""
    root = cwd or REPO_ROOT
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise BenchmarkRunError(f"cannot inspect git worktree: {error}") from error
    if completed.stdout.strip():
        raise BenchmarkRunError(
            "prepare requires a clean adapter worktree "
            "(commit or stash staged/untracked changes first)"
        )


def prepare_run(
    *,
    output: Path,
    tier: Tier,
    arm: ArmKey,
    sub_protocol: SubProtocol,
    state_bench_root: Path,
    agent_model_name: str,
    agent_model_reasoning_level: str | None = None,
    num_runs: int | None = None,
    top_k: int = DEFAULT_TOP_K,
    recipe_name: str = DEFAULT_RECIPE_NAME,
    max_evaluator_cost_usd: float = 50.0,
    domains: tuple[Domain, ...] | None = None,
    allow_dirty: bool = False,
) -> RunConfiguration:
    """Write an immutable run directory (no provider calls)."""
    allowed = ARM_SUB_PROTOCOLS.get(arm, ())
    if sub_protocol not in allowed:
        raise BenchmarkRunError(
            f"arm {arm!r} does not support sub-protocol {sub_protocol!r}; "
            f"allowed={allowed}"
        )
    if sub_protocol == "native":
        raise BenchmarkRunError(
            "sub-protocol 'native' is not implemented yet "
            "(raw-trajectory ingest); use --sub-protocol shared"
        )

    manifest = load_manifest(tier=tier)
    validate_manifest(manifest)
    selected_domains: tuple[Domain, ...] = domains or tuple(DOMAINS)  # type: ignore[assignment]
    for domain in selected_domains:
        if domain not in DOMAINS:
            raise BenchmarkRunError(f"unknown domain {domain}")
    if arm in SINGLE_DOMAIN_ARMS and len(selected_domains) != 1:
        raise BenchmarkRunError(
            f"arm {arm!r} requires exactly one --domain "
            "(one deployment / document pool per domain)"
        )

    if not allow_dirty:
        assert_clean_worktree()

    train_root = state_bench_root / "datasets" / "train_task_trajectories"
    if not train_root.is_dir():
        raise BenchmarkRunError(
            f"STATE-Bench train trajectories missing under {train_root}"
        )
    _assert_upstream_pin(state_bench_root=state_bench_root)

    for domain in selected_domains:
        train_ids = set(list_train_task_ids(trajectories_dir=train_root / domain))
        test_ids = set(manifest.domains[domain])
        assert_no_test_leakage(train_task_ids=train_ids, test_task_ids=test_ids)

    if output.exists() and any(output.iterdir()):
        raise BenchmarkRunError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    documents = _serialize_all_train(
        train_root=train_root,
        domains=selected_domains,
        documents_dir=output / "documents",
    )
    # Root documents.json remains for empty/multi-domain tooling; RS/BM25 use
    # domain-scoped files so retrieval cannot cross domains.
    (output / "documents.json").write_text(
        json.dumps(
            [document.model_dump(mode="json") for document in documents], indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    for domain in selected_domains:
        domain_docs = [document for document in documents if document.domain == domain]
        domain_path = output / "documents" / domain / "documents.json"
        domain_path.parent.mkdir(parents=True, exist_ok=True)
        domain_path.write_text(
            json.dumps(
                [document.model_dump(mode="json") for document in domain_docs], indent=2
            )
            + "\n",
            encoding="utf-8",
        )

    resolved_runs = (
        num_runs
        if num_runs is not None
        else (1 if tier != "publication" else OFFICIAL_NUM_RUNS)
    )
    selected_blob = json.dumps(
        {domain: list(manifest.domains[domain]) for domain in selected_domains},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    configuration = RunConfiguration(
        protocol=PROTOCOL_NAME,
        adapter_version=ADAPTER_VERSION,
        state_bench_commit=STATE_BENCH_COMMIT,
        state_bench_version=STATE_BENCH_VERSION,
        state_bench_protocol_id=STATE_BENCH_PROTOCOL_ID,
        tier=tier,
        arm=arm,
        sub_protocol=sub_protocol,
        domains=selected_domains,
        repository_revision=repository_revision(),
        recipe_name=recipe_name,
        render_format_version=RENDER_FORMAT_VERSION,
        top_k=top_k,
        num_runs=resolved_runs,
        agent_model_name=agent_model_name,
        agent_model_reasoning_level=agent_model_reasoning_level,
        manifest_sha256=hashlib.sha256(selected_blob).hexdigest(),
        max_evaluator_cost_usd=max_evaluator_cost_usd,
        state_bench_root=str(state_bench_root.resolve()),
        train_trajectories_root=str(train_root.resolve()),
    )
    (output / "run.json").write_text(
        configuration.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "tier": manifest.tier,
                "item_ids_sha256": configuration.manifest_sha256,
                "full_tier_item_ids_sha256": manifest.item_ids_sha256,
                "domains": {
                    domain: list(manifest.domains[domain])
                    for domain in selected_domains
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "state.json").write_text(
        json.dumps(
            {
                "phase": "prepared",
                "document_count": len(documents),
                "ingest": {},
                "eval": {},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return configuration


def plan_matrix(
    *,
    tier: Tier,
    arms: Iterable[ArmKey],
    sub_protocols: Iterable[SubProtocol],
    domains: Iterable[Domain] | None = None,
    num_runs: int | None = None,
    num_workers: int = 8,
) -> MatrixPlan:
    """Build arm × sub-protocol × domain cells, skipping invalid pairs."""
    manifest = load_manifest(tier=tier)
    validate_manifest(manifest)
    selected_domains: tuple[Domain, ...] = tuple(domains or DOMAINS)  # type: ignore[assignment]
    resolved_runs = (
        num_runs
        if num_runs is not None
        else (1 if tier != "publication" else OFFICIAL_NUM_RUNS)
    )
    cells: list[MatrixCell] = []
    for arm in arms:
        allowed = set(ARM_SUB_PROTOCOLS.get(arm, ()))
        for sub_protocol in sub_protocols:
            if sub_protocol not in allowed:
                continue
            if sub_protocol == "native":
                # Not implemented; never schedule.
                continue
            for domain in selected_domains:
                cells.append(
                    MatrixCell(
                        arm=arm,
                        sub_protocol=sub_protocol,
                        domain=domain,
                        tier=tier,
                        num_runs=resolved_runs,
                        num_workers=num_workers,
                        task_ids=tuple(manifest.domains[domain]),
                    )
                )
    return MatrixPlan(
        protocol=PROTOCOL_NAME, tier=tier, cells=tuple(cells), cell_count=len(cells)
    )


def preflight_episode_estimate(*, plan: MatrixPlan) -> Mapping[str, int | float]:
    """Order-of-magnitude episode count for cost preflight (not a USD quote)."""
    episodes = 0
    for cell in plan.cells:
        episodes += len(cell.task_ids) * cell.num_runs
    return {
        "cells": plan.cell_count,
        "episodes": episodes,
        "note": "each episode is multi-turn agent+simulator+judge; multiply by provider rates",
    }


def _serialize_all_train(
    *, train_root: Path, domains: tuple[Domain, ...], documents_dir: Path
) -> tuple[TrajectoryDocument, ...]:
    documents_dir.mkdir(parents=True, exist_ok=True)
    documents: list[TrajectoryDocument] = []
    for domain in domains:
        domain_dir = train_root / domain
        for task_id in list_train_task_ids(trajectories_dir=domain_dir):
            path = domain_dir / f"{task_id}.json"
            document = serialize_trajectory_document(
                domain=domain, task_id=task_id, trajectory_path=path
            )
            target = documents_dir / domain / f"{task_id}.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(document.markdown, encoding="utf-8")
            documents.append(document)
    return tuple(documents)


def _assert_upstream_pin(*, state_bench_root: Path) -> None:
    pin = state_bench_root / "STATE_BENCH_PIN"
    if pin.is_file() and pin.read_text(encoding="utf-8").strip() == STATE_BENCH_COMMIT:
        return
    try:
        completed = subprocess.run(
            ["git", "-C", str(state_bench_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise BenchmarkRunError(f"cannot read STATE-Bench revision: {error}") from error
    head = completed.stdout.strip()
    if head != STATE_BENCH_COMMIT:
        raise BenchmarkRunError(
            f"STATE-Bench HEAD {head} does not match pin {STATE_BENCH_COMMIT}"
        )
    dirty = subprocess.run(
        ["git", "-C", str(state_bench_root), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    if dirty.stdout.strip():
        raise BenchmarkRunError(
            "STATE-Bench checkout is dirty; refuse to fingerprint a modified pin"
        )


def load_run_configuration(run_dir: Path) -> RunConfiguration:
    """Load prepared run.json."""
    path = run_dir / "run.json"
    if not path.is_file():
        raise BenchmarkRunError(f"missing run.json under {run_dir}")
    return RunConfiguration.model_validate_json(path.read_text(encoding="utf-8"))


# Re-export for tests
__all__ = [
    "BenchmarkRunError",
    "TrajectoryError",
    "load_manifest",
    "validate_manifest",
    "prepare_run",
    "plan_matrix",
    "preflight_episode_estimate",
    "load_run_configuration",
    "repository_revision",
]
