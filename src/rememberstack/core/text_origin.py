"""Text origin time: reuse across dated versions of one lineage (D140 §5).

Every chunk records ``text_origin_at`` — when its words were first written in
the lineage, as far as the engine can tell — and a date-free
``reuse_identity_hash``. The origin replaces the version date in the D56
extraction key, the E2 header and the ``asserted_at`` of fresh claims, so an
unchanged chunk of a newly dated version keeps the key of the version that
first carried it and reuses its extraction.
"""

from collections.abc import Iterable
from datetime import datetime
import hashlib
from typing import Final

from rememberstack.model import TextOriginMatch

REUSE_IDENTITY_VERSION: Final = "d140-reuse-identity-1"
"""Generation of the identity payload layout; a change re-keys every chunk."""


def reuse_identity_hash(
    *,
    own_block_hashes: tuple[str, ...],
    neighbor_block_hashes: tuple[str, ...],
    header_facts: tuple[str, ...],
    blockizer_version: str,
    structurer_version: str,
    extractor_version: str,
) -> str:
    """The date-free chunk identity used to find its text origin (D140 §5).

    The same inputs as the D56 extraction key except any date, plus the
    blockizer version: ``header_facts`` must never carry a date or an
    effective period. A toolchain change is therefore a reuse boundary — a
    chunk under a new blockizer, structurer or extractor matches nothing.
    """
    payload = "\x1e".join(
        (
            REUSE_IDENTITY_VERSION,
            "\n".join(own_block_hashes),
            "\n".join(neighbor_block_hashes),
            "\n".join(header_facts),
            blockizer_version,
            structurer_version,
            extractor_version,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def earliest_eligible_match(
    *, matches: Iterable[TextOriginMatch], version_date: datetime | None
) -> TextOriginMatch | None:
    """The eligible match a new chunk inherits its origin from, if any.

    A match is eligible when its origin is not later than the new version's
    own date, so a back-filled older edition never inherits a later date.
    With no version date nothing is eligible. The earliest origin wins; ties
    break by version number, then ordinal. Callers pass only chunks of the
    same lineage and identity in versions that are not deleted now.
    """
    if version_date is None:
        return None
    eligible = [match for match in matches if match.text_origin_at <= version_date]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda match: (match.text_origin_at, match.version_no, match.ordinal),
    )


def resolve_text_origin(
    *, matches: Iterable[TextOriginMatch], version_date: datetime | None
) -> datetime | None:
    """A new chunk's ``text_origin_at``: the inherited origin, else the version date.

    ``None`` is today's "date unknown" and is kept as such, never replaced by
    ingestion time.
    """
    match = earliest_eligible_match(matches=matches, version_date=version_date)
    return version_date if match is None else match.text_origin_at
