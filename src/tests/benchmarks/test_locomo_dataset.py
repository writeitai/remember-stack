"""Synthetic-only LoCoMo parsing and committed-manifest checks."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
import json
from pathlib import Path
from typing import cast

from benchmarks.locomo.dataset import DatasetValidationError
from benchmarks.locomo.dataset import item_ids_hash
from benchmarks.locomo.dataset import load_dataset
from benchmarks.locomo.dataset import load_manifest
from benchmarks.locomo.dataset import parse_session_timestamp
import pytest


def test_nested_sessions_orphan_timestamps_and_integer_answers(tmp_path: Path) -> None:
    """Only list sessions count; retained integer answers canonicalize to text."""
    sample_input = _sample()
    sample_input["observation"] = {"leak": "POST-HOC SECRET"}
    sample_input["session_summary"] = {"leak": "SUMMARY SECRET"}
    path = _write_dataset(tmp_path=tmp_path, samples=[sample_input])

    dataset = load_dataset(path, required_sha256=None, require_pinned_counts=False)

    sample = dataset.samples[0]
    assert tuple(session.session_id for session in sample.sessions) == ("D1",)
    assert sample.sessions[0].source_modified_at == datetime(
        2023, 5, 1, 13, 0, tzinfo=timezone.utc
    )
    assert sample.sessions[0].source_timezone_basis == "assumed_utc"
    assert sample.questions[0].answer == "2022"
    assert sample.questions[-1].answer is None
    turn = sample.sessions[0].turns[0]
    assert turn.image_urls == ("https://example.test/not-fetched.jpg",)
    assert turn.image_query == "a search query"
    assert "SECRET" not in sample.model_dump_json()


def test_retained_null_answer_is_rejected(tmp_path: Path) -> None:
    sample = _sample()
    questions = cast("list[dict[str, object]]", sample["qa"])
    questions[0]["answer"] = None
    path = _write_dataset(tmp_path=tmp_path, samples=[sample])

    with pytest.raises(DatasetValidationError, match="retained question"):
        load_dataset(path, required_sha256=None, require_pinned_counts=False)


def test_duplicate_dialog_id_is_rejected(tmp_path: Path) -> None:
    sample = _sample()
    conversation = cast("dict[str, object]", sample["conversation"])
    turns = cast("list[dict[str, object]]", conversation["session_1"])
    turns.append({"speaker": "Beta", "dia_id": "D1:1", "text": "Duplicate identifier."})
    path = _write_dataset(tmp_path=tmp_path, samples=[sample])

    with pytest.raises(DatasetValidationError, match="duplicate dialog"):
        load_dataset(path, required_sha256=None, require_pinned_counts=False)


def test_wrong_hash_fails_before_parsing(tmp_path: Path) -> None:
    path = _write_dataset(tmp_path=tmp_path, samples=[_sample()])

    with pytest.raises(DatasetValidationError, match="SHA-256"):
        load_dataset(path, required_sha256="0" * 64, require_pinned_counts=False)


@pytest.mark.parametrize(
    ("timestamp", "expected_hour"),
    (("12:00 am on 1 May, 2023", 0), ("12:00 pm on 1 May, 2023", 12)),
)
def test_unzoned_session_timestamp_is_parsed_as_utc(
    timestamp: str, expected_hour: int
) -> None:
    parsed = parse_session_timestamp(timestamp=timestamp)

    assert parsed == datetime(2023, 5, 1, expected_hour, tzinfo=timezone.utc)


def test_invalid_session_timestamp_fails_during_dataset_load(tmp_path: Path) -> None:
    sample = _sample()
    conversation = cast("dict[str, object]", sample["conversation"])
    conversation["session_1_date_time"] = "May 1-ish"
    path = _write_dataset(tmp_path=tmp_path, samples=[sample])

    with pytest.raises(DatasetValidationError, match="unsupported format"):
        load_dataset(path, required_sha256=None, require_pinned_counts=False)


@pytest.mark.parametrize(
    ("tier", "expected_count"),
    (("smoke", 8), ("development", 200), ("publication", 1_540)),
)
def test_committed_manifest_is_exact_and_self_hashed(
    tier: str, expected_count: int
) -> None:
    manifest = load_manifest(tier)

    assert len(manifest.item_ids) == expected_count
    assert len(set(manifest.item_ids)) == expected_count
    assert item_ids_hash(item_ids=manifest.item_ids) == manifest.item_ids_sha256
    assert all("/qa/" in item_id for item_id in manifest.item_ids)


def _write_dataset(*, tmp_path: Path, samples: list[dict[str, object]]) -> Path:
    path = tmp_path / "locomo.json"
    path.write_text(json.dumps(samples), encoding="utf-8")
    return path


def _sample() -> dict[str, object]:
    return {
        "sample_id": "conv-test",
        "conversation": {
            "speaker_a": "Alpha",
            "speaker_b": "Beta",
            "session_1": [
                {
                    "speaker": "Alpha",
                    "dia_id": "D1:1",
                    "text": "A message.",
                    "img_url": ["https://example.test/not-fetched.jpg"],
                    "blip_caption": "a derived caption",
                    "query": "a search query",
                    "re-download": True,
                }
            ],
            "session_1_date_time": "1:00 pm on 1 May, 2023",
            "session_9_date_time": "orphan timestamp",
        },
        "qa": [
            {"question": "When?", "answer": 2022, "evidence": ["D1:1"], "category": 2},
            {
                "question": "Unanswerable adversarial question?",
                "adversarial_answer": "invented answer",
                "evidence": [],
                "category": 5,
            },
        ],
        "observation": {},
        "session_summary": {},
        "event_summary": {},
    }
