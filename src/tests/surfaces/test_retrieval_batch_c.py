"""Batch C proofs for question-driven current facts and evidence backing."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from pathlib import Path
from time import monotonic
from typing import Any
from typing import cast
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
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from rememberstack.adapters import BoundedPostgresReadPool
import rememberstack.adapters.bounded_postgres_read as bounded_read_module
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import current_temporal_scope
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import FactSupport
from rememberstack.model import Freshness
from rememberstack.model import Grain
from rememberstack.model import GraphNode
from rememberstack.model import NegativeKind
from rememberstack.model import P1ChunkText
from rememberstack.model import Truncation
from rememberstack.model.assured_operations import AtFactTime
from rememberstack.model.assured_operations import CurrentFactTime
from rememberstack.model.assured_operations import FactTime
from rememberstack.model.assured_operations import HistoryFactTime
from rememberstack.model.assured_operations import OverlapFactTime
from rememberstack.ports.p1_index import P1Nomination
from rememberstack.ports.p1_index import P1SearchUnavailableError
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import GraphQueries
from rememberstack.surfaces import query_engine as query_engine_module
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.graph_queries import GraphBusyError
from rememberstack.surfaces.graph_queries import GraphHydrationError
from rememberstack.surfaces.query_engine import FACT_CONTEXT_CANDIDATE_K

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

    def __init__(self, *, fact_keys: tuple[tuple[str, UUID], ...]) -> None:
        self.fact_keys = tuple((kind, str(fact_id)) for kind, fact_id in fact_keys)
        self.requested_k: list[int] = []
        self.requested_entity_ids: list[tuple[str, ...]] = []
        self.requested_deadlines: list[float | None] = []

    def search_facts_scored(
        self,
        *,
        k: int,
        candidate_keys: tuple[tuple[str, str], ...] | None = None,
        entity_ids: tuple[str, ...] = (),
        deadline: float | None = None,
        **_: object,
    ) -> tuple[P1Nomination, ...]:
        self.requested_k.append(k)
        self.requested_entity_ids.append(entity_ids)
        self.requested_deadlines.append(deadline)
        allowed = None if candidate_keys is None else set(candidate_keys)
        selected = tuple(
            (kind, fact_id)
            for kind, fact_id in self.fact_keys
            if allowed is None or (kind, fact_id) in allowed
        )[:k]
        return tuple(
            P1Nomination(
                item_id=fact_id,
                rank=rank,
                score=1.0 / rank,
                channel="semantic",
                qualifier=kind,
            )
            for rank, (kind, fact_id) in enumerate(selected, start=1)
        )

    def search_facts(self, *, k: int, **_: object) -> tuple[str, ...]:
        """Satisfy the legacy primitive port; D87 never calls this method."""
        return tuple(fact_id for _kind, fact_id in self.fact_keys[:k])

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


class _ProfileRescueIndex(_FactIndex):
    """Nominate one fact only after profile text identifies its entity scope."""

    def __init__(self, *, entity_id: UUID, fact_id: UUID) -> None:
        super().__init__(fact_keys=(("observation", fact_id),))
        self.entity_id = entity_id
        self.profile_requests = 0
        self.profile_deadlines: list[float | None] = []

    def search_entities_scored(
        self, *, deadline: float | None = None, **_: object
    ) -> tuple[P1Nomination, ...]:
        """Return the profile whose prose matches the enumerative query."""
        self.profile_requests += 1
        self.profile_deadlines.append(deadline)
        return (
            P1Nomination(
                item_id=str(self.entity_id), rank=1, score=0.9, channel="semantic"
            ),
        )

    def search_facts_scored(
        self,
        *,
        k: int,
        candidate_keys: tuple[tuple[str, str], ...] | None = None,
        entity_ids: tuple[str, ...] = (),
        deadline: float | None = None,
        **arguments: object,
    ) -> tuple[P1Nomination, ...]:
        """Keep global fact search empty so only profile rescue can find the fact."""
        if not entity_ids:
            self.requested_k.append(k)
            self.requested_deadlines.append(deadline)
            return ()
        return super().search_facts_scored(
            k=k,
            candidate_keys=candidate_keys,
            entity_ids=entity_ids,
            deadline=deadline,
            **arguments,
        )


class _UnavailableProfileIndex(_FactIndex):
    """Expose the ordinary fact channel while the additive profile channel is cut."""

    def search_entities_scored(self, **_: object) -> tuple[P1Nomination, ...]:
        """Match the adapter contract during profile backfill or republishing."""
        raise P1SearchUnavailableError("entities semantic channel is not ready")


class _GraphNeighborhood:
    """Record D97 traversal arguments and return one bounded neighbor."""

    def __init__(self, *, neighbor_ids: tuple[UUID, ...]) -> None:
        self.neighbor_ids = neighbor_ids
        self.calls: list[dict[str, object]] = []

    def neighborhood(self, **arguments: object):
        """Return the configured neighbor as a fresh live-graph result."""
        self.calls.append(arguments)
        limit = cast(int, arguments["limit"])
        selected = self.neighbor_ids[:limit]
        return query_engine_module._envelope(  # noqa: SLF001 - typed graph stub
            grain=Grain.FACT,
            temporal_scope=current_temporal_scope(evaluated_at=_NOW),
            nodes=tuple(
                GraphNode(entity_id=entity_id, name=f"Neighbor {index}", hops=1)
                for index, entity_id in enumerate(selected)
            ),
            freshness=Freshness(pg_live_ts=_NOW),
            truncation=Truncation(
                truncated=len(selected) < len(self.neighbor_ids),
                returned=len(selected),
                estimated_total=len(self.neighbor_ids),
                total_is_exact=True,
            ),
        )


class _FailingGraph:
    """Raise one configured live-graph availability failure."""

    def __init__(self, *, error: Exception) -> None:
        self.error = error

    def neighborhood(self, **_: object):
        """Fail before returning a graph envelope."""
        raise self.error


class _TimedOutReadPool:
    """Reject every fact-authority checkout at bounded admission."""

    def __init__(self) -> None:
        self.deadlines: list[float | None] = []

    @contextmanager
    def connect(
        self, *, deadline: float | None, isolation_level: str | None = None
    ) -> Iterator[Connection]:
        """Record the attempted checkout and raise its typed timeout."""
        del isolation_level
        self.deadlines.append(deadline)
        raise SQLAlchemyTimeoutError("test retrieval admission expired")
        yield cast(Connection, None)  # pragma: no cover - generator typing only


class _Corpus:
    """Relations and observations with live, stale, and tombstoned evidence."""

    def __init__(self, *, engine: Engine) -> None:
        self.engine = engine
        self.read_pool = BoundedPostgresReadPool(
            engine=engine, max_concurrency=4, pool_wait_seconds=1.0
        )
        self.provider = FakeModelProvider(generate_payloads={})
        self.subject_id = uuid4()
        self.object_id = uuid4()
        self.relation_id = uuid4()
        self.observation_id = uuid4()
        self.bank_observation_id = uuid4()
        self.dynamic_relation_id = uuid4()
        self.kind_collision_id = uuid4()
        self.unbacked_id = uuid4()
        self.withdrawn_id = uuid4()
        self.ended_id = uuid4()
        self.future_id = uuid4()
        self.invalidated_id = uuid4()
        self.budget_fact_ids: list[UUID] = []
        self.claims: dict[str, UUID] = {}
        self.claim_docs: dict[UUID, UUID] = {}
        self.doc_chunks: dict[UUID, UUID] = {}
        with engine.begin() as connection:
            self._seed_entities(connection)
            self._seed_primary_facts(connection)
            self._seed_primary_evidence(connection)
            self._seed_temporal_facts(connection)
            self._seed_budget_facts(connection)

    def _seed_entities(self, connection: Connection) -> None:
        for entity_id, kind, name in (
            (self.subject_id, "Person", "Alice"),
            (self.object_id, "Organization", "Acme"),
        ):
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id,"
                    " canonical_name, normalized_name)"
                    " VALUES (:entity, :deployment, :name, lower(:name))"
                ),
                {
                    "entity": entity_id,
                    "deployment": _DEPLOYMENT_ID,
                    "kind": kind,
                    "name": name,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                    " source_ref, document_entity_id, title)"
                    " VALUES (:doc, :deployment, 'upload', :ref, :entity, :name)"
                ),
                {
                    "doc": uuid4(),
                    "deployment": _DEPLOYMENT_ID,
                    "ref": f"batch-c-entity-{entity_id}",
                    "entity": entity_id,
                    "name": name,
                },
            )

    def _seed_primary_facts(self, connection: Connection) -> None:
        connection.execute(
            text(
                "INSERT INTO predicates (deployment_id, predicate, parent_predicate,"
                " description, tier) VALUES (:deployment, 'other:traveled',"
                " 'related_to', 'Batch C dynamic predicate', 'other')"
                " ON CONFLICT (deployment_id, predicate) DO NOTHING"
            ),
            {"deployment": _DEPLOYMENT_ID},
        )
        self._relation(
            connection,
            fact_id=self.relation_id,
            label="Alice works at Acme",
            evidence_count=3,
            contradict_count=2,
            object_id=self.object_id,
        )
        self._relation(
            connection,
            fact_id=self.dynamic_relation_id,
            label="Alice traveled to Acme",
            evidence_count=1,
            object_id=self.object_id,
            predicate="other:traveled",
        )
        self._relation(connection, fact_id=self.unbacked_id, label="Alice knows Acme")
        self._relation(
            connection,
            fact_id=self.withdrawn_id,
            label="Alice previously supported a legacy project",
        )
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
        connection.execute(
            text(
                "INSERT INTO observations (observation_id, deployment_id,"
                " subject_entity_id, statement, normalizer_version, evidence_count,"
                " ingested_at) VALUES (:fact, :deployment, :subject,"
                " 'Acme is a bank', 'batch-c', 1, :at)"
            ),
            {
                "fact": self.bank_observation_id,
                "deployment": _DEPLOYMENT_ID,
                "subject": self.object_id,
                "at": _NOW,
            },
        )
        self._relation(
            connection,
            fact_id=self.kind_collision_id,
            label="Alice supports the shared-identity relation",
            evidence_count=1,
        )
        connection.execute(
            text(
                "INSERT INTO observations (observation_id, deployment_id,"
                " subject_entity_id, statement, normalizer_version, evidence_count,"
                " contradict_count, ingested_at) VALUES (:fact, :deployment,"
                " :subject, 'Shared-identity observation', 'batch-c', 0, 1, :at)"
            ),
            {
                "fact": self.kind_collision_id,
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
            key="dynamic-relation-support",
            fact_id=self.dynamic_relation_id,
            kind="relation",
            stance="supports",
            at=_NOW,
        )
        self._evidence(
            connection,
            key="bank-observation-support",
            fact_id=self.bank_observation_id,
            kind="observation",
            stance="supports",
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
        self._evidence(
            connection,
            key="kind-collision-relation-support",
            fact_id=self.kind_collision_id,
            kind="relation",
            stance="supports",
            at=_NOW,
        )
        self._evidence(
            connection,
            key="kind-collision-observation-contradict",
            fact_id=self.kind_collision_id,
            kind="observation",
            stance="contradicts",
            at=_NOW,
        )
        self._evidence(
            connection,
            key="withdrawn-historical",
            fact_id=self.withdrawn_id,
            kind="relation",
            stance="supports",
            current=False,
            at=_NOW - timedelta(days=2),
        )
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
                "candidate": (
                    f'{{"fact_kind":"relation","fact_id":"{self.withdrawn_id}"}}'
                ),
            },
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

    def _seed_temporal_facts(self, connection: Connection) -> None:
        """Seed ended, future, and retracted intervals for all D87 modes."""
        self._relation(
            connection,
            fact_id=self.ended_id,
            label="Alice previously advised a project",
            valid_from=_NOW - timedelta(days=10),
            valid_until=_NOW - timedelta(days=2),
            ingested_at=_NOW - timedelta(days=9),
        )
        self._relation(
            connection,
            fact_id=self.future_id,
            label="Alice will advise a future project",
            valid_from=_NOW + timedelta(days=2),
            ingested_at=_NOW,
        )
        self._relation(
            connection,
            fact_id=self.invalidated_id,
            label="Retracted project assignment",
            ingested_at=_NOW - timedelta(days=5),
            invalidated_at=_NOW - timedelta(days=1),
        )
        for key, fact_id, at in (
            ("temporal-ended", self.ended_id, _NOW - timedelta(days=3)),
            ("temporal-future", self.future_id, _NOW),
            ("temporal-invalidated", self.invalidated_id, _NOW - timedelta(days=2)),
        ):
            self._evidence(
                connection,
                key=key,
                fact_id=fact_id,
                kind="relation",
                stance="supports",
                at=at,
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
        predicate: str = "works_for",
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        ingested_at: datetime = _NOW,
        invalidated_at: datetime | None = None,
    ) -> None:
        if object_id is None:
            object_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id,"
                    " canonical_name, normalized_name) VALUES (:entity, :deployment,"
                    " :name, lower(:name))"
                ),
                {
                    "entity": object_id,
                    "deployment": _DEPLOYMENT_ID,
                    "name": f"Object {object_id}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                    " source_ref, document_entity_id, title)"
                    " VALUES (:doc, :deployment, 'upload', :ref, :entity, :name)"
                ),
                {
                    "doc": uuid4(),
                    "deployment": _DEPLOYMENT_ID,
                    "ref": f"batch-c-entity-{object_id}",
                    "entity": object_id,
                    "name": f"Object {object_id}",
                },
            )
        connection.execute(
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id,"
                " normalizer_version, fact_label, evidence_count, contradict_count,"
                " valid_from, valid_until, ingested_at, invalidated_at) VALUES"
                " (:fact, :deployment, :subject, :predicate, :object, 'batch-c',"
                " :label, :supports, :contradicts, :valid_from, :valid_until,"
                " :ingested_at, :invalidated_at)"
            ),
            {
                "fact": fact_id,
                "deployment": _DEPLOYMENT_ID,
                "subject": self.subject_id,
                "object": object_id,
                "predicate": predicate,
                "label": label,
                "supports": evidence_count,
                "contradicts": contradict_count,
                "valid_from": valid_from,
                "valid_until": valid_until,
                "ingested_at": ingested_at,
                "invalidated_at": invalidated_at,
            },
        )

    def _document(self, connection: Connection, *, key: str) -> UUID:
        doc_id = uuid4()
        version_id = uuid4()
        representation_id = uuid4()
        chunk_id = uuid4()
        content_hash = f"batch-c-{key}-{doc_id}"
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
        connection.execute(
            text(
                "INSERT INTO content_objects (deployment_id, content_hash, mime, raw_uri)"
                " VALUES (:deployment, :hash, 'text/plain', :uri)"
            ),
            {
                "deployment": _DEPLOYMENT_ID,
                "hash": content_hash,
                "uri": f"mem://{content_hash}",
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_versions (version_id, deployment_id, doc_id,"
                " content_hash, version_no, status) VALUES (:version, :deployment,"
                " :doc, :hash, 1, 'ready')"
            ),
            {
                "version": version_id,
                "deployment": _DEPLOYMENT_ID,
                "doc": doc_id,
                "hash": content_hash,
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_representations (representation_id, deployment_id,"
                " version_id, route, status) VALUES (:representation, :deployment,"
                " :version, 'passthrough', 'ready')"
            ),
            {
                "representation": representation_id,
                "deployment": _DEPLOYMENT_ID,
                "version": version_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,"
                " representation_id, ordinal, block_start, block_end,"
                " chunk_content_hash, extraction_input_hash, char_start, char_end,"
                " created_at) VALUES (:chunk, :deployment, :doc, :version,"
                " :representation, 0, 0, 0, :hash, :hash, 0, 32, :at)"
            ),
            {
                "chunk": chunk_id,
                "deployment": _DEPLOYMENT_ID,
                "doc": doc_id,
                "version": version_id,
                "representation": representation_id,
                "hash": content_hash,
                "at": _NOW,
            },
        )
        self.doc_chunks[doc_id] = chunk_id
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
        chunk_id = self.doc_chunks[doc_id]
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
        """Build an engine whose P1 stub nominates the requested fact ids."""
        return self.query_engine_for_keys(
            fact_keys=tuple(
                (
                    "observation" if fact_id == self.observation_id else "relation",
                    fact_id,
                )
                for fact_id in fact_ids
            )
        )

    def query_engine_for_keys(
        self, *, fact_keys: tuple[tuple[str, UUID], ...]
    ) -> tuple[QueryEngine, _FactIndex]:
        """Build an engine whose P1 stub nominates complete fact coordinates."""
        index = _FactIndex(fact_keys=fact_keys)
        return (
            QueryEngine(
                engine=self.engine,
                search_index=index,
                model_provider=self.provider,
                embedding_model="batch-c",
                fact_read_pool=self.read_pool,
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


def test_fact_context_returns_both_fact_kinds_with_both_stances(
    corpus: _Corpus,
) -> None:
    engine, index = corpus.query_engine(
        fact_ids=(corpus.relation_id, corpus.observation_id)
    )
    answer = engine.fact_context(
        deployment_id=_DEPLOYMENT_ID,
        query="Where does Alice work and what does she prefer?",
        k=2,
        evidence_per_fact=2,
    )

    assert index.requested_k == [FACT_CONTEXT_CANDIDATE_K + 1]
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


def test_default_fact_context_expands_empty_predicate_neighborhood_for_observations(
    corpus: _Corpus,
) -> None:
    """A neighbor's observation is found as fact text, never as a graph node."""
    engine, index = corpus.query_engine(fact_ids=(corpus.observation_id,))
    graph = _GraphNeighborhood(neighbor_ids=(corpus.subject_id,))

    answer = engine.default_fact_context(
        deployment_id=_DEPLOYMENT_ID,
        graph_queries=cast(Any, graph),
        query="What does Alice prefer?",
        entity_ids=(corpus.object_id,),
        evaluated_at=_NOW,
    )

    assert tuple(fact.fact_id for fact in answer.facts) == (corpus.observation_id,)
    assert tuple(node.entity_id for node in answer.nodes) == (corpus.subject_id,)
    assert corpus.observation_id not in {node.entity_id for node in answer.nodes}
    assert len(graph.calls) == 1
    assert isinstance(graph.calls[0]["_deadline"], float)
    assert {
        key: value for key, value in graph.calls[0].items() if key != "_deadline"
    } == {
        "entity_id": corpus.object_id,
        "hops": 1,
        "predicates": (),
        "valid_at": None,
        "believed_at": None,
        "limit": 19,
    }
    assert answer.freshness.pg_live_ts == _NOW
    assert index.requested_entity_ids == [
        (str(corpus.object_id), str(corpus.subject_id))
    ]


