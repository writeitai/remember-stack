"""D140 §3 time-scoped text retrieval and §8.1 fact evidence gate, on PostgreSQL.

Lineages are seeded as complete live chains (``lineage_seed``) with one or
more versions; effective periods are declared through the real period API, so
the ``document_version_scope`` projection the reads probe is the one the
database triggers maintain. Chunks and claims are published into the real P1
channels and searched through the real adapter and query engine.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import UTC
import json
from pathlib import Path
import threading
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
from remember.models import EffectivePeriodInput
from remember.models import HistoryReadTime
from remember.models import OverlapReadTime
from rememberstack.adapters import BoundedPostgresReadPool
from rememberstack.adapters import PostgresP1Index
from rememberstack.core.embedding_input_policy import EMBEDDING_INPUT_POLICY_VERSION
from rememberstack.core.embedding_input_policy import embedding_text_hash
from rememberstack.core.text_scope import TextScope
from rememberstack.model import ClaimValidPrecision
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import P1ChunkRow
from rememberstack.model import P1ClaimRow
from rememberstack.model import P1FactRow
from rememberstack.model.assured_operations import AtFactTime
from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS
from rememberstack.ports.p1_index import P1Nomination
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.document_search import DocumentSearch
from rememberstack.spine.effective_time import declare_at_ingest_on
from rememberstack.spine.effective_time import EffectiveTimeCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.graph_queries import GraphQueries
from tests.database_reset import reset_database
from tests.surfaces.lineage_seed import LiveDocumentLineage
from tests.surfaces.lineage_seed import seed_entity_mention
from tests.surfaces.lineage_seed import seed_live_document_lineage

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("62000000-0000-0000-0000-00000d140032")
_MODEL = "qwen/qwen3-embedding-8b"
_NOW = datetime.now(UTC)
_PAST = datetime(2024, 1, 1, tzinfo=UTC)
_REVISED = datetime(2025, 1, 1, tzinfo=UTC)
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
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for D140 retrieval proofs")
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


@pytest.fixture()
def rig(database_engine: Engine) -> _Rig:
    """A fresh deployment with every P1 channel configured."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="d140-retrieval",
            name="D140 retrieval",
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
    )


@dataclass(frozen=True, slots=True)
class _Lineage:
    doc_id: UUID
    editions: tuple[LiveDocumentLineage, ...]

    def chunk(self, edition: int, ordinal: int = 0) -> UUID:
        return self.editions[edition].chunk_ids[ordinal]

    def version(self, edition: int) -> UUID:
        return self.editions[edition].version_id


