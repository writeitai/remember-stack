"""WP-5.1 acceptance: the remaining retrieval primitives (retrieval §3, §9).

Each primitive is proved over a small, directly-seeded corpus — entities,
relations, observations, claims, the adjudication/decision logs, and a handful
of K pages with their routing keys — so every arm exercises real spine tables:

- `fuse` / `rerank` — the D9 rank operators, pure and inspectable (S46/S48).
- `transcript` — the audit trail across all four subjects (S8/S32/S35).
- `delta` — the timestamped change feed, bounded and resumable (S13/S14/S30).
- `pages_about` — the K routing index read backwards, with stale flags (S31/S45).
- `aggregate` — the enumerated forms only, each a bounded shape (S26–S30/S40).
- `scan` — the batch surface, streaming over a separate pool (S53).

The negatives are typed and the caps are never silent.
"""

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
from sqlalchemy.engine import Engine

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import NegativeKind
from rememberstack.model import P1ChunkText
from rememberstack.model import RankedItem
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import QueryEngine

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("51000000-0000-0000-0000-000000000001")

_OLD = datetime(2026, 3, 5, tzinfo=UTC)
_SINCE = datetime(2026, 7, 1, tzinfo=UTC)
_INVAL = datetime(2026, 7, 5, tzinfo=UTC)
_CAP = datetime(2026, 7, 8, tzinfo=UTC)
_COMPILE = datetime(2026, 7, 9, tzinfo=UTC)
_NEW = datetime(2026, 7, 10, tzinfo=UTC)


