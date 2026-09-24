"""D134 §4 document filters on claim, chunk and fact search, on real PostgreSQL.

Documents are seeded as complete live lineages with ``document_metadata`` and
``document_people`` rows; claims carry explicit ``chunk_claims`` occurrences,
so the per-occurrence rule is exercised on the rows production writes.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from datetime import UTC
from pathlib import Path
from unittest.mock import MagicMock
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

from remember.models import DocumentSearchFilters
from rememberstack.adapters import PostgresP1Index
from rememberstack.core.embedding_input_policy import EMBEDDING_INPUT_POLICY_VERSION
from rememberstack.core.embedding_input_policy import embedding_text_hash
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import P1ChunkRow
from rememberstack.model import P1ClaimRow
from rememberstack.model import P1FactRow
from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS
from rememberstack.ports.p1_index import P1Nomination
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import QueryEngine
from tests.database_reset import reset_database
from tests.surfaces.lineage_seed import LiveDocumentLineage
from tests.surfaces.lineage_seed import seed_entity_mention
from tests.surfaces.lineage_seed import seed_live_document_lineage

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("5f000000-0000-0000-0000-0000000d0134")
_MODEL = "qwen/qwen3-embedding-8b"
_NOW = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
_ALICE = DocumentSearchFilters(authors=("alice",))


def _vector(*, axis: int) -> tuple[float, ...]:
    values = [0.0] * P1_VECTOR_DIMENSIONS
    values[axis] = 1.0
    return tuple(values)


_NEAR = _vector(axis=0)
_FAR = _vector(axis=1)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head over the integration database."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for D134 filter proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def index(database_engine: Engine) -> PostgresP1Index:
    """A fresh deployment with every P1 channel configured."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="d134-filters",
            name="D134 filters",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    index = PostgresP1Index(engine=database_engine, embedding_model=_MODEL)
    index.configure_channels(deployment_id=_DEPLOYMENT_ID)
    return index


def _metadata(
    *,
    connection: Connection,
    lineage: LiveDocumentLineage,
    family: str,
    authors: tuple[tuple[str, str], ...],
) -> None:
    """Give one seeded version its D134 metadata and authors."""
    connection.execute(
        text(
            "INSERT INTO document_metadata (deployment_id, version_id, doc_id,"
            " family, metadata_mapping_version) VALUES (:d, :v, :doc, :family,"
            " 'test')"
        ),
        {
            "d": _DEPLOYMENT_ID,
            "v": lineage.version_id,
            "doc": lineage.doc_id,
            "family": family,
        },
    )
    for ordinal, (name, address) in enumerate(authors):
        connection.execute(
            text(
                "INSERT INTO document_people (deployment_id, version_id, role,"
                " ordinal, display_name, address, normalized_name,"
                " normalized_address, provenance) VALUES (:d, :v, 'author',"
                " :ordinal, :name, :address, lower(:name), lower(:address),"
                " 'source')"
            ),
            {
                "d": _DEPLOYMENT_ID,
                "v": lineage.version_id,
                "ordinal": ordinal,
                "name": name,
                "address": address,
            },
        )


def _claim(
    *,
    connection: Connection,
    lineage: LiveDocumentLineage,
    text_body: str,
    occurrences: tuple[UUID, ...],
) -> UUID:
    """One current claim whose origin is the lineage's chunk, with occurrences."""
    claim_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
            " claim_text, source_span, char_start, char_end, anchor_ok,"
            " window_membership_ok, is_current_testimony, extractor_version,"
            " ingested_at, asserted_at) VALUES (:claim, :d, :doc, :chunk, :body,"
            " :body, 0, 10, true, true, true, 'd134-test', :at, :at)"
        ),
        {
            "claim": claim_id,
            "d": _DEPLOYMENT_ID,
            "doc": lineage.doc_id,
            "chunk": lineage.chunk_id,
            "body": text_body,
            "at": _NOW,
        },
    )
    for position, chunk_id in enumerate(occurrences):
        connection.execute(
            text(
                "INSERT INTO chunk_claims (deployment_id, chunk_id, claim_id,"
                " evidence_spans, created_at) VALUES (:d, :chunk, :claim,"
                " CAST(:spans AS jsonb), :at)"
            ),
            {
                "d": _DEPLOYMENT_ID,
                "chunk": chunk_id,
                "claim": claim_id,
                "spans": f'[{{"char_start": {position}, "char_end": {position + 5}}}]',
                "at": _NOW,
            },
        )
    return claim_id


