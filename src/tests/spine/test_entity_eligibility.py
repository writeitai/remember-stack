"""WP-I.1: bare head nouns are not entity referents."""

from rememberstack.model import EntityRef
from rememberstack.spine.entity_eligibility import is_bare_head_noun
from rememberstack.spine.entity_eligibility import surface_appears_in_claim


def test_entity_ref_discards_legacy_type() -> None:
    """D96: inbound type is not identity and is not stored on EntityRef."""
    ref = EntityRef.model_validate({"name": "Alice", "type": "Person"})
    assert ref.name == "Alice"
    assert not hasattr(ref, "type")


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


def test_surface_must_appear_as_a_claim_span() -> None:
    """Source aliases require the span in the claim, not a substring of a longer word."""
    assert surface_appears_in_claim(
        surface="App", claim_text="We opened the App today."
    )
    assert not surface_appears_in_claim(
        surface="App", claim_text="The caching process stores hot keys."
    )
    assert not surface_appears_in_claim(
        surface="App", claim_text="We installed Application on the laptop."
    )
