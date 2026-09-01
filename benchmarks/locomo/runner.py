"""Staged, guarded execution for the full-system LoCoMo protocol."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime
from datetime import timezone
from decimal import Decimal
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Final
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel
from pydantic import JsonValue
from pydantic import SecretStr
from pydantic import TypeAdapter
from pydantic import ValidationError
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from benchmarks.locomo.dataset import DATASET_COMMIT
from benchmarks.locomo.dataset import DATASET_SHA256
from benchmarks.locomo.dataset import item_ids_hash
from benchmarks.locomo.dataset import load_dataset
from benchmarks.locomo.dataset import load_manifest
from benchmarks.locomo.dataset import manifest_bytes_hash
from benchmarks.locomo.dataset import validate_manifest
from benchmarks.locomo.model import AnswerAgentModel
from benchmarks.locomo.model import AnswerAgentStep
from benchmarks.locomo.model import AnswerRecord
from benchmarks.locomo.model import BenchmarkFailure
from benchmarks.locomo.model import CategorySummary
from benchmarks.locomo.model import FailureKind
from benchmarks.locomo.model import IngestRecord
from benchmarks.locomo.model import JudgeOutput
from benchmarks.locomo.model import JudgeRecord
from benchmarks.locomo.model import LoCoMoDataset
from benchmarks.locomo.model import LoCoMoQuestion
from benchmarks.locomo.model import PreflightProbe
from benchmarks.locomo.model import PreparedDocument
from benchmarks.locomo.model import ProtocolKey
from benchmarks.locomo.model import QuestionManifest
from benchmarks.locomo.model import RetainedCategory
from benchmarks.locomo.model import RetrievedClaim
from benchmarks.locomo.model import RunConfiguration
from benchmarks.locomo.model import RunState
from benchmarks.locomo.model import RunSummary
from benchmarks.locomo.model import SessionDiagnosticSummary
from benchmarks.locomo.model import ToolCallRecord
from benchmarks.locomo.protocol import ADAPTER_VERSION
from benchmarks.locomo.protocol import ANSWER_AGENT_MODEL
from benchmarks.locomo.protocol import ANSWER_AGENT_REASONING_EFFORT
from benchmarks.locomo.protocol import ANSWER_READER_RETRY_BUDGET
from benchmarks.locomo.protocol import API_TIMEOUT_SECONDS
from benchmarks.locomo.protocol import DEFAULT_PROTOCOL_KEY
from benchmarks.locomo.protocol import EXPECTED_DOCUMENT_BINDING_GENERATION
from benchmarks.locomo.protocol import EXPECTED_INGEST_COMPONENT_VERSIONS
from benchmarks.locomo.protocol import EXPECTED_INGEST_MODEL_BINDINGS
from benchmarks.locomo.protocol import EXPECTED_PIPELINE_STAGES
from benchmarks.locomo.protocol import JUDGE_MODEL
from benchmarks.locomo.protocol import JUDGE_REASONING_EFFORT
from benchmarks.locomo.protocol import MAX_AGENT_CALLS
from benchmarks.locomo.protocol import MAX_TOOL_CALLS
from benchmarks.locomo.protocol import official_f1
from benchmarks.locomo.protocol import prompt_sha256
from benchmarks.locomo.protocol import protocol_for_key
from benchmarks.locomo.protocol import protocol_for_name
from benchmarks.locomo.protocol import render_answer_agent_prompt
from benchmarks.locomo.protocol import render_judge_prompt
from benchmarks.locomo.protocol import render_session
from benchmarks.locomo.protocol import schema_sha256
from benchmarks.locomo.protocol import session_diagnostic
from benchmarks.locomo.protocol import TEMPERATURE
from benchmarks.locomo.retrieval import answer_tool_catalog
from benchmarks.locomo.retrieval import assured_tool_catalog
from benchmarks.locomo.retrieval import dispatch_answer_tool
from benchmarks.locomo.retrieval import is_correctable_query_error
from benchmarks.locomo.retrieval import P3Mount
from benchmarks.locomo.retrieval import query_result_failure
from benchmarks.locomo.retrieval import RetrievalInfrastructureError
from benchmarks.locomo.retrieval import RetrievalToolError
from benchmarks.locomo.retrieval import tool_catalog_sha256
from rememberstack.adapters.openrouter import OpenRouterProviderError
from rememberstack.model import ContextBundleV1
from rememberstack.model import EmbeddingRequest
from rememberstack.model import Envelope
from rememberstack.model import ModelRequest
from rememberstack.model import PipelineReadinessReport
from rememberstack.model import ProviderAccountingError
from rememberstack.model import ProviderCallUsage
from rememberstack.model import ProviderInvalidResponseError
from rememberstack.model import ReadinessRequirements
from rememberstack.model import ReasoningEffort
from rememberstack.model import ToolDescriptor
from rememberstack.ports import ModelProviderPort
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.result import QueryResult
from rememberstack.surfaces.sdk import MemoryApiError
from rememberstack.surfaces.sdk import MemoryClient

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from benchmarks.locomo.tracing import LocomoTracer
    from benchmarks.locomo.tracing import QuestionTrace

_RUN_FILE: Final = "run.json"
_MANIFEST_FILE: Final = "manifest.json"
_DOCUMENTS_FILE: Final = "documents.json"
_STATE_FILE: Final = "state.json"
_SUMMARY_FILE: Final = "summary.json"
_DOCUMENTS_ADAPTER: Final = TypeAdapter(tuple[PreparedDocument, ...])


class BenchmarkRunError(RuntimeError):
    """A prepared run is invalid or inconsistent."""


class ExecutionGuardError(BenchmarkRunError):
    """A remote stage lacks an exact execution/cost/isolation acknowledgement."""


class ProviderInfrastructureError(BenchmarkRunError):
    """Provider credit or availability failed before a terminal item checkpoint."""


class _LangfuseActivationSettings(BaseSettings):
    """Standard Langfuse bindings used only to decide whether to load the shim."""

    model_config = SettingsConfigDict(env_prefix="LANGFUSE_", extra="ignore")

    public_key: SecretStr | None = None
    secret_key: SecretStr | None = None
    host: str | None = None

    def configured_values(self) -> tuple[str, str, str] | None:
        """Return credentials only when all three explicit opt-in values are set."""
        public_key = (
            ""
            if self.public_key is None
            else self.public_key.get_secret_value().strip()
        )
        secret_key = (
            ""
            if self.secret_key is None
            else self.secret_key.get_secret_value().strip()
        )
        host = (self.host or "").strip()
        if not public_key or not secret_key or not host:
            return None
        return public_key, secret_key, host


def prepare_run(
    *,
    dataset_path: Path,
    tier: str,
    output: Path,
    protocol: ProtocolKey = DEFAULT_PROTOCOL_KEY,
) -> RunConfiguration:
    """Validate, fingerprint, and render a local run without remote calls."""
    selected_protocol = protocol_for_key(protocol)
    dataset = load_dataset(dataset_path)
    manifest = load_manifest(tier)
    questions = validate_manifest(dataset=dataset, manifest=manifest)
    if output.exists() and any(output.iterdir()):
        raise BenchmarkRunError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    sample_ids = tuple(
        sample.sample_id
        for sample in dataset.samples
        if any(question.sample_id == sample.sample_id for question in questions)
    )
    documents = _prepare_documents(
        run_dir=output, dataset=dataset, sample_ids=sample_ids
    )
    revision = _repository_revision()
    base = {
        "protocol_name": selected_protocol.name,
        "adapter_version": ADAPTER_VERSION,
        "repository_revision": revision,
        "dataset_commit": DATASET_COMMIT,
        "dataset_sha256": dataset.sha256,
        "tier": manifest.tier,
        "manifest_sha256": manifest_bytes_hash(manifest=manifest),
        "item_ids_sha256": manifest.item_ids_sha256,
        "documents_sha256": _models_hash(values=documents),
        "item_count": len(manifest.item_ids),
        "sample_ids": sample_ids,
        "max_tool_calls_per_question": (selected_protocol.max_tool_calls_per_question),
        "max_agent_calls_per_question": (
            selected_protocol.max_agent_calls_per_question
        ),
        "answer_reader_retry_budget": (selected_protocol.answer_reader_retry_budget),
        "api_timeout_seconds": API_TIMEOUT_SECONDS,
        "knowledge_mode": "not_composed",
        "document_binding_generation": EXPECTED_DOCUMENT_BINDING_GENERATION,
        "answer_agent_model": selected_protocol.answer_agent_model,
        "answer_agent_reasoning_effort": (
            selected_protocol.answer_agent_reasoning_effort
        ),
        "answer_word_cap": selected_protocol.answer_word_cap,
        "judge_model": selected_protocol.judge_model,
        "judge_reasoning_effort": selected_protocol.judge_reasoning_effort,
        "answer_agent_temperature": selected_protocol.answer_agent_temperature,
        "judge_temperature": selected_protocol.judge_temperature,
        "judge_repetitions": selected_protocol.judge_repetitions,
        "surface_manifest_hash": selected_protocol.surface_manifest_hash,
        "tool_catalog_sha256": selected_protocol.tool_catalog_sha256,
        "answer_prompt_sha256": prompt_sha256(
            template=selected_protocol.answer_prompt_template
        ),
        "judge_prompt_sha256": prompt_sha256(
            template=selected_protocol.judge_prompt_template
        ),
        "answer_schema_sha256": schema_sha256(model=selected_protocol.answer_schema),
        "judge_schema_sha256": schema_sha256(model=selected_protocol.judge_schema),
    }
    configuration = RunConfiguration(
        **base,
        prepared_at=datetime.now(timezone.utc),
        dataset_path=str(dataset_path.resolve()),
        protocol_fingerprint=_canonical_hash(base),
    )
    _atomic_model(path=output / _RUN_FILE, value=configuration)
    _atomic_model(path=output / _MANIFEST_FILE, value=manifest)
    _atomic_models(path=output / _DOCUMENTS_FILE, values=documents)
    _atomic_model(
        path=output / _STATE_FILE,
        value=RunState(
            protocol_name=configuration.protocol_name,
            protocol_fingerprint=configuration.protocol_fingerprint,
        ),
    )
    return configuration


def _readiness_matches_protocol(
    *,
    readiness: PipelineReadinessReport,
    version_ids: set[UUID],
    repository_revision: str,
) -> bool:
    """Check exact E/P generations, model bindings, completion, and code identity."""
    versions_ready = all(
        version.ready
        and tuple(stage.stage for stage in version.stages) == EXPECTED_PIPELINE_STAGES
        and {stage.stage: stage.component_version for stage in version.stages}
        == dict(EXPECTED_INGEST_COMPONENT_VERSIONS)
        and all(
            stage.status in {"succeeded", "skipped"} and stage.finished_at is not None
            for stage in version.stages
        )
        for version in readiness.versions
    )
    expected_capabilities = {"pipeline", "p1", "live_graph", "p3"}
    capabilities_ready = (
        set(readiness.capabilities) == expected_capabilities
        and all(
            capability.required and capability.ready
            for capability in readiness.capabilities.values()
        )
        and bool(readiness.capabilities["p3"].version)
        and readiness.capabilities["p3"].built_at is not None
        and readiness.capabilities["p3"].published_at is not None
    )
    return bool(
        readiness.ready
        and {version.version_id for version in readiness.versions} == version_ids
        and versions_ready
        and capabilities_ready
        and readiness.document_binding_generation
        == EXPECTED_DOCUMENT_BINDING_GENERATION
        and readiness.model_bindings == dict(EXPECTED_INGEST_MODEL_BINDINGS)
        and repository_revision
        and readiness.build_revision == repository_revision
    )


_PREFLIGHT_PROMPT = "Reply with ok=true. This is a connectivity probe, not a task."


class ProviderPreflightError(BenchmarkRunError):
    """Raised when the configured provider cannot serve the run's models."""


