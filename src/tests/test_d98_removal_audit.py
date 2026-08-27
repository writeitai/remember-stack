"""Repository-level clean-cut gates for the D98 graph replacement."""

import hashlib
from pathlib import Path
import tomllib

_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME = _ROOT / "src" / "rememberstack"
_CURRENT_PRODUCT_PROSE = (
    _ROOT / "README.md",
    _ROOT / "RELEASING.md",
    _ROOT / "benchmarks" / "locomo" / "README.md",
    _ROOT / "benchmarks" / "locomo" / "sharding" / "README.md",
    *sorted((_ROOT / "website" / "src" / "app" / "docs").rglob("*.mdx")),
)
_REMOVED_PUBLIC_GRAPH_TERMS = (
    "/query/cypher",
    "query_cypher",
    "explain_cypher",
    "PROJECT_GRAPH_CYPHER",
    "LadybugDB",
    "P2 snapshot",
    "P2 graph",
    "graph built_at",
    "graph generation",
    "PostgreSQL 18",
    "topic/community pages",
    "community pages",
    "full-v13",
    "RS-LoCoMo-Full-v13",
)
_PG_TEXTSEARCH_REVISION = "578ff529894992fb9e67cae4c69424e65c84868e"
_PG_TEXTSEARCH_SOURCE_SHA256 = (
    "8632f91231251dc3e19395ef6a0d4d158d5f5920ba420691471771418e2a7cc7"
)
_PG_TEXTSEARCH_PATCH_SHA256 = (
    "a8c97f39714ab0193c82fcda3709d3e4df54bcc7f2804fde8f970710484dbdc6"
)
_PG_TEXTSEARCH_LICENSE_SHA256 = (
    "d33de21a123ce25b41722a5d10750984cb9c844c4d9b01add9e1b31f3ff452e5"
)
_PG_TEXTSEARCH_NOTICE_SHA256 = (
    "ff70cf4336c579957368a71c6b6b66ee8954011deef2b3d2c7a11f931080851d"
)


def test_removed_graph_runtime_modules_and_dependencies_do_not_return() -> None:
    """Ladybug, public Cypher, and P2 runtime artifacts stay physically absent."""
    runtime_paths = {
        path.relative_to(_RUNTIME).as_posix().lower()
        for path in _RUNTIME.rglob("*")
        if path.suffix == ".py" and "migrations/versions" not in path.as_posix()
    }
    assert not any("ladybug" in path or "cypher" in path for path in runtime_paths)
    assert "workers/p2.py" not in runtime_paths
    assert "workers/p2_analytics.py" not in runtime_paths

    dependencies = (_ROOT / "pyproject.toml").read_text(encoding="utf-8") + (
        _ROOT / "uv.lock"
    ).read_text(encoding="utf-8")
    assert "ladybug" not in dependencies.lower()


def test_active_runtime_has_no_p2_serving_vocabulary() -> None:
    """Current Python sources cannot describe P2 as a serving dependency."""
    forbidden = ("P2_graph", "P2 then", "hot P2", "discipline as P2")
    offenders: list[str] = []
    for path in _RUNTIME.rglob("*.py"):
        if "migrations/versions" in path.as_posix():
            continue
        body = path.read_text(encoding="utf-8")
        if any(token in body for token in forbidden):
            offenders.append(path.relative_to(_ROOT).as_posix())
    assert offenders == []


def test_current_product_prose_has_no_removed_graph_contracts() -> None:
    """Public/current docs cannot revive a removed graph surface or product."""
    offenders: list[str] = []
    for path in _CURRENT_PRODUCT_PROSE:
        body = path.read_text(encoding="utf-8")
        for term in _REMOVED_PUBLIC_GRAPH_TERMS:
            if term in body:
                offenders.append(f"{path.relative_to(_ROOT)}: {term}")
    assert offenders == []


