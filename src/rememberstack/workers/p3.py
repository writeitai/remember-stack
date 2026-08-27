"""The P3 corpus filesystem builder (e0 §6, D40/D49): the navigable tree.

A real directory tree, rebuilt whole and published as an immutable snapshot
with a pointer swap — P3's own rebuild-first discipline (D7). It holds
no truth: every file is generated from Postgres plus the artifacts, and the
tree is discardable.

The tree exists to make navigation cheaper than search. An agent reads ONE
`_index.md` and learns what every file in that directory is about (each
member row carries the document's PageIndex root summary, already stored by
the structure stage), so navigation cost is O(index files read), not
O(documents opened). Every level also carries `llms.txt` — orientation
before contents.

**The two-tier path contract (F6) is the load-bearing rule.**

- *Tier 1 — stable, ID-addressed leaves that never move across rebuilds*:
  `entities/<entity_id>/` and `documents/<doc_id>/`. Lineage-anchored
  (D55), so a living document's canonical path survives its versions. These
  are the durable targets agents and K pages may store.
- *Tier 2 — view paths* (`by-source/…`, `by-topic/…`), freely reorganizable
  as the corpus grows; every view stub carries its canonical Tier-1 path in
  frontmatter, so a moved stub is never a lost document.

Fully deterministic, zero LLM: a directory-level synthesis is a K page's
job (a second uncited understanding layer would drift), so `_index.md`
LINKS K and never competes with it.
"""

from collections.abc import Iterable
from datetime import datetime
from datetime import UTC
import hashlib
import json
from pathlib import PurePosixPath
from typing import Final
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.model import ObjectKey
from rememberstack.ports.object_store import ObjectStorePort
from rememberstack.spine.projection import ProjectionCatalog

P3_BUILDER_VERSION: Final = "p3-corpusfs-2026.08"
"""The builder's component version (D12)."""

INDEX_FILE: Final = "_index.md"
MANIFEST_FILE: Final = "llms.txt"

_MAX_SLUG_CHARS: Final = 60
"""Cap on any generated path component — filesystem limits are real."""

_MAX_TOPIC_DEPTH: Final = 6
"""How deep a placement hint may nest: hints are inputs, not commitments."""

_MAX_SHARD_DEPTH: Final = 4
"""How far prefix sharding deepens before accepting a wide leaf."""