def preflight_provider(
    *,
    provider: ModelProviderPort,
    embedding_model: str,
    before_call: Callable[[], None],
    record_usage: Callable[[ProviderCallUsage], None],
    answer_agent_model: AnswerAgentModel = ANSWER_AGENT_MODEL,
) -> tuple[str, ...]:
    """Prove the credential and both model kinds work before spending real time.

    Ingest makes no provider calls, so a bad credential stays invisible until the
    first pipeline stage needs a model — by which point documents are uploaded and
    stages dead-letter one retry budget at a time. That failure reads like partial
    progress rather than a misconfiguration, so it is checked up front.

    Returns the human-readable lines describing what was verified.
    """
    checks: list[str] = []

    before_call()
    try:
        response = provider.generate(
            request=ModelRequest(
                model=answer_agent_model, prompt=_PREFLIGHT_PROMPT, temperature=0.0
            ),
            response_type=PreflightProbe,
        )
    except OpenRouterProviderError as error:
        if error.usage is not None:
            record_usage(error.usage)
        raise ProviderPreflightError(
            f"chat model {answer_agent_model} is not usable: {error}"
        ) from error
    except ProviderAccountingError as error:
        raise ProviderPreflightError(
            f"chat model {answer_agent_model} is not usable: {error}"
        ) from error
    record_usage(response.usage)
    if not response.output.ok:
        raise ProviderPreflightError(
            f"chat model {answer_agent_model} answered the probe with ok=false;"
            " the provider is reachable but not usable for this run"
        )
    if response.usage.model_name != answer_agent_model:
        raise ProviderPreflightError(
            f"chat model resolved as {response.usage.model_name!r}, not the pinned"
            f" {answer_agent_model!r}"
        )
    checks.append(f"chat {answer_agent_model}: ok")

    before_call()
    try:
        embedding = provider.embed(
            request=EmbeddingRequest(model=embedding_model, texts=("preflight",))
        )
    except OpenRouterProviderError as error:
        if error.usage is not None:
            record_usage(error.usage)
        raise ProviderPreflightError(
            f"embedding model {embedding_model} is not usable: {error}"
        ) from error
    except ProviderAccountingError as error:
        raise ProviderPreflightError(
            f"embedding model {embedding_model} is not usable: {error}"
        ) from error
    record_usage(embedding.usage)
    checks.append(f"embedding {embedding_model}: ok")

    return tuple(checks)


def ingest_sample(
    *,
    run_dir: Path,
    sample_id: str,
    max_documents: int,
    max_evaluator_cost_usd: Decimal,
    execute: bool,
    isolated_deployment_confirmation: str | None,
    client: MemoryClient,
    provider: ModelProviderPort,
) -> tuple[IngestRecord, ...]:
    """Upload one conversation's sessions through the public SDK."""
    context = _load_run(run_dir=run_dir)
    _guard_remote(
        context=context,
        execute=execute,
        sample_id=sample_id,
        confirmation=isolated_deployment_confirmation,
        confirmation_name="confirm-isolated-deployment",
    )
    documents = tuple(
        document for document in context.documents if document.sample_id == sample_id
    )
    if max_documents < len(documents):
        raise ExecutionGuardError(
            f"max-documents {max_documents} is below prepared count {len(documents)}"
        )
    outstanding = tuple(
        document
        for document in documents
        if document.source_ref not in context.state.ingests
    )
    if outstanding:
        # Bind the code that will PROCESS the corpus, not just the code that
        # later serves answers over it: a pipeline run under the wrong image
        # cannot be repaired by rebuilding before the answer stage.
        build = client.deployment_build_info()
        _require_matching_revision(
            prepared=context.configuration.repository_revision,
            serving=build.build_revision,
            when=_INGEST_STAGE,
        )
        _require_current_ingest_bindings(model_bindings=build.model_bindings)
        if build.document_binding_generation != EXPECTED_DOCUMENT_BINDING_GENERATION:
            raise ExecutionGuardError(
                "deployment document binding generation differs from RS-LoCoMo-Full-v21"
            )
        _require_current_query_surface(context=context, client=client)
        _require_exact_live_ingests(
            client=client,
            expected_surface_manifest_hash=context.configuration.surface_manifest_hash,
            expected=tuple(
                record
                for record in context.state.ingests.values()
                if record.sample_id == sample_id
            ),
        )
        # A bad credential must not be discovered only once the pipeline starts
        # dead-lettering. Skipped on a full resume: nothing is left to upload.
        # The binding the E1 stage will actually use, per the deployment.
        embedding_model = build.model_bindings.get("chunk_embedding", "")
        if not embedding_model:
            raise ExecutionGuardError(
                "the deployment did not report an embedding model binding, so the"
                " preflight cannot check the model the pipeline will actually use"
            )

        def before_preflight_call() -> None:
            """Stop before another paid probe once the shared cap is reached."""
            _require_cost_before_call(
                spent=context.state.evaluator_cost_usd, ceiling=max_evaluator_cost_usd
            )

        def record_preflight_usage(usage: ProviderCallUsage) -> None:
            """Checkpoint every successfully accounted probe immediately."""
            context.state.preflight_usages.append(usage)
            context.state.evaluator_cost_usd += usage.cost_usd
            _save_state(run_dir=run_dir, state=context.state)

        for line in preflight_provider(
            provider=provider,
            embedding_model=embedding_model,
            before_call=before_preflight_call,
            record_usage=record_preflight_usage,
            answer_agent_model=context.configuration.answer_agent_model,
        ):
            print(f"preflight: {line}", file=sys.stderr)
        _require_cost_before_call(
            spent=context.state.evaluator_cost_usd, ceiling=max_evaluator_cost_usd
        )
    for document in documents:
        existing = context.state.ingests.get(document.source_ref)
        if existing is not None:
            if existing.content_sha256 != document.content_sha256:
                raise BenchmarkRunError(
                    f"stored ingest hash changed for {document.source_ref}"
                )
            continue
        path = _document_path(run_dir=run_dir, document=document)
        _require_file_hash(path=path, expected=document.content_sha256)
        attested_deployment_id = _require_exact_live_ingests(
            client=client,
            expected_surface_manifest_hash=context.configuration.surface_manifest_hash,
            expected=tuple(
                record
                for record in context.state.ingests.values()
                if record.sample_id == sample_id
            ),
        )
        ingested = client.ingest(
            path,
            mime="text/markdown",
            title=f"LoCoMo {sample_id} — session {document.session_id}",
            source_kind=document.source_kind,
            source_ref=document.source_ref,
            source_modified_at=document.source_modified_at,
            versioning_mode="snapshot",
            source_version_ref=document.source_version_ref,
        )
        if ingested.deployment_id != attested_deployment_id:
            raise ExecutionGuardError(
                "ingest response deployment differs from the attested query surface"
            )
        if not ingested.created:
            raise ExecutionGuardError(
                "fresh LoCoMo ingestion deduplicated against existing deployment"
                " state; wipe the deployment and prepare a new run"
            )
        if ingested.content_hash != document.content_sha256:
            raise BenchmarkRunError(
                f"API content hash mismatch for {document.source_ref}: "
                f"{ingested.content_hash}"
            )
        deployment_ids = {
            record.deployment_id
            for record in context.state.ingests.values()
            if record.sample_id == sample_id
        }
        if deployment_ids and deployment_ids != {ingested.deployment_id}:
            raise ExecutionGuardError(
                f"{sample_id} ingest responses span multiple deployments"
            )
        context.state.ingests[document.source_ref] = IngestRecord(
            sample_id=sample_id,
            session_id=document.session_id,
            source_ref=document.source_ref,
            content_sha256=document.content_sha256,
            source_modified_at=document.source_modified_at,
            source_timezone_basis=document.source_timezone_basis,
            deployment_id=ingested.deployment_id,
            doc_id=ingested.doc_id,
            version_id=ingested.version_id,
            created=ingested.created,
        )
        _save_state(run_dir=run_dir, state=context.state)
    return tuple(context.state.ingests[document.source_ref] for document in documents)


def answer_sample(
    *,
    run_dir: Path,
    sample_id: str,
    max_questions: int,
    max_agent_calls: int,
    max_evaluator_cost_usd: Decimal,
    execute: bool,
    p3_root: Path,
    client: MemoryClient,
    provider: ModelProviderPort,
) -> tuple[AnswerRecord, ...]:
    """Retrieve and answer one isolated conversation's selected questions."""
    context = _load_run(run_dir=run_dir)
    _guard_remote(
        context=context,
        execute=execute,
        sample_id=sample_id,
        confirmation=sample_id,
        confirmation_name="sample",
    )
    questions = _sample_questions(context=context, sample_id=sample_id)
    if max_questions < context.configuration.item_count:
        raise ExecutionGuardError(
            f"max-questions {max_questions} is below run count "
            f"{context.configuration.item_count}"
        )
    _require_sample_ingested(context=context, sample_id=sample_id)
    version_ids = tuple(
        record.version_id
        for record in context.state.ingests.values()
        if record.sample_id == sample_id
    )
    readiness = client.pipeline_readiness(
        version_ids=version_ids,
        require=ReadinessRequirements(pipeline=True, p1=True, live_graph=True, p3=True),
    )
    if readiness.build_revision:
        _require_serving_revision(context=context, readiness=readiness)
    if not _readiness_matches_protocol(
        readiness=readiness,
        version_ids=set(version_ids),
        repository_revision=context.configuration.repository_revision,
    ):
        raise ExecutionGuardError(
            "the deployment did not report the exact completed"
            " RS-LoCoMo-Full-v21 pipeline, live graph, and fresh P3 projection"
        )
    _require_serving_revision(context=context, readiness=readiness)
    prior_readiness = context.state.readiness.get(sample_id)
    if prior_readiness is not None and prior_readiness != readiness:
        raise ExecutionGuardError(
            "deployment readiness fingerprint changed after it was checkpointed"
        )
    context.state.readiness[sample_id] = readiness
    _save_state(run_dir=run_dir, state=context.state)
    p3_capability = readiness.capabilities["p3"]
    tools = _require_current_query_surface(context=context, client=client)
    _require_exact_live_ingests(
        client=client,
        expected_surface_manifest_hash=context.configuration.surface_manifest_hash,
        expected=tuple(
            record
            for record in context.state.ingests.values()
            if record.sample_id == sample_id
        ),
    )
    remaining = tuple(
        question
        for question in questions
        if question.item_id not in context.state.answers
    )
    called = context.state.interrupted_answer_calls + sum(
        record.agent_call_count for record in context.state.answers.values()
    )
    worst_case = (
        called + len(remaining) * context.configuration.max_agent_calls_per_question
    )
    if worst_case > max_agent_calls:
        raise ExecutionGuardError(
            f"max-agent-calls {max_agent_calls} cannot cover at most"
            f" {worst_case} run calls"
        )
    _require_cost_ceiling(
        spent=context.state.evaluator_cost_usd, ceiling=max_evaluator_cost_usd
    )
    doc_sessions = {
        record.doc_id: record.session_id
        for record in context.state.ingests.values()
        if record.sample_id == sample_id
    }
    tracer = _configured_langfuse_tracer(context=context)
    p3: P3Mount | None = None
    p3_error: str | None = None
    if p3_capability.version is None:
        p3_error = "P3 readiness did not identify a snapshot version"
    else:
        try:
            p3 = P3Mount(root=p3_root, expected_version=p3_capability.version)
        except (RetrievalToolError, RetrievalInfrastructureError) as error:
            p3_error = str(error)
    try:
        for question in remaining:
            if tracer is None:
                record = _answer_one(
                    question=question,
                    client=client,
                    provider=provider,
                    tools=tools,
                    p3=p3,
                    p3_error=p3_error,
                    doc_sessions=doc_sessions,
                    state=context.state,
                    max_agent_calls=max_agent_calls,
                    max_evaluator_cost_usd=max_evaluator_cost_usd,
                    answer_agent_model=context.configuration.answer_agent_model,
                    answer_agent_temperature=(
                        context.configuration.answer_agent_temperature
                    ),
                    answer_agent_reasoning_effort=(
                        context.configuration.answer_agent_reasoning_effort
                    ),
                    max_tool_calls_per_question=(
                        context.configuration.max_tool_calls_per_question
                    ),
                    max_agent_calls_per_question=(
                        context.configuration.max_agent_calls_per_question
                    ),
                    answer_reader_retry_budget=(
                        context.configuration.answer_reader_retry_budget
                    ),
                    answer_word_cap=context.configuration.answer_word_cap,
                )
            else:
                with tracer.question(
                    item_id=question.item_id, question=question.question, stage="answer"
                ) as question_trace:
                    record = _answer_one(
                        question=question,
                        client=client,
                        provider=provider,
                        tools=tools,
                        p3=p3,
                        p3_error=p3_error,
                        doc_sessions=doc_sessions,
                        state=context.state,
                        max_agent_calls=max_agent_calls,
                        max_evaluator_cost_usd=max_evaluator_cost_usd,
                        answer_agent_model=context.configuration.answer_agent_model,
                        answer_agent_temperature=(
                            context.configuration.answer_agent_temperature
                        ),
                        answer_agent_reasoning_effort=(
                            context.configuration.answer_agent_reasoning_effort
                        ),
                        max_tool_calls_per_question=(
                            context.configuration.max_tool_calls_per_question
                        ),
                        max_agent_calls_per_question=(
                            context.configuration.max_agent_calls_per_question
                        ),
                        answer_reader_retry_budget=(
                            context.configuration.answer_reader_retry_budget
                        ),
                        answer_word_cap=context.configuration.answer_word_cap,
                        question_trace=question_trace,
                    )
                    if question_trace is not None:
                        question_trace.finish_answer(
                            final_answer=record.generated_answer,
                            failure_kind=(
                                None if record.failure is None else record.failure.kind
                            ),
                        )
            context.state.answers[question.item_id] = record
            _save_state(run_dir=run_dir, state=context.state)
    except ProviderInfrastructureError:
        _save_state(run_dir=run_dir, state=context.state)
        raise
    finally:
        if p3 is not None:
            p3.close()
        if tracer is not None:
            tracer.flush()
    return tuple(context.state.answers[question.item_id] for question in questions)


