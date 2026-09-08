"""WP-7.6 acceptance tests for one release version across every artifact."""

import json
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest
from scripts.check_github_release import _required_assets
from scripts.check_pypi_release import _require_identical
from scripts.check_release_contract import _validate_release_docs
from scripts.prepare_next_release import _COORDINATE_FILES

_RELEASE_DOCS = (
    Path("README.md"),
    Path("website/src/app/docs/getting-started/page.mdx"),
    Path("website/src/app/docs/deployment/page.mdx"),
    Path("website/src/app/docs/reference/cli/page.mdx"),
    Path("website/src/app/docs/reference/api/page.mdx"),
    Path("website/src/app/docs/project-status/page.mdx"),
)


def test_release_contract_matches_package_compose_and_tag() -> None:
    """Accept the current package version, Compose image, and matching tag."""
    root = Path(__file__).resolve().parents[3]
    version = _project_version(root=root)
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "check_release_contract.py"),
            "--tag",
            f"v{version}",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert (
        result.stdout.strip() == f"release contract valid for RememberStack {version}"
    )


def test_release_contract_rejects_a_mismatched_tag() -> None:
    """Reject a tag that could publish PyPI and GHCR under different versions."""
    root = Path(__file__).resolve().parents[3]
    version = _project_version(root=root)
    invalid_tag = f"v{version}.invalid"
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "check_release_contract.py"),
            "--tag",
            invalid_tag,
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert f"release tag must be 'v{version}', found '{invalid_tag}'" in result.stderr


def test_release_workflow_is_main_only_and_derives_one_immutable_coordinate() -> None:
    """Publishing starts from protected main, never from a caller-created tag."""
    root = Path(__file__).resolve().parents[3]
    workflow = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "branches: [main]" in workflow
    assert 'tags: ["v*.*.*"]' not in workflow
    assert "group: remember-stack-release-main" in workflow
    assert 'test "$GITHUB_REF" = refs/heads/main' in workflow
    assert 'source_sha="$(git rev-parse HEAD)"' in workflow
    assert 'git merge-base --is-ancestor "$source_sha" FETCH_HEAD' in workflow
    assert "tag_name: ${{ needs.prepare.outputs.tag }}" in workflow
    assert "target_commitish: ${{ needs.prepare.outputs.source_sha }}" in workflow
    assert "GITHUB_REF_NAME" not in workflow
    assert "org.opencontainers.image.revision" in workflow
    assert "event_type=release-completed" in workflow


def test_release_preparation_uses_app_identity_and_waits_for_checks() -> None:
    """Bot pushes trigger normal checks and merge only after those checks pass."""
    root = Path(__file__).resolve().parents[3]
    workflow = (root / ".github/workflows/release-prepare.yml").read_text(
        encoding="utf-8"
    )
    assert "actions/create-github-app-token@" in workflow
    assert "REMEMBER_RELEASE_AUTOMATION_APP_ID" in workflow
    assert "REMEMBER_RELEASE_AUTOMATION_APP_PRIVATE_KEY" in workflow
    assert "if: github.ref == 'refs/heads/main'" in workflow
    assert "ref: main" in workflow
    assert "GH_TOKEN: ${{ steps.app-token.outputs.token }}" in workflow
    assert 'gh pr checks "$existing" --watch --fail-fast' in workflow
    assert 'gh pr merge "$existing" --squash' in workflow
    assert "types: [release-completed]" in workflow
    assert "uv lock --check" in workflow
    assert "git add pyproject.toml uv.lock" in workflow


def test_cla_has_an_explicit_release_automation_bot_contract() -> None:
    """The organization-owned App is exempted by exact login, not fake assent."""
    root = Path(__file__).resolve().parents[3]
    workflow = (root / ".github/workflows/cla.yml").read_text(encoding="utf-8")
    assert "REMEMBER_RELEASE_AUTOMATION_APP_BOT_LOGIN" in workflow
    assert "trusted_bots.add(automation_bot)" in workflow


