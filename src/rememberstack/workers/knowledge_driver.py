"""Deterministic Plane-K routing, scheduling, and single-commit driver."""

from collections.abc import Collection
from collections.abc import Mapping
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from pathlib import PurePosixPath
from tempfile import TemporaryDirectory
import traceback
from typing import Final
from typing import Protocol
from uuid import UUID
from uuid import uuid4

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.core import knowledge_compile_order
from rememberstack.core import knowledge_content_hash
from rememberstack.core import validate_knowledge_page_output
from rememberstack.model import KnowledgeCommitCycleResult
from rememberstack.model import KnowledgeCompilationFailure
from rememberstack.model import KnowledgeCompileArtifact
from rememberstack.model import KnowledgeCompileContext
from rememberstack.model import KnowledgeConvertKindProposal
from rememberstack.model import KnowledgeEvidenceDelta
from rememberstack.model import KnowledgeEvidenceTarget
from rememberstack.model import KnowledgeMergePagesProposal
from rememberstack.model import KnowledgeMovePageProposal
from rememberstack.model import KnowledgePageCompileOutput
from rememberstack.model import KnowledgePageCompileRequest
from rememberstack.model import KnowledgePageKind
from rememberstack.model import KnowledgePendingCycle
from rememberstack.model import KnowledgePendingPlanDecision
from rememberstack.model import KnowledgeRetirePageProposal
from rememberstack.ports import KGitRemotePort
from rememberstack.spine.knowledge import KnowledgeControlPlane
from rememberstack.workers.knowledge_authored import KnowledgeAuthoredSynchronizer

KNOWLEDGE_DRIVER_VERSION: Final = "k-driver-2026.07"