def _lineage(
    rig: _Rig,
    *,
    label: str,
    bodies: tuple[tuple[str, ...], ...],
    vectors: tuple[tuple[tuple[float, ...], ...], ...] | None = None,
    family: str = "markdown",
) -> _Lineage:
    """One live lineage whose versions hold the given chunk bodies, published.

    The last version is the served one, as the seeder points
    ``current_version_id`` at each new version.
    """
    doc_id = uuid4()
    editions: list[LiveDocumentLineage] = []
    with rig.engine.begin() as connection:
        for number, chunks in enumerate(bodies, start=1):
            edition = seed_live_document_lineage(
                connection=connection,
                deployment_id=_DEPLOYMENT_ID,
                doc_id=doc_id,
                chunk_ids=tuple(uuid4() for _ in chunks),
                label=f"{label}-{number}",
                title=f"{label} policy",
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
                    "family": family,
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
    rows: list[P1ChunkRow] = []
    for number, (edition, chunks) in enumerate(zip(editions, bodies, strict=True)):
        for ordinal, (chunk_id, body) in enumerate(
            zip(edition.chunk_ids, chunks, strict=True)
        ):
            vector = vectors[number][ordinal] if vectors is not None else _FAR
            rows.append(
                P1ChunkRow(
                    chunk_id=chunk_id,
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
            )
    rig.index.upsert_chunks(rows=tuple(rows))
    return _Lineage(doc_id=doc_id, editions=tuple(editions))


def _declare(
    rig: _Rig,
    lineage: _Lineage,
    edition: int,
    *periods: tuple[datetime, datetime | None],
) -> None:
    rig.periods.set_effective_periods(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=lineage.doc_id,
        version_id=lineage.version(edition),
        periods=tuple(
            EffectivePeriodInput(effective_from=start, effective_until=end)
            for start, end in periods
        ),
    )


def _claim(
    rig: _Rig,
    *,
    lineage: _Lineage,
    origin: UUID,
    body: str,
    occurrences: dict[UUID, tuple[int, int]],
    vector: tuple[float, ...] = _FAR,
) -> UUID:
    """One current claim with an occurrence (and its own span) per chunk."""
    claim_id = uuid4()
    start, end = occurrences.get(origin, (0, len(body)))
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO claims (claim_id, deployment_id, doc_id, chunk_id,"
                " claim_text, source_span, char_start, char_end, anchor_ok,"
                " window_membership_ok, is_current_testimony, extractor_version,"
                " ingested_at, asserted_at) VALUES (:claim, :d, :doc, :chunk, :body,"
                " :body, :start, :end, true, true, true, 'd140-test', :at, :at)"
            ),
            {
                "claim": claim_id,
                "d": _DEPLOYMENT_ID,
                "doc": lineage.doc_id,
                "chunk": origin,
                "body": body,
                "start": start,
                "end": end,
                "at": _PAST,
            },
        )
        for chunk_id, (span_start, span_end) in occurrences.items():
            connection.execute(
                text(
                    "INSERT INTO chunk_claims (deployment_id, chunk_id, claim_id,"
                    " evidence_spans, source_locators, created_at) VALUES (:d,"
                    " :chunk, :claim, CAST(:spans AS jsonb), CAST(:locators AS"
                    " jsonb), :at)"
                ),
                {
                    "d": _DEPLOYMENT_ID,
                    "chunk": chunk_id,
                    "claim": claim_id,
                    "spans": json.dumps(
                        [{"char_start": span_start, "char_end": span_end}]
                    ),
                    "locators": json.dumps({"chunk": str(chunk_id)}),
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


def _ids(nominations: tuple[P1Nomination, ...]) -> list[str]:
    return [item.item_id for item in nominations]


def _scope(time: AtReadTime | None) -> TextScope:
    return TextScope.of(time=time, evaluated_at=datetime.now(UTC))


# --- chunk search -----------------------------------------------------------


def test_undeclared_lineage_reads_its_served_version_under_every_mode(
    rig: _Rig,
) -> None:
    """Without declarations every mode selects the served version, as before."""
    lineage = _lineage(
        rig,
        label="undeclared",
        bodies=(("old harbour tariff",), ("new harbour tariff",)),
    )
    for time in (
        None,
        AtReadTime(at=_PAST),
        OverlapReadTime.model_validate({"from": _PAST, "to": _LATER}),
        HistoryReadTime(),
    ):
        answer = rig.query.search_chunks(
            deployment_id=_DEPLOYMENT_ID,
            query="harbour tariff",
            k=5,
            channel="bm25",
            time=time,
        )
        assert [chunk.chunk_id for chunk in answer.chunks] == [lineage.chunk(1)]
        chunk = answer.chunks[0]
        assert chunk.version_id == lineage.version(1)
        assert chunk.served_version is True
        assert chunk.effective == ()
        assert answer.freshness.scope_pending is None


def test_current_scope_returns_the_edition_in_force_not_the_served_one(
    rig: _Rig,
) -> None:
    """Worked example step 3: edition 2 is served but not yet in force."""
    lineage = _lineage(
        rig,
        label="travel",
        bodies=(("daily allowance forty",), ("daily allowance fifty",)),
    )
    _declare(rig, lineage, 0, (_PAST, None))
    _declare(rig, lineage, 1, (_FUTURE, None))

    current = rig.query.search_chunks(
        deployment_id=_DEPLOYMENT_ID, query="daily allowance", k=5, channel="bm25"
    )
    assert [chunk.chunk_id for chunk in current.chunks] == [lineage.chunk(0)]
    chunk = current.chunks[0]
    assert chunk.version_id == lineage.version(0)
    assert chunk.served_version is False
    assert [
        (item.from_, item.until, item.until_declared) for item in chunk.effective
    ] == [(_PAST, _FUTURE, False)]
    assert current.temporal_scope.mode == "current"

    later = rig.query.search_chunks(
        deployment_id=_DEPLOYMENT_ID,
        query="daily allowance",
        k=5,
        channel="bm25",
        time=AtReadTime(at=_LATER),
    )
    assert [chunk.chunk_id for chunk in later.chunks] == [lineage.chunk(1)]
    assert later.chunks[0].served_version is True
    assert later.temporal_scope.mode == "at"

    history = rig.query.search_chunks(
        deployment_id=_DEPLOYMENT_ID,
        query="daily allowance",
        k=5,
        channel="bm25",
        time=HistoryReadTime(),
    )
    assert [chunk.chunk_id for chunk in history.chunks] == [lineage.chunk(0)]

    window = rig.query.search_chunks(
        deployment_id=_DEPLOYMENT_ID,
        query="daily allowance",
        k=5,
        channel="bm25",
        time=OverlapReadTime.model_validate({"from": _NOW, "to": _LATER}),
    )
    assert {chunk.chunk_id for chunk in window.chunks} == {
        lineage.chunk(0),
        lineage.chunk(1),
    }


def test_out_of_force_candidate_never_displaces_an_in_force_chunk_from_top_k(
    rig: _Rig,
) -> None:
    """The scope is a predicate inside the ranked statement, before LIMIT."""
    lineage = _lineage(
        rig,
        label="ranked",
        bodies=(("allowance rules",), ("allowance rules allowance rules",)),
        vectors=((_FAR,), (_NEAR,)),
    )
    _declare(rig, lineage, 0, (_PAST, None))
    _declare(rig, lineage, 1, (_FUTURE, None))
    deployment = str(_DEPLOYMENT_ID)

    assert _ids(
        rig.index.search_chunks_scored(deployment_id=deployment, vector=_NEAR, k=1)
    ) == [str(lineage.chunk(0))]
    assert _ids(
        rig.index.search_chunks_lexical_scored(
            deployment_id=deployment, query="allowance rules", k=1
        )
    ) == [str(lineage.chunk(0))]
    assert _ids(
        rig.index.search_chunks_scored(
            deployment_id=deployment,
            vector=_NEAR,
            k=1,
            time=_scope(AtReadTime(at=_LATER)),
        )
    ) == [str(lineage.chunk(1))]


def test_adjacent_chunks_reads_a_non_served_version_without_mixing_versions(
    rig: _Rig,
) -> None:
    lineage = _lineage(
        rig,
        label="adjacent",
        bodies=(("one a", "one b", "one c"), ("two a", "two b", "two c")),
    )
    answer = rig.query.adjacent_chunks(
        deployment_id=_DEPLOYMENT_ID, chunk_id=lineage.chunk(0, 1), window=1
    )
    assert [chunk.chunk_id for chunk in answer.chunks] == list(
        lineage.editions[0].chunk_ids
    )
    assert {chunk.version_id for chunk in answer.chunks} == {lineage.version(0)}
    assert all(chunk.served_version is False for chunk in answer.chunks)


def test_scope_pending_names_a_touched_lineage_whose_in_force_edition_is_not_ready(
    rig: _Rig,
) -> None:
    lineage = _lineage(
        rig,
        label="pending",
        bodies=(("pending clause text",), ("pending clause text revised",)),
    )
    _declare(rig, lineage, 0, (_PAST, _REVISED))
    _declare(rig, lineage, 1, (_REVISED, None))
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_versions SET status = 'converting' WHERE version_id = :v"
            ),
            {"v": lineage.version(1)},
        )
    answer = rig.query.search_chunks(
        deployment_id=_DEPLOYMENT_ID,
        query="pending clause",
        k=5,
        channel="bm25",
        documents=DocumentSearchFilters(doc_ids=(lineage.doc_id,)),
    )
    assert answer.chunks == ()
    assert answer.freshness.scope_pending is not None
    assert answer.freshness.scope_pending.doc_ids == (lineage.doc_id,)
    assert answer.freshness.scope_pending.count == 1
    # the edition in force earlier is readable and nothing is pending then
    earlier = rig.query.search_chunks(
        deployment_id=_DEPLOYMENT_ID,
        query="pending clause",
        k=5,
        channel="bm25",
        documents=DocumentSearchFilters(doc_ids=(lineage.doc_id,)),
        time=AtReadTime(at=_PAST),
    )
    assert [chunk.chunk_id for chunk in earlier.chunks] == [lineage.chunk(0)]
    assert earlier.freshness.scope_pending is None


# --- claims ------------------------------------------------------------------


def test_scoped_claim_search_returns_the_selected_editions_occurrence(
    rig: _Rig,
) -> None:
    """§3.4: the evidence is the occurrence in the selected version, not the origin."""
    lineage = _lineage(
        rig,
        label="claims",
        bodies=(
            ("Edition one. the rate is ten",),
            ("Edition two longer. the rate is ten",),
        ),
    )
    _declare(rig, lineage, 0, (_PAST, None))
    _declare(rig, lineage, 1, (_FUTURE, None))
    claim_id = _claim(
        rig,
        lineage=lineage,
        origin=lineage.chunk(1),
        body="the rate is ten",
        occurrences={lineage.chunk(0): (13, 28), lineage.chunk(1): (20, 35)},
    )

    current = rig.query.search_claims(
        deployment_id=_DEPLOYMENT_ID, query="rate is ten", k=5, channel="bm25"
    )
    assert [item.claim_id for item in current.evidence] == [claim_id]
    evidence = current.evidence[0]
    assert evidence.chunk_id == lineage.chunk(0)
    assert evidence.version_id == lineage.version(0)
    assert (evidence.char_start, evidence.char_end) == (13, 28)
    assert [(span.char_start, span.char_end) for span in evidence.evidence_spans] == [
        (13, 28)
    ]
    assert [item.version_id for item in evidence.occurrences] == [lineage.version(0)]
    assert evidence.occurrences[0].source_locators == {"chunk": str(lineage.chunk(0))}
    assert evidence.occurrences[0].served_version is False
    assert evidence.effective[0].from_ == _PAST

    later = rig.query.search_claims(
        deployment_id=_DEPLOYMENT_ID,
        query="rate is ten",
        k=5,
        channel="bm25",
        time=AtReadTime(at=_LATER),
    )
    assert later.evidence[0].chunk_id == lineage.chunk(1)
    assert (later.evidence[0].char_start, later.evidence[0].char_end) == (20, 35)

    both = rig.query.search_claims(
        deployment_id=_DEPLOYMENT_ID,
        query="rate is ten",
        k=5,
        channel="bm25",
        time=OverlapReadTime.model_validate({"from": _NOW, "to": _LATER}),
    )
    assert len(both.evidence) == 1
    assert [item.version_id for item in both.evidence[0].occurrences] == [
        lineage.version(0),
        lineage.version(1),
    ]


def test_claim_occurrence_survives_deletion_of_the_claims_origin_version(
    rig: _Rig,
) -> None:
    lineage = _lineage(
        rig,
        label="origin-deleted",
        bodies=(
            ("first edition: notice period is thirty days",),
            ("notice period is thirty days",),
        ),
    )
    _declare(rig, lineage, 0, (_PAST, None))
    _declare(rig, lineage, 1, (_FUTURE, None))
    claim_id = _claim(
        rig,
        lineage=lineage,
        origin=lineage.chunk(1),
        body="notice period is thirty days",
        occurrences={lineage.chunk(0): (15, 43), lineage.chunk(1): (0, 28)},
    )
    with rig.engine.begin() as connection:
        connection.execute(
            text("UPDATE documents SET current_version_id = :v WHERE doc_id = :doc"),
            {"v": lineage.version(0), "doc": lineage.doc_id},
        )
        connection.execute(
            text(
                "UPDATE document_versions SET deleted_at = now() WHERE version_id = :v"
            ),
            {"v": lineage.version(1)},
        )
    answer = rig.query.search_claims(
        deployment_id=_DEPLOYMENT_ID, query="notice period", k=5, channel="bm25"
    )
    assert [item.claim_id for item in answer.evidence] == [claim_id]
    evidence = answer.evidence[0]
    assert evidence.chunk_id == lineage.chunk(0)
    assert (evidence.char_start, evidence.char_end) == (15, 43)
    assert evidence.occurrences[0].served_version is True


def test_undeclared_claims_keep_their_origin_and_currency(rig: _Rig) -> None:
    lineage = _lineage(rig, label="plain-claims", bodies=(("the cap is five",),))
    claim_id = _claim(
        rig,
        lineage=lineage,
        origin=lineage.chunk(0),
        body="the cap is five",
        occurrences={lineage.chunk(0): (0, 15)},
    )
    for time in (None, AtReadTime(at=_PAST), HistoryReadTime()):
        answer = rig.query.search_claims(
            deployment_id=_DEPLOYMENT_ID,
            query="cap is five",
            k=5,
            channel="bm25",
            time=time,
        )
        assert [item.claim_id for item in answer.evidence] == [claim_id]
        assert answer.evidence[0].version_id is None
        assert answer.evidence[0].occurrences == ()
        assert answer.evidence[0].chunk_id == lineage.chunk(0)


def test_claims_and_sources_context_reads_the_scope(rig: _Rig) -> None:
    lineage = _lineage(
        rig,
        label="context",
        bodies=(("mileage rate twenty cents",), ("mileage rate thirty cents",)),
        vectors=((_NEAR,), (_NEAR,)),
    )
    _declare(rig, lineage, 0, (_PAST, None))
    _declare(rig, lineage, 1, (_FUTURE, None))
    answer = rig.query.claims_and_sources_context(
        deployment_id=_DEPLOYMENT_ID, query="mileage rate"
    )
    assert [chunk.chunk_id for chunk in answer.chunks] == [lineage.chunk(0)]
    later = rig.query.claims_and_sources_context(
        deployment_id=_DEPLOYMENT_ID, query="mileage rate", time=AtFactTime(at=_LATER)
    )
    assert [chunk.chunk_id for chunk in later.chunks] == [lineage.chunk(1)]
    assert later.temporal_scope.mode == "at"


# --- search_documents ------------------------------------------------------


def _three_editions(rig: _Rig, *, label: str, family: str = "markdown") -> _Lineage:
    lineage = _lineage(
        rig,
        label=label,
        bodies=(
            ("zircon clause one",),
            ("zircon clause two",),
            ("zircon clause three",),
        ),
        family=family,
    )
    _declare(rig, lineage, 0, (_PAST, None))
    _declare(rig, lineage, 1, (_REVISED, None))
    _declare(rig, lineage, 2, (_FUTURE, None))
    return lineage


def _search(rig: _Rig, **arguments: object):
    return rig.documents.search_documents(
        deployment_id=_DEPLOYMENT_ID,
        request=DocumentSearchRequest.model_validate(arguments),
    )


def test_search_documents_representative_and_editions_per_versions_and_mode(
    rig: _Rig,
) -> None:
    lineage = _three_editions(rig, label="zircon")
    plain = _lineage(rig, label="plain", bodies=(("zircon one",), ("zircon two",)))
    for query in (None, "zircon"):
        arguments: dict[str, object] = {} if query is None else {"query": query}
        page = _search(rig, **arguments)
        by_doc = {result.doc_id: result for result in page.documents}
        result = by_doc[lineage.doc_id]
        assert result.version_id == lineage.version(1)
        assert result.served_version is False
        assert result.p3_path is None
        assert [item.version_id for item in result.matching_editions] == [
            lineage.version(1)
        ]
        assert result.effective[0].from_ == _REVISED
        # an undeclared lineage keeps D134's judging and its P3 path
        assert by_doc[plain.doc_id].version_id == plain.version(1)
        assert by_doc[plain.doc_id].p3_path == f"documents/{plain.doc_id}"
        assert by_doc[plain.doc_id].matching_editions == ()

        history_all = _search(
            rig, **arguments, versions="all", time={"mode": "history"}
        )
        result = {item.doc_id: item for item in history_all.documents}[lineage.doc_id]
        assert result.version_id == lineage.version(1)
        assert [item.version_id for item in result.matching_editions] == [
            lineage.version(0),
            lineage.version(1),
        ]
        assert result.other_matching_version_ids == (lineage.version(0),)

        history_current = _search(rig, **arguments, time={"mode": "history"})
        result = {item.doc_id: item for item in history_current.documents}[
            lineage.doc_id
        ]
        assert [item.version_id for item in result.matching_editions] == [
            lineage.version(1)
        ]

        future = _search(
            rig,
            **arguments,
            versions="all",
            time={
                "mode": "overlap",
                "from": _NOW.isoformat(),
                "to": _LATER.isoformat(),
            },
        )
        result = {item.doc_id: item for item in future.documents}[lineage.doc_id]
        assert result.version_id == lineage.version(2)
        assert result.served_version is True
        assert result.p3_path == f"documents/{lineage.doc_id}"


def test_search_documents_all_versions_judges_only_matching_candidates(
    rig: _Rig,
) -> None:
    lineage = _three_editions(rig, label="filtered")
    with rig.engine.begin() as connection:
        connection.execute(
            text("UPDATE document_metadata SET family = 'pdf' WHERE version_id = :v"),
            {"v": lineage.version(0)},
        )
    pdf = DocumentSearchFilters(family=("pdf",))
    current = _search(rig, filters=pdf.model_dump(), versions="current")
    assert current.documents == ()
    every = _search(
        rig, filters=pdf.model_dump(), versions="all", time={"mode": "history"}
    )
    assert [item.version_id for item in every.documents] == [lineage.version(0)]


def _walk_all(rig: _Rig, *, between=None, **arguments: object) -> list[UUID]:  # noqa: ANN001
    seen: list[UUID] = []
    cursor: str | None = None
    first = True
    while True:
        page = _search(rig, k=1, **arguments, **({"cursor": cursor} if cursor else {}))
        seen.extend(result.doc_id for result in page.documents)
        if first and between is not None:
            between()
        first = False
        cursor = page.cursor
        if cursor is None:
            return seen


def test_paging_pins_belief_across_a_retroactive_correction(rig: _Rig) -> None:
    lineages = [_three_editions(rig, label=f"paged-{index}") for index in range(4)]
    plain = _lineage(rig, label="paged-plain", bodies=(("zircon",),))
    baseline = _walk_all(rig)
    assert sorted(baseline) == sorted(
        [lineage.doc_id for lineage in lineages] + [plain.doc_id]
    )

    def correct() -> None:
        # withdraw the in-force edition of every remaining lineage, and clear
        # and redeclare another
        for lineage in lineages:
            _declare(rig, lineage, 1, (_REVISED, _REVISED + timedelta(days=1)))
        rig.periods.clear_effective_time(
            deployment_id=_DEPLOYMENT_ID, doc_id=lineages[0].doc_id
        )
        _declare(rig, lineages[0], 2, (_FUTURE, None))

    paged = _walk_all(rig, between=correct)
    assert paged == baseline
    assert len(set(paged)) == len(paged)
    # a fresh first page sees the correction
    fresh = {item.doc_id for item in _search(rig, k=50).documents}
    assert fresh == {plain.doc_id}


def test_paging_returns_a_short_page_with_a_cursor_at_the_scan_cap(rig: _Rig) -> None:
    for index in range(12):
        lineage = _lineage(rig, label=f"withdrawn-{index}", bodies=(("x",),))
        _declare(rig, lineage, 0, (_PAST, _REVISED))
    kept = _lineage(rig, label="kept-first", bodies=(("x",),))
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_versions SET ingested_at = now() - interval '1 day'"
                " WHERE doc_id = :doc"
            ),
            {"doc": kept.doc_id},
        )
    page = _search(rig, k=1)
    assert page.documents == ()
    assert page.cursor is not None
    assert _walk_all(rig) == [kept.doc_id]


