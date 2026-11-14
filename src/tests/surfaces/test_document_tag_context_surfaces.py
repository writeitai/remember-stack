"""D156 step 3 surfaces: the context operations' ``documents`` filter on every surface.

Database-free: the operation catalogue and its MCP schemas, JSON coercion in
the shared ``OperationSurface``, the executor handing the same filter to both
``combined_context`` children, HTTP and in-process MCP mapping of
``unknown_tag``, the SDK and CLI arguments, and the optional response fields.
"""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
import json
from pathlib import Path
from typing import cast
from uuid import UUID

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from remember.cli import main as cli_main
from remember.client import MemoryClient
from remember.errors import UnknownTag
from remember.mcp_tools import tool
from remember.models import DocumentSearchFilters
from remember.models import DocumentTagFilter
from rememberstack.model import AssuredOperation
from rememberstack.model import ClaimValidPrecision
from rememberstack.model import ContextBundleV2
from rememberstack.model import Envelope
from rememberstack.model import EvidenceTotal
from rememberstack.model import FactEvidence
from rememberstack.model import Grain
from rememberstack.model import UnknownTagError
from rememberstack.model.client import ProcessingLayer
from rememberstack.model.envelope import CoMember
from rememberstack.model.envelope import Validity
from rememberstack.spine.assured_operations import CANONICAL_OPERATIONS
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.http_api import build_api
from rememberstack.surfaces.mcp import OperationMcpServer
from rememberstack.surfaces.operation_executor import OperationExecutor
from rememberstack.surfaces.operation_surface import InvalidArgumentError
from rememberstack.surfaces.operation_surface import OperationSurface
from rememberstack.surfaces.query_engine import unconsulted_layer_envelope

_DEPLOYMENT_ID = UUID("62000000-0000-0000-0000-0000000d1563")
_AT = datetime(2026, 11, 14, tzinfo=UTC)
_CONTEXT = ("claims_and_sources_context", "facts_context", "combined_context")


class _Registry:
    def by_name(self, *, deployment_id: UUID, name: str) -> AssuredOperation | None:
        del deployment_id
        return next((op for op in CANONICAL_OPERATIONS if op.name.value == name), None)

    def active(self, *, deployment_id: UUID) -> tuple[AssuredOperation, ...]:
        del deployment_id
        return CANONICAL_OPERATIONS


class _RecordingExecutor:
    """Record coerced arguments; refuse the tag ``typo`` as the engine does."""

    def __init__(self) -> None:
        self.arguments: list[dict[str, object]] = []

    def execute(
        self,
        *,
        deployment_id: UUID,
        operation: AssuredOperation,
        arguments: dict[str, object],
        evaluated_at: datetime | None = None,
        layers: object = None,
    ) -> Envelope | ContextBundleV2:
        del deployment_id, evaluated_at, layers
        self.arguments.append(arguments)
        documents = cast(DocumentSearchFilters | None, arguments.get("documents"))
        if documents is not None and documents.tags is not None:
            unknown = [tag for tag in documents.tags.all if tag == "typo"]
            if unknown:
                raise UnknownTagError(tags=unknown)
        if operation.output_grain is None:
            return ContextBundleV2(
                claims_and_sources=_empty(grain=Grain.EVIDENCE),
                facts=_empty(grain=Grain.FACT),
            )
        return _empty(grain=operation.output_grain)


def _empty(*, grain: Grain) -> Envelope:
    return unconsulted_layer_envelope(
        grain=grain, time=None, evaluated_at=_AT, layers=frozenset()
    )


def _surface() -> tuple[OperationSurface, _RecordingExecutor]:
    executor = _RecordingExecutor()
    surface = OperationSurface(
        registry=cast("object", _Registry()),  # type: ignore[arg-type]
        executor=cast("OperationExecutor", executor),
        deployment_id=_DEPLOYMENT_ID,
    )
    return surface, executor


class _Boundary:
    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        return None


def _http() -> tuple[TestClient, _RecordingExecutor]:
    surface, executor = _surface()
    boundary = _Boundary()
    app = build_api(
        engine=cast("QueryEngine", object()),
        deployment_id=_DEPLOYMENT_ID,
        admission=boundary,
        readiness=boundary,
        surface=surface,
    )
    return TestClient(app), executor


