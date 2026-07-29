"""Bounded D79 section-summary orientation shared by E1 and E2."""

from typing import Final

from rememberstack.model import SectionSpan

SECTION_ORIENTATION_MAX_CHARS: Final = 2_048
"""Hard cap for the complete target + ancestor summary rendering."""

SECTION_ORIENTATION_VERSION: Final = (
    "d79-section-orientation-v1:"
    f"max-chars{SECTION_ORIENTATION_MAX_CHARS}:target-first:unicode-ellipsis"
)
"""Pins ordering and truncation semantics for E1/E2 provenance versions."""


def render_section_orientation(
    *, sections: tuple[SectionSpan, ...], target_path: str
) -> str | None:
    """Render non-null target/ancestor summaries under one hard character cap.

    The target is first so the most local orientation cannot be crowded out by
    a deep ancestor chain. Ancestors then run nearest-first. Missing summaries
    contribute no line; an entirely degraded generation returns ``None``.
    """
    by_path = {section.node_path: section for section in sections}
    paths = _target_and_ancestor_paths(target_path=target_path)
    lines = tuple(
        f"{'TARGET' if index == 0 else 'ANCESTOR'} {path}: {summary}"
        for index, path in enumerate(paths)
        if (section := by_path.get(path)) is not None
        and (summary := _one_line(section.summary)) is not None
    )
    if not lines:
        return None
    return _bounded_lines(lines=lines)


def _target_and_ancestor_paths(*, target_path: str) -> tuple[str, ...]:
    """Return the target followed by its materialized-path ancestors."""
    paths = [target_path]
    while "." in paths[-1]:
        paths.append(paths[-1].rsplit(".", 1)[0])
    return tuple(paths)


def _one_line(value: str | None) -> str | None:
    """Normalize defensive legacy values; D79 outputs are already one-line."""
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _bounded_lines(*, lines: tuple[str, ...]) -> str:
    """Join full lines until the cap, ellipsizing the final fitting fragment."""
    rendered: list[str] = []
    used = 0
    for line in lines:
        separator_chars = 1 if rendered else 0
        remaining = SECTION_ORIENTATION_MAX_CHARS - used - separator_chars
        if remaining <= 0:
            break
        if len(line) <= remaining:
            rendered.append(line)
            used += separator_chars + len(line)
            continue
        rendered.append(_ellipsize(value=line, max_chars=remaining))
        break
    return "\n".join(rendered)


def _ellipsize(*, value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    if max_chars <= 1:
        return "…"[:max_chars]
    return value[: max_chars - 1] + "…"
