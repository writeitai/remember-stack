"""Render a protocol's allowlisted, non-secret ingestion environment."""

import argparse

from benchmarks.locomo.protocol import protocol_for_key
from benchmarks.locomo.protocol import PROTOCOL_REGISTRY


def main() -> None:
    """Print literal export values without shell evaluation or credentials."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("protocol", choices=tuple(PROTOCOL_REGISTRY))
    args = parser.parse_args()
    profile = protocol_for_key(args.protocol).execution_profile
    for name, value in sorted(profile.ingest_environment.items()):
        if not name.startswith("REMEMBERSTACK_") or any(c in value for c in "\r\n\x00"):
            raise ValueError("unsafe non-secret profile environment")
        print(f"{name}={value}")


if __name__ == "__main__":
    main()
