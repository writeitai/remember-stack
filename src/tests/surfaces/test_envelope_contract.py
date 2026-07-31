"""WP-5.3 acceptance: the complete envelope contract (retrieval §5-§6, D49).

The envelope is the answer's machine-readable self-account, and several of its
rules are contract, not garnish — proved here over a seeded corpus:

- **Contradiction co-members are never silently absent (S23).** A fact in a
  live contradiction group ALWAYS carries the other sides (bounded by a cap,
  with group_id/returned/total/continuation) — even when the query returns
  just one side.
- **A withdrawn fact is flagged, not vanished (D54).** An open
  `support_withdrawn` review marks the fact `support=withdrawn`; it is still
  returned.
- **Composite answers are explicitly two-part, never blended (S47).** `parts`
  belong only to a composite envelope, and each part is strictly single-grain.
- **Identity regime and believed_at horizons are stated (S61, §3).** Reads
  echo which identity boundary answered; a query before a finite channel
  horizon is a typed `boundary`, never a silent truncation.
- **The negative taxonomy is frozen (S29/S39/S55).** Exactly three kinds, no
  `denied`.
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
from rememberstack.model import Envelope
from rememberstack.model import EnvelopePart
from rememberstack.model import EvidenceResult
from rememberstack.model import FactResult
from rememberstack.model import FactSupport
from rememberstack.model import Freshness
from rememberstack.model import Grain
from rememberstack.model import IdentityRegime
from rememberstack.model import NegativeKind
from rememberstack.model import P1ChunkText
from rememberstack.model import Validity
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import query_engine as query_engine_module
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.query_engine import believed_at_boundary

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("53000000-0000-0000-0000-000000000001")
_NOW = datetime(2026, 7, 10, tzinfo=UTC)


class _NullSearchIndex:
    """Unused P1 stub: these reads never nominate."""

    def search_claims(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        current_only: bool,
    ) -> tuple[str, ...]:
        """Never called."""
        return ()

    def search_claims_lexical(self, **_: object) -> tuple[str, ...]:
        """Never called."""
        return ()

    def search_chunks(self, **_: object) -> tuple[str, ...]:
        """Never called."""
        return ()

    def search_chunks_lexical(self, **_: object) -> tuple[str, ...]:
        """Never called."""
        return ()

    def chunk_texts(self, **_: object) -> dict[str, P1ChunkText]:
        """Never called."""
        return {}

    def search_facts(
        self, *, deployment_id: str, vector: tuple[float, ...], k: int, kind: str | None
    ) -> tuple[str, ...]:
        """Never called."""
        return ()


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for real envelope proofs")
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
    """A corpus with a contradiction group and a withdrawn-support fact."""

    def __init__(self, *, engine: Engine) -> None:
        """Seed a 2-side contradiction, a 3-side one, and a withdrawn fact."""
        self.engine = engine
        self.ids: dict[str, UUID] = {}
        self.rel: dict[str, UUID] = {}
        self.group = uuid4()
        self.big_group = uuid4()
        with engine.begin() as connection:
            for name, kind in (
                ("Alice", "Person"),
                ("Bob", "Person"),
                ("Acme", "Organization"),
                ("Contoso", "Organization"),
                ("Vector DBs", "Concept"),
                ("Graph DBs", "Concept"),
                ("KV DBs", "Concept"),
            ):
                entity_id = uuid4()
                self.ids[name] = entity_id
                connection.execute(
                    text(
                        "INSERT INTO entities (entity_id, deployment_id, type,"
                        " canonical_name, normalized_name)"
                        " VALUES (:e, :d, :t, :n, lower(:n))"
                    ),
                    {"e": entity_id, "d": _DEPLOYMENT_ID, "t": kind, "n": name},
                )
            # a live 2-side contradiction: Alice can't work for both at once
            self._relation(
                connection, "for_acme", "Alice", "works_for", "Acme", group=self.group
            )
            self._relation(
                connection,
                "for_contoso",
                "Alice",
                "works_for",
                "Contoso",
                group=self.group,
            )
            # a 3-side group (for the cap/continuation path)
            self._relation(
                connection,
                "knows_vector",
                "Alice",
                "knows_about",
                "Vector DBs",
                group=self.big_group,
            )
            self._relation(
                connection,
                "knows_graph",
                "Alice",
                "knows_about",
                "Graph DBs",
                group=self.big_group,
            )
            self._relation(
                connection,
                "knows_kv",
                "Alice",
                "knows_about",
                "KV DBs",
                group=self.big_group,
            )
            # a fact whose support was withdrawn (still returned, flagged)
            self._relation(connection, "bob_acme", "Bob", "works_for", "Acme")
            connection.execute(
                text(
                    "INSERT INTO review_queue (review_id, deployment_id,"
                    " item_kind, candidate, blast_radius, confidence,"
                    " expected_impact, status)"
                    " VALUES (:r, :d, 'support_withdrawn', :c, 1, 0.5, 0.5,"
                    " 'pending')"
                ).bindparams(_json_bind()),
                {
                    "r": uuid4(),
                    "d": _DEPLOYMENT_ID,
                    "c": {
                        "fact_kind": "relation",
                        "fact_id": str(self.rel["bob_acme"]),
                    },
                },
            )

    def _relation(
        self,
        connection: object,
        key: str,
        subject: str,
        predicate: str,
        obj: str,
        *,
        group: UUID | None = None,
    ) -> None:
        relation_id = uuid4()
        self.rel[key] = relation_id
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id,"
                " normalizer_version, fact_label, evidence_count, valid_from,"
                " ingested_at, contradiction_group)"
                " VALUES (:r, :d, :s, :p, :o, 'toy', :label, 2, '2024-01-01+00',"
                " :ing, :g)"
            ),
            {
                "r": relation_id,
                "d": _DEPLOYMENT_ID,
                "s": self.ids[subject],
                "p": predicate,
                "o": self.ids[obj],
                "label": f"{subject} {predicate} {obj}",
                "ing": _NOW,
                "g": group,
            },
        )


def _json_bind():  # noqa: ANN202
    """Bind the review_queue candidate as jsonb."""
    from sqlalchemy import bindparam
    from sqlalchemy import JSON

    return bindparam("c", type_=JSON)


@pytest.fixture()
def corpus(database_engine: Engine) -> _Corpus:
    """A fresh deployment and seeded corpus per proof."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="envelope-test",
            name="Envelope contract proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    return _Corpus(engine=database_engine)