def judge_sample(
    *,
    run_dir: Path,
    sample_id: str,
    max_judge_calls: int,
    max_evaluator_cost_usd: Decimal,
    execute: bool,
    provider: ModelProviderPort,
) -> tuple[JudgeRecord, ...]:
    """Judge one conversation's terminal answer records exactly once."""
    context = _load_run(run_dir=run_dir)
    _guard_remote(
        context=context,
        execute=execute,
        sample_id=sample_id,
        confirmation=sample_id,
        confirmation_name="sample",
    )
    questions = _sample_questions(context=context, sample_id=sample_id)
    missing = tuple(
        question.item_id
        for question in questions
        if question.item_id not in context.state.answers
    )
    if missing:
        raise ExecutionGuardError(
            f"answer stage is incomplete for {sample_id}: {len(missing)} missing"
        )
    remaining_calls = sum(
        context.state.answers[question.item_id].failure is None
        and question.item_id not in context.state.judges
        for question in questions
    )
    called = context.state.interrupted_judge_calls + sum(
        record.model_called for record in context.state.judges.values()
    )
    if called + remaining_calls > max_judge_calls:
        raise ExecutionGuardError(
            f"max-judge-calls {max_judge_calls} cannot cover "
            f"{called + remaining_calls} run calls"
        )
    _require_cost_ceiling(
        spent=context.state.evaluator_cost_usd, ceiling=max_evaluator_cost_usd
    )
    tracer = _configured_langfuse_tracer(context=context)
    try:
        for question in questions:
            if question.item_id in context.state.judges:
                continue
            answer = context.state.answers[question.item_id]
            if tracer is None:
                judge = _judge_answer(
                    question=question,
                    answer=answer,
                    provider=provider,
                    state=context.state,
                    max_judge_calls=max_judge_calls,
                    max_evaluator_cost_usd=max_evaluator_cost_usd,
                    judge_model=context.configuration.judge_model,
                    judge_temperature=context.configuration.judge_temperature,
                    judge_reasoning_effort=(
                        context.configuration.judge_reasoning_effort
                    ),
                )
            else:
                with tracer.question(
                    item_id=question.item_id, question=question.question, stage="judge"
                ) as question_trace:
                    judge = _judge_answer(
                        question=question,
                        answer=answer,
                        provider=provider,
                        state=context.state,
                        max_judge_calls=max_judge_calls,
                        max_evaluator_cost_usd=max_evaluator_cost_usd,
                        judge_model=context.configuration.judge_model,
                        judge_temperature=context.configuration.judge_temperature,
                        judge_reasoning_effort=(
                            context.configuration.judge_reasoning_effort
                        ),
                        question_trace=question_trace,
                    )
                    if question_trace is not None:
                        question_trace.finish_judge(
                            final_answer=answer.generated_answer,
                            verdict=judge.label,
                            failure_kind=(
                                None if judge.failure is None else judge.failure.kind
                            ),
                        )
            context.state.judges[question.item_id] = judge
            _save_state(run_dir=run_dir, state=context.state)
    except ProviderInfrastructureError:
        _save_state(run_dir=run_dir, state=context.state)
        raise
    finally:
        if tracer is not None:
            tracer.flush()
    return tuple(context.state.judges[question.item_id] for question in questions)


def summarize_run(*, run_dir: Path) -> RunSummary:
    """Aggregate the full manifest; absent or failed records score zero."""
    context = _load_run(run_dir=run_dir)
    questions = context.questions
    doc_sessions = {
        record.doc_id: record.session_id for record in context.state.ingests.values()
    }
    judge_values: list[int] = []
    f1_values: list[float] = []
    category_judge: dict[int, list[int]] = {category: [] for category in range(1, 5)}
    category_f1: dict[int, list[float]] = {category: [] for category in range(1, 5)}
    diagnostic_recalls: list[float] = []
    diagnostic_complete: list[float] = []
    malformed_fields = 0
    failures: Counter[str] = Counter()
    for question in questions:
        answer = context.state.answers.get(question.item_id)
        judge = context.state.judges.get(question.item_id)
        generated = answer.generated_answer if answer is not None else None
        f1_value = official_f1(
            prediction=generated,
            gold_answer=question.answer or "",
            category=_retained_category(question=question),
        )
        correct = int(judge is not None and judge.label == "CORRECT")
        judge_values.append(correct)
        f1_values.append(f1_value)
        category_judge[question.category].append(correct)
        category_f1[question.category].append(f1_value)
        if answer is None:
            failures["missing_answer"] += 1
        elif answer.failure is not None:
            failures[f"answer_{answer.failure.kind}"] += 1
        if judge is None:
            failures["missing_judge"] += 1
        elif judge.failure is not None:
            failures[f"judge_{judge.failure.kind}"] += 1
        diagnostic = session_diagnostic(
            gold_evidence=question.evidence,
            retrieved_sessions=(
                _retrieved_sessions(answer=answer, doc_sessions=doc_sessions)
                if answer is not None and answer.retrieval_succeeded
                else set()
            ),
        )
        malformed_fields += diagnostic.malformed_fields
        if diagnostic.recall is not None and diagnostic.complete is not None:
            diagnostic_recalls.append(diagnostic.recall)
            diagnostic_complete.append(float(diagnostic.complete))
    usages = _all_usages(state=context.state)
    summary = RunSummary(
        protocol_name=context.configuration.protocol_name,
        protocol_fingerprint=context.configuration.protocol_fingerprint,
        tier=context.configuration.tier,
        questions=len(questions),
        judge_correct=sum(judge_values),
        judge_percent=100 * sum(judge_values) / len(judge_values),
        official_f1=sum(f1_values) / len(f1_values),
        categories=tuple(
            CategorySummary(
                category=_category_literal(category),
                questions=len(category_judge[category]),
                judge_correct=sum(category_judge[category]),
                judge_percent=(
                    100 * sum(category_judge[category]) / len(category_judge[category])
                    if category_judge[category]
                    else 0
                ),
                official_f1=(
                    sum(category_f1[category]) / len(category_f1[category])
                    if category_f1[category]
                    else 0
                ),
            )
            for category in range(1, 5)
        ),
        session_diagnostic=SessionDiagnosticSummary(
            scorable_questions=len(diagnostic_recalls),
            malformed_evidence_fields=malformed_fields,
            mean_session_recall=(
                sum(diagnostic_recalls) / len(diagnostic_recalls)
                if diagnostic_recalls
                else 0
            ),
            complete_session_success=(
                sum(diagnostic_complete) / len(diagnostic_complete)
                if diagnostic_complete
                else 0
            ),
        ),
        failures=dict(sorted(failures.items())),
        answer_agent_calls=(
            context.state.interrupted_answer_calls
            + sum(record.agent_call_count for record in context.state.answers.values())
        ),
        total_reader_retries=sum(
            max(record.reader_attempts - 1, 0)
            for record in context.state.answers.values()
        ),
        total_first_step_retries=sum(
            record.first_step_retries for record in context.state.answers.values()
        ),
        total_unknown_guard_retries=sum(
            record.unknown_guard_retries for record in context.state.answers.values()
        ),
        judge_calls=(
            context.state.interrupted_judge_calls
            + sum(record.model_called for record in context.state.judges.values())
        ),
        tokens_in=sum(usage.tokens_in for usage in usages),
        tokens_out=sum(usage.tokens_out for usage in usages),
        evaluator_cost_usd=context.state.evaluator_cost_usd,
    )
    _atomic_model(path=run_dir / _SUMMARY_FILE, value=summary)
    return summary


def summarize_runs(*, run_dirs: tuple[Path, ...]) -> RunSummary:
    """Validate and score disjoint item records from multiple prepared runs."""
    if not run_dirs:
        raise BenchmarkRunError("summarize requires at least one run directory")
    if len(run_dirs) == 1:
        return summarize_run(run_dir=run_dirs[0])
    contexts = tuple(_load_run(run_dir=run_dir) for run_dir in run_dirs)
    _require_merge_identity(run_dirs=run_dirs, contexts=contexts)
    recorded_samples = _require_disjoint_recorded_samples(
        run_dirs=run_dirs, contexts=contexts
    )
    samples_by_run = tuple(_recorded_samples(context=context) for context in contexts)
    combined_state = RunState(
        protocol_name=contexts[0].configuration.protocol_name,
        protocol_fingerprint=contexts[0].configuration.protocol_fingerprint,
        ingests={
            source_ref: record
            for context, owned_samples in zip(contexts, samples_by_run, strict=True)
            for source_ref, record in context.state.ingests.items()
            if record.sample_id in owned_samples
        },
        preflight_usages=[
            usage for context in contexts for usage in context.state.preflight_usages
        ],
        interrupted_usages=[
            usage for context in contexts for usage in context.state.interrupted_usages
        ],
        interrupted_answer_calls=sum(
            context.state.interrupted_answer_calls for context in contexts
        ),
        interrupted_judge_calls=sum(
            context.state.interrupted_judge_calls for context in contexts
        ),
        answers={
            item_id: answer
            for context in contexts
            for item_id, answer in context.state.answers.items()
        },
        judges={
            item_id: judge
            for context in contexts
            for item_id, judge in context.state.judges.items()
        },
        evaluator_cost_usd=sum(
            (context.state.evaluator_cost_usd for context in contexts), start=Decimal(0)
        ),
    )
    first_run = run_dirs[0]
    with tempfile.TemporaryDirectory(
        prefix=".locomo-merge-", dir=first_run.parent
    ) as temporary:
        combined_run = Path(temporary)
        for filename in (_RUN_FILE, _MANIFEST_FILE, _DOCUMENTS_FILE):
            shutil.copy2(first_run / filename, combined_run / filename)
        shutil.copytree(
            first_run / "documents",
            combined_run / "documents",
            copy_function=_link_or_copy,
        )
        _atomic_model(path=combined_run / _STATE_FILE, value=combined_state)
        summary = summarize_run(run_dir=combined_run)
    missing_sample_ids = [
        sample_id
        for sample_id in contexts[0].configuration.sample_ids
        if sample_id not in recorded_samples
    ]
    merged = summary.model_copy(
        update={
            "merged_run_count": len(run_dirs),
            "missing_sample_ids": missing_sample_ids,
        }
    )
    _atomic_model(path=first_run / _SUMMARY_FILE, value=merged)
    return merged


def _require_merge_identity(
    *, run_dirs: tuple[Path, ...], contexts: tuple[_RunContext, ...]
) -> None:
    """Require every protocol and manifest identity named by the merge contract."""
    fields = (
        "protocol_name",
        "protocol_fingerprint",
        "tier",
        "dataset_sha256",
        "manifest_sha256",
        "item_ids_sha256",
    )
    reference = contexts[0].configuration
    for run_dir, context in zip(run_dirs[1:], contexts[1:], strict=True):
        for field in fields:
            expected = getattr(reference, field)
            actual = getattr(context.configuration, field)
            if actual != expected:
                raise BenchmarkRunError(
                    f"cannot merge {run_dir}: {field} differs from {run_dirs[0]}"
                )


