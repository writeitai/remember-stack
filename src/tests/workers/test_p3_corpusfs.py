"""WP-4.5 acceptance: the P3 corpus filesystem (e0 §6, D40).

The tree rebuilds whole, publishes as an immutable snapshot, and satisfies
the two contracts that make it worth having: **Tier-1 paths never move
across rebuilds** (S44's durable targets), and **one `_index.md` read tells
an agent what every file in the directory is about** — navigation cheaper
than search, with zero LLM inside the builder.
"""

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

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import ObjectKey
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import ProjectionCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import CorpusFsBuilder
from rememberstack.workers import CorpusFsSettings

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("45000000-0000-0000-0000-000000000001")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for real corpus-fs proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


class _Corpus:
    """A small corpus with summaries, placements, entities, and mentions."""

    def __init__(self, *, engine: Engine) -> None:
        """Seed documents (with root-section summaries) and one entity."""
        self.engine = engine
        self.docs: dict[str, UUID] = {}
        self.entity_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO entities (entity_id, deployment_id, type,"
                    " canonical_name, normalized_name, profile_summary,"
                    " mention_count)"
                    " VALUES (:e, :d, 'Organization', 'Acme', 'acme',"
                    " 'A manufacturer of anvils.', 3)"
                ),
                {"e": self.entity_id, "d": _DEPLOYMENT_ID},
            )
            for title, placement, summary in (
                (
                    "Annual Report",
                    "/finance/annual-reports/2023/",
                    "Acme's 2023 results.",
                ),
                ("Design Note", "/research/anvils/", "How the anvil is shaped."),
                ("Loose Memo", None, "An unfiled memo."),
            ):
                self.docs[title] = self._document(
                    connection, title=title, placement=placement, summary=summary
                )

    def _document(
        self, connection: object, *, title: str, placement: str | None, summary: str
    ) -> UUID:
        """One lineage with a ready version, representation, and root section."""
        doc_id, version_id, representation_id = uuid4(), uuid4(), uuid4()
        structure_generation_id = uuid4()
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO documents (doc_id, deployment_id, source_kind,"
                " source_ref, title) VALUES (:doc, :d, 'upload', :ref, :title)"
            ),
            {"doc": doc_id, "d": _DEPLOYMENT_ID, "ref": title.lower(), "title": title},
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO content_objects (deployment_id, content_hash, mime,"
                " byte_size, raw_uri) VALUES (:d, :h, 'text/markdown', 10, :uri)"
                " ON CONFLICT DO NOTHING"
            ),
            {"d": _DEPLOYMENT_ID, "h": f"hash-{title}", "uri": f"raw/{doc_id}"},
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO document_versions (version_id, deployment_id, doc_id,"
                " content_hash, version_no, status, source_modified_at)"
                " VALUES (:v, :d, :doc, :h, 1, 'ready', :modified)"
            ),
            {
                "v": version_id,
                "d": _DEPLOYMENT_ID,
                "doc": doc_id,
                "h": f"hash-{title}",
                "modified": datetime(2024, 3, 15, tzinfo=UTC),
            },
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO document_representations (representation_id,"
                " deployment_id, version_id, route, converter_name,"
                " converter_version, blockizer_version, markdown_uri, blocks_uri,"
                " conversion_uri, meta_uri, markdown_hash, manifest_hash, status)"
                " VALUES (:r, :d, :v, 'markdown', 'passthrough', '1', '1',"
                " :md, 'b', 'c', 'm', 'mh', 'sh', 'ready')"
            ),
            {
                "r": representation_id,
                "d": _DEPLOYMENT_ID,
                "v": version_id,
                "md": f"{doc_id}/document.md",
            },
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO document_structure_generations"
                " (structure_generation_id, deployment_id, doc_id, version_id,"
                " representation_id, skeleton_version, skeleton_hash,"
                " skeleton_producer_family, roles_version, summary_version,"
                " placement_version, route_tag, candidate_skeleton_hash,"
                " stats_version, stats)"
                " VALUES (:generation, :d, :doc, :v, :r, 'test-skeleton',"
                " :hash, 'N/A', 'test-role', 'test-summary', 'test-placement',"
                " 'parser', :hash, 'test-stats',"
                " jsonb_build_object('stats_version', 'test-stats',"
                " 'section_count', 0))"
            ),
            {
                "generation": structure_generation_id,
                "d": _DEPLOYMENT_ID,
                "doc": doc_id,
                "v": version_id,
                "r": representation_id,
                "hash": f"test-skeleton-{doc_id}",
            },
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO document_sections (section_id, deployment_id, doc_id,"
                " version_id, representation_id, structure_generation_id, node_path,"
                " block_start, block_end, title, role, char_start, char_end, ordinal, summary,"
                " placement_path, structurer_version)"
                " VALUES (:s, :d, :doc, :v, :r, :generation, '0', 0, 1, :title,"
                " 'body', 0, 10, 0, :summary, :placement, 'test')"
            ),
            {
                "s": uuid4(),
                "d": _DEPLOYMENT_ID,
                "doc": doc_id,
                "v": version_id,
                "r": representation_id,
                "generation": structure_generation_id,
                "title": title,
                "summary": summary,
                "placement": placement,
            },
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "UPDATE document_versions SET current_representation_id = :r"
                " WHERE version_id = :v"
            ),
            {"r": representation_id, "v": version_id},
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "UPDATE document_representations"
                " SET current_structure_generation_id = :generation"
                " WHERE representation_id = :r"
            ),
            {"generation": structure_generation_id, "r": representation_id},
        )
        connection.execute(  # type: ignore[attr-defined]
            text("UPDATE documents SET current_version_id = :v WHERE doc_id = :doc"),
            {"v": version_id, "doc": doc_id},
        )
        mention_id = uuid4()
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO mentions (mention_id, deployment_id, surface_form,"
                " normalized_lemma, doc_id) VALUES (:m, :d, 'Acme', 'acme', :doc)"
            ),
            {"m": mention_id, "d": _DEPLOYMENT_ID, "doc": doc_id},
        )
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO resolution_decisions (decision_id, deployment_id,"
                " mention_id, entity_id, method, confidence, resolver_version)"
                " VALUES (:id, :d, :m, :e, 'T0', 1.0, 'test')"
            ),
            {"id": uuid4(), "d": _DEPLOYMENT_ID, "m": mention_id, "e": self.entity_id},
        )
        return doc_id