class CorpusFsSettings(BaseSettings):
    """The P3 builder's knobs (starting points to measure, D22)."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_P3_")

    snapshot_prefix: str = Field(default="corpusfs/snapshots")
    facets: tuple[str, ...] = ("by-source", "by-time", "by-topic")
    """The declared facet skeleton (e0 §6 rule 1: the top level is
    CONFIGURED, never emergent — facets are stable, their interiors
    reorganize). Every declared facet gets orientation even when empty."""
    shard_threshold: int = Field(default=150, ge=2)
    """Above this many entries a directory shards deterministically — an
    unbounded directory is unbrowsable for an agent and slow to list."""


class CorpusFsBuilder:
    """Build, validate, publish one corpus-filesystem snapshot."""

    def __init__(
        self,
        *,
        catalog: ProjectionCatalog,
        snapshot_store: ObjectStorePort,
        settings: CorpusFsSettings | None = None,
    ) -> None:
        """Bind the builder to the spine and the corpusfs bucket."""
        self._catalog = catalog
        self._snapshot_store = snapshot_store
        self._settings = settings or CorpusFsSettings()

    def build(
        self, *, deployment_id: UUID, version: str | None = None
    ) -> dict[str, object]:
        """Rebuild the whole tree, publish it, swap the pointer."""
        version = version or datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%f")
        prefix = f"{self._settings.snapshot_prefix}/{deployment_id}/{version}"
        snapshot_id = self._catalog.open_snapshot(
            deployment_id=deployment_id,
            plane="P3_corpusfs",
            version=version,
            store_prefix=prefix,
        )
        try:
            files = self._render(deployment_id=deployment_id)
            for path, content in sorted(files.items()):
                self._snapshot_store.write_bytes(
                    key=ObjectKey(f"{prefix}/{path}"), content=content.encode("utf-8")
                )
            manifest = {
                "version": version,
                "files": {
                    path: hashlib.sha256(content.encode("utf-8")).hexdigest()
                    for path, content in files.items()
                },
            }
            self._snapshot_store.write_bytes(
                key=ObjectKey(f"{prefix}/MANIFEST.json"),
                content=json.dumps(manifest).encode("utf-8"),
            )
        except Exception as error:
            self._catalog.mark_failed(
                snapshot_id=snapshot_id,
                validation={"gate": "exception", "error": str(error)[:500]},
            )
            raise
        published = self._catalog.publish(
            deployment_id=deployment_id,
            snapshot_id=snapshot_id,
            plane="P3_corpusfs",
            row_counts={"files": len(files)},
            validation={"gate": "passed", "builder": P3_BUILDER_VERSION},
            built_from_watermark=None,
        )
        return {
            "snapshot_id": snapshot_id,
            "version": version,
            "files": len(files),
            "published": published,
        }

    def _render(self, *, deployment_id: UUID) -> dict[str, str]:
        """Render every file of the tree, keyed by its snapshot-relative path."""
        with self._catalog.corpus_export(deployment_id=deployment_id) as export:
            documents = export.documents()
            entities = export.entities()
            links = export.entity_document_links()
        by_entity: dict[UUID, list[UUID]] = {}
        for link in links:
            by_entity.setdefault(UUID(str(link["entity_id"])), []).append(
                UUID(str(link["doc_id"]))
            )
        documents_by_id = {UUID(str(doc["doc_id"])): doc for doc in documents}

        files: dict[str, str] = {}
        # ── Tier 1: canonical, ID-addressed leaves (never move) ──────────
        for document in documents:
            path = _document_path(doc_id=UUID(str(document["doc_id"])))
            files[f"{path}/{INDEX_FILE}"] = _document_stub(
                document=document, canonical_path=path
            )
        for entity in entities:
            entity_id = UUID(str(entity["entity_id"]))
            path = _entity_path(entity_id=entity_id)
            files[f"{path}/{INDEX_FILE}"] = _entity_index(
                entity=entity,
                documents=[
                    documents_by_id[doc_id]
                    for doc_id in by_entity.get(entity_id, [])
                    if doc_id in documents_by_id
                ],
            )
        # ── Tier 2: view subtrees (reorganizable; stubs carry Tier 1) ────
        views: dict[str, list[dict[str, object]]] = {}
        for document in documents:
            for view_path in _view_paths(document=document):
                views.setdefault(view_path, []).append(document)
        directories: dict[str, list[dict[str, object]]] = {}
        for view_path, members in views.items():
            for directory, shard_members in _shard_tree(
                directory=view_path,
                members=members,
                threshold=self._settings.shard_threshold,
            ).items():
                directories[directory] = shard_members
        for directory, members in directories.items():
            for document in members:
                files[f"{directory}/{_stub_name(document=document)}"] = _document_stub(
                    document=document,
                    canonical_path=_document_path(doc_id=UUID(str(document["doc_id"]))),
                    view_path=directory,
                )
        # ── every level gets orientation, including intermediates ────────
        files.update(
            _level_indexes(
                documents=documents,
                entities=entities,
                directories=directories,
                facets=self._settings.facets,
            )
        )
        return files


def _document_path(*, doc_id: UUID) -> str:
    """The canonical Tier-1 path: lineage-anchored, stable across versions."""
    return f"documents/{doc_id}"


def _entity_path(*, entity_id: UUID) -> str:
    """The canonical Tier-1 path for one entity."""
    return f"entities/{entity_id}"


def _view_paths(*, document: dict[str, object]) -> tuple[str, ...]:
    """The Tier-2 views one document appears in — one stub per view.

    Views come from the configured facet skeleton (the top level is
    configured, never emergent) plus the document's placement hint. A
    document with no hint still lands in the source and time views, so the
    tree is never partially navigable.
    """
    paths = [f"by-source/{_slug(str(document['source_kind']))}"]
    stamp = document.get("source_modified_at") or document.get("published_at")
    if isinstance(stamp, datetime):
        paths.append(f"by-time/{stamp.year:04d}/{stamp.month:02d}")
    placement = document.get("placement_path")
    if isinstance(placement, str) and placement.strip("/"):
        parts = [_slug(part) for part in placement.strip("/").split("/")]
        paths.append("by-topic/" + "/".join(parts[:_MAX_TOPIC_DEPTH]))

    return tuple(paths)


def _shard_tree(
    *, directory: str, members: list[dict[str, object]], threshold: int
) -> dict[str, list[dict[str, object]]]:
    """Split an oversized directory until every leaf fits the threshold.

    Bounded fan-out means BOUNDED: a single-character split leaves a hot
    initial ("Report …" × 500k) as unbrowsable as before (Codex review), so
    the prefix deepens until each bucket fits — or until the names stop
    distinguishing, which stops the recursion rather than looping.
    Deterministic by name, so a rebuild puts a document in the same shard.
    """
    ordered = sorted(members, key=_stub_name_of)
    return _shard_level(
        directory=directory, members=ordered, threshold=threshold, depth=1
    )


def _shard_level(
    *, directory: str, members: list[dict[str, object]], threshold: int, depth: int
) -> dict[str, list[dict[str, object]]]:
    """One level of the shard recursion."""
    if len(members) <= threshold or depth > _MAX_SHARD_DEPTH:
        return {directory: members}
    buckets: dict[str, list[dict[str, object]]] = {}
    for document in members:
        prefix = _stub_name_of(document)[:depth].lower()
        key = prefix if prefix.isalnum() else "other"
        buckets.setdefault(key, []).append(document)
    if len(buckets) == 1:  # the prefix does not distinguish: stop here
        return {directory: members}
    result: dict[str, list[dict[str, object]]] = {}
    for bucket, bucket_members in buckets.items():
        result.update(
            _shard_level(
                directory=f"{directory}/{bucket}",
                members=bucket_members,
                threshold=threshold,
                depth=depth + 1,
            )
        )
    return result


def _stub_name_of(document: dict[str, object]) -> str:
    """Sort key / shard key for one member."""
    return _stub_name(document=document)


def _stub_name(*, document: dict[str, object]) -> str:
    """The view stub's filename: readable, deterministic, collision-FREE.

    The FULL document id rides the name (Codex review: a truncated id
    collides at corpus scale, silently overwriting one stub while the
    member table still lists two documents).
    """
    return f"{_slug(str(document.get('title') or 'untitled'))}-{document['doc_id']}.md"


def _document_stub(
    *, document: dict[str, object], canonical_path: str, view_path: str | None = None
) -> str:
    """One generated stub: orientation + canonical path + artifact pointer.

    `grep -r` over stubs is content-ish lookup with zero API calls, so the
    title, summary, and pointers are IN the file — and every view stub
    names its Tier-1 canonical path, so a reorganized view never loses the
    document. The stub also carries the explicit `raw_uri` (D51): raw is
    off the browse path, never unreachable — following this pointer is how
    a multimodal harness or a re-OCR session opens the original, and that
    read is audited.
    """
    summary = _one_line(str(document.get("root_summary") or ""))
    frontmatter: dict[str, str] = {
        "doc_id": str(document["doc_id"]),
        "canonical_path": canonical_path,
        "version_id": str(document.get("version_id") or ""),
        "content_hash": str(document.get("content_hash") or ""),
        "artifact_uri": str(document.get("markdown_uri") or ""),
        "raw_uri": str(document.get("raw_uri") or ""),
        "mime": str(document.get("mime") or ""),
        "source_kind": str(document.get("source_kind") or ""),
        "source_ref": str(document.get("source_ref") or ""),
    }
    if view_path is not None:
        frontmatter["view_path"] = view_path
    lines = ["---"]
    # values are JSON-escaped: a source ref carrying a newline must never
    # inject or override a frontmatter field such as canonical_path
    lines.extend(f"{key}: {json.dumps(value)}" for key, value in frontmatter.items())
    lines.extend(
        [
            "---",
            "",
            f"# {_one_line(str(document.get('title') or 'Untitled document'))}",
            "",
        ]
    )
    if summary:
        lines.extend([summary, ""])
    lines.append(f"- Canonical path: `{canonical_path}/`")
    lines.append(f"- Full text: `{document.get('markdown_uri') or '(not converted)'}`")
    if document.get("raw_uri"):
        lines.append(
            f"- Original (off the browse path; audited): `{document['raw_uri']}`"
        )
    lines.append("")
    return "\n".join(lines)


def _entity_index(
    *, entity: dict[str, object], documents: list[dict[str, object]]
) -> str:
    """An entity's Tier-1 page: profile plus the documents evidencing it."""
    entity_id = UUID(str(entity["entity_id"]))
    canonical = _entity_path(entity_id=entity_id)
    lines = [
        "---",
        f"entity_id: {json.dumps(str(entity_id))}",
        f"canonical_path: {json.dumps(canonical)}",
        "---",
        "",
        f"# {_one_line(str(entity['canonical_name']))}",
        "",
        f"{entity.get('mention_count') or 0} mention(s)",
        "",
    ]
    profile = _one_line(str(entity.get("profile_summary") or ""))
    if profile:
        lines.extend([profile, ""])
    lines.extend(["## Documents mentioning this entity", ""])
    lines.extend(_member_table(members=documents, base=canonical))
    lines.append("")
    return "\n".join(lines)


