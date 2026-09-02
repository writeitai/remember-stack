"""The inventory a customer sees: which documents, and where each one got to.

The counts on the dashboard come from metering, which is content-free by
design. These rows are the other half — the names, the times, the per-document
processing state — and they can only come from the deployment.

Every test here is really about one hazard. The lineage's *current* version is
the one that finished and is being served; the *newest* version is whatever
arrived last. Those differ exactly when something is unfinished or broken,
which is exactly when somebody opens this screen.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from datetime import timedelta
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

from rememberstack.model import DeploymentBootstrapInput
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.document_inventory import DocumentInventory
from rememberstack.spine.settings import load_database_settings

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("b9a1f0c2-3d4e-4f5a-8b6c-7d8e9f0a1b2c")
_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head against the isolated PostgreSQL test spine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for inventory proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    DeploymentBootstrapper(engine=engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="inventory",
            name="Inventory",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _clean(database_engine: Engine) -> Iterator[None]:
    """Each test owns the corpus, so ordering assertions mean something."""
    yield
    with database_engine.begin() as connection:
        # Scoped to this module's deployment. An unscoped DELETE would empty
        # the corpus of every other test sharing this database, and the damage
        # would show up as an unrelated module failing later in the run.
        #
        # The lineage's current pointer is a foreign key into the versions
        # table, so it has to be dropped before the rows it points at.
        scope = {"deployment": _DEPLOYMENT_ID}
        connection.execute(
            text(
                "UPDATE documents SET current_version_id = NULL"
                " WHERE deployment_id = :deployment"
            ),
            scope,
        )
        for table in ("document_versions", "documents", "content_objects"):
            connection.execute(
                text(f"DELETE FROM {table} WHERE deployment_id = :deployment"), scope
            )


def _document(
    *,
    connection: Connection,
    title: str,
    deleted: bool = False,
    source_uri: str | None = None,
) -> UUID:
    """A lineage with no versions yet."""
    doc_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO documents (doc_id, deployment_id, source_kind,"
            " source_ref, source_uri, title, deleted_at) VALUES (:doc,"
            " :deployment, 'upload', :ref, :uri, :title, :deleted)"
        ),
        {
            "doc": doc_id,
            "deployment": _DEPLOYMENT_ID,
            "ref": f"{title}-{doc_id}",
            "uri": source_uri,
            "title": title,
            "deleted": _NOW if deleted else None,
        },
    )
    return doc_id


def _version(
    *,
    connection: Connection,
    doc_id: UUID,
    version_no: int,
    status: str,
    at: datetime,
    error: str | None = None,
    current: bool = False,
) -> UUID:
    """One observed snapshot, at an explicit status and time."""
    version_id = uuid4()
    content_hash = f"hash-{version_id}"
    connection.execute(
        text(
            "INSERT INTO content_objects (deployment_id, content_hash, mime,"
            " raw_uri) VALUES (:deployment, :hash, 'text/markdown', :uri)"
        ),
        {
            "deployment": _DEPLOYMENT_ID,
            "hash": content_hash,
            "uri": f"mem://raw/{content_hash}",
        },
    )
    connection.execute(
        text(
            "INSERT INTO document_versions (version_id, deployment_id, doc_id,"
            " content_hash, version_no, status, ingested_at, error) VALUES"
            " (:version, :deployment, :doc, :hash, :no, :status, :at, :error)"
        ),
        {
            "version": version_id,
            "deployment": _DEPLOYMENT_ID,
            "doc": doc_id,
            "hash": content_hash,
            "no": version_no,
            "status": status,
            "at": at,
            "error": error,
        },
    )
    if current:
        connection.execute(
            text(
                "UPDATE documents SET current_version_id = :version WHERE doc_id = :doc"
            ),
            {"version": version_id, "doc": doc_id},
        )
    return version_id


def test_a_document_still_processing_is_listed(database_engine: Engine) -> None:
    """The whole point. It has no current version, and it must still appear.

    Somebody uploads a file and refreshes the page to see whether it arrived.
    At that moment the lineage's `current_version_id` is NULL, because nothing
    has finished. A listing keyed on the current pointer would show them an
    empty screen — the single worst answer, because it is indistinguishable
    from the upload having been lost.
    """
    with database_engine.begin() as connection:
        doc_id = _document(connection=connection, title="just-uploaded.pdf")
        _version(
            connection=connection,
            doc_id=doc_id,
            version_no=1,
            status="converting",
            at=_NOW,
        )

    page = DocumentInventory(engine=database_engine).list_documents(
        deployment_id=_DEPLOYMENT_ID
    )

    assert [d.title for d in page.documents] == ["just-uploaded.pdf"]
    assert page.documents[0].latest.status == "converting"
    # Listed, but honestly: there is nothing ready to answer questions from.
    assert page.documents[0].serving is False


def test_a_failed_newest_version_does_not_hide_the_working_older_one(
    database_engine: Engine,
) -> None:
    """Two different facts, and a customer needs both.

    Re-uploading a document that then fails to convert leaves the previous
    version serving. Reporting only "failed" would suggest the document is
    gone; reporting only "ready" would hide that the new upload did not take.
    """
    with database_engine.begin() as connection:
        doc_id = _document(connection=connection, title="contract.pdf")
        _version(
            connection=connection,
            doc_id=doc_id,
            version_no=1,
            status="ready",
            at=_NOW - timedelta(days=2),
            current=True,
        )
        _version(
            connection=connection,
            doc_id=doc_id,
            version_no=2,
            status="failed",
            at=_NOW,
            error="conversion timed out",
        )

    page = DocumentInventory(engine=database_engine).list_documents(
        deployment_id=_DEPLOYMENT_ID
    )

    only = page.documents[0]
    assert only.latest.version_no == 2
    assert only.latest.status == "failed"
    assert only.latest.error == "conversion timed out"
    assert only.serving is True


def test_the_error_is_passed_through_rather_than_summarised(
    database_engine: Engine,
) -> None:
    """The engine's own message is the one somebody can act on or quote."""
    with database_engine.begin() as connection:
        doc_id = _document(connection=connection, title="scan.tiff")
        _version(
            connection=connection,
            doc_id=doc_id,
            version_no=1,
            status="failed",
            at=_NOW,
            error="unsupported media type: image/tiff",
        )

    page = DocumentInventory(engine=database_engine).list_documents(
        deployment_id=_DEPLOYMENT_ID
    )

    assert page.documents[0].latest.error == "unsupported media type: image/tiff"


