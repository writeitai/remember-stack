"""Query-time resolve over the non-LLM tiers T0–T3.

Exact aliases stay exact. Inflection and spelling ride trigram and phonetic
blocking. Embedding search is only the residue, and it never picks one
neighbor out of several.
"""

from collections.abc import Iterator
import math
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

from rememberstack.adapters import PostgresP1Index
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import AssuredOperationName
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import EmbeddingRequest
from rememberstack.model import EntityCandidate
from rememberstack.model import Envelope
from rememberstack.model import Grain
from rememberstack.model import NegativeKind
from rememberstack.model import Truncation
from rememberstack.ports.p1_index import ENTITY_INPUT_POLICY
from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS
from rememberstack.spine import CANONICAL_OPERATIONS
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.entity_registry import normalized_lemma
from rememberstack.spine.settings import load_database_settings
from rememberstack.spine.surface_cost import SqlSurfaceCostRecorder
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.operation_executor import OperationExecutor
from rememberstack.surfaces.query_engine import _envelope
from rememberstack.surfaces.query_engine import _freshness
from rememberstack.surfaces.query_engine import QUERY_RESOLVE_CANDIDATE_LIMIT
from rememberstack.surfaces.query_engine import QUERY_RESOLVE_TRIGRAM_FLOOR
from tests.database_reset import reset_database
from tests.surfaces.lineage_seed import seed_entity_mention
from tests.surfaces.lineage_seed import seed_live_document_lineage

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("71000000-0000-0000-0000-000000000001")
_OTHER_DEPLOYMENT_ID = UUID("71000000-0000-0000-0000-000000000002")
_EMBEDDING_MODEL = "query-resolve"

_JIRI = UUID("71100000-0000-0000-0000-000000000001")
_ALICE = UUID("71100000-0000-0000-0000-000000000002")
_ALICJA = UUID("71100000-0000-0000-0000-000000000003")
_CATHERINE = UUID("71100000-0000-0000-0000-000000000004")
_ACME = UUID("71100000-0000-0000-0000-000000000005")
_FOCAL = UUID("71100000-0000-0000-0000-000000000006")
_MERGED = UUID("71100000-0000-0000-0000-000000000007")
_FORGOTTEN = UUID("71100000-0000-0000-0000-000000000008")
_PROFILE_A = UUID("71100000-0000-0000-0000-000000000009")
_PROFILE_B = UUID("71100000-0000-0000-0000-00000000000a")
_PROFILE_FAR = UUID("71100000-0000-0000-0000-00000000000b")
_OTHER_ALICE = UUID("71100000-0000-0000-0000-00000000000c")
_QUINN_A = UUID("71100000-0000-0000-0000-00000000000d")
_QUINN_B = UUID("71100000-0000-0000-0000-00000000000e")
_ZZBLOCK = UUID("71100000-0000-0000-0000-00000000000f")


def _alpha_id(number: int) -> UUID:
    """Stable id so truncation order follows the entity id, not insertion."""
    return UUID(f"71200000-0000-0000-0000-{number:012d}")


