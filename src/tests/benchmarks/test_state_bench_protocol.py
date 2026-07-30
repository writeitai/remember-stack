"""Pure RS-STATE-Learning-v1 protocol and serializer proofs."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.state_bench.protocol import learning_string
from benchmarks.state_bench.protocol import PROTOCOL_NAME
from benchmarks.state_bench.protocol import source_ref
from benchmarks.state_bench.protocol import STATE_BENCH_COMMIT
from benchmarks.state_bench.retrieve import format_learnings_from_envelope
from benchmarks.state_bench.runner import load_manifest
from benchmarks.state_bench.runner import plan_matrix
from benchmarks.state_bench.runner import preflight_episode_estimate
from benchmarks.state_bench.runner import validate_manifest
from benchmarks.state_bench.trajectories import assert_no_test_leakage
from benchmarks.state_bench.trajectories import bm25_learning_strings
from benchmarks.state_bench.trajectories import render_trajectory_markdown
from benchmarks.state_bench.trajectories import serialize_trajectory_document
from benchmarks.state_bench.trajectories import TrajectoryError
import pytest


def test_committed_manifests_validate() -> None:
    for tier in ("smoke", "development", "publication"):
        manifest = load_manifest(tier=tier)  # type: ignore[arg-type]
        validate_manifest(manifest)
        assert manifest.protocol == PROTOCOL_NAME
        assert manifest.state_bench_commit == STATE_BENCH_COMMIT


def test_source_ref_and_learning_string_shapes() -> None:
    assert source_ref(domain="travel", task_id="1-cancel") == "travel/1-cancel"
    text = learning_string(source_id="travel/1", text="fee is $50", rank=1)
    assert text.startswith("[rank=1 source=travel/1]")
    assert "fee is $50" in text


def test_trajectory_markdown_includes_tool_calls(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    path.write_text(
        json.dumps(
            {
                "conversation": [
                    {"role": "user", "content": "Cancel please"},
                    {
                        "role": "assistant",
                        "content": "Checking",
                        "tool_calls": [
                            {"name": "get_booking", "arguments": {"id": "BK-1"}}
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    document = serialize_trajectory_document(
        domain="travel", task_id="1-cancel", trajectory_path=path
    )
    assert "Cancel please" in document.markdown
    assert "get_booking" in document.markdown
    assert document.source_ref == "travel/1-cancel"
    assert len(document.content_sha256) == 64


def test_train_test_leakage_guard() -> None:
    assert_no_test_leakage(train_task_ids={"a", "b"}, test_task_ids={"c"})
    with pytest.raises(TrajectoryError, match="leakage"):
        assert_no_test_leakage(train_task_ids={"a", "b"}, test_task_ids={"b", "c"})


def test_matrix_episode_preflight_counts() -> None:
    plan = plan_matrix(
        tier="smoke",
        arms=("empty", "rememberstack"),
        sub_protocols=("shared",),
        domains=("travel",),
        num_runs=1,
        num_workers=4,
    )
    assert plan.cell_count == 2
    assert plan.cells[0].num_workers == 4
    assert len(plan.cells[0].task_ids) == 5
    estimate = preflight_episode_estimate(plan=plan)
    assert estimate["episodes"] == 10


def test_matrix_skips_invalid_native_pairs() -> None:
    plan = plan_matrix(
        tier="smoke",
        arms=("empty", "rememberstack"),
        sub_protocols=("shared", "native"),
        domains=("travel",),
        num_runs=1,
        num_workers=2,
    )
    assert plan.cell_count == 2
    assert all(cell.sub_protocol == "shared" for cell in plan.cells)


def test_prepare_rejects_native_and_multi_domain_rs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.state_bench import runner as runner_mod

    monkeypatch.setattr(runner_mod, "assert_clean_worktree", lambda **_: None)
    monkeypatch.setattr(runner_mod, "_assert_upstream_pin", lambda **_: None)
    with pytest.raises(runner_mod.BenchmarkRunError, match="native"):
        runner_mod.prepare_run(
            output=tmp_path / "a",
            tier="smoke",
            arm="rememberstack",
            sub_protocol="native",
            state_bench_root=tmp_path,
            agent_model_name="gpt-5.1",
            domains=("travel",),
            allow_dirty=True,
        )
    with pytest.raises(runner_mod.BenchmarkRunError, match="exactly one"):
        runner_mod.prepare_run(
            output=tmp_path / "b",
            tier="smoke",
            arm="rememberstack",
            sub_protocol="shared",
            state_bench_root=tmp_path,
            agent_model_name="gpt-5.1",
            domains=("travel", "customer_support"),
            allow_dirty=True,
        )


def test_bm25_prefers_token_overlap() -> None:
    from benchmarks.state_bench.model import TrajectoryDocument

    docs = (
        TrajectoryDocument(
            domain="travel",
            task_id="a",
            source_ref="travel/a",
            title="a",
            markdown="# cancel fee policy refund",
            content_sha256="a" * 64,
        ),
        TrajectoryDocument(
            domain="travel",
            task_id="b",
            source_ref="travel/b",
            title="b",
            markdown="# hotel upgrade points",
            content_sha256="b" * 64,
        ),
    )
    hits = bm25_learning_strings(documents=docs, query="cancel fee", top_k=1)
    assert len(hits) == 1
    assert "travel/a" in hits[0]


def test_format_learnings_from_envelope_caps_top_k() -> None:
    from types import SimpleNamespace
    from uuid import uuid4

    evidence = SimpleNamespace(
        claim_id=uuid4(),
        claim_text="Cancellation fee is fifty dollars",
        doc_id=uuid4(),
        chunk_id=uuid4(),
        source_span="span",
        char_start=0,
        char_end=10,
        is_attributed=False,
        is_current_testimony=True,
        claim_valid_from=None,
        claim_valid_until=None,
    )
    envelope = SimpleNamespace(evidence=(evidence, evidence), facts=(), ranking=())
    items = format_learnings_from_envelope(envelope, top_k=1)  # type: ignore[arg-type]
    assert len(items) == 1
    assert "Cancellation fee is fifty dollars" in items[0]


def test_render_trajectory_rejects_empty_conversation() -> None:
    with pytest.raises(TrajectoryError):
        render_trajectory_markdown(
            domain="travel", task_id="x", trajectory={"conversation": []}
        )