class _NullSearchIndex:
    """A P1 stub: the WP-5.1 primitives never nominate, so this is unused."""

    def search_claims(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        current_only: bool,
    ) -> tuple[str, ...]:
        """Never called by these primitives."""
        return ()

    def search_claims_lexical(self, **_: object) -> tuple[str, ...]:
        """Never called by these primitives."""
        return ()

    def search_chunks(self, **_: object) -> tuple[str, ...]:
        """Never called by these primitives."""
        return ()

    def search_chunks_lexical(self, **_: object) -> tuple[str, ...]:
        """Never called by these primitives."""
        return ()

    def chunk_texts(self, **_: object) -> dict[str, P1ChunkText]:
        """Never called by these primitives."""
        return {}

    def search_facts(
        self, *, deployment_id: str, vector: tuple[float, ...], k: int, kind: str | None
    ) -> tuple[str, ...]:
        """Never called by these primitives."""
        return ()


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for real primitive proofs")
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
    """A small corpus that touches every primitive's tables."""

    def __init__(self, *, engine: Engine) -> None:
        """Seed entities, facts, the decision logs, and a few K pages."""
        self.engine = engine
        self.ids: dict[str, UUID] = {}
        self.rel: dict[str, UUID] = {}
        self.obs: dict[str, UUID] = {}
        self.art: dict[str, UUID] = {}
        with engine.begin() as connection:
            self._entities(connection)
            self._relations(connection)
            self._observations(connection)
            self._claims(connection)
            self._decisions(connection)
            self._knowledge(connection)

    def _entity(self, connection: object, name: str, entity_type: str) -> UUID:
        entity_id = uuid4()
        self.ids[name] = entity_id
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO entities (entity_id, deployment_id,"
                " canonical_name, normalized_name)"
                " VALUES (:e, :d, :n, lower(:n))"
            ),
            {"e": entity_id, "d": _DEPLOYMENT_ID, "t": entity_type, "n": name},
        )
        return entity_id

    def _entities(self, connection: object) -> None:
        for name, kind in (
            ("Alice", "Person"),
            ("Bob", "Person"),
            ("Acme", "Organization"),
            ("Contoso", "Organization"),
            ("Beacon", "Project"),
        ):
            self._entity(connection, name, kind)
        # an absorbed identity, merged into Alice (for the entity transcript)
        absorbed = uuid4()
        self.ids["A. Nowak"] = absorbed
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO entities (entity_id, deployment_id,"
                " canonical_name, normalized_name, status, merged_into)"
                " VALUES (:e, :d, 'A. Nowak', 'a. nowak', 'merged', :m)"
            ),
            {"e": absorbed, "d": _DEPLOYMENT_ID, "m": self.ids["Alice"]},
        )

    def _relation(
        self,
        connection: object,
        key: str,
        subject: str,
        predicate: str,
        obj: str,
        *,
        evidence: int,
        ingested_at: datetime,
        valid_until: datetime | None = None,
        invalidated_at: datetime | None = None,
    ) -> None:
        relation_id = uuid4()
        self.rel[key] = relation_id
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id,"
                " normalizer_version, fact_label, evidence_count, valid_from,"
                " valid_until, ingested_at, invalidated_at)"
                " VALUES (:r, :d, :s, :p, :o, 'toy', :label, :ec, :vf, :vu,"
                " :ing, :inv)"
            ),
            {
                "r": relation_id,
                "d": _DEPLOYMENT_ID,
                "s": self.ids[subject],
                "p": predicate,
                "o": self.ids[obj],
                "label": f"{subject} {predicate} {obj}",
                "ec": evidence,
                "vf": _OLD,
                "vu": valid_until,
                "ing": ingested_at,
                "inv": invalidated_at,
            },
        )

    def _relations(self, connection: object) -> None:
        self._relation(
            connection,
            "works_for_acme",
            "Alice",
            "works_for",
            "Acme",
            evidence=3,
            ingested_at=_NEW,
        )
        self._relation(
            connection,
            "works_on_beacon",
            "Alice",
            "works_on",
            "Beacon",
            evidence=1,
            ingested_at=_NEW,
        )
        self._relation(
            connection,
            "bob_acme",
            "Bob",
            "works_for",
            "Acme",
            evidence=2,
            ingested_at=_OLD,
        )
        # a capped window: Alice used to work for Contoso, closed by a supersede
        self._relation(
            connection,
            "works_for_contoso",
            "Alice",
            "works_for",
            "Contoso",
            evidence=1,
            ingested_at=_OLD,
            valid_until=_CAP,
        )
        # a relation invalidated inside the delta window
        self._relation(
            connection,
            "founded_beacon",
            "Alice",
            "founded",
            "Beacon",
            evidence=1,
            ingested_at=_OLD,
            invalidated_at=_INVAL,
        )

    def _observation(
        self,
        connection: object,
        key: str,
        subject: str,
        statement: str,
        *,
        evidence: int,
        ingested_at: datetime,
        invalidated_at: datetime | None = None,
    ) -> None:
        observation_id = uuid4()
        self.obs[key] = observation_id
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO observations (observation_id, deployment_id,"
                " subject_entity_id, statement, normalizer_version,"
                " evidence_count, ingested_at, invalidated_at)"
                " VALUES (:o, :d, :s, :st, 'toy', :ec, :ing, :inv)"
            ),
            {
                "o": observation_id,
                "d": _DEPLOYMENT_ID,
                "s": self.ids[subject],
                "st": statement,
                "ec": evidence,
                "ing": ingested_at,
                "inv": invalidated_at,
            },
        )

    def _observations(self, connection: object) -> None:
        self._observation(
            connection,
            "headcount",
            "Acme",
            "Acme's headcount is 600.",
            evidence=2,
            ingested_at=_NEW,
        )
        self._observation(
            connection,
            "revenue",
            "Acme",
            "Acme's revenue is 10M.",
            evidence=1,
            ingested_at=_OLD,
            invalidated_at=_INVAL,
        )

    def _claim(
        self, connection: object, key: str, text_value: str, ingested_at: datetime
    ) -> None:
        claim_id = uuid4()
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, extractor_version, ingested_at)"
                " VALUES (:c, :d, :doc, :ch, :ct, :ct, 0, 10, true, true,"
                " 'toy', :ing)"
            ),
            {
                "c": claim_id,
                "d": _DEPLOYMENT_ID,
                "doc": uuid4(),
                "ch": uuid4(),
                "ct": text_value,
                "ing": ingested_at,
            },
        )

    def _claims(self, connection: object) -> None:
        self._claim(connection, "new_claim", "Alice joined Acme.", _NEW)
        self._claim(connection, "old_claim", "Acme was founded long ago.", _OLD)

    def _decisions(self, connection: object) -> None:
        # a relation supersede that caps works_for_contoso (relation_id) in
        # favour of works_for_acme (related_relation_id), dated at the cap
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO relation_adjudications (adjudication_id,"
                " deployment_id, relation_id, related_relation_id, outcome,"
                " method, confidence, adjudicator_version, decided_by,"
                " decided_at)"
                " VALUES (:a, :d, :rel, :related, 'supersede', 'small_model',"
                " 0.9, 'toy', 'auto', :at)"
            ),
            {
                "a": uuid4(),
                "d": _DEPLOYMENT_ID,
                "rel": self.rel["works_for_contoso"],
                "related": self.rel["works_for_acme"],
                "at": _CAP,
            },
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO observation_adjudications (adjudication_id,"
                " deployment_id, observation_id, outcome, method,"
                " adjudicator_version, decided_by, decided_at)"
                " VALUES (:a, :d, :obs, 'add', 'small_model', 'toy', 'auto', :at)"
            ),
            {
                "a": uuid4(),
                "d": _DEPLOYMENT_ID,
                "obs": self.obs["headcount"],
                "at": _NEW,
            },
        )
        # an observation supersede caps the headcount window (delta 'capped')
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO observation_adjudications (adjudication_id,"
                " deployment_id, observation_id, related_observation_id,"
                " outcome, method, adjudicator_version, decided_by, decided_at)"
                " VALUES (:a, :d, :obs, :rel, 'supersede', 'small_model', 'toy',"
                " 'auto', :at)"
            ),
            {
                "a": uuid4(),
                "d": _DEPLOYMENT_ID,
                "obs": self.obs["headcount"],
                "rel": self.obs["revenue"],
                "at": _CAP,
            },
        )
        # Alice's identity history: a resolution decision, then a merge
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO resolution_decisions (decision_id, deployment_id,"
                " mention_id, entity_id, method, confidence, is_new_entity,"
                " resolver_version, decided_by, decided_at)"
                " VALUES (:x, :d, :m, :e, 'T3', 0.8, false, 'toy', 'auto', :at)"
            ),
            {
                "x": uuid4(),
                "d": _DEPLOYMENT_ID,
                "m": uuid4(),
                "e": self.ids["Alice"],
                "at": _OLD,
            },
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO merge_events (merge_id, deployment_id, survivor_id,"
                " absorbed_id, pre_merge_membership_snapshot, decided_by,"
                " decided_at)"
                " VALUES (:x, :d, :s, :a, '{}'::jsonb, 'auto', :at)"
            ),
            {
                "x": uuid4(),
                "d": _DEPLOYMENT_ID,
                "s": self.ids["Alice"],
                "a": self.ids["A. Nowak"],
                "at": _NEW,
            },
        )

    def _artifact(
        self, connection: object, key: str, status: str, page_kind: str = "compiled"
    ) -> UUID:
        """Insert one compiled or authored discovery-page fixture."""
        artifact_id = uuid4()
        self.art[key] = artifact_id
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO knowledge_artifacts (artifact_id, deployment_id,"
                " layer, page_kind, git_path, page_summary, last_compiled_at,"
                " status)"
                " VALUES (:a, :d, 'K1', CAST(:page_kind AS knowledge_page_kind),"
                " :path, :summary, :at,"
                " CAST(:status AS knowledge_artifact_status))"
            ),
            {
                "a": artifact_id,
                "d": _DEPLOYMENT_ID,
                "path": f"k/{key}.md",
                "summary": f"Everything about {key}",
                "at": _COMPILE,
                "status": status,
                "page_kind": page_kind,
            },
        )
        return artifact_id

    def _plan_decision(self, connection: object) -> UUID:
        """One plan decision the artifact-bound page rules can cite."""
        decision_id = uuid4()
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO knowledge_plan_decisions (decision_id,"
                " deployment_id, action, payload, trigger, planner_version)"
                " VALUES (:x, :d, 'create_page', '{}'::jsonb, 'human', 'toy')"
            ),
            {"x": decision_id, "d": _DEPLOYMENT_ID},
        )
        return decision_id

    def _rule_on(
        self, connection: object, artifact_id: UUID, plan_decision_id: UUID
    ) -> None:
        """A rule that routes the Alice entity key to `artifact_id`.

        An artifact-bound rule must cite the plan decision that created it
        (a schema check), so the seed threads one through.
        """
        rule_id = uuid4()
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO knowledge_page_rules (rule_id, deployment_id,"
                " artifact_id, plan_decision_id, rule_kind, params)"
                " VALUES (:r, :d, :a, :pd, 'entity', '{}'::jsonb)"
            ),
            {
                "r": rule_id,
                "d": _DEPLOYMENT_ID,
                "a": artifact_id,
                "pd": plan_decision_id,
            },
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO knowledge_rule_keys (deployment_id, rule_id,"
                " key_kind, key_value)"
                " VALUES (:d, :r, 'entity', :v)"
            ),
            {"d": _DEPLOYMENT_ID, "r": rule_id, "v": str(self.ids["Alice"])},
        )

    def _knowledge(self, connection: object) -> None:
        plan_decision = self._plan_decision(connection)
        fresh = self._artifact(connection, "alice_fresh", "active")
        stale = self._artifact(connection, "alice_stale", "active")
        tombstoned = self._artifact(connection, "alice_gone", "tombstoned")
        authored = self._artifact(
            connection, "alice_decision", "active", page_kind="authored"
        )
        for artifact_id in (fresh, stale, tombstoned, authored):
            self._rule_on(connection, artifact_id, plan_decision)
        # a queued, unprocessed refresh makes `stale` stale
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO knowledge_refresh_queue (refresh_id, deployment_id,"
                " artifact_id, trigger)"
                " VALUES (:r, :d, :a, 'evidence_changed')"
            ),
            {"r": uuid4(), "d": _DEPLOYMENT_ID, "a": stale},
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO knowledge_refresh_queue (refresh_id, deployment_id,"
                " artifact_id, trigger, payload)"
                " VALUES (:r, :d, :a, 'authored_review',"
                " jsonb_build_object('reasons', jsonb_build_array('tombstone'),"
                " 'redaction_required', true))"
            ),
            {"r": uuid4(), "d": _DEPLOYMENT_ID, "a": authored},
        )
        # a compilation of the fresh page (the k_page transcript + delta feed)
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO knowledge_compilations (compilation_id,"
                " deployment_id, artifact_id, inputs_hash, candidate_count,"
                " cited_count, uncited_count, evidence_added, evidence_removed,"
                " evidence_invalidated, writer_version, compiled_at)"
                " VALUES (:c, :d, :a, 'h', 5, 4, 1, 4, 0, 0, 'writer-1', :at)"
            ),
            {"c": uuid4(), "d": _DEPLOYMENT_ID, "a": fresh, "at": _COMPILE},
        )


