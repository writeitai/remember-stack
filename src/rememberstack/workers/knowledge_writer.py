"""Stock-harness prose compiler for deterministic two-band Plane-K pages."""

from collections.abc import Callable
from datetime import datetime
from datetime import UTC
import json
from pathlib import PurePosixPath
import re
import traceback
from typing import Final
from typing import Protocol
from uuid import UUID
from uuid import uuid4

from pydantic import Field
from pydantic import field_validator
from pydantic import TypeAdapter
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.core import cap_knowledge_writer_bundle
from rememberstack.core import compose_knowledge_page
from rememberstack.core import knowledge_content_hash
from rememberstack.core import knowledge_inputs_hash
from rememberstack.core import knowledge_summary_hash
from rememberstack.core import knowledge_writer_coverage
from rememberstack.core import render_knowledge_fact_sheet
from rememberstack.core import render_knowledge_writer_bundle
from rememberstack.model import KnowledgeCitation
from rememberstack.model import KnowledgeCompilationFailure
from rememberstack.model import KnowledgeCompilationWrite
from rememberstack.model import KnowledgeCompileContext
from rememberstack.model import KnowledgePageCompileOutput
from rememberstack.model import KnowledgePageCompileRequest
from rememberstack.model import KnowledgeWriterBundle
from rememberstack.model import KnowledgeWriterSessionRequest
from rememberstack.model import KnowledgeWriterSessionResult
from rememberstack.model import KnowledgeWriterSuggestion
from rememberstack.model import ObjectKey
from rememberstack.model import UTCDateTime
from rememberstack.ports import MountPublisherPort
from rememberstack.ports import ObjectStorePort
from rememberstack.spine.knowledge import KnowledgeControlPlane
from rememberstack.workers.knowledge_fact_sheet import KnowledgeFactSheetCompiler

KNOWLEDGE_WRITER_VERSION: Final = "k-writer-2026.09:dated-history"

_WRITER_PROMPT: Final = """You compile exactly one Plane-K prose band.
Your working directory is the declared output/ surface. Read ../INSTRUCTIONS.md and every
prepared input under ../bundle/ and ../context/. Memory mount locations are listed in
../context/memory_mounts.json and are read-only. Do not use the
internet, initialize git, commit, or edit outside this temporary workspace.

Write only these declared files in the current directory (archived under output/):
- prose.md: non-empty prose band; do not reproduce the generated fact sheet.
- Describe dated history using each fact's chosen valid_from/valid_until and valid_precision.
  These are canonical half-open world dates. Do not infer unknown endpoints or
  turn an unknown end into "still/currently". Claims are attributed testimony;
  their source dates do not replace a fact's chosen world dates.
- Qualify any current-state statement with the supplied evidence_as_of timestamp;
  this saved page does not become fresh merely because the wall clock advances.
- citations.json: JSON array of objects with role supports|contradicts|cites and exactly one
  target: claim_lineage_id + claim_chunk_content_hash together, relation_id, or doc_id. Copy
  stable claim coordinates and IDs exactly from available evidence.
- summary.md: a two- or three-sentence page summary for parent compilers.
- suggestions.json: JSON array of optional planner suggestions with action,
  rationale, and payload. Use [] when there are none. Suggestions never take action.

Treat curation exclusions as binding. The driver will independently validate every file,
citation, link, count, and final content hash before anything can enter git.
"""

_CITATIONS_ADAPTER = TypeAdapter(tuple[KnowledgeCitation, ...])
_SUGGESTIONS_ADAPTER = TypeAdapter(tuple[KnowledgeWriterSuggestion, ...])
_UTC_ADAPTER = TypeAdapter(UTCDateTime)


def _utc_now() -> datetime:
    """Return the current aware UTC time for production compilation metadata."""
    return datetime.now(tz=UTC)