def test_current_locomo_code_has_no_retired_protocol_identity() -> None:
    """Executable benchmark code cannot silently revive the removed v13 key."""
    code = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((_ROOT / "benchmarks" / "locomo").rglob("*.py"))
    )
    assert "full-v13" not in code
    assert "RS-LoCoMo-Full-v13" not in code
    assert "_FULL_V13" not in code


def test_postgres_image_verifies_extension_source_patch_and_license() -> None:
    """The prerelease image binds pg_textsearch inputs and redistributes notices."""
    dockerfile = (_ROOT / "Dockerfile.postgres").read_text(encoding="utf-8")
    assert f"PG_TEXTSEARCH_REVISION={_PG_TEXTSEARCH_REVISION}" in dockerfile
    assert f"PG_TEXTSEARCH_SOURCE_SHA256={_PG_TEXTSEARCH_SOURCE_SHA256}" in dockerfile
    assert f"PG_TEXTSEARCH_PATCH_SHA256={_PG_TEXTSEARCH_PATCH_SHA256}" in dockerfile
    assert f"PG_TEXTSEARCH_LICENSE_SHA256={_PG_TEXTSEARCH_LICENSE_SHA256}" in dockerfile
    assert f"PG_TEXTSEARCH_NOTICE_SHA256={_PG_TEXTSEARCH_NOTICE_SHA256}" in dockerfile
    assert dockerfile.count("sha256sum --check --strict") == 4
    assert "pg_textsearch uses the PostgreSQL License" in dockerfile
    assert "/usr/share/doc/pg_textsearch/LICENSE" in dockerfile
    assert "/usr/share/doc/pg_textsearch/NOTICE" in dockerfile
    assert "/usr/share/doc/pg_textsearch/artifacts.sha256" in dockerfile
    assert dockerfile.count("apt-archive.postgresql.org/pub/repos/apt") == 6
    assert dockerfile.count("s|http://apt.postgresql.org/pub/repos/apt|") == 2
    assert dockerfile.count("s|https://apt.postgresql.org/pub/repos/apt|") == 2
    assert dockerfile.count("trixie-pgdg-archive") == 2
    runtime_stage = dockerfile.split("FROM postgres:19beta3-trixie@", maxsplit=2)[2]
    assert "apt-archive.postgresql.org/pub/repos/apt" in runtime_stage
    assert runtime_stage.index("trixie-pgdg-archive") < runtime_stage.index(
        "postgresql-19-partman="
    )

    patch_path = _ROOT / "docker" / "pg_textsearch-pg19.patch"
    assert hashlib.sha256(patch_path.read_bytes()).hexdigest() == (
        _PG_TEXTSEARCH_PATCH_SHA256
    )

    provenance = (_ROOT / "docker" / "README.md").read_text(encoding="utf-8")
    for expected in (
        _PG_TEXTSEARCH_REVISION,
        _PG_TEXTSEARCH_SOURCE_SHA256,
        _PG_TEXTSEARCH_PATCH_SHA256,
        _PG_TEXTSEARCH_LICENSE_SHA256,
        _PG_TEXTSEARCH_NOTICE_SHA256,
    ):
        assert expected in provenance
    normalized_provenance = " ".join(provenance.split())
    assert (
        "**The PostgreSQL License** (`PostgreSQL` identifier)" in normalized_provenance
    )


def test_uv_lock_and_automation_share_one_tool_version() -> None:
    """Local, CI, nightly, and release locking must use one uv version."""
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    required_version = pyproject["tool"]["uv"]["required-version"]
    assert required_version == "==0.12.6"
    assert "revision = 3" in (_ROOT / "uv.lock").read_text(encoding="utf-8")

    for relative_path in (
        ".github/workflows/ci.yml",
        ".github/workflows/ci-nightly.yml",
        ".github/workflows/release.yml",
    ):
        workflow = (_ROOT / relative_path).read_text(encoding="utf-8")
        setup_count = workflow.count("uses: astral-sh/setup-uv@v5")
        assert setup_count > 0
        assert workflow.count('version: "0.12.6"') == setup_count