@pytest.fixture()
def corpus(database_engine: Engine) -> _Corpus:
    """A fresh deployment and seeded corpus per proof."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="primitives-test",
            name="Retrieval primitive proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    return _Corpus(engine=database_engine)


def _engine(corpus: _Corpus, *, batch_engine: Engine | None = None) -> QueryEngine:
    """A QueryEngine over the seeded corpus (P1/model unused here)."""
    return QueryEngine(
        engine=corpus.engine,
        search_index=_NullSearchIndex(),
        model_provider=FakeModelProvider(generate_payloads={}),
        embedding_model="toy",
        batch_engine=batch_engine,
    )


# --- fuse / rerank: pure operators -----------------------------------------


def test_fuse_rewards_cross_channel_agreement(corpus: _Corpus) -> None:
    """S46: RRF ranks an item both channels return above one only a single
    channel does — without ever comparing raw scores."""
    engine = _engine(corpus)
    a, b, c = uuid4(), uuid4(), uuid4()
    fused = engine.fuse(rankings=[[a, b, c], [b, a]])
    assert fused.grain == "evidence"
    order = [item.item_id for item in fused.ranking]
    assert order[0] in {a, b}  # a and b appear in both channels; c in one
    assert order[-1] == c
    assert fused.ranking[0].score > fused.ranking[-1].score


def test_fuse_with_no_candidates_is_known_empty(corpus: _Corpus) -> None:
    """Fusing empty channels is a typed known_empty, not a silent empty list."""
    fused = _engine(corpus).fuse(rankings=[[], []])
    assert fused.ranking == ()
    assert fused.negative is not None
    assert fused.negative.kind is NegativeKind.KNOWN_EMPTY


def test_rerank_by_evidence_count_is_descending_and_inspectable(
    corpus: _Corpus,
) -> None:
    """S48: evidence_count reranks most-corroborated first, and every item
    keeps its signal value visible."""
    engine = _engine(corpus)
    low, high = uuid4(), uuid4()
    items = [
        RankedItem(item_id=low, score=0.0, signals={"evidence_count": 2}),
        RankedItem(item_id=high, score=0.0, signals={"evidence_count": 9}),
    ]
    ranked = engine.rerank(items=items, signal="evidence_count")
    assert [item.item_id for item in ranked.ranking] == [high, low]
    assert ranked.ranking[0].score == 9.0


def test_rerank_by_graph_distance_is_ascending(corpus: _Corpus) -> None:
    """graph_distance reranks nearer-the-focal-entity first (ascending)."""
    engine = _engine(corpus)
    near, far = uuid4(), uuid4()
    items = [
        RankedItem(item_id=far, score=0.0, signals={"graph_distance": 3}),
        RankedItem(item_id=near, score=0.0, signals={"graph_distance": 1}),
    ]
    ranked = engine.rerank(items=items, signal="graph_distance")
    assert [item.item_id for item in ranked.ranking] == [near, far]


def test_weighted_rerank_keeps_rrf_primary_and_exposes_the_blend(
    corpus: _Corpus,
) -> None:
    """WP-5.6's tuned blend is inspectable and can break a close RRF race."""
    engine = _engine(corpus)
    contextual, unsupported = uuid4(), uuid4()
    items = [
        RankedItem(
            item_id=unsupported,
            score=0.51,
            signals={"graph_distance": 4, "evidence_count": 1},
        ),
        RankedItem(
            item_id=contextual,
            score=0.50,
            signals={"graph_distance": 1, "evidence_count": 8},
        ),
    ]

    ranked = engine.rerank(items=items, signal="weighted_relevance")

    assert [item.item_id for item in ranked.ranking] == [contextual, unsupported]
    assert ranked.ranking[0].signals["weighted_relevance"] == ranked.ranking[0].score
    assert ranked.ranking[0].signals["rrf_score"] == 0.50
    assert ranked.ranking[0].signals["graph_proximity_normalized"] == 1.0
    assert ranked.ranking[0].signals["evidence_support_normalized"] == 1.0