def _directory_index(
    *, directory: str, members: list[dict[str, object]], children: list[str]
) -> str:
    """The member table: every file's one-line meaning, from Postgres.

    This is the load-bearing property of the tree — one read tells an agent
    what everything here is about. Deterministic by contract: no LLM call
    lives inside the projection builder (a directory-level synthesis is a K
    page's job, e0 §6).
    """
    sources = sorted({str(member["source_kind"]) for member in members})
    dated = sorted(
        stamp
        for stamp in (
            member.get("source_modified_at") or member.get("published_at")
            for member in members
        )
        if isinstance(stamp, datetime)
    )
    span = f" · {dated[0].date()}–{dated[-1].date()}" if dated else ""
    headline = f"{len(members)} document(s) directly here"
    if children:
        headline += f", {len(children)} subdirectory/subdirectories"
    if sources:
        headline += f" · sources: {', '.join(sources)}"
    lines = [f"# {directory}", "", headline + span, ""]
    if children:
        lines.extend(["## Subdirectories", ""])
        lines.extend(
            f"- [`{PurePosixPath(child).name}/`]"
            f"({PurePosixPath(child).name}/{INDEX_FILE})"
            for child in sorted(children)
        )
        lines.append("")
    lines.extend(["## Contents", ""])
    lines.extend(_member_table(members=members, base=directory))
    parent = str(PurePosixPath(directory).parent)
    parent_link = INDEX_FILE if parent in {".", ""} else f"{parent}/{INDEX_FILE}"
    lines.extend(
        [
            "",
            "## Navigation",
            "",
            f"- Parent: `{parent_link}`",
            "- Canonical (never-moving) paths are in the table above and in"
            " each stub's `canonical_path` frontmatter.",
            "",
        ]
    )
    return "\n".join(lines)


