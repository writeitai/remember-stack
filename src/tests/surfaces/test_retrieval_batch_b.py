"""Batch B proofs for entity, time-window, and neighboring-chunk recipes."""

from collections.abc import Iterator
from datetime import datetime
from datetime import UTC
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import LanceChunkIndex
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import EmbeddingRequest
from rememberstack.model import NegativeKind
from rememberstack.model import P1ChunkRow
from rememberstack.model import P1ClaimRow
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import QueryEngine

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("59000000-0000-0000-0000-000000000001")
_MENTIONED_AT = datetime(2026, 8, 1, tzinfo=UTC)
_WINDOW_FROM = datetime(2024, 6, 1, tzinfo=UTC)
_WINDOW_TO = datetime(2024, 6, 30, tzinfo=UTC)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply the real structural head for authoritative PostgreSQL joins."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for Batch B proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


class _Corpus:
    """One live document plus a 55-document hub and resolution edge cases."""

    def __init__(self, *, engine: Engine, lance_root: Path) -> None:
        self.engine = engine
        self.entity_ids: dict[str, UUID] = {}
        self.doc_id = uuid4()
        self.version_id = uuid4()
        self.representation_id = uuid4()
        self.section_id = uuid4()
        self.chunk_ids = tuple(uuid4() for _ in range(3))
        self.claim_ids = tuple(uuid4() for _ in range(4))
        with engine.begin() as connection:
            self._seed_entities(connection=connection)
            self._seed_live_document(connection=connection)
            self._seed_hub_documents(connection=connection)
        self.provider = FakeModelProvider(generate_payloads={})
        self.index = LanceChunkIndex(root=lance_root)
        self._seed_p1()
        self.provider.embedded_texts.clear()

    def _seed_entities(self, *, connection: Connection) -> None:
        for key, name in (
            ("alice", "Alice Example"),
            ("sam_a", "Sam Alpha"),
            ("sam_b", "Sam Beta"),
        ):
            entity_id = uuid4()
            self.entity_ids[key] = entity_id
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id, type,"
                    " canonical_name, normalized_name)"
                    " VALUES (:entity, :deployment, 'Person', :name, lower(:name))"
                ),
                {"entity": entity_id, "deployment": _DEPLOYMENT_ID, "name": name},
            )
        for entity_id, alias, lemma in (
            (self.entity_ids["alice"], "Alice", "alice"),
            (self.entity_ids["sam_a"], "Sam", "sam"),
            (self.entity_ids["sam_b"], "Sam", "sam"),
        ):
            connection.execute(
                text(
                    "INSERT INTO aliases (alias_id, deployment_id, entity_id,"
                    " alias_text, normalized_lemma, provenance)"
                    " VALUES (:alias_id, :deployment, :entity, :alias, :lemma,"
                    " 'llm_canonical')"
                ),
                {
                    "alias_id": uuid4(),
                    "deployment": _DEPLOYMENT_ID,
                    "entity": entity_id,
                    "alias": alias,
                    "lemma": lemma,
                },
            )

    def _seed_live_document(self, *, connection: Connection) -> None:
        content_hash = "batch-b-live-content"
        connection.execute(
            text(
                "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                " source_ref, title) VALUES (:doc, :deployment, 'upload',"
                " 'batch-b-live', 'Batch B live document')"
            ),
            {"doc": self.doc_id, "deployment": _DEPLOYMENT_ID},
        )
        connection.execute(
            text(
                "INSERT INTO content_objects (deployment_id, content_hash, mime,"
                " raw_uri) VALUES (:deployment, :hash, 'text/markdown', :uri)"
            ),
            {
                "deployment": _DEPLOYMENT_ID,
                "hash": content_hash,
                "uri": "mem://raw/batch-b",
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_versions (version_id, deployment_id, doc_id,"
                " content_hash, version_no, status, source_modified_at) VALUES"
                " (:version, :deployment, :doc, :hash, 1, 'ready', :at)"
            ),
            {
                "version": self.version_id,
                "deployment": _DEPLOYMENT_ID,
                "doc": self.doc_id,
                "hash": content_hash,
                "at": _MENTIONED_AT,
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_representations (representation_id,"
                " deployment_id, version_id, route, markdown_uri, status) VALUES"
                " (:representation, :deployment, :version, 'digital',"
                " 'mem://artifacts/batch-b.md', 'ready')"
            ),
            {
                "representation": self.representation_id,
                "deployment": _DEPLOYMENT_ID,
                "version": self.version_id,
            },
        )
        generation_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO document_structure_generations"
                " (structure_generation_id, deployment_id, doc_id, version_id,"
                " representation_id, skeleton_version, skeleton_hash,"
                " skeleton_producer_family, roles_version, route_tag,"
                " candidate_skeleton_hash, stats_version, stats) VALUES"
                " (:generation, :deployment, :doc, :version, :representation,"
                " 'batch-b', 'skeleton', 'deterministic', 'batch-b', 'parser',"
                " 'candidate', 'batch-b', '{}'::jsonb)"
            ),
            {
                "generation": generation_id,
                "deployment": _DEPLOYMENT_ID,
                "doc": self.doc_id,
                "version": self.version_id,
                "representation": self.representation_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_sections (section_id, deployment_id, doc_id,"
                " version_id, representation_id, node_path, block_start, block_end,"
                " role, char_start, char_end, ordinal, structure_generation_id)"
                " VALUES (:section, :deployment, :doc, :version, :representation,"
                " '0', 0, 2, 'body', 0, 300, 0, :generation)"
            ),
            {
                "section": self.section_id,
                "deployment": _DEPLOYMENT_ID,
                "doc": self.doc_id,
                "version": self.version_id,
                "representation": self.representation_id,
                "generation": generation_id,
            },
        )
        connection.execute(
            text(
                "UPDATE document_representations"
                " SET current_structure_generation_id = :generation"
                " WHERE representation_id = :representation"
            ),
            {"generation": generation_id, "representation": self.representation_id},
        )
        connection.execute(
            text(
                "UPDATE document_versions SET current_representation_id ="
                " :representation WHERE version_id = :version"
            ),
            {"representation": self.representation_id, "version": self.version_id},
        )
        connection.execute(
            text(
                "UPDATE documents SET current_version_id = :version WHERE doc_id = :doc"
            ),
            {"version": self.version_id, "doc": self.doc_id},
        )
        for ordinal, chunk_id in enumerate(self.chunk_ids):
            connection.execute(
                text(
                    "INSERT INTO chunks (chunk_id, deployment_id, doc_id,"
                    " version_id, representation_id, section_id, ordinal,"
                    " block_start, block_end, chunk_content_hash,"
                    " extraction_input_hash, char_start, char_end, context_prefix,"
                    " created_at) VALUES (:chunk, :deployment, :doc, :version,"
                    " :representation, :section, :ordinal, :ordinal, :ordinal,"
                    " :content_hash, :input_hash, :start, :end, :prefix, :at)"
                ),
                {
                    "chunk": chunk_id,
                    "deployment": _DEPLOYMENT_ID,
                    "doc": self.doc_id,
                    "version": self.version_id,
                    "representation": self.representation_id,
                    "section": self.section_id,
                    "ordinal": ordinal,
                    "content_hash": f"chunk-{ordinal}",
                    "input_hash": f"input-{ordinal}",
                    "start": ordinal * 100,
                    "end": ordinal * 100 + 90,
                    "prefix": f"Context {ordinal}.",
                    "at": _MENTIONED_AT,
                },
            )
        claim_rows = (
            (
                self.claim_ids[0],
                self.chunk_ids[0],
                "Alice led the launch.",
                True,
                "open",
                datetime(2024, 1, 1, tzinfo=UTC),
                None,
            ),
            (
                self.claim_ids[1],
                self.chunk_ids[1],
                "Alice signed the plan in June.",
                True,
                "day",
                datetime(2024, 6, 15, tzinfo=UTC),
                datetime(2024, 6, 15, tzinfo=UTC),
            ),
            (
                self.claim_ids[2],
                self.chunk_ids[1],
                "Alice discussed an unstamped detail.",
                True,
                "unknown",
                None,
                None,
            ),
            (
                self.claim_ids[3],
                self.chunk_ids[2],
                "An older source said Alice remained responsible.",
                False,
                "open",
                datetime(2024, 3, 1, tzinfo=UTC),
                None,
            ),
        )
        for (
            claim_id,
            chunk_id,
            body,
            current,
            precision,
            valid_from,
            valid_until,
        ) in claim_rows:
            connection.execute(
                text(
                    "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                    " section_id, claim_text, source_span, char_start, char_end,"
                    " anchor_ok, window_membership_ok, is_current_testimony,"
                    " claim_valid_from, claim_valid_until, claim_valid_precision,"
                    " claim_valid_kind, extractor_version, ingested_at) VALUES"
                    " (:claim, :deployment, :doc, :chunk, :section, :body, :body,"
                    " 0, 80, true, true, :current, :valid_from, :valid_until,"
                    " CAST(:precision AS claim_valid_precision),"
                    " CASE WHEN :precision = 'unknown' THEN NULL"
                    " ELSE 'event_time'::claim_valid_kind END, 'batch-b', :at)"
                ),
                {
                    "claim": claim_id,
                    "deployment": _DEPLOYMENT_ID,
                    "doc": self.doc_id,
                    "chunk": chunk_id,
                    "section": self.section_id,
                    "body": body,
                    "current": current,
                    "valid_from": valid_from,
                    "valid_until": valid_until,
                    "precision": precision,
                    "at": _MENTIONED_AT,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO chunk_claims (deployment_id, chunk_id, claim_id,"
                    " created_at) VALUES (:deployment, :chunk, :claim, :at)"
                ),
                {
                    "deployment": _DEPLOYMENT_ID,
                    "chunk": chunk_id,
                    "claim": claim_id,
                    "at": _MENTIONED_AT,
                },
            )
        for chunk_id in self.chunk_ids:
            self._mention(
                connection=connection,
                doc_id=self.doc_id,
                chunk_id=chunk_id,
                entity_id=self.entity_ids["alice"],
            )

    def _seed_hub_documents(self, *, connection: Connection) -> None:
        for index in range(55):
            doc_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                    " source_ref, title) VALUES (:doc, :deployment, 'upload',"
                    " :source_ref, :title)"
                ),
                {
                    "doc": doc_id,
                    "deployment": _DEPLOYMENT_ID,
                    "source_ref": f"hub-{index}",
                    "title": f"Hub document {index:02d}",
                },
            )
            self._mention(
                connection=connection,
                doc_id=doc_id,
                chunk_id=uuid4(),
                entity_id=self.entity_ids["alice"],
            )

    @staticmethod
    def _mention(
        *, connection: Connection, doc_id: UUID, chunk_id: UUID, entity_id: UUID
    ) -> None:
        mention_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO mentions (mention_id, deployment_id, surface_form,"
                " normalized_lemma, chunk_id, doc_id, created_at) VALUES"
                " (:mention, :deployment, 'Alice', 'alice', :chunk, :doc, :at)"
            ),
            {
                "mention": mention_id,
                "deployment": _DEPLOYMENT_ID,
                "chunk": chunk_id,
                "doc": doc_id,
                "at": _MENTIONED_AT,
            },
        )
        connection.execute(
            text(
                "INSERT INTO resolution_decisions (decision_id, deployment_id,"
                " mention_id, entity_id, method, confidence, resolver_version,"
                " decided_at) VALUES (:decision, :deployment, :mention, :entity,"
                " 'T0', 1.0, 'batch-b', :at)"
            ),
            {
                "decision": uuid4(),
                "deployment": _DEPLOYMENT_ID,
                "mention": mention_id,
                "entity": entity_id,
                "at": _MENTIONED_AT,
            },
        )

    def _seed_p1(self) -> None:
        self.index.upsert_chunks(
            rows=tuple(
                P1ChunkRow(
                    chunk_id=chunk_id,
                    deployment_id=_DEPLOYMENT_ID,
                    doc_id=self.doc_id,
                    version_id=self.version_id,
                    section_role="body",
                    text=f"Context {ordinal}.\n\nChunk body {ordinal}.",
                    vector=(float(ordinal + 1), 0.0),
                )
                for ordinal, chunk_id in enumerate(self.chunk_ids)
            )
        )
        query_vector = self.provider.embed(
            request=EmbeddingRequest(model="batch-b", texts=("June plan",))
        ).vectors[0]
        other_vector = tuple(reversed(query_vector))
        bodies = (
            "Alice led the launch.",
            "Alice signed the plan in June.",
            "Alice discussed an unstamped detail.",
            "An older source said Alice remained responsible.",
        )
        self.index.upsert_claims(
            rows=tuple(
                P1ClaimRow(
                    claim_id=claim_id,
                    deployment_id=_DEPLOYMENT_ID,
                    doc_id=self.doc_id,
                    chunk_id=self.chunk_ids[min(index, 2)],
                    text=body,
                    is_current_testimony=index != 3,
                    is_attributed=False,
                    vector=query_vector if index == 1 else other_vector,
                )
                for index, (claim_id, body) in enumerate(
                    zip(self.claim_ids, bodies, strict=True)
                )
            )
        )

    def query_engine(self) -> QueryEngine:
        return QueryEngine(
            engine=self.engine,
            search_index=self.index,
            model_provider=self.provider,
            embedding_model="batch-b",
        )