@pytest.fixture()
def corpus(database_engine: Engine) -> _Corpus:
    """A fresh deployment carrying the seeded corpus."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
        for table in ("mentions", "resolution_decisions"):
            connection.execute(statement=text(f"TRUNCATE TABLE {table} CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="p3-test",
            name="P3 corpus filesystem proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    return _Corpus(engine=database_engine)


class _Tree:
    """Read a published snapshot the way an agent's mount would."""

    def __init__(self, *, store: LocalFSObjectStore, prefix: str) -> None:
        """Bind to one snapshot's prefix."""
        self._store = store
        self._prefix = prefix

    def read(self, path: str) -> str:
        """`cat` one file."""
        return self._store.read_bytes(key=ObjectKey(f"{self._prefix}/{path}")).decode()

    def paths(self) -> set[str]:
        """Every file in the snapshot (from its manifest)."""
        import json

        manifest = json.loads(self.read("MANIFEST.json"))
        return set(manifest["files"])


def _build(corpus: _Corpus, tmp_path: Path, **kwargs: object) -> tuple[_Tree, dict]:
    """Build one snapshot and return a reader plus the build result."""
    catalog = ProjectionCatalog(engine=corpus.engine)
    store = LocalFSObjectStore(root=tmp_path / "corpusfs")
    builder = CorpusFsBuilder(
        catalog=catalog,
        snapshot_store=store,
        settings=CorpusFsSettings(**kwargs),  # type: ignore[arg-type]
    )
    result = builder.build(deployment_id=_DEPLOYMENT_ID)
    latest = catalog.latest_snapshot(deployment_id=_DEPLOYMENT_ID, plane="P3_corpusfs")
    assert latest is not None
    return _Tree(store=store, prefix=str(latest["gcs_uri"])), result


def test_the_navigation_ladder_is_walkable(corpus: _Corpus, tmp_path: Path) -> None:
    """S44: root orientation → facet → directory member table → stub, each
    step one `cat`, and the member table tells an agent what every file is
    about without opening one."""
    tree, result = _build(corpus, tmp_path)
    assert result["published"] is True

    root = tree.read("llms.txt")
    assert "documents/" in root and "entities/" in root  # the durable tiers
    assert "by-source/" in root  # a view facet

    facet = tree.read("by-source/_index.md")
    assert "upload/" in facet  # the facet lists its subdirectories

    directory = tree.read("by-source/upload/_index.md")
    assert "3 document(s)" in directory
    # the load-bearing property: every member's meaning is IN the index
    assert "Acme's 2023 results." in directory
    assert "How the anvil is shaped." in directory
    assert "An unfiled memo." in directory

    stub_name = next(
        path.rsplit("/", 1)[1]
        for path in tree.paths()
        if path.startswith("by-source/upload/")
        and path.endswith(".md")
        and "annual-report" in path
    )
    stub = tree.read(f"by-source/upload/{stub_name}")
    assert 'canonical_path: "documents/' in stub  # every view stub names Tier 1
    assert "document.md" in stub  # and points at the artifact
    assert "raw_uri" in stub  # D51: raw is off-path, never unreachable