# --- fact evidence gate ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Facts:
    subject: UUID
    facts: dict[str, UUID]
    claims: dict[str, UUID]
    lineages: dict[str, _Lineage]


def _gate_corpus(rig: _Rig) -> _Facts:
    """Facts whose support is in force, future-only, repealed-only, mixed, undeclared."""
    statute = _lineage(
        rig, label="statute", bodies=(("old rule text",), ("new rule text",))
    )
    _declare(rig, statute, 0, (_PAST, None))
    _declare(rig, statute, 1, (_FUTURE, None))
    repealed = _lineage(rig, label="repealed", bodies=(("repealed rule text",),))
    _declare(rig, repealed, 0, (_PAST, _REVISED))
    memo = _lineage(rig, label="memo", bodies=(("memo rule text",),))
    claims = {
        "in_force": _claim(
            rig,
            lineage=statute,
            origin=statute.chunk(0),
            body="old rule",
            occurrences={statute.chunk(0): (0, 8)},
        ),
        "future": _claim(
            rig,
            lineage=statute,
            origin=statute.chunk(1),
            body="new rule",
            occurrences={statute.chunk(1): (0, 8)},
        ),
        "repealed": _claim(
            rig,
            lineage=repealed,
            origin=repealed.chunk(0),
            body="repealed rule",
            occurrences={repealed.chunk(0): (0, 13)},
        ),
        "memo": _claim(
            rig,
            lineage=memo,
            origin=memo.chunk(0),
            body="memo rule",
            occurrences={memo.chunk(0): (0, 9)},
        ),
    }
    subject = uuid4()
    objects = {
        key: uuid4()
        for key in ("in_force", "future", "repealed", "mixed", "memo", "dated")
    }
    facts = {key: uuid4() for key in objects}
    support = {
        "in_force": ("in_force",),
        "future": ("future",),
        "repealed": ("repealed",),
        "mixed": ("in_force", "future"),
        "memo": ("memo",),
        "dated": ("future",),
    }
    with rig.engine.begin() as connection:
        for entity_id, name in (
            (subject, "Subject"),
            *((value, f"Object {key}") for key, value in objects.items()),
        ):
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id, canonical_name,"
                    " normalized_name) VALUES (:e, :d, :name, lower(:name))"
                ),
                {"e": entity_id, "d": _DEPLOYMENT_ID, "name": name},
            )
        for key, fact_id in facts.items():
            connection.execute(
                text(
                    "INSERT INTO relations (relation_id, deployment_id,"
                    " subject_entity_id, predicate, object_entity_id,"
                    " normalizer_version, fact_label, ingested_at, valid_from,"
                    " valid_precision, window_claim_ids) VALUES (:fact, :d,"
                    " :subject, 'works_for', :object, 'd140-test', :label, :at,"
                    " :valid_from, 'open', :witnesses)"
                ),
                {
                    "fact": fact_id,
                    "d": _DEPLOYMENT_ID,
                    "subject": subject,
                    "object": objects[key],
                    "label": f"Subject rule {key}",
                    "at": _PAST,
                    # "dated": its own window says it holds since 2025, but its
                    # only text is in force from the future
                    "valid_from": _REVISED if key == "dated" else _PAST,
                    "witnesses": [claims[support[key][0]]],
                },
            )
            for claim_key in support[key]:
                lineage = {
                    "in_force": statute,
                    "future": statute,
                    "repealed": repealed,
                    "memo": memo,
                }[claim_key]
                chunk = {
                    "in_force": statute.chunk(0),
                    "future": statute.chunk(1),
                    "repealed": repealed.chunk(0),
                    "memo": memo.chunk(0),
                }[claim_key]
                connection.execute(
                    text(
                        "INSERT INTO relation_evidence (deployment_id, relation_id,"
                        " claim_id, doc_id, stance, normalizer_version) VALUES"
                        " (:d, :fact, :claim, :doc, 'supports', 'd140-test')"
                    ),
                    {
                        "d": _DEPLOYMENT_ID,
                        "fact": fact_id,
                        "claim": claims[claim_key],
                        "doc": lineage.doc_id,
                    },
                )
                for entity_id in (subject, objects[key]):
                    seed_entity_mention(
                        connection=connection,
                        deployment_id=_DEPLOYMENT_ID,
                        entity_id=entity_id,
                        doc_id=lineage.doc_id,
                        chunk_id=chunk,
                        claim_id=claims[claim_key],
                        surface_form=f"anchor-{entity_id}",
                        at=_PAST,
                        resolver_version="d140-test",
                    )
    rig.index.upsert_facts(
        rows=tuple(
            P1FactRow(
                fact_id=fact_id,
                deployment_id=_DEPLOYMENT_ID,
                kind="relation",
                label=f"Subject rule {key}",
                status="active",
                valid_from=_REVISED if key == "dated" else _PAST,
                valid_until=None,
                valid_precision=ClaimValidPrecision.OPEN,
                ingested_at=_PAST,
                invalidated_at=None,
                # the out-of-force facts rank first
                vector=_NEAR if key in {"future", "repealed", "dated"} else _FAR,
            )
            for key, fact_id in facts.items()
        )
    )
    return _Facts(
        subject=subject,
        facts=facts,
        claims=claims,
        lineages={"statute": statute, "repealed": repealed, "memo": memo},
    )