def _require_disjoint_recorded_samples(
    *, run_dirs: tuple[Path, ...], contexts: tuple[_RunContext, ...]
) -> set[str]:
    """Reject a sample represented by answer or judge records in two runs."""
    owners: dict[str, Path] = {}
    all_recorded: set[str] = set()
    for run_dir, context in zip(run_dirs, contexts, strict=True):
        recorded = _recorded_samples(context=context)
        for sample_id in sorted(recorded):
            prior = owners.get(sample_id)
            if prior is not None:
                raise BenchmarkRunError(
                    f"cannot merge overlapping sample {sample_id!r}: "
                    f"{prior} and {run_dir}"
                )
            owners[sample_id] = run_dir
        all_recorded.update(recorded)
    return all_recorded


def _recorded_samples(*, context: _RunContext) -> set[str]:
    """Return samples owning any durable answer or judge record in one run."""
    item_samples = {
        question.item_id: question.sample_id for question in context.questions
    }
    return {
        item_samples[item_id]
        for item_id in set(context.state.answers) | set(context.state.judges)
    }


def _link_or_copy(source: str, destination: str) -> str:
    """Hard-link immutable prepared documents, copying across filesystems."""
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)
    return destination


class _RunContext:
    """Validated in-memory view of a prepared run."""

    def __init__(
        self,
        *,
        configuration: RunConfiguration,
        manifest: QuestionManifest,
        documents: tuple[PreparedDocument, ...],
        state: RunState,
        dataset: LoCoMoDataset,
        questions: tuple[LoCoMoQuestion, ...],
    ) -> None:
        """Retain the values validated together by ``_load_run``."""
        self.configuration = configuration
        self.manifest = manifest
        self.documents = documents
        self.state = state
        self.dataset = dataset
        self.questions = questions


def _prepare_documents(
    *, run_dir: Path, dataset: LoCoMoDataset, sample_ids: tuple[str, ...]
) -> tuple[PreparedDocument, ...]:
    """Render and atomically persist every selected sample's sessions."""
    documents: list[PreparedDocument] = []
    samples = dataset.sample_map()
    for sample_id in sample_ids:
        sample = samples[sample_id]
        for session in sample.sessions:
            content = render_session(sample=sample, session=session).encode()
            relative = (
                Path("documents") / sample_id / f"{session.session_id.lower()}.md"
            )
            path = run_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_bytes(path=path, content=content)
            documents.append(
                PreparedDocument(
                    sample_id=sample_id,
                    session_id=session.session_id,
                    session_ordinal=session.ordinal,
                    timestamp=session.timestamp,
                    source_modified_at=session.source_modified_at,
                    source_timezone_basis=session.source_timezone_basis,
                    relative_path=relative.as_posix(),
                    filename=path.name,
                    content_sha256=hashlib.sha256(content).hexdigest(),
                    byte_size=len(content),
                    source_ref=(f"{DATASET_COMMIT}/{sample_id}/{session.session_id}"),
                    source_version_ref=DATASET_COMMIT,
                )
            )
    return tuple(documents)


def _load_run(*, run_dir: Path) -> _RunContext:
    """Load every persisted boundary and reject protocol or state drift."""
    configuration = RunConfiguration.model_validate_json(
        (run_dir / _RUN_FILE).read_text(encoding="utf-8")
    )
    manifest = QuestionManifest.model_validate_json(
        (run_dir / _MANIFEST_FILE).read_text(encoding="utf-8")
    )
    documents = _DOCUMENTS_ADAPTER.validate_json(
        (run_dir / _DOCUMENTS_FILE).read_text(encoding="utf-8")
    )
    state = RunState.model_validate_json(
        (run_dir / _STATE_FILE).read_text(encoding="utf-8")
    )
    dataset = load_dataset(Path(configuration.dataset_path))
    questions = validate_manifest(dataset=dataset, manifest=manifest)
    _validate_run(
        run_dir=run_dir,
        configuration=configuration,
        manifest=manifest,
        documents=documents,
        dataset=dataset,
        questions=questions,
    )
    _validate_state(
        configuration=configuration,
        state=state,
        documents=documents,
        questions=questions,
    )
    return _RunContext(
        configuration=configuration,
        manifest=manifest,
        documents=documents,
        state=state,
        dataset=dataset,
        questions=questions,
    )


def _validate_run(
    *,
    run_dir: Path,
    configuration: RunConfiguration,
    manifest: QuestionManifest,
    documents: tuple[PreparedDocument, ...],
    dataset: LoCoMoDataset,
    questions: tuple[LoCoMoQuestion, ...],
) -> None:
    """Recompute immutable run identity before any local or remote stage."""
    selected_protocol = protocol_for_name(configuration.protocol_name)
    if configuration.dataset_sha256 != DATASET_SHA256:
        raise BenchmarkRunError("run dataset hash is not RS-LoCoMo-Full-v21")
    if item_ids_hash(item_ids=manifest.item_ids) != manifest.item_ids_sha256:
        raise BenchmarkRunError("run manifest item hash changed")
    if manifest_bytes_hash(manifest=manifest) != configuration.manifest_sha256:
        raise BenchmarkRunError("run manifest hash changed")
    if manifest.item_ids_sha256 != configuration.item_ids_sha256:
        raise BenchmarkRunError("run item ID hash changed")
    if manifest.tier != configuration.tier:
        raise BenchmarkRunError("run manifest tier changed")
    if configuration.dataset_commit != DATASET_COMMIT:
        raise BenchmarkRunError("run dataset commit is not RS-LoCoMo-Full-v21")
    if configuration.adapter_version != ADAPTER_VERSION:
        raise BenchmarkRunError("run adapter version differs from current code")
    if _models_hash(values=documents) != configuration.documents_sha256:
        raise BenchmarkRunError("prepared document manifest changed")
    if len(questions) != configuration.item_count:
        raise BenchmarkRunError("run question count changed")
    expected_samples = tuple(
        sample_id
        for sample_id in configuration.sample_ids
        if any(question.sample_id == sample_id for question in questions)
    )
    if expected_samples != configuration.sample_ids:
        raise BenchmarkRunError("run sample selection changed")
    base = {
        "protocol_name": configuration.protocol_name,
        "adapter_version": configuration.adapter_version,
        "repository_revision": configuration.repository_revision,
        "dataset_commit": configuration.dataset_commit,
        "dataset_sha256": configuration.dataset_sha256,
        "tier": configuration.tier,
        "manifest_sha256": configuration.manifest_sha256,
        "item_ids_sha256": configuration.item_ids_sha256,
        "documents_sha256": configuration.documents_sha256,
        "item_count": configuration.item_count,
        "sample_ids": configuration.sample_ids,
        "max_tool_calls_per_question": configuration.max_tool_calls_per_question,
        "max_agent_calls_per_question": configuration.max_agent_calls_per_question,
        "answer_reader_retry_budget": configuration.answer_reader_retry_budget,
        "api_timeout_seconds": configuration.api_timeout_seconds,
        "knowledge_mode": configuration.knowledge_mode,
        "document_binding_generation": configuration.document_binding_generation,
        "answer_agent_model": configuration.answer_agent_model,
        "answer_agent_reasoning_effort": (configuration.answer_agent_reasoning_effort),
        "answer_word_cap": configuration.answer_word_cap,
        "judge_model": configuration.judge_model,
        "judge_reasoning_effort": configuration.judge_reasoning_effort,
        "answer_agent_temperature": configuration.answer_agent_temperature,
        "judge_temperature": configuration.judge_temperature,
        "judge_repetitions": configuration.judge_repetitions,
        "surface_manifest_hash": configuration.surface_manifest_hash,
        "tool_catalog_sha256": configuration.tool_catalog_sha256,
        "answer_prompt_sha256": configuration.answer_prompt_sha256,
        "judge_prompt_sha256": configuration.judge_prompt_sha256,
        "answer_schema_sha256": configuration.answer_schema_sha256,
        "judge_schema_sha256": configuration.judge_schema_sha256,
    }
    if _canonical_hash(base) != configuration.protocol_fingerprint:
        raise BenchmarkRunError("run protocol fingerprint changed")
    current_pin = {
        "answer_agent_model": selected_protocol.answer_agent_model,
        "judge_model": selected_protocol.judge_model,
        "judge_reasoning_effort": selected_protocol.judge_reasoning_effort,
        "max_tool_calls_per_question": (selected_protocol.max_tool_calls_per_question),
        "max_agent_calls_per_question": (
            selected_protocol.max_agent_calls_per_question
        ),
        "answer_reader_retry_budget": (selected_protocol.answer_reader_retry_budget),
        "api_timeout_seconds": API_TIMEOUT_SECONDS,
        "answer_agent_reasoning_effort": (
            selected_protocol.answer_agent_reasoning_effort
        ),
        "answer_word_cap": selected_protocol.answer_word_cap,
        "answer_agent_temperature": selected_protocol.answer_agent_temperature,
        "judge_temperature": selected_protocol.judge_temperature,
        "judge_repetitions": selected_protocol.judge_repetitions,
        "surface_manifest_hash": selected_protocol.surface_manifest_hash,
        "tool_catalog_sha256": selected_protocol.tool_catalog_sha256,
        "answer_prompt_sha256": prompt_sha256(
            template=selected_protocol.answer_prompt_template
        ),
        "judge_prompt_sha256": prompt_sha256(
            template=selected_protocol.judge_prompt_template
        ),
        "answer_schema_sha256": schema_sha256(model=selected_protocol.answer_schema),
        "judge_schema_sha256": schema_sha256(model=selected_protocol.judge_schema),
    }
    for field, expected in current_pin.items():
        if getattr(configuration, field) != expected:
            raise BenchmarkRunError(f"current {field} differs from prepared run")
    _validate_documents(
        run_dir=run_dir,
        configuration=configuration,
        dataset=dataset,
        documents=documents,
    )


def _validate_documents(
    *,
    run_dir: Path,
    configuration: RunConfiguration,
    dataset: LoCoMoDataset,
    documents: tuple[PreparedDocument, ...],
) -> None:
    """Require the exact current deterministic session rendering."""
    expected: list[PreparedDocument] = []
    samples = dataset.sample_map()
    for sample_id in configuration.sample_ids:
        sample = samples[sample_id]
        for session in sample.sessions:
            content = render_session(sample=sample, session=session).encode()
            relative = (
                Path("documents") / sample_id / f"{session.session_id.lower()}.md"
            )
            expected.append(
                PreparedDocument(
                    sample_id=sample_id,
                    session_id=session.session_id,
                    session_ordinal=session.ordinal,
                    timestamp=session.timestamp,
                    source_modified_at=session.source_modified_at,
                    source_timezone_basis=session.source_timezone_basis,
                    relative_path=relative.as_posix(),
                    filename=relative.name,
                    content_sha256=hashlib.sha256(content).hexdigest(),
                    byte_size=len(content),
                    source_ref=(f"{DATASET_COMMIT}/{sample_id}/{session.session_id}"),
                    source_version_ref=DATASET_COMMIT,
                )
            )
    if tuple(expected) != documents:
        raise BenchmarkRunError(
            "prepared document identities differ from deterministic rendering"
        )
    for document in documents:
        _require_file_hash(
            path=_document_path(run_dir=run_dir, document=document),
            expected=document.content_sha256,
        )


