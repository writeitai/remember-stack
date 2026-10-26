"""Seed one unconfigured deployment and capture its assured-operation bytes (D143 §9.1).

The same code runs on the pre-D143 base and on the current branch, so the
checked-in ``fixtures/d143_default_operations.json`` (captured on the base,
0d05b97e6) and the branch's output can be compared byte for byte by
``test_d143_default_operations_pg.py``.

The seed is the D143 retrieval rig's live lineage (one chunk, one current
claim, both published to P1) plus two resolvable entities mentioned on that
chunk and one fact between them. Every identifier the seed chooses is a
constant and every timestamp it writes is a fixed instant; the operations run
through ``OperationExecutor`` at a fixed ``evaluated_at`` and through
``POST /operations/{name}``. What the code under test stamps with the wall
clock or a fresh ``uuid4`` is normalised (:func:`normalise`) — wall-clock
instants become ``"<wallclock>"`` and unseeded UUIDs are numbered in order of
first appearance — so the structure, every seeded value and every computed
value still compare exactly.

Run as a script inside a worktree (``PYTHONPATH=src``, with
``REMEMBERSTACK_DATABASE_URL``) to reset the database, migrate it to that
worktree's head, seed, and print the capture as JSON.
"""

from __future__ import annotations

from datetime import datetime
from datetime import UTC
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

DEPLOYMENT_ID = UUID("d1430000-0000-0000-0000-0000000def02")
DOC_ID = UUID("d1430000-0000-0000-0000-0000000d0c01")
CHUNK_ID = UUID("d1430000-0000-0000-0000-0000000c4c01")
CLAIM_ID = UUID("d1430000-0000-0000-0000-0000000c1a01")
ALICE_ID = UUID("d1430000-0000-0000-0000-0000000a1ce1")
ACME_ID = UUID("d1430000-0000-0000-0000-0000000ac3e1")
RELATION_ID = UUID("d1430000-0000-0000-0000-0000000fac71")
ALIAS_IDS = {
    ALICE_ID: UUID("d1430000-0000-0000-0000-0000000a1a51"),
    ACME_ID: UUID("d1430000-0000-0000-0000-0000000a1a52"),
}
SEEDED_AT = datetime(2025, 1, 1, tzinfo=UTC)
EVALUATED_AT = datetime(2025, 6, 1, 12, tzinfo=UTC)
_MODEL = "qwen/qwen3-embedding-8b"
_BODY = "Alice works for Acme; the harbour tariff is forty crowns"
_FIXED_INSTANTS = {SEEDED_AT, EVALUATED_AT}
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_INSTANT = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)
_SEEDED_UUIDS = frozenset(
    str(value)
    for value in (
        DEPLOYMENT_ID,
        DOC_ID,
        CHUNK_ID,
        CLAIM_ID,
        ALICE_ID,
        ACME_ID,
        RELATION_ID,
        *ALIAS_IDS.values(),
    )
)
_OPERATIONS: tuple[tuple[str, dict[str, object]], ...] = (
    ("resolve_entity", {"name": "Alice"}),
    ("claims_and_sources_context", {"query": "harbour tariff"}),
    ("facts_context", {"query": "Alice works for Acme"}),
    ("combined_context", {"query": "Alice works for Acme"}),
)


def _vector(*, axis: int) -> tuple[float, ...]:
    from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS

    values = [0.0] * P1_VECTOR_DIMENSIONS
    values[axis] = 1.0
    return tuple(values)


class _OpenBoundary:
    """Readiness and admission that let every request through."""

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[()]:
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        return None