class KnowledgeCommitSettings(BaseSettings):
    """Settings-owned bound on concurrent disjoint page compilers."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_K_DRIVER_")

    max_parallel_pages: int = Field(gt=0)


class KnowledgePageCompiler(Protocol):
    """The future WP-6.3/6.4 page compiler consumed by this mechanical loop."""

    def compile_page(
        self, *, request: KnowledgePageCompileRequest
    ) -> KnowledgePageCompileOutput:
        """Return one structured page without publishing or mutating another page."""
        ...


class KnowledgeRoutingDriver:
    """Route an evidence delta, recompute manifests, and mark exact stale pages."""

    def __init__(self, *, control_plane: KnowledgeControlPlane) -> None:
        """Bind the deterministic driver to its Postgres control plane."""
        self._control_plane = control_plane

    def route_and_mark_stale(
        self,
        *,
        deployment_id: UUID,
        delta: KnowledgeEvidenceDelta,
        contexts: Mapping[UUID, KnowledgeCompileContext],
        tombstone: bool = False,
    ) -> tuple[UUID, ...]:
        """Narrow by keys/citations, then mark only manifest mismatches.

        Routing may intentionally over-select because four inverted key kinds
        cannot encode every rule parameter. The complete manifest comparison
        is the correctness gate, so a coarse match can never fabricate stale
        state.
        """
        routed = self._control_plane.route_delta(
            deployment_id=deployment_id, delta=delta
        )
        stale = self._control_plane.stale_artifacts(
            deployment_id=deployment_id, contexts=contexts, artifact_ids=routed
        )
        marked = self._control_plane.mark_stale(artifacts=stale)
        self._control_plane.route_notifications(
            deployment_id=deployment_id, delta=delta, tombstone=tombstone
        )
        return marked

    def mark_all_manifest_drift(
        self, *, deployment_id: UUID, contexts: Mapping[UUID, KnowledgeCompileContext]
    ) -> tuple[UUID, ...]:
        """Catch sidecar, summary, rule, or writer-version drift without an E delta."""
        stale = self._control_plane.stale_artifacts(
            deployment_id=deployment_id, contexts=contexts
        )
        return self._control_plane.mark_stale(artifacts=stale)


class KnowledgeCommitDriver:
    """Compile a dependency-ordered batch and publish it through one git writer."""

    def __init__(
        self,
        *,
        control_plane: KnowledgeControlPlane,
        git_remote: KGitRemotePort,
        compiler: KnowledgePageCompiler,
        settings: KnowledgeCommitSettings,
    ) -> None:
        """Bind the commit loop to its Postgres, git, and page-compiler seams."""
        self._control_plane = control_plane
        self._git_remote = git_remote
        self._compiler = compiler
        self._settings = settings
        self._authored = KnowledgeAuthoredSynchronizer(control_plane=control_plane)

    def run_cycle(
        self,
        *,
        deployment_id: UUID,
        exclusions_by_artifact: Mapping[UUID, Collection[KnowledgeEvidenceTarget]],
    ) -> KnowledgeCommitCycleResult:
        """Recover prior work, compile stale pages, publish once, then finalize."""
        with self._control_plane.commit_lease(deployment_id=deployment_id):
            with TemporaryDirectory(prefix="rememberstack-k-cycle-") as temporary:
                worktree = Path(temporary)
                checkout = self._git_remote.checkout(destination=worktree)
                recovered = self._recover_pending_cycles(
                    deployment_id=deployment_id,
                    worktree=worktree,
                    git_revision=checkout.root,
                )
                authored = self._authored.sync_checkout(
                    deployment_id=deployment_id,
                    worktree=worktree,
                    git_revision=checkout.root,
                )
                self._control_plane.materialize_due_dispatches(
                    deployment_id=deployment_id,
                    component_version=KNOWLEDGE_DRIVER_VERSION,
                )
                pending_decisions = self._control_plane.pending_plan_decisions(
                    deployment_id=deployment_id
                )
                plan_files_changed = _reconcile_plan_files(
                    decisions=pending_decisions, worktree=worktree
                )
                quarantined = self._quarantine_compiled_drift(
                    deployment_id=deployment_id, worktree=worktree
                )
                artifacts = self._control_plane.compile_artifacts(
                    deployment_id=deployment_id
                )
                schedule = knowledge_compile_order(artifacts=artifacts)
                if not schedule:
                    revision = checkout.root
                    if plan_files_changed:
                        revision = self._git_remote.publish(worktree=worktree).root
                    stamped = self._control_plane.stamp_ready_plan_decisions(
                        deployment_id=deployment_id,
                        git_commit=revision,
                        present_paths=_present_paths(worktree=worktree),
                    )
                    return KnowledgeCommitCycleResult(
                        checkout_revision=checkout.root,
                        published_revision=(
                            revision if revision != checkout.root else None
                        ),
                        recovered_cycle_ids=recovered,
                        quarantined_artifact_ids=quarantined,
                        stamped_plan_decision_ids=stamped,
                        registered_authored_artifact_ids=(
                            authored.registered_artifact_ids
                        ),
                        synced_authored_artifact_ids=authored.synced_artifact_ids,
                        authored_lint_flag_artifact_ids=(
                            authored.lint_flag_artifact_ids
                        ),
                    )

                known_paths = self._control_plane.artifact_git_paths(
                    deployment_id=deployment_id
                )
                compiled = self._compile_schedule(
                    artifacts=artifacts,
                    schedule=schedule,
                    known_paths=known_paths,
                    worktree=worktree,
                    exclusions_by_artifact=exclusions_by_artifact,
                )
                for artifact, output in compiled:
                    _write_compiled_page(
                        worktree=worktree,
                        git_path=artifact.git_path,
                        markdown=output.markdown,
                    )

                cycle_id = uuid4()
                compilations = tuple(output.compilation for _, output in compiled)
                self._control_plane.record_pending_compilations(
                    cycle_id=cycle_id, compilations=compilations
                )
                published = self._git_remote.publish(worktree=worktree)
                self._control_plane.commit_compilations(
                    compilations=compilations, git_commit=published.root
                )
                stamped = self._control_plane.stamp_ready_plan_decisions(
                    deployment_id=deployment_id,
                    git_commit=published.root,
                    present_paths=_present_paths(worktree=worktree),
                )
                return KnowledgeCommitCycleResult(
                    checkout_revision=checkout.root,
                    published_revision=published.root,
                    compiled_artifact_ids=tuple(
                        artifact.artifact_id for artifact, _ in compiled
                    ),
                    recovered_cycle_ids=recovered,
                    quarantined_artifact_ids=quarantined,
                    stamped_plan_decision_ids=stamped,
                    registered_authored_artifact_ids=(authored.registered_artifact_ids),
                    synced_authored_artifact_ids=authored.synced_artifact_ids,
                    authored_lint_flag_artifact_ids=authored.lint_flag_artifact_ids,
                )

    def _quarantine_compiled_drift(
        self, *, deployment_id: UUID, worktree: Path
    ) -> tuple[UUID, ...]:
        """Preserve direct compiled-body changes before scheduling any compiler."""
        quarantined: list[UUID] = []
        for state in self._control_plane.compiled_content_states(
            deployment_id=deployment_id
        ):
            target = _worktree_target(worktree=worktree, git_path=state.git_path)
            if target.is_file():
                try:
                    edited_markdown = target.read_text(encoding="utf-8")
                    detected_hash = knowledge_content_hash(markdown=edited_markdown)
                except UnicodeError:
                    edited_markdown = (
                        "# Quarantined compiled-page edit\n\n"
                        "The directly edited body is not valid UTF-8.\n"
                    )
                    detected_hash = knowledge_content_hash(markdown=edited_markdown)
            else:
                edited_markdown = (
                    "# Quarantined compiled-page deletion\n\n"
                    "The compiled body was deleted directly from the checkout.\n"
                )
                detected_hash = knowledge_content_hash(markdown="")
            if detected_hash == state.content_hash:
                continue
            self._control_plane.quarantine_compiled_edit(
                artifact_id=state.artifact_id,
                detected_content_hash=detected_hash,
                edited_markdown=edited_markdown,
                driver_version=KNOWLEDGE_DRIVER_VERSION,
            )
            quarantined.append(state.artifact_id)
        return tuple(quarantined)

    def _recover_pending_cycles(
        self, *, deployment_id: UUID, worktree: Path, git_revision: str
    ) -> tuple[UUID, ...]:
        """Finalize cycles present at HEAD and abandon those never published."""
        artifacts = {
            artifact.artifact_id: artifact
            for artifact in self._control_plane.compile_artifacts(
                deployment_id=deployment_id
            )
        }
        recovered: list[UUID] = []
        for cycle in self._control_plane.pending_cycles(deployment_id=deployment_id):
            if _pending_cycle_matches_worktree(
                cycle=cycle, artifacts=artifacts, worktree=worktree
            ):
                self._control_plane.commit_compilations(
                    compilations=cycle.compilations, git_commit=git_revision
                )
                recovered.append(cycle.cycle_id)
            else:
                self._control_plane.fail_pending_cycle(
                    deployment_id=deployment_id,
                    cycle_id=cycle.cycle_id,
                    failure="remote HEAD does not contain the pending cycle output",
                )
        return tuple(recovered)

    def _compile_schedule(
        self,
        *,
        artifacts: tuple[KnowledgeCompileArtifact, ...],
        schedule: tuple[KnowledgeCompileArtifact, ...],
        known_paths: Collection[str],
        worktree: Path,
        exclusions_by_artifact: Mapping[UUID, Collection[KnowledgeEvidenceTarget]],
    ) -> tuple[tuple[KnowledgeCompileArtifact, KnowledgePageCompileOutput], ...]:
        """Compile disjoint pages by wave while carrying fresh summaries upward."""
        summaries = {
            artifact.artifact_id: artifact.page_summary
            for artifact in artifacts
            if artifact.page_summary is not None
        }
        model_summaries: dict[UUID | None, str] = {
            artifact.scope_id: artifact.page_summary
            for artifact in artifacts
            if artifact.artifact_kind == "model_page"
            and artifact.page_summary is not None
        }
        changed_summaries: set[UUID] = set()
        changed_model_scopes: set[UUID | None] = set()
        outputs: list[tuple[KnowledgeCompileArtifact, KnowledgePageCompileOutput]] = []
        children = {
            artifact.artifact_id: tuple(
                child
                for child in artifacts
                if child.parent_artifact_id == artifact.artifact_id
            )
            for artifact in artifacts
        }
        with ThreadPoolExecutor(
            max_workers=self._settings.max_parallel_pages,
            thread_name_prefix="rememberstack-k-page",
        ) as executor:
            for wave in _compile_waves(artifacts=artifacts, schedule=schedule):
                eligible = tuple(
                    artifact
                    for artifact in wave
                    if artifact.stale
                    or {
                        child.artifact_id for child in children[artifact.artifact_id]
                    }.intersection(changed_summaries)
                    or (
                        artifact.artifact_kind != "model_page"
                        and artifact.scope_id in changed_model_scopes
                    )
                )
                futures: list[
                    tuple[KnowledgeCompileArtifact, Future[KnowledgePageCompileOutput]]
                ] = []
                for artifact in eligible:
                    child_summaries = {
                        child.artifact_id: summaries[child.artifact_id]
                        for child in children[artifact.artifact_id]
                        if child.artifact_id in summaries
                    }
                    futures.append(
                        (
                            artifact,
                            executor.submit(
                                self._compile_one,
                                artifact=artifact,
                                child_summaries=child_summaries,
                                shared_model_summary=(
                                    None
                                    if artifact.artifact_kind == "model_page"
                                    else model_summaries.get(artifact.scope_id)
                                ),
                                known_paths=known_paths,
                                worktree=worktree,
                                exclusions=tuple(
                                    exclusions_by_artifact.get(artifact.artifact_id, ())
                                ),
                            ),
                        )
                    )
                wave_outputs, errors = self._collect_parallel_wave(futures=futures)
                if errors:
                    self._record_aborted_wave(outputs=wave_outputs, errors=errors)
                for artifact, output in wave_outputs:
                    new_summary = output.compilation.page_summary
                    if new_summary != artifact.page_summary:
                        changed_summaries.add(artifact.artifact_id)
                        if artifact.artifact_kind == "model_page":
                            changed_model_scopes.add(artifact.scope_id)
                    summaries[artifact.artifact_id] = new_summary
                    if artifact.artifact_kind == "model_page":
                        model_summaries[artifact.scope_id] = new_summary
                    outputs.append((artifact, output))
        return tuple(outputs)

    def _compile_one(
        self,
        *,
        artifact: KnowledgeCompileArtifact,
        child_summaries: Mapping[UUID, str],
        shared_model_summary: str | None,
        known_paths: Collection[str],
        worktree: Path,
        exclusions: tuple[KnowledgeEvidenceTarget, ...],
    ) -> KnowledgePageCompileOutput:
        """Compile and validate one page without touching shared checkout state."""
        previous_markdown = (
            None
            if artifact.content_hash is None
            else _read_optional_compiler_input(
                worktree=worktree, git_path=artifact.git_path
            )
        )
        curation_markdown = (
            None
            if artifact.curation_path is None
            else _read_optional_compiler_input(
                worktree=worktree, git_path=artifact.curation_path
            )
        )
        output = self._compiler.compile_page(
            request=KnowledgePageCompileRequest(
                artifact=artifact,
                child_summaries=dict(child_summaries),
                shared_model_summary=shared_model_summary,
                curation_hash=(
                    None
                    if curation_markdown is None
                    else knowledge_content_hash(markdown=curation_markdown)
                ),
                curation_markdown=curation_markdown,
                previous_markdown=previous_markdown,
                exclusions=exclusions,
            )
        )
        try:
            validate_knowledge_page_output(
                artifact=artifact,
                output=output,
                known_git_paths=known_paths,
                exclusions=exclusions,
            )
            self._control_plane.validate_citations(
                deployment_id=artifact.deployment_id,
                citations=output.compilation.citations,
            )
        except Exception as error:
            compilation = output.compilation
            failure_trace = traceback.format_exc()
            try:
                self._control_plane.record_failed_compilation(
                    failure=KnowledgeCompilationFailure(
                        compilation_id=compilation.compilation_id,
                        deployment_id=compilation.deployment_id,
                        artifact_id=compilation.artifact_id,
                        inputs_hash=compilation.inputs_hash,
                        candidate_count=compilation.candidate_count,
                        claims_cut_count=compilation.claims_cut_count,
                        writer_version=compilation.writer_version,
                        failure=failure_trace,
                        session_transcript_uri=compilation.session_transcript_uri,
                    )
                )
            except Exception as ledger_error:
                raise ExceptionGroup(
                    "page validation and failure-ledger recording both failed",
                    (error, ledger_error),
                ) from None
            raise
        return output

    def _collect_parallel_wave(
        self,
        *,
        futures: list[
            tuple[KnowledgeCompileArtifact, Future[KnowledgePageCompileOutput]]
        ],
    ) -> tuple[
        list[tuple[KnowledgeCompileArtifact, KnowledgePageCompileOutput]],
        list[Exception],
    ]:
        """Await a whole wave so sibling sessions cannot outlive an aborted cycle."""
        outputs: list[tuple[KnowledgeCompileArtifact, KnowledgePageCompileOutput]] = []
        errors: list[Exception] = []
        for artifact, future in futures:
            try:
                outputs.append((artifact, future.result()))
            except Exception as error:
                errors.append(error)
        return outputs, errors

    def _record_aborted_wave(
        self,
        *,
        outputs: list[tuple[KnowledgeCompileArtifact, KnowledgePageCompileOutput]],
        errors: list[Exception],
    ) -> None:
        """Ledger successful siblings discarded because their wave could not publish."""
        ledger_errors: list[Exception] = []
        for _, output in outputs:
            compilation = output.compilation
            try:
                self._control_plane.record_failed_compilation(
                    failure=KnowledgeCompilationFailure(
                        compilation_id=compilation.compilation_id,
                        deployment_id=compilation.deployment_id,
                        artifact_id=compilation.artifact_id,
                        inputs_hash=compilation.inputs_hash,
                        candidate_count=compilation.candidate_count,
                        claims_cut_count=compilation.claims_cut_count,
                        writer_version=compilation.writer_version,
                        failure="parallel compile wave aborted because a sibling failed",
                        session_transcript_uri=compilation.session_transcript_uri,
                    )
                )
            except Exception as ledger_error:
                ledger_errors.append(ledger_error)
        combined = [*errors, *ledger_errors]
        if len(combined) == 1:
            raise combined[0]
        raise ExceptionGroup("parallel compile wave failed", combined)


def _compile_waves(
    *,
    artifacts: tuple[KnowledgeCompileArtifact, ...],
    schedule: tuple[KnowledgeCompileArtifact, ...],
) -> tuple[tuple[KnowledgeCompileArtifact, ...], ...]:
    """Group a valid child-first order into disjoint dependency waves."""
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    depths: dict[UUID, int] = {}

    def depth(artifact: KnowledgeCompileArtifact) -> int:
        known = depths.get(artifact.artifact_id)
        if known is not None:
            return known
        parent = (
            None
            if artifact.parent_artifact_id is None
            else by_id.get(artifact.parent_artifact_id)
        )
        value = 0 if parent is None else depth(parent) + 1
        depths[artifact.artifact_id] = value
        return value

    models = tuple(
        artifact for artifact in schedule if artifact.artifact_kind == "model_page"
    )
    root_indexes = tuple(
        artifact
        for artifact in schedule
        if artifact.artifact_kind != "model_page"
        and artifact.parent_artifact_id is None
        and artifact.git_path == "_index.md"
    )
    regular = tuple(
        artifact
        for artifact in schedule
        if artifact not in models and artifact not in root_indexes
    )
    waves: list[tuple[KnowledgeCompileArtifact, ...]] = []
    if models:
        waves.append(models)
    for level in sorted({depth(artifact) for artifact in regular}, reverse=True):
        waves.append(
            tuple(artifact for artifact in regular if depth(artifact) == level)
        )
    if root_indexes:
        waves.append(root_indexes)
    return tuple(waves)


def _reconcile_plan_files(
    *, decisions: tuple[KnowledgePendingPlanDecision, ...], worktree: Path
) -> bool:
    """Apply accepted structure to the disposable checkout without generating prose."""
    changed = False
    for decision in decisions:
        proposal = decision.proposal
        if isinstance(proposal, KnowledgeMovePageProposal):
            changed = (
                _move_worktree_file(
                    worktree=worktree,
                    old_path=proposal.old_git_path,
                    new_path=proposal.new_git_path,
                )
                or changed
            )
            changed = (
                _move_worktree_file(
                    worktree=worktree,
                    old_path=proposal.old_curation_path,
                    new_path=proposal.new_curation_path,
                )
                or changed
            )
        elif isinstance(proposal, KnowledgeMergePagesProposal):
            for artifact_id in proposal.source_artifact_ids:
                changed = (
                    _remove_worktree_file(
                        worktree=worktree, git_path=decision.artifact_paths[artifact_id]
                    )
                    or changed
                )
        elif isinstance(proposal, KnowledgeRetirePageProposal):
            changed = (
                _remove_worktree_file(
                    worktree=worktree,
                    git_path=decision.artifact_paths[proposal.artifact_id],
                )
                or changed
            )
        elif (
            isinstance(proposal, KnowledgeConvertKindProposal)
            and proposal.to_kind is KnowledgePageKind.COMPILED
        ):
            changed = (
                _preserve_handover_body(
                    worktree=worktree,
                    body_path=decision.artifact_paths[proposal.artifact_id],
                    curation_path=proposal.curation_path,
                )
                or changed
            )
    return changed


def _move_worktree_file(*, worktree: Path, old_path: str, new_path: str) -> bool:
    """Move one plan-owned file idempotently inside the checkout."""
    if old_path == new_path:
        return False
    source = _worktree_target(worktree=worktree, git_path=old_path)
    target = _worktree_target(worktree=worktree, git_path=new_path)
    if source.exists() and target.exists():
        raise ValueError(f"plan move has both source and target files: {old_path}")
    if not source.exists():
        return False
    if not source.is_file():
        raise ValueError(f"plan move source is not a file: {old_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    source.replace(target)
    return True


def _remove_worktree_file(*, worktree: Path, git_path: str) -> bool:
    """Remove one retired machine-owned page idempotently from the checkout."""
    target = _worktree_target(worktree=worktree, git_path=git_path)
    if not target.exists():
        return False
    if not target.is_file():
        raise ValueError(f"retired artifact path is not a file: {git_path}")
    target.unlink()
    return True


def _preserve_handover_body(
    *, worktree: Path, body_path: str, curation_path: str | None
) -> bool:
    """Copy an author's former body into curation before a writer takes ownership."""
    if curation_path is None:
        raise ValueError("authored handover has no curation path")
    body = _read_optional_compiler_input(worktree=worktree, git_path=body_path)
    if body is None:
        raise ValueError("authored handover body is missing")
    target = _worktree_target(worktree=worktree, git_path=curation_path)
    preserved = (
        "# Authored handover source\n\n"
        "The body below was preserved when this page became compiled.\n\n"
        f"{body}"
    )
    if target.is_file():
        existing = target.read_text(encoding="utf-8")
        if body in existing:
            return False
        preserved = f"{existing.rstrip()}\n\n{preserved}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(preserved, encoding="utf-8")
    return True