def test_rerank_cross_encoder_and_unknown_signals_are_boundaries(
    corpus: _Corpus,
) -> None:
    """cross_encoder is a flagged, unconfigured capability, and an unknown
    signal is not silently an identity sort — both are typed boundaries."""
    engine = _engine(corpus)
    for signal in ("cross_encoder", "phase_of_moon"):
        answer = engine.rerank(items=(), signal=signal)
        assert answer.negative is not None
        assert answer.negative.kind is NegativeKind.BOUNDARY
        assert answer.negative.workaround is not None


# --- transcript: four subjects ---------------------------------------------


def test_transcript_relation_returns_the_supersede_with_its_counterpart(
    corpus: _Corpus,
) -> None:
    """S8: a relation's decision history, with related_id pointing at the
    counterpart the supersede paired it with."""
    answer = _engine(corpus).transcript(
        deployment_id=_DEPLOYMENT_ID,
        subject_kind="relation",
        subject_id=corpus.rel["works_for_contoso"],
    )
    (entry,) = answer.transcript
    assert entry.subject_kind == "relation"
    assert entry.outcome == "supersede"
    assert entry.related_id == corpus.rel["works_for_acme"]


def test_transcript_observation_and_kpage(corpus: _Corpus) -> None:
    """S32/S35: observation adjudications and K-page compile provenance are
    both first-class transcripts."""
    engine = _engine(corpus)
    observation = engine.transcript(
        deployment_id=_DEPLOYMENT_ID,
        subject_kind="observation",
        subject_id=corpus.obs["headcount"],
    )
    assert observation.transcript[0].subject_kind == "observation"

    page = engine.transcript(
        deployment_id=_DEPLOYMENT_ID,
        subject_kind="k_page",
        subject_id=corpus.art["alice_fresh"],
    )
    (compile_entry,) = page.transcript
    assert compile_entry.outcome == "compiled"
    assert compile_entry.method == "writer-1"


