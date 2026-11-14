"""D156 §4 filtering ``search`` and ``search_documents`` by caller tags, on PostgreSQL.

Lineages are complete live chains (``lineage_seed``) with D134 metadata,
published into the real P1 channels and searched through the real adapter,
query engine and document search. Tags are registered and assigned through the
real ``TagCatalog``; effective periods through the real period API.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import UTC
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from remember.models import AtReadTime
from remember.models import DocumentSearchFilters
from remember.models import DocumentSearchRequest
from remember.models import DocumentTagFilter
from remember.models import DocumentTagsUpdate
from remember.models import EffectivePeriodInput
from remember.models import TAG_FILTER_LIST_MAX
from remember.models import TagCreate
from rememberstack.adapters import BoundedPostgresReadPool
from rememberstack.adapters import p1_ranked
from rememberstack.adapters import PostgresP1Index
from rememberstack.core.embedding_input_policy import EMBEDDING_INPUT_POLICY_VERSION
from rememberstack.core.embedding_input_policy import embedding_text_hash
from rememberstack.model import ClaimValidPrecision
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import NegativeKind
from rememberstack.model import P1ChunkRow
from rememberstack.model import P1ClaimRow
from rememberstack.model import P1FactRow
from rememberstack.model import UnknownTagError
from rememberstack.model.assured_operations import AtFactTime
from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS
from rememberstack.ports.p1_index import P1Nomination
from rememberstack.ports.p1_index import P1Termination
from rememberstack.ports.p1_index import search_account
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.document_search import DocumentSearch
from rememberstack.spine.effective_time import EffectiveTimeCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.spine.tags import TagCatalog
from rememberstack.surfaces import QueryEngine
from tests.database_reset import reset_database
from tests.surfaces.lineage_seed import LiveDocumentLineage
from tests.surfaces.lineage_seed import seed_entity_mention
from tests.surfaces.lineage_seed import seed_live_document_lineage

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("62000000-0000-0000-0000-0000000d0156")
_MODEL = "qwen/qwen3-embedding-8b"
_NOW = datetime.now(UTC)
_PAST = datetime(2024, 1, 1, tzinfo=UTC)
_FUTURE = _NOW + timedelta(days=30)
_LATER = _NOW + timedelta(days=60)


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
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for D156 filter proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@dataclass(frozen=True, slots=True)
class _Rig:
    engine: Engine
    index: PostgresP1Index
    query: QueryEngine
    periods: EffectiveTimeCatalog
    documents: DocumentSearch
    tags: TagCatalog
    tag_ids: dict[str, UUID]


@pytest.fixture()
def rig(database_engine: Engine) -> _Rig:
    """A fresh deployment, every P1 channel configured, three registered tags."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments, tags CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="d156-filters",
            name="D156 filters",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    read_pool = BoundedPostgresReadPool(
        engine=database_engine, max_concurrency=4, pool_wait_seconds=5.0
    )
    index = PostgresP1Index(
        engine=database_engine, embedding_model=_MODEL, read_pool=read_pool
    )
    index.configure_channels(deployment_id=_DEPLOYMENT_ID)
    provider = MagicMock()
    provider.embed.return_value = SimpleNamespace(vectors=(_NEAR,), usage=None)
    tags = TagCatalog(engine=database_engine)
    tag_ids = {
        name: tags.create_tag(
            deployment_id=_DEPLOYMENT_ID,
            request=TagCreate(name=name, description=f"{name} documents"),
        ).tag_id
        for name in ("project:a", "project:b", "kind:decision", "project:empty")
    }
    return _Rig(
        engine=database_engine,
        index=index,
        query=QueryEngine(
            engine=database_engine,
            search_index=index,
            model_provider=provider,
            embedding_model=_MODEL,
            fact_read_pool=read_pool,
        ),
        periods=EffectiveTimeCatalog(engine=database_engine),
        documents=DocumentSearch(engine=database_engine),
        tags=tags,
        tag_ids=tag_ids,
    )


@dataclass(frozen=True, slots=True)
class _Lineage:
    doc_id: UUID
    editions: tuple[LiveDocumentLineage, ...]

    def chunk(self, edition: int = 0) -> UUID:
        return self.editions[edition].chunk_ids[0]

    def version(self, edition: int = 0) -> UUID:
        return self.editions[edition].version_id


