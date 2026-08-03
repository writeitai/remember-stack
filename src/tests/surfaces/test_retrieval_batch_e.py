"""Batch E proofs for deterministic hybrid refill and claim grouping."""

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

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import Envelope
from rememberstack.model import P1ChunkText
from rememberstack.spine import CANONICAL_RECIPES
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.recipes import GRAPH_RECIPES
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces import RecipeExecutor
from rememberstack.surfaces.recipe_surface import recipe_descriptors

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("5c000000-0000-0000-0000-000000000001")
_NOW = datetime(2026, 8, 3, 15, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the real PostgreSQL confirmation path."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for Batch E proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


class _CountingClaimIndex:
    """One deterministic candidate pool with per-channel read counters."""

    def __init__(self, *, claim_ids: tuple[UUID, ...]) -> None:
        self.claim_ids = tuple(str(claim_id) for claim_id in claim_ids)
        self.semantic_calls = 0
        self.lexical_calls = 0

    def search_claims(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        current_only: bool,
    ) -> tuple[str, ...]:
        self.semantic_calls += 1
        return self.claim_ids[:k]

    def search_claims_lexical(
        self, *, deployment_id: str, query: str, k: int, current_only: bool
    ) -> tuple[str, ...]:
        self.lexical_calls += 1
        return self.claim_ids[:k]

    def search_chunks(self, **_: object) -> tuple[str, ...]:
        return ()

    def search_chunks_lexical(self, **_: object) -> tuple[str, ...]:
        return ()

    def chunk_texts(self, **_: object) -> dict[str, P1ChunkText]:
        return {}

    def search_facts(self, **_: object) -> tuple[str, ...]:
        return ()


class _Corpus:
    """Live, absent, repeated-lineage, and tombstoned claim candidates."""

    def __init__(self, *, engine: Engine) -> None:
        self.engine = engine
        self.claims: dict[str, UUID] = {}
        self.docs: dict[str, UUID] = {}
        self.stale_claim_id = uuid4()
        with engine.begin() as connection:
            self._seed_claims(connection)

    def _document(self, connection: Connection, *, key: str) -> UUID:
        existing = self.docs.get(key)
        if existing is not None:
            return existing
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
                "ref": f"batch-e-{key}",
                "title": f"Batch E {key}",
            },
        )
        return doc_id

    def _claim(
        self, connection: Connection, *, key: str, claim_text: str, doc_key: str
    ) -> UUID:
        claim_id = uuid4()
        self.claims[key] = claim_id
        doc_id = self._document(connection, key=doc_key)
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, is_current_testimony, extractor_version,"
                " ingested_at) VALUES (:claim, :deployment, :doc, :chunk, :body,"
                " :body, 0, :end, true, true, true, 'batch-e', :at)"
            ),
            {
                "claim": claim_id,
                "deployment": _DEPLOYMENT_ID,
                "doc": doc_id,
                "chunk": uuid4(),
                "body": claim_text,
                "end": len(claim_text),
                "at": _NOW,
            },
        )
        return claim_id

    def _seed_claims(self, connection: Connection) -> None:
        self._claim(
            connection,
            key="representative",
            claim_text="Café Launch",
            doc_key="repeated-lineage",
        )
        self._claim(
            connection,
            key="nfkc",
            claim_text="Ｃａｆé Ｌａｕｎｃｈ",
            doc_key="repeated-lineage",
        )
        self._claim(
            connection, key="case", claim_text="CAFÉ LAUNCH", doc_key="repeated-lineage"
        )
        self._claim(
            connection,
            key="whitespace",
            claim_text="Café\t\n  Launch",
            doc_key="repeated-lineage",
        )
        self._claim(
            connection,
            key="punctuation",
            claim_text="…Café Launch!!!",
            doc_key="independent-lineage",
        )
        self._claim(
            connection,
            key="internal-punctuation",
            claim_text="Café-Launch",
            doc_key="internal-punctuation",
        )
        self._claim(
            connection, key="different", claim_text="Café Landing", doc_key="different"
        )
        self._claim(
            connection, key="tombstoned", claim_text="café launch", doc_key="tombstoned"
        )
        self._claim(connection, key="tail-a", claim_text="Tail alpha", doc_key="tail-a")
        self._claim(connection, key="tail-b", claim_text="Tail beta", doc_key="tail-b")
        connection.execute(
            text(
                "UPDATE documents SET deleted_at = :at"
                " WHERE deployment_id = :deployment AND doc_id = :doc"
            ),
            {"at": _NOW, "deployment": _DEPLOYMENT_ID, "doc": self.docs["tombstoned"]},
        )

    def execute_claim_hybrid(
        self, *, claim_ids: tuple[UUID, ...], k: int
    ) -> tuple[Envelope, _CountingClaimIndex]:
        index = _CountingClaimIndex(claim_ids=claim_ids)
        engine = QueryEngine(
            engine=self.engine,
            search_index=index,
            model_provider=FakeModelProvider(generate_payloads={}),
            embedding_model="batch-e",
        )
        recipe = next(
            recipe for recipe in CANONICAL_RECIPES if recipe.name == "claims_hybrid_rrf"
        )
        answer = RecipeExecutor(query_engine=engine).execute(
            deployment_id=_DEPLOYMENT_ID,
            recipe=recipe,
            arguments={"query": "launch", "k": k, "candidate_k": len(claim_ids)},
        )
        return answer, index


