"""WP-I.1: bare head nouns are not entity referents."""

from rememberstack.spine.entity_eligibility import is_bare_head_noun


def test_bare_head_nouns_are_rejected() -> None:
    """Unqualified generics must not mint."""
    for name in ("game", "App", "SYSTEM", "the system", "Card", "photo"):
        assert is_bare_head_noun(name=name), name


def test_qualified_referents_are_kept() -> None:
    """FIFA 23, Application-as-canonical, and possessives may mint."""
    for name in (
        "FIFA 23",
        "Application",
        "James's Unity strategy game",
        "Photo Booth",
    ):
        assert not is_bare_head_noun(name=name), name
