"""Batch D proofs for one-call, source-backed graph connection context."""

from collections.abc import Iterator
from datetime import datetime
from datetime import timedelta
from datetime import UTC
import json
from pathlib import Path
from typing import Any
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

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import FactSupport
from rememberstack.model import Grain
from rememberstack.model import NegativeKind
from rememberstack.model import P1ChunkText
from rememberstack.ports.p1_index import P1Nomination
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import ProjectionCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import GraphQueries
from rememberstack.surfaces import QueryEngine
from rememberstack.workers import GraphRebuildWorker
from rememberstack.workers import GraphSnapshotReader

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("5b000000-0000-0000-0000-000000000001")
_NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the real PostgreSQL spine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for Batch D proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


class _QuestionIndex:
    """Question-context nominations with deliberate cross-channel duplicates."""

    def __init__(
        self,
        *,
        claim_id: UUID,
        chunk_id: UUID,
        chunk_text: P1ChunkText,
        fact_id: UUID,
        entity_id: UUID,
        entity_ids: tuple[UUID, ...] | None = None,
    ) -> None:
        self.claim_id = str(claim_id)
        self.chunk_id = str(chunk_id)
        self.chunk_text = chunk_text
        self.fact_id = str(fact_id)
        self.entity_ids = tuple(str(item) for item in (entity_ids or (entity_id,)))

    def search_claims(self, **_: object) -> tuple[str, ...]:
        return (self.claim_id,)

    def search_claims_lexical(self, **_: object) -> tuple[str, ...]:
        return (self.claim_id,)

    def search_chunks(self, **_: object) -> tuple[str, ...]:
        return (self.chunk_id,)

    def search_chunks_lexical(self, **_: object) -> tuple[str, ...]:
        return (self.chunk_id,)

    def chunk_texts(
        self,
        *,
        deployment_id: str,
        chunk_ids: tuple[str, ...],
        policy_generation: str | None = None,
        embedder_generation: str | None = None,
    ) -> dict[str, P1ChunkText]:
        return {self.chunk_id: self.chunk_text} if self.chunk_id in chunk_ids else {}

    def search_facts(self, **_: object) -> tuple[str, ...]:
        return (self.fact_id,)

    def search_entities_scored(self, **_: object) -> tuple[P1Nomination, ...]:
        return tuple(
            P1Nomination(
                item_id=entity_id, rank=rank, score=1.0 / rank, channel="semantic"
            )
            for rank, entity_id in enumerate(self.entity_ids, start=1)
        )