def _validate_state(
    *,
    configuration: RunConfiguration,
    state: RunState,
    documents: tuple[PreparedDocument, ...],
    questions: tuple[LoCoMoQuestion, ...],
) -> None:
    """Reject unknown, mismatched, or unaccounted checkpoint records."""
    if (
        state.protocol_name != configuration.protocol_name
        or state.protocol_fingerprint != configuration.protocol_fingerprint
    ):
        raise BenchmarkRunError("run state protocol pin differs from run configuration")
    document_map = {document.source_ref: document for document in documents}
    question_map = {question.item_id: question for question in questions}
    if not set(state.ingests) <= set(document_map):
        raise BenchmarkRunError("run state contains an unknown ingest source ref")
    if not set(state.answers) <= set(question_map):
        raise BenchmarkRunError("run state contains an unknown answer item")
    if not set(state.judges) <= set(question_map):
        raise BenchmarkRunError("run state contains an unknown judge item")
    if not set(state.judges) <= set(state.answers):
        raise BenchmarkRunError("run state contains a judge without an answer")
    for source_ref, record in state.ingests.items():
        document = document_map[source_ref]
        if (
            record.source_ref != source_ref
            or record.sample_id != document.sample_id
            or record.session_id != document.session_id
            or record.content_sha256 != document.content_sha256
            or record.source_modified_at != document.source_modified_at
            or record.source_timezone_basis != document.source_timezone_basis
        ):
            raise BenchmarkRunError(f"ingest state changed for {source_ref}")
    for item_id, answer in state.answers.items():
        question = question_map[item_id]
        if (
            answer.item_id != item_id
            or answer.sample_id != question.sample_id
            or answer.question != question.question
            or answer.gold_answer != (question.answer or "")
            or answer.gold_evidence != question.evidence
            or answer.category != question.category
        ):
            raise BenchmarkRunError(f"answer state changed for {item_id}")
        if tuple(claim.rank for claim in answer.claims) != tuple(
            range(1, len(answer.claims) + 1)
        ):
            raise BenchmarkRunError(f"claim ranks changed for {item_id}")
    for item_id, judge in state.judges.items():
        if judge.item_id != item_id:
            raise BenchmarkRunError(f"judge state changed for {item_id}")
    accounted = sum(
        (usage.cost_usd for usage in _all_usages(state=state)), start=Decimal(0)
    )
    if accounted != state.evaluator_cost_usd:
        raise BenchmarkRunError(
            "persisted evaluator cost differs from successful call usage"
        )


def _guard_remote(
    *,
    context: _RunContext,
    execute: bool,
    sample_id: str,
    confirmation: str | None,
    confirmation_name: str,
) -> None:
    """Require opt-in, sample acknowledgement, revision, and cleanliness."""
    if not execute:
        raise ExecutionGuardError("remote benchmark stage requires --execute")
    if sample_id not in context.configuration.sample_ids:
        raise ExecutionGuardError(f"sample {sample_id!r} is not selected")
    if confirmation != sample_id:
        raise ExecutionGuardError(
            f"--{confirmation_name} must exactly equal {sample_id!r}"
        )
    revision = _repository_revision()
    if revision != context.configuration.repository_revision:
        raise ExecutionGuardError("repository revision differs from the prepared run")
    if _repository_dirty():
        raise ExecutionGuardError("real benchmark stages require a clean worktree")


_INGEST_STAGE = "ingest time"
_ANSWER_STAGE = "answer time"


def _require_matching_revision(*, prepared: str, serving: str, when: str) -> None:
    """Require the serving image to be built from the prepared revision.

    ``when`` names the stage, because the two call sites answer different
    questions: at ingest it binds the code that *processes* the corpus, at answer
    the code that *serves* it. Checking only the latter leaves a hole — process
    under the wrong image, fail, rebuild without re-ingesting, and the answer
    stage then passes over data produced by other code.
    """
    # Answer-time drift needs only a rebuild; ingest-time drift means the corpus
    # itself was processed by other code, so it must be ingested again.
    remedy = " and re-ingest" if when == _INGEST_STAGE else ""
    if not serving:
        raise ExecutionGuardError(
            f"the deployment did not report a build revision at {when}, so the"
            " code cannot be shown to be the prepared code; rebuild the image"
            " with REMEMBERSTACK_BUILD_REVISION=$(git rev-parse HEAD)"
        )
    if serving != prepared:
        raise ExecutionGuardError(
            f"the deployment serves revision {serving} at {when} but the run was"
            f" prepared at {prepared}; rebuild the image from the prepared"
            f" revision{remedy}"
        )


def _require_current_ingest_bindings(*, model_bindings: dict[str, str]) -> None:
    """Fail before upload unless the deployment serves the pinned ingest models."""

    expected = dict(EXPECTED_INGEST_MODEL_BINDINGS)
    if model_bindings != expected:
        mismatches = sorted(
            name
            for name in set(model_bindings) | set(expected)
            if model_bindings.get(name) != expected.get(name)
        )
        raise ExecutionGuardError(
            "deployment ingest model bindings differ from RS-LoCoMo-Full-v21: "
            + ", ".join(mismatches)
        )


def _require_serving_revision(
    *, context: _RunContext, readiness: PipelineReadinessReport
) -> None:
    """Require the serving image to be built from the prepared revision.

    The other guards check the filesystem the CLI runs from, which says nothing
    about the containers doing the work: Compose serves a published image unless
    told to build, so a run can otherwise record a commit that never produced its
    numbers. An unstamped image is a hard stop, not a warning — "unknown" is not
    evidence of agreement.
    """
    _require_matching_revision(
        prepared=context.configuration.repository_revision,
        serving=readiness.build_revision,
        when=_ANSWER_STAGE,
    )


def _require_current_query_surface(
    *, context: _RunContext, client: MemoryClient
) -> tuple[ToolDescriptor, ...]:
    """Require the exact complete read contract pinned by the prepared run."""
    query_space = client.describe_query_space()
    if query_space.get("surface_manifest_hash") != (
        context.configuration.surface_manifest_hash
    ):
        raise ExecutionGuardError(
            "deployment query surface differs from the prepared protocol"
        )
    operations = client.list_operations()
    if operations != assured_tool_catalog():
        raise ExecutionGuardError(
            "deployment operation catalog is not the canonical four-operation surface"
        )
    tools = answer_tool_catalog()
    if tool_catalog_sha256() != context.configuration.tool_catalog_sha256:
        raise ExecutionGuardError(
            "complete answer-tool catalog differs from the prepared protocol"
        )
    return tools


def _require_exact_live_ingests(
    *,
    client: MemoryClient,
    expected_surface_manifest_hash: str,
    expected: tuple[IngestRecord, ...],
) -> UUID:
    """Require live lineages and their visible versions to equal checkpoints."""
    try:
        result = QueryResult.model_validate(
            client.query_sql(
                sql=(
                    "SELECT d.deployment_id, d.source_ref, d.doc_id, v.version_id "
                    "FROM documents_live AS d "
                    "JOIN document_versions_visible AS v "
                    "ON v.deployment_id = d.deployment_id AND v.doc_id = d.doc_id "
                    "ORDER BY d.source_ref, d.doc_id, v.version_id"
                ),
                max_rows=len(expected) + 1,
            ),
            strict=False,
        )
    except (ValidationError, TypeError) as error:
        raise ExecutionGuardError(
            "deployment returned an invalid live document attestation"
        ) from error
    rows = result.rows
    if (
        result.termination_reason != "completed"
        or result.truncated is not False
        or result.surface_manifest_hash != expected_surface_manifest_hash
    ):
        raise ExecutionGuardError(
            "deployment could not attest its exact live document set"
        )
    expected_deployment_ids = {record.deployment_id for record in expected}
    if len(expected_deployment_ids) > 1 or (
        expected_deployment_ids and expected_deployment_ids != {result.deployment_id}
    ):
        raise ExecutionGuardError(
            "deployment identity differs from checkpointed ingestion responses"
        )
    actual: list[tuple[UUID, str, UUID, UUID]] = []
    for row in rows:
        if len(row) != 4 or not isinstance(row[1], str):
            raise ExecutionGuardError(
                "deployment returned an invalid live document attestation"
            )
        try:
            actual.append(
                (UUID(str(row[0])), row[1], UUID(str(row[2])), UUID(str(row[3])))
            )
        except (TypeError, ValueError) as error:
            raise ExecutionGuardError(
                "deployment returned an invalid live document attestation"
            ) from error
    checkpointed = tuple(
        sorted(
            (
                (
                    record.deployment_id,
                    record.source_ref,
                    record.doc_id,
                    record.version_id,
                )
                for record in expected
            ),
            key=lambda item: (item[1], item[2]),
        )
    )
    if tuple(actual) != checkpointed:
        raise ExecutionGuardError(
            "deployment live documents do not exactly match checkpointed versions"
        )
    return result.deployment_id


def _require_sample_ingested(*, context: _RunContext, sample_id: str) -> None:
    """Require one complete sample mapped to exactly one deployment."""
    source_refs = {
        document.source_ref
        for document in context.documents
        if document.sample_id == sample_id
    }
    missing = source_refs - set(context.state.ingests)
    if missing:
        raise ExecutionGuardError(
            f"{sample_id} has {len(missing)} session documents without ingest records"
        )
    deployment_ids = {
        record.deployment_id
        for record in context.state.ingests.values()
        if record.sample_id == sample_id
    }
    if len(deployment_ids) != 1:
        raise ExecutionGuardError(
            f"{sample_id} ingest records do not identify one deployment"
        )


def _configured_langfuse_tracer(*, context: _RunContext) -> LocomoTracer | None:
    """Load the optional observer only when all standard bindings are non-empty."""
    configured = _LangfuseActivationSettings.model_validate({}).configured_values()
    if configured is None:
        return None
    public_key, secret_key, host = configured
    from benchmarks.locomo.tracing import create_langfuse_tracer

    configuration = context.configuration
    run_identity = (
        f"{configuration.protocol_fingerprint}:"
        f"{configuration.repository_revision}:"
        f"{configuration.prepared_at.isoformat()}"
    )
    try:
        return create_langfuse_tracer(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
            run_identity=run_identity,
        )
    except Exception:
        _logger.warning("optional Langfuse tracer initialization failed", exc_info=True)
        return None