@pytest.fixture(scope="module")
def corpus(database_engine: Engine) -> _Corpus:
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="batch-e",
            name="Retrieval Batch E",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    return _Corpus(engine=database_engine)


def test_confirmation_drop_refills_from_existing_ranked_tail(corpus: _Corpus) -> None:
    answer, index = corpus.execute_claim_hybrid(
        claim_ids=(
            corpus.stale_claim_id,
            corpus.claims["tail-a"],
            corpus.claims["tail-b"],
        ),
        k=2,
    )

    assert tuple(record.claim_id for record in answer.evidence) == (
        corpus.claims["tail-a"],
        corpus.claims["tail-b"],
    )
    assert answer.dropped_by_hydration == 1
    assert index.semantic_calls == 1
    assert index.lexical_calls == 1


def test_exhausted_pool_stays_honestly_below_k_without_renomination(
    corpus: _Corpus,
) -> None:
    answer, index = corpus.execute_claim_hybrid(
        claim_ids=(corpus.stale_claim_id, corpus.claims["tail-a"]), k=2
    )

    assert tuple(record.claim_id for record in answer.evidence) == (
        corpus.claims["tail-a"],
    )
    assert answer.dropped_by_hydration == 1
    assert index.semantic_calls == 1
    assert index.lexical_calls == 1


def test_exact_normalizer_groups_only_equal_confirmed_claims(corpus: _Corpus) -> None:
    grouped_keys = ("representative", "nfkc", "case", "whitespace", "punctuation")
    answer, _index = corpus.execute_claim_hybrid(
        claim_ids=tuple(
            corpus.claims[key]
            for key in (
                *grouped_keys,
                "tombstoned",
                "internal-punctuation",
                "different",
            )
        ),
        k=8,
    )

    assert tuple(record.claim_id for record in answer.evidence) == (
        corpus.claims["representative"],
        corpus.claims["internal-punctuation"],
        corpus.claims["different"],
    )
    representative = answer.evidence[0]
    assert representative.corroboration_count == 2
    assert representative.grouped_claim_ids == tuple(
        corpus.claims[key] for key in grouped_keys
    )
    assert corpus.claims["tombstoned"] not in representative.grouped_claim_ids
    assert answer.dropped_by_hydration == 1

    with corpus.engine.connect() as connection:
        confirmed_ids = set(
            connection.execute(
                text(
                    "SELECT c.claim_id FROM claims c"
                    " LEFT JOIN documents d ON d.deployment_id = c.deployment_id"
                    " AND d.doc_id = c.doc_id"
                    " WHERE c.deployment_id = :deployment"
                    " AND c.claim_id = ANY(:claim_ids)"
                    " AND c.is_current_testimony"
                    " AND (d.doc_id IS NULL OR d.deleted_at IS NULL)"
                ),
                {
                    "deployment": _DEPLOYMENT_ID,
                    "claim_ids": list(representative.grouped_claim_ids),
                },
            ).scalars()
        )
    assert confirmed_ids == set(representative.grouped_claim_ids)


def test_affected_recipe_versions_and_disclosures_are_visible_in_catalog() -> None:
    recipes = {recipe.name: recipe for recipe in (*CANONICAL_RECIPES, *GRAPH_RECIPES)}
    descriptors = {
        descriptor.name: descriptor
        for descriptor in recipe_descriptors(
            recipes=tuple(sorted(recipes.values(), key=lambda recipe: recipe.name))
        )
    }

    assert {
        name: recipes[name].version
        for name in (
            "claims_hybrid_rrf",
            "chunks_hybrid_rrf",
            "question_context",
            "multi_hop_context",
        )
    } == {
        "claims_hybrid_rrf": 6,
        "chunks_hybrid_rrf": 3,
        "question_context": 3,
        "multi_hop_context": 2,
    }
    assert all(
        descriptors[name].version == recipes[name].version
        for name in (
            "claims_hybrid_rrf",
            "chunks_hybrid_rrf",
            "question_context",
            "multi_hop_context",
        )
    )
    assert "corroboration" in descriptors["claims_hybrid_rrf"].description
    assert "corroboration" in descriptors["question_context"].description
    assert "corroboration" in descriptors["multi_hop_context"].description