def _lineage(
    rig: _Rig,
    *,
    label: str,
    bodies: tuple[str, ...],
    families: tuple[str, ...] | None = None,
    tags: tuple[str, ...] = (),
    vector: tuple[float, ...] = _FAR,
) -> _Lineage:
    """One live lineage with one single-chunk version per body, published and tagged.

    The last version is the served one. ``families`` gives each version its
    own D134 family (default markdown).
    """
    doc_id = uuid4()
    editions: list[LiveDocumentLineage] = []
    with rig.engine.begin() as connection:
        for number in range(1, len(bodies) + 1):
            edition = seed_live_document_lineage(
                connection=connection,
                deployment_id=_DEPLOYMENT_ID,
                doc_id=doc_id,
                label=f"{label}-{number}",
                title=f"{label} notes",
                source_ref=label,
                create_document=number == 1,
            )
            connection.execute(
                text(
                    "UPDATE document_versions SET version_no = :n WHERE version_id = :v"
                ),
                {"n": 1000 + number, "v": edition.version_id},
            )
            connection.execute(
                text(
                    "INSERT INTO document_metadata (deployment_id, version_id, doc_id,"
                    " family, metadata_mapping_version) VALUES (:d, :v, :doc,"
                    " :family, 'test')"
                ),
                {
                    "d": _DEPLOYMENT_ID,
                    "v": edition.version_id,
                    "doc": doc_id,
                    "family": (families or ("markdown",) * len(bodies))[number - 1],
                },
            )
            editions.append(edition)
        for number, edition in enumerate(editions, start=1):
            connection.execute(
                text(
                    "UPDATE document_versions SET version_no = :n WHERE version_id = :v"
                ),
                {"n": number, "v": edition.version_id},
            )
    rig.index.upsert_chunks(
        rows=tuple(
            P1ChunkRow(
                chunk_id=edition.chunk_ids[0],
                deployment_id=_DEPLOYMENT_ID,
                doc_id=doc_id,
                version_id=edition.version_id,
                section_role="body",
                text=body,
                vector=vector,
                policy_generation=EMBEDDING_INPUT_POLICY_VERSION,
                embedder_generation=_MODEL,
                embedding_text_hash=embedding_text_hash(body),
                source_kind="upload",
                source_shape="document",
            )
            for edition, body in zip(editions, bodies, strict=True)
        )
    )
    if tags:
        rig.tags.update_document_tags(
            deployment_id=_DEPLOYMENT_ID,
            doc_id=doc_id,
            request=DocumentTagsUpdate(add=tags),
        )
    return _Lineage(doc_id=doc_id, editions=tuple(editions))


def _declare(
    rig: _Rig, lineage: _Lineage, edition: int, start: datetime, end: datetime | None
) -> None:
    rig.periods.set_effective_periods(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=lineage.doc_id,
        version_id=lineage.version(edition),
        periods=(EffectivePeriodInput(effective_from=start, effective_until=end),),
    )


def _claim(
    rig: _Rig,
    *,
    lineage: _Lineage,
    body: str,
    editions: tuple[int, ...] = (0,),
    vector: tuple[float, ...] = _FAR,
) -> UUID:
    """One current claim, its origin in the first listed edition, an occurrence in each."""
    claim_id = uuid4()
    origin = lineage.chunk(editions[0])
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, is_current_testimony, extractor_version,"
                " ingested_at, asserted_at) VALUES (:claim, :d, :doc, :chunk, :body,"
                " :body, 0, :end, true, true, true, 'd156-test', :at, :at)"
            ),
            {
                "claim": claim_id,
                "d": _DEPLOYMENT_ID,
                "doc": lineage.doc_id,
                "chunk": origin,
                "body": body,
                "end": len(body),
                "at": _PAST,
            },
        )
        for edition in editions:
            connection.execute(
                text(
                    "INSERT INTO chunk_claims (deployment_id, chunk_id, claim_id,"
                    " evidence_spans, created_at) VALUES (:d, :chunk, :claim,"
                    " CAST(:spans AS jsonb), :at)"
                ),
                {
                    "d": _DEPLOYMENT_ID,
                    "chunk": lineage.chunk(edition),
                    "claim": claim_id,
                    "spans": json.dumps([{"char_start": 0, "char_end": len(body)}]),
                    "at": _NOW,
                },
            )
    rig.index.upsert_claims(
        rows=(
            P1ClaimRow(
                claim_id=claim_id,
                deployment_id=_DEPLOYMENT_ID,
                doc_id=lineage.doc_id,
                chunk_id=origin,
                text=body,
                is_current_testimony=True,
                is_attributed=False,
                vector=vector,
            ),
        )
    )
    return claim_id