def test_default_fact_context_obeys_the_real_live_graph_clock_contract(
    corpus: _Corpus,
) -> None:
    """The current recipe leaves both clocks to the production graph facade."""
    engine, _index = corpus.query_engine(fact_ids=(corpus.relation_id,))
    graph = GraphQueries(engine=corpus.engine, deployment_id=_DEPLOYMENT_ID)

    answer = engine.default_fact_context(
        deployment_id=_DEPLOYMENT_ID,
        graph_queries=graph,
        query="Where does Alice work?",
        entity_ids=(corpus.object_id,),
        evaluated_at=_NOW,
    )

    assert tuple(fact.fact_id for fact in answer.facts) == (corpus.relation_id,)
    assert corpus.subject_id in {node.entity_id for node in answer.nodes}


def test_default_fact_context_supplies_paired_clocks_for_at_time(
    corpus: _Corpus,
) -> None:
    """An explicit world time travels with the operation's belief-time clock."""
    engine, _index = corpus.query_engine(fact_ids=(corpus.relation_id,))
    graph = _GraphNeighborhood(neighbor_ids=(corpus.subject_id,))

    answer = engine.default_fact_context(
        deployment_id=_DEPLOYMENT_ID,
        graph_queries=cast(Any, graph),
        query="Where did Alice work?",
        entity_ids=(corpus.object_id,),
        time=AtFactTime(at=_NOW - timedelta(days=3)),
        evaluated_at=_NOW,
    )

    assert answer.temporal_scope.mode == "at"
    assert graph.calls[0]["valid_at"] == _NOW - timedelta(days=3)
    assert graph.calls[0]["believed_at"] == _NOW