def test_transcript_entity_braids_resolution_and_merge(corpus: _Corpus) -> None:
    """An entity's history is its resolution decisions and merges together,
    newest-last."""
    answer = _engine(corpus).transcript(
        deployment_id=_DEPLOYMENT_ID,
        subject_kind="entity",
        subject_id=corpus.ids["Alice"],
    )
    outcomes = [entry.outcome for entry in answer.transcript]
    assert outcomes == ["linked", "merge"]  # ordered by decided_at
    assert answer.transcript[-1].related_id == corpus.ids["A. Nowak"]
    assert answer.truncation is not None
    assert answer.truncation.truncated is False
    assert answer.truncation.returned == 2
    assert answer.truncation.estimated_total == 2
    assert answer.truncation.total_is_exact is True


def test_transcript_unknown_kind_is_boundary(corpus: _Corpus) -> None:
    """An unknown subject kind is a typed boundary naming the four that exist."""
    answer = _engine(corpus).transcript(
        deployment_id=_DEPLOYMENT_ID, subject_kind="planet", subject_id=uuid4()
    )
    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.BOUNDARY


def test_transcript_of_a_subject_with_no_history_is_known_empty(
    corpus: _Corpus,
) -> None:
    """A real kind with no decisions is known_empty, never a guess."""
    answer = _engine(corpus).transcript(
        deployment_id=_DEPLOYMENT_ID, subject_kind="relation", subject_id=uuid4()
    )
    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.KNOWN_EMPTY