class _Corpus:
    """One deployment of current aliases plus a second deployment's twin."""

    def __init__(self, *, engine: Engine) -> None:
        self.engine = engine
        self.provider = FakeModelProvider()
        self.index = PostgresP1Index(engine=engine, embedding_model=_EMBEDDING_MODEL)
        with engine.begin() as connection:
            lineage = seed_live_document_lineage(
                connection=connection,
                deployment_id=_DEPLOYMENT_ID,
                label="query-resolve",
                title="Query resolve",
                source_ref="query-resolve",
            )
            other = seed_live_document_lineage(
                connection=connection,
                deployment_id=_OTHER_DEPLOYMENT_ID,
                label="query-resolve-other",
                title="Other deployment",
                source_ref="query-resolve-other",
            )
            self._chunk = lineage.chunk_id
            self._doc = lineage.doc_id
            people = (
                (_JIRI, "Jiří Puc", "Jiří Puc"),
                (_ALICE, "Alice", "Alice"),
                (_ALICJA, "Alicja", "Alicja"),
                (_CATHERINE, "Catherine", "Catherine"),
                (_ACME, "Acme Corporation", "Acme"),
                (_FOCAL, "Context Org", "Context Org"),
                (_PROFILE_A, "Profile A", "Profile A"),
                (_PROFILE_B, "Profile B", "Profile B"),
                (_PROFILE_FAR, "Profile Far", "Profile Far"),
                (_QUINN_A, "Quinn A", "Quinn"),
                (_QUINN_B, "Quinn B", "Quinn"),
                (_ZZBLOCK, "Zzblock Person", "Zzblock"),
            )
            for entity_id, canonical_name, alias in people:
                self._entity(
                    connection=connection,
                    deployment_id=_DEPLOYMENT_ID,
                    entity_id=entity_id,
                    canonical_name=canonical_name,
                    alias=alias,
                    doc_id=lineage.doc_id,
                    chunk_id=lineage.chunk_id,
                )
            for number in range(1, 13):
                name = f"Alpha {number}"
                self._entity(
                    connection=connection,
                    deployment_id=_DEPLOYMENT_ID,
                    entity_id=_alpha_id(number),
                    canonical_name=name,
                    alias=name,
                    doc_id=lineage.doc_id,
                    chunk_id=lineage.chunk_id,
                )
            self._entity(
                connection=connection,
                deployment_id=_DEPLOYMENT_ID,
                entity_id=_FORGOTTEN,
                canonical_name="Ghost Person",
                alias="Ghost Person",
                doc_id=lineage.doc_id,
                chunk_id=lineage.chunk_id,
                mention=False,
            )
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id,"
                    " canonical_name, normalized_name, status, merged_into)"
                    " VALUES (:entity, :deployment, 'A. Novak', 'a. novak',"
                    " 'merged', :survivor)"
                ),
                {"entity": _MERGED, "deployment": _DEPLOYMENT_ID, "survivor": _ALICE},
            )
            connection.execute(
                text(
                    "INSERT INTO aliases (alias_id, deployment_id, entity_id,"
                    " alias_text, normalized_lemma, provenance) VALUES"
                    " (:alias, :deployment, :entity, 'A. Novak', 'a. novak',"
                    " 'llm_canonical')"
                ),
                {"alias": uuid4(), "deployment": _DEPLOYMENT_ID, "entity": _MERGED},
            )
            connection.execute(
                text(
                    "INSERT INTO aliases (alias_id, deployment_id, entity_id,"
                    " alias_text, normalized_lemma, provenance) VALUES"
                    " (:alias, :deployment, :entity, 'Zzblockk', :lemma,"
                    " 'source')"
                ),
                {
                    "alias": uuid4(),
                    "deployment": _DEPLOYMENT_ID,
                    "entity": _ZZBLOCK,
                    "lemma": normalized_lemma(surface="Zzblockk"),
                },
            )
            self._entity(
                connection=connection,
                deployment_id=_OTHER_DEPLOYMENT_ID,
                entity_id=_OTHER_ALICE,
                canonical_name="Alice Elsewhere",
                alias="Alice",
                doc_id=other.doc_id,
                chunk_id=other.chunk_id,
            )
            self._works_for(
                connection=connection,
                subject_id=_ALICJA,
                object_id=_FOCAL,
                doc_id=lineage.doc_id,
                chunk_id=lineage.chunk_id,
            )
        self.index.configure_channels(deployment_id=_DEPLOYMENT_ID)
        self.provider.embedded_texts.clear()

    def engine_for(
        self, *, surface_cost: SqlSurfaceCostRecorder | None = None
    ) -> QueryEngine:
        """A query engine bound to this corpus's deterministic embedder."""
        return QueryEngine(
            engine=self.engine,
            search_index=self.index,
            model_provider=self.provider,
            embedding_model=_EMBEDDING_MODEL,
            surface_cost=surface_cost,
        )

    def _entity(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        entity_id: UUID,
        canonical_name: str,
        alias: str,
        doc_id: UUID,
        chunk_id: UUID,
        mention: bool = True,
    ) -> None:
        lemma = normalized_lemma(surface=alias)
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id, canonical_name,"
                " normalized_name) VALUES (:entity, :deployment, :name, :lemma)"
            ),
            {
                "entity": entity_id,
                "deployment": deployment_id,
                "name": canonical_name,
                "lemma": normalized_lemma(surface=canonical_name),
            },
        )
        connection.execute(
            text(
                "INSERT INTO aliases (alias_id, deployment_id, entity_id,"
                " alias_text, normalized_lemma, provenance) VALUES"
                " (:alias, :deployment, :entity, :alias_text, :lemma,"
                " 'llm_canonical')"
            ),
            {
                "alias": uuid4(),
                "deployment": deployment_id,
                "entity": entity_id,
                "alias_text": alias,
                "lemma": lemma,
            },
        )
        if mention:
            seed_entity_mention(
                connection=connection,
                deployment_id=deployment_id,
                entity_id=entity_id,
                doc_id=doc_id,
                chunk_id=chunk_id,
                surface_form=alias,
                normalized_lemma=lemma,
                resolver_version="query-resolve",
            )

    def _works_for(
        self,
        *,
        connection: Connection,
        subject_id: UUID,
        object_id: UUID,
        doc_id: UUID,
        chunk_id: UUID,
    ) -> None:
        relation_id = uuid4()
        claim_id = uuid4()
        body = "Alicja works for Context Org"
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, is_current_testimony, extractor_version,"
                " ingested_at) VALUES (:claim, :deployment, :doc, :chunk, :body,"
                " :body, 0, :end, true, true, true, 'query-resolve', now())"
            ),
            {
                "claim": claim_id,
                "deployment": _DEPLOYMENT_ID,
                "doc": doc_id,
                "chunk": chunk_id,
                "body": body,
                "end": len(body),
            },
        )
        connection.execute(
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id,"
                " normalizer_version, evidence_count, ingested_at, valid_from,"
                " valid_precision, window_claim_ids) VALUES"
                " (:relation, :deployment, :subject, 'works_for', :object,"
                " 'query-resolve', 1, now(), '2024-01-01Z', 'open',"
                " ARRAY[:claim]::uuid[])"
            ),
            {
                "relation": relation_id,
                "deployment": _DEPLOYMENT_ID,
                "subject": subject_id,
                "object": object_id,
                "claim": claim_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO relation_evidence (deployment_id, relation_id,"
                " claim_id, doc_id, stance, normalizer_version) VALUES"
                " (:deployment, :relation, :claim, :doc, 'supports',"
                " 'query-resolve')"
            ),
            {
                "deployment": _DEPLOYMENT_ID,
                "relation": relation_id,
                "claim": claim_id,
                "doc": doc_id,
            },
        )


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head on the disposable integration database."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for query-resolve proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def corpus(database_engine: Engine) -> _Corpus:
    """Seed both deployments once; tests that plant vectors restore them."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    for deployment_id, slug, name in (
        (_DEPLOYMENT_ID, "query-resolve", "Query resolve"),
        (_OTHER_DEPLOYMENT_ID, "query-resolve-other", "Other deployment"),
    ):
        DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
            deployment_input=DeploymentBootstrapInput(
                deployment_id=deployment_id,
                slug=slug,
                name=name,
                default_language="en",
                raw_bucket="mem://raw",
                artifacts_bucket="mem://artifacts",
                corpusfs_bucket="mem://corpusfs",
            )
        )
    return _Corpus(engine=database_engine)


def test_exact_alias_resolution_ignores_fuzzy_neighbors(corpus: _Corpus) -> None:
    """T0 returns the current alias and does not widen into trigram neighbors."""
    corpus.provider.embedded_texts.clear()
    answer = corpus.engine_for().resolve(deployment_id=_DEPLOYMENT_ID, name="Alice")

    assert answer.negative is None
    assert answer.truncation is None
    (candidate,) = answer.entities
    assert candidate.entity_id == _ALICE
    assert candidate.tier == "T0"
    assert candidate.canonical_name == "Alice"
    assert corpus.provider.embedded_texts == []
    assert corpus.provider.generated_prompts == []


def test_normalization_folds_case_whitespace_and_accents(corpus: _Corpus) -> None:
    """The query key is the same accent-folded lemma the alias registry stores."""
    answer = corpus.engine_for().resolve(
        deployment_id=_DEPLOYMENT_ID, name="  Jiří   Puc "
    )

    (candidate,) = answer.entities
    assert candidate.entity_id == _JIRI
    assert candidate.tier == "T0"
    assert candidate.canonical_name == "Jiří Puc"


def test_alias_resolves_when_it_is_not_the_canonical_name(corpus: _Corpus) -> None:
    """T0 matches a stored alias and returns the survivor's canonical name."""
    answer = corpus.engine_for().resolve(deployment_id=_DEPLOYMENT_ID, name="Acme")

    (candidate,) = answer.entities
    assert candidate.entity_id == _ACME
    assert candidate.tier == "T0"
    assert candidate.canonical_name == "Acme Corporation"


