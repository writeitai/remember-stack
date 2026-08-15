"""Complete-plane catalog, primitive dispatch, and bounded P3 proofs."""

from datetime import datetime
from datetime import timezone
import json
import os
from pathlib import Path
from uuid import UUID

from benchmarks.locomo.retrieval import answer_tool_catalog
from benchmarks.locomo.retrieval import dispatch_answer_tool
from benchmarks.locomo.retrieval import is_correctable_query_error
from benchmarks.locomo.retrieval import P3Mount
from benchmarks.locomo.retrieval import query_result_failure
from benchmarks.locomo.retrieval import RetrievalInfrastructureError
from benchmarks.locomo.retrieval import RetrievalToolError
import httpx
import pytest

from rememberstack.model import Envelope
from rememberstack.surfaces.sdk import MemoryApiError
from rememberstack.surfaces.sdk import MemoryClient

_RELATION_ID = UUID("57000000-0000-0000-0000-000000000020")
_ENTITY_ID = UUID("57000000-0000-0000-0000-000000000021")


def _query_result_payload(**updates: object) -> dict[str, object]:
    """Return one complete synthetic QueryResult/v1 wire payload."""
    payload: dict[str, object] = {
        "contract": "QueryResult/v1",
        "request_id": "57000000-0000-0000-0000-000000000030",
        "deployment_id": "57000000-0000-0000-0000-000000000001",
        "surface_manifest_hash": "a" * 64,
        "query_hash": "b" * 64,
        "limits": {
            "row_cap": 100,
            "byte_cap": 1_000_000,
            "statement_timeout_ms": 5_000,
            "analytical_tier": False,
        },
        "execution_started_at": "2026-08-07T00:00:00Z",
        "elapsed_ms": 1.0,
        "termination_reason": "completed",
    }
    payload.update(updates)
    return payload


def test_p3_mount_lists_searches_and_reads_one_pinned_snapshot(tmp_path: Path) -> None:
    root = _p3(root=tmp_path)
    (root / "by-source").mkdir()
    (root / "by-source" / "session.md").write_text(
        "# Session\nThe launch code is ORBIT-17.\n", encoding="utf-8"
    )
    mount = P3Mount(root=root, expected_version="p3-v1")

    listed = mount.call(name="p3_list", arguments={"path": "by-source"})
    searched = mount.call(name="p3_search", arguments={"query": "orbit-17"})
    read = mount.call(
        name="p3_read", arguments={"path": "by-source/session.md", "start_line": 2}
    )

    assert listed["entries"] == [
        {"name": "session.md", "path": "by-source/session.md", "type": "file"}
    ]
    assert searched["matches"] == [
        {
            "path": "by-source/session.md",
            "line_number": 2,
            "text": "The launch code is ORBIT-17.",
        }
    ]
    assert read["content"] == "The launch code is ORBIT-17."
    assert read["snapshot_version"] == "p3-v1"


