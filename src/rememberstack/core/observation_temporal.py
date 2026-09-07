"""Stable normalized observation assertion identities; canonical redirects are separate."""

import json
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid5


def observation_assertion_id(
    *,
    deployment_id: UUID,
    receipt_id: UUID,
    normalized_subject_entity_id: UUID,
    statement: str,
) -> UUID:
    """Apply D113's exact UTF-8 JSON identity without changing text or normalized subject."""
    encoded = json.dumps(
        [
            "rememberstack:observation-assertion:1",
            str(deployment_id),
            str(receipt_id),
            str(normalized_subject_entity_id),
            statement,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return uuid5(NAMESPACE_URL, encoded)
