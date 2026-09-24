"""``search_documents`` against real PostgreSQL (D134 §3).

Documents are ingested through the real E0 ingestor, so their metadata and
observed names are the rows production writes; converter metadata is merged
through the real merge; content is seeded straight into ``chunks`` and
``chunk_search``, the rows the E1 chain would publish.
"""

from __future__ import annotations

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

from remember.models import DocumentSearchFilters
from remember.models import DocumentSearchPage
from remember.models import DocumentSearchRequest
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentUpload
from rememberstack.model import IngestedVersion
from rememberstack.model.document_metadata import DocumentMetadata
from rememberstack.model.document_metadata import DocumentPerson
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import DocumentCatalog
from rememberstack.spine import ForgetCatalog
from rememberstack.spine.document_metadata import merge_converter_metadata_on
from rememberstack.spine.document_search import DocumentSearch
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import UploadIngestor
from tests.database_reset import reset_database

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("61000000-0000-0000-0000-0000000d0134")
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head over the integration database."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for search proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


class _Rig:
    """Ingest, merge metadata, seed content, and search one deployment."""

    def __init__(self, *, engine: Engine, root: Path) -> None:
        self.engine = engine
        self.ingestor = UploadIngestor(
            catalog=DocumentCatalog(engine=engine),
            raw_store=LocalFSObjectStore(root=root / "raw"),
            admission=ForgetCatalog(engine=engine),
            routable_mimes=frozenset({"text/markdown", _XLSX, "application/pdf"}),
        )
        self.search_port = DocumentSearch(engine=engine)

    def ingest(
        self,
        *,
        filename: str,
        content: str,
        title: str | None = None,
        mime: str = "text/markdown",
        source_ref: str | None = None,
    ) -> IngestedVersion:
        upload = DocumentUpload(
            filename=filename, mime=mime, content=content.encode(), title=title
        )
        if source_ref is None:
            return self.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=upload)
        return self.ingestor.ingest_observed(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="drive",
            source_ref=source_ref,
            upload=upload,
            versioning_mode="snapshot",
            source_modified_at=None,
            source_version_ref=None,
            sync_cycle_id=None,
        )

    def merge(self, *, version_id: UUID, metadata: DocumentMetadata) -> None:
        with self.engine.begin() as connection:
            merge_converter_metadata_on(
                connection=connection,
                deployment_id=_DEPLOYMENT_ID,
                version_id=version_id,
                metadata=metadata,
                mapping_version="test@1",
            )

    def add_content(self, *, ingested: IngestedVersion, body: str) -> None:
        """Seed the version's live reading with one searchable chunk."""
        representation_id, chunk_id = uuid4(), uuid4()
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO document_representations (representation_id,"
                    " deployment_id, version_id, route, markdown_uri, pageindex_uri,"
                    " conversion_uri, blocks_uri, meta_uri, status) VALUES"
                    " (:r, :d, :v, 'passthrough', 'm', 'p', 'c', 'b', 'meta', 'ready')"
                ),
                {"r": representation_id, "d": _DEPLOYMENT_ID, "v": ingested.version_id},
            )
            connection.execute(
                text(
                    "UPDATE document_versions SET current_representation_id = :r"
                    " WHERE version_id = :v"
                ),
                {"r": representation_id, "v": ingested.version_id},
            )
            connection.execute(
                text(
                    "INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,"
                    " representation_id, ordinal, block_start, block_end,"
                    " chunk_content_hash, extraction_input_hash, char_start, char_end,"
                    " context_prefix, created_at) VALUES (:c, :d, :doc, :v, :r, 0, 0,"
                    " 0, :hash, :hash, 0, 10, '', now())"
                ),
                {
                    "c": chunk_id,
                    "d": _DEPLOYMENT_ID,
                    "doc": ingested.doc_id,
                    "v": ingested.version_id,
                    "r": representation_id,
                    "hash": f"hash-{chunk_id}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO chunk_search (deployment_id, chunk_id, search_text)"
                    " VALUES (:d, :c, :body)"
                ),
                {"d": _DEPLOYMENT_ID, "c": chunk_id, "body": body},
            )

    def search(
        self,
        query: str | None = None,
        *,
        versions: str = "current",
        k: int = 20,
        cursor: str | None = None,
        **filters: object,
    ) -> DocumentSearchPage:
        return self.search_port.search_documents(
            deployment_id=_DEPLOYMENT_ID,
            request=DocumentSearchRequest.model_validate(
                {
                    "query": query,
                    "filters": DocumentSearchFilters.model_validate(filters),
                    "versions": versions,
                    "k": k,
                    "cursor": cursor,
                }
            ),
        )


