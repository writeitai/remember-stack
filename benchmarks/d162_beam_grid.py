"""Run one D162 revision on the frozen BEAM 100K/1 E0 inputs, offline.

Restore the August lab dump separately from a blank, migrated database.
The checker copies E0 into a private clone of the latter. Compare its JSON
reports from the pre-D162 and candidate workers to check every E1 and Selection
column and the ordered wire requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from benchmarks.d162_replay import _clone
from benchmarks.d162_replay import _rows
from benchmarks.d162_replay import _subset
from benchmarks.d162_replay import _work
from benchmarks.d162_replay import Replies
from rememberstack.adapters import OpenRouterModelProvider
from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters import PostgresP1Index
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core import blockize
from rememberstack.core import BLOCKIZER_VERSION
from rememberstack.core import chunker_version
from rememberstack.core import ChunkerParams
from rememberstack.core.storage_routing import storage_class_for_derived
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS
from rememberstack.spine import ChunkCatalog
from rememberstack.spine import ClaimCatalog
from rememberstack.workers import ChunkHandler
from rememberstack.workers import e1
from rememberstack.workers import E1_CHUNK_VERSION
from rememberstack.workers import E1_EMBED_VERSION
from rememberstack.workers import E1Settings
from rememberstack.workers import E2_EXTRACTOR_VERSION
from rememberstack.workers import E2Settings
from rememberstack.workers import EmbedChunksHandler
from rememberstack.workers import ExtractClaimsHandler

_HISTORICAL_FIELDS = (
    "section_id",
    "ordinal",
    "block_start",
    "block_end",
    "char_start",
    "char_end",
    "chunk_content_hash",
    "token_count",
    "location_facts_json",
    "location_header",
    "context_prefix",
    "embedding_text_hash",
    "embedding_input_policy_version",
    "policy_generation",
    "prefixer_version",
)
_E0_TABLES = (
    "deployments",
    "content_objects",
    "documents",
    "document_versions",
    "document_representations",
    "document_skeleton_checks",
    "document_structure_generations",
    "document_sections",
)
_CURRENT_LINKS = {
    "documents": ("doc_id", "current_version_id"),
    "document_versions": ("version_id", "current_representation_id"),
    "document_representations": (
        "representation_id",
        "current_structure_generation_id",
    ),
}
_MODEL = "qwen/qwen3-embedding-8b"
_EMBEDDING_ORDER = ["nebius", "deepinfra", "siliconflow"]
_BATCH = 64


def _copy_e0(*, archive: Engine, candidate: Engine, manifest_hash: str) -> None:
    """Carry E0 rows forward with their original IDs, topology and timestamps."""
    pointers = {}
    quote = candidate.dialect.identifier_preparer.quote
    with candidate.begin() as connection:
        for table in _E0_TABLES:
            rows = [
                json.loads(row["payload"])
                for row in _rows(
                    archive, f"SELECT to_jsonb(t)::text AS payload FROM {table} t"
                )
            ]
            if table in _CURRENT_LINKS:
                pk, link = _CURRENT_LINKS[table]
                (row,) = rows
                pointers[table] = {"id": row[pk], "value": row.pop(link)}
            if (
                table == "documents"
                and rows[0].pop("deleted_sync_cycle_id") is not None
            ):
                raise ValueError("the frozen BEAM document must be active")
            if table == "document_representations":
                rows[0]["manifest_hash"] = manifest_hash
            columns = ", ".join(quote(column) for column in rows[0])
            connection.execute(
                text(f"""
                    INSERT INTO {table} ({columns})
                    SELECT {columns} FROM jsonb_populate_recordset(
                        NULL::{table}, CAST(:rows AS jsonb)
                    )
                """),
                {"rows": json.dumps(rows)},
            )
        # Restore the three cyclic foreign keys after all rows exist.
        for table, (pk, link) in _CURRENT_LINKS.items():
            connection.execute(
                text(f"UPDATE {table} SET {link}=:value WHERE {pk}=:id"),
                pointers[table],
            )


def _artifacts(
    *, source: dict[str, Any], markdown: bytes, store: LocalFSObjectStore
) -> dict[str, str]:
    """Rebuild passthrough objects and verify the archive's content pins."""
    digest = hashlib.sha256(markdown).hexdigest()
    if digest != source["content_hash"] or digest != source["markdown_hash"]:
        raise ValueError("Markdown does not match the frozen BEAM representation")
    if (
        source["converter_name"] != "passthrough"
        or source["blockizer_version"] != BLOCKIZER_VERSION
    ):
        raise ValueError("unexpected BEAM converter or blockizer")
    document = markdown.decode()
    blocks = blockize(document_md=document)
    manifest = {
        "route": "passthrough",
        "converter": {"name": "passthrough", "version": source["converter_version"]},
        "blockizer_version": BLOCKIZER_VERSION,
        "execution": "library-local",
        "markdown_sha256": digest,
        "source_map": None,
        "derived_assets": [],
        "warnings": [],
    }
    original = json.dumps(manifest, indent=2, sort_keys=True).encode()
    if hashlib.sha256(original).hexdigest() != source["manifest_hash"]:
        raise ValueError("reconstructed conversion manifest differs from the archive")
    # The August format predates derivation labels. Keep its unknown provenance
    # using the empty range list required by the current manifest schema.
    conversion = json.dumps(
        {**manifest, "derivation_ranges": []}, indent=2, sort_keys=True
    ).encode()
    sidecar = json.dumps(
        {
            "blockizer_version": BLOCKIZER_VERSION,
            "block_count": len(blocks),
            "markdown_chars": len(document),
            "blocks": [
                block.model_dump(mode="json", exclude_none=True) for block in blocks
            ],
        },
        indent=2,
        sort_keys=True,
    ).encode()
    payloads = {
        "markdown_uri": markdown,
        "blocks_uri": sidecar,
        "conversion_uri": conversion,
    }
    for field, payload in payloads.items():
        store.write_bytes(
            key=ObjectKey(source[field]),
            content=payload,
            storage_class=storage_class_for_derived(),
        )
    return {
        "markdown_sha256": digest,
        "blocks_sha256": hashlib.sha256(sidecar).hexdigest(),
        "archived_conversion_sha256": source["manifest_hash"],
        "replay_conversion_sha256": hashlib.sha256(conversion).hexdigest(),
    }