def _member_table(*, members: Iterable[dict[str, object]], base: str) -> list[str]:
    """One row per child carrying its root summary AND its canonical link.

    The canonical column is what makes the table navigable from anywhere:
    an entity page lists documents that do not live in its directory, so a
    bare filename would be a dead end (Codex review).
    """
    rows = [
        "| File | What it is | Canonical | Source | Date |",
        "|---|---|---|---|---|",
    ]
    for member in sorted(members, key=_stub_name_of):
        summary = _one_line(str(member.get("root_summary") or ""))[:160]
        stamp = member.get("source_modified_at") or member.get("published_at")
        date = stamp.date().isoformat() if isinstance(stamp, datetime) else "—"
        canonical = _document_path(doc_id=UUID(str(member["doc_id"])))
        link = f"{_relative(base=base, target=canonical)}/{INDEX_FILE}"
        rows.append(
            f"| `{_stub_name(document=member)}` | {summary or '—'} |"
            f" [`{canonical}/`]({link}) |"
            f" {member.get('source_kind') or '—'} | {date} |"
        )
    if len(rows) == 2:
        rows.append("| — | (empty) | — | — | — |")
    return rows


def _directory_manifest(
    *, directory: str, members: list[dict[str, object]], children: list[str]
) -> str:
    """`llms.txt`: orientation before contents (the navigation-manifest pattern)."""
    lines = [
        f"# {directory}",
        "",
        f"> {len(members)} document(s) here, {len(children)} subdirectory/"
        f"subdirectories. Read {INDEX_FILE} for the member table — every"
        " file's one-line meaning — before opening any file.",
        "",
    ]
    if children:
        lines.extend(["## Subdirectories", ""])
        lines.extend(f"- {PurePosixPath(child).name}/" for child in sorted(children))
        lines.append("")
    if members:
        lines.extend(["## Files", ""])
        lines.extend(
            f"- [{_stub_name(document=member)}]({_stub_name(document=member)}):"
            f" {_one_line(str(member.get('root_summary') or ''))[:120] or 'no summary'}"
            for member in sorted(members, key=_stub_name_of)
        )
        lines.append("")
    return "\n".join(lines)