def _relation(
    rig: _Rig,
    *,
    label: str,
    support: tuple[tuple[UUID, _Lineage, int], ...],
    entity: UUID | None = None,
    vector: tuple[float, ...] = _NEAR,
) -> UUID:
    """One open relation supported by (claim, lineage, edition) triples."""
    subject, obj, fact_id = entity or uuid4(), uuid4(), uuid4()
    with rig.engine.begin() as connection:
        for entity_id, name in ((subject, f"{label} subject"), (obj, f"{label} obj")):
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id, canonical_name,"
                    " normalized_name) VALUES (:e, :d, :name, lower(:name))"
                    " ON CONFLICT DO NOTHING"
                ),
                {"e": entity_id, "d": _DEPLOYMENT_ID, "name": name},
            )
        connection.execute(
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id,"
                " normalizer_version, fact_label, ingested_at, valid_from,"
                " valid_precision, window_claim_ids) VALUES (:fact, :d,"
                " :subject, 'works_for', :object, 'd156-test', :label, :at, :at,"
                " 'open', :witnesses)"
            ),
            {
                "fact": fact_id,
                "d": _DEPLOYMENT_ID,
                "subject": subject,
                "object": obj,
                "label": label,
                "at": _PAST,
                "witnesses": [claim for claim, _, _ in support],
            },
        )
        for claim_id, lineage, edition in support:
            connection.execute(
                text(
                    "INSERT INTO relation_evidence (deployment_id, relation_id,"
                    " claim_id, doc_id, stance, normalizer_version) VALUES"
                    " (:d, :fact, :claim, :doc, 'supports', 'd156-test')"
                ),
                {
                    "d": _DEPLOYMENT_ID,
                    "fact": fact_id,
                    "claim": claim_id,
                    "doc": lineage.doc_id,
                },
            )
            for entity_id in (subject, obj):
                seed_entity_mention(
                    connection=connection,
                    deployment_id=_DEPLOYMENT_ID,
                    entity_id=entity_id,
                    doc_id=lineage.doc_id,
                    chunk_id=lineage.chunk(edition),
                    claim_id=claim_id,
                    surface_form=f"anchor-{entity_id}",
                    at=_PAST,
                    resolver_version="d156-test",
                )
    rig.index.upsert_facts(
        rows=(
            P1FactRow(
                fact_id=fact_id,
                deployment_id=_DEPLOYMENT_ID,
                kind="relation",
                label=label,
                status="active",
                valid_from=_PAST,
                valid_until=None,
                valid_precision=ClaimValidPrecision.OPEN,
                ingested_at=_PAST,
                invalidated_at=None,
                vector=vector,
            ),
        )
    )
    return fact_id


def _ids(nominations: tuple[P1Nomination, ...]) -> list[str]:
    return [item.item_id for item in nominations]


def _tags(**lists: tuple[str, ...]) -> DocumentSearchFilters:
    return DocumentSearchFilters(tags=DocumentTagFilter(**lists))


@dataclass(frozen=True, slots=True)
class _Corpus:
    a: _Lineage
    b: _Lineage
    untagged: _Lineage
    claims: dict[str, UUID]


def _corpus(rig: _Rig) -> _Corpus:
    """A: project:a + kind:decision (markdown); B: project:b (email); one untagged."""
    a = _lineage(
        rig,
        label="alpha",
        bodies=("rollout plan for alpha",),
        tags=("project:a", "kind:decision"),
    )
    b = _lineage(
        rig,
        label="beta",
        bodies=("rollout plan for beta",),
        families=("email",),
        tags=("project:b",),
    )
    untagged = _lineage(rig, label="gamma", bodies=("rollout plan for gamma",))
    claims = {
        "a": _claim(rig, lineage=a, body="the rollout plan for alpha"),
        "b": _claim(rig, lineage=b, body="the rollout plan for beta"),
        "untagged": _claim(rig, lineage=untagged, body="the rollout plan for gamma"),
    }
    return _Corpus(a=a, b=b, untagged=untagged, claims=claims)