def seed(*, engine: Engine) -> UUID:
    """Bootstrap the deployment and seed the lineage, claim, entities and fact.

    Returns the seeded version id. Writes no D143 configuration row.
    """
    from rememberstack.adapters import PostgresP1Index
    from rememberstack.core.embedding_input_policy import EMBEDDING_INPUT_POLICY_VERSION
    from rememberstack.core.embedding_input_policy import embedding_text_hash
    from rememberstack.model import ClaimValidPrecision
    from rememberstack.model import DeploymentBootstrapInput
    from rememberstack.model import P1ChunkRow
    from rememberstack.model import P1ClaimRow
    from rememberstack.model import P1FactRow
    from rememberstack.spine import DeploymentBootstrapper
    from tests.surfaces.lineage_seed import seed_entity_mention
    from tests.surfaces.lineage_seed import seed_live_document_lineage

    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=DEPLOYMENT_ID,
            slug="d143-defaults",
            name="D143 default operations",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    index = PostgresP1Index(engine=engine, embedding_model=_MODEL)
    index.configure_channels(deployment_id=DEPLOYMENT_ID)
    with engine.begin() as connection:
        lineage = seed_live_document_lineage(
            connection=connection,
            deployment_id=DEPLOYMENT_ID,
            doc_id=DOC_ID,
            chunk_ids=(CHUNK_ID,),
            label="tariff",
            title="tariff policy",
            source_ref="tariff",
            at=SEEDED_AT,
        )
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, is_current_testimony, extractor_version,"
                " ingested_at, asserted_at) VALUES (:claim, :d, :doc, :chunk, :body,"
                " :body, 0, :end, true, true, true, 'd143-test', :at, :at)"
            ),
            {
                "claim": CLAIM_ID,
                "d": DEPLOYMENT_ID,
                "doc": DOC_ID,
                "chunk": CHUNK_ID,
                "body": _BODY,
                "end": len(_BODY),
                "at": SEEDED_AT,
            },
        )
        connection.execute(
            text(
                "INSERT INTO chunk_claims (deployment_id, chunk_id, claim_id,"
                " evidence_spans, source_locators, created_at) VALUES (:d, :chunk,"
                " :claim, CAST(:spans AS jsonb), CAST('{}' AS jsonb), :at)"
            ),
            {
                "d": DEPLOYMENT_ID,
                "chunk": CHUNK_ID,
                "claim": CLAIM_ID,
                "spans": f'[{{"char_start": 0, "char_end": {len(_BODY)}}}]',
                "at": SEEDED_AT,
            },
        )
        for entity_id, name in ((ALICE_ID, "Alice"), (ACME_ID, "Acme")):
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id,"
                    " canonical_name, normalized_name)"
                    " VALUES (:e, :d, :n, lower(:n))"
                ),
                {"e": entity_id, "d": DEPLOYMENT_ID, "n": name},
            )
            connection.execute(
                text(
                    "INSERT INTO aliases (alias_id, deployment_id, entity_id,"
                    " alias_text, normalized_lemma, provenance) VALUES"
                    " (:alias, :d, :e, :n, lower(:n), 'llm_canonical')"
                ),
                {
                    "alias": ALIAS_IDS[entity_id],
                    "d": DEPLOYMENT_ID,
                    "e": entity_id,
                    "n": name,
                },
            )
            seed_entity_mention(
                connection=connection,
                deployment_id=DEPLOYMENT_ID,
                entity_id=entity_id,
                doc_id=DOC_ID,
                chunk_id=CHUNK_ID,
                claim_id=CLAIM_ID,
                surface_form=name,
                resolver_version="d143-defaults",
                at=SEEDED_AT,
            )
        connection.execute(
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id,"
                " normalizer_version, fact_label, evidence_count, valid_from,"
                " valid_precision, window_claim_ids, ingested_at) VALUES (:r, :d,"
                " :s, 'works_for', :o, 'toy', 'Alice works for Acme.', 1, :at,"
                " 'open', :witnesses, :at)"
            ),
            {
                "r": RELATION_ID,
                "d": DEPLOYMENT_ID,
                "s": ALICE_ID,
                "o": ACME_ID,
                "at": SEEDED_AT,
                "witnesses": [CLAIM_ID],
            },
        )
        # the fact's supporting testimony, so facts_context returns it
        connection.execute(
            text(
                "INSERT INTO relation_evidence (deployment_id, relation_id,"
                " claim_id, doc_id, stance, normalizer_version) VALUES"
                " (:d, :r, :claim, :doc, CAST('supports' AS evidence_stance), 'toy')"
            ),
            {"d": DEPLOYMENT_ID, "r": RELATION_ID, "claim": CLAIM_ID, "doc": DOC_ID},
        )
    index.upsert_chunks(
        rows=(
            P1ChunkRow(
                chunk_id=CHUNK_ID,
                deployment_id=DEPLOYMENT_ID,
                doc_id=DOC_ID,
                version_id=lineage.version_id,
                section_role="body",
                text=_BODY,
                vector=_vector(axis=0),
                policy_generation=EMBEDDING_INPUT_POLICY_VERSION,
                embedder_generation=_MODEL,
                embedding_text_hash=embedding_text_hash(_BODY),
                source_kind="upload",
                source_shape="document",
            ),
        )
    )
    index.upsert_claims(
        rows=(
            P1ClaimRow(
                claim_id=CLAIM_ID,
                deployment_id=DEPLOYMENT_ID,
                doc_id=DOC_ID,
                chunk_id=CHUNK_ID,
                text=_BODY,
                is_current_testimony=True,
                is_attributed=False,
                vector=_vector(axis=0),
            ),
        )
    )
    index.upsert_facts(
        rows=(
            P1FactRow(
                fact_id=RELATION_ID,
                deployment_id=DEPLOYMENT_ID,
                kind="relation",
                label="Alice works for Acme.",
                status="active",
                valid_from=SEEDED_AT,
                valid_until=None,
                valid_precision=ClaimValidPrecision.OPEN,
                ingested_at=SEEDED_AT,
                invalidated_at=None,
                vector=_vector(axis=0),
            ),
        )
    )
    return lineage.version_id


