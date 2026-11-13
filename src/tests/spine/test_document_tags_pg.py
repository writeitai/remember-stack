"""D156 document tags against real PostgreSQL: registry, strictness, assignments.

Documents are ingested through the real E0 ingestor and deleted through the
real D135 deleter, so the rows tags attach to are the rows production writes.
The D56 proof (tag writes cost no model call) lives with the other reuse
proofs in ``tests/workers/test_reuse_lifecycle.py``; hard forget's removal of
assignments is proved in ``test_forget_catalog.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
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
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from remember.models import DocumentSearchRequest
from remember.models import DocumentTagsUpdate
from remember.models import TagCreate
from remember.models import TAGS_PER_DEPLOYMENT_MAX
from remember.models import TAGS_PER_DOCUMENT_MAX
from remember.models import TagUpdate
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import DocumentUpload
from rememberstack.model import IngestedVersion
from rememberstack.model import TagConflictError
from rememberstack.model import TagLimitError
from rememberstack.model import UnknownTagError
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import DocumentCatalog
from rememberstack.spine import ForgetCatalog
from rememberstack.spine.document_inventory import DocumentInventory
from rememberstack.spine.document_search import DocumentSearch
from rememberstack.spine.settings import load_database_settings
from rememberstack.spine.tags import TagCatalog
from rememberstack.workers import DocumentDeleter
from rememberstack.workers import UploadIngestor
from tests.database_reset import reset_database

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("61000000-0000-0000-0000-0000000d0156")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head over the integration database."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for tag proofs")
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
    """One fresh deployment: ingest, delete, tag and read documents."""

    def __init__(self, *, engine: Engine, root: Path) -> None:
        self.engine = engine
        self.raw_root = root / "raw"
        self.ingestor = UploadIngestor(
            catalog=DocumentCatalog(engine=engine),
            raw_store=LocalFSObjectStore(root=self.raw_root),
            admission=ForgetCatalog(engine=engine),
            routable_mimes=frozenset({"text/markdown"}),
        )
        self.tags = TagCatalog(engine=engine)

    def create(self, name: str, description: str = "") -> UUID:
        return self.tags.create_tag(
            deployment_id=_DEPLOYMENT_ID,
            request=TagCreate(name=name, description=description or name),
        ).tag_id

    def upload(
        self, *, body: str, tags: tuple[str, ...] = (), filename: str = "note.md"
    ) -> IngestedVersion:
        return self.ingestor.ingest(
            deployment_id=_DEPLOYMENT_ID,
            upload=DocumentUpload(
                filename=filename,
                mime="text/markdown",
                content=body.encode(),
                tags=tags,
            ),
        )

    def observe(
        self,
        *,
        body: str,
        tags: tuple[str, ...] = (),
        version_key: str | None = None,
        source_ref: str = "docs/plan.md",
    ) -> IngestedVersion:
        return self.ingestor.ingest_observed(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="drive",
            source_ref=source_ref,
            upload=DocumentUpload(
                filename="plan.md",
                mime="text/markdown",
                content=body.encode(),
                version_key=version_key,
                tags=tags,
            ),
            versioning_mode="snapshot",
            source_modified_at=None,
            source_version_ref=None,
            sync_cycle_id=None,
        )

    def update(self, doc_id: UUID, **change: object) -> tuple[str, ...]:
        return self.tags.update_document_tags(
            deployment_id=_DEPLOYMENT_ID,
            doc_id=doc_id,
            request=DocumentTagsUpdate.model_validate(change),
        ).tags

    def document_tags(self, doc_id: UUID) -> tuple[str, ...]:
        """The tags ``GET /documents`` reports for a live document."""
        page = DocumentInventory(engine=self.engine).list_documents(
            deployment_id=_DEPLOYMENT_ID, limit=200
        )
        return next(item.tags for item in page.documents if item.doc_id == doc_id)

    def delete(self, doc_id: UUID) -> None:
        DocumentDeleter(
            engine=self.engine, profile_refresher=MagicMock()
        ).delete_document(deployment_id=_DEPLOYMENT_ID, doc_id=doc_id)

    def count(self, sql: str, **parameters: object) -> int:
        with self.engine.connect() as connection:
            return connection.execute(text(sql), parameters).scalar_one()


@pytest.fixture()
def rig(database_engine: Engine, tmp_path: Path) -> _Rig:
    """A fresh deployment with an empty registry per proof."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments, tags CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="tags-test",
            name="Document tags proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    return _Rig(engine=database_engine, root=tmp_path)


# -- registry ----------------------------------------------------------------