def test_punctuation_is_not_an_exact_alias_and_still_trigram_matches(
    corpus: _Corpus,
) -> None:
    """A trailing dot misses T0. Trigram wins even when the phonetic code also hits."""
    query = normalized_lemma(surface="Jiri Puc.")
    stored = normalized_lemma(surface="Jiří Puc")
    with corpus.engine.connect() as connection:
        overlap = connection.execute(
            text(
                "SELECT similarity(:query, :stored) >= :floor AS trigram,"
                " daitch_mokotoff(:query) && daitch_mokotoff(:stored) AS phonetic"
            ),
            {"query": query, "stored": stored, "floor": QUERY_RESOLVE_TRIGRAM_FLOOR},
        ).one()
    answer = corpus.engine_for().resolve(deployment_id=_DEPLOYMENT_ID, name="Jiri Puc.")

    assert overlap.trigram is True
    assert overlap.phonetic is True
    (candidate,) = answer.entities
    assert candidate.entity_id == _JIRI
    assert candidate.tier == "T1"


def test_inflected_name_uses_trigram_against_the_stored_canonical_alias(
    corpus: _Corpus,
) -> None:
    """S50: 'Jiřího Puce' is not the stored nominative, and trigram still recalls it."""
    corpus.provider.embedded_texts.clear()
    answer = corpus.engine_for().resolve(
        deployment_id=_DEPLOYMENT_ID, name="Jiřího Puce"
    )

    (candidate,) = answer.entities
    assert candidate.entity_id == _JIRI
    assert candidate.tier == "T1"
    assert candidate.canonical_name == "Jiří Puc"
    assert corpus.provider.embedded_texts == []


