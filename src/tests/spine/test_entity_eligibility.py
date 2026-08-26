"""WP-I.1: bare head nouns are not entity referents."""

from pydantic import ValidationError
import pytest

from rememberstack.model import EntityRef
from rememberstack.model import ResolvedEntity
from rememberstack.spine.entity_eligibility import is_bare_head_noun
from rememberstack.spine.entity_eligibility import surface_appears_in_claim


def test_entity_values_reject_legacy_type_fields() -> None:
    """D96 hard cut: stale type-producing callers fail instead of hiding drift."""
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EntityRef.model_validate({"name": "Alice", "type": "Person"})
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ResolvedEntity.model_validate(
            {
                "entity_id": "00000000-0000-0000-0000-000000000001",
                "created": False,
                "entity_type": "Person",
            }
        )


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
