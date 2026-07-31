"""Staged, guarded execution for the full-system LoCoMo protocol."""

from __future__ import annotations

from collections import Counter
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
from benchmarks.locomo.protocol import ANSWER_READER_RETRY_BUDGET
from benchmarks.locomo.protocol import DEFAULT_PROTOCOL_KEY
from benchmarks.locomo.protocol import EXPECTED_PIPELINE_STAGES
from benchmarks.locomo.protocol import EXPECTED_PROJECTION_PLANES
from benchmarks.locomo.protocol import JUDGE_MODEL
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
from rememberstack.adapters.openrouter import OpenRouterProviderError
from rememberstack.model import EmbeddingRequest
from rememberstack.model import ModelRequest
from rememberstack.model import PipelineReadinessReport
from rememberstack.model import ProviderAccountingError
from rememberstack.model import ProviderCallUsage
from rememberstack.model import ProviderInvalidResponseError
from rememberstack.model import ReasoningEffort
from rememberstack.model import ToolDescriptor
from rememberstack.ports import ModelProviderPort
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
        "knowledge_mode": "not_composed",
        "answer_agent_model": selected_protocol.answer_agent_model,
        "answer_agent_reasoning_effort": (
            selected_protocol.answer_agent_reasoning_effort
        ),
        "judge_model": selected_protocol.judge_model,
        "answer_agent_temperature": selected_protocol.answer_agent_temperature,
        "judge_temperature": selected_protocol.judge_temperature,
        "judge_repetitions": selected_protocol.judge_repetitions,
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


_PREFLIGHT_PROMPT = "Reply with ok=true. This is a connectivity probe, not a task."


class ProviderPreflightError(BenchmarkRunError):
    """Raised when the configured provider cannot serve the run's models."""


