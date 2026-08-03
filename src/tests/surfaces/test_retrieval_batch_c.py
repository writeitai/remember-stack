"""Batch C proofs for question-driven current facts and evidence backing."""

from collections.abc import Iterator
from datetime import datetime
from datetime import timedelta
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

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import NegativeKind
from rememberstack.model import P1ChunkText
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import QueryEngine

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("5a000000-0000-0000-0000-000000000001")
_NOW = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply the real structural head for authoritative Batch C proofs."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for Batch C proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


class _FactIndex:
    """Deterministic P1 facts nomination with no unrelated channel behavior."""

    def __init__(self, *, fact_ids: tuple[UUID, ...]) -> None:
        self.fact_ids = tuple(str(fact_id) for fact_id in fact_ids)
        self.requested_k: list[int] = []

    def search_facts(
        self, *, deployment_id: str, vector: tuple[float, ...], k: int, kind: str | None
    ) -> tuple[str, ...]:
        self.requested_k.append(k)
        return self.fact_ids[:k]

    def search_claims(self, **_: object) -> tuple[str, ...]:
        return ()

    def search_claims_lexical(self, **_: object) -> tuple[str, ...]:
        return ()

    def search_chunks(self, **_: object) -> tuple[str, ...]:
        return ()

    def search_chunks_lexical(self, **_: object) -> tuple[str, ...]:
        return ()

    def chunk_texts(self, **_: object) -> dict[str, P1ChunkText]:
        return {}