def test_tier_one_paths_survive_rebuilds(corpus: _Corpus, tmp_path: Path) -> None:
    """The path contract (F6): canonical document and entity leaves never
    move across rebuilds — even when the corpus grows and views reshuffle."""
    first, _ = _build(corpus, tmp_path)
    canonical = {
        path for path in first.paths() if path.startswith(("documents/", "entities/"))
    }
    assert f"documents/{corpus.docs['Annual Report']}/_index.md" in canonical
    assert any(str(corpus.entity_id) in path for path in canonical)

    with corpus.engine.begin() as connection:  # the corpus grows
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id, type,"
                " canonical_name, normalized_name)"
                " VALUES (:e, :d, 'Person', 'Wile E', 'wile e')"
            ),
            {"e": uuid4(), "d": _DEPLOYMENT_ID},
        )
    second, _ = _build(corpus, tmp_path / "again")
    assert canonical <= second.paths()  # every old Tier-1 path still there


def test_placement_hints_drive_topic_views(corpus: _Corpus, tmp_path: Path) -> None:
    """The structurer's placement hint (D39) becomes a topic view — and a
    document without one is still fully navigable."""
    tree, _ = _build(corpus, tmp_path)
    paths = tree.paths()
    assert any(
        path.startswith("by-topic/finance/annual-reports/2023/") for path in paths
    )
    assert any(path.startswith("by-topic/research/anvils/") for path in paths)
    # the unplaced memo appears in the source and time views regardless
    memo_stubs = [
        path for path in paths if "loose-memo" in path and path.endswith(".md")
    ]
    assert any(path.startswith("by-source/") for path in memo_stubs)
    assert any(path.startswith("by-time/2024/03/") for path in memo_stubs)


def test_entity_pages_carry_their_evidencing_documents(
    corpus: _Corpus, tmp_path: Path
) -> None:
    """`entities/<type>/<id>/_index.md`: profile plus the documents that
    mention it — the evidence side of understanding ↔ evidence."""
    tree, _ = _build(corpus, tmp_path)
    page = tree.read(f"entities/organization/{corpus.entity_id}/_index.md")
    assert "# Acme" in page
    assert "A manufacturer of anvils." in page
    assert "3 mention(s)" in page
    assert "annual-report" in page  # the member table lists its documents


def test_oversized_directories_shard_deterministically(
    corpus: _Corpus, tmp_path: Path
) -> None:
    """Bounded fan-out: a directory under the threshold stays flat, one
    above it shards — and a rebuild puts each document in the same shard."""
    flat, _ = _build(corpus, tmp_path / "flat")
    assert "by-source/upload/_index.md" in flat.paths()  # 3 members: no shards

    with corpus.engine.begin() as connection:  # grow past a low threshold
        for index in range(6):
            corpus._document(  # noqa: SLF001 — the fixture's own seeder
                connection,
                title=f"Bulk {index}",
                placement=None,
                summary=f"Bulk document {index}.",
            )
    sharded, _ = _build(corpus, tmp_path / "sharded", shard_threshold=4)
    shard_indexes = {
        path
        for path in sharded.paths()
        if path.startswith("by-source/upload/") and path.endswith("/_index.md")
    }
    assert len(shard_indexes) > 1  # the oversized view split
    again, _ = _build(corpus, tmp_path / "again", shard_threshold=4)
    assert sharded.paths() == again.paths()  # same document, same shard


def test_the_builder_calls_no_model(corpus: _Corpus, tmp_path: Path) -> None:
    """The projection builder is fully deterministic: two builds of an
    unchanged corpus render byte-identical trees (a directory-level LLM
    summary is a rejected alternative — that is a K page's job)."""
    first, _ = _build(corpus, tmp_path)
    second, _ = _build(corpus, tmp_path / "again")
    assert first.paths() == second.paths()
    for path in sorted(first.paths()):
        assert first.read(path) == second.read(path)


def test_every_level_carries_orientation(corpus: _Corpus, tmp_path: Path) -> None:
    """Codex review: e0 §6's contract is that EACH level carries an index —
    an intermediate directory named as a parent but missing its own index
    is a dead end in the navigation ladder."""
    tree, _ = _build(corpus, tmp_path)
    paths = tree.paths()
    directories = {path.rsplit("/", 1)[0] for path in paths if "/" in path}
    for directory in directories:
        assert f"{directory}/_index.md" in paths, f"{directory} has no index"
    # intermediates of a deep topic path exist in their own right
    assert "by-topic/finance/_index.md" in paths
    assert "by-topic/finance/annual-reports/_index.md" in paths
    # and the declared facet skeleton exists whether or not documents landed
    for facet in ("by-source", "by-time", "by-topic"):
        assert f"{facet}/_index.md" in paths
        assert f"{facet}/llms.txt" in paths