def _index_rows(
    *,
    index: PostgresP1Index,
    lineage: LiveDocumentLineage,
    claim_id: UUID,
    body: str,
    vector: tuple[float, ...],
) -> None:
    """Publish the lineage's chunk and its claim into the P1 channels."""
    index.upsert_chunks(
        rows=(
            P1ChunkRow(
                chunk_id=lineage.chunk_id,
                deployment_id=_DEPLOYMENT_ID,
                doc_id=lineage.doc_id,
                version_id=lineage.version_id,
                section_role="body",
                text=body,
                vector=vector,
                policy_generation=EMBEDDING_INPUT_POLICY_VERSION,
                embedder_generation=_MODEL,
                embedding_text_hash=embedding_text_hash(body),
                source_kind="upload",
                source_shape="document",
            ),
        )
    )
    index.upsert_claims(
        rows=(
            P1ClaimRow(
                claim_id=claim_id,
                deployment_id=_DEPLOYMENT_ID,
                doc_id=lineage.doc_id,
                chunk_id=lineage.chunk_id,
                text=body,
                is_current_testimony=True,
                is_attributed=False,
                vector=vector,
            ),
        )
    )


def _document(
    *,
    engine: Engine,
    index: PostgresP1Index,
    label: str,
    family: str,
    author: tuple[str, str],
    body: str,
    vector: tuple[float, ...],
) -> tuple[LiveDocumentLineage, UUID]:
    """One live document with metadata, a chunk and a claim, all published."""
    with engine.begin() as connection:
        lineage = seed_live_document_lineage(
            connection=connection, deployment_id=_DEPLOYMENT_ID, label=label, at=_NOW
        )
        _metadata(
            connection=connection, lineage=lineage, family=family, authors=(author,)
        )
        claim_id = _claim(
            connection=connection,
            lineage=lineage,
            text_body=body,
            occurrences=(lineage.chunk_id,),
        )
    _index_rows(
        index=index, lineage=lineage, claim_id=claim_id, body=body, vector=vector
    )
    return lineage, claim_id


def _ids(nominations: tuple[P1Nomination, ...]) -> list[str]:
    return [item.item_id for item in nominations]


def _engine(*, database_engine: Engine, index: PostgresP1Index) -> QueryEngine:
    """The public query engine over the real index; BM25 needs no embedder."""
    return QueryEngine(
        engine=database_engine,
        search_index=index,
        model_provider=MagicMock(),
        embedding_model=_MODEL,
    )


def test_a_matching_document_below_the_unfiltered_top_k_is_still_returned(
    database_engine: Engine, index: PostgresP1Index
) -> None:
    """The filter is inside the ranked statement, not applied to a finished top-k."""
    bob = ("Bob Stone", "bob@acme.com")
    first, first_claim = _document(
        engine=database_engine,
        index=index,
        label="bob-one",
        family="markdown",
        author=bob,
        body="project x project x budget review",
        vector=_NEAR,
    )
    second, second_claim = _document(
        engine=database_engine,
        index=index,
        label="bob-two",
        family="markdown",
        author=bob,
        body="project x project x launch review",
        vector=_NEAR,
    )
    alice, alice_claim = _document(
        engine=database_engine,
        index=index,
        label="alice",
        family="markdown",
        author=("Alice Novak", "alice@acme.com"),
        body="a long passage that mentions project x once among many other words",
        vector=_FAR,
    )
    deployment = str(_DEPLOYMENT_ID)

    unfiltered_chunks = _ids(
        index.search_chunks_scored(deployment_id=deployment, vector=_NEAR, k=2)
    )
    assert str(alice.chunk_id) not in unfiltered_chunks
    assert _ids(
        index.search_chunks_scored(
            deployment_id=deployment, vector=_NEAR, k=2, documents=_ALICE
        )
    ) == [str(alice.chunk_id)]
    assert str(alice.chunk_id) not in _ids(
        index.search_chunks_lexical_scored(
            deployment_id=deployment, query="project x", k=2
        )
    )
    assert _ids(
        index.search_chunks_lexical_scored(
            deployment_id=deployment, query="project x", k=2, documents=_ALICE
        )
    ) == [str(alice.chunk_id)]

    assert str(alice_claim) not in _ids(
        index.search_claims_scored(
            deployment_id=deployment, vector=_NEAR, k=2, current_only=True
        )
    )
    assert _ids(
        index.search_claims_scored(
            deployment_id=deployment,
            vector=_NEAR,
            k=2,
            current_only=True,
            documents=_ALICE,
        )
    ) == [str(alice_claim)]
    assert _ids(
        index.search_claims_lexical_scored(
            deployment_id=deployment,
            query="project x",
            k=2,
            current_only=True,
            documents=_ALICE,
        )
    ) == [str(alice_claim)]
    # the other documents still match their own author
    bob_only = DocumentSearchFilters(authors=("bob@acme.com",))
    assert set(
        _ids(
            index.search_claims_scored(
                deployment_id=deployment,
                vector=_NEAR,
                k=5,
                current_only=True,
                documents=bob_only,
            )
        )
    ) == {str(first_claim), str(second_claim)}
    assert {first.doc_id, second.doc_id}.isdisjoint({alice.doc_id})


