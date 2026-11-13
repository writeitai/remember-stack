"""Synthetic ready folders for offline benchmark-attestation unit tests."""

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from uuid import UUID

from benchmarks.locomo.corpus import CorpusPublication


def publications(revision: str = "p3-v1") -> tuple[CorpusPublication, ...]:
    return (
        CorpusPublication(
            UUID("57000000-0000-0000-0000-000000000002"),
            UUID("57000000-0000-0000-0000-000000000003"),
            UUID("57000000-0000-0000-0000-000000000004"),
            "document/session--"
            + hashlib.sha256(
                UUID("57000000-0000-0000-0000-000000000002").bytes
            ).hexdigest()[:24],
            revision,
        ),
    )


def write_corpus(root: Path, revision: str = "p3-v1") -> tuple[CorpusPublication, ...]:
    expected = publications(revision)
    publication = expected[0]
    folder = root / publication.path
    folder.mkdir(parents=True, exist_ok=True)
    record = asdict(publication)
    record["export_revision"] = record.pop("revision")
    record["files"] = []
    (folder / "document.json").write_text(json.dumps(record, default=str))
    (folder / "_index.md").write_text(f"State: ready\nExport revision: {revision}\n")
    return expected