def test_a_forgotten_lineage_is_not_listed(database_engine: Engine) -> None:
    """A deletion the customer asked for must not come back as a row.

    The tombstone stays in the table so an auditor can tell "forgotten" from
    "never existed", but the customer's own inventory is what they still have.
    """
    with database_engine.begin() as connection:
        kept = _document(connection=connection, title="kept.md")
        _version(
            connection=connection, doc_id=kept, version_no=1, status="ready", at=_NOW
        )
        forgotten = _document(connection=connection, title="forgotten.md", deleted=True)
        _version(
            connection=connection,
            doc_id=forgotten,
            version_no=1,
            status="ready",
            at=_NOW,
        )

    page = DocumentInventory(engine=database_engine).list_documents(
        deployment_id=_DEPLOYMENT_ID
    )

    assert [d.title for d in page.documents] == ["kept.md"]


def test_the_newest_activity_comes_first(database_engine: Engine) -> None:
    """Ordered by when the newest version landed, not when the lineage began.

    A document first seen a year ago and re-ingested this morning is this
    morning's news.
    """
    with database_engine.begin() as connection:
        old = _document(connection=connection, title="old.md")
        _version(
            connection=connection,
            doc_id=old,
            version_no=1,
            status="ready",
            at=_NOW - timedelta(days=365),
        )
        revived = _document(connection=connection, title="revived.md")
        _version(
            connection=connection,
            doc_id=revived,
            version_no=1,
            status="ready",
            at=_NOW - timedelta(days=400),
        )
        _version(
            connection=connection, doc_id=revived, version_no=2, status="ready", at=_NOW
        )

    page = DocumentInventory(engine=database_engine).list_documents(
        deployment_id=_DEPLOYMENT_ID
    )

    assert [d.title for d in page.documents] == ["revived.md", "old.md"]