def _engine(corpus: _Corpus) -> QueryEngine:
    """A QueryEngine over the seeded corpus."""
    return QueryEngine(
        engine=corpus.engine,
        search_index=_NullSearchIndex(),
        model_provider=FakeModelProvider(generate_payloads={}),
        embedding_model="toy",
    )


# --- S23: contradiction co-members -----------------------------------------


def test_a_contradiction_surfaces_both_sides(corpus: _Corpus) -> None:
    """S23: reading the revenue relations returns both figures, and each
    carries the OTHER as a co-member — the contradiction is never resolved."""
    answer = _engine(corpus).lookup_relations(
        deployment_id=_DEPLOYMENT_ID,
        subject_entity_id=corpus.ids["Alice"],
        predicate="works_for",
    )
    assert len(answer.facts) == 2
    for fact in answer.facts:
        assert fact.contradiction is not None
        assert fact.contradiction.group_id == corpus.group
        assert fact.contradiction.total == 1
        (co_member,) = fact.contradiction.co_members
        assert co_member.fact_id != fact.fact_id  # the OTHER side, never itself


def test_a_one_sided_query_still_carries_the_contradiction(corpus: _Corpus) -> None:
    """S23 contract: even a query that returns a single side must disclose the
    contradiction — one-sided-with-no-indication is a contract violation."""
    answer = _engine(corpus).lookup_relations(
        deployment_id=_DEPLOYMENT_ID,
        subject_entity_id=corpus.ids["Alice"],
        predicate="works_for",
        object_entity_id=corpus.ids["Acme"],
    )
    (fact,) = answer.facts  # only the Acme side matched the filter
    assert fact.contradiction is not None
    assert fact.contradiction.co_members[0].fact_id == corpus.rel["for_contoso"]