def _present_paths(*, worktree: Path) -> tuple[str, ...]:
    """Return normalized repository file paths used by plan readiness checks."""
    return tuple(
        sorted(
            path.relative_to(worktree).as_posix()
            for path in worktree.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(worktree).parts
        )
    )


def _pending_cycle_matches_worktree(
    *,
    cycle: KnowledgePendingCycle,
    artifacts: Mapping[UUID, KnowledgeCompileArtifact],
    worktree: Path,
) -> bool:
    """Return whether remote HEAD contains every exact pending page body."""
    for compilation in cycle.compilations:
        artifact = artifacts.get(compilation.artifact_id)
        if artifact is None:
            return False
        try:
            target = _worktree_target(worktree=worktree, git_path=artifact.git_path)
        except ValueError:
            return False
        if not target.is_file():
            return False
        try:
            markdown = target.read_text(encoding="utf-8")
        except UnicodeError:
            return False
        if knowledge_content_hash(markdown=markdown) != compilation.content_hash:
            return False
    return True


def _write_compiled_page(*, worktree: Path, git_path: str, markdown: str) -> None:
    """Atomically replace one validated path inside the disposable checkout."""
    target = _worktree_target(worktree=worktree, git_path=git_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4()}.tmp")
    temporary.write_text(markdown, encoding="utf-8")
    temporary.replace(target)


def _read_optional_compiler_input(*, worktree: Path, git_path: str) -> str | None:
    """Read one repository-native compiler input without widening its path surface."""
    target = _worktree_target(worktree=worktree, git_path=git_path)
    if not target.is_file():
        return None
    return target.read_text(encoding="utf-8")


def _worktree_target(*, worktree: Path, git_path: str) -> Path:
    """Map a validated repository-native POSIX path into a local checkout."""
    target = worktree.joinpath(*PurePosixPath(git_path).parts)
    root = worktree.resolve()
    if target.is_symlink() or not target.resolve().is_relative_to(root):
        raise ValueError(f"compiled page path escapes worktree: {git_path}")
    return target
