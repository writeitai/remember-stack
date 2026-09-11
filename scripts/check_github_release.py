"""Classify whether a GitHub release has every required immutable attachment."""

from __future__ import annotations

import argparse
import json
import sys


def main() -> None:
    """Print complete or incomplete for the supplied release asset inventory."""
    arguments = _parser().parse_args()
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict) or not isinstance(payload.get("assets"), list):
        raise TypeError("GitHub release response must contain an assets list")
    names = {
        item.get("name")
        for item in payload["assets"]
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    required = _required_assets(version=arguments.version)
    print("complete" if required <= names else "incomplete")


def _required_assets(*, version: str) -> set[str]:
    """Return exact release asset names for one canonical distribution version."""
    required = {
        f"remember-{version}-py3-none-any.whl",
        f"remember-{version}.tar.gz",
        "application-image-digest.json",
        "postgres-image-digests.json",
        "compose.yaml",
        "default.env.example",
        "openapi.json",
    }
    if version == "0.17.0":
        required.update(
            {"rememberstack-0.17.0-py3-none-any.whl", "rememberstack-0.17.0.tar.gz"}
        )
    return required


def _parser() -> argparse.ArgumentParser:
    """Build the release attachment classifier CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    return parser


if __name__ == "__main__":
    main()
