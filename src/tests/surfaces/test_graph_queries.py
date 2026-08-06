"""WP-4.3 acceptance: the `graph` primitive against S17–S22.

Each scenario class runs over a real rebuilt snapshot: S17 (how are A and
B connected), S18 (2-hop neighborhood with EXPLICIT truncation), S19
(predicate-constrained multi-hop), S20 (graph join across predicates), S21
(multi-hop as-of via inline path predicates), S22 (document-graph
transitive citation). The negatives are typed, and the caps are never
silent.
"""

from collections.abc import Iterator
from datetime import datetime
from datetime import UTC
from pathlib import Path
from typing import cast
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import NegativeKind
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import ProjectionCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import GraphQueries
import rememberstack.surfaces.graph_queries as graph_queries_module
from rememberstack.workers import GraphRebuildWorker
from rememberstack.workers import GraphSnapshotReader

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("43000000-0000-0000-0000-000000000001")
_JAN_2024 = datetime(2024, 1, 1, tzinfo=UTC)
_JUN_2024 = datetime(2024, 6, 1, tzinfo=UTC)
_JAN_2026 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for real graph query proofs"
        )
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


class _Graph:
    """The scenario corpus: people, projects, orgs, documents."""

    def __init__(self, *, engine: Engine) -> None:
        """Seed a graph rich enough for every S17–S22 shape."""
        self.engine = engine
        self.ids: dict[str, UUID] = {}
        with engine.begin() as connection:
            for name, entity_type in (
                ("Alice", "Person"),
                ("Bob", "Person"),
                ("Carol", "Person"),
                ("Acme", "Organization"),
                ("Beacon", "Project"),
                ("ESB Migration", "Project"),
                ("Vector Databases", "Concept"),
            ):
                entity_id = uuid4()
                self.ids[name] = entity_id
                connection.execute(
                    text(
                        "INSERT INTO entities (entity_id, deployment_id, type,"
                        " canonical_name, normalized_name)"
                        " VALUES (:e, :d, :t, :n, lower(:n))"
                    ),
                    {"e": entity_id, "d": _DEPLOYMENT_ID, "t": entity_type, "n": name},
                )
                connection.execute(
                    text(
                        "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                        " source_ref, document_entity_id, title)"
                        " VALUES (:doc, :d, 'upload', :ref, :e, :n)"
                    ),
                    {
                        "doc": uuid4(),
                        "d": _DEPLOYMENT_ID,
                        "ref": f"graph-entity-{entity_id}",
                        "e": entity_id,
                        "n": name,
                    },
                )
            self.evidence_doc, self.evidence_claim = self._live_claim(connection)
            # S17/S19/S20/S21 topology
            self._edge(connection, "Alice", "works_for", "Acme")
            self._edge(connection, "Bob", "works_for", "Acme")
            self._edge(connection, "Alice", "works_on", "Beacon")
            self._edge(connection, "Carol", "works_on", "ESB Migration")
            self._edge(connection, "Beacon", "part_of", "ESB Migration")
            self._edge(connection, "Bob", "knows_about", "Vector Databases")
            self.docs: dict[str, UUID] = {}
            for title in ("Report", "Follow-up", "Original Spec"):
                doc_id = uuid4()
                self.docs[title] = doc_id
                connection.execute(
                    text(
                        "INSERT INTO documents (doc_id, deployment_id,"
                        " source_kind, source_ref, title)"
                        " VALUES (:doc, :d, 'upload', :ref, :title)"
                    ),
                    {
                        "doc": doc_id,
                        "d": _DEPLOYMENT_ID,
                        "ref": title.lower().replace(" ", "-"),
                        "title": title,
                    },
                )
            for citing, cited in (
                ("Report", "Follow-up"),
                ("Follow-up", "Original Spec"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO document_crossrefs (crossref_id,"
                        " deployment_id, from_doc_id, to_doc_id, kind,"
                        " resolved) VALUES (:c, :d, :f, :t, 'cites', true)"
                    ),
                    {
                        "c": uuid4(),
                        "d": _DEPLOYMENT_ID,
                        "f": self.docs[citing],
                        "t": self.docs[cited],
                    },
                )
            # S21: an edge that only existed in 2024 (closed since)
            self._edge(
                connection,
                "Carol",
                "works_for",
                "Acme",
                valid_from=_JAN_2024,
                valid_until=_JUN_2024,
            )

    def _live_claim(self, connection: object) -> tuple[UUID, UUID]:
        """Seed one complete visible claim coordinate for the graph facts."""
        doc_id = uuid4()
        version_id = uuid4()
        representation_id = uuid4()
        chunk_id = uuid4()
        claim_id = uuid4()
        content_hash = f"graph-evidence-{doc_id}"
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                " source_ref, title) VALUES (:doc, :d, 'upload', :ref, 'Evidence')"
            ),
            {"doc": doc_id, "d": _DEPLOYMENT_ID, "ref": content_hash},
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO content_objects (deployment_id, content_hash, mime, raw_uri)"
                " VALUES (:d, :hash, 'text/plain', :uri)"
            ),
            {"d": _DEPLOYMENT_ID, "hash": content_hash, "uri": f"mem://{content_hash}"},
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO document_versions (version_id, deployment_id, doc_id,"
                " content_hash, version_no, status)"
                " VALUES (:version, :d, :doc, :hash, 1, 'ready')"
            ),
            {
                "version": version_id,
                "d": _DEPLOYMENT_ID,
                "doc": doc_id,
                "hash": content_hash,
            },
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO document_representations (representation_id, deployment_id,"
                " version_id, route, status) VALUES (:representation, :d, :version,"
                " 'passthrough', 'ready')"
            ),
            {
                "representation": representation_id,
                "d": _DEPLOYMENT_ID,
                "version": version_id,
            },
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,"
                " representation_id, ordinal, block_start, block_end,"
                " chunk_content_hash, extraction_input_hash, char_start, char_end)"
                " VALUES (:chunk, :d, :doc, :version, :representation, 0, 0, 0,"
                " :hash, :hash, 0, 8)"
            ),
            {
                "chunk": chunk_id,
                "d": _DEPLOYMENT_ID,
                "doc": doc_id,
                "version": version_id,
                "representation": representation_id,
                "hash": content_hash,
            },
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, extractor_version)"
                " VALUES (:claim, :d, :doc, :chunk, 'evidence', 'evidence', 0, 8,"
                " true, true, 'graph-test')"
            ),
            {"claim": claim_id, "d": _DEPLOYMENT_ID, "doc": doc_id, "chunk": chunk_id},
        )
        return doc_id, claim_id

    def _edge(
        self,
        connection: object,
        subject: str,
        predicate: str,
        obj: str,
        *,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
    ) -> UUID:
        """One relation row."""
        relation_id = uuid4()
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id,"
                " normalizer_version, fact_label, evidence_count,"
                " valid_from, valid_until)"
                " VALUES (:r, :d, :s, :p, :o, 'toy', :label, 2, :vf, :vu)"
            ),
            {
                "r": relation_id,
                "d": _DEPLOYMENT_ID,
                "s": self.ids[subject],
                "p": predicate,
                "o": self.ids[obj],
                "label": f"{subject} {predicate} {obj}",
                "vf": valid_from,
                "vu": valid_until,
            },
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO relation_evidence (deployment_id, relation_id, claim_id,"
                " doc_id, stance, normalizer_version)"
                " VALUES (:d, :r, :claim, :doc, 'supports', 'toy')"
            ),
            {
                "d": _DEPLOYMENT_ID,
                "r": relation_id,
                "claim": self.evidence_claim,
                "doc": self.evidence_doc,
            },
        )
        return relation_id