def test_a_claim_reused_across_versions_is_tested_per_occurrence(
    database_engine: Engine, index: PostgresP1Index
) -> None:
    """Each version's metadata is tested on its own occurrence; hydration names it."""
    with database_engine.begin() as connection:
        older = seed_live_document_lineage(
            connection=connection,
            deployment_id=_DEPLOYMENT_ID,
            label="spec-v1",
            at=_NOW,
        )
        # the seeder numbers every version 1; make room for the second
        connection.execute(
            text("UPDATE document_versions SET version_no = 2 WHERE version_id = :v"),
            {"v": older.version_id},
        )
        newer = seed_live_document_lineage(
            connection=connection,
            deployment_id=_DEPLOYMENT_ID,
            doc_id=older.doc_id,
            label="spec-v2",
            at=_NOW,
            create_document=False,
        )
        for version_id, number in ((newer.version_id, 3), (older.version_id, 1)):
            connection.execute(
                text(
                    "UPDATE document_versions SET version_no = :n WHERE version_id = :v"
                ),
                {"n": number, "v": version_id},
            )
        _metadata(
            connection=connection,
            lineage=older,
            family="markdown",
            authors=(("Alice Novak", "alice@acme.com"),),
        )
        _metadata(
            connection=connection,
            lineage=newer,
            family="markdown",
            authors=(("Bob Stone", "bob@acme.com"),),
        )
        claim_id = _claim(
            connection=connection,
            lineage=newer,
            text_body="the zanzibar rollout starts in may",
            occurrences=(older.chunk_id, newer.chunk_id),
        )
    _index_rows(
        index=index,
        lineage=newer,
        claim_id=claim_id,
        body="the zanzibar rollout starts in may",
        vector=_NEAR,
    )
    engine = _engine(database_engine=database_engine, index=index)

    for filters, occurrence in (
        (_ALICE, older.chunk_id),
        (DocumentSearchFilters(authors=("bob@acme.com",)), newer.chunk_id),
    ):
        envelope = engine.search_claims(
            deployment_id=_DEPLOYMENT_ID,
            query="zanzibar rollout",
            k=5,
            channel="bm25",
            documents=filters,
        )
        assert [(item.claim_id, item.chunk_id) for item in envelope.evidence] == [
            (claim_id, occurrence)
        ]
    nobody = engine.search_claims(
        deployment_id=_DEPLOYMENT_ID,
        query="zanzibar rollout",
        k=5,
        channel="bm25",
        documents=DocumentSearchFilters(authors=("carol",)),
    )
    assert nobody.evidence == ()
    # unfiltered hydration still names the origin occurrence
    unfiltered = engine.search_claims(
        deployment_id=_DEPLOYMENT_ID, query="zanzibar rollout", k=5, channel="bm25"
    )
    assert [item.chunk_id for item in unfiltered.evidence] == [newer.chunk_id]


