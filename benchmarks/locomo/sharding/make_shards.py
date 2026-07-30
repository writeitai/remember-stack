"""Balance LoCoMo conversations across independent benchmark hosts."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import re

_SESSION_KEY = re.compile(r"session_[1-9][0-9]*")


@dataclass(frozen=True)
class SampleSize:
    """One conversation's two observable processing-size inputs."""

    sample_id: str
    document_count: int
    turn_count: int

    def __post_init__(self) -> None:
        """Reject sizes that cannot describe a real conversation."""
        if not self.sample_id:
            raise ValueError("sample IDs must be non-empty")
        if self.document_count < 1:
            raise ValueError("document counts must be positive")
        if self.turn_count < 1:
            raise ValueError("turn counts must be positive")

    @property
    def balance_units(self) -> int:
        """Count documents and turns as explicit units of conversation work."""
        return self.document_count + self.turn_count


@dataclass
class _ShardBin:
    """Mutable accumulator for one deterministic largest-first bin."""

    shard_id: str
    sample_ids: list[str]
    documents: int = 0
    turns: int = 0
    units: int = 0


def read_sample_sizes(dataset: object) -> tuple[SampleSize, ...]:
    """Extract per-sample session-document and dialogue-turn counts."""
    if not isinstance(dataset, list) or not dataset:
        raise ValueError("dataset root must be a non-empty JSON array")
    sizes: list[SampleSize] = []
    seen: set[str] = set()
    for position, raw_sample in enumerate(dataset):
        if not isinstance(raw_sample, dict):
            raise ValueError(f"dataset sample {position} must be an object")
        sample_id = raw_sample.get("sample_id")
        conversation = raw_sample.get("conversation")
        if not isinstance(sample_id, str) or not sample_id:
            raise ValueError(f"dataset sample {position} has no sample_id")
        if sample_id in seen:
            raise ValueError(f"dataset contains duplicate sample_id {sample_id!r}")
        if not isinstance(conversation, dict):
            raise ValueError(f"sample {sample_id!r} has no conversation object")
        sessions = [
            turns
            for key, turns in conversation.items()
            if _SESSION_KEY.fullmatch(key) is not None and isinstance(turns, list)
        ]
        if not sessions:
            raise ValueError(f"sample {sample_id!r} has no session documents")
        if any(not turns for turns in sessions):
            raise ValueError(f"sample {sample_id!r} has an empty session document")
        sizes.append(
            SampleSize(
                sample_id=sample_id,
                document_count=len(sessions),
                turn_count=sum(len(turns) for turns in sessions),
            )
        )
        seen.add(sample_id)
    return tuple(sizes)


def make_shard_plan(
    *, samples: Sequence[SampleSize], shard_ids: Sequence[str]
) -> dict[str, list[str]]:
    """Assign largest conversations to the currently lightest shard."""
    if not samples:
        raise ValueError("at least one sample is required")
    if not shard_ids:
        raise ValueError("at least one shard is required")
    if any(not shard_id for shard_id in shard_ids):
        raise ValueError("shard IDs must be non-empty")
    if len(set(shard_ids)) != len(shard_ids):
        raise ValueError("shard IDs must be unique")
    if len({sample.sample_id for sample in samples}) != len(samples):
        raise ValueError("sample IDs must be unique")
    bins = [_ShardBin(shard_id=shard_id, sample_ids=[]) for shard_id in shard_ids]
    largest_first = sorted(
        samples,
        key=lambda sample: (
            -sample.balance_units,
            -sample.turn_count,
            -sample.document_count,
            sample.sample_id,
        ),
    )
    for sample in largest_first:
        target = min(
            enumerate(bins),
            key=lambda item: (
                item[1].units,
                item[1].turns,
                item[1].documents,
                len(item[1].sample_ids),
                item[0],
            ),
        )[1]
        target.sample_ids.append(sample.sample_id)
        target.documents += sample.document_count
        target.turns += sample.turn_count
        target.units += sample.balance_units
    return {shard.shard_id: list(shard.sample_ids) for shard in bins}


def main(argv: list[str] | None = None) -> int:
    """Read one dataset and emit a deterministic JSON shard plan."""
    parser = argparse.ArgumentParser(
        description=(
            "balance LoCoMo samples by combined session-document and turn counts"
        )
    )
    parser.add_argument("dataset", type=Path)
    destinations = parser.add_mutually_exclusive_group(required=True)
    destinations.add_argument("--shards", type=_positive_int)
    destinations.add_argument("--hosts", nargs="+")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        raw = json.loads(args.dataset.read_text(encoding="utf-8"))
        samples = read_sample_sizes(raw)
        shard_ids = (
            tuple(args.hosts)
            if args.hosts is not None
            else tuple(
                f"shard-{position:02d}" for position in range(1, args.shards + 1)
            )
        )
        plan = make_shard_plan(samples=samples, shard_ids=shard_ids)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))
    rendered = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


def _positive_int(value: str) -> int:
    """Parse one positive shard count."""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