def test_registry_keeps_declared_spelling_and_refuses_case_duplicates(
    rig: _Rig,
) -> None:
    """Names are unique ignoring case; listing is by name with no counts."""
    rig.create("Project:Autoboard", "Documents about the Autoboard product.")
    rig.create("kind:decision", "An accepted decision record.")
    with pytest.raises(TagConflictError) as refused:
        rig.create("project:AUTOBOARD")
    assert refused.value.code == "conflict"

    listed = rig.tags.list_tags(deployment_id=_DEPLOYMENT_ID).tags
    assert [tag.name for tag in listed] == ["kind:decision", "Project:Autoboard"]
    assert set(listed[0].model_dump()) == {
        "tag_id",
        "name",
        "description",
        "created_at",
        "updated_at",
    }
    by_name = rig.tags.list_tags(deployment_id=_DEPLOYMENT_ID, query="AUTOBOARD")
    by_description = rig.tags.list_tags(deployment_id=_DEPLOYMENT_ID, query="record")
    assert [tag.name for tag in by_name.tags] == ["Project:Autoboard"]
    assert [tag.name for tag in by_description.tags] == ["kind:decision"]


def test_names_are_nfc_and_refuse_whitespace_controls_and_bad_lengths() -> None:
    """§3.1 names: NFC, 1-128 characters, no whitespace or control characters."""
    decomposed = "café:menu"
    assert TagCreate(name=decomposed, description="").name == "café:menu"
    for name in ("", "two words", "tab\there", "line\nbreak", "bell\x07", "x" * 129):
        with pytest.raises(ValidationError):
            TagCreate(name=name, description="")
    assert TagCreate(name="x" * 128, description="").name == "x" * 128
    with pytest.raises(ValidationError):
        TagCreate(name="ok", description="d" * 2001)
    with pytest.raises(ValidationError):
        TagUpdate()


def test_nfc_spellings_are_one_name(rig: _Rig) -> None:
    """A decomposed spelling resolves to, and conflicts with, the composed name."""
    rig.create("café")
    with pytest.raises(TagConflictError):
        rig.create("CAFÉ")
    doc = rig.upload(body="Menu.", tags=("café",))
    assert rig.document_tags(doc.doc_id) == ("café",)


def test_the_database_refuses_case_duplicates_and_long_descriptions(rig: _Rig) -> None:
    """The unique index and checks make the rules database rules too."""
    rig.create("project:a")
    with pytest.raises(IntegrityError), rig.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tags (deployment_id, tag_id, name, description)"
                " VALUES (:d, :t, 'PROJECT:A', '')"
            ),
            {"d": _DEPLOYMENT_ID, "t": uuid4()},
        )
    with pytest.raises(IntegrityError), rig.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tags (deployment_id, tag_id, name, description)"
                " VALUES (:d, :t, 'project:b', repeat('x', 2001))"
            ),
            {"d": _DEPLOYMENT_ID, "t": uuid4()},
        )


def test_rename_keeps_assignments_and_the_old_name_stops_resolving(rig: _Rig) -> None:
    """A rename is one row; there are no aliases."""
    tag_id = rig.create("project:autoboard")
    doc = rig.upload(body="Board.", tags=("project:autoboard",))

    renamed = rig.tags.update_tag(
        deployment_id=_DEPLOYMENT_ID,
        tag="PROJECT:AUTOBOARD",
        request=TagUpdate(name="project:board"),
    )
    assert (renamed.tag_id, renamed.name) == (tag_id, "project:board")
    assert renamed.description == "project:autoboard"
    assert renamed.updated_at >= renamed.created_at
    assert rig.document_tags(doc.doc_id) == ("project:board",)
    with pytest.raises(UnknownTagError) as unknown:
        rig.update(doc.doc_id, add=["project:autoboard"])
    assert unknown.value.tags == ("project:autoboard",)

    described = rig.tags.update_tag(
        deployment_id=_DEPLOYMENT_ID,
        tag=str(tag_id),
        request=TagUpdate(description="The board."),
    )
    assert (described.name, described.description) == ("project:board", "The board.")
    rig.create("kind:x")
    with pytest.raises(TagConflictError):
        rig.tags.update_tag(
            deployment_id=_DEPLOYMENT_ID,
            tag="project:board",
            request=TagUpdate(name="KIND:X"),
        )
    with pytest.raises(UnknownTagError) as missing:
        rig.tags.update_tag(
            deployment_id=_DEPLOYMENT_ID,
            tag=str(uuid4()),
            request=TagUpdate(description="nobody"),
        )
    assert missing.value.status_code == 404