def test_the_three_context_tools_take_the_documents_filter() -> None:
    """Versions 5/5/6, a closed documents object with tags, list_tags guidance."""
    versions = {name: tool(name).tool_version for name in _CONTEXT}
    assert versions == {
        "claims_and_sources_context": 5,
        "facts_context": 5,
        "combined_context": 6,
    }
    for name in _CONTEXT:
        definition = tool(name)
        properties = cast("dict[str, dict[str, object]]", definition.input_schema)[
            "properties"
        ]
        documents = cast("dict[str, object]", properties["documents"])
        assert documents["additionalProperties"] is False
        assert set(cast("dict[str, object]", documents["properties"])) == {
            "family",
            "authors",
            "recipients",
            "created_from",
            "created_to",
            "modified_from",
            "modified_to",
            "language",
            "thread_ref",
            "doc_ids",
            "tags",
        }
        assert "list_tags" in definition.description
    # the tags selector is the same object search_documents takes
    search = cast("dict[str, dict[str, object]]", tool("search_documents").input_schema)
    facts = cast("dict[str, dict[str, object]]", tool("facts_context").input_schema)
    documents = cast("dict[str, dict[str, object]]", facts["properties"]["documents"])
    assert documents["properties"]["tags"] == search["properties"]["tags"]
    resolve = cast("dict[str, dict[str, object]]", tool("resolve_entity").input_schema)
    assert "documents" not in resolve["properties"]
    operations = {op.name.value: op for op in CANONICAL_OPERATIONS}
    assert [operations[name].version for name in _CONTEXT] == [5, 5, 6]


def test_the_operation_surface_validates_documents_before_running() -> None:
    surface, executor = _surface()
    surface.run(
        name="facts_context",
        arguments={
            "query": "q",
            "documents": {
                "family": ["email"],
                "created_from": "2026-01-01T00:00:00Z",
                "tags": {"all": ["project:a"], "none": ["kind:draft"]},
            },
        },
    )
    assert executor.arguments[-1]["documents"] == DocumentSearchFilters(
        family=("email",),
        created_from=datetime(2026, 1, 1, tzinfo=UTC),
        tags=DocumentTagFilter(all=("project:a",), none=("kind:draft",)),
    )
    for documents in (
        {"tags": {}},
        {"tags": {"any": ["a", "A"]}},
        {"tags": {"all": ["x"] * 33}},
        {"colour": "red"},
        {"doc_ids": ["nope"]},
        {"created_from": "2026-01-01T00:00:00"},
        "project:a",
    ):
        with pytest.raises(InvalidArgumentError):
            surface.run(
                name="claims_and_sources_context",
                arguments={"query": "q", "documents": documents},
            )
    with pytest.raises(InvalidArgumentError, match="unknown argument"):
        surface.run(name="resolve_entity", arguments={"name": "x", "documents": {}})


def test_combined_context_hands_the_same_filter_to_both_children() -> None:
    """The executor passes ``documents`` unchanged to each child."""
    calls: dict[str, object] = {}

    class _Engine:
        def layer_admission(self, **_: object) -> object:
            class _Admission:
                consulted = frozenset(ProcessingLayer)

                def admits(self, *, layer: object) -> bool:
                    return True

            return _Admission()

        def claims_and_sources_context(self, **kwargs: object) -> Envelope:
            calls["claims_and_sources"] = kwargs["documents"]
            return unconsulted_layer_envelope(
                grain=Grain.EVIDENCE, time=None, evaluated_at=_AT, layers=frozenset()
            )

        def default_facts_context(self, **kwargs: object) -> Envelope:
            calls["facts"] = kwargs["documents"]
            return unconsulted_layer_envelope(
                grain=Grain.FACT, time=None, evaluated_at=_AT, layers=frozenset()
            )

    surface = OperationSurface(
        registry=cast("object", _Registry()),  # type: ignore[arg-type]
        executor=OperationExecutor(query_engine=cast("QueryEngine", _Engine())),
        deployment_id=_DEPLOYMENT_ID,
    )
    surface.run(
        name="combined_context",
        arguments={"query": "q", "documents": {"tags": {"any": ["project:a"]}}},
    )
    expected = DocumentSearchFilters(tags=DocumentTagFilter(any=("project:a",)))
    assert calls == {"claims_and_sources": expected, "facts": expected}


