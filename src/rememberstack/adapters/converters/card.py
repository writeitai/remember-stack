"""The D138 ``card`` route, registered by name for route tables.

The converter lives in ``rememberstack.core.file_card`` because the convert
worker also calls it directly for oversized files, whatever the route table
maps (D138 §3).
"""

from rememberstack.core.file_card import CARD_CONVERTER_VERSION
from rememberstack.core.file_card import CardConverter
from rememberstack.core.file_card import MAX_LISTED_MEMBERS
from rememberstack.core.file_card import MAX_TAR_SCAN_BYTES

__all__ = (
    "CARD_CONVERTER_VERSION",
    "CardConverter",
    "MAX_LISTED_MEMBERS",
    "MAX_TAR_SCAN_BYTES",
)
