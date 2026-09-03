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
    _validate_postgres_release(root=root)
    if arguments.tag is not None:
        _validate_tag(tag=arguments.tag, version=version)
    print(f"release contract valid for RememberStack {version}")


def _parser() -> argparse.ArgumentParser:
    """Build the small release-contract command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tag",
        help="release tag to compare with the package version, for example v0.1.0",
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
        Path("website/src/app/docs/getting-started/page.mdx"): (image,),
        Path("website/src/app/docs/deployment/page.mdx"): (
            f"`v{version}` release",
            image,
        ),
        Path("website/src/app/docs/reference/cli/page.mdx"): (
            f"# RememberStack {version}",
        ),
        Path("website/src/app/docs/reference/api/page.mdx"): (
            f"Release v{version} and later",
            f"releases/download/v{version}/openapi.json",
        ),
        Path("website/src/app/docs/project-status/page.mdx"): (
            f"releases/tag/v{version}",
            f"rememberstack/{version}/",
        ),
    }
    for relative_path, expected_markers in markers.items():
        document = (root / relative_path).read_text(encoding="utf-8")
        for marker in expected_markers:
            if marker not in document:
                raise ValueError(
                    f"{relative_path} must contain release coordinate {marker!r}"
                )


def _validate_postgres_release(*, root: Path) -> None:
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
    if f"image: rememberstack-postgres:{base}" not in compose:
        raise ValueError(
            "Compose PostgreSQL source marker must match Dockerfile.postgres"
        )
    workflow = (root / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    for required in (
        "file: Dockerfile.postgres",
        "platforms: linux/amd64,linux/arm64",
        f"pattern={base}-{{{{version}}}}",
        "postgres-image-digests.json",
    ):
        if required not in workflow:
            raise ValueError(
                f"release workflow is missing PostgreSQL image contract {required!r}"
            )


if __name__ == "__main__":
    main()
