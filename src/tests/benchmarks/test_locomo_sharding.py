"""Deterministic LoCoMo shard planning tests."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.locomo.sharding.make_shards import main
from benchmarks.locomo.sharding.make_shards import make_shard_plan
from benchmarks.locomo.sharding.make_shards import read_sample_sizes
from benchmarks.locomo.sharding.make_shards import SampleSize
import pytest


def test_largest_first_plan_balances_document_and_turn_units() -> None:
    samples = (
        SampleSize(sample_id="large", document_count=10, turn_count=100),
        SampleSize(sample_id="medium-a", document_count=5, turn_count=60),
        SampleSize(sample_id="medium-b", document_count=5, turn_count=50),
        SampleSize(sample_id="small", document_count=2, turn_count=20),
    )

    plan = make_shard_plan(samples=samples, shard_ids=("host-a", "host-b"))

    assert plan == {"host-a": ["large", "small"], "host-b": ["medium-a", "medium-b"]}


def test_read_sample_sizes_counts_only_session_documents_and_their_turns() -> None:
    dataset = [
        {
            "sample_id": "conv-a",
            "conversation": {
                "speaker_a": "A",
                "session_1": [{"text": "one"}, {"text": "two"}],
                "session_1_date_time": "yesterday",
                "session_2": [{"text": "three"}],
            },
        }
    ]

    assert read_sample_sizes(dataset) == (
        SampleSize(sample_id="conv-a", document_count=2, turn_count=3),
    )


def test_empty_shards_are_retained_when_hosts_outnumber_samples() -> None:
    plan = make_shard_plan(
        samples=(SampleSize("only", document_count=1, turn_count=1),),
        shard_ids=("host-a", "host-b"),
    )

    assert plan == {"host-a": ["only"], "host-b": []}


def test_cli_writes_host_keyed_json_plan(tmp_path: Path) -> None:
    dataset_path = tmp_path / "locomo.json"
    output_path = tmp_path / "plan.json"
    dataset_path.write_text(
        json.dumps(
            [
                {"sample_id": "conv-a", "conversation": {"session_1": [{"text": "a"}]}},
                {
                    "sample_id": "conv-b",
                    "conversation": {"session_1": [{"text": "b"}, {"text": "c"}]},
                },
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            str(dataset_path),
            "--hosts",
            "bench-a",
            "bench-b",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "bench-a": ["conv-b"],
        "bench-b": ["conv-a"],
    }


def test_duplicate_shard_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        make_shard_plan(
            samples=(SampleSize("only", document_count=1, turn_count=1),),
            shard_ids=("host-a", "host-a"),
        )