def test_live_graph_rejects_an_expired_composed_operation_deadline() -> None:
    """A composed graph read expires before semaphore or pool admission."""
    graph = GraphQueries(engine=cast(Engine, object()), deployment_id=_DEPLOYMENT_ID)

    with pytest.raises(TimeoutError, match="deadline expired"):
        graph.neighborhood(entity_id=uuid4(), _deadline=0.0)


def test_default_fact_context_keeps_anchor_fact_rank_after_neighborhood_expansion(
    corpus: _Corpus,
) -> None:
    """A two-endpoint neighbor relation cannot evict a better anchor observation."""
    engine, _index = corpus.query_engine_for_keys(
        fact_keys=(
            ("observation", corpus.bank_observation_id),
            ("relation", corpus.relation_id),
        )
    )
    graph = _GraphNeighborhood(neighbor_ids=(corpus.subject_id,))

    answer = engine.default_fact_context(
        deployment_id=_DEPLOYMENT_ID,
        graph_queries=cast(Any, graph),
        query="list banks",
        entity_ids=(corpus.object_id,),
        k=1,
        evaluated_at=_NOW,
    )

    assert tuple(fact.fact_id for fact in answer.facts) == (corpus.bank_observation_id,)


def test_default_fact_context_drops_a_stale_neighbor_without_losing_anchor_facts(
    corpus: _Corpus,
) -> None:
    """A concurrently stale graph node is disclosed without vetoing anchor facts."""
    stale_neighbor_id = uuid4()
    engine, index = corpus.query_engine(fact_ids=(corpus.relation_id,))
    graph = _GraphNeighborhood(neighbor_ids=(stale_neighbor_id,))

    answer = engine.default_fact_context(
        deployment_id=_DEPLOYMENT_ID,
        graph_queries=cast(Any, graph),
        query="Where does Alice work?",
        entity_ids=(corpus.subject_id,),
        evaluated_at=_NOW,
    )

    assert tuple(fact.fact_id for fact in answer.facts) == (corpus.relation_id,)
    assert answer.nodes == ()
    assert answer.negative is None
    assert answer.dropped_by_hydration == 1
    assert answer.truncation is not None
    assert answer.truncation.truncated is False
    assert answer.truncation.returned == 1
    assert answer.truncation.estimated_total == 1
    assert answer.truncation.total_is_exact is True
    assert index.requested_entity_ids == [(str(corpus.subject_id),)]


