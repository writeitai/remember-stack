"""Pure helpers for D159 forget: the source identity and citation needles."""

import hashlib
import json
from uuid import UUID


def source_identity_hash(
    *, deployment_id: UUID, source_kind: str, source_ref: str
) -> str:
    """Hash one unambiguous deployment-owned connector identity tuple."""
    canonical = json.dumps(
        [str(deployment_id), source_kind, source_ref],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def cites_forgotten(*, content: bytes, needles: tuple[str, ...]) -> bool:
    """Whether a file's bytes name any forgotten ID (D159 §3.3 step 4).

    ``needles`` are the record's document, version and collected fact IDs.
    Matching is case-insensitive, so a citation written by hand in upper case
    still counts.
    """
    lowered = content.lower()
    return any(needle.lower().encode("utf-8") in lowered for needle in needles)
