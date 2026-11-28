"""Replay one migrated, local D162 benchmark checkpoint without provider I/O.

The database must be an idle restore with the benchmark's original E0, E1 and
E2 rows. A private database clone holds all replay writes. Put the E0 objects
under --artifacts using their stored object keys. A wire tape is a JSON array
of records with stage, target_id, method, url, request (JSON or null), status,
and reply (JSON). Records are consumed in wire order within each work item.

--checkpoint-only uses persisted vectors and Selection responses when an
archive lacks its wire tape. It verifies E1 rows, Selection and the D122
producer order/stamps. It records Claimify request digests using empty
replies so they can be compared across revisions. Claimify output equality
requires the original replies and is only checked in strict mode. Strict
mode replays downstream claim vectors from the checkpoint through the P1
writer before comparing every claim column.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from datetime import UTC
import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID
from uuid import uuid4

import httpx
from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from rememberstack.adapters import OpenRouterModelProvider
from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters import PostgresP1Index
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core import chunker_version
from rememberstack.core import ChunkerParams
from rememberstack.core.embedding_input_policy import embedding_text_hash
from rememberstack.core.selection_references import claimify_input_hash
from rememberstack.model import ClaimedWork
from rememberstack.model import ClaimRecord
from rememberstack.model import P1ClaimRow
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS
from rememberstack.spine import ChunkCatalog
from rememberstack.spine import ClaimCatalog
from rememberstack.workers import ChunkHandler
from rememberstack.workers import e1
from rememberstack.workers import E1_CHUNK_VERSION
from rememberstack.workers import E1_EMBED_VERSION
from rememberstack.workers import E1Settings
from rememberstack.workers import e2
from rememberstack.workers import E2_EXTRACTOR_VERSION
from rememberstack.workers import E2Settings
from rememberstack.workers import EmbedChunksHandler
from rememberstack.workers import ExtractClaimsHandler
from rememberstack.workers.extraction_references import preceding_producer_ids

_TABLES = ("chunks", "chunk_search", "selection_results", "claims", "chunk_claims")
_CLOCK = dict(
    chunks="created_at",
    claims="ingested_at",
    selection_results="created_at",
    chunk_claims="created_at",
    claim_extraction_decisions="decided_at",
)
_MODEL = "qwen/qwen3-embedding-8b"


def _rows(engine: Engine, statement: str, **params: object) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [
            dict(row) for row in connection.execute(text(statement), params).mappings()
        ]


def _subset(engine: Engine, table: str, chunk_ids: list[UUID]) -> tuple[int, str]:
    # to_jsonb(t) includes every column. Order by the canonical JSON to preserve
    # multiplicity even where a table's logical key has more than one row.
    rows = _rows(
        engine,
        f"""
        SELECT to_jsonb(t)::text COLLATE "C" AS payload
        FROM {table} t WHERE chunk_id=ANY(:chunk_ids)
        ORDER BY payload
        """,
        chunk_ids=chunk_ids,
    )
    digest = hashlib.sha256()
    for row in rows:
        digest.update(row["payload"].encode())
        digest.update(b"\n")
    return len(rows), digest.hexdigest()


def _replay_claim_embeddings(
    *, engine: Engine, frozen_claims: list[dict[str, Any]]
) -> None:
    """Write frozen vectors onto replayed claims and recompute their P1 stamps."""
    by_model: dict[str, list[P1ClaimRow]] = defaultdict(list)
    for row in frozen_claims:
        if row["vector"] is None:
            continue
        by_model[row["embedding_model"]].append(
            P1ClaimRow(
                claim_id=row["claim_id"],
                deployment_id=row["deployment_id"],
                doc_id=row["doc_id"],
                chunk_id=row["chunk_id"],
                text=row["claim_text"],
                is_current_testimony=row["is_current_testimony"],
                is_attributed=row["is_attributed"],
                vector=tuple(json.loads(row["vector"], parse_int=float)),
            )
        )
    for model, rows in by_model.items():
        PostgresP1Index(engine=engine, embedding_model=model).upsert_claims(
            rows=tuple(rows)
        )


@contextmanager
def _clone(database_url: str) -> Iterator[tuple[Engine, Engine]]:
    url = make_url(database_url)
    if url.host not in {"127.0.0.1", "localhost", "::1"} or not url.database:
        raise ValueError("restore the checkpoint to a local PostgreSQL database")
    admin = create_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    baseline = create_engine(url, poolclass=NullPool)
    name = f"d162_replay_{uuid4().hex[:12]}"
    candidate = create_engine(url.set(database=name))
    try:
        quote = admin.dialect.identifier_preparer.quote
        with admin.connect() as connection:
            connection.exec_driver_sql(
                f"CREATE DATABASE {quote(name)} TEMPLATE {quote(url.database)}"
            )
        try:
            yield baseline, candidate
        finally:
            candidate.dispose()
            with admin.connect() as connection:
                connection.exec_driver_sql(f"DROP DATABASE {quote(name)}")
    finally:
        baseline.dispose()
        admin.dispose()


class Replies:
    """A network-free transport, strict against wire records when supplied."""

    def __init__(
        self,
        *,
        records: list[dict[str, Any]] | None,
        search: dict[UUID, dict[str, Any]],
        selections: dict[UUID, dict[str, Any]],
        model: str,
    ) -> None:
        self.tape: dict[tuple[str, str], deque[dict[str, Any]]] = defaultdict(deque)
        for entry in records or ():
            self.tape[(entry["stage"], entry["target_id"])].append(entry)
        self.strict = records is not None
        self.search = search
        self.selections = selections
        self.model = model
        self.stage = ""
        self.target = UUID(int=0)
        self.pending_vectors: deque[UUID] = deque()
        self.calls: dict[str, int] = defaultdict(int)
        self.request_digest = hashlib.sha256()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls[self.stage] += 1
        key = (self.stage, str(self.target))
        envelope = (key, request.method, str(request.url), body)
        self.request_digest.update(
            json.dumps(envelope, sort_keys=True, ensure_ascii=False).encode() + b"\n"
        )
        if self.strict:
            if not self.tape[key]:
                raise ValueError(f"wire tape exhausted for {key}")
            saved = self.tape[key].popleft()
            if (saved["method"], saved["url"], saved["request"]) != envelope[1:]:
                raise ValueError(f"wire request differs for {key}")
            return httpx.Response(
                saved["status"], json=saved["reply"], headers=saved.get("headers")
            )
        if not isinstance(body, dict):
            raise ValueError("embedding and extraction requests must be JSON objects")
        if self.stage == "embed_chunk":
            if (
                request.url.path != "/api/v1/embeddings"
                or body["model"] != self.model
                or body["dimensions"] != P1_VECTOR_DIMENSIONS
                or body.get("provider")
                != {"only": ["nebius"], "allow_fallbacks": False}
            ):
                raise ValueError("embedding routing or framing differs from Full-v45")
            data = []
            for index, value in enumerate(body["input"]):
                frozen = self.search[self.pending_vectors.popleft()]
                if embedding_text_hash(value) != frozen["embedding_text_hash"]:
                    raise ValueError(
                        "ordered embedding text differs from its frozen hash"
                    )
                data.append({"index": index, "embedding": json.loads(frozen["vector"])})
            return httpx.Response(
                200,
                json={
                    "model": self.model,
                    "data": data,
                    "usage": {"prompt_tokens": 0, "total_tokens": 0, "cost": 0},
                },
            )
        if self.stage in {"extract_claims", "ground_claims"}:
            output = (
                self.selections[self.target]["output"]["selection"]
                if self.stage == "extract_claims"
                else {"claims": []}
            )
            return httpx.Response(
                200,
                json={
                    "model": body["model"],
                    "provider": "OpenAI",
                    "choices": [
                        {
                            "message": {"content": json.dumps(output)},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0},
                },
            )
        raise ValueError(f"checkpoint contains no reply for {key}")


def _work(
    *,
    source: dict[str, Any],
    stage: PipelineStage,
    target: UUID,
    generation: str,
    is_chunk: bool = False,
) -> ClaimedWork:
    return ClaimedWork(
        processing_id=uuid4(),
        deployment_id=source["deployment_id"],
        target_kind=ProcessingTarget.CHUNK
        if is_chunk
        else ProcessingTarget.DOCUMENT_VERSION,
        target_id=target,
        stage=stage,
        component_version=generation,
        content_hash=source["content_hash"],
        lane=ProcessingLane.STEADY,
        attempt=1,
        payload={
            "representation_id": str(source["representation_id"]),
            "version_id": str(source["version_id"]),
            **({"chunk_id": str(target)} if is_chunk else {}),
        },
    )


def _json_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


def replay(
    args: argparse.Namespace, baseline: Engine, candidate: Engine
) -> dict[str, object]:
    params = ChunkerParams.model_validate_json(args.packing)
    generation = chunker_version(params=params)
    current = _rows(
        baseline,
        """
        SELECT DISTINCT r.representation_id, r.deployment_id, v.version_id, v.content_hash,
                        v.ingested_at, v.version_no
        FROM chunks c JOIN document_representations r USING (representation_id)
        JOIN document_versions v ON v.version_id=r.version_id
        WHERE c.chunker_version=:generation
          AND (CAST(:representations AS uuid[]) IS NULL OR r.representation_id=ANY(:representations))
        ORDER BY v.ingested_at, v.version_no, r.representation_id
    """,
        generation=generation,
        representations=args.representation,
    )
    if not current:
        raise ValueError(
            "checkpoint contains no target grids under the specified packing"
        )
    representations = [row["representation_id"] for row in current]
    old = _rows(
        baseline,
        """
        SELECT * FROM chunks WHERE representation_id=ANY(:representations)
          AND chunker_version=:generation ORDER BY ordinal, chunk_id
    """,
        representations=representations,
        generation=generation,
    )
    chunk_ids = [row["chunk_id"] for row in old]
    search = {
        row["chunk_id"]: row
        for row in _rows(
            baseline,
            """
            SELECT *,embedding::text AS vector FROM chunk_search WHERE chunk_id=ANY(:chunk_ids)
        """,
            chunk_ids=chunk_ids,
        )
    }
    selections = {
        row["chunk_id"]: row
        for row in _rows(
            baseline,
            """
            SELECT * FROM selection_results WHERE chunk_id=ANY(:chunk_ids)
              AND extractor_version=:extractor
        """,
            chunk_ids=chunk_ids,
            extractor=E2_EXTRACTOR_VERSION,
        )
    }
    claim_rows = _rows(
        baseline,
        """
        SELECT claim_id, deployment_id, doc_id, chunk_id, char_start, char_end,
               claim_text, is_current_testimony, is_attributed, embedding_model,
               embedding::text AS vector
        FROM claims WHERE chunk_id=ANY(:chunk_ids) ORDER BY claim_id
        """,
        chunk_ids=chunk_ids,
    )
    occurrences = _rows(
        baseline,
        "SELECT chunk_id,created_at FROM chunk_claims WHERE chunk_id=ANY(:chunk_ids)",
        chunk_ids=chunk_ids,
    )
    decisions = _rows(
        baseline,
        """
        SELECT chunk_id,decided_at FROM claim_extraction_decisions WHERE chunk_id=ANY(:chunk_ids)
        """,
        chunk_ids=chunk_ids,
    )
    exact_replies = json.loads(args.wire_tape.read_text()) if args.wire_tape else None
    replies = Replies(
        records=exact_replies, search=search, selections=selections, model=args.model
    )
    if args.routing == "locomo-v45":
        settings = OpenRouterSettings(
            api_key="offline",
            embedding_provider="nebius",
            zdr=False,
            chat_provider_only=["openai/flex", "openai"],
            chat_provider_order=["openai/flex", "openai"],
            reasoning_effort="low",
            max_completion_tokens=32_000,
        )
    else:
        settings = OpenRouterSettings(
            api_key="offline",
            base_url=args.base_url,
            embedding_provider=args.embedding_provider,
            zdr=False,
        )
    provider = OpenRouterModelProvider(settings=settings)
    if provider.uses_hosted_embedding_ceiling(model=args.model):
        raise ValueError("frozen benchmark routes bypass the hosted binding")

    clock = datetime.now(UTC)
    with candidate.begin() as connection:
        for table, column in _CLOCK.items():
            connection.exec_driver_sql(
                f"ALTER TABLE {table} ALTER COLUMN {column} "
                "SET DEFAULT current_setting('d162.clock')::timestamptz"
            )
        for table in (
            *_TABLES[:3],
            "chunk_claims",
            "claim_extraction_decisions",
            "claims",
        ):
            connection.execute(
                text(f"DELETE FROM {table} WHERE chunk_id=ANY(:chunk_ids)"),
                {"chunk_ids": chunk_ids},
            )
        work_predicate = """
            WHERE (target_id=ANY(:chunk_ids) OR payload->>'representation_id'=ANY(:reps))
              AND stage IN ('chunk','embed_chunk','extract_claims','ground_claims')
        """
        work_params = {
            "chunk_ids": chunk_ids,
            "reps": [str(rep) for rep in representations],
        }
        connection.execute(
            text(f"""
            DELETE FROM cost_ledger WHERE (deployment_id,processing_id) IN (
                SELECT deployment_id,processing_id FROM processing_state {work_predicate}
            )
        """),
            work_params,
        )
        connection.execute(
            text(f"DELETE FROM processing_state {work_predicate}"), work_params
        )

    @event.listens_for(candidate, "begin")
    def set_clock(connection: Any) -> None:
        # A SET command leaves the transaction's first snapshot to the worker.
        connection.exec_driver_sql(f"SET LOCAL d162.clock = '{clock.isoformat()}'")

    artifacts = LocalFSObjectStore(root=args.artifacts)
    chunks = ChunkCatalog(engine=candidate)
    claims = ClaimCatalog(engine=candidate)
    pack = ChunkHandler(params=params, artifact_store=artifacts, catalog=chunks)
    embed = EmbedChunksHandler(
        params=params,
        artifact_store=artifacts,
        catalog=chunks,
        model_provider=provider,
        chunk_index=PostgresP1Index(engine=candidate, embedding_model=args.model),
        settings=E1Settings(embed_batch_size=args.batch_size),
        embedding_model=args.model,
    )
    extract = ExtractClaimsHandler(
        artifact_store=artifacts,
        catalog=claims,
        chunk_catalog=chunks,
        model_provider=provider,
        settings=E2Settings(extract_model=args.extract_model),
        chunker_version=generation,
    )
    meter = NoopCostMeter()
    old_by_rep: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
    for row in old:
        old_by_rep[row["representation_id"]].append(row)
    expected_producers, replayed_producers = {}, {}
    occurrence_dates = {row["chunk_id"]: row["created_at"] for row in occurrences}
    decision_dates = {row["chunk_id"]: row["decided_at"] for row in decisions}
    claim_ids: dict[tuple[UUID, int, int, str], deque[UUID]] = defaultdict(deque)
    for row in claim_rows:
        claim_ids[
            (row["chunk_id"], row["char_start"], row["char_end"], row["claim_text"])
        ].append(row["claim_id"])
    original_ground = e2._grounded_claim

    def freeze_claim_id(**kwargs: Any) -> Any:
        result = original_ground(**kwargs)
        if not isinstance(result, ClaimRecord):
            return result
        ids = claim_ids[
            (result.chunk_id, result.char_start, result.char_end, result.claim_text)
        ]
        if not ids:
            raise ValueError(f"grounded claim differs on chunk {result.chunk_id}")
        return result.model_copy(update={"claim_id": ids.popleft()})

    with httpx.Client(
        base_url=settings.base_url, transport=httpx.MockTransport(replies)
    ) as client:
        with patch.object(provider, "_client", client):
            for source in current:
                representation = source["representation_id"]
                group = old_by_rep[representation]
                ordered_ids = [row["chunk_id"] for row in group]
                # PG creates the entire immutable grid within one writing transaction.
                clocks = {row["created_at"] for row in group}
                if len(clocks) != 1:
                    raise ValueError(
                        f"frozen grid has more than one creation clock: {representation}"
                    )
                clock = clocks.pop()
                with patch.object(e1, "uuid4", side_effect=ordered_ids):
                    pack.handle(
                        work=_work(
                            source=source,
                            stage=PipelineStage.CHUNK,
                            target=source["version_id"],
                            generation=E1_CHUNK_VERSION,
                        ),
                        meter=meter,
                    )
                if chunks.existing_chunk_ids(
                    representation_id=representation, chunker_version=generation
                ) != tuple(ordered_ids):
                    raise ValueError(f"grid order or IDs differ on {representation}")
                replies.stage, replies.target = "embed_chunk", source["version_id"]
                replies.pending_vectors = deque(
                    item for item in ordered_ids if item in search
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
                if not replies.strict and replies.pending_vectors:
                    raise ValueError(f"unused frozen vectors on {representation}")
                for chunk_id in ordered_ids:
                    clock = selections[chunk_id]["created_at"]
                    replies.stage, replies.target = "extract_claims", chunk_id
                    extract.handle(
                        work=_work(
                            source=source,
                            stage=PipelineStage.EXTRACT_CLAIMS,
                            target=chunk_id,
                            generation=E2_EXTRACTOR_VERSION,
                            is_chunk=True,
                        ),
                        meter=meter,
                    )
                for index, row in enumerate(group):
                    chunk_id = row["chunk_id"]
                    window = chunks.chunks_for_references(
                        representation_id=representation,
                        chunker_version=generation,
                        chunk_id=chunk_id,
                    )
                    target = next(
                        chunk for chunk in window if chunk.chunk_id == chunk_id
                    )
                    actual = preceding_producer_ids(chunks=window, target=target)
                    expected_producers[chunk_id] = ordered_ids[
                        max(0, index - 8) : index
                    ]
                    replayed_producers[chunk_id] = list(actual)
                    loaded = claims.selections.preceding(
                        chunk_ids=(chunk_id, *actual),
                        extractor_version=E2_EXTRACTOR_VERSION,
                    )
                    stamp = claimify_input_hash(
                        target_selection_input_hash=loaded[chunk_id].input_hash,
                        preceding_selection_input_hashes=tuple(
                            loaded[item].input_hash for item in actual
                        ),
                    )
                    if stamp != row["claimify_input_hash"]:
                        raise ValueError(f"D122 stamp differs on {chunk_id}")
                    clock = occurrence_dates.get(
                        chunk_id, decision_dates.get(chunk_id, clock)
                    )
                    replies.stage, replies.target = "ground_claims", chunk_id
                    with patch.object(
                        e2, "_grounded_claim", side_effect=freeze_claim_id
                    ):
                        extract.handle(
                            work=_work(
                                source=source,
                                stage=PipelineStage.GROUND_CLAIMS,
                                target=chunk_id,
                                generation=E2_EXTRACTOR_VERSION,
                                is_chunk=True,
                            ),
                            meter=meter,
                        )

    if any(replies.tape.values()) or (replies.strict and any(claim_ids.values())):
        raise ValueError("unused frozen requests or claims")
    if replayed_producers != expected_producers:
        raise ValueError("D122 producer order differs")
    splits = _rows(
        candidate, "SELECT count(*) AS n FROM chunks WHERE span_kind='split'"
    )[0]["n"]
    if splits:
        raise ValueError(f"expected zero splits, observed {splits}")
    if replies.strict:
        _replay_claim_embeddings(engine=candidate, frozen_claims=claim_rows)

    table_results = {}
    for table in _TABLES:
        before = _subset(baseline, table, chunk_ids)
        checked = replies.strict or table not in {"claims", "chunk_claims"}
        after = _subset(candidate, table, chunk_ids) if checked else None
        if checked and before != after:
            raise ValueError(f"{table} differs: frozen {before}, replayed {after}")
        table_results[table] = {
            "rows": before[0],
            "sha256": before[1],
            "equal": before == after if checked else None,
        }
    return {
        "grids": len(current),
        "splits": splits,
        "live_calls": 0,
        "tables": table_results,
        "producer_order_sha256": _json_digest(
            {str(k): v for k, v in expected_producers.items()}
        ),
        "wire_verified": replies.strict,
        "request_sha256": replies.request_digest.hexdigest(),
        "offline_calls": dict(replies.calls),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--representation", type=UUID, action="append")
    parser.add_argument("--model", default=_MODEL)
    parser.add_argument("--extract-model", default="openai/gpt-6-luna")
    parser.add_argument("--packing", default="{}")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument(
        "--routing", choices=("locomo-v45", "custom"), default="locomo-v45"
    )
    parser.add_argument("--base-url", default="https://openrouter.ai/api/v1")
    parser.add_argument("--embedding-provider")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--wire-tape", type=Path)
    mode.add_argument("--checkpoint-only", action="store_true")
    args = parser.parse_args()
    if args.checkpoint_only and args.routing != "locomo-v45":
        parser.error("--checkpoint-only requires the Full-v45 binding")
    with _clone(args.database_url) as (baseline, candidate):
        print(json.dumps(replay(args, baseline, candidate), sort_keys=True))


if __name__ == "__main__":
    main()