def test_out_of_force_facts_never_displace_in_force_facts_from_top_k(rig: _Rig) -> None:
    corpus = _gate_corpus(rig)
    deployment = str(_DEPLOYMENT_ID)
    in_scope = {str(corpus.facts[key]) for key in ("in_force", "mixed", "memo")}
    for method in (rig.index.search_facts_scored, rig.index.nominate_facts_scored):
        top = _ids(method(deployment_id=deployment, vector=_NEAR, k=3, kind="relation"))
        assert set(top) == in_scope
    future = set(
        _ids(
            rig.index.search_facts_scored(
                deployment_id=deployment,
                vector=_NEAR,
                k=10,
                kind="relation",
                time=AtFactTime(at=_LATER),
            )
        )
    )
    assert str(corpus.facts["future"]) in future
    assert str(corpus.facts["dated"]) in future
    assert str(corpus.facts["repealed"]) not in future
    assert str(corpus.facts["in_force"]) not in future
    assert str(corpus.facts["memo"]) in future
    past = set(
        _ids(
            rig.index.search_facts_scored(
                deployment_id=deployment,
                vector=_NEAR,
                k=10,
                kind="relation",
                time=AtFactTime(at=_PAST + timedelta(days=10)),
            )
        )
    )
    assert str(corpus.facts["repealed"]) in past
    # conflicting dates: the fact's own window holds from 2025, but the only
    # text supporting it is in force from the future
    revised = set(
        _ids(
            rig.index.search_facts_scored(
                deployment_id=deployment,
                vector=_NEAR,
                k=10,
                kind="relation",
                time=AtFactTime(at=_REVISED + timedelta(days=10)),
            )
        )
    )
    assert str(corpus.facts["dated"]) not in revised
    assert str(corpus.facts["repealed"]) not in revised
    assert str(corpus.facts["in_force"]) in revised