@pytest.fixture()
def graph(database_engine: Engine, tmp_path: Path) -> Iterator[GraphQueries]:
    """A rebuilt, published snapshot exposed through the graph primitive."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="graph-query-test",
            name="Graph query proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    corpus = _Graph(engine=database_engine)
    catalog = ProjectionCatalog(engine=database_engine)
    store = LocalFSObjectStore(root=tmp_path / "snapshots")
    GraphRebuildWorker(catalog=catalog, snapshot_store=store).rebuild(
        deployment_id=_DEPLOYMENT_ID, workdir=tmp_path / "work"
    )
    reader = GraphSnapshotReader(
        catalog=catalog,
        snapshot_store=store,
        deployment_id=_DEPLOYMENT_ID,
        cache_dir=tmp_path / "cache",
    )
    queries = GraphQueries(reader=reader)
    queries.ids = corpus.ids  # type: ignore[attr-defined]
    queries.docs = corpus.docs  # type: ignore[attr-defined]
    yield queries


def _names(envelope: object) -> set[str]:
    """The entity names an envelope's nodes carry."""
    return {node.name for node in envelope.nodes}  # type: ignore[attr-defined]


def test_s17_how_are_two_entities_connected(graph: GraphQueries) -> None:
    """S17: 'How are Alice and the Beacon project connected?' — a path with
    every traversed edge, returned as a unit."""
    ids = graph.ids  # type: ignore[attr-defined]
    envelope = graph.path(
        from_entity_id=ids["Alice"], to_entity_id=ids["ESB Migration"]
    )
    assert envelope.negative is None
    assert envelope.paths
    path = envelope.paths[0]
    assert path.length == 2  # Alice → Beacon → ESB Migration
    assert [node.name for node in path.nodes] == ["Alice", "Beacon", "ESB Migration"]
    assert [edge.predicate for edge in path.edges] == ["works_on", "part_of"]
    assert envelope.freshness.p2_snapshot_version is not None  # S42