def test_phonetic_tier_recalls_a_spelling_trigram_rejects(corpus: _Corpus) -> None:
    """Kathryn/Catherine share a Daitch-Mokotoff code and sit below the trigram floor."""
    corpus.provider.embedded_texts.clear()
    answer = corpus.engine_for().resolve(deployment_id=_DEPLOYMENT_ID, name="Kathryn")

    (candidate,) = answer.entities
    assert candidate.entity_id == _CATHERINE
    assert candidate.tier == "T2"
    assert corpus.provider.embedded_texts == []


def test_trigram_ambiguity_stays_ranked_and_context_reorders_without_hiding(
    corpus: _Corpus,
) -> None:
    """Two inexact neighbors both remain; adjacency raises one and hides neither."""
    engine = corpus.engine_for()
    baseline = engine.resolve(deployment_id=_DEPLOYMENT_ID, name="Alicia")
    narrowed = engine.resolve(
        deployment_id=_DEPLOYMENT_ID, name="Alicia", context_entity_ids=(_FOCAL,)
    )

    assert [item.entity_id for item in baseline.entities] == [_ALICE, _ALICJA]
    assert {item.tier for item in baseline.entities} == {"T1"}
    assert baseline.negative is None
    assert [item.entity_id for item in narrowed.entities] == [_ALICJA, _ALICE]
    assert narrowed.entities[0].context_hits == 1
    assert narrowed.entities[1].context_hits == 0


def test_fuzzy_block_discloses_its_candidate_cap(corpus: _Corpus) -> None:
    """The shared blocking width is a disclosed cap, not a silent top-k."""
    answer = corpus.engine_for().resolve(deployment_id=_DEPLOYMENT_ID, name="Alpha")

    returned = {item.entity_id for item in answer.entities}
    assert len(answer.entities) == QUERY_RESOLVE_CANDIDATE_LIMIT
    assert {_alpha_id(number) for number in range(1, 10)} <= returned
    assert _alpha_id(10) in returned
    assert _alpha_id(11) not in returned
    assert _alpha_id(12) not in returned
    assert {item.tier for item in answer.entities} == {"T1"}
    assert answer.truncation is not None
    assert answer.truncation.truncated
    assert answer.truncation.total_is_exact is False
    assert answer.truncation.reason == "resolve_candidate_limit"
    assert answer.negative is None


