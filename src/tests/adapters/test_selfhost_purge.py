"""D159 per-document erasure over the real local filesystem adapter."""

from pathlib import Path

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.selfhost import SelfHostProjectionPurger
from rememberstack.model import ObjectKey
from tests.corpus_export_support import document
from tests.corpus_purge_support import scrubbed_publisher
from tests.corpus_purge_support import ScrubbedAuthority


def test_local_object_purge_is_exact_prefix_aware_and_idempotent(
    tmp_path: Path,
) -> None:
    store = LocalFSObjectStore(root=tmp_path / "objects")
    for key in ("doc/exact", "doc/nested/body", "doc-other/control"):
        store.write_bytes(key=ObjectKey(key), content=key.encode(), storage_class="hot")
    for _ in range(2):
        store.purge_objects(keys=(), prefixes=(ObjectKey("doc"),))
        store.verify_objects_purged(keys=(), prefixes=(ObjectKey("doc"),))
    assert store.read_bytes(key=ObjectKey("doc-other/control")) == b"doc-other/control"


def test_local_suffix_erasure_repeats_and_reports_what_it_removed(
    tmp_path: Path,
) -> None:
    """D159 §3.3 step 3: suffix folders, listing navigation, then nothing left."""
    store = LocalFSObjectStore(root=tmp_path / "corpus")
    forgotten, control = document(title="Private"), document(2, title="Control")
    authority = ScrubbedAuthority(survivors=(control,))
    publisher = scrubbed_publisher(store=store, authority=authority)
    invalidated: list[tuple[str, ...]] = []

    class _Sessions:
        def invalidate(self, *, paths: tuple[str, ...], **_: object) -> None:
            invalidated.append(paths)
            raise RuntimeError("an offline session never holds a forget open")

    adapter = SelfHostProjectionPurger(
        publisher=publisher, object_purger=store, caches=_Sessions()
    )
    control_key = ObjectKey(control.path + "/original.pdf")
    store.write_bytes(key=control_key, content=b"untouched", storage_class="hot")
    for _ in range(2):
        # Independent bucket restore after an earlier sweep already erased it.
        store.write_bytes(
            key=ObjectKey(forgotten.path + "/unindexed"),
            content=b"private",
            storage_class="hot",
        )
        # A failed local delete can leave only private digest/class metadata.
        hidden = ObjectKey(forgotten.path + "/metadata-only")
        store.write_bytes(key=hidden, content=b"private", storage_class="hot")
        store._path_for(key=hidden).unlink()  # noqa: SLF001
        assert adapter.erase_document(
            deployment_id=forgotten.deployment_id, doc_id=forgotten.doc_id
        )
        assert hidden not in set(store.iter_purge_keys())
        assert not list(store.iter_keys(prefix=forgotten.path))
        assert store.read_bytes(key=control_key) == b"untouched"
        assert forgotten.path in invalidated[-1]
        assert not adapter.erase_document(
            deployment_id=forgotten.deployment_id, doc_id=forgotten.doc_id
        )