class _Corpus:
    """Relations and observations with live, stale, and tombstoned evidence."""

    def __init__(self, *, engine: Engine) -> None:
        self.engine = engine
        self.provider = FakeModelProvider(generate_payloads={})
        self.subject_id = uuid4()
        self.object_id = uuid4()
        self.relation_id = uuid4()
        self.observation_id = uuid4()
        self.unbacked_id = uuid4()
        self.budget_fact_ids: list[UUID] = []
        self.claims: dict[str, UUID] = {}
        self.claim_docs: dict[UUID, UUID] = {}
        with engine.begin() as connection:
            self._seed_entities(connection)
            self._seed_primary_facts(connection)
            self._seed_primary_evidence(connection)
            self._seed_budget_facts(connection)

    def _seed_entities(self, connection: Connection) -> None:
        for entity_id, kind, name in (
            (self.subject_id, "Person", "Alice"),
            (self.object_id, "Organization", "Acme"),
        ):
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id, type,"
                    " canonical_name, normalized_name)"
                    " VALUES (:entity, :deployment, :kind, :name, lower(:name))"
                ),
                {
                    "entity": entity_id,
                    "deployment": _DEPLOYMENT_ID,
                    "kind": kind,
                    "name": name,
                },
            )

    def _seed_primary_facts(self, connection: Connection) -> None:
        self._relation(
            connection,
            fact_id=self.relation_id,
            label="Alice works at Acme",
            evidence_count=3,
            contradict_count=2,
            object_id=self.object_id,
        )
        self._relation(connection, fact_id=self.unbacked_id, label="Alice knows Acme")
        connection.execute(
            text(
                "INSERT INTO observations (observation_id, deployment_id,"
                " subject_entity_id, statement, normalizer_version, evidence_count,"
                " contradict_count, ingested_at) VALUES (:fact, :deployment,"
                " :subject, 'Alice prefers concise reports', 'batch-c', 1, 1, :at)"
            ),
            {
                "fact": self.observation_id,
                "deployment": _DEPLOYMENT_ID,
                "subject": self.subject_id,
                "at": _NOW,
            },
        )

    def _seed_primary_evidence(self, connection: Connection) -> None:
        shared_doc = self._document(connection, key="shared-support")
        self._evidence(
            connection,
            key="support-new-same-lineage",
            fact_id=self.relation_id,
            kind="relation",
            stance="supports",
            doc_id=shared_doc,
            at=_NOW,
        )
        self._evidence(
            connection,
            key="support-old-same-lineage",
            fact_id=self.relation_id,
            kind="relation",
            stance="supports",
            doc_id=shared_doc,
            at=_NOW - timedelta(days=3),
        )
        self._evidence(
            connection,
            key="support-other-lineage",
            fact_id=self.relation_id,
            kind="relation",
            stance="supports",
            at=_NOW - timedelta(days=1),
        )
        for index in range(2):
            self._evidence(
                connection,
                key=f"contradict-{index}",
                fact_id=self.relation_id,
                kind="relation",
                stance="contradicts",
                at=_NOW - timedelta(hours=index),
            )
        tombstoned_doc = self._document(connection, key="tombstoned")
        self._evidence(
            connection,
            key="tombstoned-support",
            fact_id=self.relation_id,
            kind="relation",
            stance="supports",
            doc_id=tombstoned_doc,
            at=_NOW + timedelta(minutes=1),
        )
        connection.execute(
            text("UPDATE documents SET deleted_at = :at WHERE doc_id = :doc"),
            {"at": _NOW, "doc": tombstoned_doc},
        )
        self._evidence(
            connection,
            key="noncurrent-contradict",
            fact_id=self.relation_id,
            kind="relation",
            stance="contradicts",
            current=False,
            at=_NOW + timedelta(minutes=2),
        )
        self._evidence(
            connection,
            key="observation-support",
            fact_id=self.observation_id,
            kind="observation",
            stance="supports",
            at=_NOW,
        )
        self._evidence(
            connection,
            key="observation-contradict",
            fact_id=self.observation_id,
            kind="observation",
            stance="contradicts",
            at=_NOW,
        )

    def _seed_budget_facts(self, connection: Connection) -> None:
        for fact_index in range(30):
            fact_id = uuid4()
            self.budget_fact_ids.append(fact_id)
            self._relation(
                connection,
                fact_id=fact_id,
                label=f"Budget fact {fact_index:02d}",
                evidence_count=3,
                contradict_count=3,
            )
            for stance in ("supports", "contradicts"):
                for evidence_index in range(3):
                    self._evidence(
                        connection,
                        key=f"budget-{fact_index}-{stance}-{evidence_index}",
                        fact_id=fact_id,
                        kind="relation",
                        stance=stance,
                        at=_NOW - timedelta(minutes=evidence_index),
                    )

    def _relation(
        self,
        connection: Connection,
        *,
        fact_id: UUID,
        label: str,
        evidence_count: int = 0,
        contradict_count: int = 0,
        object_id: UUID | None = None,
    ) -> None:
        if object_id is None:
            object_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id, type,"
                    " canonical_name, normalized_name) VALUES (:entity, :deployment,"
                    " 'Organization', :name, lower(:name))"
                ),
                {
                    "entity": object_id,
                    "deployment": _DEPLOYMENT_ID,
                    "name": f"Object {object_id}",
                },
            )
        connection.execute(
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id,"
                " normalizer_version, fact_label, evidence_count, contradict_count,"
                " ingested_at) VALUES (:fact, :deployment, :subject, 'works_for',"
                " :object, 'batch-c', :label, :supports, :contradicts, :at)"
            ),
            {
                "fact": fact_id,
                "deployment": _DEPLOYMENT_ID,
                "subject": self.subject_id,
                "object": object_id,
                "label": label,
                "supports": evidence_count,
                "contradicts": contradict_count,
                "at": _NOW,
            },
        )

    def _document(self, connection: Connection, *, key: str) -> UUID:
        doc_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                " source_ref, title) VALUES (:doc, :deployment, 'upload', :ref, :title)"
            ),
            {
                "doc": doc_id,
                "deployment": _DEPLOYMENT_ID,
                "ref": f"batch-c-{key}-{doc_id}",
                "title": f"Batch C {key}",
            },
        )
        return doc_id

    def _evidence(
        self,
        connection: Connection,
        *,
        key: str,
        fact_id: UUID,
        kind: str,
        stance: str,
        at: datetime,
        doc_id: UUID | None = None,
        current: bool = True,
    ) -> None:
        doc_id = doc_id or self._document(connection, key=key)
        claim_id = uuid4()
        chunk_id = uuid4()
        body = f"Evidence for {key}."
        self.claims[key] = claim_id
        self.claim_docs[claim_id] = doc_id
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, is_current_testimony, extractor_version,"
                " ingested_at, asserted_at) VALUES (:claim, :deployment, :doc,"
                " :chunk, :body, :body, 0, :end, true, true, :current, 'batch-c',"
                " :at, :at)"
            ),
            {
                "claim": claim_id,
                "deployment": _DEPLOYMENT_ID,
                "doc": doc_id,
                "chunk": chunk_id,
                "body": body,
                "end": len(body),
                "current": current,
                "at": at,
            },
        )
        table = "relation_evidence" if kind == "relation" else "observation_evidence"
        fact_column = "relation_id" if kind == "relation" else "observation_id"
        connection.execute(
            text(
                f"INSERT INTO {table} (deployment_id, {fact_column}, claim_id,"
                " doc_id, stance, normalizer_version) VALUES (:deployment, :fact,"
                " :claim, :doc, CAST(:stance AS evidence_stance), 'batch-c')"
            ),
            {
                "deployment": _DEPLOYMENT_ID,
                "fact": fact_id,
                "claim": claim_id,
                "doc": doc_id,
                "stance": stance,
            },
        )

    def query_engine(
        self, *, fact_ids: tuple[UUID, ...]
    ) -> tuple[QueryEngine, _FactIndex]:
        index = _FactIndex(fact_ids=fact_ids)
        return (
            QueryEngine(
                engine=self.engine,
                search_index=index,
                model_provider=self.provider,
                embedding_model="batch-c",
            ),
            index,
        )