def test_merge_redirect_and_forgotten_alias_follow_current_identity(
    corpus: _Corpus,
) -> None:
    """A merged alias returns the survivor; an unproven alias resolves as unknown."""
    engine = corpus.engine_for()
    merged = engine.resolve(deployment_id=_DEPLOYMENT_ID, name="A. Novak")
    forgotten = engine.resolve(deployment_id=_DEPLOYMENT_ID, name="Ghost Person")

    (candidate,) = merged.entities
    assert candidate.entity_id == _ALICE
    assert candidate.canonical_name == "Alice"
    assert candidate.tier == "T0"
    assert forgotten.entities == ()
    assert forgotten.negative is not None
    assert forgotten.negative.kind is NegativeKind.UNKNOWN_ENTITY


def test_resolution_stays_inside_the_deployment(corpus: _Corpus) -> None:
    """The same alias in another deployment is a different trust domain."""
    engine = corpus.engine_for()
    home = engine.resolve(deployment_id=_DEPLOYMENT_ID, name="Alice")
    other = engine.resolve(deployment_id=_OTHER_DEPLOYMENT_ID, name="Alice")

    assert [item.entity_id for item in home.entities] == [_ALICE]
    assert [item.entity_id for item in other.entities] == [_OTHER_ALICE]


def test_blank_name_is_unknown_without_an_embedding_call(corpus: _Corpus) -> None:
    """A lemma that folds to nothing never reaches blocking or the model port."""
    corpus.provider.embedded_texts.clear()
    answer = corpus.engine_for().resolve(deployment_id=_DEPLOYMENT_ID, name="   ")

    assert answer.entities == ()
    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.UNKNOWN_ENTITY
    assert corpus.provider.embedded_texts == []


def test_string_miss_with_no_admissible_profile_is_unknown(corpus: _Corpus) -> None:
    """T3 runs, and a neighbor at or below the reject band is not a candidate."""
    corpus.provider.embedded_texts.clear()
    answer = corpus.engine_for().resolve(deployment_id=_DEPLOYMENT_ID, name="Contoso")

    assert answer.entities == ()
    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.UNKNOWN_ENTITY
    assert corpus.provider.embedded_texts == ["Contoso"]
    assert corpus.provider.generated_prompts == []


def test_embedding_tier_returns_only_profiles_above_the_reject_band(
    corpus: _Corpus,
) -> None:
    """One strong profile is T3; a distant profile is not silently added."""
    vector = _contoso_vector(corpus)
    distant = (1.0,) + (0.0,) * (P1_VECTOR_DIMENSIONS - 1)
    try:
        _plant_profile(corpus, entity_id=_PROFILE_A, vector=vector)
        _plant_profile(corpus, entity_id=_PROFILE_FAR, vector=distant)
        answer = corpus.engine_for().resolve(
            deployment_id=_DEPLOYMENT_ID, name="Contoso"
        )
    finally:
        _clear_profiles(corpus)

    (candidate,) = answer.entities
    assert candidate.entity_id == _PROFILE_A
    assert candidate.tier == "T3"
    assert answer.negative is None


def test_embedding_tier_keeps_every_strong_profile(corpus: _Corpus) -> None:
    """Two profiles above the reject band are ambiguity, not a single guess."""
    vector = _contoso_vector(corpus)
    try:
        _plant_profile(corpus, entity_id=_PROFILE_A, vector=vector)
        _plant_profile(corpus, entity_id=_PROFILE_B, vector=vector)
        answer = corpus.engine_for().resolve(
            deployment_id=_DEPLOYMENT_ID, name="Contoso"
        )
    finally:
        _clear_profiles(corpus)

    assert {item.entity_id for item in answer.entities} == {_PROFILE_A, _PROFILE_B}
    assert {item.tier for item in answer.entities} == {"T3"}
    assert answer.negative is None


def test_unpublished_embedding_channel_is_a_boundary(corpus: _Corpus) -> None:
    """A string miss does not pretend the embedding tier ran when it cannot."""
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE p1_search_channels SET ready = false"
                " WHERE deployment_id = :deployment AND target = 'entities'"
            ),
            {"deployment": _DEPLOYMENT_ID},
        )
    corpus.provider.embedded_texts.clear()
    corpus.provider.generated_prompts.clear()
    try:
        answer = corpus.engine_for().resolve(
            deployment_id=_DEPLOYMENT_ID, name="Contoso"
        )
    finally:
        corpus.index.configure_channels(deployment_id=_DEPLOYMENT_ID)

    assert answer.entities == ()
    assert answer.negative is not None
    assert answer.negative.kind is NegativeKind.BOUNDARY
    assert corpus.provider.embedded_texts == []
    assert corpus.provider.generated_prompts == []