class KnowledgeWriterSettings(BaseSettings):
    """Settings-owned model, timeout, cap, and transcript-key policy."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_K_WRITER_")

    model: str = Field(min_length=1)
    timeout_seconds: int = Field(gt=0)
    residue_claim_limit: int = Field(ge=0)
    evidence_claims_per_fact: int = Field(ge=0)
    transcript_prefix: str = Field(min_length=1)

    @field_validator("transcript_prefix")
    @classmethod
    def require_safe_transcript_prefix(cls, value: str) -> str:
        """Keep immutable transcript objects under one normalized relative prefix."""
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or str(path) != value:
            raise ValueError("transcript_prefix must be a normalized relative path")
        return value.rstrip("/")


class KnowledgeWriterError(RuntimeError):
    """A writer session failed before producing acceptable declared output."""


class KnowledgeWriterSession(Protocol):
    """The narrow stock-harness seam used only by the Plane-K writer."""

    def run_session(
        self, *, request: KnowledgeWriterSessionRequest
    ) -> "KnowledgeWriterSessionResult":
        """Return raw declared files and a transcript without accepting output."""
        ...


class KnowledgeProseCompiler:
    """Compile one prose page from an exact capped bundle and generated fact sheet."""

    def __init__(
        self,
        *,
        control_plane: KnowledgeControlPlane,
        writer_session: KnowledgeWriterSession,
        transcript_store: ObjectStorePort,
        mount_publisher: MountPublisherPort,
        settings: KnowledgeWriterSettings,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        """Bind authoritative hydration, stock harness, ledger store, and settings."""
        self._control_plane = control_plane
        self._writer_session = writer_session
        self._transcript_store = transcript_store
        self._mount_publisher = mount_publisher
        self._settings = settings
        self._clock = clock
        self.writer_version = _writer_version(settings=settings)

    def compile_page(
        self, *, request: KnowledgePageCompileRequest
    ) -> KnowledgePageCompileOutput:
        """Run one writer, archive its transcript, then build validated two-band output."""
        artifact = request.artifact
        if artifact.artifact_kind == "fact_sheet":
            raise KnowledgeWriterError(
                "prose compiler cannot compile a fact-sheet-only page"
            )
        _validate_curation_input(request=request)
        child_summary_hashes = tuple(
            knowledge_summary_hash(summary=summary)
            for summary in request.child_summaries.values()
        )
        shared_model_summary_hash = (
            None
            if request.shared_model_summary is None
            else knowledge_summary_hash(summary=request.shared_model_summary)
        )
        hydrated = self._control_plane.writer_bundle(
            artifact_id=artifact.artifact_id,
            context=KnowledgeCompileContext(
                curation_hash=request.curation_hash,
                shared_model_summary_hash=shared_model_summary_hash,
                writer_version=self.writer_version,
            ),
            child_summary_hashes=child_summary_hashes,
        )
        bundle = cap_knowledge_writer_bundle(
            bundle=hydrated,
            exclusions=request.exclusions,
            residue_claim_limit=self._settings.residue_claim_limit,
            evidence_claims_per_fact=self._settings.evidence_claims_per_fact,
        )
        compilation_id = uuid4()
        transcript_uri: str | None = None
        candidate_count = len(bundle.fact_sheet.facts) + len(bundle.claim_groups)
        try:
            mounts = self._mount_publisher.publish(deployment_id=artifact.deployment_id)
            if mounts.deployment_id != artifact.deployment_id:
                raise KnowledgeWriterError("mount publisher crossed deployments")
            session_id = uuid4()
            result = self._writer_session.run_session(
                request=KnowledgeWriterSessionRequest(
                    session_id=session_id,
                    model=self._settings.model,
                    prompt=_WRITER_PROMPT,
                    timeout_seconds=self._settings.timeout_seconds,
                    input_files=_writer_inputs(
                        request=request,
                        bundle=bundle,
                        mounts_json=mounts.model_dump_json(),
                    ),
                    mounts=mounts,
                )
            )
            if result.session_id != session_id:
                raise KnowledgeWriterError("writer returned a different session ID")
            transcript_uri = self._archive_transcript(
                request=request, session_id=session_id, transcript=result.transcript
            )
            if result.timed_out:
                raise KnowledgeWriterError("writer session timed out")
            if result.exit_code != 0:
                raise KnowledgeWriterError(
                    f"writer session exited with status {result.exit_code}"
                )
            prose, citations, summary, suggestions = _parse_writer_outputs(
                files=result.output_files
            )
            citations = _unique_citations(citations=citations)
            self._control_plane.validate_citations(
                deployment_id=artifact.deployment_id, citations=citations
            )
            coverage = knowledge_writer_coverage(bundle=bundle, citations=citations)
            rendered = render_knowledge_fact_sheet(
                snapshot=bundle.fact_sheet,
                compiled_at=_UTC_ADAPTER.validate_python(self._clock()),
                citation_count=len(citations),
                candidate_count=coverage.candidate_count,
            )
            markdown = compose_knowledge_page(
                prose_markdown=prose, fact_sheet_markdown=rendered.markdown
            )
            return KnowledgePageCompileOutput(
                compilation=KnowledgeCompilationWrite(
                    compilation_id=compilation_id,
                    deployment_id=artifact.deployment_id,
                    artifact_id=artifact.artifact_id,
                    inputs_hash=knowledge_inputs_hash(
                        snapshot=bundle.fact_sheet.input_snapshot
                    ),
                    candidate_count=coverage.candidate_count,
                    uncited_count=coverage.uncited_count,
                    claims_cut_count=bundle.claims_cut_count,
                    citations=citations,
                    suggestions=suggestions,
                    writer_version=self.writer_version,
                    page_summary=summary,
                    content_hash=knowledge_content_hash(markdown=markdown),
                    tokens=result.tokens,
                    cost_usd=result.cost_usd,
                    session_transcript_uri=transcript_uri,
                ),
                markdown=markdown,
            )
        except Exception as error:
            failure_trace = traceback.format_exc()
            try:
                self._control_plane.record_failed_compilation(
                    failure=KnowledgeCompilationFailure(
                        compilation_id=compilation_id,
                        deployment_id=artifact.deployment_id,
                        artifact_id=artifact.artifact_id,
                        inputs_hash=knowledge_inputs_hash(
                            snapshot=bundle.fact_sheet.input_snapshot
                        ),
                        candidate_count=candidate_count,
                        claims_cut_count=bundle.claims_cut_count,
                        writer_version=self.writer_version,
                        failure=failure_trace,
                        session_transcript_uri=transcript_uri,
                    )
                )
            except Exception as ledger_error:
                raise ExceptionGroup(
                    "writer compilation and failure-ledger recording both failed",
                    (error, ledger_error),
                ) from None
            raise

    def _archive_transcript(
        self, *, request: KnowledgePageCompileRequest, session_id: UUID, transcript: str
    ) -> str:
        """Persist the complete session ledger before interpreting any writer file."""
        key = ObjectKey(
            f"{self._settings.transcript_prefix}/"
            f"{request.artifact.deployment_id}/{request.artifact.artifact_id}/"
            f"{session_id}.json"
        )
        self._transcript_store.write_bytes(key=key, content=transcript.encode("utf-8"))
        return key.root


class KnowledgePageCompilerRouter:
    """Route fact-sheet-only pages away from the stock writer session entirely."""

    def __init__(
        self,
        *,
        fact_sheet_compiler: KnowledgeFactSheetCompiler,
        prose_compiler: KnowledgeProseCompiler,
    ) -> None:
        """Bind the two page compilers behind the existing driver protocol."""
        self._fact_sheet_compiler = fact_sheet_compiler
        self._prose_compiler = prose_compiler

    def compile_page(
        self, *, request: KnowledgePageCompileRequest
    ) -> KnowledgePageCompileOutput:
        """Skip the writer exactly for artifacts designated as fact-sheet-only."""
        if request.artifact.artifact_kind == "fact_sheet":
            return self._fact_sheet_compiler.compile_page(request=request)
        return self._prose_compiler.compile_page(request=request)


def _writer_inputs(
    *,
    request: KnowledgePageCompileRequest,
    bundle: KnowledgeWriterBundle,
    mounts_json: str,
) -> dict[str, str]:
    """Prepare the fixed workspace inputs accepted by the stock harness adapter."""
    children = {
        str(artifact_id): summary
        for artifact_id, summary in sorted(
            request.child_summaries.items(), key=lambda item: str(item[0])
        )
    }
    return {
        "INSTRUCTIONS.md": _WRITER_PROMPT,
        "bundle/evidence.json": render_knowledge_writer_bundle(bundle=bundle),
        "context/curation.md": request.curation_markdown or "_No curation guidance._\n",
        "context/previous_page.md": request.previous_markdown or "_No prior page._\n",
        "context/child_summaries.json": f"{json.dumps(children, sort_keys=True, indent=2)}\n",
        "context/shared_model.md": request.shared_model_summary
        or "_No shared model summary._\n",
        "context/memory_mounts.json": f"{mounts_json}\n",
    }


def _parse_writer_outputs(
    *, files: dict[str, str]
) -> tuple[
    str, tuple[KnowledgeCitation, ...], str, tuple[KnowledgeWriterSuggestion, ...]
]:
    """Parse only declared writer files into the typed compiler contract."""
    required = ("output/prose.md", "output/citations.json", "output/summary.md")
    missing = tuple(path for path in required if path not in files)
    if missing:
        raise KnowledgeWriterError(
            f"writer omitted required outputs: {', '.join(missing)}"
        )
    prose = files["output/prose.md"].strip()
    summary = files["output/summary.md"].strip()
    if not prose:
        raise KnowledgeWriterError("writer prose output is empty")
    if _sentence_count(text=summary) not in (2, 3):
        raise KnowledgeWriterError("writer summary must contain two or three sentences")
    try:
        citations = _CITATIONS_ADAPTER.validate_json(files["output/citations.json"])
        suggestions = _SUGGESTIONS_ADAPTER.validate_json(
            files.get("output/suggestions.json", "[]")
        )
    except ValueError as error:
        raise KnowledgeWriterError(
            "writer JSON output violates the typed contract"
        ) from error
    return prose, citations, summary, suggestions


def _validate_curation_input(*, request: KnowledgePageCompileRequest) -> None:
    """Bind the raw sidecar supplied to the harness to its D45 hash input."""
    expected = (
        None
        if request.curation_markdown is None
        else knowledge_content_hash(markdown=request.curation_markdown)
    )
    if request.curation_hash != expected:
        raise KnowledgeWriterError("curation markdown does not match curation hash")


def _sentence_count(*, text: str) -> int:
    """Count terminally punctuated summary sentences with one deterministic rule."""
    return len(re.findall(r"[^.!?]+[.!?](?:\s|$)", text.strip()))


def _unique_citations(
    *, citations: tuple[KnowledgeCitation, ...]
) -> tuple[KnowledgeCitation, ...]:
    """Apply deterministic set semantics to repeated writer citations."""
    keyed = {
        (
            item.role.value,
            str(item.claim_lineage_id),
            item.claim_chunk_content_hash or "",
            str(item.relation_id),
            str(item.doc_id),
        ): item
        for item in citations
    }
    return tuple(keyed[key] for key in sorted(keyed))


def _writer_version(*, settings: KnowledgeWriterSettings) -> str:
    """Make prompt, model, cap, and timeout settings hash-visible in D45 manifests."""
    fingerprint = knowledge_summary_hash(
        summary=f"{_WRITER_PROMPT}\n{settings.model_dump_json()}"
    )
    return f"{KNOWLEDGE_WRITER_VERSION}:{fingerprint[:16]}"
