"""D155 pure type/name rules and bounded, deterministic reader navigation."""

from dataclasses import replace
import hashlib
import json
import posixpath
from urllib.parse import unquote
from uuid import UUID

from markdown_it import MarkdownIt
import pytest

from rememberstack.core.corpus_export import corpus_suffix
from rememberstack.core.corpus_export import corpus_topics
from rememberstack.core.corpus_export import corpus_type
from rememberstack.core.corpus_export import CORPUS_TYPES
from rememberstack.core.corpus_export import CorpusPolicy
from rememberstack.core.corpus_export import desired_export_revision
from rememberstack.core.corpus_export import GeneratedFile
from rememberstack.core.corpus_export import original_name
from rememberstack.core.corpus_export import render_navigation
from rememberstack.core.corpus_export import title_slug
from rememberstack.core.format_registry import FAMILIES
from rememberstack.workers.corpus_export import load_document_export
from tests.corpus_export_support import authority
from tests.corpus_export_support import document


def test_suffix_uses_uuid_bytes_and_needs_no_retained_metadata() -> None:
    source = document()
    expected = hashlib.sha256(source.doc_id.bytes).hexdigest()[:24]
    scrubbed = replace(
        source,
        title=None,
        file_name=None,
        lineage_title=None,
        source_ref=None,
        source_uri=None,
        placement_path=None,
    )
    assert corpus_suffix(scrubbed.doc_id) == corpus_suffix(source.doc_id) == expected
    assert len(expected) == 24 and all(char in "0123456789abcdef" for char in expected)
    assert (
        corpus_suffix(source.doc_id)
        != hashlib.sha256(str(source.doc_id).encode()).hexdigest()[:24]
    )


@pytest.mark.parametrize(
    ("source", "mime", "family", "route", "attachment", "expected"),
    [
        ("email", "application/pdf", "pdf", "pdf", False, "email"),
        ("chat", "message/rfc822", "email", "email", False, "email"),
        ("upload", "application/vnd.ms-outlook", "binary", "card", False, "email"),
        ("upload", "application/octet-stream", "binary", "msg", False, "email"),
        ("chat", "application/pdf", "pdf", "pdf", False, "chat"),
        ("upload", "text/plain", "text", "conversation", False, "chat"),
        ("upload", "image/jpeg", "image", "card", False, "image"),
        ("upload", "audio/mpeg", "media", "card", False, "audio"),
        ("upload", "video/mp4", "media", "card", False, "video"),
        ("upload", "application/pdf", "pdf", "card", False, "pdf"),
        ("email", "application/pdf", "pdf", "pdf", True, "pdf"),
        ("chat", "image/png", "image", "card", True, "image"),
        ("upload", "text/csv", "delimited", "table", False, "spreadsheet"),
        (
            "upload",
            "text/tab-separated-values",
            "delimited",
            "table",
            False,
            "spreadsheet",
        ),
        (
            "upload",
            "application/vnd.ms-excel",
            "spreadsheet",
            "card",
            False,
            "spreadsheet",
        ),
        (
            "upload",
            "application/vnd.ms-powerpoint",
            "presentation",
            "card",
            False,
            "presentation",
        ),
        *(
            ("upload", "text/plain", family, "text", False, "document")
            for family in ("text", "markdown", "html", "word", "notebook", "ebook")
        ),
        *(
            ("upload", "application/octet-stream", family, "card", False, "data")
            for family in ("dataset", "code", "config", "archive", "log", "other_text")
        ),
        ("upload", "application/octet-stream", "binary", "card", False, "other"),
        (
            "upload",
            "application/x-unrecognized",
            "custom",
            "custom-card",
            False,
            "other",
        ),
    ],
)
def test_ordered_types(source, mime, family, route, attachment, expected) -> None:
    assert (
        corpus_type(
            document(
                source_kind=f"{source}_attachment" if attachment else source,
                mime=mime,
                family=family,
                route=route,
                file_name="malicious.eml",
            )
        )
        == expected
    )


def test_every_registered_family_has_an_intended_type() -> None:
    expected = {
        "archive": "data",
        "binary": "other",
        "code": "data",
        "config": "data",
        "dataset": "data",
        "delimited": "spreadsheet",
        "ebook": "document",
        "email": "email",
        "html": "document",
        "image": "image",
        "log": "data",
        "markdown": "document",
        "media": "audio",  # The registry's canonical media MIME is audio/mpeg.
        "notebook": "document",
        "other_text": "data",
        "pdf": "pdf",
        "presentation": "presentation",
        "spreadsheet": "spreadsheet",
        "text": "document",
        "word": "document",
    }
    assert {family.name for family in FAMILIES} == expected.keys()
    for family in FAMILIES:
        assert (
            corpus_type(
                document(
                    family=family.name, mime=family.mime, route=family.converter or ""
                )
            )
            == expected[family.name]
        ), family.name