@pytest.fixture(scope="module")
def corpus(
    database_engine: Engine, tmp_path_factory: pytest.TempPathFactory
) -> _Corpus:
    """Seed one deployment once; the behavior tests are read-only."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="batch-b",
            name="Retrieval Batch B",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    return _Corpus(
        engine=database_engine, lance_root=tmp_path_factory.mktemp("batch-b-lance")
    )


def test_documents_about_orders_mentions_and_bounds_a_hub(corpus: _Corpus) -> None:
    answer = corpus.query_engine().documents_about(
        deployment_id=_DEPLOYMENT_ID, entity="Alice", k=20
    )

    assert len(answer.sources) == 20
    assert answer.sources[0].doc_id == corpus.doc_id
    assert answer.sources[0].mention_count == 3
    assert answer.sources[0].first_mentioned_at == _MENTIONED_AT
    assert answer.sources[0].last_mentioned_at == _MENTIONED_AT
    assert answer.truncation is not None
    assert answer.truncation.truncated
    assert answer.truncation.estimated_total == 56


def test_claims_about_uses_mentions_and_one_bounded_embedding(corpus: _Corpus) -> None:
    corpus.provider.embedded_texts.clear()
    answer = corpus.query_engine().claims_about(
        deployment_id=_DEPLOYMENT_ID, entity="Alice", query="June plan", k=2
    )

    assert corpus.provider.embedded_texts == ["June plan"]
    assert len(answer.evidence) == 2
    assert answer.evidence[0].claim_id == corpus.claim_ids[1]
    assert corpus.claim_ids[3] not in {row.claim_id for row in answer.evidence}
    assert answer.ranking[0].signals["semantic_similarity"] > 0
    assert answer.truncation is not None and answer.truncation.truncated


def test_claims_as_of_intersects_open_windows_and_counts_unknown(
    corpus: _Corpus,
) -> None:
    answer = corpus.query_engine().claims_as_of(
        deployment_id=_DEPLOYMENT_ID, from_=_WINDOW_FROM, to=_WINDOW_TO, k=20
    )

    returned = {claim.claim_id for claim in answer.evidence}
    assert returned == {corpus.claim_ids[0], corpus.claim_ids[1], corpus.claim_ids[3]}
    assert not next(
        claim for claim in answer.evidence if claim.claim_id == corpus.claim_ids[3]
    ).is_current_testimony
    assert corpus.claim_ids[2] not in returned
    assert answer.excluded_unstamped == 1


def test_claims_as_of_excludes_tombstoned_lineages_before_candidate_bound(
    corpus: _Corpus,
) -> None:
    tombstoned_doc_id = uuid4()
    tombstoned_claim_id = uuid4()
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                " source_ref, title) VALUES (:doc, :deployment, 'upload',"
                " :source_ref, 'Tombstoned Batch B document')"
            ),
            {
                "doc": tombstoned_doc_id,
                "deployment": _DEPLOYMENT_ID,
                "source_ref": f"batch-b-tombstone-{tombstoned_doc_id}",
            },
        )
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, claim_valid_from, claim_valid_until,"
                " claim_valid_precision, claim_valid_kind, extractor_version,"
                " ingested_at) VALUES (:claim, :deployment, :doc, :chunk, :body,"
                " :body, 0, 30, true, true, :valid_at, :valid_at, 'day',"
                " 'event_time', 'batch-b', :ingested_at)"
            ),
            {
                "claim": tombstoned_claim_id,
                "deployment": _DEPLOYMENT_ID,
                "doc": tombstoned_doc_id,
                "chunk": uuid4(),
                "body": "A removed source described a later June event.",
                "valid_at": datetime(2024, 6, 20, tzinfo=UTC),
                "ingested_at": _MENTIONED_AT,
            },
        )
    with corpus.engine.begin() as connection:
        connection.execute(
            text("UPDATE documents SET deleted_at = :at WHERE doc_id = :doc"),
            {"at": _MENTIONED_AT, "doc": tombstoned_doc_id},
        )

    try:
        answer = corpus.query_engine().claims_as_of(
            deployment_id=_DEPLOYMENT_ID, from_=_WINDOW_FROM, to=_WINDOW_TO, k=1
        )

        assert tuple(claim.claim_id for claim in answer.evidence) == (
            corpus.claim_ids[1],
        )
        assert tombstoned_claim_id not in {claim.claim_id for claim in answer.evidence}
        assert answer.dropped_by_hydration == 0
    finally:
        with corpus.engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM claims WHERE deployment_id = :deployment"
                    " AND claim_id = :claim"
                ),
                {"deployment": _DEPLOYMENT_ID, "claim": tombstoned_claim_id},
            )
            connection.execute(
                text("DELETE FROM documents WHERE doc_id = :doc"),
                {"doc": tombstoned_doc_id},
            )


def test_claims_as_of_semantically_ranks_only_the_window_set(corpus: _Corpus) -> None:
    corpus.provider.embedded_texts.clear()
    answer = corpus.query_engine().claims_as_of(
        deployment_id=_DEPLOYMENT_ID,
        from_=_WINDOW_FROM,
        to=_WINDOW_TO,
        query="June plan",
        k=2,
    )

    assert corpus.provider.embedded_texts == ["June plan"]
    assert answer.evidence[0].claim_id == corpus.claim_ids[1]
    assert corpus.claim_ids[2] not in {row.claim_id for row in answer.evidence}
    assert answer.ranking[0].signals["semantic_similarity"] > 0


def test_claims_as_of_rejects_an_inverted_window(corpus: _Corpus) -> None:
    with pytest.raises(ValueError, match="greater than or equal"):
        corpus.query_engine().claims_as_of(
            deployment_id=_DEPLOYMENT_ID, from_=_WINDOW_TO, to=_WINDOW_FROM
        )


@pytest.mark.parametrize(
    ("index", "expected", "truncated"),
    ((0, (0, 1), True), (1, (0, 1, 2), False), (2, (1, 2), True)),
)
def test_chunk_neighbors_preserve_order_and_disclose_document_edges(
    corpus: _Corpus, index: int, expected: tuple[int, ...], truncated: bool
) -> None:
    answer = corpus.query_engine().chunk_neighbors(
        deployment_id=_DEPLOYMENT_ID, chunk_id=corpus.chunk_ids[index], radius=1
    )

    assert tuple(chunk.chunk_id for chunk in answer.chunks) == tuple(
        corpus.chunk_ids[position] for position in expected
    )
    assert answer.truncation is not None
    assert answer.truncation.truncated is truncated


def test_chunk_neighbors_unknown_chunk_names_chunk_id(corpus: _Corpus) -> None:
    unknown_chunk_id = uuid4()
    answer = corpus.query_engine().chunk_neighbors(
        deployment_id=_DEPLOYMENT_ID, chunk_id=unknown_chunk_id
    )

    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.UNKNOWN_ENTITY
    assert f"chunk_id {unknown_chunk_id}" in answer.negative.explanation


@pytest.mark.parametrize("recipe", ("documents", "claims"))
def test_entity_ambiguity_returns_candidates_and_boundary(
    corpus: _Corpus, recipe: str
) -> None:
    engine = corpus.query_engine()
    answer = (
        engine.documents_about(deployment_id=_DEPLOYMENT_ID, entity="Sam")
        if recipe == "documents"
        else engine.claims_about(deployment_id=_DEPLOYMENT_ID, entity="Sam")
    )

    assert len(answer.entities) == 2
    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.BOUNDARY
    assert "Sam Alpha" in answer.negative.explanation
    assert "Sam Beta" in answer.negative.explanation


def test_entity_resolution_failure_is_unknown_entity(corpus: _Corpus) -> None:
    answer = corpus.query_engine().claims_about(
        deployment_id=_DEPLOYMENT_ID, entity="Nobody Here"
    )

    assert not answer.entities
    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.UNKNOWN_ENTITY
