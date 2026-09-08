"""Refuse to overwrite a PyPI version unless every artifact is byte-identical."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
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
    prefix = arguments.project.replace("-", "_") + "-"
    local = {
        path.name: sha256(path.read_bytes()).hexdigest()
        for path in sorted(arguments.dist.iterdir())
        if path.is_file() and path.name.startswith(prefix)
    }
    if not local:
        raise RuntimeError(f"no local distributions found for {arguments.project}")
    missing = _require_identical(local=local, payload=payload)
    if arguments.missing_dir is not None:
        arguments.missing_dir.mkdir(parents=True, exist_ok=True)
        for filename in missing:
            shutil.copy2(arguments.dist / filename, arguments.missing_dir / filename)
    print("identical" if not missing else "partial")


def _require_identical(
    *, local: dict[str, str], payload: dict[str, object]
) -> tuple[str, ...]:
    """Validate every existing file and return verified local files still missing."""
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
    unexpected = sorted(set(remote) - set(local))
    conflicts = sorted(
        filename
        for filename, digest in remote.items()
        if filename in local and local[filename] != digest
    )
    if unexpected or conflicts:
        raise RuntimeError(
            "existing PyPI release differs from verified distributions: "
            f"unexpected={unexpected}, conflicts={conflicts}"
        )
    return tuple(sorted(set(local) - set(remote)))


def _parser() -> argparse.ArgumentParser:
    """Build the exact-coordinate comparison command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist", required=True, type=Path)
    parser.add_argument("--missing-dir", type=Path)
    return parser


if __name__ == "__main__":
    main()
