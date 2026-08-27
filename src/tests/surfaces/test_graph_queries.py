"""Acceptance proofs for live PostgreSQL 19 graph queries.

The corpus is authority data only: there is no graph export, rebuild, snapshot,
or cache. One- and two-hop reads exercise SQL/PGQ; deeper shortest-tier entity
and directed citation paths exercise the bounded recursive helpers.
"""

from collections.abc import Iterable
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
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
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError

from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import NegativeKind
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.postgres_graph_sql import HISTORY_NEIGHBORHOOD_GUARD
from rememberstack.spine.postgres_graph_sql import HISTORY_NEIGHBORHOOD_PGQ
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import GraphQueries
import rememberstack.surfaces.graph_queries as graph_queries_module

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("43000000-0000-0000-0000-000000000001")
_OTHER_DEPLOYMENT_ID = UUID("43000000-0000-0000-0000-000000000002")
_JAN_2024 = datetime(2024, 1, 1, tzinfo=UTC)
_JUN_2024 = datetime(2024, 6, 1, tzinfo=UTC)
_JAN_2026 = datetime(2026, 1, 1, tzinfo=UTC)
_JAN_2027 = datetime(2027, 1, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose a PostgreSQL 19 engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for graph proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


class _GraphCorpus:
    """People, projects, organizations, and a citation chain."""

    def __init__(self, *, engine: Engine) -> None:
        """Insert a compact authority corpus with current and historical edges."""
        entities = (
            "Alice",
            "Bob",
            "Carol",
            "Acme",
            "Beacon",
            "ESB Migration",
            "Vector Databases",
        ) + tuple(f"Hub Leaf {index:02d}" for index in range(20))
        self.ids = {name: uuid4() for name in entities}
        self.docs: dict[str, UUID] = {}
        self.relations: dict[tuple[str, str, str], UUID] = {}
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO predicates (deployment_id, predicate,"
                    " parent_predicate, description, tier) VALUES"
                    " (:deployment_id, 'connected_to', 'related_to',"
                    " 'A bounded graph-fixture connection.', 'extension')"
                ),
                {"deployment_id": _DEPLOYMENT_ID},
            )
            for name in entities:
                connection.execute(
                    text(
                        "INSERT INTO entities (entity_id, deployment_id,"
                        " canonical_name, normalized_name)"
                        " VALUES (:entity_id, :deployment_id, :name, lower(:name))"
                    ),
                    {
                        "entity_id": self.ids[name],
                        "deployment_id": _DEPLOYMENT_ID,
                        "name": name,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                        " source_ref, document_entity_id, title) VALUES"
                        " (:doc_id, :deployment_id, 'upload', :source_ref,"
                        " :entity_id, :title)"
                    ),
                    {
                        "doc_id": uuid4(),
                        "deployment_id": _DEPLOYMENT_ID,
                        "source_ref": f"graph-entity-{self.ids[name]}",
                        "entity_id": self.ids[name],
                        "title": name,
                    },
                )
            evidence_doc, evidence_claim = self._seed_claim(connection=connection)
            self.evidence_doc = evidence_doc
            for subject, predicate, obj in (
                ("Alice", "works_for", "Acme"),
                ("Bob", "works_for", "Acme"),
                ("Alice", "works_on", "Beacon"),
                ("Carol", "works_on", "ESB Migration"),
                ("Beacon", "part_of", "ESB Migration"),
                ("Bob", "knows_about", "Vector Databases"),
            ):
                relation_id = self._seed_edge(
                    connection=connection,
                    evidence_doc=evidence_doc,
                    evidence_claim=evidence_claim,
                    subject=subject,
                    predicate=predicate,
                    obj=obj,
                )
                self.relations[(subject, predicate, obj)] = relation_id
            for leaf in (name for name in entities if name.startswith("Hub Leaf")):
                self._seed_edge(
                    connection=connection,
                    evidence_doc=evidence_doc,
                    evidence_claim=evidence_claim,
                    subject="Acme",
                    predicate="connected_to",
                    obj=leaf,
                )
            self._seed_edge(
                connection=connection,
                evidence_doc=evidence_doc,
                evidence_claim=evidence_claim,
                subject="Carol",
                predicate="works_for",
                obj="Acme",
                valid_from=_JAN_2024,
                valid_until=_JUN_2024,
            )
            for title in ("Report", "Follow-up", "Original Spec"):
                doc_id = uuid4()
                self.docs[title] = doc_id
                connection.execute(
                    text(
                        "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                        " source_ref, title) VALUES (:doc_id, :deployment_id,"
                        " 'upload', :source_ref, :title)"
                    ),
                    {
                        "doc_id": doc_id,
                        "deployment_id": _DEPLOYMENT_ID,
                        "source_ref": title.lower().replace(" ", "-"),
                        "title": title,
                    },
                )
            for citing, cited in (
                ("Report", "Follow-up"),
                ("Follow-up", "Original Spec"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO document_crossrefs (crossref_id, deployment_id,"
                        " from_doc_id, to_doc_id, kind, resolved)"
                        " VALUES (:crossref_id, :deployment_id, :from_doc_id,"
                        " :to_doc_id, 'cites', true)"
                    ),
                    {
                        "crossref_id": uuid4(),
                        "deployment_id": _DEPLOYMENT_ID,
                        "from_doc_id": self.docs[citing],
                        "to_doc_id": self.docs[cited],
                    },
                )

    def _seed_claim(self, *, connection: Connection) -> tuple[UUID, UUID]:
        """Create one complete live evidence coordinate for all relation facts."""
        doc_id = uuid4()
        version_id = uuid4()
        representation_id = uuid4()
        chunk_id = uuid4()
        claim_id = uuid4()
        content_hash = f"graph-evidence-{doc_id}"
        connection.execute(
            text(
                "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                " source_ref, title) VALUES (:doc_id, :deployment_id, 'upload',"
                " :source_ref, 'Evidence')"
            ),
            {
                "doc_id": doc_id,
                "deployment_id": _DEPLOYMENT_ID,
                "source_ref": content_hash,
            },
        )
        connection.execute(
            text(
                "INSERT INTO content_objects (deployment_id, content_hash, mime,"
                " raw_uri) VALUES (:deployment_id, :content_hash, 'text/plain',"
                " :raw_uri)"
            ),
            {
                "deployment_id": _DEPLOYMENT_ID,
                "content_hash": content_hash,
                "raw_uri": f"mem://{content_hash}",
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_versions (version_id, deployment_id, doc_id,"
                " content_hash, version_no, status) VALUES (:version_id,"
                " :deployment_id, :doc_id, :content_hash, 1, 'ready')"
            ),
            {
                "version_id": version_id,
                "deployment_id": _DEPLOYMENT_ID,
                "doc_id": doc_id,
                "content_hash": content_hash,
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_representations (representation_id,"
                " deployment_id, version_id, route, status) VALUES"
                " (:representation_id, :deployment_id, :version_id,"
                " 'passthrough', 'ready')"
            ),
            {
                "representation_id": representation_id,
                "deployment_id": _DEPLOYMENT_ID,
                "version_id": version_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,"
                " representation_id, ordinal, block_start, block_end,"
                " chunk_content_hash, extraction_input_hash, char_start, char_end)"
                " VALUES (:chunk_id, :deployment_id, :doc_id, :version_id,"
                " :representation_id, 0, 0, 0, :content_hash, :content_hash, 0, 8)"
            ),
            {
                "chunk_id": chunk_id,
                "deployment_id": _DEPLOYMENT_ID,
                "doc_id": doc_id,
                "version_id": version_id,
                "representation_id": representation_id,
                "content_hash": content_hash,
            },
        )
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, extractor_version) VALUES (:claim_id,"
                " :deployment_id, :doc_id, :chunk_id, 'evidence', 'evidence',"
                " 0, 8, true, true, 'graph-test')"
            ),
            {
                "claim_id": claim_id,
                "deployment_id": _DEPLOYMENT_ID,
                "doc_id": doc_id,
                "chunk_id": chunk_id,
            },
        )
        return doc_id, claim_id

    def _seed_edge(
        self,
        *,
        connection: Connection,
        evidence_doc: UUID,
        evidence_claim: UUID,
        subject: str,
        predicate: str,
        obj: str,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
    ) -> UUID:
        """Create one supported relation with optional world-time bounds."""
        relation_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id,"
                " normalizer_version, fact_label, evidence_count, valid_from,"
                " valid_until) VALUES (:relation_id, :deployment_id,"
                " :subject_entity_id, :predicate, :object_entity_id, 'toy',"
                " :fact_label, 1, :valid_from, :valid_until)"
            ),
            {
                "relation_id": relation_id,
                "deployment_id": _DEPLOYMENT_ID,
                "subject_entity_id": self.ids[subject],
                "predicate": predicate,
                "object_entity_id": self.ids[obj],
                "fact_label": f"{subject} {predicate} {obj}",
                "valid_from": valid_from,
                "valid_until": valid_until,
            },
        )
        connection.execute(
            text(
                "INSERT INTO relation_evidence (deployment_id, relation_id,"
                " claim_id, doc_id, stance, normalizer_version) VALUES"
                " (:deployment_id, :relation_id, :claim_id, :doc_id,"
                " 'supports', 'toy')"
            ),
            {
                "deployment_id": _DEPLOYMENT_ID,
                "relation_id": relation_id,
                "claim_id": evidence_claim,
                "doc_id": evidence_doc,
            },
        )
        return relation_id