def test_resolve_entity_operation_uses_the_same_cascade(corpus: _Corpus) -> None:
    """The assured identity operation is the cascade, not a private exact path."""
    operation = next(
        item
        for item in CANONICAL_OPERATIONS
        if item.name is AssuredOperationName.RESOLVE_ENTITY
    )
    corpus.provider.generated_prompts.clear()
    result = OperationExecutor(query_engine=corpus.engine_for()).execute(
        deployment_id=_DEPLOYMENT_ID,
        operation=operation,
        arguments={"name": "Jiřího Puce"},
    )

    assert isinstance(result, Envelope)
    assert result.entities[0].entity_id == _JIRI
    assert result.entities[0].tier == "T1"
    assert corpus.provider.generated_prompts == []


def test_exact_homonyms_stay_ranked_and_complete(corpus: _Corpus) -> None:
    """Two current exact aliases are ambiguity, with no fuzzy widening."""
    corpus.provider.embedded_texts.clear()
    answer = corpus.engine_for().resolve(deployment_id=_DEPLOYMENT_ID, name="Quinn")

    assert [item.entity_id for item in answer.entities] == [_QUINN_A, _QUINN_B]
    assert {item.tier for item in answer.entities} == {"T0"}
    assert answer.negative is None
    assert answer.truncation is None
    assert corpus.provider.embedded_texts == []


def test_repeated_aliases_collapse_to_one_trigram_survivor(corpus: _Corpus) -> None:
    """Two inexact aliases of one survivor are one T1 candidate, not two rows."""
    answer = corpus.engine_for().resolve(deployment_id=_DEPLOYMENT_ID, name="Zzblockx")

    (candidate,) = answer.entities
    assert candidate.entity_id == _ZZBLOCK
    assert candidate.tier == "T1"
    assert candidate.canonical_name == "Zzblock Person"


def test_embedding_tier_ranks_stronger_profiles_first(corpus: _Corpus) -> None:
    """Profile similarity orders T3 candidates and does not drop the weaker one."""
    vector = _contoso_vector(corpus)
    weaker = _damped_profile(vector)
    try:
        _plant_profile(corpus, entity_id=_PROFILE_B, vector=vector)
        _plant_profile(corpus, entity_id=_PROFILE_A, vector=weaker)
        answer = corpus.engine_for().resolve(
            deployment_id=_DEPLOYMENT_ID, name="Contoso"
        )
    finally:
        _clear_profiles(corpus)

    assert [item.entity_id for item in answer.entities] == [_PROFILE_B, _PROFILE_A]
    assert {item.tier for item in answer.entities} == {"T3"}
    assert answer.negative is None
    assert corpus.provider.generated_prompts == []


def test_embedding_tier_discloses_its_candidate_cap(corpus: _Corpus) -> None:
    """Profile search uses the same width as blocking and says when it stopped."""
    vector = _contoso_vector(corpus)
    try:
        for number in range(1, QUERY_RESOLVE_CANDIDATE_LIMIT + 2):
            _plant_profile(corpus, entity_id=_alpha_id(number), vector=vector)
        answer = corpus.engine_for().resolve(
            deployment_id=_DEPLOYMENT_ID, name="Contoso"
        )
    finally:
        _clear_profiles(corpus)

    returned = {item.entity_id for item in answer.entities}
    assert len(answer.entities) == QUERY_RESOLVE_CANDIDATE_LIMIT
    assert {_alpha_id(number) for number in range(1, 11)} <= returned
    assert _alpha_id(11) not in returned
    assert {item.tier for item in answer.entities} == {"T3"}
    assert answer.truncation is not None
    assert answer.truncation.truncated
    assert answer.truncation.total_is_exact is False
    assert answer.truncation.reason == "resolve_candidate_limit"
    assert answer.negative is None


