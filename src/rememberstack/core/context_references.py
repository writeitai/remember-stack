"""Bounded generic context references on normalized assertions (D123).

The normalizer may name ordinary entities the claim refers to besides the
subject (and besides a relation's object). The first four original array
slots are attempted. Extra items are truncation, recorded from the frozen
list; they must not reject the assertion or prove novelty. Name equality is
not entity identity: resolved canonical ids are deduplicated after ordinary
resolution, and original ordinals are preserved.
"""

from __future__ import annotations

from typing import Final

from rememberstack.model.relations import EntityRef

MAX_CONTEXT_REFS: Final = 4


def attempted_context_refs(
    *, refs: tuple[EntityRef, ...]
) -> tuple[tuple[tuple[int, EntityRef], ...], bool]:
    """Keep original ordinals of the first four emitted references.

    Truncation is whether the frozen list is longer than the bound. Equal
    names stay distinct until ordinary resolution assigns entity ids.
    """
    truncated = len(refs) > MAX_CONTEXT_REFS
    return (
        tuple((index, ref) for index, ref in enumerate(refs[:MAX_CONTEXT_REFS])),
        truncated,
    )
