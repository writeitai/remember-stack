"""Validate the single version shared by RememberStack release artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import tomllib

_SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
_IMAGE = "ghcr.io/writeitai/remember-stack"
_POSTGRES_SOURCE_MARKER = re.compile(
    r"^FROM postgres:(19(?:beta\d+|rc\d+|(?:\.\d+)*))-"
)


def main() -> None:
    """Validate the repository release contract and an optional Git tag."""
    arguments = _parser().parse_args()
    root = Path(__file__).resolve().parents[1]
    version = _package_version(root=root)
    _validate_semver(version=version)
    _validate_compose_pin(root=root, version=version)
    _validate_release_docs(root=root, version=version)
    _validate_postgres_release(root=root, version=version)
    _validate_engine_image_release(root=root)
    _validate_terminal_package_release(root=root)
    if arguments.tag is not None:
        _validate_tag(tag=arguments.tag, version=version)
    if arguments.print_version:
        print(version)
    else:
        print(f"release contract valid for RememberStack {version}")


def _parser() -> argparse.ArgumentParser:
    """Build the small release-contract command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tag",
        help="release tag to compare with the package version, for example v0.1.0",
    )
    parser.add_argument(
        "--print-version",
        action="store_true",
        help="print only the validated package version",
    )
    return parser


def _package_version(*, root: Path) -> str:
    """Read the authoritative distribution version from pyproject.toml."""
    with (root / "pyproject.toml").open("rb") as pyproject:
        document = tomllib.load(pyproject)
    project = document.get("project")
    if not isinstance(project, dict):
        raise TypeError("project must be a table")
    version = project.get("version")
    if not isinstance(version, str):
        raise TypeError("project.version must be a string")
    return version


def _validate_semver(*, version: str) -> None:
    """Require the deliberately small MAJOR.MINOR.PATCH release vocabulary."""
    if _SEMVER.fullmatch(version) is None:
        raise ValueError(
            f"project.version must be semantic MAJOR.MINOR.PATCH, found {version!r}"
        )


def _validate_compose_pin(*, root: Path, version: str) -> None:
    """Require Compose to name the same fixed release coordinate as PyPI."""
    expected = f"image: {_IMAGE}:{version}"
    compose = (root / "compose.yaml").read_text(encoding="utf-8")
    matches = [
        line.strip()
        for line in compose.splitlines()
        if line.strip().startswith("image:")
    ]
    if expected not in matches:
        raise ValueError(f"compose.yaml must contain {expected!r}")


def _validate_tag(*, tag: str, version: str) -> None:
    """Require a release tag to be exactly v plus the package version."""
    expected = f"v{version}"
    if tag != expected:
        raise ValueError(f"release tag must be {expected!r}, found {tag!r}")


def _validate_release_docs(*, root: Path, version: str) -> None:
    """Keep public version claims on the same coordinate as the artifacts."""
    image = f"ghcr.io/writeitai/remember-stack:{version}"
    markers = {
        Path("README.md"): (
            f"[v{version}](https://github.com/writeitai/remember-stack/releases/tag/v{version})",
        ),
        Path("website/src/app/docs/start/quickstart/page.mdx"): (
            f"releases/tag/v{version}",
            f"remember/{version}/",
        ),
        Path("website/src/app/docs/self-hosting/install/page.mdx"): (image,),
        Path("website/src/app/docs/self-hosting/requirements/page.mdx"): (image,),
        Path("website/src/app/docs/reference/cli/page.mdx"): (
            f"`remember` CLI (v{version}",
        ),
        Path("website/src/app/docs/reference/http-api/page.mdx"): (
            f"releases/download/v{version}/openapi.json",
        ),
    }
    for relative_path, expected_markers in markers.items():
        document = (root / relative_path).read_text(encoding="utf-8")
        for marker in expected_markers:
            if marker not in document:
                raise ValueError(
                    f"{relative_path} must contain release coordinate {marker!r}"
                )