@pytest.fixture(scope="module")
def corpus(database_engine: Engine) -> _Corpus:
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="batch-c",
            name="Retrieval Batch C",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    return _Corpus(engine=database_engine)


def test_current_context_returns_both_fact_kinds_with_both_stances(
    corpus: _Corpus,
) -> None:
    engine, index = corpus.query_engine(
        fact_ids=(corpus.relation_id, corpus.observation_id)
    )
    answer = engine.current_context(
        deployment_id=_DEPLOYMENT_ID,
        query="Where does Alice work and what does she prefer?",
        k=2,
        evidence_per_fact=2,
    )

    assert index.requested_k == [3]
    assert tuple(fact.kind for fact in answer.facts) == ("relation", "observation")
    for fact in answer.facts:
        assert {
            link.stance for link in answer.fact_evidence if link.fact_id == fact.fact_id
        } == {"supports", "contradicts"}
    selected_support = [
        link.claim_id
        for link in answer.fact_evidence
        if link.fact_id == corpus.relation_id and link.stance == "supports"
    ]
    assert len({corpus.claim_docs[claim_id] for claim_id in selected_support}) == 2
    assert corpus.claims["support-old-same-lineage"] not in selected_support


@pytest.mark.parametrize(
    ("k", "evidence_per_fact", "message"),
    (
        (0, 1, "k must"),
        (31, 1, "k must"),
        (1, 0, "evidence_per_fact"),
        (1, 6, "evidence_per_fact"),
    ),
)
def test_current_context_enforces_public_bounds(
    corpus: _Corpus, k: int, evidence_per_fact: int, message: str
) -> None:
    engine, _index = corpus.query_engine(fact_ids=(corpus.relation_id,))
    with pytest.raises(ValueError, match=message):
        engine.current_context(
            deployment_id=_DEPLOYMENT_ID,
            query="Alice",
            k=k,
            evidence_per_fact=evidence_per_fact,
        )