def test_tier_one_roots_carry_member_tables(corpus: _Corpus, tmp_path: Path) -> None:
    """Codex review: `documents/_index.md` and `entities/_index.md` are member
    tables, not bare counts — and every member row links to a path that
    actually exists."""
    tree, _ = _build(corpus, tmp_path)
    documents_index = tree.read("documents/_index.md")
    assert "| File |" in documents_index
    assert "Annual Report".lower().replace(" ", "-") in documents_index
    entities_index = tree.read("entities/_index.md")
    assert "| Entity |" in entities_index
    assert "Acme" in entities_index
    assert str(corpus.entity_id) in entities_index

    # an entity page's member rows link to canonical paths that resolve
    page = tree.read(f"entities/organization/{corpus.entity_id}/_index.md")
    assert "../../../documents/" in page  # a real relative link, not a dead name


def test_pathological_titles_and_refs_are_contained(
    corpus: _Corpus, tmp_path: Path
) -> None:
    """Codex review: one hostile or merely verbose document must not break a
    publish — long names are capped, and a source ref carrying a newline
    cannot inject or override frontmatter."""
    with corpus.engine.begin() as connection:
        corpus._document(  # noqa: SLF001 — the fixture's own seeder
            connection,
            title="X" * 300,
            placement="/" + "/".join("deep" for _ in range(20)) + "/",
            summary="A very long title.",
        )
        connection.execute(
            text("UPDATE documents SET source_ref = :ref WHERE title = :title"),
            {
                "ref": 'evil\ncanonical_path: "documents/attacker-controlled"',
                "title": "X" * 300,
            },
        )
    tree, _ = _build(corpus, tmp_path)
    # a slug caps at 60 chars; a stub adds "-<uuid>.md" — comfortably under
    # the 255-byte component limit common filesystems enforce
    assert all(len(part) <= 110 for path in tree.paths() for part in path.split("/"))
    # and a 20-deep placement hint is capped, not honored blindly
    assert all(path.count("/") <= 8 for path in tree.paths())
    hostile = next(
        tree.read(path)
        for path in tree.paths()
        if path.startswith("by-source/") and "xxxxx" in path
    )
    # the injected text survives as part of a JSON-escaped VALUE (harmless)
    # but never becomes a second frontmatter FIELD — only one line declares
    # canonical_path, and it names the real document
    field_lines = [
        line for line in hostile.splitlines() if line.startswith("canonical_path:")
    ]
    assert len(field_lines) == 1
    assert "attacker-controlled" not in field_lines[0]
    assert "\\n" in hostile  # the newline was escaped, not emitted raw


def test_non_current_generations_never_leak_into_the_tree(
    corpus: _Corpus, tmp_path: Path
) -> None:
    """P3 reads ONLY the representation's current generation: a second,
    non-current generation with a poison section must leave the built tree
    byte-identical (review: nothing proved the join excludes history)."""
    before, _ = _build(corpus, tmp_path / "before")
    with corpus.engine.begin() as connection:
        representation = connection.execute(
            text(
                "SELECT r.representation_id, r.version_id, v.doc_id"
                " FROM document_representations r"
                " JOIN document_versions v ON v.version_id = r.version_id LIMIT 1"
            )
        ).one()
        stale_generation = uuid4()
        connection.execute(
            text(
                "INSERT INTO document_structure_generations ("
                " structure_generation_id, deployment_id, doc_id, version_id,"
                " representation_id, skeleton_version, skeleton_hash,"
                " skeleton_producer_family, route_tag, candidate_skeleton_hash,"
                " stats_version, stats) VALUES ("
                " :generation, :d, :doc, :v, :r, 'stale-v', 'stale-hash', 'N/A',"
                " 'parser', 'stale-hash', 'stale-stats', CAST('{}' AS jsonb))"
            ),
            {
                "generation": stale_generation,
                "d": _DEPLOYMENT_ID,
                "doc": representation.doc_id,
                "v": representation.version_id,
                "r": representation.representation_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO document_sections (section_id, deployment_id, doc_id,"
                " version_id, representation_id, structure_generation_id, node_path,"
                " block_start, block_end, title, role, char_start, char_end,"
                " ordinal, structurer_version)"
                " VALUES (:s, :d, :doc, :v, :r, :generation, '0', 0, 1,"
                " 'POISON NON-CURRENT SECTION', 'body', 0, 10, 0, 'stale-v')"
            ),
            {
                "s": uuid4(),
                "d": _DEPLOYMENT_ID,
                "doc": representation.doc_id,
                "v": representation.version_id,
                "r": representation.representation_id,
                "generation": stale_generation,
            },
        )
    after, _ = _build(corpus, tmp_path / "after")
    assert before.paths() == after.paths()
    for path in sorted(after.paths()):
        content = after.read(path)
        assert "POISON" not in content
        assert after.read(path) == before.read(path)