def test_p3_mount_rejects_wrong_version_traversal_and_symlinks(tmp_path: Path) -> None:
    root = _p3(root=tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    (root / "escape").symlink_to(outside)

    with pytest.raises(RetrievalInfrastructureError, match="differs from readiness"):
        P3Mount(root=root, expected_version="other")
    (root / ".snapshot-version").write_text(
        "operator-private-version", encoding="utf-8"
    )
    with pytest.raises(RetrievalInfrastructureError) as version_error:
        P3Mount(root=root, expected_version="expected-version")
    assert "operator-private-version" not in str(version_error.value)
    (root / ".snapshot-version").write_text("p3-v1", encoding="utf-8")
    mount = P3Mount(root=root, expected_version="p3-v1")
    with pytest.raises(RetrievalToolError, match="path must be"):
        mount.call(name="p3_list", arguments={"path": None})
    with pytest.raises(RetrievalToolError, match="relative"):
        mount.call(name="p3_read", arguments={"path": "../outside.md"})
    with pytest.raises(RetrievalToolError, match="symlinks"):
        mount.call(name="p3_read", arguments={"path": "escape"})
    (root / "session.md").write_text("inside", encoding="utf-8")
    with pytest.raises(RetrievalToolError, match="non-directory"):
        mount.call(name="p3_read", arguments={"path": "session.md/extra"})
    with pytest.raises(RetrievalToolError, match="invalid character") as captured:
        mount.call(name="p3_read", arguments={"path": "bad\x00path"})
    assert str(tmp_path) not in str(captured.value)


def test_p3_mount_sanitizes_root_and_io_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "operator-private" / "missing"
    with pytest.raises(RetrievalInfrastructureError) as missing_error:
        P3Mount(root=missing, expected_version="p3-v1")
    assert str(missing) not in str(missing_error.value)

    root = _p3(root=tmp_path)
    mount = P3Mount(root=root, expected_version="p3-v1")
    original_iterdir = Path.iterdir

    def unreadable(path: Path):  # noqa: ANN202
        if path == root:
            raise OSError(f"private host path: {root}")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", unreadable)
    with pytest.raises(RetrievalInfrastructureError) as io_error:
        mount.call(name="p3_list", arguments={})
    assert str(root) not in str(io_error.value)


def test_p3_post_resolution_disappearance_is_infrastructure_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A changing served snapshot never earns the model a correction call."""
    root = _p3(root=tmp_path)
    target = root / "by-source"
    target.mkdir()
    mount = P3Mount(root=root, expected_version="p3-v1")
    original = mount._snapshot_kind  # noqa: SLF001
    inspected = 0

    def disappear(*, path: Path):  # noqa: ANN202
        nonlocal inspected
        if path == target:
            inspected += 1
            if inspected > 1:
                raise RetrievalInfrastructureError(
                    "P3 snapshot entry disappeared during inspection"
                )
        return original(path=path)

    monkeypatch.setattr(mount, "_snapshot_kind", disappear)
    with pytest.raises(RetrievalInfrastructureError, match="disappeared"):
        mount.call(name="p3_list", arguments={"path": "by-source"})


def test_p3_resolution_and_descriptor_failures_are_infrastructure_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A served path race or descriptor exhaustion never earns a correction call."""
    from benchmarks.locomo import retrieval

    root = _p3(root=tmp_path)
    target = root / "session.md"
    target.write_text("inside", encoding="utf-8")
    mount = P3Mount(root=root, expected_version="p3-v1")
    original_resolve = Path.resolve

    def disappear(path: Path, *, strict: bool = False) -> Path:
        """Simulate a path disappearing after its successful lstat."""
        if path == target:
            raise FileNotFoundError("changed after inspection")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", disappear)
    with pytest.raises(RetrievalInfrastructureError, match="changed"):
        mount.call(name="p3_read", arguments={"path": "session.md"})

    monkeypatch.setattr(Path, "resolve", original_resolve)
    monkeypatch.setattr(
        retrieval.os,
        "dup",
        lambda _fd: (_ for _ in ()).throw(OSError("descriptor exhaustion")),
    )
    with pytest.raises(RetrievalInfrastructureError, match="unreadable"):
        mount.call(name="p3_read", arguments={"path": "session.md"})


def test_p3_intermediate_close_failure_releases_every_owned_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed directory handoff retains ownership of both descriptors."""
    from benchmarks.locomo import retrieval

    root = _p3(root=tmp_path)
    nested = root / "nested"
    nested.mkdir()
    (nested / "session.md").write_text("inside", encoding="utf-8")
    mount = P3Mount(root=root, expected_version="p3-v1")
    original_close = retrieval.os.close
    opened_nested_fd: int | None = None
    failed_once = False

    def fail_handoff_once(fd: int) -> None:
        """Fail the first close after the nested directory has been opened."""
        nonlocal failed_once
        if opened_nested_fd is not None and fd != opened_nested_fd and not failed_once:
            failed_once = True
            raise OSError("close handoff failed")
        original_close(fd)

    original_open = retrieval.os.open

    def observe_nested_open(  # noqa: ANN202
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ):
        """Remember the descriptor opened for the nested directory."""
        nonlocal opened_nested_fd
        result = original_open(path, flags, mode, dir_fd=dir_fd)
        if path == "nested":
            opened_nested_fd = result
        return result

    monkeypatch.setattr(retrieval.os, "open", observe_nested_open)
    monkeypatch.setattr(retrieval.os, "close", fail_handoff_once)
    with pytest.raises(RetrievalInfrastructureError, match="unreadable"):
        mount.call(name="p3_read", arguments={"path": "nested/session.md"})

    assert failed_once is True
    assert opened_nested_fd is not None
    with pytest.raises(OSError):
        os.fstat(opened_nested_fd)


def test_p3_root_close_failure_is_best_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mount teardown never exposes a raw operating-system exception."""
    from benchmarks.locomo import retrieval

    mount = P3Mount(root=_p3(root=tmp_path), expected_version="p3-v1")
    root_fd = mount._root_fd  # noqa: SLF001
    assert root_fd is not None
    original_close = retrieval.os.close

    def fail_root_close(fd: int) -> None:
        """Simulate one opaque teardown failure without leaking it to the caller."""
        if fd == root_fd:
            raise OSError("root close failed")
        original_close(fd)

    monkeypatch.setattr(retrieval.os, "close", fail_root_close)
    mount.close()
    mount.close()
    assert mount._root_fd is None  # noqa: SLF001

    monkeypatch.setattr(retrieval.os, "close", original_close)
    original_close(root_fd)


def test_p3_missing_child_checks_that_its_parent_did_not_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing child is correctable only while its inspected parent is unchanged."""
    root = _p3(root=tmp_path)
    mount = P3Mount(root=root, expected_version="p3-v1")
    parent = mount._root / "dir"  # noqa: SLF001
    parent.mkdir()
    target = parent / "session.md"
    target.write_text("inside", encoding="utf-8")
    original_lstat = Path.lstat

    def remove_parent_before_child_lstat(path: Path):  # noqa: ANN202
        """Remove the verified parent immediately before its child's lstat."""
        if path == target and target.exists():
            target.unlink()
            parent.rmdir()
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", remove_parent_before_child_lstat)
    with pytest.raises(RetrievalInfrastructureError, match="changed"):
        mount.call(name="p3_read", arguments={"path": "dir/session.md"})


def test_p3_fifo_replacement_cannot_block_a_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The final descriptor open is non-blocking and rejects a replacement FIFO."""
    from benchmarks.locomo import retrieval

    root = _p3(root=tmp_path)
    target = root / "session.md"
    target.write_text("inside", encoding="utf-8")
    mount = P3Mount(root=root, expected_version="p3-v1")
    original_open = retrieval.os.open
    replaced = False

    def replace_with_fifo(  # noqa: ANN202
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ):
        """Swap the checked file for a FIFO immediately before descriptor open."""
        nonlocal replaced
        if path == "session.md" and dir_fd is not None and not replaced:
            replaced = True
            target.unlink()
            os.mkfifo(target)
            assert flags & os.O_NONBLOCK
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(retrieval.os, "open", replace_with_fifo)
    with pytest.raises(RetrievalInfrastructureError, match="changed type"):
        mount.call(name="p3_read", arguments={"path": "session.md"})


@pytest.mark.parametrize("tool_name", ("p3_search", "p3_read"))
def test_p3_file_replacement_cannot_escape_pinned_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    """Search and read use one no-follow descriptor for type-check and bytes."""
    from benchmarks.locomo import retrieval

    root = _p3(root=tmp_path)
    target = root / "session.md"
    target.write_text("inside", encoding="utf-8")
    outside = tmp_path / "operator-private.md"
    outside.write_text("outside secret", encoding="utf-8")
    mount = P3Mount(root=root, expected_version="p3-v1")
    original_open = retrieval.os.open
    replaced = False

    def replace_before_open(  # noqa: ANN202
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ):
        nonlocal replaced
        if path == "session.md" and dir_fd is not None and not replaced:
            replaced = True
            target.unlink()
            target.symlink_to(outside)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(retrieval.os, "open", replace_before_open)
    arguments = (
        {"query": "outside"} if tool_name == "p3_search" else {"path": "session.md"}
    )

    with pytest.raises(RetrievalInfrastructureError, match="unreadable"):
        mount.call(name=tool_name, arguments=arguments)


def test_p3_walk_caps_are_real_and_fingerprinted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.locomo import retrieval

    root = _p3(root=tmp_path)
    for name in ("a.md", "b.md", "c.md"):
        (root / name).write_text(name, encoding="utf-8")
    monkeypatch.setattr(retrieval, "P3_LIST_MAX_VISITED", 2)
    monkeypatch.setattr(retrieval, "P3_SEARCH_MAX_ENTRIES", 2)
    mount = P3Mount(root=root, expected_version="p3-v1")

    listed = mount.call(name="p3_list", arguments={})
    searched = mount.call(name="p3_search", arguments={"query": "missing"})
    descriptors = {tool.name: tool for tool in answer_tool_catalog()}

    assert listed["visited_entries"] == 2
    assert listed["truncated"] is True
    assert searched["visited_entries"] == 2
    assert searched["truncated"] is True
    assert descriptors["p3_list"].input_schema["x-runtime-limits"] == {
        "visited_entries": 2,
        "returned_entries": retrieval.P3_LIST_LIMIT,
    }
    assert descriptors["p3_search"].input_schema["x-runtime-limits"] == {
        "visited_entries": 2,
        "scanned_files": retrieval.P3_SEARCH_MAX_FILES,
        "scanned_bytes": retrieval.P3_SEARCH_MAX_BYTES,
        "returned_matches": retrieval.P3_SEARCH_MAX_RESULTS,
    }


def test_query_failure_classification_uses_typed_codes() -> None:
    failure = query_result_failure(
        {
            "contract": "QueryResult/v1",
            "error_code": "quota_exceeded",
            "error_message": "query budget exhausted",
        }
    )

    assert failure == ("quota_exceeded", "query budget exhausted")
    assert is_correctable_query_error(code="parse_error") is True
    assert is_correctable_query_error(code="saved_query_not_found") is True
    assert is_correctable_query_error(code="quota_exceeded") is False
    assert is_correctable_query_error(code="p2_unavailable") is False


def test_complete_catalog_dispatches_open_query_and_direct_primitives() -> None:
    observed: list[tuple[str, str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        observed.append((request.method, request.url.path, body))
        if request.url.path == "/query/sql":
            return httpx.Response(
                200,
                json=_query_result_payload(
                    columns=[{"name": "answer", "type": "text", "nullable": False}],
                    rows=[["Prague"]],
                    truncated=False,
                ),
            )
        return httpx.Response(
            200,
            json={
                "grain": "composite",
                "temporal_scope": {
                    "mode": "current",
                    "evaluated_at": "2026-08-07T00:00:00Z",
                    "believed_at": "2026-08-07T00:00:00Z",
                    "identity_regime": "current",
                },
                "freshness": {"pg_live_ts": "2026-08-07T00:00:00Z"},
            },
        )

    raw = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(respond)
    )
    client = MemoryClient(client=raw)
    try:
        sql = dispatch_answer_tool(
            client=client,
            p3=None,
            name="query_sql",
            arguments={"sql": "SELECT 'Prague' AS answer", "max_rows": 1},
        )
        transcript = dispatch_answer_tool(
            client=client,
            p3=None,
            name="transcript_relation",
            arguments={"relation_id": str(_RELATION_ID)},
        )
        observations = dispatch_answer_tool(
            client=client,
            p3=None,
            name="lookup_observations",
            arguments={
                "entity_id": str(_ENTITY_ID),
                "property_query": "location",
                "k": 7,
            },
        )
        relations = dispatch_answer_tool(
            client=client,
            p3=None,
            name="lookup_relations",
            arguments={
                "subject_entity_id": str(_ENTITY_ID),
                "valid_at": datetime(2026, 8, 7, tzinfo=timezone.utc).isoformat(),
            },
        )
    finally:
        raw.close()

    assert isinstance(sql, dict)
    assert isinstance(transcript, Envelope)
    assert isinstance(observations, Envelope)
    assert isinstance(relations, Envelope)
    assert len(answer_tool_catalog()) == 23
    assert observed[0] == (
        "POST",
        "/query/sql",
        {"sql": "SELECT 'Prague' AS answer", "parameters": [], "max_rows": 1},
    )
    assert observed[1][:2] == ("GET", f"/transcript/relation/{_RELATION_ID}")
    assert observed[2][:2] == ("GET", "/lookup/observations")
    assert observed[3][:2] == ("GET", "/lookup/relations")


def test_assured_dispatch_omits_an_empty_optional_entity_scope() -> None:
    """An empty model-produced scope is the same unscoped assured operation."""
    observed: list[object] = []

    def respond(request: httpx.Request) -> httpx.Response:
        observed.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "grain": "fact",
                "temporal_scope": {
                    "mode": "current",
                    "evaluated_at": "2026-08-07T00:00:00Z",
                    "believed_at": "2026-08-07T00:00:00Z",
                    "identity_regime": "current",
                },
                "freshness": {"pg_live_ts": "2026-08-07T00:00:00Z"},
            },
        )

    raw = httpx.Client(
        base_url="http://memory.test", transport=httpx.MockTransport(respond)
    )
    try:
        result = dispatch_answer_tool(
            client=MemoryClient(client=raw),
            p3=None,
            name="fact_context",
            arguments={"query": "launch timing", "entity_ids": []},
        )
    finally:
        raw.close()

    assert isinstance(result, Envelope)
    assert observed == [{"query": "launch timing"}]


def test_query_execution_rejects_partial_query_result_contract() -> None:
    """Two plausible header fields are not a complete QueryResult/v1 response."""
    raw = httpx.Client(
        base_url="http://memory.test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"contract": "QueryResult/v1", "termination_reason": "completed"},
            )
        ),
    )
    try:
        with pytest.raises(MemoryApiError, match="invalid response body"):
            dispatch_answer_tool(
                client=MemoryClient(client=raw),
                p3=None,
                name="query_sql",
                arguments={"sql": "SELECT 1"},
            )
    finally:
        raw.close()


def _p3(*, root: Path) -> Path:
    """Create one minimal valid P3 snapshot directory."""
    p3 = root / "p3"
    p3.mkdir()
    (p3 / ".snapshot-version").write_text("p3-v1", encoding="utf-8")
    return p3