def test_s18_neighborhood_caps_are_explicit(graph: GraphQueries) -> None:
    """S18: 'Everything within 2 hops of Acme' — and when the page caps, the
    truncation marker plus a continuation say so (never a silent top-k)."""
    ids = graph.ids  # type: ignore[attr-defined]
    full = graph.neighborhood(entity_id=ids["Acme"], hops=2)
    assert full.negative is None
    assert "Alice" in _names(full) and "Bob" in _names(full)
    assert full.truncation is not None
    assert full.truncation.truncated is False  # nothing hidden

    page = graph.neighborhood(entity_id=ids["Acme"], hops=2, limit=1)
    assert page.truncation is not None
    assert page.truncation.truncated is True
    assert page.truncation.returned == 1
    assert page.truncation.estimated_total > 1  # the real total, not limit+1
    assert page.truncation.total_is_exact is True
    assert page.truncation.continuation is not None
    nxt = graph.neighborhood(
        entity_id=ids["Acme"],
        hops=2,
        limit=1,
        continuation=page.truncation.continuation,
    )
    assert _names(nxt).isdisjoint(_names(page))  # pagination is stable


def test_hub_paging_continues_beyond_the_bounded_count_probe(
    graph: GraphQueries, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WP-5.6: COUNT_CAP limits metadata cost, never result reachability."""
    monkeypatch.setattr(graph_queries_module, "COUNT_CAP", 2)
    expected = _names(graph.neighborhood(entity_id=graph.ids["Acme"], hops=2))  # type: ignore[attr-defined]
    seen: set[str] = set()
    continuation: str | None = None
    while True:
        page = graph.neighborhood(
            entity_id=graph.ids["Acme"],  # type: ignore[attr-defined]
            hops=2,
            limit=1,
            continuation=continuation,
        )
        seen.update(_names(page))
        assert page.truncation is not None
        assert page.truncation.estimated_total >= len(seen)
        continuation = page.truncation.continuation
        if continuation is None:
            assert page.truncation.truncated is False
            break

    assert seen == expected
    assert len(seen) > graph_queries_module.COUNT_CAP


def test_s19_predicate_constrained_multi_hop(graph: GraphQueries) -> None:
    """S19: people on projects connected to the ESB migration — the
    traversal follows only the named predicates."""
    ids = graph.ids  # type: ignore[attr-defined]
    envelope = graph.neighborhood(
        entity_id=ids["ESB Migration"], hops=2, predicates=("works_on", "part_of")
    )
    reached = _names(envelope)
    assert {"Beacon", "Alice", "Carol"} <= reached
    assert "Vector Databases" not in reached  # knows_about was not requested


def test_s20_graph_join_across_predicates(graph: GraphQueries) -> None:
    """S20: 'Colleagues of Bob who know about vector databases' — the
    co-membership hop and the topic hop compose in one traversal."""
    ids = graph.ids  # type: ignore[attr-defined]
    colleagues = graph.neighborhood(
        entity_id=ids["Bob"], hops=2, predicates=("works_for",)
    )
    assert "Alice" in _names(colleagues)  # via Acme co-membership
    topic = graph.neighborhood(
        entity_id=ids["Vector Databases"], hops=1, predicates=("knows_about",)
    )
    assert _names(topic) == {"Bob"}


def test_s21_multi_hop_as_of(graph: GraphQueries) -> None:
    """S21: 'Who was connected to Acme as of 2024-06?' — the inline path
    predicate prunes DURING traversal, so a window closed since is visible
    then and invisible now."""
    ids = graph.ids  # type: ignore[attr-defined]
    historical = graph.neighborhood(
        entity_id=ids["Acme"], hops=1, valid_at=datetime(2024, 3, 1, tzinfo=UTC)
    )
    assert "Carol" in _names(historical)  # her spell was open in March 2024

    current = graph.neighborhood(entity_id=ids["Acme"], hops=1, valid_at=_JAN_2026)
    assert "Carol" not in _names(current)  # closed in June 2024
    assert {"Alice", "Bob"} <= _names(current)  # open-ended edges persist
    assert current.as_of_valid_at == _JAN_2026  # the echo (S15/S16)


def test_s22_document_citation_chain(graph: GraphQueries) -> None:
    """S22: 'Which documents ultimately cite the original spec?' — the
    DOCUMENT graph traverses transitively (Codex review: the entity graph
    cannot answer this; DOC_CROSSREF can)."""
    docs = graph.docs  # type: ignore[attr-defined]
    chain = graph.citation_path(
        from_doc_id=docs["Report"], to_doc_id=docs["Original Spec"]
    )
    assert chain.negative is None
    assert chain.paths
    path = chain.paths[0]
    assert path.length == 2  # Report → Follow-up → Original Spec
    assert [edge.predicate for edge in path.edges] == ["cites", "cites"]
    # direction is the STORED direction, edge by edge
    assert path.edges[0].subject_id == docs["Report"]
    assert path.edges[-1].object_id == docs["Original Spec"]

    unrelated = graph.citation_path(
        from_doc_id=docs["Original Spec"], to_doc_id=docs["Report"]
    )
    assert unrelated.negative is not None  # citation is directed


class _FailDocCrossref:
    """A connection proxy that injects the engine's intermittent INT128
    overflow on the DOC_CROSSREF traversal, delegating everything else to a
    real connection — the WP-4.1 spike battery recorded this fault as
    nondeterministic, so it is simulated rather than provoked."""

    def __init__(self, *, real: object, forever: bool) -> None:
        """Fail on every DOC_CROSSREF query (`forever`) or just the first."""
        self._real = real
        self._forever = forever
        self._failed = False

    def execute(self, query: str, parameters: object) -> object:
        """Raise the overflow on the traversal; pass other queries through."""
        if "DOC_CROSSREF" in query and (self._forever or not self._failed):
            self._failed = True
            raise RuntimeError(
                "Overflow exception: INT128 is out of range: cannot add in place"
            )
        return self._real.execute(query, parameters)  # type: ignore[attr-defined]


def test_a_transient_engine_fault_retries_on_a_fresh_connection(
    graph: GraphQueries, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The engine's intermittent INT128 overflow on a SHORTEST traversal
    must never surface as a crash: the read retries on a FRESH connection
    and still returns the real citation chain (WP-4.5 defensive finding).

    Capture a successful DOC_CROSSREF result first, then inject the overflow
    on the first attempt only and replay the captured rows on retry. That
    proves the retry branch without rolling the real engine's nondeterministic
    INT128 coin twice under CI memory pressure (WP-4.1).
    """
    docs = graph.docs  # type: ignore[attr-defined]
    real_run_rows = graph_queries_module._run_rows
    captured: dict[str, list[list[object]]] = {}

    def _capture_rows(
        connection: object, query: str, parameters: dict[str, object]
    ) -> list[list[object]]:
        """Record a successful DOC_CROSSREF row set for later replay."""
        rows = real_run_rows(connection, query, parameters)  # type: ignore[arg-type]
        if "DOC_CROSSREF" in query:
            captured["doc_crossref"] = rows
        return rows

    monkeypatch.setattr(graph_queries_module, "_run_rows", _capture_rows)
    baseline = graph.citation_path(
        from_doc_id=docs["Report"], to_doc_id=docs["Original Spec"]
    )
    assert baseline.negative is None
    assert "doc_crossref" in captured

    attempts = {"doc_crossref": 0}

    def _run_rows_with_injected_overflow(
        connection: object, query: str, parameters: dict[str, object]
    ) -> list[list[object]]:
        """Fail once on DOC_CROSSREF, then replay the captured success."""
        if "DOC_CROSSREF" in query:
            attempts["doc_crossref"] += 1
            if attempts["doc_crossref"] == 1:
                raise RuntimeError(
                    "Overflow exception: INT128 is out of range: cannot add in place"
                )
            return captured["doc_crossref"]
        return real_run_rows(connection, query, parameters)  # type: ignore[arg-type]

    monkeypatch.setattr(
        graph_queries_module, "_run_rows", _run_rows_with_injected_overflow
    )
    chain = graph.citation_path(
        from_doc_id=docs["Report"], to_doc_id=docs["Original Spec"]
    )
    assert attempts["doc_crossref"] == 2  # first fault + one retry
    assert chain.negative is None  # the fresh-connection retry cleared it
    assert chain.paths
    assert chain.paths[0].length == 2


def test_a_persistent_engine_fault_becomes_a_typed_boundary(
    graph: GraphQueries,
) -> None:
    """If the fault does not clear on retry, the read degrades to a typed
    BOUNDARY with a workaround — an agent sees an honest 'retry' negative,
    never a raw INT128 RuntimeError."""
    reader = cast("GraphSnapshotReader", graph._reader)  # type: ignore[attr-defined]
    proxy = _FailDocCrossref(real=reader.fresh_connection(), forever=True)
    reader._connection = proxy  # type: ignore[assignment]
    reader.fresh_connection = lambda: proxy  # type: ignore[method-assign] # the retry faults too
    docs = graph.docs  # type: ignore[attr-defined]
    chain = graph.citation_path(
        from_doc_id=docs["Report"], to_doc_id=docs["Original Spec"]
    )
    assert chain.negative is not None
    assert chain.negative.kind is NegativeKind.BOUNDARY
    assert chain.negative.workaround is not None


def test_transitive_entity_reach_by_hop_bound(graph: GraphQueries) -> None:
    """A chain is reachable at 2 hops and not at 1 — the hop bound means
    what it says."""
    ids = graph.ids  # type: ignore[attr-defined]
    one_hop = graph.neighborhood(
        entity_id=ids["Alice"], hops=1, predicates=("works_on", "part_of")
    )
    assert "ESB Migration" not in _names(one_hop)
    two_hops = graph.neighborhood(
        entity_id=ids["Alice"], hops=2, predicates=("works_on", "part_of")
    )
    assert "ESB Migration" in _names(two_hops)


def test_typed_negatives_and_the_hop_clamp(graph: GraphQueries) -> None:
    """Absence is typed, and a request beyond the engine's 30-hop ceiling is
    clamped AND disclosed rather than silently honored or thrown."""
    ids = graph.ids  # type: ignore[attr-defined]
    absent = graph.neighborhood(entity_id=uuid4(), hops=2)
    assert absent.negative is not None
    # Codex review: an id the graph never heard of is UNKNOWN_ENTITY, not
    # "this entity has no neighbors"
    assert absent.negative.kind is NegativeKind.UNKNOWN_ENTITY

    isolated = graph.neighborhood(
        entity_id=ids["Vector Databases"], hops=1, predicates=("works_for",)
    )
    assert isolated.negative is not None
    assert isolated.negative.kind is NegativeKind.KNOWN_EMPTY  # exists, no match

    no_path = graph.path(
        from_entity_id=ids["Vector Databases"],
        to_entity_id=ids["ESB Migration"],
        max_hops=1,
    )
    assert no_path.negative is not None
    assert no_path.negative.kind is NegativeKind.KNOWN_EMPTY

    clamped = graph.neighborhood(entity_id=ids["Acme"], hops=99)
    assert clamped.truncation is not None
    assert clamped.truncation.truncated is True  # the ceiling is disclosed


def test_boundary_when_no_snapshot_is_published(tmp_path: Path) -> None:
    """A graph question asked before any rebuild is a typed BOUNDARY with a
    workaround — never an empty answer posing as knowledge."""

    class _NoSnapshot:
        version = None

        def connection(self) -> object:
            raise RuntimeError("no published P2 snapshot exists yet")

    envelope = GraphQueries(reader=_NoSnapshot()).neighborhood(entity_id=uuid4())
    assert envelope.negative is not None
    assert envelope.negative.kind is NegativeKind.BOUNDARY
    assert envelope.negative.workaround is not None


def test_current_means_currently_valid(graph: GraphQueries) -> None:
    """Codex review: a default (no `valid_at`) neighborhood must not return
    an EXPIRED edge just because it was never invalidated — 'current' means
    currently-valid, and the applied instant is always echoed."""
    ids = graph.ids  # type: ignore[attr-defined]
    default = graph.neighborhood(entity_id=ids["Acme"], hops=1)
    assert "Carol" not in _names(default)  # her spell closed in June 2024
    assert {"Alice", "Bob"} <= _names(default)
    assert default.as_of_valid_at is not None  # echoed, never silent


def test_edge_direction_survives_reverse_traversal(graph: GraphQueries) -> None:
    """Codex review: traversing an edge BACKWARDS must not invert the fact.
    Stored: Alice -[works_for]-> Acme. Asked the other way round, the edge
    still reports Alice as subject and Acme as object."""
    ids = graph.ids  # type: ignore[attr-defined]
    reverse = graph.path(
        from_entity_id=ids["Acme"], to_entity_id=ids["Alice"], max_hops=1
    )
    assert reverse.negative is None
    edge = reverse.paths[0].edges[0]
    assert edge.subject_id == ids["Alice"]  # never "Acme works_for Alice"
    assert edge.object_id == ids["Acme"]
    assert edge.predicate == "works_for"
    assert edge.ingested_at is not None  # the bi-temporal state is complete


def test_continuation_is_snapshot_bound(graph: GraphQueries) -> None:
    """Codex review: a cursor from a superseded snapshot is refused, not
    silently applied — paging across a swap would skip or duplicate."""
    ids = graph.ids  # type: ignore[attr-defined]
    page = graph.neighborhood(entity_id=ids["Acme"], hops=2, limit=1)
    assert page.truncation is not None
    stale = graph.neighborhood(
        entity_id=ids["Acme"], hops=2, limit=1, continuation="some-older-snapshot:1"
    )
    assert stale.negative is not None
    assert stale.negative.kind is NegativeKind.BOUNDARY
    with pytest.raises(ValueError, match="at least 1"):
        graph.neighborhood(entity_id=ids["Acme"], limit=0)  # no zero-page loop


def test_believed_at_is_applied_and_echoed(graph: GraphQueries) -> None:
    """Codex review: system-time filtering must be visible in the answer —
    two calls differing only by `believed_at` are distinguishable."""
    ids = graph.ids  # type: ignore[attr-defined]
    before_ingest = graph.neighborhood(
        entity_id=ids["Acme"], hops=1, believed_at=datetime(2020, 1, 1, tzinfo=UTC)
    )
    assert before_ingest.negative is not None  # nothing was believed yet
    assert before_ingest.as_of_believed_at == datetime(2020, 1, 1, tzinfo=UTC)

    now = graph.neighborhood(
        entity_id=ids["Acme"], hops=1, believed_at=datetime(2026, 12, 1, tzinfo=UTC)
    )
    assert {"Alice", "Bob"} <= _names(now)
    assert now.as_of_believed_at == datetime(2026, 12, 1, tzinfo=UTC)


def test_freshness_carries_the_snapshot_stamp(graph: GraphQueries) -> None:
    """Codex review: S42 needs WHEN, not only which — the published-at
    timestamp rides every graph answer."""
    ids = graph.ids  # type: ignore[attr-defined]
    envelope = graph.neighborhood(entity_id=ids["Acme"], hops=1)
    assert envelope.freshness.p2_snapshot_version is not None
    assert envelope.freshness.p2_snapshot_ts is not None
