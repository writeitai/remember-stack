"""Zero-LLM compiler for deterministic ``kind='fact_sheet'`` pages."""

from collections.abc import Callable
from datetime import datetime
from datetime import UTC
from typing import Final
from uuid import uuid4

from pydantic import TypeAdapter

from rememberstack.core import compose_knowledge_page
from rememberstack.core import knowledge_content_hash
from rememberstack.core import knowledge_inputs_hash
from rememberstack.core import knowledge_summary_hash
from rememberstack.core import render_knowledge_fact_sheet
from rememberstack.model import KnowledgeCompilationWrite
from rememberstack.model import KnowledgeCompileContext
from rememberstack.model import KnowledgePageCompileOutput
from rememberstack.model import KnowledgePageCompileRequest
from rememberstack.model import UTCDateTime
from rememberstack.spine.knowledge import KnowledgeControlPlane

KNOWLEDGE_FACT_SHEET_VERSION: Final = "k-fact-sheet-2026.09:dated-history"
"""Hash-visible deterministic renderer version."""

_UTC_ADAPTER = TypeAdapter(UTCDateTime)


def _utc_now() -> datetime:
    """Return the current aware UTC timestamp for the machine footer."""
    return datetime.now(tz=UTC)


class KnowledgeFactSheetCompileError(ValueError):
    """A non-fact-sheet artifact reached the deterministic compiler."""


class KnowledgeFactSheetCompiler:
    """Compile exact rule-selected facts without invoking a model provider."""

    def __init__(
        self,
        *,
        control_plane: KnowledgeControlPlane,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        """Bind the renderer to the authoritative fact query and an injectable clock."""
        self._control_plane = control_plane
        self._clock = clock

    def compile_page(
        self, *, request: KnowledgePageCompileRequest
    ) -> KnowledgePageCompileOutput:
        """Return one complete fact-sheet-only page and honest compile metadata."""
        artifact = request.artifact
        if artifact.artifact_kind != "fact_sheet":
            raise KnowledgeFactSheetCompileError(
                "deterministic compiler requires artifact kind 'fact_sheet'"
            )
        child_summary_hashes = tuple(
            knowledge_summary_hash(summary=summary)
            for summary in request.child_summaries.values()
        )
        shared_model_summary_hash = (
            None
            if request.shared_model_summary is None
            else knowledge_summary_hash(summary=request.shared_model_summary)
        )
        snapshot = self._control_plane.fact_sheet_snapshot(
            artifact_id=artifact.artifact_id,
            context=KnowledgeCompileContext(
                curation_hash=request.curation_hash,
                shared_model_summary_hash=shared_model_summary_hash,
                writer_version=KNOWLEDGE_FACT_SHEET_VERSION,
            ),
            child_summary_hashes=child_summary_hashes,
        )
        excluded_relations = {
            target.relation_id
            for target in request.exclusions
            if target.relation_id is not None
        }
        render_snapshot = snapshot.model_copy(
            update={
                "facts": tuple(
                    fact
                    for fact in snapshot.facts
                    if fact.kind != "relation" or fact.fact_id not in excluded_relations
                )
            }
        )
        rendered = render_knowledge_fact_sheet(
            snapshot=render_snapshot,
            compiled_at=_UTC_ADAPTER.validate_python(self._clock()),
            citation_count=0,
        )
        markdown = compose_knowledge_page(
            prose_markdown=None, fact_sheet_markdown=rendered.markdown
        )
        candidate_count = len(snapshot.input_snapshot.facts) + len(
            snapshot.input_snapshot.claims
        )
        summary = (
            f"Fact sheet for {artifact.git_path}: "
            f"{rendered.relation_count} relations in dated history, "
            f"{rendered.observation_count} observations, and "
            f"{rendered.contradiction_group_count} open contradiction groups."
        )
        return KnowledgePageCompileOutput(
            compilation=KnowledgeCompilationWrite(
                compilation_id=uuid4(),
                deployment_id=artifact.deployment_id,
                artifact_id=artifact.artifact_id,
                inputs_hash=knowledge_inputs_hash(snapshot=snapshot.input_snapshot),
                candidate_count=candidate_count,
                uncited_count=candidate_count,
                citations=(),
                writer_version=KNOWLEDGE_FACT_SHEET_VERSION,
                page_summary=summary,
                content_hash=knowledge_content_hash(markdown=markdown),
            ),
            markdown=markdown,
        )
