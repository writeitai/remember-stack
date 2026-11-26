"""CLI arguments, output and polling for the SDK-backed commands."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import httpx
import pytest

from remember import cli
import remember.client as client_module
from remember.client import MemoryClient
from remember.models import AtReadTime
from remember.models import DocumentSearchFilters
from remember.models import EffectivePeriodInput
from remember.models import HistoryReadTime
from remember.models import ReferenceInput

_DOC = "10000000-0000-0000-0000-000000000001"
_VERSION = "10000000-0000-0000-0000-000000000002"
_AT = "2026-09-24T12:00:00+00:00"
_AT_TIME = datetime.fromisoformat(_AT)
_REFERENCE = {
    "kind": "cites",
    "target": {"source_kind": "file", "source_ref": "policy.md"},
}
_PERIODS = [{"effective_from": _AT}]


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Record the SDK call without reading connection settings."""
    client = MagicMock(spec=MemoryClient)
    client.__enter__.return_value = client
    monkeypatch.setattr(cli, "_cli_memory_client", lambda _args: client)
    return client


@pytest.mark.parametrize(
    ("command", "method", "expected"),
    [
        (["deployment"], "deployment_build_info", {}),
        (["processing-layers"], "processing_layers", {}),
        (
            [
                "resolve",
                "Dana",
                "--context-entity-id",
                _DOC,
                "--context-entity-id",
                _VERSION,
            ],
            "resolve",
            {"name": "Dana", "context_entity_ids": (UUID(_DOC), UUID(_VERSION))},
        ),
        (
            ["search", "chunks", "cutover"],
            "search_chunks",
            {
                "query": "cutover",
                "k": 10,
                "channel": "semantic",
                "documents": None,
                "time": None,
            },
        ),
        (
            [
                "search",
                "claims",
                "cutover",
                "--k",
                "25",
                "--channel",
                "bm25",
                "--documents",
                json.dumps({"doc_ids": [_DOC]}),
                "--time",
                json.dumps({"mode": "at", "at": _AT}),
            ],
            "search_claims",
            {
                "query": "cutover",
                "k": 25,
                "channel": "bm25",
                "documents": DocumentSearchFilters(doc_ids=(UUID(_DOC),)),
                "time": AtReadTime(at=_AT_TIME),
            },
        ),
        (
            [
                "lookup",
                "relations",
                "--subject-entity-id",
                _DOC,
                "--object-entity-id",
                _VERSION,
                "--predicate",
                "leads",
                "--valid-at",
                _AT,
                "--limit",
                "20",
                "--cursor",
                "cursor-1",
            ],
            "lookup_relations",
            {
                "subject_entity_id": UUID(_DOC),
                "object_entity_id": UUID(_VERSION),
                "predicate": "leads",
                "valid_at": _AT_TIME,
                "limit": 20,
                "cursor": "cursor-1",
            },
        ),
        (
            ["lookup", "observations", _DOC, "--limit", "2", "--cursor", "cursor-1"],
            "lookup_observations",
            {
                "entity_id": UUID(_DOC),
                "property_query": None,
                "k": 10,
                "limit": 2,
                "cursor": "cursor-1",
            },
        ),
        (
            ["lookup", "observations", _DOC, "--property-query", "team", "--k", "5"],
            "lookup_observations",
            {
                "entity_id": UUID(_DOC),
                "property_query": "team",
                "k": 5,
                "limit": 50,
                "cursor": None,
            },
        ),
        (
            [
                "graph",
                "neighborhood",
                _DOC,
                "--hops",
                "3",
                "--predicate",
                "leads",
                "--predicate",
                "owns",
                "--valid-at",
                _AT,
                "--believed-at",
                _AT,
                "--limit",
                "40",
                "--cursor",
                "cursor-1",
                "--include-paths",
            ],
            "graph_neighborhood",
            {
                "entity_id": UUID(_DOC),
                "hops": 3,
                "predicates": ("leads", "owns"),
                "valid_at": _AT_TIME,
                "believed_at": _AT_TIME,
                "limit": 40,
                "cursor": "cursor-1",
                "include_paths": True,
            },
        ),
        (
            [
                "graph",
                "path",
                _DOC,
                _VERSION,
                "--max-hops",
                "3",
                "--predicate",
                "leads",
            ],
            "graph_path",
            {
                "from_entity_id": UUID(_DOC),
                "to_entity_id": UUID(_VERSION),
                "max_hops": 3,
                "predicates": ("leads",),
                "valid_at": None,
                "believed_at": None,
            },
        ),
        (
            ["graph", "citation-path", _DOC, _VERSION, "--max-hops", "2"],
            "graph_citation_path",
            {"from_doc_id": UUID(_DOC), "to_doc_id": UUID(_VERSION), "max_hops": 2},
        ),
        (
            ["relation", "hydrate", _DOC],
            "hydrate_relation",
            {"relation_id": UUID(_DOC)},
        ),
        (
            ["relation", "transcript", _DOC],
            "transcript_relation",
            {"relation_id": UUID(_DOC)},
        ),
        (
            [
                "documents",
                "section-history",
                _DOC,
                "part/4:approvals",
                "--time",
                json.dumps({"mode": "at", "at": _AT}),
                "--limit",
                "15",
                "--cursor",
                "cursor-1",
            ],
            "section_history",
            {
                "doc_id": UUID(_DOC),
                "section_key": "part/4:approvals",
                "time": AtReadTime(at=_AT_TIME),
                "limit": 15,
                "cursor": "cursor-1",
            },
        ),
        (
            [
                "documents",
                "references",
                "--doc-id",
                _DOC,
                "--section-key",
                "approvals",
                "--direction",
                "incoming",
                "--kind",
                "cites",
                "--kind",
                "amends",
                "--time",
                '{"mode":"history"}',
                "--limit",
                "10",
                "--cursor",
                "cursor-1",
            ],
            "document_references",
            {
                "doc_id": UUID(_DOC),
                "chunk_id": None,
                "section_key": "approvals",
                "direction": "incoming",
                "kinds": ["cites", "amends"],
                "time": HistoryReadTime(),
                "limit": 10,
                "cursor": "cursor-1",
            },
        ),
        (
            ["documents", "references", "--chunk-id", _DOC],
            "document_references",
            {
                "doc_id": None,
                "chunk_id": UUID(_DOC),
                "section_key": None,
                "direction": "both",
                "kinds": None,
                "time": None,
                "limit": 50,
                "cursor": None,
            },
        ),
        (
            ["documents", "reference-generations", _DOC, _VERSION],
            "reference_generations",
            {"doc_id": UUID(_DOC), "version_id": UUID(_VERSION)},
        ),
        (
            ["documents", "set-references", _DOC, _VERSION, "REFS_FILE"],
            "set_references",
            {
                "doc_id": UUID(_DOC),
                "version_id": UUID(_VERSION),
                "references": [ReferenceInput.model_validate(_REFERENCE)],
            },
        ),
        (
            [
                "documents",
                "set-effective-periods",
                _DOC,
                _VERSION,
                "--periods",
                json.dumps(_PERIODS),
            ],
            "set_effective_periods",
            {
                "doc_id": UUID(_DOC),
                "version_id": UUID(_VERSION),
                "periods": (EffectivePeriodInput(effective_from=_AT_TIME),),
            },
        ),
        (
            ["documents", "set-effective-periods", _DOC, _VERSION, "--periods", "[]"],
            "set_effective_periods",
            {"doc_id": UUID(_DOC), "version_id": UUID(_VERSION), "periods": ()},
        ),
        (
            ["documents", "clear-effective-periods", _DOC],
            "clear_effective_time",
            {"doc_id": UUID(_DOC)},
        ),
    ],
    ids=[
        "deployment",
        "processing-layers",
        "resolve",
        "chunks",
        "claims",
        "relations",
        "observations-page",
        "observations-ranked",
        "neighborhood",
        "path",
        "citation-path",
        "hydrate",
        "transcript",
        "section-history",
        "document-references",
        "chunk-references",
        "reference-generations",
        "set-references",
        "set-periods",
        "empty-periods",
        "clear-periods",
    ],
)
def test_cli_passes_parsed_inputs_and_prints_the_result(
    command: list[str],
    method: str,
    expected: dict[str, object],
    client: MagicMock,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the public CLI grammar and assert its exact SDK arguments."""
    references = tmp_path / "references with spaces.ndjson"
    references.write_text("\n" + json.dumps(_REFERENCE) + "\n", encoding="utf-8")
    sdk_call = getattr(client, method)
    sdk_call.return_value.model_dump_json.return_value = '{"result":"example"}'
    paged = method in {"section_history", "document_references"}
    if paged:
        sdk_call.return_value.next_cursor = "cursor-2"
    argv = [str(references) if arg == "REFS_FILE" else arg for arg in command]

    assert cli.main([*argv, "--json"]) == 0

    sdk_call.assert_called_once_with(**expected)
    output = capsys.readouterr()
    assert json.loads(output.out) == {"result": "example"}
    assert output.err == ("# next_cursor=cursor-2\n" if paged else "")


def test_invalid_reference_reports_the_line_without_writing(
    client: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    references = tmp_path / "invalid.ndjson"
    references.write_text(
        json.dumps(_REFERENCE) + '\n\n{"kind":"unrecognised"}', encoding="utf-8"
    )

    assert (
        cli.main(
            ["documents", "set-references", _DOC, _VERSION, str(references), "--json"]
        )
        == 2
    )

    client.set_references.assert_not_called()
    output = capsys.readouterr()
    assert not output.out
    error = json.loads(output.err)["error"]
    assert error["code"] == "invalid_arguments"
    assert "line 3" in error["detail"]


@pytest.mark.parametrize(
    ("options", "statuses", "sleeps", "error_code"),
    [
        (["--require-p3"], ["dead_letter"], [], None),
        (["--wait"], ["succeeded"], [], None),
        (
            ["--wait", "--poll-interval", "0.25", "--require-p3"],
            ["failed", "succeeded"],
            [0.25],
            None,
        ),
        (["--wait"], ["dead_letter"], [], "pipeline_dead_lettered"),
        (["--wait", "--timeout", "2"], ["pending"], [2.0], "readiness_timeout"),
        (["--wait"], [524, "succeeded"], [15.0], None),
        (
            ["--wait", "--timeout", "7", "--poll-interval", "1"],
            ["pending", 503, 503],
            [1.0, 2.0, 4.0],
            "readiness_timeout",
        ),
        (["--wait", "--timeout", "3"], [503], [3.0], "readiness_timeout"),
    ],
    ids=[
        "inspect",
        "ready",
        "failed-then-ready",
        "dead-letter",
        "timeout",
        "524-then-ready",
        "503-timeout-with-report",
        "503-timeout-without-report",
    ],
)
def test_readiness_polls_through_the_real_sdk(
    options: list[str],
    statuses: list[str | int],
    sleeps: list[float],
    error_code: str | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Check the HTTP request and the CLI result without real polling delays."""
    bodies: list[dict[str, object]] = []
    slept: list[float] = []
    replies = iter(statuses)

    def respond(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("POST", "/readiness")
        bodies.append(json.loads(request.content))
        status = next(replies)
        if isinstance(status, int):
            return httpx.Response(status, text="Deployment unavailable")
        ready = status == "succeeded"
        return httpx.Response(
            200,
            json={
                "ready": ready,
                "versions": [
                    {
                        "version_id": version,
                        "ready": ready,
                        "stages": [
                            {
                                "stage": "extract",
                                "status": status,
                                "component_version": "1",
                            }
                        ],
                    }
                    for version in (_VERSION, _DOC)
                ],
                "capabilities": {},
            },
        )

    monkeypatch.setattr(
        client_module,
        "time",
        SimpleNamespace(monotonic=lambda: sum(slept), sleep=slept.append),
    )
    with httpx.Client(
        base_url="http://engine.test", transport=httpx.MockTransport(respond)
    ) as http:
        monkeypatch.setattr(
            cli, "_cli_memory_client", lambda _args: MemoryClient(client=http)
        )
        assert cli.main(["readiness", _VERSION, _DOC, *options, "--json"]) == int(
            error_code is not None
        )

    expected_body = {
        "version_ids": [_VERSION, _DOC],
        "require": {
            "pipeline": True,
            "p1": True,
            "live_graph": True,
            "p3": "--require-p3" in options,
        },
    }
    assert bodies == [expected_body] * len(statuses)
    assert slept == sleeps
    output = capsys.readouterr()
    if error_code is None:
        assert not output.err
    else:
        error = json.loads(output.err)["error"]
        assert error["code"] == error_code
        if error_code == "pipeline_dead_lettered":
            assert _VERSION in error["detail"] and "extract" in error["detail"]
            assert not output.out
            return
        if isinstance(statuses[-1], int):
            assert f"Last readiness check error: API {statuses[-1]}" in error["detail"]
    last_stage = next(
        (status for status in reversed(statuses) if isinstance(status, str)), None
    )
    if last_stage is None:
        assert not output.out
        return
    report = json.loads(output.out)
    assert report["ready"] is (last_stage == "succeeded")
    assert report["versions"][0]["stages"][0]["status"] == last_stage


@pytest.mark.parametrize("interval", ["0", "nan", "inf"])
def test_readiness_rejects_an_invalid_poll_interval(
    interval: str, client: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(
            ["readiness", _VERSION, "--wait", "--poll-interval", interval, "--json"]
        )
        == 2
    )

    client.wait_for_readiness.assert_not_called()
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "invalid_arguments"