def _answer_one(
    *,
    question: LoCoMoQuestion,
    client: MemoryClient,
    provider: ModelProviderPort,
    tools: tuple[ToolDescriptor, ...],
    doc_sessions: dict[UUID, str],
    state: RunState,
    max_agent_calls: int,
    max_evaluator_cost_usd: Decimal,
    answer_agent_model: AnswerAgentModel = ANSWER_AGENT_MODEL,
    answer_agent_temperature: float = TEMPERATURE,
    answer_agent_reasoning_effort: ReasoningEffort | None = (
        ANSWER_AGENT_REASONING_EFFORT
    ),
    max_tool_calls_per_question: int = MAX_TOOL_CALLS,
    max_agent_calls_per_question: int = MAX_AGENT_CALLS,
    answer_reader_retry_budget: int = ANSWER_READER_RETRY_BUDGET,
    answer_word_cap: int | None = None,
    p3: P3Mount | None = None,
    p3_error: str | None = None,
    question_trace: QuestionTrace | None = None,
) -> AnswerRecord:
    """Let a bounded agent choose any complete-plane read, then answer."""
    if p3_error is not None:
        return _failed_answer(
            question=question,
            kind="tool",
            message=p3_error,
            retrieval_latency_ms=0,
            retrieval_succeeded=False,
            agent_call_count=0,
        )
    tool_names = {tool.name for tool in tools}
    trace: list[ToolCallRecord] = []
    usages: list[ProviderCallUsage] = []
    agent_latency_ms = 0
    tool_latency_ms = 0
    agent_call_count = 0
    reader_attempts = 0
    first_step_retries = 0
    unknown_guard_retries = 0
    invalid_completion_attempts = 0
    guard_feedback: str | None = None
    prior_calls = state.interrupted_answer_calls + sum(
        record.agent_call_count for record in state.answers.values()
    )
    for _ in range(max_agent_calls_per_question):
        if prior_calls + agent_call_count >= max_agent_calls:
            raise ExecutionGuardError(
                "answer-agent call ceiling reached before next call"
            )
        _require_cost_before_call(
            spent=state.evaluator_cost_usd, ceiling=max_evaluator_cost_usd
        )
        prompt = render_answer_agent_prompt(
            question=question.question,
            tools=tools,
            trace=tuple(trace),
            answer_word_cap=answer_word_cap,
            guard_feedback=guard_feedback,
        )
        agent_observation = (
            None
            if question_trace is None
            else question_trace.start_agent_call(model=answer_agent_model)
        )
        started = time.monotonic_ns()
        agent_call_count += 1
        try:
            response = provider.generate(
                request=ModelRequest(
                    model=answer_agent_model,
                    prompt=prompt,
                    temperature=answer_agent_temperature,
                    reasoning_effort=answer_agent_reasoning_effort,
                ),
                response_type=AnswerAgentStep,
            )
        except ProviderAccountingError as error:
            call_latency_ms = _elapsed_ms(started)
            if agent_observation is not None:
                agent_observation.finish(
                    usage=None, latency_ms=call_latency_ms, outcome="accounting_error"
                )
            return _failed_answer(
                question=question,
                kind="accounting",
                message=str(error),
                retrieval_latency_ms=tool_latency_ms,
                retrieval_succeeded=_trace_succeeded(trace=trace),
                agent_call_count=agent_call_count,
                reader_attempts=reader_attempts + int(bool(trace)),
                first_step_retries=first_step_retries,
                unknown_guard_retries=unknown_guard_retries,
                reader_latency_ms=agent_latency_ms + call_latency_ms,
                claims=_claims_from_trace(
                    trace=tuple(trace), doc_sessions=doc_sessions
                ),
                tool_calls=tuple(trace),
                usages=tuple(usages),
            )
        except ValidationError as error:
            call_latency_ms = _elapsed_ms(started)
            if agent_observation is not None:
                agent_observation.finish(
                    usage=None, latency_ms=call_latency_ms, outcome="invalid_response"
                )
            return _failed_answer(
                question=question,
                kind="invalid_response",
                message=str(error),
                retrieval_latency_ms=tool_latency_ms,
                retrieval_succeeded=_trace_succeeded(trace=trace),
                agent_call_count=agent_call_count,
                reader_attempts=reader_attempts,
                first_step_retries=first_step_retries,
                unknown_guard_retries=unknown_guard_retries,
                reader_latency_ms=agent_latency_ms + call_latency_ms,
                claims=_claims_from_trace(
                    trace=tuple(trace), doc_sessions=doc_sessions
                ),
                tool_calls=tuple(trace),
                usages=tuple(usages),
            )
        except ProviderInvalidResponseError as error:
            call_latency_ms = _elapsed_ms(started)
            agent_latency_ms += call_latency_ms
            if error.usage is not None:
                usages.append(error.usage)
                state.evaluator_cost_usd += error.usage.cost_usd
            model_mismatch = (
                error.usage is not None and error.usage.model_name != answer_agent_model
            )
            invalid_completion_attempts += 1
            if trace:
                reader_attempts += 1
            if agent_observation is not None:
                agent_observation.finish(
                    usage=error.usage,
                    latency_ms=call_latency_ms,
                    outcome=(
                        "accounting_error" if model_mismatch else "provider_error"
                    ),
                )
            can_retry = (
                not model_mismatch
                and invalid_completion_attempts <= answer_reader_retry_budget
                and agent_call_count < max_agent_calls_per_question
                and prior_calls + agent_call_count < max_agent_calls
                and state.evaluator_cost_usd < max_evaluator_cost_usd
            )
            if can_retry:
                if not trace:
                    first_step_retries += 1
                continue
            return _failed_answer(
                question=question,
                kind="accounting" if model_mismatch else "reader",
                message=(
                    f"provider resolved answer model as"
                    f" {error.usage.model_name!r}, not {answer_agent_model!r}"
                    if model_mismatch and error.usage is not None
                    else str(error)
                ),
                retrieval_latency_ms=tool_latency_ms,
                retrieval_succeeded=_trace_succeeded(trace=trace),
                agent_call_count=agent_call_count,
                reader_attempts=reader_attempts,
                first_step_retries=first_step_retries,
                unknown_guard_retries=unknown_guard_retries,
                reader_latency_ms=agent_latency_ms,
                claims=_claims_from_trace(
                    trace=tuple(trace), doc_sessions=doc_sessions
                ),
                tool_calls=tuple(trace),
                usages=tuple(usages),
            )
        except OpenRouterProviderError as error:
            call_latency_ms = _elapsed_ms(started)
            if error.usage is not None:
                usages.append(error.usage)
                state.evaluator_cost_usd += error.usage.cost_usd
            model_mismatch = (
                error.usage is not None and error.usage.model_name != answer_agent_model
            )
            if agent_observation is not None:
                agent_observation.finish(
                    usage=error.usage,
                    latency_ms=call_latency_ms,
                    outcome=(
                        "accounting_error" if model_mismatch else "provider_error"
                    ),
                )
            if _is_openrouter_credit_exhaustion(error=error):
                _record_interrupted_answer(
                    state=state, usages=tuple(usages), call_count=agent_call_count
                )
                raise ProviderInfrastructureError(
                    "OpenRouter credits unavailable; stopping before answer checkpoint"
                ) from error
            return _failed_answer(
                question=question,
                kind="accounting" if model_mismatch else "reader",
                message=(
                    f"provider resolved answer model as"
                    f" {error.usage.model_name!r}, not {answer_agent_model!r}"
                    if model_mismatch and error.usage is not None
                    else str(error)
                ),
                retrieval_latency_ms=tool_latency_ms,
                retrieval_succeeded=_trace_succeeded(trace=trace),
                agent_call_count=agent_call_count,
                reader_attempts=reader_attempts + int(bool(trace)),
                first_step_retries=first_step_retries,
                unknown_guard_retries=unknown_guard_retries,
                reader_latency_ms=agent_latency_ms + call_latency_ms,
                claims=_claims_from_trace(
                    trace=tuple(trace), doc_sessions=doc_sessions
                ),
                tool_calls=tuple(trace),
                usages=tuple(usages),
            )
        call_latency_ms = _elapsed_ms(started)
        agent_latency_ms += call_latency_ms
        usages.append(response.usage)
        state.evaluator_cost_usd += response.usage.cost_usd
        step = response.output
        if response.usage.model_name != answer_agent_model:
            if agent_observation is not None:
                agent_observation.finish(
                    usage=response.usage,
                    latency_ms=call_latency_ms,
                    outcome="accounting_error",
                )
            return _failed_answer(
                question=question,
                kind="accounting",
                message=(
                    f"provider resolved answer model as"
                    f" {response.usage.model_name!r}, not {answer_agent_model!r}"
                ),
                retrieval_latency_ms=tool_latency_ms,
                retrieval_succeeded=_trace_succeeded(trace=trace),
                agent_call_count=agent_call_count,
                reader_attempts=reader_attempts,
                first_step_retries=first_step_retries,
                unknown_guard_retries=unknown_guard_retries,
                reader_latency_ms=agent_latency_ms,
                claims=_claims_from_trace(
                    trace=tuple(trace), doc_sessions=doc_sessions
                ),
                tool_calls=tuple(trace),
                usages=tuple(usages),
            )
        if agent_observation is not None and step.action == "tool":
            agent_observation.finish(
                usage=response.usage, latency_ms=call_latency_ms, outcome="tool"
            )
        threshold_exhausted_before_tool = (
            state.evaluator_cost_usd == max_evaluator_cost_usd and step.action == "tool"
        )
        if (
            state.evaluator_cost_usd > max_evaluator_cost_usd
            or threshold_exhausted_before_tool
        ):
            if agent_observation is not None and step.action == "answer":
                agent_observation.finish(
                    usage=response.usage,
                    latency_ms=call_latency_ms,
                    outcome="accounting_error",
                )
            return _failed_answer(
                question=question,
                kind="accounting",
                message=(
                    f"reported evaluator spend {state.evaluator_cost_usd} reached"
                    f" or crossed stop threshold {max_evaluator_cost_usd}"
                ),
                retrieval_latency_ms=tool_latency_ms,
                retrieval_succeeded=_trace_succeeded(trace=trace),
                agent_call_count=agent_call_count,
                reader_attempts=(
                    reader_attempts + int(step.action == "answer" and bool(trace))
                ),
                first_step_retries=first_step_retries,
                unknown_guard_retries=unknown_guard_retries,
                reader_latency_ms=agent_latency_ms,
                claims=_claims_from_trace(
                    trace=tuple(trace), doc_sessions=doc_sessions
                ),
                tool_calls=tuple(trace),
                usages=tuple(usages),
            )
        if step.action == "answer":
            if not trace:
                if agent_observation is not None:
                    agent_observation.finish(
                        usage=response.usage,
                        latency_ms=call_latency_ms,
                        outcome="invalid_response",
                    )
                return _failed_answer(
                    question=question,
                    kind="invalid_response",
                    message="answer agent finished without consulting RememberStack",
                    retrieval_latency_ms=tool_latency_ms,
                    retrieval_succeeded=False,
                    agent_call_count=agent_call_count,
                    reader_attempts=reader_attempts,
                    first_step_retries=first_step_retries,
                    unknown_guard_retries=unknown_guard_retries,
                    reader_latency_ms=agent_latency_ms,
                    tool_calls=tuple(trace),
                    usages=tuple(usages),
                )
            answer = step.answer or ""
            guarded_terminal = False
            if _is_unknown(answer=answer) and not _has_content_bearing_attempt(
                trace=trace
            ):
                reader_attempts += 1
                unknown_guard_retries += 1
                can_continue = (
                    agent_call_count < max_agent_calls_per_question
                    and prior_calls + agent_call_count < max_agent_calls
                    and state.evaluator_cost_usd < max_evaluator_cost_usd
                )
                if can_continue:
                    if agent_observation is not None:
                        agent_observation.finish(
                            usage=response.usage,
                            latency_ms=call_latency_ms,
                            outcome="guarded_unknown",
                        )
                    guard_feedback = (
                        'Terminal "Unknown" was rejected because the trace contains '
                        "only identity or metadata reads. Call one content-bearing "
                        "testimony, fact, context, primitive, row-returning query, "
                        "or P3 search/read operation before answering Unknown."
                    )
                    continue
                guarded_terminal = True
            if answer_word_cap is not None and len(answer.split()) > answer_word_cap:
                if agent_observation is not None:
                    agent_observation.finish(
                        usage=response.usage,
                        latency_ms=call_latency_ms,
                        outcome="invalid_response",
                    )
                return _failed_answer(
                    question=question,
                    kind="invalid_response",
                    message=(
                        f"answer agent exceeded the {answer_word_cap}-word answer limit"
                    ),
                    retrieval_latency_ms=tool_latency_ms,
                    retrieval_succeeded=_trace_succeeded(trace=trace),
                    agent_call_count=agent_call_count,
                    reader_attempts=reader_attempts + 1,
                    first_step_retries=first_step_retries,
                    unknown_guard_retries=unknown_guard_retries,
                    reader_latency_ms=agent_latency_ms,
                    claims=_claims_from_trace(
                        trace=tuple(trace), doc_sessions=doc_sessions
                    ),
                    tool_calls=tuple(trace),
                    usages=tuple(usages),
                )
            claims = _claims_from_trace(trace=tuple(trace), doc_sessions=doc_sessions)
            if agent_observation is not None:
                agent_observation.finish(
                    usage=response.usage,
                    latency_ms=call_latency_ms,
                    outcome="answer",
                    final_answer=answer,
                )
            return AnswerRecord(
                item_id=question.item_id,
                sample_id=question.sample_id,
                category=_retained_category(question=question),
                question=question.question,
                gold_answer=question.answer or "",
                gold_evidence=question.evidence,
                claims=claims,
                tool_calls=tuple(trace),
                dropped_by_hydration=sum(
                    _dropped_by_hydration(call=call) for call in trace
                ),
                retrieval_succeeded=_trace_succeeded(trace=trace),
                retrieval_latency_ms=tool_latency_ms,
                reader_called=True,
                agent_call_count=agent_call_count,
                reader_attempts=(
                    reader_attempts if guarded_terminal else reader_attempts + 1
                ),
                first_step_retries=first_step_retries,
                unknown_guard_retries=unknown_guard_retries,
                reader_latency_ms=agent_latency_ms,
                generated_answer=answer,
                reader_usage=_aggregate_usage(usages=tuple(usages)),
            )
        if step.tool_name not in tool_names:
            return _failed_answer(
                question=question,
                kind="invalid_response",
                message=f"answer agent requested unknown tool {step.tool_name!r}",
                retrieval_latency_ms=tool_latency_ms,
                retrieval_succeeded=_trace_succeeded(trace=trace),
                agent_call_count=agent_call_count,
                reader_attempts=reader_attempts,
                first_step_retries=first_step_retries,
                unknown_guard_retries=unknown_guard_retries,
                reader_latency_ms=agent_latency_ms,
                claims=_claims_from_trace(
                    trace=tuple(trace), doc_sessions=doc_sessions
                ),
                tool_calls=tuple(trace),
                usages=tuple(usages),
            )
        if len(trace) >= max_tool_calls_per_question:
            return _failed_answer(
                question=question,
                kind="invalid_response",
                message="answer agent exceeded the per-question tool-call limit",
                retrieval_latency_ms=tool_latency_ms,
                retrieval_succeeded=_trace_succeeded(trace=trace),
                agent_call_count=agent_call_count,
                reader_attempts=reader_attempts,
                first_step_retries=first_step_retries,
                unknown_guard_retries=unknown_guard_retries,
                reader_latency_ms=agent_latency_ms,
                claims=_claims_from_trace(
                    trace=tuple(trace), doc_sessions=doc_sessions
                ),
                tool_calls=tuple(trace),
                usages=tuple(usages),
            )
        # Validated by the model's own validator for tool steps, so this cannot
        # raise here; trailing junk (observed: a sentence period after the
        # closing brace at temperature 0) is recorded on the trace row.
        step_arguments, step_trailing = step.parsed_arguments()
        tool_observation = (
            None
            if question_trace is None
            else question_trace.start_tool_call(
                name=step.tool_name or "", arguments=step_arguments
            )
        )
        tool_started = time.monotonic_ns()
        try:
            tool_response = dispatch_answer_tool(
                client=client,
                p3=p3,
                name=step.tool_name or "",
                arguments=step_arguments,
            )
        except MemoryApiError as error:
            failed_latency = _elapsed_ms(tool_started)
            public_error: dict[str, JsonValue] = {
                "status_code": error.status_code,
                "detail": error.detail,
            }
            if error.code is not None:
                public_error["code"] = error.code
            if error.status_code == 503:
                if tool_observation is not None:
                    tool_observation.finish(
                        latency_ms=failed_latency, outcome="api_error"
                    )
                _record_interrupted_answer(
                    state=state, usages=tuple(usages), call_count=agent_call_count
                )
                raise ProviderInfrastructureError(
                    "retrieval infrastructure unavailable; stopping before answer "
                    "checkpoint"
                ) from error
            correctable = is_correctable_query_error(code=error.code)
            if correctable:
                if tool_observation is not None:
                    tool_observation.finish(
                        latency_ms=failed_latency, outcome="rejected"
                    )
                tool_latency_ms += failed_latency
                trace.append(
                    ToolCallRecord(
                        name=step.tool_name or "",
                        arguments=step_arguments,
                        arguments_trailing=step_trailing,
                        latency_ms=failed_latency,
                        succeeded=False,
                        response={"error": public_error},
                    )
                )
                continue
            if tool_observation is not None:
                tool_observation.finish(latency_ms=failed_latency, outcome="api_error")
            tool_latency_ms += failed_latency
            trace.append(
                ToolCallRecord(
                    name=step.tool_name or "",
                    arguments=step_arguments,
                    arguments_trailing=step_trailing,
                    latency_ms=failed_latency,
                    succeeded=False,
                    response={"error": public_error},
                )
            )
            return _failed_answer(
                question=question,
                kind="tool",
                message=str(error),
                retrieval_latency_ms=tool_latency_ms,
                retrieval_succeeded=_trace_succeeded(trace=trace),
                agent_call_count=agent_call_count,
                reader_attempts=reader_attempts,
                first_step_retries=first_step_retries,
                unknown_guard_retries=unknown_guard_retries,
                reader_latency_ms=agent_latency_ms,
                claims=_claims_from_trace(
                    trace=tuple(trace), doc_sessions=doc_sessions
                ),
                tool_calls=tuple(trace),
                usages=tuple(usages),
            )
        except RetrievalToolError as error:
            failed_latency = _elapsed_ms(tool_started)
            if tool_observation is not None:
                tool_observation.finish(latency_ms=failed_latency, outcome="rejected")
            tool_latency_ms += failed_latency
            trace.append(
                ToolCallRecord(
                    name=step.tool_name or "",
                    arguments=step_arguments,
                    arguments_trailing=step_trailing,
                    latency_ms=failed_latency,
                    succeeded=False,
                    response={"error": {"status_code": None, "detail": str(error)}},
                )
            )
            continue
        except (RetrievalInfrastructureError, SandboxRejection) as error:
            failed_latency = _elapsed_ms(tool_started)
            code = error.code.value if isinstance(error, SandboxRejection) else None
            correctable = is_correctable_query_error(code=code)
            if tool_observation is not None:
                tool_observation.finish(
                    latency_ms=failed_latency,
                    outcome="rejected" if correctable else "api_error",
                )
            tool_latency_ms += failed_latency
            public_error: dict[str, JsonValue] = {
                "status_code": None,
                "detail": str(error),
            }
            if code is not None:
                public_error["code"] = code
            trace.append(
                ToolCallRecord(
                    name=step.tool_name or "",
                    arguments=step_arguments,
                    arguments_trailing=step_trailing,
                    latency_ms=failed_latency,
                    succeeded=False,
                    response={"error": public_error},
                )
            )
            if correctable:
                continue
            return _failed_answer(
                question=question,
                kind="tool",
                message=str(error),
                retrieval_latency_ms=tool_latency_ms,
                retrieval_succeeded=_trace_succeeded(trace=trace),
                agent_call_count=agent_call_count,
                reader_attempts=reader_attempts,
                first_step_retries=first_step_retries,
                unknown_guard_retries=unknown_guard_retries,
                reader_latency_ms=agent_latency_ms,
                claims=_claims_from_trace(
                    trace=tuple(trace), doc_sessions=doc_sessions
                ),
                tool_calls=tuple(trace),
                usages=tuple(usages),
            )
        latency = _elapsed_ms(tool_started)
        query_failure = query_result_failure(tool_response)
        if query_failure is not None:
            code, message = query_failure
            correctable = is_correctable_query_error(code=code)
            if tool_observation is not None:
                tool_observation.finish(
                    latency_ms=latency,
                    outcome="rejected" if correctable else "api_error",
                )
            tool_latency_ms += latency
            trace.append(
                ToolCallRecord(
                    name=step.tool_name or "",
                    arguments=step_arguments,
                    arguments_trailing=step_trailing,
                    latency_ms=latency,
                    succeeded=False,
                    response=tool_response,
                )
            )
            if correctable:
                continue
            return _failed_answer(
                question=question,
                kind="tool",
                message=f"{code}: {message}",
                retrieval_latency_ms=tool_latency_ms,
                retrieval_succeeded=_trace_succeeded(trace=trace),
                agent_call_count=agent_call_count,
                reader_attempts=reader_attempts,
                first_step_retries=first_step_retries,
                unknown_guard_retries=unknown_guard_retries,
                reader_latency_ms=agent_latency_ms,
                claims=_claims_from_trace(
                    trace=tuple(trace), doc_sessions=doc_sessions
                ),
                tool_calls=tuple(trace),
                usages=tuple(usages),
            )
        if tool_observation is not None:
            tool_observation.finish(latency_ms=latency, outcome="succeeded")
        tool_latency_ms += latency
        trace.append(
            ToolCallRecord(
                name=step.tool_name or "",
                arguments=step_arguments,
                arguments_trailing=step_trailing,
                latency_ms=latency,
                response=tool_response,
            )
        )
        if _has_content_bearing_attempt(trace=trace):
            guard_feedback = None
    return _failed_answer(
        question=question,
        kind="invalid_response",
        message="answer agent exhausted its step budget without a final answer",
        retrieval_latency_ms=tool_latency_ms,
        retrieval_succeeded=_trace_succeeded(trace=trace),
        agent_call_count=agent_call_count,
        reader_attempts=reader_attempts,
        first_step_retries=first_step_retries,
        unknown_guard_retries=unknown_guard_retries,
        reader_latency_ms=agent_latency_ms,
        claims=_claims_from_trace(trace=tuple(trace), doc_sessions=doc_sessions),
        tool_calls=tuple(trace),
        usages=tuple(usages),
    )