def test_release_completion_revisits_changes_merged_during_publication() -> None:
    """A newer main tip neither changes source A nor strands later commit B."""
    root = Path(__file__).resolve().parents[3]
    publish = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    prepare = (root / ".github/workflows/release-prepare.yml").read_text(
        encoding="utf-8"
    )
    assert "ref: ${{ needs.prepare.outputs.source_sha }}" in publish
    assert 'git merge-base --is-ancestor "$source_sha" FETCH_HEAD' in publish
    assert '"${tagged}" != "$source_sha"' in publish
    assert "event_type=release-completed" in publish
    assert "repository_dispatch:" in prepare
    assert "types: [release-completed]" in prepare
    assert "refs/tags/v${current}" in prepare
    assert "scripts/check_unreleased_changes.py" in prepare


def test_release_publishes_immutable_image_receipts_to_umc() -> None:
    """UMC receives only release-attached image digests bound to one source."""
    root = Path(__file__).resolve().parents[3]
    workflow = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "application-image-digest.json" in workflow
    assert "postgres-image-digests.json" in workflow
    assert "source_revision:$revision" in workflow
    assert "actions/create-github-app-token@" in workflow
    assert "ultimate-memory-cloud" in workflow
    assert "event_type=remember-stack-release" in workflow
    assert "client_payload[source_revision]" in workflow
    assert "client_payload[application_digest]" in workflow
    assert "client_payload[postgres_digest]" in workflow
    assert ".github/ci/postgres-evidence.jq" in workflow