@pytest.fixture()
def graph(database_engine: Engine) -> Iterator[GraphQueries]:
    """Expose authority rows directly through the live graph facade."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    for deployment_id, slug in (
        (_DEPLOYMENT_ID, "graph-query-test"),
        (_OTHER_DEPLOYMENT_ID, "graph-query-other"),
    ):
        DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
            deployment_input=DeploymentBootstrapInput(
                deployment_id=deployment_id,
                slug=slug,
                name=slug,
                default_language="en",
                raw_bucket="mem://raw",
                artifacts_bucket="mem://artifacts",
                corpusfs_bucket="mem://corpusfs",
            )
        )
    corpus = _GraphCorpus(engine=database_engine)
    queries = GraphQueries(engine=database_engine, deployment_id=_DEPLOYMENT_ID)
    queries.ids = corpus.ids  # type: ignore[attr-defined]
    queries.docs = corpus.docs  # type: ignore[attr-defined]
    queries.evidence_doc = corpus.evidence_doc  # type: ignore[attr-defined]
    queries.relations = corpus.relations  # type: ignore[attr-defined]
    yield queries


def _names(envelope: object) -> set[str]:
    """Return the names carried by an envelope's nodes."""
    return {node.name for node in envelope.nodes}  # type: ignore[attr-defined]