def _every_grain(
    rig: _Rig, corpus: _Corpus, filters: DocumentSearchFilters
) -> tuple[set[UUID], set[UUID], set[UUID]]:
    """The lineages each surface returns for one filter: chunks, claims, documents."""
    chunks = rig.query.search_chunks(
        deployment_id=_DEPLOYMENT_ID,
        query="rollout plan",
        k=10,
        channel="bm25",
        documents=filters,
    )
    claims = rig.query.search_claims(
        deployment_id=_DEPLOYMENT_ID,
        query="rollout plan",
        k=10,
        channel="bm25",
        documents=filters,
    )
    owners = {claim_id: key for key, claim_id in corpus.claims.items()}
    lineage = {"a": corpus.a, "b": corpus.b, "untagged": corpus.untagged}
    documents = rig.documents.search_documents(
        deployment_id=_DEPLOYMENT_ID, request=DocumentSearchRequest(filters=filters)
    )
    return (
        {chunk.doc_id for chunk in chunks.chunks},
        {lineage[owners[claim.claim_id]].doc_id for claim in claims.evidence},
        {document.doc_id for document in documents.documents},
    )


def test_every_tag_list_by_name_and_by_id_selects_the_same_lineages_on_every_grain(
    rig: _Rig,
) -> None:
    """§4.1: all/any/none, by name (any case) or tag_id, AND with D134 fields."""
    corpus = _corpus(rig)
    a, b, untagged = corpus.a.doc_id, corpus.b.doc_id, corpus.untagged.doc_id
    ids = {name: str(tag_id) for name, tag_id in rig.tag_ids.items()}
    cases: list[tuple[DocumentSearchFilters, set[UUID]]] = [
        (_tags(all=("project:a",)), {a}),
        (_tags(all=(ids["project:a"],)), {a}),
        (_tags(all=("PROJECT:A",)), {a}),
        (_tags(all=("project:a", "kind:decision")), {a}),
        (_tags(all=("project:b", ids["kind:decision"])), set()),
        (_tags(any=("project:a", "project:b")), {a, b}),
        (_tags(any=(ids["project:a"], ids["project:b"])), {a, b}),
        (_tags(none=("project:a",)), {b, untagged}),
        (_tags(none=(ids["project:a"], "project:b")), {untagged}),
        (_tags(all=("kind:decision",), any=("project:a", "project:b")), {a}),
        (_tags(any=("project:a", "project:b"), none=("kind:decision",)), {b}),
        (
            DocumentSearchFilters(
                family=("email",),
                tags=DocumentTagFilter(any=("project:a", "project:b")),
            ),
            {b},
        ),
        (
            DocumentSearchFilters(
                doc_ids=(a, untagged), tags=DocumentTagFilter(none=("project:b",))
            ),
            {a, untagged},
        ),
    ]
    for filters, expected in cases:
        chunks, claims, documents = _every_grain(rig, corpus, filters)
        assert (chunks, claims, documents) == (expected, expected, expected), filters


def test_search_documents_results_carry_the_lineage_tags(rig: _Rig) -> None:
    corpus = _corpus(rig)
    page = rig.documents.search_documents(
        deployment_id=_DEPLOYMENT_ID,
        request=DocumentSearchRequest(
            query="rollout", filters=_tags(any=("project:a", "project:b"))
        ),
    )
    assert {document.doc_id: document.tags for document in page.documents} == {
        corpus.a.doc_id: ("kind:decision", "project:a"),
        corpus.b.doc_id: ("project:b",),
    }


def test_an_unregistered_tag_fails_every_read_before_retrieval(rig: _Rig) -> None:
    _corpus(rig)
    filters = _tags(all=("project:a", "typo"), none=("oops",))
    for read in (
        lambda: rig.query.search_chunks(
            deployment_id=_DEPLOYMENT_ID,
            query="rollout",
            k=5,
            channel="bm25",
            documents=filters,
        ),
        lambda: rig.query.search_claims(
            deployment_id=_DEPLOYMENT_ID,
            query="rollout",
            k=5,
            channel="bm25",
            documents=filters,
        ),
        lambda: rig.documents.search_documents(
            deployment_id=_DEPLOYMENT_ID, request=DocumentSearchRequest(filters=filters)
        ),
    ):
        with pytest.raises(UnknownTagError) as refused:
            read()
        assert refused.value.tags == ("typo", "oops")


