"""Extract eligibility: which strings may become entity names (D95 WP-I.1).

Bare head nouns are not referents. ``game`` is not FIFA 23. The check is
deterministic so ingest does not depend on the LLM obeying the prompt.
"""

from rememberstack.spine.entity_registry import normalized_lemma

_BARE_HEAD_NOUNS: frozenset[str] = frozenset(
    {
        "adapter",
        "app",
        "card",
        "game",
        "item",
        "module",
        "photo",
        "system",
        "the app",
        "the module",
        "the system",
        "thing",
        "tool",
    }
)


def is_bare_head_noun(*, name: str) -> bool:
    """Return True when ``name`` is an unqualified generic head, not a referent.

    ``FIFA 23`` and ``James's Unity strategy game`` are not bare. ``game``,
    ``App``, and ``the system`` are.
    """
    lemma = normalized_lemma(surface=name)
    if lemma in _BARE_HEAD_NOUNS:
        return True
    if lemma.startswith("the ") and lemma[4:] in _BARE_HEAD_NOUNS:
        return True
    return False