def test_facts_context_gates_confirmation_and_shows_in_scope_evidence(
    rig: _Rig,
) -> None:
    corpus = _gate_corpus(rig)
    answer = rig.query.facts_context(
        deployment_id=_DEPLOYMENT_ID, query="subject rule", k=10, evidence_per_fact=3
    )
    returned = {fact.fact_id for fact in answer.facts}
    assert returned == {corpus.facts[key] for key in ("in_force", "mixed", "memo")}
    shown = {item.claim_id for item in answer.evidence}
    assert corpus.claims["future"] not in shown
    assert corpus.claims["in_force"] in shown
    statute = corpus.lineages["statute"]
    in_force = next(
        item for item in answer.evidence if item.claim_id == corpus.claims["in_force"]
    )
    assert in_force.version_id == statute.version(0)
    mixed_total = next(
        total
        for total in answer.evidence_totals
        if total.fact_id == corpus.facts["mixed"] and total.stance == "supports"
    )
    assert mixed_total.total == 1

    later = rig.query.facts_context(
        deployment_id=_DEPLOYMENT_ID,
        query="subject rule",
        k=10,
        time=AtFactTime(at=_LATER),
    )
    assert corpus.facts["future"] in {fact.fact_id for fact in later.facts}
    assert corpus.facts["in_force"] not in {fact.fact_id for fact in later.facts}


def test_relation_lookup_and_one_hop_graph_apply_the_gate(rig: _Rig) -> None:
    corpus = _gate_corpus(rig)
    lookup = rig.query.lookup_relations(
        deployment_id=_DEPLOYMENT_ID, subject_entity_id=corpus.subject, k=20
    )
    assert {fact.fact_id for fact in lookup.facts} == {
        corpus.facts[key] for key in ("in_force", "mixed", "memo")
    }
    as_of = rig.query.lookup_relations(
        deployment_id=_DEPLOYMENT_ID,
        subject_entity_id=corpus.subject,
        valid_at=_LATER,
        k=20,
    )
    assert corpus.facts["future"] in {fact.fact_id for fact in as_of.facts}

    graph = GraphQueries(engine=rig.engine, deployment_id=_DEPLOYMENT_ID)
    neighbourhood = graph.neighborhood(entity_id=corpus.subject, hops=1)
    assert neighbourhood.negative is None, neighbourhood.negative
    reached = {node.entity_id for node in neighbourhood.nodes}
    with rig.engine.connect() as connection:
        objects: dict[UUID, UUID] = {
            row.relation_id: row.object_entity_id
            for row in connection.execute(
                text(
                    "SELECT relation_id, object_entity_id FROM relations"
                    " WHERE deployment_id = :d"
                ),
                {"d": _DEPLOYMENT_ID},
            )
        }
    assert objects[corpus.facts["in_force"]] in reached
    assert objects[corpus.facts["future"]] not in reached
    assert objects[corpus.facts["repealed"]] not in reached


def test_undeclared_corpus_facts_are_unaffected(rig: _Rig) -> None:
    corpus = _gate_corpus(rig)
    with rig.engine.connect() as connection:
        count = connection.execute(
            text(
                "SELECT count(*) FROM document_version_scope"
                " WHERE deployment_id = :d AND doc_id = :doc AND periodised"
            ),
            {"d": _DEPLOYMENT_ID, "doc": corpus.lineages["memo"].doc_id},
        ).scalar_one()
    assert count == 0
    for time in (None, AtFactTime(at=_PAST), AtFactTime(at=_LATER)):
        assert str(corpus.facts["memo"]) in _ids(
            rig.index.search_facts_scored(
                deployment_id=str(_DEPLOYMENT_ID),
                vector=_FAR,
                k=10,
                kind="relation",
                time=time,
            )
        )


# --- implementation review round 1 (P1-1, P1-2, P1-5) -----------------------


def _relation(
    rig: _Rig,
    *,
    label: str,
    support: tuple[tuple[UUID, _Lineage, UUID], ...],
    vector: tuple[float, ...] = _NEAR,
    stance: str = "supports",
    subject: UUID | None = None,
    fact_id: UUID | None = None,
) -> UUID:
    """One open relation linked to the given (claim, lineage, chunk) triples."""
    new_subject = subject is None
    subject = subject or uuid4()
    obj, fact_id = uuid4(), fact_id or uuid4()
    with rig.engine.begin() as connection:
        entities: tuple[tuple[UUID, str], ...] = ((obj, f"{label} object"),)
        if new_subject:
            entities = ((subject, f"{label} subject"), *entities)
        for entity_id, name in entities:
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id, canonical_name,"
                    " normalized_name) VALUES (:e, :d, :name, lower(:name))"
                ),
                {"e": entity_id, "d": _DEPLOYMENT_ID, "name": name},
            )
        connection.execute(
            text(
                "INSERT INTO relations (relation_id, deployment_id,"
                " subject_entity_id, predicate, object_entity_id,"
                " normalizer_version, fact_label, ingested_at, valid_from,"
                " valid_precision, window_claim_ids) VALUES (:fact, :d,"
                " :subject, 'works_for', :object, 'd140-test', :label, :at, :at,"
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
        for claim_id, lineage, chunk in support:
            connection.execute(
                text(
                    "INSERT INTO relation_evidence (deployment_id, relation_id,"
                    " claim_id, doc_id, stance, normalizer_version) VALUES"
                    " (:d, :fact, :claim, :doc, CAST(:stance AS evidence_stance),"
                    " 'd140-test')"
                ),
                {
                    "d": _DEPLOYMENT_ID,
                    "fact": fact_id,
                    "claim": claim_id,
                    "doc": lineage.doc_id,
                    "stance": stance,
                },
            )
            for entity_id in (subject, obj):
                seed_entity_mention(
                    connection=connection,
                    deployment_id=_DEPLOYMENT_ID,
                    entity_id=entity_id,
                    doc_id=lineage.doc_id,
                    chunk_id=chunk,
                    claim_id=claim_id,
                    surface_form=f"anchor-{entity_id}",
                    at=_PAST,
                    resolver_version="d140-test",
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


def _swap_reading(rig: _Rig, version_id: UUID) -> UUID:
    """Point a version at a fresh representation (D65); its old chunks go stale."""
    representation_id = uuid4()
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO document_representations (representation_id,"
                " deployment_id, version_id, route, markdown_uri, status) VALUES"
                " (:r, :d, :v, 'digital', :uri, 'ready')"
            ),
            {
                "r": representation_id,
                "d": _DEPLOYMENT_ID,
                "v": version_id,
                "uri": f"mem://artifacts/{representation_id}.md",
            },
        )
        connection.execute(
            text(
                "UPDATE document_versions SET current_representation_id = :r"
                " WHERE version_id = :v"
            ),
            {"r": representation_id, "v": version_id},
        )
    return representation_id


def _fact_ids_everywhere(rig: _Rig, *, subject_query: str) -> dict[str, set[UUID]]:
    """The fact ids each read path returns at the default (current) scope."""
    deployment = str(_DEPLOYMENT_ID)
    scored = {
        UUID(item)
        for item in _ids(
            rig.index.search_facts_scored(
                deployment_id=deployment, vector=_NEAR, k=20, kind="relation"
            )
        )
    }
    nominated = {
        UUID(item)
        for item in _ids(
            rig.index.nominate_facts_scored(
                deployment_id=deployment, vector=_NEAR, k=20, kind="relation"
            )
        )
    }
    context = {
        fact.fact_id
        for fact in rig.query.facts_context(
            deployment_id=_DEPLOYMENT_ID, query=subject_query, k=20
        ).facts
    }
    return {"scored": scored, "nominated": nominated, "context": context}


def _support_function(rig: _Rig, fact_id: UUID) -> bool:
    with rig.engine.connect() as connection:
        return bool(
            connection.execute(
                text(
                    "SELECT memory_v1.fact_in_scope_support(:d, 'relation', :f,"
                    " 'current')"
                ),
                {"d": _DEPLOYMENT_ID, "f": fact_id},
            ).scalar_one()
        )


def test_a_fact_without_live_supporting_text_is_not_eligible(rig: _Rig) -> None:
    """§8.1: at least one in-scope supporting occurrence; no vacuous pass."""
    memo = _lineage(rig, label="memo-live", bodies=(("memo live text",),))
    gone = _lineage(rig, label="memo-gone", bodies=(("memo gone text",),))
    live_claim = _claim(
        rig,
        lineage=memo,
        origin=memo.chunk(0),
        body="memo live",
        occurrences={memo.chunk(0): (0, 9)},
    )
    gone_claim = _claim(
        rig,
        lineage=gone,
        origin=gone.chunk(0),
        body="memo gone",
        occurrences={gone.chunk(0): (0, 9)},
    )
    supported = _relation(
        rig, label="Memo governs live", support=((live_claim, memo, memo.chunk(0)),)
    )
    deleted_only = _relation(
        rig, label="Memo governs gone", support=((gone_claim, gone, gone.chunk(0)),)
    )
    against_gone = _claim(
        rig,
        lineage=gone,
        origin=gone.chunk(0),
        body="memo gone against",
        occurrences={gone.chunk(0): (0, 4)},
    )
    unsupported = _relation(
        rig,
        label="Memo governs nothing",
        support=((against_gone, gone, gone.chunk(0)),),
        stance="contradicts",
    )
    # D54 parity: a contradiction-only fact with live (undated) evidence is
    # flagged, not hidden, exactly as before D140
    against_live = _claim(
        rig,
        lineage=memo,
        origin=memo.chunk(0),
        body="memo against",
        occurrences={memo.chunk(0): (0, 4)},
    )
    contradicted_only = _relation(
        rig,
        label="Memo governs disputed",
        support=((against_live, memo, memo.chunk(0)),),
        stance="contradicts",
    )
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_versions SET deleted_at = now() WHERE version_id = :v"
            ),
            {"v": gone.version(0)},
        )
    seen = _fact_ids_everywhere(rig, subject_query="memo governs")
    for path, ids in seen.items():
        assert supported in ids, path
        assert deleted_only not in ids, path
        assert unsupported not in ids, path
    assert _support_function(rig, supported) is True
    assert _support_function(rig, deleted_only) is False
    assert _support_function(rig, unsupported) is False
    lookup = rig.query.lookup_relations(deployment_id=_DEPLOYMENT_ID, k=50)
    assert contradicted_only in {fact.fact_id for fact in lookup.facts}
    assert _support_function(rig, contradicted_only) is True


