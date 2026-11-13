"""D155 inventory allowlist, exact reader bytes and local-link integrity."""

from dataclasses import replace
import json
import posixpath

import pytest

from rememberstack.core.corpus_export import CopyFile
from rememberstack.core.corpus_export import CorpusDocument
from rememberstack.core.corpus_export import CorpusPolicy
from rememberstack.core.corpus_export import DocumentExport
from rememberstack.core.corpus_export import GeneratedFile
from rememberstack.core.corpus_export import json_bytes
from rememberstack.core.corpus_export import READER_ASSET_KINDS
from rememberstack.model import ObjectKey
from rememberstack.workers.corpus_export import load_document_export
from tests.core.test_corpus_export import _check_indexes
from tests.corpus_export_support import authority
from tests.corpus_export_support import AuthorityStore
from tests.corpus_export_support import digest


def _manifest(
    source: CorpusDocument, store: AuthorityStore, **changes: object
) -> CorpusDocument:
    inventory = json.loads(store.objects[source.conversion_uri])
    inventory.update(changes)
    payload = json_bytes(inventory)
    store.objects[source.conversion_uri] = payload
    return replace(source, manifest_hash=digest(payload))


def _bytes(
    export: DocumentExport, artifacts: AuthorityStore, raw: AuthorityStore
) -> dict[str, bytes]:
    return {
        file.path: file.content
        if isinstance(file, GeneratedFile)
        else (raw if file.store == "raw" else artifacts).read_bytes(
            key=ObjectKey(file.uri)
        )
        for file in export.files
    }


def test_exact_recorded_original_markdown_source_map_and_active_pageindex() -> None:
    source, artifacts, raw = authority()
    export = load_document_export(source, artifact_store=artifacts)
    # No original/media buffering and no storage capabilities from slice 1.
    assert raw.reads == []
    assert artifacts.reads == [
        source.conversion_uri,
        source.markdown_uri,
        source.pageindex_uri,
    ]
    files = _bytes(export, artifacts, raw)
    assert files["original.pdf"] == raw.objects[source.raw_uri]
    assert files["document.md"] == artifacts.objects[source.markdown_uri]
    assert files["source_map.json"] == artifacts.objects["recorded/maps/source.json"]
    assert files["pageindex.json"] == artifacts.objects[source.pageindex_uri]
    assert files["media/nested/plot.png"] == artifacts.objects["recorded/assets/plot"]
    for file in export.files:
        assert digest(files[file.path]) == file.sha256
    for forbidden in (
        "provider",
        "blocks.json",
        "conversion.json",
        "meta.json",
        "references",
        "stale",
    ):
        assert not any(forbidden in path for path in files)
    metadata = json.loads(files["document.json"])
    assert metadata["coverage"]["gaps"] == ["page 2 unreadable"]
    assert metadata["warnings"] == ["Blurred scan"]
    assert metadata["derivation_ranges"][0]["derivation_kind"] == "ocr"
    asset = next(
        file for file in metadata["files"] if file["path"].startswith("media/")
    )
    assert asset["locators"] == [{"kind": "page", "page": 1, "precision": "page"}]
    assert "private_execution_state" not in metadata and "components" not in metadata
    assert source.raw_uri.encode() not in files["document.json"]
    assert b"MUST NOT EXPORT" not in b"".join(files.values())
    files["../_index.md"] = b"parent"  # document orientation's logical ancestor
    _check_indexes(files, 150)


@pytest.mark.parametrize("kind", sorted(READER_ASSET_KINDS))
def test_every_reader_kind_is_included_even_when_not_linked(kind: str) -> None:
    source, store, _ = authority(markdown=b"# No linked media\n")
    source = _manifest(
        source,
        store,
        derived_assets=[
            {
                "name": "unlinked/asset.bin",
                "kind": kind,
                "media_type": "application/octet-stream",
                "uri": "recorded/unlinked/asset",
                "sha256": "a" * 64,
            }
        ],
    )
    export = load_document_export(source, artifact_store=store)
    copied = next(
        file for file in export.files if file.path == "media/unlinked/asset.bin"
    )
    assert isinstance(copied, CopyFile) and copied.uri == "recorded/unlinked/asset"


def test_unknown_asset_kind_fails_instead_of_leaking_or_silently_dropping_it() -> None:
    source, store, _ = authority(markdown=b"# Reading\n")
    source = _manifest(
        source,
        store,
        derived_assets=[
            {
                "name": "unknown.json",
                "kind": "new_provider_state",
                "media_type": "application/json",
                "uri": "recorded/private/new",
                "sha256": "a" * 64,
            }
        ],
    )
    with pytest.raises(ValueError, match="unclassified conversion asset kind"):
        load_document_export(source, artifact_store=store)