def test_delete_removes_the_tag_and_every_assignment(rig: _Rig) -> None:
    """One write removes the tag and its assignments; other tags stay."""
    rig.create("project:a")
    rig.create("project:b")
    first = rig.upload(body="One.", tags=("project:a", "project:b"))
    second = rig.upload(body="Two.", tags=("project:a",))

    deleted = rig.tags.delete_tag(deployment_id=_DEPLOYMENT_ID, tag="project:a")

    assert deleted.name == "project:a"
    assert rig.document_tags(first.doc_id) == ("project:b",)
    assert rig.document_tags(second.doc_id) == ()
    assert rig.count("SELECT count(*) FROM document_tags") == 1
    with pytest.raises(UnknownTagError):
        rig.tags.delete_tag(deployment_id=_DEPLOYMENT_ID, tag="project:a")


def test_deployment_tag_limit_is_enforced_on_create(rig: _Rig) -> None:
    """10,000 tags per deployment; the next registration is refused."""
    with rig.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tags (deployment_id, tag_id, name, description)"
                " SELECT :d, gen_random_uuid(), 'bulk:' || n, ''"
                " FROM generate_series(1, :limit) AS n"
            ),
            {"d": _DEPLOYMENT_ID, "limit": TAGS_PER_DEPLOYMENT_MAX},
        )
    with pytest.raises(TagLimitError):
        rig.create("one:more")


# -- strictness --------------------------------------------------------------


def test_ingest_with_unknown_tags_stores_nothing_and_names_every_one(rig: _Rig) -> None:
    """The whole ingest is refused before bytes or rows are stored."""
    rig.create("project:a")
    with pytest.raises(UnknownTagError) as refused:
        rig.upload(body="Nothing lands.", tags=("project:a", "typo:one", "typo:two"))
    assert refused.value.tags == ("typo:one", "typo:two")
    assert refused.value.code == "unknown_tag"
    assert rig.count("SELECT count(*) FROM documents") == 0
    assert rig.count("SELECT count(*) FROM content_objects") == 0
    assert not rig.raw_root.exists() or not any(rig.raw_root.rglob("*.md"))

    doc = rig.observe(body="Version one.")
    with pytest.raises(UnknownTagError):
        rig.observe(body="Version two.", tags=("typo:one",))
    assert rig.count("SELECT count(*) FROM document_versions") == 1
    with pytest.raises(UnknownTagError) as update_refused:
        rig.update(doc.doc_id, add=["project:a", "typo:three"])
    assert update_refused.value.tags == ("typo:three",)
    assert rig.document_tags(doc.doc_id) == ()


# -- ingest ------------------------------------------------------------------


def test_ingest_adds_tags_and_never_decides_versions(rig: _Rig) -> None:
    """Add-only, by name or id; a no-op stays a no-op; a new key still versions."""
    a_id = rig.create("project:a")
    rig.create("project:b")
    first = rig.observe(body="Edition one.", tags=("project:a",))
    assert first.created

    again = rig.observe(body="Edition one.", tags=("Project:B",))
    assert not again.created
    assert again.version_id == first.version_id
    assert rig.document_tags(first.doc_id) == ("project:a", "project:b")

    untagged = rig.observe(body="Edition one.")
    assert not untagged.created
    assert rig.document_tags(first.doc_id) == ("project:a", "project:b")

    keyed = rig.observe(body="Edition one.", version_key="ed-2", tags=(str(a_id),))
    assert keyed.created
    assert rig.count("SELECT count(*) FROM document_versions") == 2
    assert rig.document_tags(first.doc_id) == ("project:a", "project:b")


def test_two_uploads_of_the_same_bytes_keep_both_callers_tags(rig: _Rig) -> None:
    """One-shot uploads of identical bytes reach one lineage; tags accumulate."""
    rig.create("project:autoboard")
    rig.create("project:legalit")
    first = rig.upload(body="Shared notes.", tags=("project:autoboard",))
    second = rig.upload(
        body="Shared notes.", tags=("project:legalit",), filename="copy.md"
    )
    assert second.doc_id == first.doc_id
    assert not second.created
    assert rig.document_tags(first.doc_id) == ("project:autoboard", "project:legalit")