def test_an_occurrence_left_in_a_replaced_reading_does_not_support_a_fact(
    rig: _Rig,
) -> None:
    """§3.3/§8.1 with D65: only the version's current reading is evidence."""
    statute = _lineage(rig, label="reread", bodies=(("reread rule text",),))
    _declare(rig, statute, 0, (_PAST, None))
    claim_id = _claim(
        rig,
        lineage=statute,
        origin=statute.chunk(0),
        body="reread rule",
        occurrences={statute.chunk(0): (0, 11)},
    )
    fact_id = _relation(
        rig, label="Reread governs", support=((claim_id, statute, statute.chunk(0)),)
    )
    assert (
        fact_id in _fact_ids_everywhere(rig, subject_query="reread governs")["scored"]
    )
    assert _support_function(rig, fact_id) is True
    _swap_reading(rig, statute.version(0))
    seen = _fact_ids_everywhere(rig, subject_query="reread governs")
    for path, ids in seen.items():
        assert fact_id not in ids, path
    assert _support_function(rig, fact_id) is False


def test_facts_context_shows_a_reused_claim_after_its_origin_version_is_deleted(
    rig: _Rig,
) -> None:
    """§3.4/§8.1: evidence comes from the selected occurrence, not the origin."""
    statute = _lineage(
        rig,
        label="reused",
        bodies=(("first: notice is thirty days",), ("notice is thirty days",)),
    )
    _declare(rig, statute, 0, (_PAST, None))
    _declare(rig, statute, 1, (_FUTURE, None))
    claim_id = _claim(
        rig,
        lineage=statute,
        origin=statute.chunk(1),
        body="notice is thirty days",
        occurrences={statute.chunk(0): (7, 28), statute.chunk(1): (0, 21)},
    )
    fact_id = _relation(
        rig, label="Notice governs", support=((claim_id, statute, statute.chunk(0)),)
    )
    with rig.engine.begin() as connection:
        connection.execute(
            text("UPDATE documents SET current_version_id = :v WHERE doc_id = :doc"),
            {"v": statute.version(0), "doc": statute.doc_id},
        )
        connection.execute(
            text(
                "UPDATE document_versions SET deleted_at = now() WHERE version_id = :v"
            ),
            {"v": statute.version(1)},
        )
    answer = rig.query.facts_context(
        deployment_id=_DEPLOYMENT_ID, query="notice governs", k=10, evidence_per_fact=3
    )
    assert fact_id in {fact.fact_id for fact in answer.facts}
    evidence = [item for item in answer.evidence if item.claim_id == claim_id]
    assert len(evidence) == 1
    assert evidence[0].chunk_id == statute.chunk(0)
    assert evidence[0].version_id == statute.version(0)
    assert (evidence[0].char_start, evidence[0].char_end) == (7, 28)


# --- implementation review round 1 (P1-3): commit-visible belief pin --------


def test_a_first_page_never_misses_a_correction_stamped_before_its_belief(
    rig: _Rig,
) -> None:
    """A writer stamps a correction, then commits after page one starts.

    Page one's pinned belief instant must reflect exactly the declarations a
    later reconstruction at that instant sees; a pre-commit stamp earlier than
    the instant may not surface only on a later page.
    """
    older = _lineage(rig, label="pin-older", bodies=(("x",),))
    _declare(rig, older, 0, (_PAST, None))
    corrected = _lineage(rig, label="pin-corrected", bodies=(("x",),))
    _declare(rig, corrected, 0, (_FUTURE, None))

    stamped = threading.Event()
    release = threading.Event()

    def write() -> None:
        with rig.engine.begin() as connection:
            connection.execute(
                text(
                    "SELECT doc_id FROM documents WHERE deployment_id = :d"
                    " AND doc_id = :doc FOR UPDATE"
                ),
                {"d": _DEPLOYMENT_ID, "doc": corrected.doc_id},
            )
            declare_at_ingest_on(
                connection=connection,
                deployment_id=_DEPLOYMENT_ID,
                doc_id=corrected.doc_id,
                version_id=corrected.version(0),
                versioning_mode="snapshot",
                effective_from=_PAST,
                effective_until=None,
            )
            stamped.set()
            release.wait(timeout=30)

    pages: list[object] = []

    def read() -> None:
        pages.append(_search(rig, k=1))

    writer = threading.Thread(target=write)
    writer.start()
    assert stamped.wait(timeout=30)
    reader = threading.Thread(target=read)
    reader.start()
    reader.join(timeout=1.0)
    release.set()
    writer.join(timeout=30)
    reader.join(timeout=30)
    page = pages[0]
    shown = {item.doc_id for item in page.documents}  # type: ignore[attr-defined]
    with rig.engine.connect() as connection:
        selected_at_pin = connection.execute(
            text(
                "SELECT count(*) FROM memory_v1.versions_in_scope(:d, 'current',"
                " NULL, NULL, NULL, :pin, :pin, CAST(:docs AS uuid[]))"
            ),
            {
                "d": _DEPLOYMENT_ID,
                "pin": page.as_of,  # type: ignore[attr-defined]
                "docs": [str(corrected.doc_id)],
            },
        ).scalar_one()
    assert (corrected.doc_id in shown) == (selected_at_pin > 0)
    assert corrected.doc_id in shown


