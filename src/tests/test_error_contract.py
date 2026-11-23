"""D158: an unexpected failure is raised, or logged at ERROR with a stable code.

One AST rule over ``src/rememberstack`` (D158 design §4):

* every ``except`` that catches a failure class must re-raise (not
  ``raise HTTPException``), log at ERROR with a literal or ``.code``
  ``error_code``, call a reporter, or carry ``# error-contract: <reason>``;
* every ``.error`` / ``.exception`` / ``.critical`` logging call passes such an
  ``error_code``.

``BASELINE`` lists the backlog sites (analysis §5) not yet converted. Each
implementation PR removes the entries it fixes; a stale entry fails the test.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / "rememberstack"
CODE = re.compile(r"[a-z][a-z0-9_]{2,63}")
FAILURE_CLASSES = {
    "Exception",
    "BaseException",
    "ProviderCallError",
    "ProviderAccountingError",
    "ProviderInvalidResponseError",
    "OpenRouterProviderError",
    "MistralOcrProviderError",
    "DBAPIError",
    "SQLAlchemyError",
    "SQLAlchemyTimeoutError",
    "OSError",
    "TimeoutError",
    "httpx.HTTPError",
    "subprocess.TimeoutExpired",
    "RuntimeError",
}
REPORTERS = {"report_failure", "_internal_error"}
LOG_LEVELS = {"error", "exception", "critical"}
REASONS = ("raised later", "expected", "exempt", "mixed")

# Backlog (analysis §5): "relative/path.py::qualified.function" for handlers not
# yet converted. Remove entries as they are fixed.
BASELINE: set[str] = {
    "adapters/codex_writer.py::CodexCLIAgentAdapter.run_session",
    "adapters/codex_writer.py::_declared_outputs",
    "adapters/converters/image_ocr_description.py::ImageOcrDescriptionConverter._run_description",
    "adapters/converters/image_ocr_description.py::ImageOcrDescriptionConverter._run_missing_lanes",
    "adapters/converters/image_ocr_description.py::ImageOcrDescriptionConverter._run_ocr",
    "adapters/converters/pdf.py::_Sink.write",
    "adapters/converters/time_limit.py::run_with_time_limit._target",
    "adapters/fsit_storage_capture_transport.py::_PendingProviderCapture.discard",
    "adapters/generation_recorder.py::OtelSpanRecorder.flush",
    "adapters/generation_recorder.py::OtelSpanRecorder.record",
    "adapters/managed/composite_auth.py::CompositeAuth.authenticate",
    "adapters/managed/perimeter_trust.py::PerimeterTrust.refresh",
    "adapters/openrouter.py::OpenRouterModelProvider._record_generation",
    "adapters/openrouter.py::OpenRouterModelProvider._resolve_provider_host",
    "adapters/openrouter.py::OpenRouterModelProvider._write_invalid_capture",
    "adapters/openrouter.py::OpenRouterModelProvider.generate",
    "adapters/openrouter.py::recover_completion_usage",
    "adapters/postgres_p1.py::PostgresP1Index.entity_semantic_ready",
    "adapters/selfhost/object_store.py::LocalFSObjectStore._prune_parents",
    "adapters/selfhost/queue.py::SelfHostWorkerLoop._slot",
    "adapters/selfhost/telemetry.py::FanoutTelemetry.export_event",
    "adapters/selfhost/telemetry.py::FanoutTelemetry.export_exception",
    "adapters/sentry.py::SentryTelemetry.export_exception",
    "adapters/vertex.py::VertexModelProvider._record_generation",
    "adapters/vertex.py::_error_preview",
    "adapters/vertex.py::_try_usage",
    "core/content_detection.py::_office_zip_mime",
    "profiles/storage_capture_batch.py::capture_storage_batch",
    "spine/corpus_publication.py::CorpusPublicationCatalog.locked",
    "spine/managed_metering.py::ManagedMeterCatalog._deliver_measurements",
    "spine/managed_metering.py::ManagedMeterCatalog._deliver_outcomes",
    "spine/readiness.py::_live_graph_status",
    "surfaces/http_api.py::_authenticate",
    "surfaces/http_api.py::_graph_errors",
    "surfaces/http_api.py::_install_spend_lease.spend_lease_middleware",
    "surfaces/mcp.py::OperationMcpServer.call_tool",
    "surfaces/query_engine.py::QueryEngine._embedding_resolve_hits",
    "surfaces/query_engine.py::QueryEngine._nominate_facts_context",
    "surfaces/query_engine.py::QueryEngine.default_facts_context",
    "surfaces/query_engine.py::QueryEngine.default_facts_context.confirmed_facts",
    "surfaces/query_engine.py::_with_fact_snapshot.wrapped",
    "surfaces/query_sandbox/executor.py::QuerySandboxExecutor._execute",
    "workers/e0.py::StructureHandler._assign_roles",
    "workers/e0.py::StructureHandler._check",
    "workers/e0.py::StructureHandler._fallback",
    "workers/e0_summary.py::SectionSummarizer._invoke_final",
    "workers/e0_summary.py::SectionSummarizer._invoke_summary",
    "workers/e0_summary.py::SectionSummarizer._load_cache",
    "workers/knowledge_driver.py::KnowledgeCommitDriver._collect_parallel_wave",
    "workers/knowledge_driver.py::KnowledgeCommitDriver._record_aborted_wave",
    "workers/reconcile.py::DocumentDeleter.delete_document",
    "workers/sync.py::SyncCycleRunner.run_cycle",
}


def _failure_types(handler: ast.ExceptHandler) -> bool:
    """Whether the handler catches a failure class (analysis §5.1)."""
    if handler.type is None:
        return True
    nodes = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for node in nodes:
        name = ast.unparse(node)
        if (
            name in FAILURE_CLASSES
            or name.startswith("psycopg.")
            or name.endswith(("Unavailable", "UnavailableError"))
        ):
            return True
    return False


def _module_loggers(tree: ast.Module) -> set[str]:
    """Names bound at module level to ``logging.getLogger(...)``."""
    names: set[str] = set()
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and ast.unparse(node.value.func) in {"logging.getLogger", "getLogger"}
        ):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _coded(call: ast.Call) -> bool:
    """``extra={"error_code": <literal matching the pattern> | <name>.code, ...}``."""
    for keyword in call.keywords:
        if keyword.arg != "extra" or not isinstance(keyword.value, ast.Dict):
            continue
        for key, value in zip(keyword.value.keys, keyword.value.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "error_code":
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    return bool(CODE.fullmatch(value.value))
                return isinstance(value, ast.Attribute) and value.attr == "code"
    return False


def _error_log(node: ast.AST, loggers: set[str]) -> ast.Call | None:
    """The node, if it is an ERROR-level call on a module logger."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in LOG_LEVELS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in loggers
    ):
        return node
    return None