def test_embedding_neighbors_do_not_cross_deployments(corpus: _Corpus) -> None:
    """A profile vector in another deployment is not a candidate here."""
    vector = _contoso_vector(corpus)
    corpus.index.configure_channels(deployment_id=_OTHER_DEPLOYMENT_ID)
    try:
        _plant_profile(
            corpus,
            entity_id=_OTHER_ALICE,
            vector=vector,
            deployment_id=_OTHER_DEPLOYMENT_ID,
        )
        home = corpus.engine_for().resolve(deployment_id=_DEPLOYMENT_ID, name="Contoso")
        other = corpus.engine_for().resolve(
            deployment_id=_OTHER_DEPLOYMENT_ID, name="Contoso"
        )
    finally:
        _clear_profiles(corpus, deployment_id=_DEPLOYMENT_ID)
        _clear_profiles(corpus, deployment_id=_OTHER_DEPLOYMENT_ID)

    assert home.entities == ()
    assert home.negative is not None
    assert home.negative.kind is NegativeKind.UNKNOWN_ENTITY
    (candidate,) = other.entities
    assert candidate.entity_id == _OTHER_ALICE
    assert candidate.tier == "T3"


def test_exact_resolve_is_unmetered_and_embedding_resolve_is_owned(
    corpus: _Corpus,
) -> None:
    """Only the T3 embed writes a resolve_entity receipt, and it is not a generation."""
    recorder = SqlSurfaceCostRecorder(
        engine=corpus.engine, deployment_id=_DEPLOYMENT_ID
    )
    engine = corpus.engine_for(surface_cost=recorder)
    corpus.provider.generated_prompts.clear()
    corpus.provider.embedded_texts.clear()
    with corpus.engine.begin() as connection:
        before = int(
            connection.execute(
                text("SELECT count(*) FROM surface_cost_ledger")
            ).scalar_one()
        )
    exact = engine.resolve(deployment_id=_DEPLOYMENT_ID, name="Alice")
    with corpus.engine.begin() as connection:
        after_exact = int(
            connection.execute(
                text("SELECT count(*) FROM surface_cost_ledger")
            ).scalar_one()
        )
    missed = engine.resolve(deployment_id=_DEPLOYMENT_ID, name="Contoso")
    with corpus.engine.connect() as connection:
        receipts = connection.execute(
            text(
                "SELECT surface::text, call_site, outcome::text"
                " FROM surface_cost_ledger ORDER BY occurred_at, cost_id"
            )
        ).all()

    assert exact.entities[0].tier == "T0"
    assert after_exact == before
    assert missed.negative is not None
    assert missed.negative.kind is NegativeKind.UNKNOWN_ENTITY
    assert corpus.provider.generated_prompts == []
    assert corpus.provider.embedded_texts == ["Contoso"]
    assert len(receipts) == before + 1
    assert receipts[-1] == ("lookup", "resolve_entity", "ok")


def test_context_reads_keep_ambiguous_and_capped_resolves_as_boundaries(
    corpus: _Corpus,
) -> None:
    """A later read must not pick one Alicia or one Alpha out of a capped list."""
    engine = corpus.engine_for()
    ambiguous = engine.documents_about(deployment_id=_DEPLOYMENT_ID, entity="Alicia")
    capped = engine.documents_about(deployment_id=_DEPLOYMENT_ID, entity="Alpha")

    assert ambiguous.negative is not None
    assert ambiguous.negative.kind is NegativeKind.BOUNDARY
    assert {item.entity_id for item in ambiguous.entities} == {_ALICE, _ALICJA}
    assert capped.negative is not None
    assert capped.negative.kind is NegativeKind.BOUNDARY
    assert capped.truncation is not None
    assert capped.truncation.truncated
    assert len(capped.entities) == QUERY_RESOLVE_CANDIDATE_LIMIT


def test_assured_resolve_meters_the_embed_on_the_operation_surface(
    corpus: _Corpus,
) -> None:
    """resolve_entity keeps the operation scope and still names the embed site."""
    recorder = SqlSurfaceCostRecorder(
        engine=corpus.engine, deployment_id=_DEPLOYMENT_ID
    )
    engine = corpus.engine_for(surface_cost=recorder)
    operation = next(
        item
        for item in CANONICAL_OPERATIONS
        if item.name is AssuredOperationName.RESOLVE_ENTITY
    )
    corpus.provider.generated_prompts.clear()
    with corpus.engine.connect() as connection:
        before = int(
            connection.execute(
                text("SELECT count(*) FROM surface_cost_ledger")
            ).scalar_one()
        )
    result = OperationExecutor(query_engine=engine).execute(
        deployment_id=_DEPLOYMENT_ID, operation=operation, arguments={"name": "Contoso"}
    )
    with corpus.engine.connect() as connection:
        receipts = connection.execute(
            text(
                "SELECT surface::text, call_site, outcome::text"
                " FROM surface_cost_ledger ORDER BY occurred_at, cost_id"
            )
        ).all()

    assert isinstance(result, Envelope)
    assert result.negative is not None
    assert result.negative.kind is NegativeKind.UNKNOWN_ENTITY
    assert corpus.provider.generated_prompts == []
    assert len(receipts) == before + 1
    assert receipts[-1] == ("operation", "resolve_entity", "ok")


