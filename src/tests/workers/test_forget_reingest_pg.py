"""D159 §5 test 9 with the flag: adding forgotten content back, deliberately.

Real PostgreSQL and the real E0 ingest, hide and sweeps over local object
stores. Without ``allow_forgotten`` forgotten content is refused; with it, it
is refused while the record is ``erasing`` and accepted afterwards under a
fresh ``doc_id``, releasing on the immutable record only what the request
matched. A lineage's other forgotten versions stay guarded, and a later
forget or ``forget-reapply`` erases only its own copy.
"""

from __future__ import annotations

from collections.abc import Iterator
import hashlib
from pathlib import Path
from typing import Any
from typing import cast
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid5

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentForgottenError
from rememberstack.model import DocumentUpload
from rememberstack.model import ForgetNotCompleteError
from rememberstack.model import ForgetStatus
from rememberstack.model import IngestedVersion
from rememberstack.model import RunResultOutcome
from rememberstack.profiles import SelfHostProfile
from rememberstack.profiles import SelfHostSettings
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import DocumentCatalog
from rememberstack.spine import ForgetCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import UploadIngestor
from tests.database_reset import reset_database

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("59300000-0000-0000-0000-000000000001")
_KIND = "filesystem"
_REF = "notes/plan.md"
_A = b"# Plan\n\nVersion A of the plan.\n"
_B = b"# Plan\n\nVersion B of the plan.\n"


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for re-ingest proofs")
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
    """E0 ingest and the self-host forget over one deployment and local stores."""

    def __init__(self, *, engine: Engine, root: Path) -> None:
        self.engine = engine
        self.raw = LocalFSObjectStore(root=root / "raw")
        stores = cast(Any, self.raw)
        self.profile = SelfHostProfile(
            settings=SelfHostSettings(
                deployment_id=_DEPLOYMENT_ID, forget_quiet_period_s=0
            ),
            engine=engine,
            raw_store=stores,
            artifact_store=cast(Any, LocalFSObjectStore(root=root / "artifacts")),
            corpusfs_store=cast(Any, LocalFSObjectStore(root=root / "corpus")),
            model_provider=FakeModelProvider(),
            model_provider_settings=OpenRouterSettings(api_key="unused"),
        )
        self.catalog = ForgetCatalog(engine=engine)
        self.ingestor = UploadIngestor(
            catalog=DocumentCatalog(engine=engine),
            raw_store=self.raw,
            admission=self.catalog,
            routable_mimes=frozenset({"text/markdown"}),
        )

    def watched(self, content: bytes, *, allow: bool = False) -> IngestedVersion:
        return self.ingestor.ingest_observed(
            deployment_id=_DEPLOYMENT_ID,
            source_kind=_KIND,
            source_ref=_REF,
            upload=self._upload(content=content, allow=allow),
            versioning_mode="snapshot",
            source_modified_at=None,
            source_version_ref=None,
            sync_cycle_id=None,
        )

    def upload(self, content: bytes, *, allow: bool = False) -> IngestedVersion:
        return self.ingestor.ingest(
            deployment_id=_DEPLOYMENT_ID,
            upload=self._upload(content=content, allow=allow),
        )

    def forget(self, doc_id: UUID) -> UUID:
        forget, created = self.profile.forget().request(
            deployment_id=_DEPLOYMENT_ID, doc_id=doc_id
        )
        assert created
        return forget.forget_id

    def settle(self) -> None:
        """Run due work, sweeps included, until no record is erasing."""
        for _ in range(8):
            for result in self.profile.worker_loop(
                worker_class="llm", concurrency=1
            ).drain_due():
                assert result.outcome in {
                    RunResultOutcome.SUCCEEDED,
                    RunResultOutcome.NO_WORK,
                }
            if all(
                record.status is ForgetStatus.COMPLETE
                for record in self.catalog.records(deployment_id=_DEPLOYMENT_ID)
            ):
                return
        raise AssertionError("a forget never completed")

    def release(self, forget_id: UUID) -> dict[str, Any]:
        with self.engine.connect() as connection:
            return dict(
                connection.execute(
                    text(
                        "SELECT version_ids, content_hashes, source_identity_hash,"
                        " released_hashes, identity_released FROM forgets"
                        " WHERE forget_id = :f"
                    ),
                    {"f": forget_id},
                )
                .mappings()
                .one()
            )

    def rows(self, doc_id: UUID) -> int:
        with self.engine.connect() as connection:
            return connection.execute(
                text(
                    "SELECT (SELECT count(*) FROM documents WHERE doc_id = :d)"
                    " + (SELECT count(*) FROM document_versions WHERE doc_id = :d)"
                ),
                {"d": doc_id},
            ).scalar_one()

    def originals(self, doc_id: UUID) -> list[str]:
        return sorted(key.root for key in self.raw.iter_keys(prefix=f"{doc_id}/"))

    @staticmethod
    def _upload(*, content: bytes, allow: bool) -> DocumentUpload:
        return DocumentUpload(
            filename="plan.md",
            mime="text/markdown",
            content=content,
            allow_forgotten=allow,
        )