@pytest.mark.parametrize(
    ("mime", "expected"),
    [
        (" AUDIO/MP4; codecs=mp4a.40.2 ", "audio"),
        ("video/quicktime", "video"),
        ("application/octet-stream", "other"),
    ],
)
def test_media_type_uses_stored_mime_with_generic_card_route(mime, expected) -> None:
    assert corpus_type(document(family="media", mime=mime, route="card")) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("../../ Bad\\name | [x](evil)\n", "bad-name-x-evil"),
        ("", "untitled"),
        ("日本語", "untitled"),
        ("CON", "untitled"),
        ("CÓN", "untitled"),
        ("Ｒｅｐｏｒｔ １２", "report-12"),
        ("Résumé", "resume"),
        ("A" * 1000, "a" * 60),
        ("ß" * 100, "ss" * 30),
        ("..", "untitled"),
        ("x--y", "x-y"),
    ],
)
def test_slug_is_safe_and_bounded(title, expected) -> None:
    assert title_slug(title) == expected
    assert len(title_slug(title).encode()) <= 60


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Smlouva o dílo – Žluťoučký kůň 2024", "smlouva-o-dilo-zlutoucky-kun-2024"),
        ("Straße für Öl", "strasse-fur-ol"),
        ("Łódź i Gdańsk", "lodz-i-gdansk"),
        ("Cœur de l’été à Noël", "coeur-de-l-ete-a-noel"),
        ("ẞ Æ Œ Ø Ł Đ Ð Þ", "ss-ae-oe-o-l-d-d-th"),
        ("Re\u0301sume\u0301", "resume"),
    ],
)
def test_latin_titles_and_topics_transliterate(title, expected) -> None:
    assert title_slug(title) == expected
    assert corpus_topics(f"Root/{title}") == ("root", expected)


@pytest.mark.parametrize(
    "hint",
    [
        None,
        "",
        "/absolute",
        "trailing/",
        "a//b",
        "a/../b",
        "a/./b",
        "a\\b",
        "a/b/c/d/e",
        "a/日本語",
        "a/~pages",
        "a/_index.md",
        "a/document.json",
        "a/ＣＯＮ",
        "a/cón",
        "a/one／two",
        "a/one＼two",
    ],
)
def test_invalid_or_null_topic_rejects_whole_hint(hint) -> None:
    assert corpus_topics(hint) == ()


def test_topic_nfkc_normalizes_and_caps_each_component() -> None:
    assert corpus_topics("Ｆｉｎａｎｃｅ/Annual  Reports") == (
        "finance",
        "annual-reports",
    )
    assert corpus_topics("x" * 100) == ("x" * 48,)


@pytest.mark.parametrize(
    ("title", "filename", "lineage", "expected"),
    [
        ("Version title", "file.pdf", "Lineage", "Version title"),
        (None, "file.pdf", "Lineage", "file.pdf"),
        (" ", None, "Lineage", "Lineage"),
        (None, None, None, "Untitled document"),
    ],
)
def test_title_fallback_uses_selected_version(
    title, filename, lineage, expected
) -> None:
    assert (
        document(title=title, file_name=filename, lineage_title=lineage).display_title
        == expected
    )


def test_original_extension_comes_from_registry_not_title() -> None:
    assert original_name("application/pdf") == "original.pdf"
    assert original_name("video/mp4") == "original.mp4"
    assert original_name("application/vnd.ms-outlook") == "original.msg"
    assert original_name("unrecognized/anything") == "original.bin"


def _links(files: dict[str, bytes], path: str) -> list[str]:
    tokens = MarkdownIt().parse(files[path].decode())
    urls = [
        child.attrGet("href")
        for token in tokens
        for child in (token.children or [])
        if child.type == "link_open"
    ]
    return [
        posixpath.normpath(posixpath.join(posixpath.dirname(path), unquote(url)))
        for url in urls
        if isinstance(url, str) and url
    ]