def test_facts_are_kept_by_a_supporting_claim_from_a_matching_document(
    database_engine: Engine, index: PostgresP1Index
) -> None:
    alice, alice_claim = _document(
        engine=database_engine,
        index=index,
        label="alice-fact",
        family="markdown",
        author=("Alice Novak", "alice@acme.com"),
        body="aster coordinates beacon",
        vector=_NEAR,
    )
    bob, bob_claim = _document(
        engine=database_engine,
        index=index,
        label="bob-fact",
        family="markdown",
        author=("Bob Stone", "bob@acme.com"),
        body="aster coordinates cedar",
        vector=_NEAR,
    )
    subject, beacon, cedar = uuid4(), uuid4(), uuid4()
    alice_fact, bob_fact = uuid4(), uuid4()
    with database_engine.begin() as connection:
        for entity_id, name in (
            (subject, "Aster"),
            (beacon, "Beacon"),
            (cedar, "Cedar"),
        ):
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id, canonical_name,"
                    " normalized_name) VALUES (:e, :d, :name, lower(:name))"
                ),
                {"e": entity_id, "d": _DEPLOYMENT_ID, "name": name},
            )
        for fact_id, target, claim_id, doc_id, label in (
            (alice_fact, beacon, alice_claim, alice.doc_id, "Aster coordinates Beacon"),
            (bob_fact, cedar, bob_claim, bob.doc_id, "Aster coordinates Cedar"),
        ):
            connection.execute(
                text(
                    "INSERT INTO relations (relation_id, deployment_id,"
                    " subject_entity_id, predicate, object_entity_id,"
                    " normalizer_version, fact_label, ingested_at) VALUES"
                    " (:fact, :d, :subject, 'works_for', :object, 'd134-test',"
                    " :label, :at)"
                ),
                {
                    "fact": fact_id,
                    "d": _DEPLOYMENT_ID,
                    "subject": subject,
                    "object": target,
                    "label": label,
                    "at": _NOW,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO relation_evidence (deployment_id, relation_id,"
                    " claim_id, doc_id, stance, normalizer_version) VALUES"
                    " (:d, :fact, :claim, :doc, 'supports', 'd134-test')"
                ),
                {
                    "d": _DEPLOYMENT_ID,
                    "fact": fact_id,
                    "claim": claim_id,
                    "doc": doc_id,
                },
            )
        # facts are visible through the resolved mentions of their evidence
        for lineage, claim_id, entities in (
            (alice, alice_claim, (subject, beacon)),
            (bob, bob_claim, (subject, cedar)),
        ):
            for entity_id in entities:
                seed_entity_mention(
                    connection=connection,
                    deployment_id=_DEPLOYMENT_ID,
                    entity_id=entity_id,
                    doc_id=lineage.doc_id,
                    chunk_id=lineage.chunk_id,
                    claim_id=claim_id,
                    surface_form=f"anchor-{entity_id}",
                    at=_NOW,
                    resolver_version="d134-test",
                )
    index.upsert_facts(
        rows=tuple(
            P1FactRow(
                fact_id=fact_id,
                deployment_id=_DEPLOYMENT_ID,
                kind="relation",
                label=label,
                status="active",
                valid_from=None,
                valid_until=None,
                ingested_at=_NOW,
                invalidated_at=None,
                vector=vector,
            )
            for fact_id, label, vector in (
                (alice_fact, "Aster coordinates Beacon", _FAR),
                (bob_fact, "Aster coordinates Cedar", _NEAR),
            )
        )
    )
    deployment = str(_DEPLOYMENT_ID)

    unfiltered = index.search_facts_scored(
        deployment_id=deployment, vector=_NEAR, k=5, kind="relation"
    )
    assert set(_ids(unfiltered)) == {str(alice_fact), str(bob_fact)}
    filtered = index.search_facts_scored(
        deployment_id=deployment, vector=_NEAR, k=5, kind="relation", documents=_ALICE
    )
    assert _ids(filtered) == [str(alice_fact)]
    # Alice's fact ranks below the unfiltered top-1, and is still returned
    assert _ids(
        index.search_facts_scored(
            deployment_id=deployment, vector=_NEAR, k=1, kind="relation"
        )
    ) == [str(bob_fact)]
    assert _ids(
        index.search_facts_scored(
            deployment_id=deployment,
            vector=_NEAR,
            k=1,
            kind="relation",
            documents=_ALICE,
        )
    ) == [str(alice_fact)]


def test_deleted_versions_and_lineages_never_match(
    database_engine: Engine, index: PostgresP1Index
) -> None:
    alice, alice_claim = _document(
        engine=database_engine,
        index=index,
        label="alice-deleted",
        family="markdown",
        author=("Alice Novak", "alice@acme.com"),
        body="the deleted memo about project x",
        vector=_NEAR,
    )
    kept, kept_claim = _document(
        engine=database_engine,
        index=index,
        label="alice-kept",
        family="markdown",
        author=("Alice Novak", "alice@acme.com"),
        body="the kept memo about project x",
        vector=_NEAR,
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_versions SET deleted_at = now() WHERE version_id = :v"
            ),
            {"v": alice.version_id},
        )
    deployment = str(_DEPLOYMENT_ID)
    assert _ids(
        index.search_claims_lexical_scored(
            deployment_id=deployment,
            query="memo project x",
            k=5,
            current_only=True,
            documents=_ALICE,
        )
    ) == [str(kept_claim)]
    assert _ids(
        index.search_chunks_lexical_scored(
            deployment_id=deployment, query="memo project x", k=5, documents=_ALICE
        )
    ) == [str(kept.chunk_id)]

    with database_engine.begin() as connection:
        connection.execute(
            text("UPDATE documents SET deleted_at = now() WHERE doc_id = :d"),
            {"d": kept.doc_id},
        )
    assert (
        index.search_claims_lexical_scored(
            deployment_id=deployment,
            query="memo project x",
            k=5,
            current_only=True,
            documents=_ALICE,
        )
        == ()
    )
    assert alice_claim != kept_claim