def test_default_fact_context_rechecks_only_caller_supplied_anchors(
    corpus: _Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A graph-derived neighbor retiring late cannot veto valid anchor facts."""
    engine, _index = corpus.query_engine(fact_ids=(corpus.relation_id,))
    graph = _GraphNeighborhood(neighbor_ids=(corpus.object_id,))
    checked_scopes: list[tuple[UUID, ...]] = []

    def current_ids(
        *, connection: Connection, deployment_id: UUID, entity_ids: tuple[UUID, ...]
    ) -> set[UUID]:
        """Retire the neighbor only after its explicit hydration check."""
        del connection
        assert deployment_id == _DEPLOYMENT_ID
        checked_scopes.append(entity_ids)
        if len(checked_scopes) == 2:
            return set(entity_ids)
        return {corpus.subject_id}

    monkeypatch.setattr(query_engine_module, "_current_context_entity_ids", current_ids)
    answer = engine.default_fact_context(
        deployment_id=_DEPLOYMENT_ID,
        graph_queries=cast(Any, graph),
        query="Where does Alice work?",
        entity_ids=(corpus.subject_id,),
        evaluated_at=_NOW,
    )

    assert tuple(fact.fact_id for fact in answer.facts) == (corpus.relation_id,)
    assert checked_scopes[-1] == (corpus.subject_id, corpus.object_id)
    assert answer.nodes == ()
    assert answer.dropped_by_hydration == 1
    assert answer.truncation is not None
    assert answer.truncation.returned == answer.truncation.estimated_total == 1


@pytest.mark.parametrize(
    "error",
    [
        GraphBusyError("busy"),
        GraphHydrationError("stale"),
        OperationalError("graph query", {}, Exception("timed out")),
        SQLAlchemyTimeoutError("graph pool timed out"),
    ],
)
def test_default_fact_context_maps_graph_failures_to_a_boundary(
    corpus: _Corpus, error: Exception
) -> None:
    """Busy, stale, and database-failed graph reads never escape as HTTP 500s."""
    engine, index = corpus.query_engine(fact_ids=(corpus.relation_id,))

    answer = engine.default_fact_context(
        deployment_id=_DEPLOYMENT_ID,
        graph_queries=cast(Any, _FailingGraph(error=error)),
        query="Where does Alice work?",
        entity_ids=(corpus.subject_id,),
        evaluated_at=_NOW,
    )

    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.BOUNDARY
    assert answer.facts == ()
    assert index.requested_k == []


def test_default_fact_context_maps_fact_pool_admission_timeout_to_boundary(
    corpus: _Corpus,
) -> None:
    """The shared 25-second budget includes fact-authority pool admission."""
    index = _FactIndex(fact_keys=(("relation", corpus.relation_id),))
    read_pool = _TimedOutReadPool()
    engine = QueryEngine(
        engine=corpus.engine,
        search_index=index,
        model_provider=corpus.provider,
        embedding_model="batch-c",
        fact_read_pool=read_pool,
    )
    started = monotonic()

    answer = engine.default_fact_context(
        deployment_id=_DEPLOYMENT_ID,
        graph_queries=cast(Any, _GraphNeighborhood(neighbor_ids=())),
        query="Where does Alice work?",
        entity_ids=(corpus.subject_id,),
        evaluated_at=_NOW,
    )

    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.BOUNDARY
    assert index.requested_k == []
    assert len(read_pool.deadlines) == 1
    assert read_pool.deadlines[0] is not None
    assert started + 24.0 < read_pool.deadlines[0] < started + 26.0


def test_default_fact_context_fails_closed_without_bounded_fact_pool(
    corpus: _Corpus,
) -> None:
    """A library composition cannot expose D97 through the general SQL pool."""
    index = _FactIndex(fact_keys=(("relation", corpus.relation_id),))
    engine = QueryEngine(
        engine=corpus.engine,
        search_index=index,
        model_provider=corpus.provider,
        embedding_model="batch-c",
    )

    answer = engine.default_fact_context(
        deployment_id=_DEPLOYMENT_ID,
        graph_queries=None,
        query="What is current?",
        evaluated_at=_NOW,
    )

    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.BOUNDARY
    assert index.requested_k == []


def test_default_fact_context_stops_graph_expansion_at_the_shared_deadline(
    corpus: _Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sequential anchor traversals consult the 25-second operation deadline."""
    engine, index = corpus.query_engine(fact_ids=(corpus.relation_id,))
    graph = _GraphNeighborhood(neighbor_ids=())
    observed_times = iter((0.0, 0.0, 0.0, 0.0, 0.0, 26.0))

    def operation_clock() -> float:
        """Advance one shared monotonic clock across admission and execution."""
        return next(observed_times)

    monkeypatch.setattr(query_engine_module, "monotonic", operation_clock)
    monkeypatch.setattr(bounded_read_module, "monotonic", operation_clock)

    answer = engine.default_fact_context(
        deployment_id=_DEPLOYMENT_ID,
        graph_queries=cast(Any, graph),
        query="shared context",
        entity_ids=(corpus.subject_id, corpus.object_id),
        evaluated_at=_NOW,
    )

    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.BOUNDARY
    assert len(graph.calls) == 1
    assert graph.calls[0]["_deadline"] == 25.0
    assert index.requested_k == []


def test_default_fact_context_reserves_neighbor_capacity_and_graph_predicate_size(
    corpus: _Corpus,
) -> None:
    """Direct callers receive the same 19-anchor/200-character bounds as HTTP."""
    engine, _index = corpus.query_engine(fact_ids=())

    with pytest.raises(ValueError, match="at most 19"):
        engine.default_fact_context(
            deployment_id=_DEPLOYMENT_ID,
            graph_queries=None,
            query="context",
            entity_ids=tuple(uuid4() for _ in range(20)),
            evaluated_at=_NOW,
        )
    with pytest.raises(ValueError, match="between 1 and 200"):
        engine.default_fact_context(
            deployment_id=_DEPLOYMENT_ID,
            graph_queries=None,
            query="context",
            entity_ids=(corpus.subject_id,),
            predicate="p" * 201,
            evaluated_at=_NOW,
        )


def test_default_fact_context_forwards_dynamic_predicate_to_graph_and_confirmation(
    corpus: _Corpus,
) -> None:
    """An `other:` predicate is legal and enforced again by PostgreSQL."""
    engine, _index = corpus.query_engine(fact_ids=(corpus.dynamic_relation_id,))
    graph = _GraphNeighborhood(neighbor_ids=(corpus.object_id,))

    answer = engine.default_fact_context(
        deployment_id=_DEPLOYMENT_ID,
        graph_queries=cast(Any, graph),
        query="Where did Alice travel?",
        entity_ids=(corpus.subject_id,),
        predicate="other:traveled",
        evaluated_at=_NOW,
    )
    wrong_filter = engine.default_fact_context(
        deployment_id=_DEPLOYMENT_ID,
        graph_queries=cast(Any, graph),
        query="Where did Alice travel?",
        entity_ids=(corpus.subject_id,),
        predicate="reports_to",
        evaluated_at=_NOW,
    )

    assert tuple(fact.fact_id for fact in answer.facts) == (corpus.dynamic_relation_id,)
    assert graph.calls[0]["predicates"] == ("other:traveled",)
    assert graph.calls[1]["predicates"] == ("reports_to",)
    assert wrong_filter.facts == ()


def test_default_fact_context_reports_missing_graph_without_widening(
    corpus: _Corpus,
) -> None:
    """No graph dependency is a boundary before P1 nomination or embedding."""
    engine, index = corpus.query_engine(fact_ids=(corpus.relation_id,))
    embedded_before = len(corpus.provider.embedded_texts)

    answer = engine.default_fact_context(
        deployment_id=_DEPLOYMENT_ID,
        graph_queries=None,
        query="Where does Alice work?",
        entity_ids=(corpus.subject_id,),
        evaluated_at=_NOW,
    )

    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.BOUNDARY
    assert answer.facts == ()
    assert index.requested_k == []
    assert len(corpus.provider.embedded_texts) == embedded_before


def test_default_fact_context_discloses_the_combined_entity_cap(
    corpus: _Corpus,
) -> None:
    """A large neighborhood returns 19 nodes beside one anchor and marks loss."""
    neighbor_ids = tuple(uuid4() for _ in range(25))
    document_ids = tuple(uuid4() for _ in neighbor_ids)
    with corpus.engine.begin() as connection:
        for index, (entity_id, document_id) in enumerate(
            zip(neighbor_ids, document_ids, strict=True)
        ):
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id, canonical_name,"
                    " normalized_name) VALUES (:entity, :deployment, :name, :name)"
                ),
                {
                    "entity": entity_id,
                    "deployment": _DEPLOYMENT_ID,
                    "name": f"neighbor-{index}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                    " source_ref, document_entity_id, title) VALUES (:doc, :deployment,"
                    " 'upload', :ref, :entity, :title)"
                ),
                {
                    "doc": document_id,
                    "deployment": _DEPLOYMENT_ID,
                    "ref": f"batch-c-neighbor-{index}",
                    "entity": entity_id,
                    "title": f"Neighbor {index}",
                },
            )
    try:
        engine, _index = corpus.query_engine(fact_ids=())
        graph = _GraphNeighborhood(neighbor_ids=neighbor_ids)
        answer = engine.default_fact_context(
            deployment_id=_DEPLOYMENT_ID,
            graph_queries=cast(Any, graph),
            query="neighbor context",
            entity_ids=(corpus.subject_id,),
            evaluated_at=_NOW,
        )

        assert len(answer.nodes) == 19
        assert answer.truncation is not None
        assert answer.truncation.truncated
        assert answer.truncation.returned == 19
        assert answer.truncation.estimated_total == 25
        assert not answer.truncation.total_is_exact
    finally:
        with corpus.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM documents WHERE doc_id = ANY(:ids)"),
                {"ids": list(document_ids)},
            )
            connection.execute(
                text("DELETE FROM entities WHERE entity_id = ANY(:ids)"),
                {"ids": list(neighbor_ids)},
            )