def _path_rows(rows: Iterable[RowMapping]) -> list[tuple[object, object, object]]:
    """Return comparable path tuples from mapped graph rows."""
    return [
        (row["hops"], tuple(row["relation_ids"]), tuple(row["node_ids"]))
        for row in rows
        if row["row_kind"] == "data"
    ]


def test_sql_pgq_neighborhood_is_live_and_paginates(graph: GraphQueries) -> None:
    """A one-hop PGQ read sees authority immediately and discloses paging."""
    ids = graph.ids  # type: ignore[attr-defined]
    full = graph.neighborhood(entity_id=ids["Acme"], hops=1)
    assert {"Alice", "Bob"} <= _names(full)
    assert full.freshness.pg_live_ts is not None
    assert full.truncation is not None and full.truncation.truncated is False
    first = graph.neighborhood(entity_id=ids["Acme"], hops=1, limit=1)
    assert first.truncation is not None and first.truncation.continuation is not None
    assert first.truncation.truncated is True
    assert first.truncation.reason == "result_budget"
    second = graph.neighborhood(
        entity_id=ids["Acme"],
        hops=1,
        limit=1,
        continuation=first.truncation.continuation,
    )
    assert _names(first).isdisjoint(_names(second))


def test_tombstoned_relation_evidence_disappears_and_restores_live(
    graph: GraphQueries, database_engine: Engine
) -> None:
    """A relation never outlives its last live evidence document."""
    evidence_doc = graph.evidence_doc  # type: ignore[attr-defined]
    relation_id = graph.relations[("Alice", "works_for", "Acme")]  # type: ignore[attr-defined]
    with database_engine.begin() as connection:
        before = connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM "
                "rememberstack_graph_internal.relations_history "
                "WHERE deployment_id = :deployment_id "
                "AND relation_id = :relation_id)"
            ),
            {"deployment_id": _DEPLOYMENT_ID, "relation_id": relation_id},
        ).scalar_one()
        assert before is True
        connection.execute(
            text(
                "UPDATE documents SET deleted_at = now() "
                "WHERE deployment_id = :deployment_id AND doc_id = :doc_id"
            ),
            {"deployment_id": _DEPLOYMENT_ID, "doc_id": evidence_doc},
        )
    with database_engine.connect() as connection:
        private_present = connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM "
                "rememberstack_graph_internal.relations_history "
                "WHERE deployment_id = :deployment_id "
                "AND relation_id = :relation_id)"
            ),
            {"deployment_id": _DEPLOYMENT_ID, "relation_id": relation_id},
        ).scalar_one()
        public_present = connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM memory_v1.graph_edges_visible_history "
                "WHERE deployment_id = :deployment_id "
                "AND relation_id = :relation_id)"
            ),
            {"deployment_id": _DEPLOYMENT_ID, "relation_id": relation_id},
        ).scalar_one()
    assert private_present is public_present is False
    absent = graph.neighborhood(entity_id=graph.ids["Acme"], hops=1)  # type: ignore[attr-defined]
    assert absent.negative is not None
    assert absent.negative.kind is NegativeKind.KNOWN_EMPTY

    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE documents SET deleted_at = NULL "
                "WHERE deployment_id = :deployment_id AND doc_id = :doc_id"
            ),
            {"deployment_id": _DEPLOYMENT_ID, "doc_id": evidence_doc},
        )
    restored = graph.neighborhood(entity_id=graph.ids["Acme"], hops=1)  # type: ignore[attr-defined]
    assert "Alice" in _names(restored)