def test_transcript_recent_first_bound_signals_truncation(corpus: _Corpus) -> None:
    """#156: over-cap entity history keeps the newest rows and discloses the cut.

    An unbounded identity transcript once returned ~310 rows / ~75KB for one
    entity and five calls blew a 128k reader context. The default bound is a
    measurement starting point; the envelope must never be silent about it
    (S18).
    """
    from datetime import timedelta

    from rememberstack.surfaces.query_engine import DEFAULT_TRANSCRIPT_LIMIT

    entity_id = corpus.ids["Bob"]
    over_cap = DEFAULT_TRANSCRIPT_LIMIT + 7
    origin = datetime(2026, 1, 1, tzinfo=UTC)
    with corpus.engine.begin() as connection:
        for index in range(over_cap):
            connection.execute(
                text(
                    "INSERT INTO resolution_decisions (decision_id, deployment_id,"
                    " mention_id, entity_id, method, confidence, is_new_entity,"
                    " resolver_version, decided_by, decided_at)"
                    " VALUES (:x, :d, :m, :e, 'T3', 0.8, false, 'toy', 'auto', :at)"
                ),
                {
                    "x": uuid4(),
                    "d": _DEPLOYMENT_ID,
                    "m": uuid4(),
                    "e": entity_id,
                    "at": origin + timedelta(hours=index),
                },
            )

    answer = _engine(corpus).transcript(
        deployment_id=_DEPLOYMENT_ID, subject_kind="entity", subject_id=entity_id
    )
    assert answer.truncation is not None
    assert answer.truncation.truncated is True
    assert answer.truncation.returned == DEFAULT_TRANSCRIPT_LIMIT
    assert answer.truncation.estimated_total == over_cap
    assert answer.truncation.total_is_exact is True
    assert len(answer.transcript) == DEFAULT_TRANSCRIPT_LIMIT
    # kept window is newest-last among the recent rows, not the oldest tail
    times = [entry.decided_at for entry in answer.transcript if entry.decided_at]
    assert times == sorted(times)
    assert times[0] == origin + timedelta(hours=7)
    assert times[-1] == origin + timedelta(hours=over_cap - 1)

    full = _engine(corpus).transcript(
        deployment_id=_DEPLOYMENT_ID,
        subject_kind="entity",
        subject_id=entity_id,
        limit=over_cap,
    )
    assert full.truncation is not None
    assert full.truncation.truncated is False
    assert len(full.transcript) == over_cap


# --- delta: the change feed ------------------------------------------------


def test_delta_reports_all_four_change_types(corpus: _Corpus) -> None:
    """S13/S14/S30: new, invalidated, capped, and recompiled all surface,
    each dated on a real column and ordered newest-first."""
    answer = _engine(corpus).delta(deployment_id=_DEPLOYMENT_ID, since=_SINCE)
    by_change = {(c.kind, c.change) for c in answer.changes}
    assert ("relation", "new") in by_change
    assert ("relation", "invalidated") in by_change
    assert ("relation", "capped") in by_change
    assert ("observation", "new") in by_change
    assert ("observation", "capped") in by_change  # observation supersede too
    assert ("claim", "new") in by_change
    assert ("page", "recompiled") in by_change
    # nothing ingested before the window leaks in
    ids = {c.id for c in answer.changes}
    assert corpus.rel["bob_acme"] not in ids
    # ordered newest-first
    times = [c.at for c in answer.changes]
    assert times == sorted(times, reverse=True)


def test_delta_kinds_filter_and_truncation(corpus: _Corpus) -> None:
    """The kinds filter narrows the feed; hitting the limit is disclosed, not
    a silent cut."""
    engine = _engine(corpus)
    relations_only = engine.delta(
        deployment_id=_DEPLOYMENT_ID, since=_SINCE, kinds=("relation",)
    )
    assert {c.kind for c in relations_only.changes} == {"relation"}

    capped = engine.delta(deployment_id=_DEPLOYMENT_ID, since=_SINCE, limit=1)
    assert len(capped.changes) == 1
    assert capped.truncation is not None
    assert capped.truncation.truncated is True


def test_delta_empty_window_is_known_empty(corpus: _Corpus) -> None:
    """A window in which nothing changed is a typed known_empty."""
    answer = _engine(corpus).delta(
        deployment_id=_DEPLOYMENT_ID, since=datetime(2099, 1, 1, tzinfo=UTC)
    )
    assert answer.changes == ()
    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.KNOWN_EMPTY


def test_delta_pagination_via_continuation_drops_nothing(corpus: _Corpus) -> None:
    """Paging the feed one row at a time via the continuation returns exactly
    the same set as one unpaged call — a page boundary that splits rows
    sharing a timestamp never skips the tied remainder (Codex finding)."""
    engine = _engine(corpus)
    whole = engine.delta(deployment_id=_DEPLOYMENT_ID, since=_SINCE, limit=1000)
    expected = [(c.kind, c.change, c.id) for c in whole.changes]

    paged: list[tuple[str, str, UUID]] = []
    cursor: str | None = None
    for _ in range(len(expected) + 5):  # a bound so a bug cannot loop forever
        page = engine.delta(
            deployment_id=_DEPLOYMENT_ID, since=_SINCE, limit=1, continuation=cursor
        )
        paged.extend((c.kind, c.change, c.id) for c in page.changes)
        assert page.truncation is not None
        if not page.truncation.truncated:
            break
        cursor = page.truncation.continuation
        assert cursor is not None
    assert paged == expected  # same order, every row, no duplicates, no drops


# --- pages_about: the routing index, backwards -----------------------------