def test_project_x_from_emails_from_alice(
    database_engine: Engine, index: PostgresP1Index
) -> None:
    """The design's worked example, through the public engine search."""
    alice = ("Alice Novak", "alice@acme.com")
    email, email_claim = _document(
        engine=database_engine,
        index=index,
        label="alice-email",
        family="other",
        author=alice,
        body="project x ships on friday",
        vector=_NEAR,
    )
    _document(
        engine=database_engine,
        index=index,
        label="bob-email",
        family="other",
        author=("Bob Stone", "bob@acme.com"),
        body="project x slips to monday",
        vector=_NEAR,
    )
    _document(
        engine=database_engine,
        index=index,
        label="alice-notes",
        family="markdown",
        author=alice,
        body="project x notes from the planning meeting",
        vector=_NEAR,
    )
    engine = _engine(database_engine=database_engine, index=index)
    emails_from_alice = DocumentSearchFilters(
        family=("other",), authors=("alice@acme.com",)
    )

    claims = engine.search_claims(
        deployment_id=_DEPLOYMENT_ID,
        query="project x",
        k=10,
        channel="bm25",
        documents=emails_from_alice,
    )
    assert [(item.claim_id, item.doc_id) for item in claims.evidence] == [
        (email_claim, email.doc_id)
    ]
    chunks = engine.search_chunks(
        deployment_id=_DEPLOYMENT_ID,
        query="project x",
        k=10,
        channel="bm25",
        documents=emails_from_alice,
    )
    assert [item.chunk_id for item in chunks.chunks] == [email.chunk_id]
    unfiltered = engine.search_claims(
        deployment_id=_DEPLOYMENT_ID, query="project x", k=10, channel="bm25"
    )
    assert len(unfiltered.evidence) == 3