def test_tombstoned_crossref_endpoint_disappears_and_restores_live(
    graph: GraphQueries, database_engine: Engine
) -> None:
    """Citation traversal uses no stale edge after either endpoint tombstones."""
    docs = graph.docs  # type: ignore[attr-defined]
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE documents SET deleted_at = now() "
                "WHERE deployment_id = :deployment_id AND doc_id = :doc_id"
            ),
            {"deployment_id": _DEPLOYMENT_ID, "doc_id": docs["Follow-up"]},
        )
    missing = graph.citation_path(
        from_doc_id=docs["Report"], to_doc_id=docs["Original Spec"]
    )
    assert missing.negative is not None
    assert missing.negative.kind is NegativeKind.KNOWN_EMPTY
    with database_engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) = 0 FROM "
                "rememberstack_graph_internal.crossrefs_live "
                "WHERE deployment_id = :deployment_id"
            ),
            {"deployment_id": _DEPLOYMENT_ID},
        ).scalar_one()

    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE documents SET deleted_at = NULL "
                "WHERE deployment_id = :deployment_id AND doc_id = :doc_id"
            ),
            {"deployment_id": _DEPLOYMENT_ID, "doc_id": docs["Follow-up"]},
        )
    restored = graph.citation_path(
        from_doc_id=docs["Report"], to_doc_id=docs["Original Spec"]
    )
    assert restored.negative is None and restored.paths[0].length == 2


def test_property_graph_element_keys_are_unique(
    graph: GraphQueries, database_engine: Engine
) -> None:
    """Every declared property-graph KEY is unique on its source relation."""
    del graph
    key_contracts = (
        ("rememberstack_graph_internal.entities_live", "deployment_id, entity_id"),
        ("rememberstack_graph_internal.documents_live", "deployment_id, doc_id"),
        (
            "rememberstack_graph_internal.relations_current",
            "deployment_id, relation_id",
        ),
        (
            "rememberstack_graph_internal.relations_history",
            "deployment_id, relation_id",
        ),
        ("rememberstack_graph_internal.crossrefs_live", "deployment_id, crossref_id"),
        ("memory_v1.entity_document_mentions", "deployment_id, entity_id, doc_id"),
    )
    with database_engine.connect() as connection:
        for relation, keys in key_contracts:
            duplicates = connection.exec_driver_sql(
                f"SELECT count(*) FROM (SELECT {keys}, count(*) "
                f"FROM {relation} GROUP BY {keys} HAVING count(*) > 1) AS d"
            ).scalar_one()
            assert duplicates == 0, relation