def _replies(frozen: list[dict[str, Any]], markdown: str) -> Replies:
    search, selections = {}, {}
    previous_section = None
    for row in frozen:
        chunk_id = row["chunk_id"]
        search[chunk_id] = {
            "embedding_text_hash": row["embedding_text_hash"],
            "vector": json.dumps(
                [float(row["ordinal"] + 1)] + [0.0] * (P1_VECTOR_DIMENSIONS - 1)
            ),
        }
        # Fix replies before dispatch. Each chunk gets a verbatim keep for
        # Claimify, and each new section run introduces a card.
        span = markdown[row["char_start"] : row["char_end"]].strip().splitlines()[0]
        references = []
        if row["section_id"] != previous_section:
            references = [{"name": span[:80], "source_refs": ["S1"]}]
        selections[chunk_id] = {
            "output": {
                "selection": {
                    "candidates": [{"source_span": span, "outcome": "keep"}],
                    "references": references,
                }
            }
        }
        previous_section = row["section_id"]
    replies = Replies(
        records=None,
        search=search,
        selections=selections,
        model=_MODEL,
        embedding_route={"order": _EMBEDDING_ORDER, "allow_fallbacks": True},
    )
    replies.pending_vectors.extend(row["chunk_id"] for row in frozen)
    return replies