@pytest.fixture()
def rig(database_engine: Engine, tmp_path: Path) -> _Rig:
    """A fresh deployment and ingest/search pair per proof."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="d134-search-test",
            name="D134 search proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    return _Rig(engine=database_engine, root=tmp_path)


def _doc_ids(page: DocumentSearchPage) -> list[UUID]:
    return [result.doc_id for result in page.documents]


def test_names_match_exactly_partially_and_misspelled(rig: _Rig) -> None:
    sales = rig.ingest(filename="Q3_sales_2025.xlsx", content="sheet", mime=_XLSX)
    report = rig.ingest(
        filename="quarterly_report.md", content="# Report\n", title="Quarterly report"
    )
    rig.ingest(filename="holiday_rota.md", content="# Rota\n")

    for query in ("Q3_sales_2025.xlsx", "Q3 sales", "sales 2025"):
        page = rig.search(query)
        assert _doc_ids(page)[0] == sales.doc_id, query
        assert page.documents[0].matched_by == ("name",)
        assert page.cursor is None
    misspelled = rig.search("quartely reprot")
    assert _doc_ids(misspelled)[0] == report.doc_id
    first = misspelled.documents[0]
    assert first.file_name == "quarterly_report.md"
    assert first.title == "Quarterly report"
    assert first.family == "markdown"
    assert first.status == "converting"
    assert rig.search("zzzz qqqq").documents == ()


def test_a_renamed_file_is_found_by_its_new_and_old_names(rig: _Rig) -> None:
    draft = rig.ingest(filename="budget_draft.md", content="# Budget\n")
    renamed = rig.ingest(filename="final_budget_2026.md", content="# Budget\n")
    assert renamed.version_id == draft.version_id

    for query in ("budget_draft", "final_budget_2026"):
        page = rig.search(query)
        assert _doc_ids(page) == [draft.doc_id], query
    # the version's own metadata keeps the name it was ingested under
    assert rig.search("final_budget_2026").documents[0].file_name == "budget_draft.md"


def test_content_matches_by_the_best_chunk(rig: _Rig) -> None:
    notes = rig.ingest(filename="notes.md", content="# Notes\n")
    rig.add_content(ingested=notes, body="the zanzibar partnership renewal terms")
    rig.ingest(filename="other.md", content="# Other\n")

    page = rig.search("zanzibar partnership")
    assert _doc_ids(page) == [notes.doc_id]
    assert page.documents[0].matched_by == ("content",)
    assert page.documents[0].score is not None


def test_filters_only_order_newest_first_and_cursor_pins_as_of(rig: _Rig) -> None:
    dated = {}
    for name, created in (
        ("march.md", datetime(2025, 3, 1, tzinfo=UTC)),
        ("january.md", datetime(2025, 1, 1, tzinfo=UTC)),
        ("undated.md", None),
    ):
        ingested = rig.ingest(filename=name, content=f"# {name}\n")
        if created is not None:
            rig.merge(
                version_id=ingested.version_id,
                metadata=DocumentMetadata(created_at=created),
            )
        dated[name] = ingested.doc_id

    first = rig.search(k=2)
    assert _doc_ids(first) == [dated["march.md"], dated["january.md"]]
    assert first.cursor is not None

    # a document arriving between pages is not considered by this search
    newest = rig.ingest(filename="newest.md", content="# newest\n")
    rig.merge(
        version_id=newest.version_id,
        metadata=DocumentMetadata(created_at=datetime(2026, 1, 1, tzinfo=UTC)),
    )
    second = rig.search(k=2, cursor=first.cursor)
    assert _doc_ids(second) == [dated["undated.md"]]
    assert second.cursor is None
    assert second.as_of == first.as_of
    assert _doc_ids(rig.search(k=1))[0] == newest.doc_id

    # a date range excludes documents that declare no date
    ranged = rig.search(created_from=datetime(2025, 2, 1, tzinfo=UTC))
    assert set(_doc_ids(ranged)) == {dated["march.md"], newest.doc_id}


def test_people_filters_match_names_and_addresses_and_disclose_people(
    rig: _Rig,
) -> None:
    novak = rig.ingest(filename="audit.md", content="# Audit\n")
    rig.merge(
        version_id=novak.version_id,
        metadata=DocumentMetadata(
            authors=(DocumentPerson(name="Alice Novák", address="alice@acme.com"),),
            recipients=(DocumentPerson(name="Bob Stone"),),
        ),
    )
    chen = rig.ingest(filename="plan.md", content="# Plan\n")
    rig.merge(
        version_id=chen.version_id,
        metadata=DocumentMetadata(
            authors=(DocumentPerson(name="Alice Chen", address="achen@x.io"),)
        ),
    )
    rig.ingest(filename="unrelated.md", content="# Unrelated\n")

    ambiguous = rig.search(authors=["alice"])
    assert set(_doc_ids(ambiguous)) == {novak.doc_id, chen.doc_id}
    assert {
        (person.name, person.address, person.documents)
        for person in ambiguous.people_matched
    } == {("Alice Novák", "alice@acme.com", 1), ("Alice Chen", "achen@x.io", 1)}
    novak_result = next(r for r in ambiguous.documents if r.doc_id == novak.doc_id)
    assert novak_result.authors[0].name == "Alice Novák"
    assert novak_result.recipients[0].name == "Bob Stone"

    narrowed = rig.search(authors=["ACHEN@x.io"])
    assert _doc_ids(narrowed) == [chen.doc_id]
    assert _doc_ids(rig.search(authors=["novak"])) == [novak.doc_id]
    assert _doc_ids(rig.search(recipients=["bob stone"])) == [novak.doc_id]
    assert rig.search(authors=["ali"]).documents == ()
    assert rig.search(family=["markdown"]).people_matched == ()


def test_versions_all_matches_any_live_version(rig: _Rig) -> None:
    first = rig.ingest(filename="spec.md", content="# v1\n", source_ref="spec")
    second = rig.ingest(filename="spec.md", content="# v2\n", source_ref="spec")
    assert first.doc_id == second.doc_id and first.version_id != second.version_id
    rig.merge(
        version_id=first.version_id,
        metadata=DocumentMetadata(authors=(DocumentPerson(name="Alice Novak"),)),
    )
    rig.merge(
        version_id=second.version_id,
        metadata=DocumentMetadata(authors=(DocumentPerson(name="Bob Stone"),)),
    )

    # judged by the newest version while nothing is current yet
    assert rig.search(authors=["alice"]).documents == ()
    old = rig.search(authors=["alice"], versions="all").documents
    assert [(r.doc_id, r.version_id) for r in old] == [(first.doc_id, first.version_id)]
    assert old[0].authors[0].name == "Alice Novak"

    both = rig.search("spec", versions="all").documents
    assert [r.version_id for r in both] == [second.version_id]
    assert both[0].other_matching_version_ids == (first.version_id,)

    # once the first version is current, the default judges by it
    with rig.engine.begin() as connection:
        connection.execute(
            text("UPDATE documents SET current_version_id = :v WHERE doc_id = :d"),
            {"v": first.version_id, "d": first.doc_id},
        )
    assert _doc_ids(rig.search(authors=["alice"])) == [first.doc_id]
    assert rig.search(authors=["bob"]).documents == ()


def test_deleted_and_forgotten_documents_never_match(rig: _Rig) -> None:
    kept = rig.ingest(filename="kept_minutes.md", content="# Kept\n")
    deleted = rig.ingest(filename="deleted_minutes.md", content="# Deleted\n")
    forgotten = rig.ingest(filename="forgotten_minutes.md", content="# Forgotten\n")
    version_gone = rig.ingest(filename="version_gone_minutes.md", content="# Gone\n")
    with rig.engine.begin() as connection:
        connection.execute(
            text("UPDATE documents SET deleted_at = now() WHERE doc_id = :d"),
            {"d": deleted.doc_id},
        )
        connection.execute(
            text(
                "UPDATE document_versions SET deleted_at = now() WHERE version_id = :v"
            ),
            {"v": version_gone.version_id},
        )
        # what hard forget leaves: a tombstoned lineage without metadata rows
        connection.execute(
            text("UPDATE documents SET deleted_at = now() WHERE doc_id = :d"),
            {"d": forgotten.doc_id},
        )
        connection.execute(
            text("DELETE FROM document_metadata WHERE doc_id = :d"),
            {"d": forgotten.doc_id},
        )

    for versions in ("current", "all"):
        assert _doc_ids(rig.search("minutes", versions=versions)) == [kept.doc_id]
        assert _doc_ids(rig.search(versions=versions)) == [kept.doc_id]


def test_family_language_and_doc_id_filters(rig: _Rig) -> None:
    sheet = rig.ingest(filename="costs.xlsx", content="sheet", mime=_XLSX)
    notes = rig.ingest(filename="costs.md", content="# Costs\n")
    rig.merge(version_id=notes.version_id, metadata=DocumentMetadata(language="cs"))

    assert _doc_ids(rig.search("costs", family=["office"])) == [sheet.doc_id]
    assert _doc_ids(rig.search(language="cs")) == [notes.doc_id]
    assert _doc_ids(rig.search("costs", doc_ids=[str(notes.doc_id)])) == [notes.doc_id]
    with pytest.raises(ValueError, match="cursor is malformed"):
        rig.search(cursor="not-a-cursor")
