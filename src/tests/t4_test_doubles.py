"""Small deterministic helpers for binary T4 integration tests."""

import json


def t4_candidates(prompt: str) -> list[dict[str, object]]:
    """Decode the deterministic candidate JSON embedded in a T4 prompt."""
    payload = prompt.split("CANDIDATES IN RELEVANCE ORDER:\n", 1)[1].split(
        "\n\nChoose exactly one result:", 1
    )[0]
    value = json.loads(payload)
    assert isinstance(value, list)
    return value


def match_first_t4_candidate(prompt: str, type_name: str) -> dict[str, object]:
    """Return a valid match selecting the first supplied T4 candidate."""
    if type_name != "T4Selection":
        raise AssertionError(f"unexpected generate call: {type_name}")
    candidate = t4_candidates(prompt)[0]
    return {
        "decision": "match",
        "candidate_id": candidate["candidate_id"],
        "confidence": 0.9,
    }