def test_current_context_respects_the_60_record_budget(corpus: _Corpus) -> None:
    engine, _index = corpus.query_engine(fact_ids=tuple(corpus.budget_fact_ids))
    answer = engine.current_context(
        deployment_id=_DEPLOYMENT_ID,
        query="all budget facts",
        k=30,
        evidence_per_fact=3,
    )

    assert len(answer.facts) == 30
    assert len(answer.evidence) == 60
    assert len(answer.fact_evidence) == 60
    assert all(
        sum(link.fact_id == fact.fact_id for link in answer.fact_evidence) == 2
        for fact in answer.facts
    )
    assert {(total.returned, total.total) for total in answer.evidence_totals} == {
        (1, 3)
    }


def test_evidence_totals_are_exact_under_per_stance_truncation(corpus: _Corpus) -> None:
    engine, _index = corpus.query_engine(fact_ids=(corpus.relation_id,))
    answer = engine.current_context(
        deployment_id=_DEPLOYMENT_ID, query="Alice employment", k=1, evidence_per_fact=1
    )
    totals = {
        total.stance: (total.returned, total.total) for total in answer.evidence_totals
    }

    assert totals == {"supports": (1, 3), "contradicts": (1, 2)}


def test_tombstoned_and_noncurrent_evidence_is_excluded(corpus: _Corpus) -> None:
    engine, _index = corpus.query_engine(fact_ids=(corpus.relation_id,))
    answer = engine.current_context(
        deployment_id=_DEPLOYMENT_ID, query="Alice employment", k=1, evidence_per_fact=5
    )
    returned = {claim.claim_id for claim in answer.evidence}

    assert corpus.claims["tombstoned-support"] not in returned
    assert corpus.claims["noncurrent-contradict"] not in returned
    assert {(total.stance, total.total) for total in answer.evidence_totals} == {
        ("supports", 3),
        ("contradicts", 2),
    }


def test_unbacked_fact_is_dropped_and_never_returned_without_evidence(
    corpus: _Corpus,
) -> None:
    engine, _index = corpus.query_engine(fact_ids=(corpus.unbacked_id,))
    answer = engine.current_context(
        deployment_id=_DEPLOYMENT_ID, query="unbacked relationship"
    )

    assert not answer.facts
    assert not answer.evidence
    assert answer.dropped_by_hydration == 1
    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.KNOWN_EMPTY


def test_no_results_uses_the_query_driven_known_empty_convention(
    corpus: _Corpus,
) -> None:
    engine, _index = corpus.query_engine(fact_ids=())
    answer = engine.current_context(
        deployment_id=_DEPLOYMENT_ID, query="nothing resembles this"
    )

    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.KNOWN_EMPTY
    assert "nothing resembles this" in answer.negative.explanation
    assert answer.negative.workaround is not None
    assert answer.truncation is not None
    assert answer.truncation.estimated_total == 0
    assert answer.truncation.total_is_exact


def test_envelope_associations_are_explicit_and_never_order_based(
    corpus: _Corpus,
) -> None:
    engine, _index = corpus.query_engine(
        fact_ids=(corpus.relation_id, corpus.observation_id)
    )
    answer = engine.current_context(
        deployment_id=_DEPLOYMENT_ID, query="Alice context", k=2, evidence_per_fact=1
    )
    evidence_ids = {claim.claim_id for claim in answer.evidence}
    fact_ids = {fact.fact_id for fact in answer.facts}

    assert all(link.claim_id in evidence_ids for link in answer.fact_evidence)
    assert all(link.fact_id in fact_ids for link in answer.fact_evidence)
    assert len(answer.evidence_totals) == 2 * len(answer.facts)
    assert not answer.parts


def test_fact_nomination_k_probe_discloses_truncation(corpus: _Corpus) -> None:
    engine, _index = corpus.query_engine(
        fact_ids=(corpus.relation_id, corpus.observation_id)
    )
    answer = engine.current_context(
        deployment_id=_DEPLOYMENT_ID, query="Alice", k=1, evidence_per_fact=1
    )

    assert answer.truncation is not None
    assert answer.truncation.truncated
    assert answer.truncation.estimated_total == 2
    assert not answer.truncation.total_is_exact
