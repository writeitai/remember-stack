"""Undated-corpus comparison: does D140 slow a deployment that never uses it?

A LoCoMo-style corpus — conversation transcripts, no effective periods
anywhere, many facts — measured with the same code path on ``origin/main``
and on the D140 branch (same data, same machine, each against a database
migrated by its own checkout). The concern is the per-candidate fact
evidence gate and the time-scope probe, which run even when no lineage is
periodised.

It uses only APIs both trees have (``PostgresP1Index`` upserts and
``QueryEngine.search_chunks`` / ``claims_and_sources_context`` /
``facts_context``) and SQL against tables both schemas share, so the identical
script runs in either checkout:

    REMEMBERSTACK_DATABASE_URL=... uv run python -m benchmarks.d140_scope.undated \\
        --conversations 1000 --turns 60 --out undated_main.json

Query embeddings come from a deterministic fake provider; no model is called.
Synthetic data and timings only; nothing here is a correctness test.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime
from datetime import UTC
import json
import random
import statistics
import subprocess
import time
from types import SimpleNamespace
from typing import Any
from uuid import UUID
from uuid import uuid5

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters import BoundedPostgresReadPool
from rememberstack.adapters import PostgresP1Index
from rememberstack.core.embedding_input_policy import EMBEDDING_INPUT_POLICY_VERSION
from rememberstack.core.embedding_input_policy import embedding_text_hash
from rememberstack.model import ClaimValidPrecision
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import P1ChunkRow
from rememberstack.model import P1ClaimRow
from rememberstack.model import P1FactRow
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import QueryEngine

DEPLOYMENT = UUID("d1400000-0000-4000-8000-0000000000da")
MODEL = "d140-undated-model"
DIMENSIONS = 1_536
VOCABULARY = 8_000
AT = datetime(2025, 3, 1, tzinfo=UTC)


def _id(*parts: object) -> UUID:
    return uuid5(DEPLOYMENT, "/".join(str(part) for part in parts))


def _vector(rng: random.Random) -> tuple[float, ...]:
    values = [rng.gauss(0, 1) for _ in range(DIMENSIONS)]
    norm = sum(value * value for value in values) ** 0.5
    return tuple(value / norm for value in values)


def _sentence(rng: random.Random, words: int) -> str:
    return " ".join(f"w{int((rng.random() ** 3) * VOCABULARY)}" for _ in range(words))


_INSERT_CHUNK = text(
    "INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,"
    " representation_id, ordinal, block_start, block_end, chunk_content_hash,"
    " extraction_input_hash, char_start, char_end, created_at) VALUES (:c, :d,"
    " :doc, :v, :r, :n, :n, :n, :c, :c, :s, :e, now())"
)
_INSERT_CLAIM = text(
    "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id, claim_text,"
    " source_span, char_start, char_end, anchor_ok, window_membership_ok,"
    " is_current_testimony, extractor_version, ingested_at, asserted_at) VALUES"
    " (:claim, :d, :doc, :c, :t, :t, 0, 10, true, true, true, 'undated', :at, :at)"
)
_INSERT_OCCURRENCE = text(
    "INSERT INTO chunk_claims (deployment_id, chunk_id, claim_id, evidence_spans)"
    ' VALUES (:d, :c, :claim, \'[{"char_start": 0, "char_end": 10}]\'::jsonb)'
)


def seed(
    *,
    engine: Engine,
    index: PostgresP1Index,
    conversations: int,
    turns: int,
    claims_per_turn: int,
    entities: int,
    facts_per_conversation: int,
) -> dict[str, float]:
    """Load the corpus; returns phase timings in seconds."""
    started = time.monotonic()
    rng = random.Random(140)
    DeploymentBootstrapper(engine=engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=DEPLOYMENT,
            slug="d140-undated",
            name="D140 undated corpus",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    index.configure_channels(deployment_id=DEPLOYMENT)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id, canonical_name,"
                " normalized_name) SELECT e, :d, 'Person ' || n, 'person ' || n"
                " FROM unnest(CAST(:ids AS uuid[])) WITH ORDINALITY AS t(e, n)"
            ),
            {"d": DEPLOYMENT, "ids": [str(_id("entity", n)) for n in range(entities)]},
        )
    for conversation in range(conversations):
        doc, version, representation = (
            _id("doc", conversation),
            _id("version", conversation),
            _id("representation", conversation),
        )
        chunk_rows: list[P1ChunkRow] = []
        claim_rows: list[P1ClaimRow] = []
        claims: list[tuple[UUID, UUID]] = []
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                    " source_ref, title) VALUES (:doc, :d, 'conversation', :ref, :t)"
                ),
                {
                    "doc": doc,
                    "d": DEPLOYMENT,
                    "ref": f"chat/{conversation}",
                    "t": f"Session {conversation}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO content_objects (deployment_id, content_hash, mime,"
                    " raw_uri) VALUES (:d, :h, 'text/markdown', 'mem://raw/' || :h)"
                ),
                {"d": DEPLOYMENT, "h": f"undated-{conversation}"},
            )
            connection.execute(
                text(
                    "INSERT INTO document_versions (version_id, deployment_id, doc_id,"
                    " content_hash, version_no, status, source_modified_at) VALUES"
                    " (:v, :d, :doc, :h, 1, 'ready', :at)"
                ),
                {
                    "v": version,
                    "d": DEPLOYMENT,
                    "doc": doc,
                    "h": f"undated-{conversation}",
                    "at": AT,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO document_representations (representation_id,"
                    " deployment_id, version_id, route, status) VALUES"
                    " (:r, :d, :v, 'passthrough', 'ready')"
                ),
                {"r": representation, "d": DEPLOYMENT, "v": version},
            )
            connection.execute(
                text(
                    "UPDATE document_versions SET current_representation_id = :r"
                    " WHERE version_id = :v"
                ),
                {"r": representation, "v": version},
            )
            connection.execute(
                text(
                    "UPDATE documents SET current_version_id = :v WHERE doc_id = :doc"
                ),
                {"v": version, "doc": doc},
            )
            chunk_params: list[dict[str, object]] = []
            claim_params: list[dict[str, object]] = []
            for turn in range(turns):
                chunk = _id("chunk", conversation, turn)
                body = _sentence(rng, 30)
                chunk_params.append(
                    {
                        "c": chunk,
                        "d": DEPLOYMENT,
                        "doc": doc,
                        "v": version,
                        "r": representation,
                        "n": turn,
                        "s": turn * 200,
                        "e": turn * 200 + len(body),
                    }
                )
                chunk_rows.append(
                    P1ChunkRow(
                        chunk_id=chunk,
                        deployment_id=DEPLOYMENT,
                        doc_id=doc,
                        version_id=version,
                        section_role="body",
                        text=body,
                        vector=_vector(rng),
                        policy_generation=EMBEDDING_INPUT_POLICY_VERSION,
                        embedder_generation=MODEL,
                        embedding_text_hash=embedding_text_hash(body),
                        source_kind="conversation",
                        source_shape="conversation",
                    )
                )
                for ordinal in range(claims_per_turn):
                    claim = _id("claim", conversation, turn, ordinal)
                    claim_text = _sentence(rng, 10)
                    claim_params.append(
                        {
                            "claim": claim,
                            "d": DEPLOYMENT,
                            "doc": doc,
                            "c": chunk,
                            "t": claim_text,
                            "at": AT,
                        }
                    )
                    claims.append((claim, chunk))
                    claim_rows.append(
                        P1ClaimRow(
                            claim_id=claim,
                            deployment_id=DEPLOYMENT,
                            doc_id=doc,
                            chunk_id=chunk,
                            text=claim_text,
                            is_current_testimony=True,
                            is_attributed=False,
                            vector=_vector(rng),
                        )
                    )
            connection.execute(_INSERT_CHUNK, chunk_params)
            connection.execute(_INSERT_CLAIM, claim_params)
            connection.execute(_INSERT_OCCURRENCE, claim_params)
            fact_rows = _seed_facts(
                connection=connection,
                rng=rng,
                doc=doc,
                conversation=conversation,
                claims=claims,
                entities=entities,
                count=facts_per_conversation,
            )
        index.upsert_chunks(rows=tuple(chunk_rows))
        index.upsert_claims(rows=tuple(claim_rows))
        index.upsert_facts(rows=fact_rows)
        if conversation % 50 == 0:
            print(f"seeded conversation {conversation}", flush=True)
    with engine.begin() as connection:
        for table in ("chunks", "chunk_search", "claims", "relations", "observations"):
            connection.execute(text(f"ANALYZE {table}"))
    return {"load_seconds": time.monotonic() - started}


def _seed_facts(
    *,
    connection: Any,
    rng: random.Random,
    doc: UUID,
    conversation: int,
    claims: list[tuple[UUID, UUID]],
    entities: int,
    count: int,
) -> tuple[P1FactRow, ...]:
    """Relations (four in five) and observations, each supported by 1–3 claims."""
    rows: list[P1FactRow] = []
    for ordinal in range(count):
        fact = _id("fact", conversation, ordinal)
        subject = _id("entity", rng.randrange(entities))
        support = rng.sample(claims, k=min(len(claims), rng.randint(1, 3)))
        label = _sentence(rng, 8)
        if ordinal % 5:
            obj = _id("entity", rng.randrange(entities))
            connection.execute(
                text(
                    "INSERT INTO relations (relation_id, deployment_id,"
                    " subject_entity_id, predicate, object_entity_id,"
                    " normalizer_version, fact_label, ingested_at, valid_from,"
                    " valid_precision, window_claim_ids) VALUES (:f, :d, :s,"
                    " 'works_for', :o, 'undated', :label, :at, :at, 'open', :w)"
                ),
                {
                    "f": fact,
                    "d": DEPLOYMENT,
                    "s": subject,
                    "o": obj,
                    "label": label,
                    "at": AT,
                    "w": [support[0][0]],
                },
            )
            table, column, kind = "relation_evidence", "relation_id", "relation"
        else:
            connection.execute(
                text(
                    "INSERT INTO observations (observation_id, deployment_id,"
                    " subject_entity_id, statement, normalizer_version, ingested_at)"
                    " VALUES (:f, :d, :s, :label, 'undated', :at)"
                ),
                {"f": fact, "d": DEPLOYMENT, "s": subject, "label": label, "at": AT},
            )
            table, column, kind = (
                "observation_evidence",
                "observation_id",
                "observation",
            )
        for claim, _chunk in support:
            connection.execute(
                text(
                    f"INSERT INTO {table} (deployment_id, {column}, claim_id, doc_id,"  # noqa: S608
                    " stance, normalizer_version) VALUES (:d, :f, :c, :doc,"
                    " 'supports', 'undated')"
                ),
                {"d": DEPLOYMENT, "f": fact, "c": claim, "doc": doc},
            )
        rows.append(
            P1FactRow(
                fact_id=fact,
                deployment_id=DEPLOYMENT,
                kind=kind,
                label=label,
                status="active",
                valid_from=AT if kind == "relation" else None,
                valid_until=None,
                valid_precision=(
                    ClaimValidPrecision.OPEN
                    if kind == "relation"
                    else ClaimValidPrecision.UNKNOWN
                ),
                ingested_at=AT,
                invalidated_at=None,
                vector=_vector(rng),
            )
        )
    return tuple(rows)


class _Provider:
    """Deterministic query embeddings; never calls a model."""

    def __init__(self) -> None:
        self._rng = random.Random(7)

    def embed(self, *args: object, **kwargs: object) -> SimpleNamespace:
        del args, kwargs
        return SimpleNamespace(vectors=(_vector(self._rng),), usage=None)


def _percentiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "p50_ms": round(statistics.median(ordered) * 1000, 2),
        "p95_ms": round(ordered[int(0.95 * (len(ordered) - 1))] * 1000, 2),
        "max_ms": round(ordered[-1] * 1000, 2),
    }


def measure(*, engine: Engine, queries: int, repeats: int) -> dict[str, Any]:
    """The three read paths, each over the same queries after a warm-up."""
    rng = random.Random(1140)
    terms = [_sentence(rng, 2) for _ in range(queries)]
    pool = BoundedPostgresReadPool(
        engine=engine, max_concurrency=4, pool_wait_seconds=30
    )
    index = PostgresP1Index(engine=engine, embedding_model=MODEL, read_pool=pool)
    query = QueryEngine(
        engine=engine,
        search_index=index,
        model_provider=_Provider(),  # type: ignore[arg-type]
        embedding_model=MODEL,
        fact_read_pool=pool,
    )
    paths: dict[str, Callable[[str], object]] = {
        "search_chunks_bm25": lambda q: query.search_chunks(
            deployment_id=DEPLOYMENT, query=q, k=20, channel="bm25"
        ),
        "search_chunks_semantic": lambda q: query.search_chunks(
            deployment_id=DEPLOYMENT, query=q, k=20, channel="semantic"
        ),
        "claims_and_sources_context": lambda q: query.claims_and_sources_context(
            deployment_id=DEPLOYMENT, query=q
        ),
        "facts_context": lambda q: query.facts_context(
            deployment_id=DEPLOYMENT, query=q
        ),
    }
    results: dict[str, Any] = {}
    for name, call in paths.items():
        for term in terms[: max(queries // 10, 5)]:
            call(term)  # warm-up
        samples: list[float] = []
        for _ in range(repeats):
            for term in terms:
                started = time.perf_counter()
                call(term)
                samples.append(time.perf_counter() - started)
        results[name] = _percentiles(samples)
        print(name, results[name], flush=True)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--conversations", type=int, default=200)
    parser.add_argument("--turns", type=int, default=60)
    parser.add_argument("--claims-per-turn", type=int, default=3)
    parser.add_argument("--entities", type=int, default=2_000)
    parser.add_argument("--facts-per-conversation", type=int, default=40)
    parser.add_argument("--queries", type=int, default=200)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--skip-seed", action="store_true")
    parser.add_argument("--out", default="d140_undated.json")
    args = parser.parse_args()

    engine = create_engine(
        load_database_settings().database_url.get_secret_value(), pool_size=6
    )
    report: dict[str, Any] = {"arguments": vars(args)}
    report["git"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    if not args.skip_seed:
        report["seed"] = seed(
            engine=engine,
            index=PostgresP1Index(engine=engine, embedding_model=MODEL),
            conversations=args.conversations,
            turns=args.turns,
            claims_per_turn=args.claims_per_turn,
            entities=args.entities,
            facts_per_conversation=args.facts_per_conversation,
        )
    with engine.connect() as connection:
        report["corpus"] = {
            name: connection.execute(text(f"SELECT count(*) FROM {name}")).scalar_one()  # noqa: S608
            for name in ("documents", "chunks", "claims", "relations", "observations")
        }
    report["latency"] = measure(
        engine=engine, queries=args.queries, repeats=args.repeats
    )
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