def test_fact_context_uses_profile_text_to_rescue_list_queries(corpus: _Corpus) -> None:
    """“List banks” can nominate Acme's profile, then its bank observation."""
    index = _ProfileRescueIndex(
        entity_id=corpus.object_id, fact_id=corpus.bank_observation_id
    )
    engine = QueryEngine(
        engine=corpus.engine,
        search_index=index,
        model_provider=corpus.provider,
        embedding_model="batch-c",
        fact_read_pool=corpus.read_pool,
    )
    embedded_before = len(corpus.provider.embedded_texts)

    answer = engine.fact_context(
        deployment_id=_DEPLOYMENT_ID, query="list banks", evaluated_at=_NOW
    )

    assert tuple(fact.fact_id for fact in answer.facts) == (corpus.bank_observation_id,)
    assert index.profile_requests == 1
    assert index.requested_k == [
        FACT_CONTEXT_CANDIDATE_K + 1,
        FACT_CONTEXT_CANDIDATE_K + 1,
    ]
    assert len(index.requested_deadlines) == 2
    assert index.requested_deadlines[0] == index.requested_deadlines[1]
    assert index.profile_deadlines == [index.requested_deadlines[0]]
    assert len(corpus.provider.embedded_texts) == embedded_before + 1


def test_fact_context_falls_back_while_profile_channel_is_unpublished(
    corpus: _Corpus,
) -> None:
    """Profile backfill cannot turn ordinary deployment-wide facts into a 500."""
    index = _UnavailableProfileIndex(fact_keys=(("relation", corpus.relation_id),))
    engine = QueryEngine(
        engine=corpus.engine,
        search_index=index,
        model_provider=corpus.provider,
        embedding_model="batch-c",
        fact_read_pool=corpus.read_pool,
    )

    answer = engine.fact_context(
        deployment_id=_DEPLOYMENT_ID, query="Alice employment", evaluated_at=_NOW
    )

    assert tuple(fact.fact_id for fact in answer.facts) == (corpus.relation_id,)


