"""Extract eligibility: which strings may become entity names (D95 WP-I.1).

Bare head nouns are not referents. ``game`` is not FIFA 23. The check is
deterministic so ingest does not depend on the LLM obeying the prompt.
A ``source`` alias must also appear in the claim so a hallucinated surface
cannot poison T0.
"""

import re

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


def surface_appears_in_claim(*, surface: str, claim_text: str) -> bool:
    """Return True when ``surface`` occurs as a span in the claim text.

    Word-bounded and case-insensitive so ``App`` matches the claim
    ``We opened the App`` and does not match ``Application`` alone.
    """
    needle = surface.strip()
    if not needle:
        return False
    pattern = re.compile(
        pattern=r"(?<!\w)" + re.escape(needle) + r"(?!\w)",
        flags=re.IGNORECASE | re.UNICODE,
    )
    return pattern.search(claim_text) is not None