def test_property_graph_owner_cannot_bypass_invoker_source_acl(
    graph: GraphQueries, database_engine: Engine
) -> None:
    """Revoking one source grant makes PGQ fail even though the graph owner can read."""
    del graph
    parameters = {
        "deployment_id": _DEPLOYMENT_ID,
        "anchor_id": uuid4(),
        "max_depth": 1,
        "predicates": None,
        "valid_at": _JAN_2027,
        "believed_at": _JAN_2027,
        "max_results": 1,
        "expansion_budget": 8,
        "result_offset": 0,
        "guard_examined_edges": 0,
    }
    with database_engine.connect() as connection:
        transaction = connection.begin()
        role = connection.execute(
            text("SELECT quote_ident('rememberstack_graph_' || current_database())")
        ).scalar_one()
        try:
            connection.exec_driver_sql(
                f"REVOKE SELECT ON rememberstack_graph_internal.relations_history "
                f"FROM {role}"
            )
            connection.exec_driver_sql(f"SET LOCAL ROLE {role}")
            with pytest.raises(DBAPIError):
                connection.execute(text(HISTORY_NEIGHBORHOOD_PGQ), parameters).all()
        finally:
            transaction.rollback()


def test_sql_pgq_matches_canonical_helper_under_budget(
    graph: GraphQueries, database_engine: Engine
) -> None:
    """One-hop PGQ and the canonical frontier return identical paths."""
    anchor_id = graph.ids["Acme"]  # type: ignore[attr-defined]
    with database_engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    ) as connection:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        operation_at = connection.execute(
            text("SELECT statement_timestamp()")
        ).scalar_one()
        parameters = {
            "deployment_id": _DEPLOYMENT_ID,
            "anchor_id": anchor_id,
            "max_depth": 1,
            "predicates": None,
            "valid_at": operation_at,
            "believed_at": operation_at,
            "max_results": 500,
            "expansion_budget": 2000,
            "frontier_budget": 1000,
            "time_budget_ms": 1000,
            "result_offset": 0,
        }
        pgq = graph_queries_module._shallow_neighborhood_rows(  # noqa: SLF001
            connection=connection, parameters=parameters
        )
        helper = (
            connection.execute(
                text(
                    "SELECT * FROM memory_v1.graph_neighborhood("
                    ":deployment_id, :anchor_id, :max_depth, :predicates, "
                    ":valid_at, :believed_at, :max_results, :expansion_budget, "
                    ":frontier_budget, :time_budget_ms)"
                ),
                parameters,
            )
            .mappings()
            .all()
        )
        connection.rollback()
    assert _path_rows(pgq) == _path_rows(helper)
    pgq_status = next(row for row in pgq if row["row_kind"] == "status")
    helper_status = next(row for row in helper if row["row_kind"] == "status")
    assert pgq_status["examined_edges"] == helper_status["examined_edges"]
    assert pgq_status["truncated"] == helper_status["truncated"] is False


def test_recursive_helper_null_depth_uses_default_and_truthful_data_status(
    graph: GraphQueries, database_engine: Engine
) -> None:
    """Explicit NULL depth stays valid and every broad-projection row agrees."""
    anchor_id = graph.ids["Acme"]  # type: ignore[attr-defined]
    with database_engine.connect() as connection:
        operation_at = connection.execute(
            text("SELECT statement_timestamp()")
        ).scalar_one()
        rows = (
            connection.execute(
                text(
                    "SELECT * FROM memory_v1.graph_neighborhood("
                    ":deployment_id, :anchor_id, NULL, NULL, :operation_at, "
                    ":operation_at, 100, 2000, 1000, 1000)"
                ),
                {
                    "deployment_id": _DEPLOYMENT_ID,
                    "anchor_id": anchor_id,
                    "operation_at": operation_at,
                },
            )
            .mappings()
            .all()
        )
    status = next(row for row in rows if row["row_kind"] == "status")
    data = [row for row in rows if row["row_kind"] == "data"]
    assert data
    assert status["effective_depth"] == 2
    assert status["truncated"] is False
    assert status["truncation_reason"] is None
    assert all(row["truncated"] == status["truncated"] for row in data)
    assert all(row["truncation_reason"] == status["truncation_reason"] for row in data)