def _composed(*, engine: Engine) -> tuple[Any, Any, Any]:
    """The query engine, registry and executor, composed as the profile does."""
    from rememberstack.adapters import BoundedPostgresReadPool
    from rememberstack.adapters import PostgresP1Index
    from rememberstack.spine import AssuredOperationRegistry
    from rememberstack.spine import seed_canonical_operations
    from rememberstack.surfaces import QueryEngine
    from rememberstack.surfaces.graph_queries import GraphQueries
    from rememberstack.surfaces.operation_executor import OperationExecutor

    pool = BoundedPostgresReadPool(
        engine=engine, max_concurrency=4, pool_wait_seconds=5.0
    )
    provider = MagicMock()
    provider.embed.return_value = SimpleNamespace(
        vectors=(_vector(axis=0),), usage=None
    )
    query = QueryEngine(
        engine=engine,
        search_index=PostgresP1Index(
            engine=engine, embedding_model=_MODEL, read_pool=pool
        ),
        model_provider=provider,
        embedding_model=_MODEL,
        fact_read_pool=pool,
    )
    registry = AssuredOperationRegistry(engine=engine)
    seed_canonical_operations(registry=registry, deployment_id=DEPLOYMENT_ID)
    executor = OperationExecutor(
        query_engine=query,
        graph_queries=GraphQueries(engine=engine, deployment_id=DEPLOYMENT_ID),
    )
    return query, registry, executor


def _outcome(call: Any) -> Any:
    """The JSON of one call, or its exception's type and message."""
    try:
        return json.loads(call())
    except Exception as error:  # noqa: BLE001 - the refusal is part of the capture
        return {"raised": type(error).__name__, "message": str(error)}


def capture(*, engine: Engine, version_id: UUID) -> dict[str, Any]:
    """Run the four assured operations and readiness; normalise volatile values."""
    from rememberstack.model import ReadinessRequirements
    from rememberstack.profiles.selfhost import _expected_components
    from rememberstack.spine.projection import ProjectionCatalog
    from rememberstack.spine.readiness import PipelineReadinessCatalog
    from rememberstack.surfaces.http_api import build_api
    from rememberstack.surfaces.operation_surface import OperationSurface

    query, registry, executor = _composed(engine=engine)
    surface = OperationSurface(
        registry=registry, executor=executor, deployment_id=DEPLOYMENT_ID
    )
    boundary = _OpenBoundary()
    client = TestClient(
        build_api(
            engine=query,
            deployment_id=DEPLOYMENT_ID,
            admission=boundary,
            readiness=boundary,
            surface=surface,
        )
    )
    captured: dict[str, Any] = {"executor": {}, "http": {}}
    for name, arguments in _OPERATIONS:
        operation = registry.by_name(deployment_id=DEPLOYMENT_ID, name=name)
        assert operation is not None, name
        captured["executor"][name] = _outcome(
            lambda operation=operation, arguments=arguments: executor.execute(
                deployment_id=DEPLOYMENT_ID,
                operation=operation,
                arguments=dict(arguments),
                evaluated_at=EVALUATED_AT,
            ).model_dump_json()
        )
        response = client.post(f"/operations/{name}", json=arguments)
        captured["http"][name] = {
            "status": response.status_code,
            "body": response.json(),
        }
    report = PipelineReadinessCatalog(
        engine=engine,
        expected_components=_expected_components(),
        projections=ProjectionCatalog(engine=engine),
    ).inspect(
        deployment_id=DEPLOYMENT_ID,
        version_ids=(version_id,),
        require=ReadinessRequirements(
            pipeline=True, p1=False, live_graph=False, p3=False
        ),
    )
    captured["readiness"] = json.loads(report.model_dump_json())
    # D143 §8: reading every surface writes no configuration or gap row (the
    # base has neither table, so both count zero there too).
    with engine.connect() as connection:
        for table in ("deployment_processing_layers", "processing_layer_gaps"):
            exists = connection.execute(
                text("SELECT to_regclass(:table) IS NOT NULL"),
                {"table": f"public.{table}"},
            ).scalar_one()
            captured[f"{table}_rows"] = (
                connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                if exists
                else 0
            )
    return normalise(captured)


def normalise(value: Any) -> Any:
    """Replace wall-clock instants and unseeded UUIDs with stable placeholders."""
    numbering: dict[str, str] = {}

    def instant(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return raw if parsed in _FIXED_INSTANTS else "<wallclock>"

    def uuid(match: re.Match[str]) -> str:
        raw = match.group(0)
        if raw in _SEEDED_UUIDS:
            return raw
        return numbering.setdefault(raw, f"<uuid-{len(numbering) + 1}>")

    def walk(item: Any) -> Any:
        if isinstance(item, dict):
            return {walk(key): walk(child) for key, child in item.items()}
        if isinstance(item, list):
            return [walk(child) for child in item]
        if isinstance(item, str):
            return _UUID.sub(uuid, _INSTANT.sub(instant, item))
        return item

    return walk(value)


def render(captured: dict[str, Any]) -> str:
    """The fixture's on-disk form."""
    return json.dumps(captured, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _main() -> int:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    from rememberstack.spine.settings import load_database_settings
    from tests.database_reset import reset_database

    url = load_database_settings().sqlalchemy_url()
    config = Config(str(Path.cwd() / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(url)
    try:
        version_id = seed(engine=engine)
        sys.stdout.write(render(capture(engine=engine, version_id=version_id)))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
