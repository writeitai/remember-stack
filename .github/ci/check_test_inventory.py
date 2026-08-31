#!/usr/bin/env python3
"""Fail if test inventory drifts: every test file in unit XOR integration, every path exists."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "src" / "tests"
UNIT = ROOT / ".github" / "ci" / "unit-paths.txt"
INTEG = ROOT / ".github" / "ci" / "integration-paths.txt"

# Soft PR lanes must be able to reach every integration file via these prefixes.
SOFT_PREFIXES = (
    "src/tests/workers/",
    "src/tests/spine/",
    "src/tests/surfaces/",
    "src/tests/eval/",
    "src/tests/adapters/",
    "src/tests/profiles/",
    "src/tests/spikes/",
)


def _load(path: Path) -> list[str]:
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return lines


def main() -> int:
    unit = _load(UNIT)
    integ = _load(INTEG)
    errors: list[str] = []

    unit_set = set(unit)
    integ_set = set(integ)
    overlap = sorted(unit_set & integ_set)
    if overlap:
        errors.append("paths in both unit and integration:\n  " + "\n  ".join(overlap))

    for label, paths in (("unit", unit), ("integration", integ)):
        missing = [p for p in paths if not (ROOT / p).is_file()]
        if missing:
            errors.append(f"{label} lists missing files:\n  " + "\n  ".join(missing))

    discovered = sorted(
        str(path.relative_to(ROOT)) for path in TESTS.rglob("test_*.py")
    )
    covered = unit_set | integ_set
    orphans = [p for p in discovered if p not in covered]
    if orphans:
        errors.append("test files not in any inventory:\n  " + "\n  ".join(orphans))

    unreachable = [
        p for p in integ if not any(p.startswith(prefix) for prefix in SOFT_PREFIXES)
    ]
    if unreachable:
        errors.append(
            "integration files unreachable by soft PR lanes:\n  "
            + "\n  ".join(unreachable)
        )

    if errors:
        print("test inventory check FAILED:\n")
        print("\n\n".join(errors))
        return 1
    print(
        f"test inventory OK: unit={len(unit)} integration={len(integ)} "
        f"discovered={len(discovered)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