def test_dense_hub_refuses_pgq_over_budget_and_helper_returns_whole_prefix(
    graph: GraphQueries, database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PGQ returns zero data after guard refusal; helper keeps a whole-path prefix."""
    anchor_id = graph.ids["Acme"]  # type: ignore[attr-defined]
    with database_engine.connect() as connection:
        operation_at = connection.execute(
            text("SELECT statement_timestamp()")
        ).scalar_one()
        parameters = {
            "deployment_id": _DEPLOYMENT_ID,
            "anchor_id": anchor_id,
            "max_depth": 1,
            "predicates": None,
            "valid_at": operation_at,
            "believed_at": operation_at,
            "max_results": 500,
            "expansion_budget": 1,
            "frontier_budget": 1000,
            "time_budget_ms": 1000,
            "result_offset": 0,
        }
        guard = (
            connection.execute(text(HISTORY_NEIGHBORHOOD_GUARD), parameters)
            .mappings()
            .one()
        )
        assert guard["admitted"] is False
        helper_statement = text(
            "SELECT * FROM memory_v1.graph_neighborhood("
            ":deployment_id, :anchor_id, :max_depth, :predicates, "
            ":valid_at, :believed_at, :max_results, :expansion_budget, "
            ":frontier_budget, :time_budget_ms)"
        )
        helper = connection.execute(helper_statement, parameters).mappings().all()
        repeated_helper = (
            connection.execute(helper_statement, parameters).mappings().all()
        )
    monkeypatch.setattr(graph_queries_module, "DEFAULT_EXPANSION_BUDGET", 1)
    refused = graph.neighborhood(entity_id=anchor_id, hops=1)
    assert refused.nodes == ()
    assert refused.negative is None
    assert refused.truncation is not None
    assert refused.truncation.truncated is True
    assert refused.truncation.reason == "expansion_budget"
    assert [row for row in helper if row["row_kind"] == "data"]
    assert _path_rows(helper) == _path_rows(repeated_helper)
    assert guard["truncated"] is True
    assert guard["truncation_reason"] == "expansion_budget"
    assert guard["examined_edges"] <= parameters["expansion_budget"]
    helper_status = next(row for row in helper if row["row_kind"] == "status")
    assert helper_status["truncated"] is True
    assert helper_status["truncation_reason"] == "expansion_budget"
    assert helper_status["examined_edges"] <= parameters["expansion_budget"]
    helper_data = [row for row in helper if row["row_kind"] == "data"]
    assert all(row["truncated"] is True for row in helper_data)
    assert all(row["truncation_reason"] == "expansion_budget" for row in helper_data)


def test_shallow_guard_plan_is_endpoint_anchored(
    graph: GraphQueries, database_engine: Engine
) -> None:
    """The guard stays tenant-indexed through survivor-resolved endpoints."""
    parameters = {
        "deployment_id": _DEPLOYMENT_ID,
        "anchor_id": graph.ids["Acme"],  # type: ignore[attr-defined]
        "max_depth": 2,
        "predicates": None,
        "valid_at": _JAN_2027,
        "believed_at": _JAN_2027,
        "expansion_budget": 2000,
        "frontier_budget": 1000,
    }
    with database_engine.connect() as connection:
        connection.exec_driver_sql("SET LOCAL enable_seqscan = off")
        plan = connection.execute(
            text(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + HISTORY_NEIGHBORHOOD_GUARD
            ),
            parameters,
        ).scalar_one()

    plan_text = str(plan)
    assert "ix_relations_block_subj" in plan_text
    assert "ix_relations_block_obj" in plan_text, plan_text
    assert not (
        "'Node Type': 'Seq Scan'" in plan_text
        and "'Relation Name': 'relations'" in plan_text
    )


def test_bounded_graph_read_transaction_does_not_starve_authority_write(
    graph: GraphQueries, database_engine: Engine
) -> None:
    """A concurrent authority update commits while a graph snapshot stays open."""
    identifier = graph.ids["Alice"]  # type: ignore[attr-defined]

    def write_authority() -> int:
        with database_engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL statement_timeout = '2s'")
            return connection.execute(
                text(
                    "UPDATE entities SET profile_summary = profile_summary "
                    "WHERE deployment_id = :deployment_id AND entity_id = :entity_id"
                ),
                {"deployment_id": _DEPLOYMENT_ID, "entity_id": identifier},
            ).rowcount

    with graph._transaction() as graph_connection:  # noqa: SLF001
        assert graph_connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM memory_v1.entities_current "
                "WHERE deployment_id = :deployment_id AND entity_id = :entity_id)"
            ),
            {"deployment_id": _DEPLOYMENT_ID, "entity_id": identifier},
        ).scalar_one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(write_authority).result(timeout=3) == 1


def test_recursive_shortest_tier_path_hydrates_stored_direction(
    graph: GraphQueries,
) -> None:
    """Deep helper returns a shortest tier while keeping factual edge direction."""
    ids = graph.ids  # type: ignore[attr-defined]
    connected = graph.path(
        from_entity_id=ids["Alice"], to_entity_id=ids["ESB Migration"]
    )
    assert connected.negative is None
    assert [node.name for node in connected.paths[0].nodes] == [
        "Alice",
        "Beacon",
        "ESB Migration",
    ]
    reverse = graph.path(
        from_entity_id=ids["Acme"], to_entity_id=ids["Alice"], max_hops=1
    )
    edge = reverse.paths[0].edges[0]
    assert edge.subject_id == ids["Alice"] and edge.object_id == ids["Acme"]


def test_predicates_and_paired_bitemporal_clocks_apply_during_expansion(
    graph: GraphQueries,
) -> None:
    """The traversal filters eligible edges before reachability is decided."""
    ids = graph.ids  # type: ignore[attr-defined]
    filtered = graph.neighborhood(
        entity_id=ids["ESB Migration"], hops=2, predicates=("works_on", "part_of")
    )
    assert {"Beacon", "Alice", "Carol"} <= _names(filtered)
    assert "Vector Databases" not in _names(filtered)
    historical = graph.neighborhood(
        entity_id=ids["Acme"],
        hops=1,
        valid_at=datetime(2024, 3, 1, tzinfo=UTC),
        believed_at=_JAN_2027,
    )
    current = graph.neighborhood(
        entity_id=ids["Acme"], hops=1, valid_at=_JAN_2026, believed_at=_JAN_2027
    )
    assert "Carol" in _names(historical) and "Carol" not in _names(current)
    assert historical.temporal_scope.believed_at == _JAN_2027
    with pytest.raises(ValueError, match="both clocks"):
        graph.neighborhood(entity_id=ids["Acme"], valid_at=_JAN_2026)


def test_recursive_citation_path_is_directed(graph: GraphQueries) -> None:
    """Document traversal follows stored citation direction over live rows."""
    docs = graph.docs  # type: ignore[attr-defined]
    chain = graph.citation_path(
        from_doc_id=docs["Report"], to_doc_id=docs["Original Spec"]
    )
    assert chain.negative is None and chain.paths[0].length == 2
    reverse = graph.citation_path(
        from_doc_id=docs["Original Spec"], to_doc_id=docs["Report"]
    )
    assert (
        reverse.negative is not None
        and reverse.negative.kind is NegativeKind.KNOWN_EMPTY
    )


def test_typed_absence_boundaries_and_hard_depth_limits(graph: GraphQueries) -> None:
    """Unknown, known-empty, foreign cursor, and invalid bounds stay distinct."""
    ids = graph.ids  # type: ignore[attr-defined]
    unknown = graph.neighborhood(entity_id=uuid4())
    assert (
        unknown.negative is not None
        and unknown.negative.kind is NegativeKind.UNKNOWN_ENTITY
    )
    empty = graph.neighborhood(
        entity_id=ids["Vector Databases"], hops=1, predicates=("works_for",)
    )
    assert (
        empty.negative is not None and empty.negative.kind is NegativeKind.KNOWN_EMPTY
    )
    foreign = graph.neighborhood(entity_id=ids["Acme"], continuation="snapshot-v1:1")
    assert (
        foreign.negative is not None and foreign.negative.kind is NegativeKind.BOUNDARY
    )
    with pytest.raises(ValueError, match="between 1 and 4"):
        graph.neighborhood(entity_id=ids["Acme"], hops=5)
    with pytest.raises(ValueError, match="between 1 and 6"):
        graph.path(
            from_entity_id=ids["Alice"], to_entity_id=ids["ESB Migration"], max_hops=7
        )


def test_graph_facade_never_crosses_deployments(
    graph: GraphQueries, database_engine: Engine
) -> None:
    """An existing entity owned by another deployment is unknown here."""
    foreign_id = uuid4()
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id, canonical_name,"
                " normalized_name) VALUES"
                " (:entity_id, :deployment_id, 'Foreign', 'foreign')"
            ),
            {"entity_id": foreign_id, "deployment_id": _OTHER_DEPLOYMENT_ID},
        )
    result = graph.neighborhood(entity_id=foreign_id)
    assert (
        result.negative is not None
        and result.negative.kind is NegativeKind.UNKNOWN_ENTITY
    )