def test_an_occurrence_in_a_superseded_reading_does_not_match(
    database_engine: Engine, index: PostgresP1Index
) -> None:
    """Only occurrences in the version's current representation are live."""
    with database_engine.begin() as connection:
        lineage = seed_live_document_lineage(
            connection=connection,
            deployment_id=_DEPLOYMENT_ID,
            label="reconverted",
            at=_NOW,
        )
        _metadata(
            connection=connection,
            lineage=lineage,
            family="markdown",
            authors=(("Alice Novak", "alice@acme.com"),),
        )
        # a newer reading of the same version becomes current
        representation_id, current_chunk = uuid4(), uuid4()
        connection.execute(
            text(
                "INSERT INTO document_representations (representation_id,"
                " deployment_id, version_id, route, markdown_uri, status) VALUES"
                " (:r, :d, :v, 'digital', 'mem://artifacts/reconverted-2.md',"
                " 'ready')"
            ),
            {"r": representation_id, "d": _DEPLOYMENT_ID, "v": lineage.version_id},
        )
        connection.execute(
            text(
                "INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,"
                " representation_id, ordinal, block_start, block_end,"
                " chunk_content_hash, extraction_input_hash, char_start, char_end,"
                " context_prefix, created_at) VALUES (:c, :d, :doc, :v, :r, 0, 0,"
                " 0, 'reconverted-2', 'reconverted-2', 0, 10, '', :at)"
            ),
            {
                "c": current_chunk,
                "d": _DEPLOYMENT_ID,
                "doc": lineage.doc_id,
                "v": lineage.version_id,
                "r": representation_id,
                "at": _NOW,
            },
        )
        connection.execute(
            text(
                "UPDATE document_versions SET current_representation_id = :r"
                " WHERE version_id = :v"
            ),
            {"r": representation_id, "v": lineage.version_id},
        )
        current = LiveDocumentLineage(
            doc_id=lineage.doc_id,
            version_id=lineage.version_id,
            representation_id=representation_id,
            section_id=lineage.section_id,
            generation_id=lineage.generation_id,
            chunk_ids=(current_chunk,),
        )
        # the claim's only occurrence is in the superseded reading's chunk
        claim_id = _claim(
            connection=connection,
            lineage=current,
            text_body="the orchid migration finished",
            occurrences=(lineage.chunk_id,),
        )
    _index_rows(
        index=index,
        lineage=current,
        claim_id=claim_id,
        body="the orchid migration finished",
        vector=_NEAR,
    )
    deployment = str(_DEPLOYMENT_ID)

    def filtered() -> list[str]:
        return _ids(
            index.search_claims_lexical_scored(
                deployment_id=deployment,
                query="orchid migration",
                k=5,
                current_only=True,
                documents=_ALICE,
            )
        )

    assert _ids(
        index.search_claims_lexical_scored(
            deployment_id=deployment, query="orchid migration", k=5, current_only=True
        )
    ) == [str(claim_id)]
    assert filtered() == []

    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO chunk_claims (deployment_id, chunk_id, claim_id,"
                " evidence_spans, created_at) VALUES (:d, :c, :claim,"
                ' CAST(\'[{"char_start": 0, "char_end": 5}]\' AS jsonb), :at)'
            ),
            {"d": _DEPLOYMENT_ID, "c": current_chunk, "claim": claim_id, "at": _NOW},
        )
    assert filtered() == [str(claim_id)]


class _MetadataChangingIndex(PostgresP1Index):
    """Nominate, then change the nominated document's author before hydration."""

    def __init__(self, *, engine: Engine, version_id: UUID) -> None:
        super().__init__(engine=engine, embedding_model=_MODEL)
        self._database = engine
        self._version_id = version_id

    def search_chunks_lexical(self, **kwargs: object) -> tuple[str, ...]:
        nominated = super().search_chunks_lexical(**kwargs)  # type: ignore[arg-type]
        with self._database.begin() as connection:
            connection.execute(
                text(
                    "UPDATE document_people SET display_name = 'Bob Stone',"
                    " normalized_name = 'bob stone', address = 'bob@acme.com',"
                    " normalized_address = 'bob@acme.com' WHERE version_id = :v"
                ),
                {"v": self._version_id},
            )
        return nominated


def test_metadata_changed_after_nomination_drops_the_chunk_at_hydration(
    database_engine: Engine, index: PostgresP1Index
) -> None:
    """Hydration re-checks the document filter; it does not trust nomination."""
    alice, _claim_id = _document(
        engine=database_engine,
        index=index,
        label="alice-changing",
        family="markdown",
        author=("Alice Novak", "alice@acme.com"),
        body="the heron budget memo",
        vector=_NEAR,
    )
    engine = QueryEngine(
        engine=database_engine,
        search_index=_MetadataChangingIndex(
            engine=database_engine, version_id=alice.version_id
        ),
        model_provider=MagicMock(),
        embedding_model=_MODEL,
    )

    envelope = engine.search_chunks(
        deployment_id=_DEPLOYMENT_ID,
        query="heron budget",
        k=5,
        channel="bm25",
        documents=_ALICE,
    )

    assert envelope.chunks == ()
    assert envelope.dropped_by_hydration == 1