# --- implementation review round 1 (P1-6): pending on an empty answer -------


def test_an_empty_answer_names_the_pending_lineage_the_query_reaches(rig: _Rig) -> None:
    """§3.7 without a document filter: unfiltered chunk, claim, compound and
    ranked document searches all say the in-force text is still processing."""
    lineage = _lineage(
        rig,
        label="quartz",
        bodies=(("quartz allowance clause",), ("quartz allowance clause revised",)),
    )
    _declare(rig, lineage, 0, (_PAST, _REVISED))
    _declare(rig, lineage, 1, (_REVISED, None))
    unrelated = _lineage(
        rig, label="basalt", bodies=(("basalt rule",), ("basalt rule revised",))
    )
    _declare(rig, unrelated, 0, (_PAST, _REVISED))
    _declare(rig, unrelated, 1, (_REVISED, None))
    _claim(
        rig,
        lineage=lineage,
        origin=lineage.chunk(0),
        body="quartz allowance",
        occurrences={lineage.chunk(0): (0, 16)},
    )
    with rig.engine.begin() as connection:
        for item in (lineage, unrelated):
            # the new edition is still converting; the old one stays served
            connection.execute(
                text(
                    "UPDATE documents SET current_version_id = :v WHERE doc_id = :doc"
                ),
                {"v": item.version(0), "doc": item.doc_id},
            )
            connection.execute(
                text(
                    "UPDATE document_versions SET status = 'converting'"
                    " WHERE version_id = :v"
                ),
                {"v": item.version(1)},
            )

    chunks = rig.query.search_chunks(
        deployment_id=_DEPLOYMENT_ID, query="quartz allowance", k=5, channel="bm25"
    )
    claims = rig.query.search_claims(
        deployment_id=_DEPLOYMENT_ID, query="quartz allowance", k=5, channel="bm25"
    )
    context = rig.query.claims_and_sources_context(
        deployment_id=_DEPLOYMENT_ID, query="quartz allowance", k=5
    )
    for name, answer in (("chunks", chunks), ("claims", claims), ("context", context)):
        assert answer.chunks == () and answer.evidence == (), name
        assert answer.freshness.scope_pending is not None, name
    # BM25 reaches only the lineage whose text has the terms
    for answer in (chunks, claims):
        pending = answer.freshness.scope_pending
        assert pending is not None and pending.doc_ids == (lineage.doc_id,)
    # the compound context also nominates semantically (top candidate_k by
    # vector), which in this two-lineage corpus reaches every readable text
    context_pending = context.freshness.scope_pending
    assert context_pending is not None and lineage.doc_id in context_pending.doc_ids
    documents = _search(rig, query="quartz allowance", k=5)
    assert documents.documents == ()
    assert documents.scope_pending is not None
    assert documents.scope_pending.doc_ids == (lineage.doc_id,)


# --- implementation review round 1 (deviation a): gate inside traversal -----


def _endpoint(rig: _Rig, relation_id: UUID, column: str) -> UUID:
    with rig.engine.connect() as connection:
        return connection.execute(
            text(f"SELECT {column} FROM relations WHERE relation_id = :r"),  # noqa: S608
            {"r": relation_id},
        ).scalar_one()


def _hub(rig: _Rig) -> tuple[UUID, UUID, UUID, _Lineage]:
    """A subject with a future-only edge that sorts first and an in-force one."""
    statute = _lineage(rig, label="hub", bodies=(("old hub rule",), ("new hub rule",)))
    _declare(rig, statute, 0, (_PAST, None))
    _declare(rig, statute, 1, (_FUTURE, None))
    in_force = _claim(
        rig,
        lineage=statute,
        origin=statute.chunk(0),
        body="old hub rule",
        occurrences={statute.chunk(0): (0, 12)},
    )
    future = _claim(
        rig,
        lineage=statute,
        origin=statute.chunk(1),
        body="new hub rule",
        occurrences={statute.chunk(1): (0, 12)},
    )
    excluded = _relation(
        rig,
        label="Hub future",
        support=((future, statute, statute.chunk(1)),),
        fact_id=UUID("00000000-0000-4000-8000-000000000001"),
    )
    subject = _endpoint(rig, excluded, "subject_entity_id")
    eligible = _relation(
        rig,
        label="Hub in force",
        support=((in_force, statute, statute.chunk(0)),),
        subject=subject,
        fact_id=UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
    )
    return subject, excluded, eligible, statute


@pytest.mark.parametrize("hops", [1, 2])
def test_an_ineligible_first_edge_never_takes_the_only_result_slot(
    rig: _Rig, hops: int
) -> None:
    subject, excluded, eligible, _ = _hub(rig)
    graph = GraphQueries(engine=rig.engine, deployment_id=_DEPLOYMENT_ID)
    page = graph.neighborhood(entity_id=subject, hops=hops, limit=1)
    assert page.negative is None, page.negative
    assert [node.entity_id for node in page.nodes] == [
        _endpoint(rig, eligible, "object_entity_id")
    ]
    assert page.truncation is None or page.truncation.truncated is False
    blocked = graph.path(
        from_entity_id=subject,
        to_entity_id=_endpoint(rig, excluded, "object_entity_id"),
    )
    assert blocked.paths == ()
    reached = graph.path(
        from_entity_id=subject,
        to_entity_id=_endpoint(rig, eligible, "object_entity_id"),
    )
    assert reached.paths != ()


def test_traversal_and_gate_read_one_snapshot(rig: _Rig) -> None:
    """A deletion committed after the caller's snapshot does not split them."""
    subject, _, eligible, statute = _hub(rig)
    graph = GraphQueries(engine=rig.engine, deployment_id=_DEPLOYMENT_ID)
    with rig.engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    ) as snapshot:
        snapshot.exec_driver_sql("SET TRANSACTION READ ONLY")
        snapshot.execute(text("SELECT 1"))  # the snapshot starts here
        with rig.engine.begin() as writer:
            writer.execute(
                text(
                    "UPDATE document_versions SET deleted_at = now()"
                    " WHERE version_id = :v"
                ),
                {"v": statute.version(0)},
            )
        page = graph.neighborhood(entity_id=subject, hops=1, _connection=snapshot)
        snapshot.rollback()
    assert [node.entity_id for node in page.nodes] == [
        _endpoint(rig, eligible, "object_entity_id")
    ]
    assert graph.neighborhood(entity_id=subject, hops=1).nodes == ()


# --- implementation review round 2 (P1-6 remainder): probe follows the request -


def _pending_pair(
    rig: _Rig, *, label: str, body: str, vector: tuple[float, ...]
) -> _Lineage:
    """A lineage whose readable edition holds ``body``; the in-force one converts."""
    lineage = _lineage(
        rig,
        label=label,
        bodies=((body,), (body + " revised",)),
        vectors=((vector,), (vector,)),
    )
    _declare(rig, lineage, 0, (_PAST, _REVISED))
    _declare(rig, lineage, 1, (_REVISED, None))
    with rig.engine.begin() as connection:
        connection.execute(
            text("UPDATE documents SET current_version_id = :v WHERE doc_id = :doc"),
            {"v": lineage.version(0), "doc": lineage.doc_id},
        )
        connection.execute(
            text(
                "UPDATE document_versions SET status = 'converting'"
                " WHERE version_id = :v"
            ),
            {"v": lineage.version(1)},
        )
    return lineage