def _judge_answer(
    *,
    question: LoCoMoQuestion,
    answer: AnswerRecord,
    provider: ModelProviderPort,
    state: RunState,
    max_judge_calls: int,
    max_evaluator_cost_usd: Decimal,
    judge_model: str = JUDGE_MODEL,
    judge_temperature: float = TEMPERATURE,
    judge_reasoning_effort: ReasoningEffort | None = JUDGE_REASONING_EFFORT,
    question_trace: QuestionTrace | None = None,
) -> JudgeRecord:
    """Return a local wrong for answer failures or invoke the configured judge."""
    if answer.failure is not None:
        return JudgeRecord(item_id=question.item_id, label="WRONG", model_called=False)
    return _judge_one(
        question=question,
        answer=answer,
        provider=provider,
        state=state,
        max_judge_calls=max_judge_calls,
        max_evaluator_cost_usd=max_evaluator_cost_usd,
        judge_model=judge_model,
        judge_temperature=judge_temperature,
        judge_reasoning_effort=judge_reasoning_effort,
        question_trace=question_trace,
    )


def _judge_one(
    *,
    question: LoCoMoQuestion,
    answer: AnswerRecord,
    provider: ModelProviderPort,
    state: RunState,
    max_judge_calls: int,
    max_evaluator_cost_usd: Decimal,
    judge_model: str = JUDGE_MODEL,
    judge_temperature: float = TEMPERATURE,
    judge_reasoning_effort: ReasoningEffort | None = JUDGE_REASONING_EFFORT,
    question_trace: QuestionTrace | None = None,
) -> JudgeRecord:
    """Invoke the judge once; every call failure becomes a visible wrong."""
    called = state.interrupted_judge_calls + sum(
        record.model_called for record in state.judges.values()
    )
    if called >= max_judge_calls:
        raise ExecutionGuardError("judge call ceiling reached before next call")
    _require_cost_before_call(
        spent=state.evaluator_cost_usd, ceiling=max_evaluator_cost_usd
    )
    judge_observation = (
        None
        if question_trace is None
        else question_trace.start_judge_call(model=judge_model)
    )
    started = time.monotonic_ns()
    try:
        response = provider.generate(
            request=ModelRequest(
                model=judge_model,
                prompt=render_judge_prompt(
                    question=question.question,
                    gold_answer=question.answer or "",
                    generated_answer=answer.generated_answer or "",
                ),
                temperature=judge_temperature,
                reasoning_effort=judge_reasoning_effort,
            ),
            response_type=JudgeOutput,
        )
    except ProviderAccountingError as error:
        latency_ms = _elapsed_ms(started)
        if judge_observation is not None:
            judge_observation.finish(
                usage=None, latency_ms=latency_ms, outcome="accounting_error"
            )
        return JudgeRecord(
            item_id=question.item_id,
            label="WRONG",
            model_called=True,
            latency_ms=latency_ms,
            failure=_failure(kind="accounting", message=str(error)),
        )
    except OpenRouterProviderError as error:
        latency_ms = _elapsed_ms(started)
        if error.usage is not None:
            state.evaluator_cost_usd += error.usage.cost_usd
        model_mismatch = (
            error.usage is not None and error.usage.model_name != judge_model
        )
        credit_exhausted = _is_openrouter_credit_exhaustion(error=error)
        if judge_observation is not None:
            judge_observation.finish(
                usage=error.usage,
                latency_ms=latency_ms,
                outcome=(
                    "provider_unavailable"
                    if credit_exhausted
                    else "accounting_error"
                    if model_mismatch
                    else "provider_error"
                ),
                verdict=None if credit_exhausted else "WRONG",
            )
        if credit_exhausted:
            if error.usage is not None:
                state.interrupted_usages.append(error.usage)
            state.interrupted_judge_calls += 1
            raise ProviderInfrastructureError(
                "OpenRouter credits unavailable; stopping before judge checkpoint"
            ) from error
        return JudgeRecord(
            item_id=question.item_id,
            label="WRONG",
            model_called=True,
            usage=error.usage,
            latency_ms=latency_ms,
            failure=_failure(
                kind="accounting" if model_mismatch else "judge",
                message=(
                    f"provider resolved judge model as"
                    f" {error.usage.model_name!r}, not {judge_model!r}"
                    if model_mismatch and error.usage is not None
                    else str(error)
                ),
            ),
        )
    except ValidationError as error:
        latency_ms = _elapsed_ms(started)
        if judge_observation is not None:
            judge_observation.finish(
                usage=None,
                latency_ms=latency_ms,
                outcome="invalid_response",
                verdict="WRONG",
            )
        return JudgeRecord(
            item_id=question.item_id,
            label="WRONG",
            model_called=True,
            latency_ms=latency_ms,
            failure=_failure(kind="judge", message=str(error)),
        )
    state.evaluator_cost_usd += response.usage.cost_usd
    latency_ms = _elapsed_ms(started)
    if response.usage.model_name != judge_model:
        if judge_observation is not None:
            judge_observation.finish(
                usage=response.usage,
                latency_ms=latency_ms,
                outcome="accounting_error",
                verdict="WRONG",
            )
        return JudgeRecord(
            item_id=question.item_id,
            label="WRONG",
            model_called=True,
            usage=response.usage,
            latency_ms=latency_ms,
            failure=_failure(
                kind="accounting",
                message=(
                    f"provider resolved judge model as"
                    f" {response.usage.model_name!r}, not {judge_model!r}"
                ),
            ),
        )
    if state.evaluator_cost_usd > max_evaluator_cost_usd:
        if judge_observation is not None:
            judge_observation.finish(
                usage=response.usage,
                latency_ms=latency_ms,
                outcome="accounting_error",
                verdict="WRONG",
            )
        return JudgeRecord(
            item_id=question.item_id,
            label="WRONG",
            model_called=True,
            usage=response.usage,
            latency_ms=latency_ms,
            failure=_failure(
                kind="accounting",
                message=(
                    f"reported evaluator spend {state.evaluator_cost_usd} crossed"
                    f" stop threshold {max_evaluator_cost_usd}"
                ),
            ),
        )
    if judge_observation is not None:
        judge_observation.finish(
            usage=response.usage,
            latency_ms=latency_ms,
            outcome="judged",
            verdict=response.output.label,
        )
    return JudgeRecord(
        item_id=question.item_id,
        label=response.output.label,
        model_called=True,
        usage=response.usage,
        latency_ms=latency_ms,
    )