def test_profile_rescue_rrf_counts_each_fact_once_per_channel() -> None:
    """A malformed duplicate nomination cannot forge cross-channel agreement."""
    fact_id = uuid4()
    fused = query_engine_module._fuse_fact_nominations(  # noqa: SLF001
        channels=(
            (
                P1Nomination(
                    item_id=str(fact_id),
                    rank=1,
                    score=0.9,
                    channel="semantic",
                    qualifier="observation",
                ),
                P1Nomination(
                    item_id=str(fact_id),
                    rank=2,
                    score=0.8,
                    channel="semantic",
                    qualifier="observation",
                ),
            ),
        ),
        limit=2,
    )

    assert len(fused) == 1
    assert fused[0].score == pytest.approx(1.0 / 61.0)


def test_fact_context_keeps_evidence_separate_across_kind_id_collisions(
    corpus: _Corpus,
) -> None:
    """Relations and observations with one UUID retain distinct evidence."""
    engine, _index = corpus.query_engine_for_keys(
        fact_keys=(
            ("relation", corpus.kind_collision_id),
            ("observation", corpus.kind_collision_id),
        )
    )
    answer = engine.fact_context(
        deployment_id=_DEPLOYMENT_ID,
        query="shared identity",
        k=2,
        evidence_per_fact=1,
        evaluated_at=_NOW,
    )

    assert {(fact.kind, fact.fact_id) for fact in answer.facts} == {
        ("relation", corpus.kind_collision_id),
        ("observation", corpus.kind_collision_id),
    }
    assert {
        (link.fact_kind, link.fact_id, link.stance, link.claim_id)
        for link in answer.fact_evidence
    } == {
        (
            "relation",
            corpus.kind_collision_id,
            "supports",
            corpus.claims["kind-collision-relation-support"],
        ),
        (
            "observation",
            corpus.kind_collision_id,
            "contradicts",
            corpus.claims["kind-collision-observation-contradict"],
        ),
    }
    assert {
        (total.fact_kind, total.stance): (total.returned, total.total)
        for total in answer.evidence_totals
    } == {
        ("relation", "supports"): (1, 1),
        ("relation", "contradicts"): (0, 0),
        ("observation", "supports"): (0, 0),
        ("observation", "contradicts"): (1, 1),
    }


