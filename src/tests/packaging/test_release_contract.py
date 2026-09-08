"""WP-7.6 acceptance tests for one release version across every artifact."""

from pathlib import Path
import subprocess
import sys
import tomllib

import pytest
from scripts.check_release_contract import _validate_release_docs

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
    assert 'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"' in workflow
    assert "tag_name: ${{ needs.prepare.outputs.tag }}" in workflow
    assert "target_commitish: ${{ github.sha }}" in workflow
    assert "GITHUB_REF_NAME" not in workflow


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