def test_context_resolution_does_not_adopt_an_incomplete_candidate() -> None:
    """A capped unique hit is still not an identity verdict for other reads."""
    candidate = EntityCandidate(
        entity_id=UUID("71100000-0000-0000-0000-0000000000aa"),
        canonical_name="Only Visible",
        tier="T1",
    )
    resolved = _envelope(
        grain=Grain.FACT,
        entities=(candidate,),
        freshness=_freshness(),
        truncation=Truncation(
            truncated=True,
            returned=1,
            estimated_total=2,
            total_is_exact=False,
            reason="resolve_candidate_limit",
        ),
    )
    engine = QueryEngine.__new__(QueryEngine)

    def _resolve(**_kwargs: object) -> Envelope:
        return resolved

    engine.resolve = _resolve  # type: ignore[method-assign]
    entity_id, boundary = engine._resolve_context_entity(
        deployment_id=_DEPLOYMENT_ID, entity="Only", grain=Grain.EVIDENCE
    )

    assert entity_id is None
    assert boundary is not None
    assert boundary.entities == (candidate,)
    assert boundary.truncation is not None
    assert boundary.truncation.truncated
    assert boundary.negative is not None
    assert boundary.negative.kind is NegativeKind.BOUNDARY


def _contoso_vector(corpus: _Corpus) -> tuple[float, ...]:
    """The deterministic vector the fake embedder will produce for Contoso."""
    vector = corpus.provider.embed(
        request=EmbeddingRequest(
            model=_EMBEDDING_MODEL, texts=("Contoso",), dimensions=P1_VECTOR_DIMENSIONS
        )
    ).vectors[0]
    corpus.provider.embedded_texts.clear()
    return vector


def _plant_profile(
    corpus: _Corpus,
    *,
    entity_id: UUID,
    vector: tuple[float, ...],
    deployment_id: UUID = _DEPLOYMENT_ID,
) -> None:
    """Attach one current-generation profile vector without going through refresh."""
    literal = "[" + ",".join(repr(value) for value in vector) + "]"
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE entities SET profile_summary = 'planted',"
                " embedding = CAST(:embedding AS vector),"
                " embedding_model = :model,"
                " embedding_input_policy_version = :policy,"
                " embedding_text_hash = 'query-resolve'"
                " WHERE deployment_id = :deployment AND entity_id = :entity"
            ),
            {
                "embedding": literal,
                "model": _EMBEDDING_MODEL,
                "policy": ENTITY_INPUT_POLICY,
                "deployment": deployment_id,
                "entity": entity_id,
            },
        )


def _clear_profiles(corpus: _Corpus, *, deployment_id: UUID = _DEPLOYMENT_ID) -> None:
    """Remove planted vectors so later misses are not false embedding hits."""
    with corpus.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE entities SET profile_summary = NULL, embedding = NULL,"
                " embedding_model = NULL, embedding_input_policy_version = NULL,"
                " embedding_text_hash = NULL WHERE deployment_id = :deployment"
            ),
            {"deployment": deployment_id},
        )


def _damped_profile(vector: tuple[float, ...]) -> tuple[float, ...]:
    """A same-dimension vector whose cosine with ``vector`` sits above the reject band."""
    half = len(vector) // 2
    damped = vector[:half] + tuple(0.0 for _ in vector[half:])
    score = _cosine(vector, damped)
    if score <= 0.60:
        damped = tuple(
            value if index < half else value * 0.25
            for index, value in enumerate(vector)
        )
        score = _cosine(vector, damped)
    if not 0.60 < score < 1.0:
        raise AssertionError(f"damped profile cosine {score} is outside the open band")
    return damped


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """Ordinary cosine, matching the semantic channel's similarity score."""
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    return dot / (left_norm * right_norm)