def _level_indexes(
    *,
    documents: tuple[dict[str, object], ...],
    entities: tuple[dict[str, object], ...],
    directories: dict[str, list[dict[str, object]]],
    facets: tuple[str, ...],
) -> dict[str, str]:
    """Orientation for EVERY level: leaves, intermediates, facets, root.

    e0 §6's contract is "each level carries a generated `_index.md` /
    `llms.txt`" — an intermediate directory named as a parent but missing
    its own index is a dead end in the navigation ladder (Codex review).
    The CONFIGURED facet skeleton is emitted whether or not documents
    landed in it, so the top level never depends on what happens to be
    ingested (e0 §6 rule 1).
    """
    rendered: dict[str, str] = {}
    members_by_directory: dict[str, list[dict[str, object]]] = dict(directories)
    children_by_directory: dict[str, set[str]] = {}
    for directory in list(directories):
        current = PurePosixPath(directory)
        while True:
            parent = str(current.parent)
            if parent in {".", ""}:
                break
            children_by_directory.setdefault(parent, set()).add(str(current))
            members_by_directory.setdefault(parent, [])
            current = current.parent
    for facet in facets:
        members_by_directory.setdefault(facet, [])
        children_by_directory.setdefault(facet, set())
    for directory, members in members_by_directory.items():
        children = sorted(children_by_directory.get(directory, set()))
        rendered[f"{directory}/{INDEX_FILE}"] = _directory_index(
            directory=directory, members=members, children=children
        )
        rendered[f"{directory}/{MANIFEST_FILE}"] = _directory_manifest(
            directory=directory, members=members, children=children
        )
    rendered[f"documents/{INDEX_FILE}"] = _tier_one_documents_index(documents=documents)
    rendered[f"entities/{INDEX_FILE}"] = _tier_one_entities_index(entities=entities)
    rendered[MANIFEST_FILE] = _root_manifest(
        documents=documents, entities=entities, facets=facets
    )
    rendered[INDEX_FILE] = _root_index(
        documents=documents, entities=entities, facets=facets
    )
    return rendered


def _tier_one_documents_index(*, documents: tuple[dict[str, object], ...]) -> str:
    """`documents/_index.md`: the canonical leaves, one row each."""
    lines = [
        "# documents",
        "",
        "Canonical (Tier 1) document leaves — these paths never move across"
        f" rebuilds. {len(documents)} lineage(s).",
        "",
    ]
    lines.extend(_member_table(members=documents, base="documents"))
    lines.append("")
    return "\n".join(lines)