def test_the_tag_selector_refuses_empty_repeated_and_overlong_lists() -> None:
    with pytest.raises(ValidationError, match="non-empty"):
        DocumentTagFilter()
    with pytest.raises(ValidationError, match="non-empty"):
        DocumentTagFilter(all=(), any=(), none=())
    with pytest.raises(ValidationError, match="more than once"):
        DocumentTagFilter(any=("project:a", "Project:A"))
    with pytest.raises(ValidationError):
        DocumentTagFilter(all=tuple(f"t{n}" for n in range(TAG_FILTER_LIST_MAX + 1)))
    assert DocumentTagFilter(all=("x",), none=("x",)).none == ("x",)


def test_a_tagged_item_ranked_below_the_unfiltered_top_k_is_still_returned(
    rig: _Rig,
) -> None:
    """§4.6: eligibility before ranking on chunks, claims and facts."""
    loud = _lineage(
        rig, label="loud", bodies=("zebra zebra zebra crossing",), tags=("project:b",)
    )
    quiet = _lineage(
        rig,
        label="quiet",
        bodies=("a long text that names a zebra once among many other words",),
        tags=("project:a",),
    )
    loud_claim = _claim(rig, lineage=loud, body="zebra zebra zebra crossing")
    quiet_claim = _claim(
        rig,
        lineage=quiet,
        body="a long text that names a zebra once among many other words",
    )
    only_a = _tags(all=("project:a",))

    def chunks(**arguments: object) -> list[UUID]:
        return [
            item.chunk_id
            for item in rig.query.search_chunks(
                deployment_id=_DEPLOYMENT_ID,
                query="zebra",
                k=1,
                channel="bm25",
                **arguments,  # type: ignore[arg-type]
            ).chunks
        ]

    def claims(**arguments: object) -> list[UUID]:
        return [
            item.claim_id
            for item in rig.query.search_claims(
                deployment_id=_DEPLOYMENT_ID,
                query="zebra",
                k=1,
                channel="bm25",
                **arguments,  # type: ignore[arg-type]
            ).evidence
        ]

    assert chunks() == [loud.chunk()]
    assert chunks(documents=only_a) == [quiet.chunk()]
    assert claims() == [loud_claim]
    assert claims(documents=only_a) == [quiet_claim]

    loud_fact = _relation(
        rig, label="zebra loud", support=((loud_claim, loud, 0),), vector=_NEAR
    )
    quiet_fact = _relation(
        rig, label="zebra quiet", support=((quiet_claim, quiet, 0),), vector=_FAR
    )
    deployment = str(_DEPLOYMENT_ID)
    assert _ids(
        rig.index.search_facts_scored(
            deployment_id=deployment, vector=_NEAR, k=1, kind="relation"
        )
    ) == [str(loud_fact)]
    assert _ids(
        rig.index.search_facts_scored(
            deployment_id=deployment,
            vector=_NEAR,
            k=1,
            kind="relation",
            documents=rig.query._resolve_documents(  # noqa: SLF001
                deployment_id=_DEPLOYMENT_ID, documents=only_a
            ),
        )
    ) == [str(quiet_fact)]


def test_tags_combine_with_entity_ids_at_nomination(rig: _Rig) -> None:
    """An item must be associated with the entity and come from a matching document."""
    corpus = _corpus(rig)
    entity = uuid4()
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id, canonical_name,"
                " normalized_name) VALUES (:e, :d, 'Rollout', 'rollout')"
            ),
            {"e": entity, "d": _DEPLOYMENT_ID},
        )
        for key, lineage in (("a", corpus.a), ("b", corpus.b)):
            seed_entity_mention(
                connection=connection,
                deployment_id=_DEPLOYMENT_ID,
                entity_id=entity,
                doc_id=lineage.doc_id,
                chunk_id=lineage.chunk(),
                claim_id=corpus.claims[key],
                surface_form="rollout",
                at=_PAST,
                resolver_version="d156-test",
            )
    resolved = rig.query._resolve_documents(  # noqa: SLF001
        deployment_id=_DEPLOYMENT_ID, documents=_tags(none=("project:a",))
    )
    deployment = str(_DEPLOYMENT_ID)
    unfiltered = rig.index.search_claims_lexical_scored(
        deployment_id=deployment,
        query="rollout plan",
        k=10,
        current_only=True,
        entity_ids=(str(entity),),
    )
    assert set(_ids(unfiltered)) == {str(corpus.claims["a"]), str(corpus.claims["b"])}
    filtered = rig.index.search_claims_lexical_scored(
        deployment_id=deployment,
        query="rollout plan",
        k=10,
        current_only=True,
        entity_ids=(str(entity),),
        documents=resolved,
    )
    assert _ids(filtered) == [str(corpus.claims["b"])]
    chunks = rig.index.search_chunks_lexical_scored(
        deployment_id=deployment,
        query="rollout plan",
        k=10,
        entity_ids=(str(entity),),
        documents=resolved,
    )
    assert _ids(chunks) == [str(corpus.b.chunk())]