def _validate_postgres_release(*, root: Path, version: str) -> None:
    """Bind Compose to the multi-architecture immutable image publisher."""
    dockerfile = (root / "Dockerfile.postgres").read_text(encoding="utf-8")
    base = next(
        (
            match.group(1)
            for line in dockerfile.splitlines()
            if (match := _POSTGRES_SOURCE_MARKER.match(line)) is not None
        ),
        None,
    )
    if base is None:
        raise ValueError("Dockerfile.postgres must pin a PostgreSQL 19 source marker")
    compose = (root / "compose.yaml").read_text(encoding="utf-8")
    if f"image: {_IMAGE}-postgres:{base}-{version}" not in compose:
        raise ValueError(
            "Compose must name the published PostgreSQL image "
            f"{_IMAGE}-postgres:{base}-{version}"
        )
    workflow = (root / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    for required in (
        "file: Dockerfile.postgres",
        "platforms: linux/amd64,linux/arm64",
        f"type=raw,value={base}-${{{{ needs.prepare.outputs.version }}}}",
        "postgres-image-digests.json",
        "docker/setup-qemu-action@49b3bc8e6bdd4a60e6116a5414239cba5943d3cf",
        "docker/setup-buildx-action@b5ca514318bd6ebac0fb2aedd5d36ec1b5c232a2",
    ):
        if required not in workflow:
            raise ValueError(
                f"release workflow is missing PostgreSQL image contract {required!r}"
            )


def _validate_engine_image_release(*, root: Path) -> None:
    """Require the API/worker image to publish for both supported architectures."""
    workflow = (root / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    start, end = "\n  publish-ghcr:\n", "\n  publish-postgres-ghcr:\n"
    if start not in workflow or end not in workflow:
        raise ValueError("release workflow must keep the publish-ghcr job")
    job = workflow.split(start, maxsplit=1)[1].split(end, maxsplit=1)[0]
    for required in (
        "platforms: linux/amd64,linux/arm64",
        "docker/setup-qemu-action@49b3bc8e6bdd4a60e6116a5414239cba5943d3cf",
        'sort == ["amd64", "arm64"]',
    ):
        if required not in job:
            raise ValueError(
                f"release workflow is missing engine image contract {required!r}"
            )


def _validate_terminal_package_release(*, root: Path) -> None:
    """Validate the one-time, non-public transition package contract (D108)."""
    with (root / "pyproject.toml").open("rb") as pyproject:
        canonical_document = tomllib.load(pyproject)
    canonical_scripts = canonical_document.get("project", {}).get("scripts", {})
    if "rememberstack" in canonical_scripts:
        raise ValueError(
            "the canonical remember distribution must not install a rememberstack command"
        )

    terminal_pyproject = root / "packages" / "rememberstack" / "pyproject.toml"
    if not terminal_pyproject.is_file():
        raise ValueError(f"missing terminal package manifest: {terminal_pyproject}")
    with terminal_pyproject.open("rb") as pyproject:
        document = tomllib.load(pyproject)
    project = document.get("project", {})
    version = project.get("version")
    if version != "0.17.0":
        raise ValueError(
            f"packages/rememberstack version must remain terminal 0.17.0, found {version!r}"
        )
    deps = project.get("dependencies", [])
    if "remember>=0.17.0" not in deps:
        raise ValueError("packages/rememberstack must depend on 'remember>=0.17.0'")
    classifiers = project.get("classifiers", [])
    if "Development Status :: 7 - Inactive" not in classifiers:
        raise ValueError("packages/rememberstack must be marked inactive")

    workflow = (root / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    if 'if [ "${{ needs.prepare.outputs.tag }}" = "v0.17.0" ]; then' not in workflow:
        raise ValueError(
            "release workflow must gate packages/rememberstack build strictly on v0.17.0"
        )
    if "skip-existing: true" not in workflow:
        raise ValueError(
            "release workflow publish-pypi step must set skip-existing: true"
        )
    for required in (
        "name: remember ${{ needs.prepare.outputs.version }}",
        "dist/remember-*",
        "fail_on_unmatched_files: true",
    ):
        if required not in workflow:
            raise ValueError(
                f"release workflow is missing canonical remember presentation {required!r}"
            )
    github_release = workflow.split("github-release:", maxsplit=1)[1]
    if "dist/*" in github_release or "dist/rememberstack" in github_release:
        raise ValueError(
            "GitHub releases must not expose the terminal rememberstack distribution"
        )


if __name__ == "__main__":
    main()