def _tier_one_entities_index(*, entities: tuple[dict[str, object], ...]) -> str:
    """`entities/_index.md`: the canonical entity leaves, one row each."""
    lines = [
        "# entities",
        "",
        "Canonical (Tier 1) entity leaves — these paths never move across"
        f" rebuilds. {len(entities)} active entity/entities.",
        "",
        "| Entity | Mentions | Canonical |",
        "|---|---|---|",
    ]
    for entity in sorted(entities, key=lambda item: str(item["canonical_name"])):
        canonical = _entity_path(entity_id=UUID(str(entity["entity_id"])))
        link = f"{_relative(base='entities', target=canonical)}/{INDEX_FILE}"
        lines.append(
            f"| {_one_line(str(entity['canonical_name']))} |"
            f" {entity.get('mention_count') or 0} | [`{canonical}/`]({link}) |"
        )
    if len(lines) == 6:
        lines.append("| — | — | — |")
    lines.append("")
    return "\n".join(lines)


def _root_index(
    *,
    documents: tuple[dict[str, object], ...],
    entities: tuple[dict[str, object], ...],
    facets: tuple[str, ...],
) -> str:
    """The root `_index.md`: how to navigate, and the durable-path contract."""
    lines = [
        "# Corpus",
        "",
        f"{len(documents)} document(s), {len(entities)} entity/entities.",
        "",
        "## How to navigate",
        "",
        "1. `cat llms.txt` — facets and where things live.",
        f"2. `cat <facet>/{INDEX_FILE}` — what kinds of things exist there.",
        f"3. `cat <directory>/{INDEX_FILE}` — the member table: every file's"
        " one-line meaning.",
        "4. `cat <stub>.md` — orientation, canonical path, artifact pointer.",
        "",
        "## Facets",
        "",
    ]
    lines.extend(
        f"- [`{facet}/`]({facet}/{INDEX_FILE}) — view paths (reorganizable)"
        for facet in facets
    )
    lines.extend(
        [
            f"- [`documents/`](documents/{INDEX_FILE}) — canonical, stable per lineage",
            f"- [`entities/`](entities/{INDEX_FILE}) — canonical, stable per entity",
            "",
            "Durable paths live under `documents/` and `entities/` (Tier 1) and"
            " never move; view subtrees reorganize as the corpus grows.",
            "",
        ]
    )
    return "\n".join(lines)


def _root_manifest(
    *,
    documents: tuple[dict[str, object], ...],
    entities: tuple[dict[str, object], ...],
    facets: tuple[str, ...],
) -> str:
    """The root orientation file an agent reads first."""
    lines = [
        "# Corpus filesystem",
        "",
        "> A generated, rebuildable view over the memory's documents and"
        " entities. Nothing here is source of truth; every file names the"
        " artifact it points at.",
        "",
        f"- {len(documents)} document lineage(s)",
        f"- {len(entities)} active entity/entities",
        "",
        "## Facets",
        "",
    ]
    lines.extend(f"- `{facet}/` (view paths — reorganizable)" for facet in facets)
    lines.extend(
        [
            "- `documents/` (canonical, stable per lineage)",
            "- `entities/` (canonical, stable per entity)",
            "",
            "## Contract",
            "",
            "Paths under `documents/` and `entities/` are stable across"
            " rebuilds and safe to store. View paths may reorganize; every"
            " view stub carries its canonical path in frontmatter.",
            "",
            "Originals live off this path: follow a stub's `raw_uri`"
            " deliberately — those reads are audited.",
            "",
        ]
    )
    return "\n".join(lines)


def _relative(*, base: str, target: str) -> str:
    """A relative link from one directory to another inside the tree."""
    up = "/".join([".."] * len(PurePosixPath(base).parts))
    return f"{up}/{target}" if up else target


def _one_line(value: str) -> str:
    """Collapse whitespace so a title or summary can never break a table."""
    return " ".join(value.split())


def _slug(value: str) -> str:
    """A filesystem-safe, deterministic, LENGTH-CAPPED slug.

    Capping matters: a 300-character title would otherwise produce a name
    common filesystems reject, failing the whole publish for one verbose
    (or hostile) document (Codex review).
    """
    cleaned = "".join(
        character.lower() if character.isalnum() else "-" for character in value.strip()
    )
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    capped = (cleaned.strip("-") or "untitled")[:_MAX_SLUG_CHARS]
    return capped.strip("-") or "untitled"