def _check_indexes(files: dict[str, bytes], limit: int) -> None:
    for path, content in files.items():
        if not path.endswith("_index.md"):
            continue
        lines = content.decode().splitlines()
        assert len([line for line in lines if line.startswith("| ")]) - 2 <= limit
        assert all(target in files for target in _links(files, path)), path


def test_navigation_split_merge_keeps_document_paths_and_bytes() -> None:
    source, store, _ = authority()
    policy = CorpusPolicy(member_limit=32)
    before = load_document_export(source, artifact_store=store, policy=policy)
    docs = [
        replace(
            source, doc_id=UUID(int=10_000 + i), title="Same title", placement_path=None
        )
        for i in range(180)
    ]
    docs[0] = replace(source, placement_path=None)
    small = render_navigation(docs[:32], policy=policy)
    large = render_navigation(reversed(docs), policy=policy)
    again = render_navigation(docs, policy=policy)
    assert large == again
    assert not any("/~" in path for path in small)
    assert any("/~" in path for path in large)
    assert render_navigation(docs[:32], policy=policy) == small
    exported = [
        load_document_export(doc, artifact_store=store, policy=policy) for doc in docs
    ]
    all_files = dict(large)
    for export in exported:
        all_files.update(
            {
                f"{export.path}/{file.path}": file.content
                for file in export.files
                if isinstance(file, GeneratedFile)
            }
        )
        for file in export.files:
            all_files.setdefault(f"{export.path}/{file.path}", b"authority")
    _check_indexes(all_files, 32)
    assert load_document_export(source, artifact_store=store, policy=policy) == before
    assert set(large).issuperset(f"{kind}/_index.md" for kind in CORPUS_TYPES)
    assert [path for path in large if path.endswith("llms.txt")] == ["llms.txt"]
    assert all(
        not path.startswith(("documents/", "entities/", "_paths/", "by-"))
        for path in large
    )


def test_many_topics_are_bounded_without_changing_physical_folders() -> None:
    docs = [
        document(i, placement_path=f"topic-{i:03d}/same/deeper/leaf")
        for i in range(220)
    ]
    files = render_navigation(docs)
    for doc in docs:
        files[f"{doc.path}/_index.md"] = b""
    _check_indexes(files, 150)
    assert any(path.startswith("pdf/~") for path in files)
    assert all(
        doc.path.startswith(f"pdf/topic-{i:03d}/same/deeper/leaf/")
        for i, doc in enumerate(docs)
    )


def test_hostile_metadata_cannot_inject_markdown_rows_or_links() -> None:
    title = "A | [injected](https://evil.invalid) <img src=x>\n# heading"
    doc = document(title=title, root_summary=title, source_kind=title)
    files = render_navigation([doc])
    files[f"{doc.path}/_index.md"] = b""
    _check_indexes(files, 150)
    index = files["pdf/finance/annual-reports/_index.md"].decode()
    assert "<img" not in index and "[injected]" not in index
    assert index.count(str(doc.doc_id)) == 1


def test_digest_covers_authority_and_policy_but_not_success_marker() -> None:
    source, store, _ = authority()
    original = load_document_export(source, artifact_store=store)
    assert desired_export_revision(source) == original.revision
    assert (
        load_document_export(
            replace(source, corpus_published_revision="already-published"),
            artifact_store=store,
        ).revision
        == original.revision
    )
    for changes in (
        {"title": "Renamed"},
        {"placement_path": None},
        {"root_summary": "New summary"},
        {"structure_generation_id": UUID(int=999)},
        {"version_id": UUID(int=888)},
        {"representation_id": UUID(int=777)},
        {"mime": "image/png", "family": "image"},
    ):
        assert (
            load_document_export(
                replace(source, **changes), artifact_store=store
            ).revision
            != original.revision
        )
    assert (
        load_document_export(
            source,
            artifact_store=store,
            policy=CorpusPolicy(builder_version="new-policy"),
        ).revision
        != original.revision
    )
    metadata = next(file for file in original.files if file.path == "document.json")
    assert isinstance(metadata, GeneratedFile)
    assert json.loads(metadata.content)["export_revision"] == original.revision


@pytest.mark.parametrize(
    "field",
    ["content_hash", "markdown_hash", "manifest_hash", "pageindex_uri", "raw_uri"],
)
def test_db_only_digest_covers_immutable_inventory_and_active_structure(
    field: str,
) -> None:
    source = document()
    assert desired_export_revision(
        replace(source, **{field: "changed"})
    ) != desired_export_revision(source)
