"""The extraction eligibility policy (D133 §4.5, D138 §2).

Converters label every range of ``document.md`` with a ``derivation_kind``.
This versioned policy names the kinds that are never claim-extracted: code,
configuration, logs, unrecognized text, the large-text head/tail profile,
data-file profiles and file cards. They are still chunked, embedded and
found by search; E2 simply completes them without a Selection call.

Eligibility changes only at block boundaries: a block labelled with both an
eligible and an ineligible kind is a converter error, so E1 can cut chunks
where eligibility changes and every chunk is wholly one or the other.
"""

from typing import Final

from rememberstack.model import Block
from rememberstack.model import DerivationRange

EXTRACTION_ELIGIBILITY_POLICY_VERSION: Final = "extraction-eligibility-2026.09"
"""Identifies the ineligible-kind list; it joins the Selection reuse basis of
ineligible chunks, so a policy change re-keys exactly those chunks."""

INELIGIBLE_DERIVATION_KINDS: Final = frozenset(
    {
        "code",
        "config",
        "log",
        "other_text",
        "large_text",
        "profile_structure",
        "profile_sample",
        "file_card",
    }
)
"""Derivation kinds whose ranges are never claim-extracted (D138 §2)."""


SEARCH_ONLY_TEXT_KINDS: Final = frozenset(
    {"code", "config", "log", "other_text", "large_text"}
)
"""Ineligible kinds whose text is the file's own text, not a document built
by a converter: its ``#`` lines are comments or data, never section headings."""


class MixedEligibilityError(Exception):
    """One block carries both eligible and ineligible labelled ranges."""


def block_eligibility(
    *, blocks: tuple[Block, ...], ranges: tuple[DerivationRange, ...]
) -> tuple[bool, ...]:
    """Whether each block may be claim-extracted, in block order.

    A block is ineligible when a range it overlaps has an ineligible kind; a
    block no range overlaps is eligible. Raises ``MixedEligibilityError``
    when a block overlaps ranges of both kinds.
    """
    eligibility: list[bool] = []
    for block in blocks:
        overlapping = {
            labeled.derivation_kind not in INELIGIBLE_DERIVATION_KINDS
            for labeled in ranges
            if labeled.start < block.char_end and block.char_start < labeled.end
        }
        if len(overlapping) > 1:
            raise MixedEligibilityError(
                f"block {block.ordinal} mixes eligible and ineligible ranges"
            )
        eligibility.append(overlapping != {False})
    return tuple(eligibility)


def is_model_free(*, ranges: tuple[DerivationRange, ...]) -> bool:
    """Whether a representation holds no prose: every range is ineligible.

    Such a representation is structured without model calls (D138 §1).
    """
    return bool(ranges) and all(
        labeled.derivation_kind in INELIGIBLE_DERIVATION_KINDS for labeled in ranges
    )


def is_search_only_text(*, ranges: tuple[DerivationRange, ...]) -> bool:
    """Whether every range is search-only text (code, config, logs, …).

    Such a reading is structured as one flat section (D138 §1).
    """
    return bool(ranges) and all(
        labeled.derivation_kind in SEARCH_ONLY_TEXT_KINDS for labeled in ranges
    )
