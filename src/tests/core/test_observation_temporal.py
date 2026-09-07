"""D113 assertion identities are exact, deployment-local, and independent of canonical redirects."""

from uuid import UUID

import pytest

from rememberstack.core.observation_temporal import observation_assertion_id


@pytest.mark.parametrize(
    "statement, expected",
    [
        ("CEO", "44daeb43-2baf-5ca3-9930-8c785c1e43f7"),
        ("Café\nCEO", "9fa88577-9020-5e35-9cbf-a8e3c237ff66"),
    ],
)
def test_binding_assertion_encoding_vectors(statement: str, expected: str) -> None:
    """Non-ASCII and control characters retain the binding UTF-8 compact-JSON encoding."""
    assert observation_assertion_id(
        deployment_id=UUID(int=1),
        receipt_id=UUID(int=2),
        normalized_subject_entity_id=UUID(int=3),
        statement=statement,
    ) == UUID(expected)


def test_identity_preserves_each_original_coordinate_and_exact_statement() -> None:
    """Other statements, deployments, receipts or normalized subjects cannot alias a saved application."""
    original = dict(
        deployment_id=UUID(int=1),
        receipt_id=UUID(int=2),
        normalized_subject_entity_id=UUID(int=3),
    )
    identities = {
        observation_assertion_id(**original, statement=value)
        for value in ("Café", "Cafe\u0301", "Café ", "CAFÉ", 'a"b\\c\nd', 'a\\"b\\c\nd')
    }
    assert len(identities) == 6
    for key in original:
        altered = {**original, key: UUID(int=4)}
        identities.add(observation_assertion_id(**altered, statement="Café"))
    assert len(identities) == 9
