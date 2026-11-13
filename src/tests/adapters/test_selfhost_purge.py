"""D74 per-document erasure over the real local filesystem adapter."""

from pathlib import Path
from uuid import uuid4

import pytest

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


def test_local_suffix_purge_retries_with_empty_recorded_state(tmp_path: Path) -> None:
    store = LocalFSObjectStore(root=tmp_path / "corpus")
    forgotten, control = document(title="Private"), document(2, title="Control")
    authority = ScrubbedAuthority(survivors=(control,))
    publisher = scrubbed_publisher(store=store, authority=authority)
    adapter = SelfHostProjectionPurger(publisher=publisher, object_purger=store)
    args = {
        "deployment_id": forgotten.deployment_id,
        "doc_id": forgotten.doc_id,
        "forget_id": uuid4(),
    }
    control_key = ObjectKey(control.path + "/original.pdf")
    store.write_bytes(key=control_key, content=b"untouched", storage_class="hot")
    for _ in range(2):
        # Independent bucket restore after the local set was already cleared.
        store.write_bytes(
            key=ObjectKey(forgotten.path + "/unindexed"),
            content=b"private",
            storage_class="hot",
        )
        store.replace_stream(
            key=ObjectKey("pdf/obsolete/_index.md"),
            chunks=(b"private",),
            storage_class="hot",
        )
        adapter.quiesce_projections(
            deployment_id=forgotten.deployment_id, forget_id=args["forget_id"]
        )
        adapter.purge_projections(**args)
        adapter.verify_projections_purged(**args)
        assert authority.recorded[forgotten.doc_id] == set()
        assert store.read_bytes(key=control_key) == b"untouched"
        with pytest.raises(FileNotFoundError):
            store.read_bytes(key=ObjectKey("pdf/obsolete/_index.md"))