def test_paging_returns_every_document_exactly_once(database_engine: Engine) -> None:
    """A keyset cursor, so a page boundary cannot drop or repeat a document.

    All five share one `ingested_at` on purpose: that is the case an ordering
    with no tie-break gets wrong, and the case a real bulk import produces.
    """
    with database_engine.begin() as connection:
        for index in range(5):
            doc_id = _document(connection=connection, title=f"bulk-{index}.md")
            _version(
                connection=connection,
                doc_id=doc_id,
                version_no=1,
                status="ready",
                at=_NOW,
            )

    inventory = DocumentInventory(engine=database_engine)
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        page = inventory.list_documents(
            deployment_id=_DEPLOYMENT_ID, limit=2, cursor=cursor
        )
        seen.extend(d.title or "" for d in page.documents)
        cursor = page.cursor
        if cursor is None:
            break

    assert cursor is None, "paging did not terminate"
    assert sorted(seen) == [f"bulk-{index}.md" for index in range(5)]
    assert len(seen) == len(set(seen)), "a document was returned twice"


def test_the_last_page_carries_no_cursor(database_engine: Engine) -> None:
    """An exactly-full page must not promise another one.

    Off by one here shows the reader an empty final page, which reads as the
    corpus ending sooner than it does.
    """
    with database_engine.begin() as connection:
        for index in range(2):
            doc_id = _document(connection=connection, title=f"exact-{index}.md")
            _version(
                connection=connection,
                doc_id=doc_id,
                version_no=1,
                status="ready",
                at=_NOW - timedelta(minutes=index),
            )

    page = DocumentInventory(engine=database_engine).list_documents(
        deployment_id=_DEPLOYMENT_ID, limit=2
    )

    assert len(page.documents) == 2
    assert page.cursor is None


def test_the_status_filter_asks_about_the_newest_version(
    database_engine: Engine,
) -> None:
    """ "Show me what failed" has to mean the newest state, not any state."""
    with database_engine.begin() as connection:
        recovered = _document(connection=connection, title="recovered.md")
        _version(
            connection=connection,
            doc_id=recovered,
            version_no=1,
            status="failed",
            at=_NOW - timedelta(days=1),
            error="transient",
        )
        _version(
            connection=connection,
            doc_id=recovered,
            version_no=2,
            status="ready",
            at=_NOW,
        )
        broken = _document(connection=connection, title="broken.md")
        _version(
            connection=connection,
            doc_id=broken,
            version_no=1,
            status="failed",
            at=_NOW,
            error="still broken",
        )

    page = DocumentInventory(engine=database_engine).list_documents(
        deployment_id=_DEPLOYMENT_ID, status="failed"
    )

    assert [d.title for d in page.documents] == ["broken.md"]


def test_another_deployments_documents_are_not_visible(database_engine: Engine) -> None:
    """Tenancy is the floor. A listing must never cross a deployment."""
    other = UUID("c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f")
    with database_engine.begin() as connection:
        mine = _document(connection=connection, title="mine.md")
        _version(
            connection=connection, doc_id=mine, version_no=1, status="ready", at=_NOW
        )

    page = DocumentInventory(engine=database_engine).list_documents(deployment_id=other)

    assert page.documents == ()


def test_a_cursor_we_did_not_issue_is_refused(database_engine: Engine) -> None:
    """Not treated as "start again".

    Silently restarting would serve page one to somebody who asked for page
    three, and the repeated documents would look like duplicates in the corpus
    rather than like the bug it is.
    """
    with pytest.raises(ValueError, match="cursor"):
        DocumentInventory(engine=database_engine).list_documents(
            deployment_id=_DEPLOYMENT_ID, cursor="not-a-real-cursor"
        )


def test_an_oversized_page_request_is_capped_not_refused(
    database_engine: Engine,
) -> None:
    """A caller asking for the world gets a page, not an error."""
    with database_engine.begin() as connection:
        doc_id = _document(connection=connection, title="one.md")
        _version(
            connection=connection, doc_id=doc_id, version_no=1, status="ready", at=_NOW
        )

    page = DocumentInventory(engine=database_engine).list_documents(
        deployment_id=_DEPLOYMENT_ID, limit=10_000
    )

    assert len(page.documents) == 1