def test_http_and_sdk_carry_documents_and_map_unknown_tags() -> None:
    http, executor = _http()
    client = MemoryClient(client=http)
    client.facts_context("q", documents={"tags": {"all": ["project:a"]}})
    client.claims_and_sources_context(
        "q", documents=DocumentSearchFilters(family=("email",))
    )
    client.combined_context(
        "q", documents=DocumentSearchFilters(tags=DocumentTagFilter(none=("ci",)))
    )
    assert [args["documents"] for args in executor.arguments] == [
        DocumentSearchFilters(tags=DocumentTagFilter(all=("project:a",))),
        DocumentSearchFilters(family=("email",)),
        DocumentSearchFilters(tags=DocumentTagFilter(none=("ci",))),
    ]
    client.facts_context("q")
    assert "documents" not in executor.arguments[-1]

    refused = http.post(
        "/operations/facts_context",
        json={"query": "q", "documents": {"tags": {"all": ["typo"]}}},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"] == {
        "code": "unknown_tag",
        "message": "unknown tags: typo",
        "tags": ["typo"],
    }
    with pytest.raises(UnknownTag) as unknown:
        client.facts_context("q", documents={"tags": {"all": ["typo"]}})
    assert unknown.value.tags == ("typo",)
    invalid = http.post(
        "/operations/combined_context", json={"query": "q", "documents": {"tags": {}}}
    )
    assert (invalid.status_code, invalid.json()["detail"]["code"]) == (
        422,
        "invalid_parameter",
    )
    # the SDK refuses a malformed filter before sending it
    sent = len(executor.arguments)
    with pytest.raises(ValidationError):
        client.facts_context("q", documents={"tags": {"all": []}})
    assert len(executor.arguments) == sent


def test_in_process_mcp_maps_an_unknown_tag_to_unknown_tag() -> None:
    surface, executor = _surface()
    server = OperationMcpServer(surface=surface)
    ok = server.call_tool(
        name="facts_context",
        arguments={"query": "q", "documents": {"tags": {"all": ["project:a"]}}},
    )
    assert ok["isError"] is False
    refused = server.call_tool(
        name="facts_context",
        arguments={"query": "q", "documents": {"tags": {"all": ["typo"]}}},
    )
    assert refused["isError"] is True
    payload = json.loads(cast("list[dict[str, str]]", refused["content"])[0]["text"])
    assert (payload["error"]["code"], payload["error"]["status_code"]) == (
        "unknown_tag",
        422,
    )
    assert len(executor.arguments) == 2


def test_cli_query_tag_flags_build_the_documents_filter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, executor = _http()
    client = MemoryClient(client=http)
    monkeypatch.setenv("REMEMBER_CONFIG_DIR", str(tmp_path / "cli-config"))
    monkeypatch.setattr("remember.cli._cli_memory_client", lambda _args: client)
    tags = ["--tag", "project:a", "--any-tag", "kind:decision", "--not-tag", "ci"]
    expected = DocumentSearchFilters(
        tags=DocumentTagFilter(all=("project:a",), any=("kind:decision",), none=("ci",))
    )
    assert cli_main(["query", "who owns it", *tags]) == 0
    assert cli_main(["query", "text", "who owns it", "--combined", *tags]) == 0
    assert cli_main(["query", "text", "who", "--layers", "facts", *tags]) == 0
    capsys.readouterr()
    assert [args["documents"] for args in executor.arguments] == [expected] * 3
    assert cli_main(["query", "who owns it"]) == 0
    capsys.readouterr()
    assert "documents" not in executor.arguments[-1]
    sent = len(executor.arguments)
    assert cli_main(["query", "text", "q", "--tag", "x", "--tag", "X"]) == 2
    assert "more than once" in capsys.readouterr().err
    assert len(executor.arguments) == sent


def test_filter_disclosures_are_absent_from_unfiltered_payloads() -> None:
    """Unfiltered reads serialize exactly as before; filtered ones carry the marks."""
    fact_id = UUID("00000000-0000-0000-0000-000000000001")
    plain = FactEvidence(
        fact_kind="relation", fact_id=fact_id, claim_id=fact_id, stance="supports"
    )
    assert "in_documents_filter" not in plain.model_dump_json()
    marked = plain.model_copy(update={"in_documents_filter": False})
    assert json.loads(marked.model_dump_json())["in_documents_filter"] is False
    total = EvidenceTotal(
        fact_kind="relation", fact_id=fact_id, stance="supports", returned=1, total=3
    )
    assert "in_documents_filter_total" not in total.model_dump_json()
    with pytest.raises(ValidationError):
        EvidenceTotal(
            fact_kind="relation",
            fact_id=fact_id,
            stance="supports",
            returned=1,
            total=3,
            in_documents_filter_total=4,
        )
    member = CoMember(
        fact_id=fact_id,
        label="x",
        evidence_count=1,
        validity=Validity(
            valid_from=None,
            valid_until=None,
            valid_precision=ClaimValidPrecision.OPEN,
            ingested_at=_AT,
            invalidated_at=None,
        ),
    )
    assert "in_documents_filter" not in member.model_dump_json()