@pytest.mark.parametrize(
    "name",
    [
        "../document.json",
        "/absolute",
        "a/../../x",
        "a\\x",
        "a//x",
        "a/./x",
        "_index.md",
        "a/~page/x",
        "a/../x",
        "a/%2e%2e/x",
    ],
)
def test_unsafe_asset_names_cannot_override_generated_files(name: str) -> None:
    source, store, _ = authority(markdown=b"# Reading\n")
    source = _manifest(
        source,
        store,
        derived_assets=[
            {
                "name": name,
                "kind": "embedded_image",
                "media_type": "image/png",
                "uri": "recorded/asset",
                "sha256": "a" * 64,
            }
        ],
    )
    with pytest.raises(ValueError):
        load_document_export(source, artifact_store=store)


@pytest.mark.parametrize("names", [("a", "a"), ("a", "a/b.png")])
def test_duplicate_files_and_file_directory_conflicts_fail(
    names: tuple[str, str],
) -> None:
    source, store, _ = authority(markdown=b"# Reading\n")
    source = _manifest(
        source,
        store,
        derived_assets=[
            {
                "name": name,
                "kind": "embedded_image",
                "media_type": "image/png",
                "uri": "recorded/" + name,
                "sha256": "a" * 64,
            }
            for name in names
        ],
    )
    with pytest.raises(ValueError, match="duplicate|conflicts"):
        load_document_export(source, artifact_store=store)


@pytest.mark.parametrize(
    "markdown",
    [
        b"![image](./media/nested/plot.png#view)\n",
        b"[asset](media/nested/plot.png?download=1)\n",
        b"![image][plot]\n\n[plot]: media/nested/plot.png\n",
        b'<img src="media/nested/plot.png">\n',
        b'<video poster="media/nested/plot.png"></video>\n',
        b'<img srcset="media/nested/plot.png 1x, media/nested/plot.png 2x">\n',
        b"[web](https://example.com/elsewhere) [anchor](#heading)\n",
        b"`![code](media/missing.png)`\n\n```md\n![code](media/missing.png)\n```\n",
    ],
)
def test_reader_links_resolve_without_rewriting_markdown(markdown: bytes) -> None:
    source, store, _ = authority(markdown=markdown)
    load_document_export(source, artifact_store=store)
    assert store.objects[source.markdown_uri] == markdown


@pytest.mark.parametrize(
    "markdown",
    [
        b"![image](media/missing.png)\n",
        b"[asset](media/provider.json)\n",
        b"![image](../outside.png)\n",
        b"![image](/outside.png)\n",
        b"![image](media/%2e%2e/private.png)\n",
        b'<img src="file:///private/image.png">\n',
        b'<video poster="media/private.png"></video>\n',
        b'<source srcset="media/missing.png 1x">\n',
    ],
)
def test_missing_private_or_escaping_local_links_fail(markdown: bytes) -> None:
    source, store, _ = authority(markdown=markdown)
    with pytest.raises(ValueError, match="unresolved local reader link"):
        load_document_export(source, artifact_store=store)


@pytest.mark.parametrize("field", ["markdown_uri", "conversion_uri"])
def test_changed_immutable_inputs_fail_hash_validation(field: str) -> None:
    source, store, _ = authority()
    store.objects[getattr(source, field)] += b"corruption"
    with pytest.raises(ValueError, match="hash mismatch"):
        load_document_export(source, artifact_store=store)


def test_inventory_cannot_override_recorded_markdown_hash() -> None:
    source, store, _ = authority()
    source = _manifest(source, store, markdown_sha256="f" * 64)
    with pytest.raises(ValueError, match="disagrees"):
        load_document_export(source, artifact_store=store)


def test_optional_source_map_is_omitted_when_not_emitted() -> None:
    source, store, _ = authority()
    source = _manifest(source, store, source_map=None)
    assert "source_map.json" not in {
        file.path for file in load_document_export(source, artifact_store=store).files
    }


def test_large_asset_tables_split_and_merge_without_renaming_any_reader_file() -> None:
    source, store, _ = authority(markdown=b"# Large asset inventory\n")
    assets = [
        {
            "name": f"frames/frame-{i:04d}.png",
            "kind": "keyframe",
            "media_type": "image/png",
            "uri": f"recorded/frame/{i}",
            "sha256": digest(str(i).encode()),
        }
        for i in range(350)
    ]
    policy = CorpusPolicy(member_limit=32)
    first = _manifest(source, store, derived_assets=assets[:10])
    small = load_document_export(first, artifact_store=store, policy=policy)
    expanded = _manifest(source, store, derived_assets=assets)
    large = load_document_export(expanded, artifact_store=store, policy=policy)
    assert large == load_document_export(expanded, artifact_store=store, policy=policy)
    files = {
        file.path: file.content if isinstance(file, GeneratedFile) else b"reader bytes"
        for file in large.files
    }
    files["../_index.md"] = b"parent"
    _check_indexes(files, 32)
    assert any(
        posixpath.basename(posixpath.dirname(path)).startswith("~") for path in files
    )
    old_copies = {file.path: file for file in small.files if isinstance(file, CopyFile)}
    new_copies = {file.path: file for file in large.files if isinstance(file, CopyFile)}
    assert all(new_copies[path] == file for path, file in old_copies.items())
    _manifest(source, store, derived_assets=assets[:10])
    assert load_document_export(first, artifact_store=store, policy=policy) == small
    assert not any(file.path.startswith("~") for file in small.files)