def check(
    *, archive: Engine, candidate: Engine, markdown: bytes, store: LocalFSObjectStore
) -> dict[str, object]:
    (source,) = _rows(
        archive,
        """
        SELECT r.*, v.content_hash
        FROM document_representations r JOIN document_versions v USING (version_id)
        """,
    )
    frozen = _rows(archive, "SELECT * FROM chunks ORDER BY ordinal")
    if len(frozen) != 749:
        raise ValueError(f"expected 749 frozen BEAM chunks, found {len(frozen)}")
    digests = _artifacts(source=source, markdown=markdown, store=store)
    _copy_e0(
        archive=archive,
        candidate=candidate,
        manifest_hash=digests["replay_conversion_sha256"],
    )
    (clock,) = {row["created_at"] for row in frozen}
    with candidate.begin() as connection:
        for table in ("chunks", "selection_results"):
            connection.exec_driver_sql(
                f"ALTER TABLE {table} ALTER COLUMN created_at "
                f"SET DEFAULT '{clock.isoformat()}'::timestamptz"
            )

    replies = _replies(frozen, markdown.decode())
    settings = OpenRouterSettings(
        api_key="offline",
        base_url="https://openrouter.ai/api/v1",
        embedding_provider=None,
        embedding_provider_only=None,
        embedding_provider_order=_EMBEDDING_ORDER,
        zdr=False,
        chat_provider_only=None,
        chat_provider_order=[
            "baseten",
            "siliconflow",
            "deepinfra",
            "fireworks",
            "together",
        ],
        reasoning_effort="none",
        reasoning_effort_map=None,
        max_completion_tokens=65_536,
    )
    provider = OpenRouterModelProvider(settings=settings)
    qualifies = getattr(provider, "uses_hosted_embedding_ceiling", None)
    if qualifies is not None and qualifies(model=_MODEL):
        raise ValueError("the BEAM lab binding must bypass the hosted ceiling")
    params = ChunkerParams()
    generation = chunker_version(params=params)
    catalog = ChunkCatalog(engine=candidate)
    pack = ChunkHandler(catalog=catalog, artifact_store=store, params=params)
    embed = EmbedChunksHandler(
        catalog=catalog,
        artifact_store=store,
        params=params,
        model_provider=provider,
        chunk_index=PostgresP1Index(engine=candidate, embedding_model=_MODEL),
        settings=E1Settings(embed_batch_size=_BATCH),
        embedding_model=_MODEL,
    )
    extract = ExtractClaimsHandler(
        catalog=ClaimCatalog(engine=candidate),
        chunk_catalog=catalog,
        artifact_store=store,
        model_provider=provider,
        settings=E2Settings(extract_model="openai/gpt-5.6-luna"),
        chunker_version=generation,
    )
    ids = [row["chunk_id"] for row in frozen]
    meter = NoopCostMeter()
    provider._client.close()
    with httpx.Client(
        base_url=settings.base_url, transport=httpx.MockTransport(replies)
    ) as client:
        provider._client = client
        with patch.object(e1, "uuid4", side_effect=ids):
            pack.handle(
                work=_work(
                    source=source,
                    stage=PipelineStage.CHUNK,
                    target=source["version_id"],
                    generation=E1_CHUNK_VERSION,
                ),
                meter=meter,
            )
        replies.stage, replies.target = (
            PipelineStage.EMBED_CHUNK.value,
            source["version_id"],
        )
        embed.handle(
            work=_work(
                source=source,
                stage=PipelineStage.EMBED_CHUNK,
                target=source["version_id"],
                generation=E1_EMBED_VERSION,
            ),
            meter=meter,
        )
        for stage in (PipelineStage.EXTRACT_CLAIMS, PipelineStage.GROUND_CLAIMS):
            for chunk_id in ids:
                replies.stage, replies.target = stage.value, chunk_id
                extract.handle(
                    work=_work(
                        source=source,
                        stage=stage,
                        target=chunk_id,
                        generation=E2_EXTRACTOR_VERSION,
                        is_chunk=True,
                    ),
                    meter=meter,
                )

    expected_calls = {
        "embed_chunk": (len(ids) + _BATCH - 1) // _BATCH,
        "extract_claims": len(ids),
        "ground_claims": len(ids),
    }
    if replies.calls != expected_calls or replies.pending_vectors:
        raise ValueError(f"incomplete offline BEAM replay: {dict(replies.calls)}")
    current = _rows(candidate, "SELECT * FROM chunks ORDER BY ordinal")
    splits = sum(row["span_kind"] == "split" for row in current)
    if splits:
        raise ValueError(f"expected zero BEAM splits, found {splits}")
    for row, old in zip(current, frozen, strict=True):
        changed = [
            name for name in ("chunk_id", *_HISTORICAL_FIELDS) if row[name] != old[name]
        ]
        if changed:
            raise ValueError(
                f"BEAM grid or D80 changed at ordinal {row['ordinal']}: {changed}"
            )
    tables = {}
    for table in ("chunks", "chunk_search", "selection_results"):
        count, digest = _subset(candidate, table, ids)
        if count != len(ids):
            raise ValueError(f"{table} has {count} of {len(ids)} BEAM rows")
        tables[table] = {"rows": count, "sha256": digest}
    return {
        **digests,
        "splits": splits,
        "live_calls": 0,
        "offline_calls": dict(replies.calls),
        "request_sha256": replies.request_digest.hexdigest(),
        "stage_request_sha256": {
            stage: digest.hexdigest()
            for stage, digest in replies.stage_request_digests.items()
        },
        "tables": tables,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url", required=True, help="restored August BEAM dump"
    )
    parser.add_argument(
        "--replay-database-url", required=True, help="empty, migrated local database"
    )
    parser.add_argument("--markdown", required=True, type=Path)
    args = parser.parse_args()
    archive = create_engine(args.database_url)
    try:
        with (
            TemporaryDirectory() as folder,
            _clone(args.replay_database_url) as (_, candidate),
        ):
            report = check(
                archive=archive,
                candidate=candidate,
                markdown=args.markdown.read_bytes(),
                store=LocalFSObjectStore(root=Path(folder) / "artifacts"),
            )
        print(json.dumps(report, sort_keys=True))
    finally:
        archive.dispose()


if __name__ == "__main__":
    main()