@pytest.fixture()
def rig(database_engine: Engine, tmp_path: Path) -> _Rig:
    """A fresh deployment per proof."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="forget-reingest",
            name="Forget re-ingest proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
            knowledge_repo_uri="mem://knowledge.git",
        )
    )
    return _Rig(engine=database_engine, root=tmp_path)


def test_a_forgotten_lineage_comes_back_only_deliberately_and_only_once_erased(
    rig: _Rig,
) -> None:
    """Refused, refused while erasing, then re-added under a fresh doc_id."""
    first_a = rig.watched(_A)
    first_b = rig.watched(_B)
    old = first_a.doc_id
    assert first_b.doc_id == old and first_b.created
    first = rig.forget(old)

    with pytest.raises(DocumentForgottenError):
        rig.watched(_A)
    with pytest.raises(ForgetNotCompleteError) as erasing:
        rig.watched(_A, allow=True)
    assert erasing.value.forget_id == first
    rig.settle()
    with pytest.raises(DocumentForgottenError):
        rig.watched(_A)

    readded = rig.watched(_A, allow=True)
    assert readded.created and readded.doc_id != old
    released = rig.release(first)
    assert released["released_hashes"] == [_hash(_A)]
    assert released["identity_released"] is True
    # The sweep keys never change.
    assert set(released["content_hashes"]) == {_hash(_A), _hash(_B)}
    assert set(released["version_ids"]) == {first_a.version_id, first_b.version_id}

    # Adding back A leaves B guarded, on this identity and on any other.
    with pytest.raises(DocumentForgottenError):
        rig.watched(_B)
    with pytest.raises(DocumentForgottenError):
        rig.upload(_B)
    # A is ordinary content again: identical bytes are the D55 no-op.
    again = rig.watched(_A)
    assert (again.doc_id, again.created) == (readded.doc_id, False)

    # A reapply of the first record while the replacement is live erases
    # only the forgotten lineage's copy.
    kept = rig.originals(readded.doc_id)
    assert kept and rig.rows(readded.doc_id) == 2
    assert rig.profile.forget()._service.reapply(  # noqa: SLF001
        deployment_id=_DEPLOYMENT_ID
    ) == (first,)
    rig.settle()
    assert rig.originals(readded.doc_id) == kept
    assert rig.rows(readded.doc_id) == 2
    assert rig.rows(old) == 0 and rig.originals(old) == []

    # A second forget of the re-added lineage erases it and guards it anew;
    # the third copy gets yet another doc_id.
    second = rig.forget(readded.doc_id)
    rig.settle()
    assert rig.rows(readded.doc_id) == 0 and rig.originals(readded.doc_id) == []
    with pytest.raises(DocumentForgottenError) as refused:
        rig.watched(_A)
    assert refused.value.forget_id == second
    third = rig.watched(_A, allow=True)
    assert third.doc_id not in {old, readded.doc_id}
    assert rig.release(second)["released_hashes"] == [_hash(_A)]

    # Reapplying both records erases neither the other's copy nor the third.
    kept = rig.originals(third.doc_id)
    rig.profile.forget()._service.reapply(deployment_id=_DEPLOYMENT_ID)  # noqa: SLF001
    rig.settle()
    assert rig.originals(third.doc_id) == kept and rig.rows(third.doc_id) == 2


def test_a_forgotten_upload_comes_back_under_a_fresh_doc_id(rig: _Rig) -> None:
    """One-shot uploads are guarded by their bytes; the re-add is a new lineage."""
    base = uuid5(NAMESPACE_URL, f"rememberstack:upload:{_DEPLOYMENT_ID}:{_hash(_A)}")
    uploaded = rig.upload(_A)
    assert uploaded.doc_id == base
    forget_id = rig.forget(base)
    rig.settle()

    with pytest.raises(DocumentForgottenError):
        rig.upload(_A)
    readded = rig.upload(_A, allow=True)
    assert readded.created and readded.doc_id != base
    released = rig.release(forget_id)
    assert released["released_hashes"] == [_hash(_A)]
    # An upload carries no source identity, so none is released.
    assert released["source_identity_hash"] is None
    assert released["identity_released"] is False
    assert rig.upload(_A).doc_id == readded.doc_id