def test_ingest_refuses_past_the_per_document_limit(rig: _Rig) -> None:
    """64 tags per lineage; an ingest that would pass it stores nothing."""
    names = [f"t:{index:02d}" for index in range(TAGS_PER_DOCUMENT_MAX + 1)]
    for name in names:
        rig.create(name)
    doc = rig.observe(body="Full.", tags=tuple(names[:TAGS_PER_DOCUMENT_MAX]))
    assert len(rig.document_tags(doc.doc_id)) == TAGS_PER_DOCUMENT_MAX
    with pytest.raises(TagLimitError):
        rig.observe(body="Full, edited.", tags=(names[-1],))
    assert rig.count("SELECT count(*) FROM document_versions") == 1
    with pytest.raises(TagLimitError):
        rig.update(doc.doc_id, add=[names[-1]])
    assert len(rig.document_tags(doc.doc_id)) == TAGS_PER_DOCUMENT_MAX


# -- update_document_tags ----------------------------------------------------


def test_update_document_tags_adds_removes_and_sets_atomically(rig: _Rig) -> None:
    """add/remove in one write; set replaces; empty set clears; absent remove ok."""
    a_id = rig.create("project:a")
    rig.create("project:b")
    rig.create("kind:decision")
    doc = rig.observe(body="Decision.")

    assert rig.update(doc.doc_id, add=["project:a", "kind:decision"]) == (
        "kind:decision",
        "project:a",
    )
    assert rig.update(doc.doc_id, add=["project:b"], remove=[str(a_id)]) == (
        "kind:decision",
        "project:b",
    )
    assert rig.update(doc.doc_id, remove=["project:a"]) == (
        "kind:decision",
        "project:b",
    )
    assert rig.update(doc.doc_id, set=["PROJECT:A"]) == ("project:a",)
    assert rig.update(doc.doc_id) == ("project:a",)
    assert rig.update(doc.doc_id, set=[]) == ()
    assert rig.document_tags(doc.doc_id) == ()
    with pytest.raises(ValidationError):
        DocumentTagsUpdate(set=("project:a",), add=("project:b",))


def test_update_document_tags_refuses_unknown_and_deleted_documents(rig: _Rig) -> None:
    """A document that does not exist, or is deleted, is not_found."""
    rig.create("project:a")
    with pytest.raises(DocumentNotFoundError):
        rig.update(uuid4(), add=["project:a"])
    doc = rig.observe(body="Soon gone.", tags=("project:a",))
    rig.delete(doc.doc_id)
    with pytest.raises(DocumentNotFoundError):
        rig.update(doc.doc_id, add=["project:a"])


# -- deletion and reads -------------------------------------------------------


def test_deletion_keeps_assignment_rows_but_returns_no_tags(rig: _Rig) -> None:
    """D135 keeps the rows for audit; a deleted lineage is never shown with tags."""
    rig.create("project:a")
    kept = rig.observe(body="Stays.", tags=("project:a",), source_ref="docs/a.md")
    gone = rig.observe(body="Goes.", tags=("project:a",), source_ref="docs/b.md")

    rig.delete(gone.doc_id)

    assert (
        rig.count("SELECT count(*) FROM document_tags WHERE doc_id = :d", d=gone.doc_id)
        == 1
    )
    page = DocumentInventory(engine=rig.engine).list_documents(
        deployment_id=_DEPLOYMENT_ID
    )
    assert [(item.doc_id, item.tags) for item in page.documents] == [
        (kept.doc_id, ("project:a",))
    ]
    with rig.engine.connect() as connection:
        hidden = connection.execute(
            text(
                "SELECT d.deleted_at IS NOT NULL,"
                " ARRAY(SELECT t.name FROM document_tags dt JOIN tags t"
                "  USING (deployment_id, tag_id)"
                "  WHERE dt.doc_id = d.doc_id AND d.deleted_at IS NULL)"
                " FROM documents d WHERE d.doc_id = :d"
            ),
            {"d": gone.doc_id},
        ).one()
    assert tuple(hidden) == (True, [])

    returned = rig.observe(body="Goes.", source_ref="docs/b.md")
    assert returned.created, "re-ingest after deletion versions as without tags"


def test_search_documents_returns_each_documents_tags(rig: _Rig) -> None:
    """search_documents results carry the lineage's tag names, by name."""
    rig.create("project:b")
    rig.create("Project:A")
    tagged = rig.upload(
        body="Tagged.", tags=("project:b", "project:a"), filename="tagged.md"
    )
    rig.upload(body="Plain.", filename="plain.md")

    page = DocumentSearch(engine=rig.engine).search_documents(
        deployment_id=_DEPLOYMENT_ID, request=DocumentSearchRequest()
    )

    assert {item.doc_id: item.tags for item in page.documents} == {
        tagged.doc_id: ("Project:A", "project:b"),
        next(
            item.doc_id for item in page.documents if item.doc_id != tagged.doc_id
        ): (),
    }
