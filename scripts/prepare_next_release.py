"""Advance canonical RememberStack release coordinates by one patch version."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import tomllib

_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_COORDINATE_FILES = (
    Path("README.md"),
    Path("compose.yaml"),
    Path("website/src/app/docs/getting-started/page.mdx"),
    Path("website/src/app/docs/deployment/page.mdx"),
    Path("website/src/app/docs/reference/cli/page.mdx"),
    Path("website/src/app/docs/reference/api/page.mdx"),
    Path("website/src/app/docs/project-status/page.mdx"),
)


def main() -> None:
    """Update the root package and public release coordinates atomically."""
    arguments = _parser().parse_args()
    root = arguments.root.resolve()
    current = _current_version(root=root)
    major, minor, patch = _parts(version=current)
    target = f"{major}.{minor}.{patch + 1}"
    _replace_once(
        path=root / "pyproject.toml",
        before=f'version = "{current}"',
        after=f'version = "{target}"',
    )
    for relative in _COORDINATE_FILES:
        path = root / relative
        document = path.read_text(encoding="utf-8")
        updated = document.replace(current, target)
        if updated == document:
            raise ValueError(f"{relative} has no {current} release coordinate")
        path.write_text(updated, encoding="utf-8")
    print(target)


def _parser() -> argparse.ArgumentParser:
    """Build the release preparation command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return parser


def _current_version(*, root: Path) -> str:
    """Read the root distribution version without touching the terminal shim."""
    with (root / "pyproject.toml").open("rb") as pyproject:
        project = tomllib.load(pyproject).get("project")
    if not isinstance(project, dict) or not isinstance(project.get("version"), str):
        raise TypeError("project.version must be a string")
    version = project["version"]
    _parts(version=version)
    return version


def _parts(*, version: str) -> tuple[int, int, int]:
    """Parse the deliberately narrow stable semantic-version vocabulary."""
    match = _VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"invalid stable semantic version {version!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _replace_once(*, path: Path, before: str, after: str) -> None:
    """Replace exactly one authoritative scalar and reject ambiguous input."""
    document = path.read_text(encoding="utf-8")
    if document.count(before) != 1:
        raise ValueError(f"{path} must contain exactly one {before!r}")
    path.write_text(document.replace(before, after), encoding="utf-8")


if __name__ == "__main__":
    main()