def _satisfied(handler: ast.ExceptHandler, loggers: set[str], line: str) -> bool:
    """Raise, coded ERROR log, reporter, or an explicit reason comment."""
    if "# error-contract:" in line:
        reason = line.split("# error-contract:", 1)[1].strip()
        return reason.startswith(REASONS)
    for node in (n for stmt in handler.body for n in ast.walk(stmt)):
        if isinstance(node, ast.Raise):
            raised = node.exc
            target = raised.func if isinstance(raised, ast.Call) else raised
            if raised is None or "HTTPException" not in ast.unparse(target or node):
                return True
        call = _error_log(node, loggers)
        if call is not None and _coded(call):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in REPORTERS
        ):
            return True
    return False


def _scan() -> tuple[set[str], list[str]]:
    """(handler keys violating the rule, uncoded ERROR calls as path:line)."""
    handlers: set[str] = set()
    calls: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        source = path.read_text()
        lines = source.splitlines()
        tree = ast.parse(source)
        loggers = _module_loggers(tree)
        relative = path.relative_to(ROOT).as_posix()
        scopes: list[tuple[ast.AST, str]] = [(tree, "")]
        while scopes:
            scope, prefix = scopes.pop()
            for node in ast.iter_child_nodes(scope):
                name = prefix
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    name = f"{prefix}.{node.name}" if prefix else node.name
                if isinstance(node, ast.ExceptHandler) and _failure_types(node):
                    if not _satisfied(node, loggers, lines[node.lineno - 1]):
                        handlers.add(f"{relative}::{name or '<module>'}")
                call = _error_log(node, loggers)
                if call is not None and not _coded(call):
                    calls.append(f"{relative}:{call.lineno}")
                scopes.append((node, name))
    return handlers, calls


def test_failure_handlers_raise_or_report_with_a_code() -> None:
    """Every failure-class handler outside the backlog follows the contract."""
    handlers, _ = _scan()
    new = sorted(handlers - BASELINE)
    assert new == [], f"handlers that neither raise nor log a coded ERROR: {new}"


def test_baseline_has_no_fixed_entries() -> None:
    """Remove a backlog entry once its handler is fixed."""
    handlers, _ = _scan()
    stale = sorted(BASELINE - handlers)
    assert stale == [], f"fixed, remove from BASELINE: {stale}"


def test_every_error_log_has_a_stable_code() -> None:
    """ERROR-level logging always carries a literal or declared ``error_code``."""
    _, calls = _scan()
    assert calls == [], f"ERROR logs without a stable error_code: {calls}"