@pytest.mark.parametrize(
    ("k", "evidence_per_fact", "message"),
    (
        (0, 1, "k must"),
        (31, 1, "k must"),
        (1, 0, "evidence_per_fact"),
        (1, 6, "evidence_per_fact"),
    ),
)
def test_fact_context_enforces_public_bounds(
    corpus: _Corpus, k: int, evidence_per_fact: int, message: str
) -> None:
    engine, _index = corpus.query_engine(fact_ids=(corpus.relation_id,))
    with pytest.raises(ValueError, match=message):
        engine.fact_context(
            deployment_id=_DEPLOYMENT_ID,
            query="Alice",
            k=k,
            evidence_per_fact=evidence_per_fact,
        )


def test_fact_context_respects_the_60_record_budget(corpus: _Corpus) -> None:
    engine, _index = corpus.query_engine(fact_ids=tuple(corpus.budget_fact_ids))
    answer = engine.fact_context(
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
    answer = engine.fact_context(
        deployment_id=_DEPLOYMENT_ID, query="Alice employment", k=1, evidence_per_fact=1
    )
    totals = {
        total.stance: (total.returned, total.total) for total in answer.evidence_totals
    }

    assert totals == {"supports": (1, 2), "contradicts": (1, 2)}


@pytest.mark.parametrize(
    ("time", "expected", "mode"),
    (
        (CurrentFactTime(), (), "current"),
        (AtFactTime(at=_NOW - timedelta(days=3)), ("ended",), "at"),
        (
            OverlapFactTime.model_validate(
                {
                    "mode": "overlap",
                    "from": _NOW - timedelta(days=4),
                    "to": _NOW - timedelta(days=1),
                }
            ),
            ("ended",),
            "overlap",
        ),
        (HistoryFactTime(), ("ended",), "history"),
        (AtFactTime(at=_NOW + timedelta(days=3)), ("future",), "at"),
    ),
)
def test_fact_context_time_modes_apply_world_and_belief_time(
    corpus: _Corpus, time: FactTime, expected: tuple[str, ...], mode: str
) -> None:
    """Ended/future intervals are selectable; retracted facts never return."""
    ids = {
        "ended": corpus.ended_id,
        "future": corpus.future_id,
        "invalidated": corpus.invalidated_id,
    }
    engine, _index = corpus.query_engine(fact_ids=tuple(ids.values()))
    answer = engine.fact_context(
        deployment_id=_DEPLOYMENT_ID,
        query="project assignments over time",
        k=3,
        evidence_per_fact=1,
        time=time,
        evaluated_at=_NOW,
    )

    assert answer.temporal_scope.mode == mode
    assert tuple(fact.fact_id for fact in answer.facts) == tuple(
        ids[name] for name in expected
    )
    assert corpus.invalidated_id not in {fact.fact_id for fact in answer.facts}


def test_current_fact_context_uses_the_disclosed_half_open_boundary(
    corpus: _Corpus,
) -> None:
    """A fact ending exactly at evaluated_at is outside current membership."""
    engine, _index = corpus.query_engine(fact_ids=(corpus.ended_id,))
    answer = engine.fact_context(
        deployment_id=_DEPLOYMENT_ID,
        query="boundary assignment",
        time=CurrentFactTime(),
        evaluated_at=_NOW - timedelta(days=2),
    )

    assert answer.temporal_scope.evaluated_at == _NOW - timedelta(days=2)
    assert answer.facts == ()


def test_withdrawn_fact_keeps_historical_provenance_and_zero_live_totals(
    corpus: _Corpus,
) -> None:
    """D54 flags a historically backed fact instead of making it disappear."""
    engine, _index = corpus.query_engine(fact_ids=(corpus.withdrawn_id,))
    answer = engine.fact_context(
        deployment_id=_DEPLOYMENT_ID, query="legacy project", evaluated_at=_NOW
    )

    assert tuple(fact.fact_id for fact in answer.facts) == (corpus.withdrawn_id,)
    assert answer.facts[0].support is FactSupport.WITHDRAWN
    assert answer.evidence == ()
    assert answer.fact_evidence == ()
    assert {
        total.stance: (total.returned, total.total) for total in answer.evidence_totals
    } == {"supports": (0, 0), "contradicts": (0, 0)}


def test_fact_context_rejects_unknown_entity_ids_without_partial_results(
    corpus: _Corpus,
) -> None:
    """One unavailable anchor makes the whole scoped fact response opaque."""
    engine, index = corpus.query_engine(fact_ids=(corpus.relation_id,))
    answer = engine.fact_context(
        deployment_id=_DEPLOYMENT_ID,
        query="Alice employment",
        entity_ids=(corpus.subject_id, uuid4()),
        evaluated_at=_NOW,
    )

    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.UNKNOWN_ENTITY
    assert answer.facts == ()
    assert index.requested_k == []


def test_fact_context_orders_multi_anchor_coverage_before_relevance(
    corpus: _Corpus,
) -> None:
    """A two-anchor fact outranks a semantically earlier one-anchor fact."""
    one_anchor = corpus.budget_fact_ids[0]
    engine, _index = corpus.query_engine(fact_ids=(one_anchor, corpus.relation_id))
    answer = engine.fact_context(
        deployment_id=_DEPLOYMENT_ID,
        query="Alice and Acme",
        entity_ids=(corpus.subject_id, corpus.object_id),
        k=2,
        evidence_per_fact=1,
        evaluated_at=_NOW,
    )

    assert tuple(fact.fact_id for fact in answer.facts) == (
        corpus.relation_id,
        one_anchor,
    )


def test_tombstoned_and_noncurrent_evidence_is_excluded(corpus: _Corpus) -> None:
    engine, _index = corpus.query_engine(fact_ids=(corpus.relation_id,))
    answer = engine.fact_context(
        deployment_id=_DEPLOYMENT_ID, query="Alice employment", k=1, evidence_per_fact=5
    )
    returned = {claim.claim_id for claim in answer.evidence}

    assert corpus.claims["tombstoned-support"] not in returned
    assert corpus.claims["noncurrent-contradict"] not in returned
    assert {(total.stance, total.total) for total in answer.evidence_totals} == {
        ("supports", 2),
        ("contradicts", 2),
    }


def test_unbacked_fact_is_dropped_and_never_returned_without_evidence(
    corpus: _Corpus,
) -> None:
    engine, _index = corpus.query_engine(fact_ids=(corpus.unbacked_id,))
    answer = engine.fact_context(
        deployment_id=_DEPLOYMENT_ID, query="unbacked relationship"
    )

    assert not answer.facts
    assert not answer.evidence
    assert answer.dropped_by_hydration == 1
    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.KNOWN_EMPTY


def test_fact_context_refills_a_dropped_head_candidate(corpus: _Corpus) -> None:
    """The final k applies after confirmation, so a stale head does not waste it."""
    engine, _index = corpus.query_engine(
        fact_ids=(corpus.unbacked_id, corpus.relation_id)
    )
    answer = engine.fact_context(
        deployment_id=_DEPLOYMENT_ID,
        query="employment after a stale nomination",
        k=1,
        evidence_per_fact=1,
    )

    assert tuple(fact.fact_id for fact in answer.facts) == (corpus.relation_id,)
    assert answer.dropped_by_hydration == 1


def test_fact_context_refill_crosses_the_default_confirmation_batch(
    corpus: _Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One stale row cannot prevent a full page at the 16-row boundary."""
    original_confirm = query_engine_module._confirm_fact_context
    confirmed_batch_sizes: list[int] = []

    def observed_confirm(**kwargs: Any) -> tuple[Any, ...]:
        """Record confirmation batch sizes while preserving the real authority."""
        candidate_keys = kwargs["candidate_keys"]
        confirmed_batch_sizes.append(len(candidate_keys))
        return original_confirm(**kwargs)

    monkeypatch.setattr(query_engine_module, "_confirm_fact_context", observed_confirm)
    engine, _index = corpus.query_engine(
        fact_ids=(corpus.unbacked_id, corpus.ended_id, *corpus.budget_fact_ids[:16])
    )
    answer = engine.fact_context(
        deployment_id=_DEPLOYMENT_ID,
        query="budget facts after one stale nomination",
        k=15,
        evidence_per_fact=1,
    )

    assert len(answer.facts) == 15
    assert answer.dropped_by_hydration == 2
    assert confirmed_batch_sizes == [16, 2]
    assert answer.truncation is not None
    assert answer.truncation.truncated
    assert answer.truncation.estimated_total == 16
    assert answer.truncation.total_is_exact


def test_no_results_uses_the_query_driven_known_empty_convention(
    corpus: _Corpus,
) -> None:
    engine, _index = corpus.query_engine(fact_ids=())
    answer = engine.fact_context(
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
    answer = engine.fact_context(
        deployment_id=_DEPLOYMENT_ID, query="Alice context", k=2, evidence_per_fact=1
    )
    evidence_ids = {claim.claim_id for claim in answer.evidence}
    fact_ids = {fact.fact_id for fact in answer.facts}

    assert all(link.claim_id in evidence_ids for link in answer.fact_evidence)
    assert all(link.fact_id in fact_ids for link in answer.fact_evidence)
    assert len(answer.evidence_totals) == 2 * len(answer.facts)


def test_fact_nomination_k_probe_discloses_truncation(corpus: _Corpus) -> None:
    engine, _index = corpus.query_engine(
        fact_ids=(corpus.relation_id, corpus.observation_id)
    )
    answer = engine.fact_context(
        deployment_id=_DEPLOYMENT_ID, query="Alice", k=1, evidence_per_fact=1
    )

    assert answer.truncation is not None
    assert answer.truncation.truncated
    assert answer.truncation.estimated_total == 2
    assert answer.truncation.total_is_exact