def test_a_time_scoped_read_selects_old_versions_by_the_lineage_current_tags(
    rig: _Rig,
) -> None:
    """D140 + §3.2: an edition in force in the past is found by today's tags."""
    lineage = _lineage(
        rig, label="policy", bodies=("travel policy one", "travel policy two")
    )
    _declare(rig, lineage, 0, _PAST, _NOW - timedelta(days=1))
    _declare(rig, lineage, 1, _NOW - timedelta(days=1), None)
    only_a = _tags(all=("project:a",))
    past = AtReadTime(at=_PAST + timedelta(days=1))

    def chunks(time: AtReadTime | None) -> list[UUID]:
        return [
            item.chunk_id
            for item in rig.query.search_chunks(
                deployment_id=_DEPLOYMENT_ID,
                query="travel policy",
                k=5,
                channel="bm25",
                documents=only_a,
                time=time,
            ).chunks
        ]

    assert chunks(past) == []
    rig.tags.update_document_tags(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=lineage.doc_id,
        request=DocumentTagsUpdate(add=("project:a",)),
    )
    assert chunks(past) == [lineage.chunk(0)]
    assert chunks(None) == [lineage.chunk(1)]


def test_a_claim_qualifies_only_through_one_occurrence_in_force_and_matching(
    rig: _Rig,
) -> None:
    """§4.3 with D140: the filter and the scope hold on the same occurrence.

    The claim occurs in edition 1 (email, in force now) and edition 2
    (markdown, in force next month). Each occurrence passes one test, so a
    current read filtered to markdown must not return it; this is the
    D134 version-level case, fixed for the whole ``documents`` filter.
    """
    lineage = _lineage(
        rig,
        label="handbook",
        bodies=("the leave allowance is ten days", "the leave allowance is ten days"),
        families=("email", "markdown"),
        tags=("project:a",),
    )
    _declare(rig, lineage, 0, _PAST, None)
    _declare(rig, lineage, 1, _FUTURE, None)
    claim_id = _claim(
        rig, lineage=lineage, body="the leave allowance is ten days", editions=(0, 1)
    )

    def claims(
        filters: DocumentSearchFilters, time: AtReadTime | None
    ) -> list[tuple[UUID, tuple[UUID, ...]]]:
        envelope = rig.query.search_claims(
            deployment_id=_DEPLOYMENT_ID,
            query="leave allowance",
            k=5,
            channel="bm25",
            documents=filters,
            time=time,
        )
        return [
            (item.claim_id, tuple(o.version_id for o in item.occurrences))
            for item in envelope.evidence
        ]

    markdown = DocumentSearchFilters(
        family=("markdown",), tags=DocumentTagFilter(all=("project:a",))
    )
    email = DocumentSearchFilters(family=("email",))
    later = AtReadTime(at=_LATER)
    assert claims(markdown, None) == []
    assert claims(email, None) == [(claim_id, (lineage.version(0),))]
    assert claims(markdown, later) == [(claim_id, (lineage.version(1),))]
    assert claims(email, later) == []
    deployment = str(_DEPLOYMENT_ID)
    resolved = rig.query._resolve_documents(  # noqa: SLF001
        deployment_id=_DEPLOYMENT_ID, documents=markdown
    )
    assert (
        rig.index.search_claims_lexical_scored(
            deployment_id=deployment,
            query="leave allowance",
            k=5,
            current_only=True,
            documents=resolved,
        )
        == ()
    )