def test_pages_about_discovers_pages_and_flags_stale(corpus: _Corpus) -> None:
    """S31/S45: the routing keys read backwards find the pages about an
    entity; a page with a queued refresh is flagged stale; a tombstoned one
    is gone."""
    answer = _engine(corpus).pages_about(
        deployment_id=_DEPLOYMENT_ID, entity_id=corpus.ids["Alice"]
    )
    assert answer.grain == "compiled"
    by_id = {page.artifact_id: page for page in answer.pages}
    assert corpus.art["alice_fresh"] in by_id
    assert corpus.art["alice_gone"] not in by_id  # tombstoned excluded
    assert by_id[corpus.art["alice_fresh"]].stale is False
    assert by_id[corpus.art["alice_stale"]].stale is True
    authored = by_id[corpus.art["alice_decision"]]
    assert authored.stale is False
    assert authored.open_review_flags == 1
    assert authored.redaction_required is True


def test_pages_about_with_no_pages_is_known_empty(corpus: _Corpus) -> None:
    """An entity no page routes on is known_empty with a workaround — K
    synthesis is optional, the primitives still answer."""
    answer = _engine(corpus).pages_about(
        deployment_id=_DEPLOYMENT_ID, entity_id=corpus.ids["Bob"]
    )
    assert answer.pages == ()
    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.KNOWN_EMPTY
    assert answer.negative.workaround is not None


# --- aggregate: enumerated forms -------------------------------------------


def test_aggregate_count_and_group_forms(corpus: _Corpus) -> None:
    """S26–S28: count and the group-by forms are bounded shapes over live
    relations only (the invalidated one never counts)."""
    engine = _engine(corpus)
    alice = corpus.ids["Alice"]
    count = engine.aggregate(
        deployment_id=_DEPLOYMENT_ID, form="count", subject_entity_id=alice
    )
    assert count.aggregate is not None
    assert count.aggregate.total == 3  # works_for Acme, works_on Beacon, Contoso

    by_predicate = engine.aggregate(
        deployment_id=_DEPLOYMENT_ID, form="group_by_predicate", subject_entity_id=alice
    )
    assert by_predicate.aggregate is not None
    buckets = {b.key: b.count for b in by_predicate.aggregate.buckets}
    assert buckets == {"works_for": 2, "works_on": 1}

    by_object = engine.aggregate(
        deployment_id=_DEPLOYMENT_ID,
        form="group_by_object",
        subject_entity_id=alice,
        predicate="works_for",
    )
    assert by_object.aggregate is not None
    objects = {b.key for b in by_object.aggregate.buckets}
    assert objects == {"Acme", "Contoso"}
    assert all(b.entity_id is not None for b in by_object.aggregate.buckets)


def test_aggregate_timeline_and_delta_top_and_typed_absence(corpus: _Corpus) -> None:
    """S30/S40: entity timeline, the delta-bounded leaderboard, and typed
    absence — the anti-join the ontology makes answerable."""
    engine = _engine(corpus)
    alice = corpus.ids["Alice"]
    timeline = engine.aggregate(
        deployment_id=_DEPLOYMENT_ID, form="timeline", subject_entity_id=alice
    )
    assert timeline.aggregate is not None
    assert timeline.aggregate.total >= 3
    assert all(bucket.key is not None for bucket in timeline.aggregate.buckets)

    top = engine.aggregate(
        deployment_id=_DEPLOYMENT_ID, form="delta_top_entities", since=_SINCE
    )
    assert top.aggregate is not None
    assert top.aggregate.bounded_by == "delta window"
    leaders = {b.entity_id: b.count for b in top.aggregate.buckets}
    assert leaders.get(alice) == 2  # two relations ingested in the window

    absent = engine.aggregate(
        deployment_id=_DEPLOYMENT_ID,
        form="typed_absence",
        entity_type="Organization",
        predicate="works_for",
    )
    assert absent.aggregate is not None
    absent_entities = {b.entity_id for b in absent.aggregate.buckets}
    assert corpus.ids["Acme"] in absent_entities  # orgs are never the subject
    assert corpus.ids["Contoso"] in absent_entities


def test_aggregate_unknown_form_is_boundary_and_missing_param_raises(
    corpus: _Corpus,
) -> None:
    """An unknown form is a typed boundary; a form missing its required
    parameter is a programming error, raised, not a silent empty."""
    engine = _engine(corpus)
    unknown = engine.aggregate(deployment_id=_DEPLOYMENT_ID, form="median_headcount")
    assert unknown.negative is not None
    assert unknown.negative.kind is NegativeKind.BOUNDARY

    with pytest.raises(ValueError, match="subject_entity_id"):
        engine.aggregate(deployment_id=_DEPLOYMENT_ID, form="group_by_predicate")


