"""Report whether main contains commits after an immutable release tag."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess


def main() -> None:
    """Print true only when HEAD is a strict descendant of the release tag."""
    arguments = _parser().parse_args()
    released = _git(
        root=arguments.root, arguments=("rev-list", "-n", "1", arguments.tag)
    )
    head = _git(root=arguments.root, arguments=("rev-parse", "HEAD"))
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", released, head],
        cwd=arguments.root,
        check=True,
    )
    print("true" if released != head else "false")


def _parser() -> argparse.ArgumentParser:
    """Build the immutable-tag comparison command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--tag", required=True)
    return parser


def _git(*, root: Path, arguments: tuple[str, ...]) -> str:
    """Run one read-only Git identity query."""
    return subprocess.run(
        ["git", *arguments], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


if __name__ == "__main__":
    main()