class _Corpus:
    """A two-hop path, neighborhood-only edges, and D48/D54 edge cases."""

    def __init__(self, *, engine: Engine) -> None:
        self.engine = engine
        self.entities: dict[str, UUID] = {}
        self.relations: dict[str, UUID] = {}
        self.claims: dict[str, UUID] = {}
        self.docs: dict[str, UUID] = {}
        self.doc_chunks: dict[UUID, UUID] = {}
        self.query_chunk_id = uuid4()
        self.query_chunk_text = P1ChunkText(
            chunk_id=self.query_chunk_id,
            section_role="body",
            indexed_text=(
                "Connection context.\n\nAlice works with Beacon on the migration."
            ),
        )
        with engine.begin() as connection:
            self._seed_entities(connection)
            self._seed_relations(connection)
            self._seed_evidence(connection)
            self._flag_withdrawn(connection)

    def _seed_entities(self, connection: Connection) -> None:
        for key, name, entity_type, aliases in (
            ("alice", "Alice", "Person", ("Alice",)),
            ("beacon", "Beacon", "Project", ("Beacon",)),
            ("acme", "Acme", "Organization", ("Acme",)),
            ("bob", "Bob", "Person", ("Bob",)),
            ("legacy", "Legacy", "Project", ("Legacy",)),
            ("unsupported", "Unsupported", "Concept", ("Unsupported",)),
            ("isolated", "Isolated", "Person", ("Isolated",)),
            ("alex_a", "Alex Alpha", "Person", ("Alex",)),
            ("alex_b", "Alex Beta", "Person", ("Alex",)),
        ):
            entity_id = uuid4()
            self.entities[key] = entity_id
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id, type,"
                    " canonical_name, normalized_name) VALUES (:entity,"
                    " :deployment, :type, :name, lower(:name))"
                ),
                {
                    "entity": entity_id,
                    "deployment": _DEPLOYMENT_ID,
                    "type": entity_type,
                    "name": name,
                },
            )
            for alias in aliases:
                connection.execute(
                    text(
                        "INSERT INTO aliases (alias_id, deployment_id, entity_id,"
                        " alias_text, normalized_lemma, provenance) VALUES"
                        " (:alias_id, :deployment, :entity, :alias, lower(:alias),"
                        " 'llm_canonical')"
                    ),
                    {
                        "alias_id": uuid4(),
                        "deployment": _DEPLOYMENT_ID,
                        "entity": entity_id,
                        "alias": alias,
                    },
                )

    def _seed_relations(self, connection: Connection) -> None:
        self._relation(connection, "alice_beacon", "alice", "works_on", "beacon")
        self._relation(connection, "beacon_acme", "beacon", "part_of", "acme")
        self._relation(connection, "alice_bob", "alice", "works_for", "bob")
        self._relation(connection, "withdrawn", "alice", "works_on", "legacy")
        self._relation(connection, "unsupported", "alice", "works_on", "unsupported")

    def _relation(
        self,
        connection: Connection,
        key: str,
        subject: str,
        predicate: str,
        object_: str,
    ) -> None:
        relation_id = uuid4()
        self.relations[key] = relation_id
        connection.execute(
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id,"
                " normalizer_version, fact_label, evidence_count, ingested_at)"
                " VALUES (:relation, :deployment, :subject, :predicate, :object,"
                " 'batch-d', :label, 2, :at)"
            ),
            {
                "relation": relation_id,
                "deployment": _DEPLOYMENT_ID,
                "subject": self.entities[subject],
                "predicate": predicate,
                "object": self.entities[object_],
                "label": f"{subject} {predicate} {object_}",
                "at": _NOW,
            },
        )

    def _seed_evidence(self, connection: Connection) -> None:
        for relation_key in ("alice_beacon", "beacon_acme", "alice_bob"):
            for index in range(2):
                self._evidence(
                    connection,
                    key=f"{relation_key}-support-{index}",
                    relation_key=relation_key,
                    stance="supports",
                    live_chunk=(relation_key == "alice_beacon" and index == 0),
                    at=_NOW - timedelta(hours=index),
                )
            self._evidence(
                connection,
                key=f"{relation_key}-contradict",
                relation_key=relation_key,
                stance="contradicts",
                at=_NOW - timedelta(days=1),
            )
        # Repeating a claim inside one source lineage must not inflate D54's
        # evidence total. The production query therefore has to count the
        # authoritative evidence_lineage rows, not raw claim associations.
        self._evidence(
            connection,
            key="alice_beacon-support-repeat",
            relation_key="alice_beacon",
            stance="supports",
            doc_id=self.docs["alice_beacon-support-0"],
            at=_NOW + timedelta(minutes=2),
        )
        # A withdrawn edge still needs surviving historical provenance even
        # though it has no current testimony. Build that state explicitly.
        self._evidence(
            connection,
            key="withdrawn-historical",
            relation_key="withdrawn",
            stance="supports",
            at=_NOW - timedelta(days=2),
        )
        connection.execute(
            text(
                "UPDATE claims SET is_current_testimony = false"
                " WHERE deployment_id = :deployment AND claim_id = :claim"
            ),
            {
                "deployment": _DEPLOYMENT_ID,
                "claim": self.claims["withdrawn-historical"],
            },
        )
        # Entity resolution is provenance-gated. An isolated but known entity
        # therefore needs its own live source before a no-path answer can be a
        # typed known-empty result.
        isolated_doc = self._document(
            connection, key="isolated-provenance", live_chunk=False
        )
        connection.execute(
            text(
                "UPDATE documents SET document_entity_id = :entity"
                " WHERE deployment_id = :deployment AND doc_id = :doc"
            ),
            {
                "entity": self.entities["isolated"],
                "deployment": _DEPLOYMENT_ID,
                "doc": isolated_doc,
            },
        )
        tombstoned_doc = self._document(connection, key="tombstoned", live_chunk=False)
        self._evidence(
            connection,
            key="tombstoned-support",
            relation_key="alice_beacon",
            stance="supports",
            doc_id=tombstoned_doc,
            at=_NOW + timedelta(minutes=1),
        )
        connection.execute(
            text("UPDATE documents SET deleted_at = :at WHERE doc_id = :doc"),
            {"at": _NOW, "doc": tombstoned_doc},
        )
        # The v4 entity channel confirms through entities_current, whose D48
        # membership requires a surviving document association. Give the
        # three connected entities explicit, live document provenance instead
        # of weakening the production view for a test fixture.
        for entity_key, document_key in (
            ("alice", "alice_beacon-support-0"),
            ("beacon", "beacon_acme-support-0"),
            ("acme", "beacon_acme-support-1"),
            ("legacy", "withdrawn-historical"),
        ):
            connection.execute(
                text(
                    "UPDATE documents SET document_entity_id = :entity"
                    " WHERE deployment_id = :deployment AND doc_id = :doc"
                ),
                {
                    "entity": self.entities[entity_key],
                    "deployment": _DEPLOYMENT_ID,
                    "doc": self.docs[document_key],
                },
            )

    def _document(self, connection: Connection, *, key: str, live_chunk: bool) -> UUID:
        doc_id = uuid4()
        self.docs[key] = doc_id
        connection.execute(
            text(
                "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                " source_ref, title) VALUES (:doc, :deployment, 'upload', :ref,"
                " :title)"
            ),
            {
                "doc": doc_id,
                "deployment": _DEPLOYMENT_ID,
                "ref": f"batch-d-{key}-{doc_id}",
                "title": f"Batch D {key}",
            },
        )
        chunk_id = self.query_chunk_id if live_chunk else uuid4()
        self.doc_chunks[doc_id] = chunk_id
        self._live_chunk_document(connection, doc_id=doc_id, chunk_id=chunk_id)
        return doc_id

    def _live_chunk_document(
        self, connection: Connection, *, doc_id: UUID, chunk_id: UUID
    ) -> None:
        version_id = uuid4()
        representation_id = uuid4()
        section_id = uuid4()
        generation_id = uuid4()
        content_hash = f"batch-d-{doc_id}"
        connection.execute(
            text(
                "INSERT INTO content_objects (deployment_id, content_hash, mime,"
                " raw_uri) VALUES (:deployment, :hash, 'text/markdown', :uri)"
            ),
            {
                "deployment": _DEPLOYMENT_ID,
                "hash": content_hash,
                "uri": f"mem://raw/{doc_id}",
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_versions (version_id, deployment_id, doc_id,"
                " content_hash, version_no, status, source_modified_at) VALUES"
                " (:version, :deployment, :doc, :hash, 1, 'ready', :at)"
            ),
            {
                "version": version_id,
                "deployment": _DEPLOYMENT_ID,
                "doc": doc_id,
                "hash": content_hash,
                "at": _NOW,
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_representations (representation_id,"
                " deployment_id, version_id, route, markdown_uri, status) VALUES"
                " (:representation, :deployment, :version, 'digital', :uri, 'ready')"
            ),
            {
                "representation": representation_id,
                "deployment": _DEPLOYMENT_ID,
                "version": version_id,
                "uri": f"mem://artifacts/{doc_id}.md",
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_structure_generations"
                " (structure_generation_id, deployment_id, doc_id, version_id,"
                " representation_id, skeleton_version, skeleton_hash,"
                " skeleton_producer_family, roles_version, route_tag,"
                " candidate_skeleton_hash, stats_version, stats) VALUES"
                " (:generation, :deployment, :doc, :version, :representation,"
                " 'batch-d', 'skeleton', 'deterministic', 'batch-d', 'parser',"
                " 'candidate', 'batch-d', '{}'::jsonb)"
            ),
            {
                "generation": generation_id,
                "deployment": _DEPLOYMENT_ID,
                "doc": doc_id,
                "version": version_id,
                "representation": representation_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_sections (section_id, deployment_id, doc_id,"
                " version_id, representation_id, node_path, block_start, block_end,"
                " role, char_start, char_end, ordinal, structure_generation_id)"
                " VALUES (:section, :deployment, :doc, :version, :representation,"
                " '0', 0, 1, 'body', 0, 100, 0, :generation)"
            ),
            {
                "section": section_id,
                "deployment": _DEPLOYMENT_ID,
                "doc": doc_id,
                "version": version_id,
                "representation": representation_id,
                "generation": generation_id,
            },
        )
        connection.execute(
            text(
                "UPDATE document_representations SET"
                " current_structure_generation_id = :generation"
                " WHERE representation_id = :representation"
            ),
            {"generation": generation_id, "representation": representation_id},
        )
        connection.execute(
            text(
                "UPDATE document_versions SET current_representation_id ="
                " :representation WHERE version_id = :version"
            ),
            {"representation": representation_id, "version": version_id},
        )
        connection.execute(
            text(
                "UPDATE documents SET current_version_id = :version WHERE doc_id = :doc"
            ),
            {"version": version_id, "doc": doc_id},
        )
        connection.execute(
            text(
                "INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,"
                " representation_id, section_id, ordinal, block_start, block_end,"
                " chunk_content_hash, extraction_input_hash, char_start, char_end,"
                " context_prefix, created_at) VALUES (:chunk, :deployment, :doc,"
                " :version, :representation, :section, 0, 0, 0, 'batch-d-chunk',"
                " 'batch-d-input', 0, 60, 'Connection context.', :at)"
            ),
            {
                "chunk": chunk_id,
                "deployment": _DEPLOYMENT_ID,
                "doc": doc_id,
                "version": version_id,
                "representation": representation_id,
                "section": section_id,
                "at": _NOW,
            },
        )

    def _evidence(
        self,
        connection: Connection,
        *,
        key: str,
        relation_key: str,
        stance: str,
        at: datetime,
        live_chunk: bool = False,
        doc_id: UUID | None = None,
    ) -> None:
        doc_id = doc_id or self._document(connection, key=key, live_chunk=live_chunk)
        claim_id = uuid4()
        self.claims[key] = claim_id
        chunk_id = self.doc_chunks[doc_id]
        body = f"Evidence for {key}."
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, is_current_testimony, extractor_version,"
                " ingested_at, asserted_at) VALUES (:claim, :deployment, :doc,"
                " :chunk, :body, :body, 0, :end, true, true, true, 'batch-d',"
                " :at, :at)"
            ),
            {
                "claim": claim_id,
                "deployment": _DEPLOYMENT_ID,
                "doc": doc_id,
                "chunk": chunk_id,
                "body": body,
                "end": len(body),
                "at": at,
            },
        )
        connection.execute(
            text(
                "INSERT INTO relation_evidence (deployment_id, relation_id,"
                " claim_id, doc_id, stance, normalizer_version) VALUES"
                " (:deployment, :relation, :claim, :doc,"
                " CAST(:stance AS evidence_stance), 'batch-d')"
            ),
            {
                "deployment": _DEPLOYMENT_ID,
                "relation": self.relations[relation_key],
                "claim": claim_id,
                "doc": doc_id,
                "stance": stance,
            },
        )

    def _flag_withdrawn(self, connection: Connection) -> None:
        connection.execute(
            text(
                "INSERT INTO review_queue (review_id, deployment_id, item_kind,"
                " candidate, blast_radius, confidence, expected_impact, status)"
                " VALUES (:review, :deployment, 'support_withdrawn',"
                " CAST(:candidate AS jsonb), 1, 0.5, 0.5, 'pending')"
            ),
            {
                "review": uuid4(),
                "deployment": _DEPLOYMENT_ID,
                "candidate": json.dumps(
                    {
                        "fact_kind": "relation",
                        "fact_id": str(self.relations["withdrawn"]),
                    }
                ),
            },
        )

    def query_engine(
        self, *, entity_ids: tuple[UUID, ...] | None = None
    ) -> QueryEngine:
        """Compose the public engine, optionally with a ranked entity fixture."""
        index = _QuestionIndex(
            claim_id=self.claims["alice_beacon-support-0"],
            chunk_id=self.query_chunk_id,
            chunk_text=self.query_chunk_text,
            fact_id=self.relations["alice_beacon"],
            entity_id=self.entities["acme"],
            entity_ids=entity_ids,
        )
        return QueryEngine(
            engine=self.engine,
            search_index=index,
            model_provider=FakeModelProvider(generate_payloads={}),
            embedding_model="batch-d",
        )


@pytest.fixture(scope="module")
def corpus(
    database_engine: Engine, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[_Corpus, GraphQueries]]:
    """Publish one real P2 snapshot over the authoritative Batch D corpus."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="batch-d",
            name="Retrieval Batch D",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    seeded = _Corpus(engine=database_engine)
    root = tmp_path_factory.mktemp("batch-d-graph")
    catalog = ProjectionCatalog(engine=database_engine)
    store = LocalFSObjectStore(root=root / "snapshots")
    GraphRebuildWorker(catalog=catalog, snapshot_store=store).rebuild(
        deployment_id=_DEPLOYMENT_ID, workdir=root / "work"
    )
    reader = GraphSnapshotReader(
        catalog=catalog,
        snapshot_store=store,
        deployment_id=_DEPLOYMENT_ID,
        cache_dir=root / "cache",
    )
    yield seeded, GraphQueries(reader=reader)


def _answer(corpus: tuple[_Corpus, GraphQueries], **arguments: Any):  # noqa: ANN202, ANN401
    seeded, graph = corpus
    return seeded.query_engine().multi_hop_context(
        deployment_id=_DEPLOYMENT_ID,
        graph_queries=graph,
        query="How are Alice and Acme connected?",
        entity_a="Alice",
        **arguments,
    )


def test_question_context_v4_flags_default_false(
    corpus: tuple[_Corpus, GraphQueries],
) -> None:
    seeded, _graph = corpus
    answer = seeded.query_engine().question_context(
        deployment_id=_DEPLOYMENT_ID, query="Alice"
    )

    assert answer.evidence
    assert answer.chunks
    assert answer.facts == ()
    assert answer.entities == ()


def test_question_context_v4_fact_channel_reuses_current_context(
    corpus: tuple[_Corpus, GraphQueries],
) -> None:
    seeded, _graph = corpus
    answer = seeded.query_engine().question_context(
        deployment_id=_DEPLOYMENT_ID, query="Alice", include_facts=True
    )

    assert {fact.fact_id for fact in answer.facts} == {seeded.relations["alice_beacon"]}
    assert answer.fact_evidence
    assert answer.evidence_totals
    assert len(answer.fact_evidence) <= 60


def test_question_context_v4_entity_channel_is_resolution_first_and_confirmed(
    corpus: tuple[_Corpus, GraphQueries],
) -> None:
    seeded, _graph = corpus
    answer = seeded.query_engine().question_context(
        deployment_id=_DEPLOYMENT_ID, query="Alice", include_entities=True
    )

    assert [candidate.entity_id for candidate in answer.entities] == [
        seeded.entities["alice"],
        seeded.entities["acme"],
    ]
    assert [candidate.tier for candidate in answer.entities] == ["T0", "semantic"]


def test_question_context_confirms_before_cutting_the_entity_cap(
    corpus: tuple[_Corpus, GraphQueries],
) -> None:
    """A stale semantic head cannot hide the 21st, live ranked nomination."""
    seeded, _graph = corpus
    stale_head = tuple(uuid4() for _ in range(20))
    engine = seeded.query_engine(entity_ids=(*stale_head, seeded.entities["acme"]))

    answer = engine.question_context(
        deployment_id=_DEPLOYMENT_ID, query="no exact alias", include_entities=True
    )

    assert [candidate.entity_id for candidate in answer.entities] == [
        seeded.entities["acme"]
    ]
    assert answer.dropped_by_hydration >= len(stale_head)


def test_question_context_v4_flags_work_together(
    corpus: tuple[_Corpus, GraphQueries],
) -> None:
    seeded, _graph = corpus
    answer = seeded.query_engine().question_context(
        deployment_id=_DEPLOYMENT_ID,
        query="Alice",
        include_facts=True,
        include_entities=True,
    )

    assert answer.facts
    assert answer.entities
    assert answer.evidence
    assert answer.chunks


def test_two_entity_path_has_both_stances_and_exact_totals(
    corpus: tuple[_Corpus, GraphQueries],
) -> None:
    seeded, _graph = corpus
    answer = _answer(corpus, entity_b="Acme", hops=2, evidence_per_fact=1)

    assert answer.grain is Grain.EVIDENCE
    assert not answer.parts
    assert answer.negative is None
    assert answer.paths and answer.paths[0].length == 2
    assert {edge.relation_id for edge in answer.edges} == {
        seeded.relations["alice_beacon"],
        seeded.relations["beacon_acme"],
    }
    for edge in answer.edges:
        assert {
            link.stance
            for link in answer.fact_evidence
            if link.fact_id == edge.relation_id
        } == {"supports", "contradicts"}
        assert {
            total.stance: (total.returned, total.total)
            for total in answer.evidence_totals
            if total.fact_id == edge.relation_id
        } == {"supports": (1, 2), "contradicts": (1, 1)}


def test_one_entity_neighborhood_returns_ranked_edges(
    corpus: tuple[_Corpus, GraphQueries],
) -> None:
    answer = _answer(corpus, hops=1, k=30)

    assert answer.negative is None
    assert answer.edges
    assert all(path.length == 1 for path in answer.paths)
    assert answer.truncation is not None
    assert len(answer.edges) <= 30


def test_hops_bound_is_honored(corpus: tuple[_Corpus, GraphQueries]) -> None:
    seeded, _graph = corpus
    one_hop = _answer(corpus, hops=1, k=30)
    two_hops = _answer(corpus, hops=2, k=30)

    assert seeded.relations["beacon_acme"] not in {
        edge.relation_id for edge in one_hop.edges
    }
    assert seeded.relations["beacon_acme"] in {
        edge.relation_id for edge in two_hops.edges
    }


def test_withdrawn_edge_is_kept_flagged_without_blanket_unsupported_keep(
    corpus: tuple[_Corpus, GraphQueries],
) -> None:
    seeded, _graph = corpus
    answer = _answer(corpus, hops=1, k=30)
    edges = {edge.relation_id: edge for edge in answer.edges}

    assert edges[seeded.relations["withdrawn"]].support is FactSupport.WITHDRAWN
    assert seeded.relations["unsupported"] not in edges
    withdrawn_totals = {
        total.stance: (total.returned, total.total)
        for total in answer.evidence_totals
        if total.fact_id == seeded.relations["withdrawn"]
    }
    assert withdrawn_totals == {"supports": (0, 0), "contradicts": (0, 0)}


def test_tombstoned_lineage_evidence_is_excluded(
    corpus: tuple[_Corpus, GraphQueries],
) -> None:
    seeded, _graph = corpus
    answer = _answer(corpus, entity_b="Acme", hops=2, evidence_per_fact=5)
    evidence_ids = {record.claim_id for record in answer.evidence}

    assert seeded.claims["tombstoned-support"] not in evidence_ids
    totals = {
        total.stance: total.total
        for total in answer.evidence_totals
        if total.fact_id == seeded.relations["alice_beacon"]
    }
    assert totals == {"supports": 2, "contradicts": 1}


def test_ambiguous_entity_returns_candidates_and_boundary(
    corpus: tuple[_Corpus, GraphQueries],
) -> None:
    seeded, graph = corpus
    answer = seeded.query_engine().multi_hop_context(
        deployment_id=_DEPLOYMENT_ID,
        graph_queries=graph,
        query="What connects Alex?",
        entity_a="Alex",
    )

    assert len(answer.entities) == 2
    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.BOUNDARY
    assert "Alex Alpha" in answer.negative.explanation
    assert "Alex Beta" in answer.negative.explanation


def test_unknown_entity_is_typed(corpus: tuple[_Corpus, GraphQueries]) -> None:
    seeded, graph = corpus
    answer = seeded.query_engine().multi_hop_context(
        deployment_id=_DEPLOYMENT_ID,
        graph_queries=graph,
        query="What connects Nobody?",
        entity_a="Nobody",
    )

    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.UNKNOWN_ENTITY


def test_no_path_is_known_empty(corpus: tuple[_Corpus, GraphQueries]) -> None:
    answer = _answer(corpus, entity_b="Isolated", hops=2)

    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.KNOWN_EMPTY
    assert not answer.paths
    assert not answer.edges


def test_existing_path_that_cannot_fit_k_is_a_boundary(
    corpus: tuple[_Corpus, GraphQueries],
) -> None:
    answer = _answer(corpus, entity_b="Acme", hops=2, k=1)

    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.BOUNDARY
    assert "k=1" in answer.negative.explanation


def test_question_and_edge_evidence_and_chunks_are_deduplicated_by_id(
    corpus: tuple[_Corpus, GraphQueries],
) -> None:
    seeded, _graph = corpus
    answer = _answer(corpus, entity_b="Acme", hops=2)
    evidence_ids = [record.claim_id for record in answer.evidence]
    chunk_ids = [record.chunk_id for record in answer.chunks]

    assert len(evidence_ids) == len(set(evidence_ids))
    assert len(chunk_ids) == len(set(chunk_ids)) == 1
    assert len(evidence_ids) + len(chunk_ids) <= 60
    assert evidence_ids.count(seeded.claims["alice_beacon-support-0"]) == 1


@pytest.mark.parametrize(
    ("arguments", "message"),
    (
        ({"k": 0}, "k must"),
        ({"k": 31}, "k must"),
        ({"hops": 0}, "hops must"),
        ({"hops": 3}, "hops must"),
        ({"evidence_per_fact": 0}, "evidence_per_fact"),
        ({"evidence_per_fact": 6}, "evidence_per_fact"),
    ),
)
def test_public_bounds_are_enforced(
    corpus: tuple[_Corpus, GraphQueries], arguments: dict[str, int], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _answer(corpus, **arguments)