def test_tagged_support_out_of_force_plus_untagged_support_in_force_admits_no_fact(
    rig: _Rig,
) -> None:
    """§4.3 / §8: the design's same-occurrence case for facts."""
    statute = _lineage(
        rig, label="statute", bodies=("the old fee rule",), tags=("project:a",)
    )
    _declare(rig, statute, 0, _PAST, _NOW - timedelta(days=1))
    memo = _lineage(rig, label="memo", bodies=("memo on the fee rule",))
    tagged = _claim(rig, lineage=statute, body="the fee is five")
    untagged = _claim(rig, lineage=memo, body="the fee is five")
    fact = _relation(
        rig, label="fee five", support=((tagged, statute, 0), (untagged, memo, 0))
    )
    only_a = rig.query._resolve_documents(  # noqa: SLF001
        deployment_id=_DEPLOYMENT_ID, documents=_tags(all=("project:a",))
    )
    deployment = str(_DEPLOYMENT_ID)

    def facts(**arguments: object) -> list[str]:
        return _ids(
            rig.index.search_facts_scored(
                deployment_id=deployment,
                vector=_NEAR,
                k=5,
                kind="relation",
                **arguments,  # type: ignore[arg-type]
            )
        )

    assert facts() == [str(fact)]
    assert facts(documents=only_a) == []
    in_force = AtFactTime(at=_PAST + timedelta(days=1))
    assert facts(documents=only_a, time=in_force) == [str(fact)]
    # the same rule through a supplied-entity scope
    with rig.engine.begin() as connection:
        subject = connection.execute(
            text("SELECT subject_entity_id FROM relations WHERE relation_id = :r"),
            {"r": fact},
        ).scalar_one()
    assert facts(entity_ids=(str(subject),), documents=only_a) == []
    assert facts(entity_ids=(str(subject),)) == [str(fact)]


def test_a_completed_empty_filtered_read_says_which_empty_case_occurred(
    rig: _Rig,
) -> None:
    """§4.5: no live document matched, or the matching ones say nothing relevant."""
    corpus = _corpus(rig)

    def negative(filters: DocumentSearchFilters, query: str) -> tuple[str, str]:
        envelopes = (
            rig.query.search_chunks(
                deployment_id=_DEPLOYMENT_ID,
                query=query,
                k=5,
                channel="bm25",
                documents=filters,
            ),
            rig.query.search_claims(
                deployment_id=_DEPLOYMENT_ID,
                query=query,
                k=5,
                channel="bm25",
                documents=filters,
            ),
        )
        explanations = set()
        for envelope in envelopes:
            assert envelope.negative is not None
            assert envelope.negative.kind is NegativeKind.KNOWN_EMPTY
            explanations.add(envelope.negative.explanation.split(": ", 1)[1])
        assert len(explanations) == 1
        return explanations.pop(), query

    no_document = "no live document matches the documents filter"
    nothing_relevant = (
        "live documents match the documents filter,"
        " but none of them says anything relevant"
    )
    assert negative(_tags(all=("project:empty",)), "rollout")[0] == no_document
    assert negative(_tags(all=("project:a",)), "submarine")[0] == nothing_relevant
    # a deleted lineage keeps its assignments but never matches
    with rig.engine.begin() as connection:
        connection.execute(
            text("UPDATE documents SET deleted_at = now() WHERE doc_id = :d"),
            {"d": corpus.a.doc_id},
        )
    assert negative(_tags(all=("project:a",)), "rollout")[0] == no_document
    # an unfiltered empty read keeps its ordinary explanation
    plain = rig.query.search_chunks(
        deployment_id=_DEPLOYMENT_ID, query="submarine", k=5, channel="bm25"
    )
    assert plain.negative is not None
    assert plain.negative.explanation == "no live source chunks match the query"