def test_a_semantic_only_match_names_its_pending_lineage(rig: _Rig) -> None:
    """The query vector reaches the text although no query term occurs in it."""
    reached = _pending_pair(
        rig, label="vehicle", body="automobile parking", vector=_NEAR
    )
    _pending_pair(rig, label="unrelated", body="garden hedges", vector=_FAR)
    answer = rig.query.search_chunks(
        deployment_id=_DEPLOYMENT_ID, query="vehicle rules", k=1, channel="semantic"
    )
    assert answer.chunks == ()
    assert answer.freshness.scope_pending is not None
    assert answer.freshness.scope_pending.doc_ids == (reached.doc_id,)


def test_a_claim_text_only_match_names_its_pending_lineage(rig: _Rig) -> None:
    """Claim search matches decontextualized claim text, not the chunk's words."""
    reached = _pending_pair(rig, label="tenure", body="she said so", vector=_FAR)
    _claim(
        rig,
        lineage=reached,
        origin=reached.chunk(0),
        body="the employee tenure requirement",
        occurrences={reached.chunk(0): (0, 11)},
    )
    unrelated = _pending_pair(rig, label="hedges", body="garden hedges", vector=_FAR)
    _claim(
        rig,
        lineage=unrelated,
        origin=unrelated.chunk(0),
        body="hedges are trimmed",
        occurrences={unrelated.chunk(0): (0, 6)},
    )
    answer = rig.query.search_claims(
        deployment_id=_DEPLOYMENT_ID, query="tenure requirement", k=5, channel="bm25"
    )
    assert answer.evidence == ()
    assert answer.freshness.scope_pending is not None
    assert answer.freshness.scope_pending.doc_ids == (reached.doc_id,)


# --- implementation review round 2 (P2): paging under deletion and pending ----


def _ingested(rig: _Rig, version_id: UUID, at: datetime) -> None:
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_versions SET ingested_at = :at WHERE version_id = :v"
            ),
            {"at": at, "v": version_id},
        )


def test_soft_deleting_a_returned_lineages_newest_version_never_repeats_it(
    rig: _Rig,
) -> None:
    """P2-1: the walk key ignores later tombstones, so no lineage comes twice."""
    newest = _lineage(rig, label="walk-newest", bodies=(("a",), ("a two",)))
    middle = _lineage(rig, label="walk-middle", bodies=(("b",),))
    oldest = _lineage(rig, label="walk-oldest", bodies=(("c",),))
    _ingested(rig, newest.version(0), _NOW - timedelta(days=10))
    _ingested(rig, newest.version(1), _NOW - timedelta(hours=1))
    _ingested(rig, middle.version(0), _NOW - timedelta(days=1))
    _ingested(rig, oldest.version(0), _NOW - timedelta(days=2))

    def delete_newest_version() -> None:
        with rig.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE documents SET current_version_id = :v WHERE doc_id = :doc"
                ),
                {"v": newest.version(0), "doc": newest.doc_id},
            )
            connection.execute(
                text(
                    "UPDATE document_versions SET deleted_at = now()"
                    " WHERE version_id = :v"
                ),
                {"v": newest.version(1)},
            )

    walked = _walk_all(rig, between=delete_newest_version)
    assert walked[0] == newest.doc_id
    assert len(walked) == len(set(walked))
    assert set(walked) == {newest.doc_id, middle.doc_id, oldest.doc_id}


def _walk_pending(rig: _Rig, *, between) -> set[UUID]:  # noqa: ANN001
    """Every lineage any filter-only page reported as pending."""
    pending: set[UUID] = set()
    cursor: str | None = None
    first = True
    while True:
        page = _search(rig, k=1, **({"cursor": cursor} if cursor else {}))
        if page.scope_pending is not None:
            pending.update(page.scope_pending.doc_ids)
        if first:
            between()
        first = False
        cursor = page.cursor
        if cursor is None:
            return pending


@pytest.mark.parametrize("change", ["correction", "clear_redeclare"])
def test_pinned_pages_keep_reporting_pending_as_known_at_their_belief(
    rig: _Rig, change: str
) -> None:
    """P2-2: pending is derived at the pinned belief instant, not current belief."""
    # three newer ready lineages: page one (a batch of k + 1) never reaches
    # the converting lineage, so it is examined only after the change
    for hours in (1, 2, 3):
        leading = _lineage(rig, label=f"pending-leading-{hours}", bodies=(("x",),))
        _ingested(rig, leading.version(0), _NOW - timedelta(hours=hours))
    converting = _pending_pair(rig, label="pending-late", body="y", vector=_FAR)
    for edition in (0, 1):
        _ingested(rig, converting.version(edition), _NOW - timedelta(days=5 - edition))

    def change_belief() -> None:
        if change == "correction":
            # the converting edition is no longer in force now: nothing pending
            _declare(rig, converting, 1, (_FUTURE, None))
        else:
            rig.periods.clear_effective_time(
                deployment_id=_DEPLOYMENT_ID, doc_id=converting.doc_id
            )
            _declare(rig, converting, 0, (_PAST, None))

    assert converting.doc_id in _walk_pending(rig, between=change_belief)
    fresh = _search(rig, k=50)
    assert (
        fresh.scope_pending is None
        or converting.doc_id not in fresh.scope_pending.doc_ids
    )


# --- implementation review round 3 (P1): entity-scoped pending probe ---------


def test_an_entity_scoped_context_names_a_lineage_reached_only_on_an_older_edition(
    rig: _Rig,
) -> None:
    """Three editions: the entity is mentioned only in an older, readable,
    non-served edition; another edition is served; the in-force edition is
    still converting. The entity-scoped nomination (mentions across versions,
    coverage first) reaches the older edition, so the empty answer must say
    the lineage is pending."""
    lineage = _lineage(
        rig,
        label="contract",
        bodies=(("Acme renewal terms",), ("renewal terms",), ("renewal terms v3",)),
    )
    _declare(rig, lineage, 0, (_PAST, _REVISED))
    _declare(rig, lineage, 1, (_REVISED, _NOW - timedelta(days=1)))
    _declare(rig, lineage, 2, (_NOW - timedelta(days=1), None))
    claim_id = _claim(
        rig,
        lineage=lineage,
        origin=lineage.chunk(0),
        body="Acme renewal terms",
        occurrences={lineage.chunk(0): (0, 18)},
    )
    entity = uuid4()
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id, canonical_name,"
                " normalized_name) VALUES (:e, :d, 'Acme', 'acme')"
            ),
            {"e": entity, "d": _DEPLOYMENT_ID},
        )
        seed_entity_mention(
            connection=connection,
            deployment_id=_DEPLOYMENT_ID,
            entity_id=entity,
            doc_id=lineage.doc_id,
            chunk_id=lineage.chunk(0),
            claim_id=claim_id,
            surface_form="Acme",
            at=_PAST,
            resolver_version="d140-test",
        )
        # the middle edition is served; the in-force one is still converting
        connection.execute(
            text("UPDATE documents SET current_version_id = :v WHERE doc_id = :doc"),
            {"v": lineage.version(1), "doc": lineage.doc_id},
        )
        connection.execute(
            text(
                "UPDATE document_versions SET status = 'converting'"
                " WHERE version_id = :v"
            ),
            {"v": lineage.version(2)},
        )
    answer = rig.query.claims_and_sources_context(
        deployment_id=_DEPLOYMENT_ID, query="renewal terms", entity_ids=(entity,), k=5
    )
    assert answer.chunks == () and answer.evidence == ()
    pending = answer.freshness.scope_pending
    assert pending is not None and pending.doc_ids == (lineage.doc_id,)