def _failed_answer(
    *,
    question: LoCoMoQuestion,
    kind: FailureKind,
    message: str,
    retrieval_latency_ms: int,
    retrieval_succeeded: bool,
    agent_call_count: int,
    reader_attempts: int = 0,
    first_step_retries: int = 0,
    unknown_guard_retries: int = 0,
    reader_latency_ms: int | None = None,
    claims: tuple[RetrievedClaim, ...] = (),
    tool_calls: tuple[ToolCallRecord, ...] = (),
    usages: tuple[ProviderCallUsage, ...] = (),
) -> AnswerRecord:
    """Build a bounded terminal failure without erasing retrieval evidence."""
    return AnswerRecord(
        item_id=question.item_id,
        sample_id=question.sample_id,
        category=_retained_category(question=question),
        question=question.question,
        gold_answer=question.answer or "",
        gold_evidence=question.evidence,
        claims=claims,
        tool_calls=tool_calls,
        dropped_by_hydration=sum(
            _dropped_by_hydration(call=call) for call in tool_calls
        ),
        retrieval_succeeded=retrieval_succeeded,
        retrieval_latency_ms=retrieval_latency_ms,
        reader_called=agent_call_count > 0,
        agent_call_count=agent_call_count,
        reader_attempts=reader_attempts,
        first_step_retries=first_step_retries,
        unknown_guard_retries=unknown_guard_retries,
        reader_latency_ms=reader_latency_ms,
        reader_usage=_aggregate_usage(usages=usages) if usages else None,
        failure=_failure(kind=kind, message=message),
    )


def _is_unknown(*, answer: str) -> bool:
    """Recognize the protocol's exact terminal Unknown after light punctuation."""
    return answer.strip().casefold().rstrip(".") == "unknown"


def _has_content_bearing_attempt(*, trace: list[ToolCallRecord]) -> bool:
    """Whether the trace contains one successful v17 content-bearing read."""
    direct = {
        "answer_context",
        "fact_context",
        "testimony_context",
        "lookup_relations",
        "transcript_relation",
        "lookup_observations",
        "search_claims",
        "search_chunks",
        "hydrate_relation",
        "p3_search",
        "p3_read",
    }
    for call in trace:
        if not call.succeeded:
            continue
        if call.name in direct:
            return True
        if (
            call.name in {"query_sql", "run_saved_query"}
            and isinstance(call.response, dict)
            and "rows" in call.response
        ):
            return True
    return False


def _claims_from_trace(
    *, trace: tuple[ToolCallRecord, ...], doc_sessions: dict[UUID, str]
) -> tuple[RetrievedClaim, ...]:
    """Collect first-seen evidence claims from every public tool response."""
    seen: set[UUID] = set()
    claims: list[RetrievedClaim] = []
    for call in trace:
        for envelope in _response_envelopes(response=call.response):
            for claim in envelope.evidence:
                if claim.claim_id in seen:
                    continue
                seen.add(claim.claim_id)
                claims.append(
                    RetrievedClaim(
                        rank=len(claims) + 1,
                        claim_id=claim.claim_id,
                        doc_id=claim.doc_id,
                        chunk_id=claim.chunk_id,
                        claim_text=claim.claim_text,
                        source_span=claim.source_span,
                        char_start=claim.char_start,
                        char_end=claim.char_end,
                        is_attributed=claim.is_attributed,
                        is_current_testimony=claim.is_current_testimony,
                        session_id=doc_sessions.get(claim.doc_id),
                    )
                )
    return tuple(claims)


def _retrieved_sessions(
    *, answer: AnswerRecord, doc_sessions: dict[UUID, str]
) -> set[str]:
    """Collect diagnostic sessions from claim and chunk evidence alike."""
    sessions = {
        claim.session_id for claim in answer.claims if claim.session_id is not None
    }
    for call in answer.tool_calls:
        for envelope in _response_envelopes(response=call.response):
            sessions.update(
                session
                for chunk in envelope.chunks
                if (session := doc_sessions.get(chunk.doc_id)) is not None
            )
    return sessions


def _trace_succeeded(*, trace: list[ToolCallRecord]) -> bool:
    """Return whether at least one retrieval tool completed successfully."""
    return any(call.succeeded for call in trace)


def _dropped_by_hydration(*, call: ToolCallRecord) -> int:
    """Sum hydration drops from every authority carried by one response."""
    return sum(
        envelope.dropped_by_hydration
        for envelope in _response_envelopes(response=call.response)
    )


def _response_envelopes(
    *, response: Envelope | ContextBundleV1 | JsonValue
) -> tuple[Envelope, ...]:
    """Expose typed envelope children without blending their authorities."""
    if isinstance(response, Envelope):
        return (response,)
    if isinstance(response, ContextBundleV1):
        return (response.testimony, response.facts)
    return ()


def _aggregate_usage(*, usages: tuple[ProviderCallUsage, ...]) -> ProviderCallUsage:
    """Collapse calls while preserving every distinct provider model identity."""
    if not usages:
        raise ValueError("cannot aggregate an empty usage tuple")
    model_names = sorted({usage.model_name for usage in usages})
    model_name = (
        model_names[0] if len(model_names) == 1 else f"mixed:{'|'.join(model_names)}"
    )
    return ProviderCallUsage(
        model_name=model_name,
        tokens_in=sum(usage.tokens_in for usage in usages),
        tokens_out=sum(usage.tokens_out for usage in usages),
        cost_usd=sum((usage.cost_usd for usage in usages), start=Decimal(0)),
        latency_ms=sum(usage.latency_ms for usage in usages),
    )


def _failure(*, kind: FailureKind, message: str) -> BenchmarkFailure:
    """Normalize an external error into a bounded durable failure."""
    bounded = " ".join(message.split())[:500] or "unspecified failure"
    return BenchmarkFailure.model_validate({"kind": kind, "message": bounded})


def _sample_questions(
    *, context: _RunContext, sample_id: str
) -> tuple[LoCoMoQuestion, ...]:
    """Return one sample's manifest-ordered retained questions."""
    selected = tuple(
        question for question in context.questions if question.sample_id == sample_id
    )
    if not selected:
        raise ExecutionGuardError(f"sample {sample_id!r} has no selected questions")
    return selected


def _require_cost_ceiling(*, spent: Decimal, ceiling: Decimal) -> None:
    """Require a positive reported-spend threshold no lower than persisted spend."""
    if not ceiling.is_finite() or ceiling <= 0:
        raise ExecutionGuardError("max-evaluator-cost-usd must be positive and finite")
    if ceiling < spent:
        raise ExecutionGuardError(
            f"cost threshold {ceiling} is below already recorded spend {spent}"
        )


def _require_cost_before_call(*, spent: Decimal, ceiling: Decimal) -> None:
    """Stop before a provider call once the reported-spend threshold is reached."""
    _require_cost_ceiling(spent=spent, ceiling=ceiling)
    if spent >= ceiling:
        raise ExecutionGuardError(
            f"evaluator spend {spent} has reached run threshold {ceiling}"
        )


def _all_usages(*, state: RunState) -> tuple[ProviderCallUsage, ...]:
    """Collect every persisted provider usage record exactly once."""
    return tuple(
        usage
        for usage in (
            *state.preflight_usages,
            *state.interrupted_usages,
            *(record.reader_usage for record in state.answers.values()),
            *(record.usage for record in state.judges.values()),
        )
        if usage is not None
    )


def _is_openrouter_credit_exhaustion(*, error: OpenRouterProviderError) -> bool:
    """Recognize OpenRouter's stable HTTP status prefix without parsing its body."""
    return " returned 402:" in str(error)


def _record_interrupted_answer(
    *, state: RunState, usages: tuple[ProviderCallUsage, ...], call_count: int
) -> None:
    """Keep answer calls exact when infrastructure aborts before a checkpoint."""
    state.interrupted_usages.extend(usages)
    state.interrupted_answer_calls += call_count


def _retained_category(*, question: LoCoMoQuestion) -> RetainedCategory:
    """Narrow a manifest question to the retained category type."""
    if question.category not in {1, 2, 3, 4}:
        raise BenchmarkRunError(
            f"excluded category reached execution: {question.category}"
        )
    return _category_literal(question.category)


def _category_literal(category: int) -> RetainedCategory:
    """Narrow a runtime integer to one retained literal."""
    if category == 1:
        return 1
    if category == 2:
        return 2
    if category == 3:
        return 3
    if category == 4:
        return 4
    raise BenchmarkRunError(f"invalid retained category {category}")


def _repository_revision() -> str:
    """Read the exact Git commit used in the protocol fingerprint."""
    result = subprocess.run(
        ("git", "rev-parse", "HEAD"), check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _repository_dirty() -> bool:
    """Return whether non-ignored files differ from the prepared revision."""
    result = subprocess.run(
        ("git", "status", "--porcelain"), check=True, capture_output=True, text=True
    )
    return bool(result.stdout.strip())


def _canonical_hash(value: object) -> str:
    """Hash a JSON-canonical protocol value."""
    canonical = json.dumps(
        value, default=str, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _models_hash(*, values: tuple[BaseModel, ...]) -> str:
    """Hash an ordered tuple of strict Pydantic boundaries."""
    return _canonical_hash(
        [value.model_dump(mode="json", exclude_none=False) for value in values]
    )


def _document_path(*, run_dir: Path, document: PreparedDocument) -> Path:
    """Resolve a path while refusing traversal outside the run directory."""
    path = (run_dir / document.relative_path).resolve()
    root = run_dir.resolve()
    if root not in path.parents:
        raise BenchmarkRunError(
            f"prepared document escapes run directory: {document.relative_path}"
        )
    return path


def _require_file_hash(*, path: Path, expected: str) -> None:
    """Reject a local file whose exact bytes changed after preparation."""
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise BenchmarkRunError(f"file hash changed for {path}: {actual}")


def _save_state(*, run_dir: Path, state: RunState) -> None:
    """Atomically checkpoint the one mutable run-state document."""
    _atomic_model(path=run_dir / _STATE_FILE, value=state)


def _atomic_models(*, path: Path, values: tuple[BaseModel, ...]) -> None:
    """Persist an ordered model list as stable readable JSON."""
    content = (
        json.dumps(
            [value.model_dump(mode="json") for value in values],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode()
        + b"\n"
    )
    _atomic_bytes(path=path, content=content)


def _atomic_model(*, path: Path, value: BaseModel) -> None:
    """Persist one model as stable readable JSON."""
    content = (
        json.dumps(
            value.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
        )
        + "\n"
    ).encode()
    _atomic_bytes(path=path, content=content)


def _atomic_bytes(*, path: Path, content: bytes) -> None:
    """Flush, fsync, and replace without a partial destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _elapsed_ms(started_ns: int) -> int:
    """Convert a monotonic start instant to elapsed milliseconds."""
    return (time.monotonic_ns() - started_ns) // 1_000_000