# --- scan: the batch surface -----------------------------------------------


def test_scan_streams_every_row_over_a_separate_pool(
    corpus: _Corpus, database_engine: Engine
) -> None:
    """S53: scan streams a full export as a generator, and it runs against
    the SEPARATE batch pool it was given (pool isolation)."""
    batch = create_engine(database_engine.url.render_as_string(hide_password=False))
    try:
        engine = _engine(corpus, batch_engine=batch)
        stream = engine.scan(
            deployment_id=_DEPLOYMENT_ID, kind="relation", batch_size=2
        )
        assert not isinstance(stream, list)  # a lazy generator, not a buffer
        rows = list(stream)
        assert len(rows) == 5  # every seeded relation
        assert all(row.kind == "relation" for row in rows)
        assert {row.id for row in rows} == set(corpus.rel.values())

        observations = list(
            engine.scan(deployment_id=_DEPLOYMENT_ID, kind="observation")
        )
        assert len(observations) == 2
    finally:
        batch.dispose()


def test_scan_unknown_kind_raises(corpus: _Corpus) -> None:
    """An unknown export kind is a programming error, not a silent empty
    stream that reads as 'nothing to export'."""
    with pytest.raises(ValueError, match="scan export"):
        next(_engine(corpus).scan(deployment_id=_DEPLOYMENT_ID, kind="galaxies"))


# --- regression proofs for the Codex review fixes --------------------------


def test_rrf_duplicate_in_one_channel_does_not_forge_agreement(corpus: _Corpus) -> None:
    """A channel that lists an id twice must not out-score an id another
    channel ranks once — only an item's best rank per channel counts."""
    engine = _engine(corpus)
    a, b = uuid4(), uuid4()
    fused = engine.fuse(rankings=[[a, a], [b]])  # a duplicated in one channel
    scores = {item.item_id: item.score for item in fused.ranking}
    assert scores[a] == scores[b]  # each contributes exactly one channel


def test_rerank_missing_signal_keeps_a_finite_score_and_sorts_last(
    corpus: _Corpus,
) -> None:
    """An item missing the signal keeps its incoming (finite) score and sorts
    last — never stamped with an infinity that would not survive JSON."""
    engine = _engine(corpus)
    has, lacks = uuid4(), uuid4()
    items = [
        RankedItem(item_id=lacks, score=0.5, signals={}),
        RankedItem(item_id=has, score=0.0, signals={"evidence_count": 4}),
    ]
    ranked = engine.rerank(items=items, signal="evidence_count")
    assert ranked.ranking[0].item_id == has
    assert ranked.ranking[-1].item_id == lacks
    assert ranked.ranking[-1].score == 0.5  # finite, its own prior score
    # and the whole envelope serializes as valid JSON (no Infinity token)
    assert "Infinity" not in ranked.model_dump_json()


def test_transcript_related_id_is_the_counterpart_from_either_side(
    corpus: _Corpus,
) -> None:
    """Querying the transcript from the OTHER relation in a supersede pair
    still reports the counterpart as related_id, never the subject itself."""
    answer = _engine(corpus).transcript(
        deployment_id=_DEPLOYMENT_ID,
        subject_kind="relation",
        subject_id=corpus.rel["works_for_acme"],  # the related side of the pair
    )
    (entry,) = answer.transcript
    assert entry.related_id == corpus.rel["works_for_contoso"]
    assert entry.related_id != corpus.rel["works_for_acme"]


def test_aggregate_truncation_is_disclosed(corpus: _Corpus) -> None:
    """A bounded aggregate that hits its limit says so — the bucket total is
    a floor, never a silent 'this is all there is' (Codex finding)."""
    absent = _engine(corpus).aggregate(
        deployment_id=_DEPLOYMENT_ID,
        form="typed_absence",
        entity_type="Organization",
        predicate="works_for",
        limit=1,
    )
    assert absent.truncation is not None
    assert absent.truncation.truncated is True
    assert absent.truncation.total_is_exact is False
    assert absent.aggregate is not None
    assert len(absent.aggregate.buckets) == 1  # capped, not the full two


def test_scan_and_aggregate_reject_nonpositive_bounds(corpus: _Corpus) -> None:
    """Zero or negative bounds are programming errors, raised — not a silent
    unbounded scan or an empty result posing as complete."""
    engine = _engine(corpus)
    with pytest.raises(ValueError, match="batch_size"):
        next(engine.scan(deployment_id=_DEPLOYMENT_ID, kind="relation", batch_size=0))
    with pytest.raises(ValueError, match="limit"):
        engine.aggregate(deployment_id=_DEPLOYMENT_ID, form="count", limit=0)
