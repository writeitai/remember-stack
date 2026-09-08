"""Refuse to overwrite a PyPI version unless every artifact is byte-identical."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen


def main() -> None:
    """Print missing or identical after comparing the complete file set."""
    arguments = _parser().parse_args()
    url = f"https://pypi.org/pypi/{arguments.project}/{arguments.version}/json"
    try:
        with urlopen(url, timeout=30) as response:  # noqa: S310 -- fixed PyPI origin
            payload = json.load(response)
    except HTTPError as error:
        if error.code == 404:
            print("missing")
            return
        raise
    local = {
        path.name: sha256(path.read_bytes()).hexdigest()
        for path in sorted(arguments.dist.iterdir())
        if path.is_file()
    }
    _require_identical(local=local, payload=payload)
    print("identical")


def _require_identical(*, local: dict[str, str], payload: dict[str, object]) -> None:
    """Require PyPI's complete filename/hash set to equal verified local files."""
    urls = payload.get("urls")
    if not isinstance(urls, list):
        raise RuntimeError("PyPI release response omitted artifact URLs")
    remote: dict[str, str] = {}
    for item in urls:
        if not isinstance(item, dict) or not isinstance(item.get("digests"), dict):
            raise RuntimeError("PyPI release response has invalid artifact metadata")
        filename = item.get("filename")
        digest = item["digests"].get("sha256")
        if not isinstance(filename, str) or not isinstance(digest, str):
            raise RuntimeError("PyPI release response has invalid artifact identity")
        remote[filename] = digest
    if local != remote:
        raise RuntimeError("existing PyPI release differs from verified distributions")


def _parser() -> argparse.ArgumentParser:
    """Build the exact-coordinate comparison command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist", required=True, type=Path)
    return parser


if __name__ == "__main__":
    main()