def test_postgres_receipt_excludes_buildx_attestation_descriptors() -> None:
    """Only runnable linux images enter the immutable PostgreSQL receipt."""
    root = Path(__file__).resolve().parents[3]
    payload = {
        "manifests": [
            {
                "digest": "sha256:amd",
                "platform": {"os": "linux", "architecture": "amd64"},
            },
            {
                "digest": "sha256:arm",
                "platform": {"os": "linux", "architecture": "arm64"},
            },
            {
                "digest": "sha256:att",
                "platform": {"os": "unknown", "architecture": "unknown"},
            },
        ]
    }
    result = subprocess.run(
        [
            "jq",
            "--arg",
            "image",
            "example.invalid/postgres:tag",
            "--arg",
            "manifest",
            "sha256:index",
            "-f",
            str(root / ".github/ci/postgres-evidence.jq"),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    evidence = json.loads(result.stdout)
    assert [item["digest"] for item in evidence["platforms"]] == [
        "sha256:amd",
        "sha256:arm",
    ]


def test_unreleased_change_gate_stops_loop_and_detects_later_commit(
    tmp_path: Path,
) -> None:
    """Release A emits no new PR; a later substantive commit B emits exactly one."""
    root = Path(__file__).resolve().parents[3]
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    marker = tmp_path / "source"
    marker.write_text("A", encoding="utf-8")
    subprocess.run(["git", "add", "source"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "tag", "v1.0.0"], cwd=tmp_path, check=True)

    command = [
        sys.executable,
        str(root / "scripts/check_unreleased_changes.py"),
        "--root",
        str(tmp_path),
        "--tag",
        "v1.0.0",
    ]
    assert (
        subprocess.run(
            command, check=True, capture_output=True, text=True
        ).stdout.strip()
        == "false"
    )
    marker.write_text("B", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "B"], cwd=tmp_path, check=True)
    assert (
        subprocess.run(
            command, check=True, capture_output=True, text=True
        ).stdout.strip()
        == "true"
    )


def test_release_contract_can_print_the_validated_version_for_ci() -> None:
    """The workflow consumes the validator's version rather than parsing TOML twice."""
    root = Path(__file__).resolve().parents[3]
    version = _project_version(root=root)
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/check_release_contract.py"),
            "--print-version",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == version


def test_github_release_completion_requires_every_attachment() -> None:
    """A matching tag alone cannot suppress recovery of a partial release upload."""
    required = _required_assets(version="0.17.0")
    assert "application-image-digest.json" in required
    assert "postgres-image-digests.json" in required
    assert "remember-0.17.0-py3-none-any.whl" in required
    assert "rememberstack-0.17.0-py3-none-any.whl" in required
    assert len(required - {"application-image-digest.json"}) > 0


def test_release_preparer_updates_only_canonical_root_coordinates(
    tmp_path: Path,
) -> None:
    """The generated PR advances a patch without changing the terminal shim."""
    root = Path(__file__).resolve().parents[3]
    (tmp_path / "pyproject.toml").write_text(
        (root / "pyproject.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    terminal = tmp_path / "packages/rememberstack/pyproject.toml"
    terminal.parent.mkdir(parents=True)
    terminal_document = (root / "packages/rememberstack/pyproject.toml").read_text(
        encoding="utf-8"
    )
    terminal.write_text(terminal_document, encoding="utf-8")
    for relative in _COORDINATE_FILES:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            (root / relative).read_text(encoding="utf-8"), encoding="utf-8"
        )
    current = _project_version(root=root)
    major, minor, patch = (int(part) for part in current.split("."))
    target = f"{major}.{minor}.{patch + 1}"
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/prepare_next_release.py"),
            "--root",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == target
    assert _project_version(root=tmp_path) == target
    assert terminal.read_text(encoding="utf-8") == terminal_document
    for relative in _COORDINATE_FILES:
        assert current not in (tmp_path / relative).read_text(encoding="utf-8")


def test_release_lock_regeneration_works_with_an_empty_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regenerate the root coordinate offline without cached optional dependencies."""
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "empty-cache"))
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "remember"\nversion = "1.2.3"\n'
        'requires-python = ">=3.12"\ndependencies = []\n',
        encoding="utf-8",
    )
    for relative in _COORDINATE_FILES:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("Release 1.2.3\n", encoding="utf-8")
    subprocess.run(["uv", "lock", "--offline"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            sys.executable,
            str(root / "scripts/prepare_next_release.py"),
            "--root",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["uv", "lock", "--offline"], cwd=tmp_path, check=True)
    subprocess.run(["uv", "lock", "--check", "--offline"], cwd=tmp_path, check=True)
    lock = (tmp_path / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "remember"\nversion = "1.2.4"' in lock


def test_existing_pypi_release_must_be_byte_identical() -> None:
    """Partial recovery uploads missing files and rejects conflicting content."""
    payload: dict[str, object] = {
        "urls": [{"filename": "remember.whl", "digests": {"sha256": "abc"}}]
    }
    assert _require_identical(
        local={"remember.whl": "abc", "remember.tar.gz": "def"}, payload=payload
    ) == ("remember.tar.gz",)
    with pytest.raises(RuntimeError, match="differs"):
        _require_identical(local={"remember.whl": "def"}, payload=payload)
    with pytest.raises(RuntimeError, match="unexpected"):
        _require_identical(local={"other.whl": "abc"}, payload=payload)


def test_release_contract_rejects_a_stale_document_coordinate(tmp_path: Path) -> None:
    """Reject a public document that advertises a different release."""
    root = Path(__file__).resolve().parents[3]
    version = _project_version(root=root)
    for relative_path in _RELEASE_DOCS:
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            (root / relative_path).read_text(encoding="utf-8"), encoding="utf-8"
        )

    cli_reference = tmp_path / "website/src/app/docs/reference/cli/page.mdx"
    cli_reference.write_text(
        cli_reference.read_text(encoding="utf-8").replace(
            f"`remember` CLI (v{version}", "`remember` CLI (v0.0.0", 1
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as error:
        _validate_release_docs(root=tmp_path, version=version)
    assert str(error.value) == (
        "website/src/app/docs/reference/cli/page.mdx must contain release "
        f"coordinate '`remember` CLI (v{version}'"
    )


def _project_version(*, root: Path) -> str:
    """Read the package version independently from the release checker process."""
    with (root / "pyproject.toml").open("rb") as pyproject:
        document = tomllib.load(pyproject)
    project = document.get("project")
    assert isinstance(project, dict)
    version = project.get("version")
    assert isinstance(version, str)
    return version
