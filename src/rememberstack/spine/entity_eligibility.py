"""Extract eligibility: which strings may become entity names (D95 WP-I.1).

Bare head nouns are not referents. ``game`` is not FIFA 23. The check is
deterministic so ingest does not depend on the LLM obeying the prompt.
A ``source`` alias must also appear in the claim so a hallucinated surface
cannot poison T0.

The helper is used from resolve, not only from E3. A document's own name,
written into a claim in place of a self-reference, is not a referent either
(D134): ``own_document_name_slot`` finds the one reference to skip.
"""

import re

from rememberstack.core.document_metadata import normalize_name
from rememberstack.model.relations import EntityRef
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


def own_document_name_slot(
    *, refs: tuple[EntityRef, ...], own_document_name: str | None
) -> tuple[int | None, bool]:
    """Find the one reference that is the claim's own document name (D134).

    ``refs`` are one assertion's references in slot order (subject, object
    when a relation, then context references). A reference matches when its
    ``name`` or its claim surface contains the name at the claim's recorded
    span as a whole (word-bounded, after the D134 name normalization) — so a
    surface "The report Audit_2025.pdf" or a canonical name "Audit_2025.pdf"
    with a longer surface both match. That reference is the document itself,
    not an entity, and is not minted or resolved. Returns ``(slot,
    ambiguous)``: the slot of the single matching reference, or ``None``;
    ``ambiguous`` is True when several references in the assertion match
    ("Alice wrote the report Alice") — then none is skipped, so a person is
    never lost to a document's name. A claim without the span skips nothing.
    """
    if own_document_name is None:
        return None, False
    normalized = normalize_name(value=own_document_name)
    if normalized is None:
        return None, False
    whole_name = re.compile(r"(?<!\w)" + re.escape(normalized) + r"(?!\w)")
    matches = [
        slot
        for slot, ref in enumerate(refs)
        if any(
            whole_name.search(normalize_name(value=text) or "")
            for text in (ref.name, ref.mention_surface())
        )
    ]
    if len(matches) == 1:
        return matches[0], False
    return None, len(matches) > 1