def test_a_tag_filtered_read_stopped_by_its_work_bound_reports_boundary(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§4.5: an incomplete filtered read is a boundary, never known_empty."""
    loud = _lineage(
        rig, label="loud", bodies=("zebra zebra zebra crossing",), tags=("project:b",)
    )
    quiet = _lineage(
        rig,
        label="quiet",
        bodies=("a long text that names a zebra once among many other words",),
        tags=("project:a",),
    )
    _claim(rig, lineage=loud, body="zebra zebra zebra crossing")
    _claim(
        rig,
        lineage=quiet,
        body="a long text that names a zebra once among many other words",
    )
    # one candidate of work: the first-ranked claim, which is not project:a;
    # the eligible-set tiers are off, as for a filter too broad to enumerate
    monkeypatch.setattr(p1_ranked, "MIN_WORK_BUDGET", 1)
    monkeypatch.setattr(p1_ranked, "WORK_BUDGET_PER_K", 0)
    monkeypatch.setattr(p1_ranked, "EXACT_TIER_THRESHOLD", 0)
    monkeypatch.setattr(p1_ranked, "FILTER_PUSHDOWN_LIMIT", 0)
    envelope = rig.query.search_claims(
        deployment_id=_DEPLOYMENT_ID,
        query="zebra",
        k=1,
        channel="bm25",
        documents=_tags(all=("project:a",)),
    )
    assert envelope.evidence == ()
    assert envelope.negative is not None
    assert envelope.negative.kind is NegativeKind.BOUNDARY
    assert envelope.truncation is not None
    assert envelope.truncation.reason == "p1_work_budget"


@pytest.mark.parametrize("tiers", ["enumerated", "disabled"])
def test_a_narrow_tag_filter_finds_items_ranked_beyond_the_work_budget(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch, tiers: str
) -> None:
    """§4.6: a selective filter's small eligible set is scored exactly.

    Twelve untagged documents outrank three tagged ones on every grain, and the
    work budget is four candidates. Plain nomination stops on the budget
    before reaching a tagged item; the enumerated eligible set finds all
    three and is complete. With the eligible-set tiers switched off the same
    read is a ``boundary``, so the proof depends on the tier.
    """
    loud = [
        _lineage(
            rig,
            label=f"loud-{n}",
            bodies=("zebra zebra zebra crossing",),
            tags=("project:b",),
        )
        for n in range(12)
    ]
    quiet = [
        _lineage(
            rig,
            label=f"quiet-{n}",
            bodies=("a long text that names a zebra once among many other words",),
            tags=("project:a",),
        )
        for n in range(3)
    ]
    loud_claims = [
        _claim(rig, lineage=lineage, body="zebra zebra zebra crossing")
        for lineage in loud
    ]
    quiet_claims = [
        _claim(
            rig,
            lineage=lineage,
            body="a long text that names a zebra once among many other words",
        )
        for lineage in quiet
    ]
    for n, (claim_id, lineage) in enumerate(zip(loud_claims, loud, strict=True)):
        _relation(
            rig,
            label=f"zebra loud {n}",
            support=((claim_id, lineage, 0),),
            vector=_NEAR,
        )
    quiet_facts = {
        _relation(
            rig,
            label=f"zebra quiet {n}",
            support=((claim_id, lineage, 0),),
            vector=_FAR,
        )
        for n, (claim_id, lineage) in enumerate(zip(quiet_claims, quiet, strict=True))
    }
    monkeypatch.setattr(p1_ranked, "MIN_WORK_BUDGET", 4)
    monkeypatch.setattr(p1_ranked, "WORK_BUDGET_PER_K", 0)
    if tiers == "disabled":
        monkeypatch.setattr(p1_ranked, "EXACT_TIER_THRESHOLD", 0)
        monkeypatch.setattr(p1_ranked, "FILTER_PUSHDOWN_LIMIT", 0)
    only_a = _tags(all=("project:a",))
    chunks = rig.query.search_chunks(
        deployment_id=_DEPLOYMENT_ID,
        query="zebra",
        k=3,
        channel="bm25",
        documents=only_a,
    )
    claims = rig.query.search_claims(
        deployment_id=_DEPLOYMENT_ID,
        query="zebra",
        k=3,
        channel="bm25",
        documents=only_a,
    )
    facts = rig.index.search_facts_scored(
        deployment_id=str(_DEPLOYMENT_ID),
        vector=_NEAR,
        k=3,
        kind="relation",
        documents=rig.query._resolve_documents(  # noqa: SLF001
            deployment_id=_DEPLOYMENT_ID, documents=only_a
        ),
    )
    if tiers == "disabled":
        for envelope in (chunks, claims):
            assert envelope.negative is not None
            assert envelope.negative.kind is NegativeKind.BOUNDARY
        assert search_account(facts).termination is P1Termination.WORK_BUDGET
        return
    assert {item.chunk_id for item in chunks.chunks} == {
        lineage.chunk() for lineage in quiet
    }
    assert {item.claim_id for item in claims.evidence} == set(quiet_claims)
    assert {UUID(item) for item in _ids(facts)} == quiet_facts
    assert chunks.truncation is None and claims.truncation is None
    assert search_account(facts).termination.complete