def preflight_provider(
    *,
    provider: ModelProviderPort,
    embedding_model: str,
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

    try:
        response = provider.generate(
            request=ModelRequest(
                model=answer_agent_model, prompt=_PREFLIGHT_PROMPT, temperature=0.0
            ),
            response_type=PreflightProbe,
        )
    except (OpenRouterProviderError, ProviderAccountingError) as error:
        raise ProviderPreflightError(
            f"chat model {answer_agent_model} is not usable: {error}"
        ) from error
    if not response.output.ok:
        raise ProviderPreflightError(
            f"chat model {answer_agent_model} answered the probe with ok=false;"
            " the provider is reachable but not usable for this run"
        )
    checks.append(f"chat {answer_agent_model}: ok")

    try:
        provider.embed(
            request=EmbeddingRequest(model=embedding_model, texts=("preflight",))
        )
    except (OpenRouterProviderError, ProviderAccountingError) as error:
        raise ProviderPreflightError(
            f"embedding model {embedding_model} is not usable: {error}"
        ) from error
    checks.append(f"embedding {embedding_model}: ok")

    return tuple(checks)


def ingest_sample(
    *,
    run_dir: Path,
    sample_id: str,
    max_documents: int,
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
        # A bad credential must not be discovered only once the pipeline starts
        # dead-lettering. Skipped on a full resume: nothing is left to upload.
        # The binding the E1 stage will actually use, per the deployment.
        embedding_model = build.model_bindings.get("chunk_embedding", "")
        if not embedding_model:
            raise ExecutionGuardError(
                "the deployment did not report an embedding model binding, so the"
                " preflight cannot check the model the pipeline will actually use"
            )
        for line in preflight_provider(
            provider=provider,
            embedding_model=embedding_model,
            answer_agent_model=context.configuration.answer_agent_model,
        ):
            print(f"preflight: {line}", file=sys.stderr)
    if max_documents < len(documents):
        raise ExecutionGuardError(
            f"max-documents {max_documents} is below prepared count {len(documents)}"
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
        version_ids=version_ids, require_projections=True
    )
    reported_versions = {version.version_id for version in readiness.versions}
    complete_versions = all(
        version.ready
        and tuple(stage.stage for stage in version.stages) == EXPECTED_PIPELINE_STAGES
        and all(stage.status in {"succeeded", "skipped"} for stage in version.stages)
        for version in readiness.versions
    )
    reported_planes = tuple(projection.plane for projection in readiness.projections)
    complete_projections = reported_planes == EXPECTED_PROJECTION_PLANES and all(
        projection.ready for projection in readiness.projections
    )
    if (
        not readiness.ready
        or reported_versions != set(version_ids)
        or not complete_versions
        or not complete_projections
    ):
        raise ExecutionGuardError(
            "the deployment did not report the exact completed"
            " RS-LoCoMo-Full-v7 pipeline and fresh P2/P3 projections"
        )
    _require_serving_revision(context=context, readiness=readiness)
    prior_readiness = context.state.readiness.get(sample_id)
    if prior_readiness is not None and prior_readiness != readiness:
        raise ExecutionGuardError(
            "deployment readiness fingerprint changed after it was checkpointed"
        )
    context.state.readiness[sample_id] = readiness
    _save_state(run_dir=run_dir, state=context.state)
    tools = client.recipes()
    if _models_hash(values=tools) != context.configuration.tool_catalog_sha256:
        raise ExecutionGuardError(
            "deployment recipe catalog differs from the prepared protocol"
        )
    remaining = tuple(
        question
        for question in questions
        if question.item_id not in context.state.answers
    )
    called = sum(record.agent_call_count for record in context.state.answers.values())
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
    try:
        for question in remaining:
            if tracer is None:
                record = _answer_one(
                    question=question,
                    client=client,
                    provider=provider,
                    tools=tools,
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
    finally:
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
    called = sum(record.model_called for record in context.state.judges.values())
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
        answer_agent_calls=sum(
            record.agent_call_count for record in context.state.answers.values()
        ),
        total_reader_retries=sum(
            max(record.reader_attempts - 1, 0)
            for record in context.state.answers.values()
        ),
        judge_calls=sum(
            record.model_called for record in context.state.judges.values()
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
    combined_state = RunState(
        protocol_name=contexts[0].configuration.protocol_name,
        protocol_fingerprint=contexts[0].configuration.protocol_fingerprint,
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
        item_samples = {
            question.item_id: question.sample_id for question in context.questions
        }
        recorded = {
            item_samples[item_id]
            for item_id in set(context.state.answers) | set(context.state.judges)
        }
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
        raise BenchmarkRunError("run dataset hash is not RS-LoCoMo-Full-v7")
    if item_ids_hash(item_ids=manifest.item_ids) != manifest.item_ids_sha256:
        raise BenchmarkRunError("run manifest item hash changed")
    if manifest_bytes_hash(manifest=manifest) != configuration.manifest_sha256:
        raise BenchmarkRunError("run manifest hash changed")
    if manifest.item_ids_sha256 != configuration.item_ids_sha256:
        raise BenchmarkRunError("run item ID hash changed")
    if manifest.tier != configuration.tier:
        raise BenchmarkRunError("run manifest tier changed")
    if configuration.dataset_commit != DATASET_COMMIT:
        raise BenchmarkRunError("run dataset commit is not RS-LoCoMo-Full-v7")
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
        "knowledge_mode": configuration.knowledge_mode,
        "answer_agent_model": configuration.answer_agent_model,
        "answer_agent_reasoning_effort": (configuration.answer_agent_reasoning_effort),
        "judge_model": configuration.judge_model,
        "answer_agent_temperature": configuration.answer_agent_temperature,
        "judge_temperature": configuration.judge_temperature,
        "judge_repetitions": configuration.judge_repetitions,
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
        "max_tool_calls_per_question": (selected_protocol.max_tool_calls_per_question),
        "max_agent_calls_per_question": (
            selected_protocol.max_agent_calls_per_question
        ),
        "answer_reader_retry_budget": (selected_protocol.answer_reader_retry_budget),
        "answer_agent_reasoning_effort": (
            selected_protocol.answer_agent_reasoning_effort
        ),
        "answer_agent_temperature": selected_protocol.answer_agent_temperature,
        "judge_temperature": selected_protocol.judge_temperature,
        "judge_repetitions": selected_protocol.judge_repetitions,
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
    answer_agent_reasoning_effort: ReasoningEffort | None = None,
    max_tool_calls_per_question: int = MAX_TOOL_CALLS,
    max_agent_calls_per_question: int = MAX_AGENT_CALLS,
    answer_reader_retry_budget: int = ANSWER_READER_RETRY_BUDGET,
    question_trace: QuestionTrace | None = None,
) -> AnswerRecord:
    """Let a bounded agent choose ordinary public recipes, then answer."""
    tool_names = {tool.name for tool in tools}
    trace: list[ToolCallRecord] = []
    usages: list[ProviderCallUsage] = []
    agent_latency_ms = 0
    tool_latency_ms = 0
    agent_call_count = 0
    reader_attempts = 0
    prior_calls = sum(record.agent_call_count for record in state.answers.values())
    for _ in range(max_agent_calls_per_question):
        if prior_calls + agent_call_count >= max_agent_calls:
            raise ExecutionGuardError(
                "answer-agent call ceiling reached before next call"
            )
        _require_cost_before_call(
            spent=state.evaluator_cost_usd, ceiling=max_evaluator_cost_usd
        )
        prompt = render_answer_agent_prompt(
            question=question.question, tools=tools, trace=tuple(trace)
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
                retrieval_succeeded=bool(trace),
                agent_call_count=agent_call_count,
                reader_attempts=reader_attempts + int(bool(trace)),
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
                retrieval_succeeded=bool(trace),
                agent_call_count=agent_call_count,
                reader_attempts=reader_attempts,
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
            if trace:
                reader_attempts += 1
            if agent_observation is not None:
                agent_observation.finish(
                    usage=error.usage,
                    latency_ms=call_latency_ms,
                    outcome="provider_error",
                )
            can_retry = (
                bool(trace)
                and reader_attempts <= answer_reader_retry_budget
                and agent_call_count < max_agent_calls_per_question
                and prior_calls + agent_call_count < max_agent_calls
                and state.evaluator_cost_usd < max_evaluator_cost_usd
            )
            if can_retry:
                continue
            return _failed_answer(
                question=question,
                kind="reader",
                message=str(error),
                retrieval_latency_ms=tool_latency_ms,
                retrieval_succeeded=bool(trace),
                agent_call_count=agent_call_count,
                reader_attempts=reader_attempts,
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
            if agent_observation is not None:
                agent_observation.finish(
                    usage=error.usage,
                    latency_ms=call_latency_ms,
                    outcome="provider_error",
                )
            return _failed_answer(
                question=question,
                kind="reader",
                message=str(error),
                retrieval_latency_ms=tool_latency_ms,
                retrieval_succeeded=bool(trace),
                agent_call_count=agent_call_count,
                reader_attempts=reader_attempts + int(bool(trace)),
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
        if agent_observation is not None and step.action == "tool":
            agent_observation.finish(
                usage=response.usage, latency_ms=call_latency_ms, outcome="tool"
            )
        if state.evaluator_cost_usd > max_evaluator_cost_usd:
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
                    f"reported evaluator spend {state.evaluator_cost_usd} crossed"
                    f" stop threshold {max_evaluator_cost_usd}"
                ),
                retrieval_latency_ms=tool_latency_ms,
                retrieval_succeeded=bool(trace),
                agent_call_count=agent_call_count,
                reader_attempts=(
                    reader_attempts + int(step.action == "answer" and bool(trace))
                ),
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
                    reader_latency_ms=agent_latency_ms,
                    tool_calls=tuple(trace),
                    usages=tuple(usages),
                )
            answer = step.answer or ""
            if len(answer.split()) > 6:
                if agent_observation is not None:
                    agent_observation.finish(
                        usage=response.usage,
                        latency_ms=call_latency_ms,
                        outcome="invalid_response",
                    )
                return _failed_answer(
                    question=question,
                    kind="invalid_response",
                    message="answer agent exceeded the six-word answer limit",
                    retrieval_latency_ms=tool_latency_ms,
                    retrieval_succeeded=True,
                    agent_call_count=agent_call_count,
                    reader_attempts=reader_attempts + 1,
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
                    call.response.dropped_by_hydration for call in trace
                ),
                retrieval_succeeded=True,
                retrieval_latency_ms=tool_latency_ms,
                reader_called=True,
                agent_call_count=agent_call_count,
                reader_attempts=reader_attempts + 1,
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
                retrieval_succeeded=bool(trace),
                agent_call_count=agent_call_count,
                reader_attempts=reader_attempts,
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
                retrieval_succeeded=True,
                agent_call_count=agent_call_count,
                reader_attempts=reader_attempts,
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
            envelope = client.run_recipe(
                name=step.tool_name or "", arguments=step_arguments
            )
        except MemoryApiError as error:
            failed_latency = _elapsed_ms(tool_started)
            if tool_observation is not None:
                tool_observation.finish(latency_ms=failed_latency, outcome="api_error")
            return _failed_answer(
                question=question,
                kind="tool",
                message=str(error),
                retrieval_latency_ms=tool_latency_ms + failed_latency,
                retrieval_succeeded=False,
                agent_call_count=agent_call_count,
                reader_attempts=reader_attempts,
                reader_latency_ms=agent_latency_ms,
                claims=_claims_from_trace(
                    trace=tuple(trace), doc_sessions=doc_sessions
                ),
                tool_calls=tuple(trace),
                usages=tuple(usages),
            )
        latency = _elapsed_ms(tool_started)
        if tool_observation is not None:
            tool_observation.finish(latency_ms=latency, outcome="succeeded")
        tool_latency_ms += latency
        trace.append(
            ToolCallRecord(
                name=step.tool_name or "",
                arguments=step_arguments,
                arguments_trailing=step_trailing,
                latency_ms=latency,
                response=envelope,
            )
        )
    return _failed_answer(
        question=question,
        kind="invalid_response",
        message="answer agent exhausted its step budget without a final answer",
        retrieval_latency_ms=tool_latency_ms,
        retrieval_succeeded=bool(trace),
        agent_call_count=agent_call_count,
        reader_attempts=reader_attempts,
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
    question_trace: QuestionTrace | None = None,
) -> JudgeRecord:
    """Invoke the judge once; every call failure becomes a visible wrong."""
    called = sum(record.model_called for record in state.judges.values())
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
        if judge_observation is not None:
            judge_observation.finish(
                usage=error.usage,
                latency_ms=latency_ms,
                outcome="provider_error",
                verdict="WRONG",
            )
        return JudgeRecord(
            item_id=question.item_id,
            label="WRONG",
            model_called=True,
            usage=error.usage,
            latency_ms=latency_ms,
            failure=_failure(kind="judge", message=str(error)),
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
            call.response.dropped_by_hydration for call in tool_calls
        ),
        retrieval_succeeded=retrieval_succeeded,
        retrieval_latency_ms=retrieval_latency_ms,
        reader_called=agent_call_count > 0,
        agent_call_count=agent_call_count,
        reader_attempts=reader_attempts,
        reader_latency_ms=reader_latency_ms,
        reader_usage=_aggregate_usage(usages=usages) if usages else None,
        failure=_failure(kind=kind, message=message),
    )


def _claims_from_trace(
    *, trace: tuple[ToolCallRecord, ...], doc_sessions: dict[UUID, str]
) -> tuple[RetrievedClaim, ...]:
    """Collect first-seen evidence claims from every public tool response."""
    seen: set[UUID] = set()
    claims: list[RetrievedClaim] = []
    for call in trace:
        evidence = [
            *call.response.evidence,
            *(item for part in call.response.parts for item in part.evidence),
        ]
        for claim in evidence:
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
        chunks = [
            *call.response.chunks,
            *(item for part in call.response.parts for item in part.chunks),
        ]
        sessions.update(
            session
            for chunk in chunks
            if (session := doc_sessions.get(chunk.doc_id)) is not None
        )
    return sessions


def _aggregate_usage(*, usages: tuple[ProviderCallUsage, ...]) -> ProviderCallUsage:
    """Collapse one question's same-model agent calls for durable accounting."""
    if not usages:
        raise ValueError("cannot aggregate an empty usage tuple")
    model_names = {usage.model_name for usage in usages}
    if len(model_names) != 1:
        raise BenchmarkRunError("one answer-agent trace used multiple models")
    return ProviderCallUsage(
        model_name=usages[0].model_name,
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
    if ceiling <= 0:
        raise ExecutionGuardError("max-evaluator-cost-usd must be positive")
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
    """Collect successful reader and judge usage records exactly once."""
    return tuple(
        usage
        for usage in (
            *(record.reader_usage for record in state.answers.values()),
            *(record.usage for record in state.judges.values()),
        )
        if usage is not None
    )


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