def test_the_contradiction_cap_is_disclosed_with_a_continuation(
    corpus: _Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S23: beyond the inline cap the block still carries group_id, returned,
    total, and a continuation — bounded like every hub answer, never silent."""
    monkeypatch.setattr(query_engine_module, "CONTRADICTION_COMEMBER_CAP", 1)
    answer = _engine(corpus).lookup_relations(
        deployment_id=_DEPLOYMENT_ID,
        subject_entity_id=corpus.ids["Alice"],
        predicate="knows_about",
        object_entity_id=corpus.ids["Vector DBs"],
    )
    (fact,) = answer.facts
    assert fact.contradiction is not None
    assert fact.contradiction.total == 2  # two other sides
    assert fact.contradiction.returned == 1  # capped
    assert fact.contradiction.continuation is not None  # paging is offered


# --- D54: the support marker -----------------------------------------------


def test_a_withdrawn_fact_is_flagged_not_hidden(corpus: _Corpus) -> None:
    """D54: an open support_withdrawn flag marks the fact withdrawn, but the
    fact is still returned — the agent sees the ground moved."""
    answer = _engine(corpus).lookup_relations(
        deployment_id=_DEPLOYMENT_ID, subject_entity_id=corpus.ids["Bob"]
    )
    (fact,) = answer.facts
    assert fact.support is FactSupport.WITHDRAWN

    unaffected = _engine(corpus).lookup_relations(
        deployment_id=_DEPLOYMENT_ID,
        subject_entity_id=corpus.ids["Alice"],
        predicate="works_for",
    )
    assert all(fact.support is FactSupport.CURRENT for fact in unaffected.facts)


def test_hydrate_also_discloses_contradiction_and_support(corpus: _Corpus) -> None:
    """The audit hop discloses the same S23 contradiction and D54 support as a
    lookup — a contradicted relation is never hydrated one-sided, and a
    withdrawn one is flagged (Codex finding)."""
    engine = _engine(corpus)
    hydrated = engine.hydrate_relation(
        deployment_id=_DEPLOYMENT_ID, relation_id=corpus.rel["for_acme"]
    )
    (fact,) = hydrated.facts
    assert fact.contradiction is not None
    assert fact.contradiction.co_members[0].fact_id == corpus.rel["for_contoso"]

    withdrawn = engine.hydrate_relation(
        deployment_id=_DEPLOYMENT_ID, relation_id=corpus.rel["bob_acme"]
    )
    assert withdrawn.facts[0].support is FactSupport.WITHDRAWN


# --- S47: composite parts are single-grain ---------------------------------


def test_a_composite_answer_is_explicitly_single_grain_parts() -> None:
    """S47: a said-vs-believe answer is two labeled single-grain parts."""
    said = EnvelopePart(
        grain=Grain.EVIDENCE,
        label="said",
        evidence=(
            EvidenceResult(
                claim_id=uuid4(),
                doc_id=uuid4(),
                chunk_id=uuid4(),
                claim_text="Alice said pricing rose.",
                source_span="pricing rose",
                char_start=0,
                char_end=12,
                is_attributed=True,
                is_current_testimony=True,
            ),
        ),
    )
    believed = EnvelopePart(
        grain=Grain.FACT,
        label="believed",
        facts=(
            FactResult(
                fact_id=uuid4(),
                kind="relation",
                label="Pricing is $10.",
                evidence_count=3,
                validity=Validity(
                    valid_from=None,
                    valid_until=None,
                    ingested_at=_NOW,
                    invalidated_at=None,
                ),
            ),
        ),
    )
    envelope = Envelope(
        grain=Grain.COMPOSITE,
        parts=(said, believed),
        freshness=Freshness(pg_live_ts=_NOW),
    )
    assert [part.grain for part in envelope.parts] == [Grain.EVIDENCE, Grain.FACT]


def test_parts_require_a_composite_grain() -> None:
    """A non-composite envelope may not carry parts (the discipline is typed)."""
    part = EnvelopePart(grain=Grain.FACT)
    with pytest.raises(ValidationError, match="composite"):
        Envelope(grain=Grain.FACT, parts=(part,), freshness=Freshness(pg_live_ts=_NOW))


def test_a_part_may_not_itself_be_composite() -> None:
    """Each part is strictly single-grain — no nested blending (S47)."""
    with pytest.raises(ValidationError, match="single-grain"):
        EnvelopePart(grain=Grain.COMPOSITE)


def test_a_part_may_not_carry_another_grains_payload() -> None:
    """A fact-grain part carrying evidence records is blended, not
    single-grain — the discipline is enforced per part (Codex finding)."""
    span = EvidenceResult(
        claim_id=uuid4(),
        doc_id=uuid4(),
        chunk_id=uuid4(),
        claim_text="x",
        source_span="x",
        char_start=0,
        char_end=1,
        is_attributed=True,
        is_current_testimony=True,
    )
    with pytest.raises(ValidationError, match="another grain"):
        EnvelopePart(grain=Grain.FACT, evidence=(span,))


def test_a_composite_may_not_blend_parts_with_top_level_payload() -> None:
    """A composite's data lives in parts[], never also blended into the
    top-level result tuples (Codex finding)."""
    fact = FactResult(
        fact_id=uuid4(),
        kind="relation",
        label="Pricing is $10.",
        evidence_count=1,
        validity=Validity(
            valid_from=None, valid_until=None, ingested_at=_NOW, invalidated_at=None
        ),
    )
    with pytest.raises(ValidationError, match="blended"):
        Envelope(
            grain=Grain.COMPOSITE,
            parts=(EnvelopePart(grain=Grain.FACT, facts=(fact,)),),
            facts=(fact,),  # also blended at the top — forbidden
            freshness=Freshness(pg_live_ts=_NOW),
        )


# --- S61 identity regime, horizons, and the negative taxonomy --------------


def test_reads_echo_the_current_identity_regime(corpus: _Corpus) -> None:
    """S61: a read states which identity boundary answered — current by
    default (following today's aliases and merges)."""
    answer = _engine(corpus).lookup_relations(
        deployment_id=_DEPLOYMENT_ID, subject_entity_id=corpus.ids["Bob"]
    )
    assert answer.identity_regime is IdentityRegime.CURRENT
    assert set(IdentityRegime) == {IdentityRegime.CURRENT, IdentityRegime.AS_OF}


def test_believed_at_before_a_finite_horizon_is_a_boundary() -> None:
    """§3: a believed_at before a channel's finite horizon is a typed
    boundary; an unbounded (null) horizon never triggers one."""
    past = datetime(2020, 1, 1, tzinfo=UTC)
    horizon = datetime(2025, 1, 1, tzinfo=UTC)
    boundary = believed_at_boundary(believed_at=past, horizon=horizon)
    assert boundary is not None
    assert boundary.kind is NegativeKind.BOUNDARY
    assert boundary.workaround is not None
    # unbounded (D69 P2) never bounds a query
    assert believed_at_boundary(believed_at=past, horizon=None) is None
    assert believed_at_boundary(believed_at=None, horizon=horizon) is None


def test_the_negative_taxonomy_is_frozen_at_three_kinds() -> None:
    """S29/S39/S55: exactly three kinds, and deliberately no `denied` — the
    taxonomy is safe to freeze because forgotten content is empty-shaped."""
    assert {kind.value for kind in NegativeKind} == {
        "unknown_entity",
        "known_empty",
        "boundary",
    }
