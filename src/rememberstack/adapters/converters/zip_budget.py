"""The expansion budget for zip-based documents (OOXML, EPUB) (D138 §9).

The 100 MB reading limit measures the compressed file; a zip can declare
far more once expanded. Before a zip package is parsed, its central
directory's declared sizes are checked against a budget; Python's zipfile
never decompresses a member past its declared size, so the budget bounds
what parsing can expand.
"""

import io
from typing import Final
import zipfile

from rememberstack.model import ConversionError

MAX_UNCOMPRESSED_TOTAL_BYTES: Final = 500_000_000
MAX_UNCOMPRESSED_MEMBER_BYTES: Final = 200_000_000
"""Declared-size budget per package and per member (starting values)."""


def require_zip_within_budget(*, content: bytes, what: str) -> None:
    """Fail when ``content`` is not a zip or declares too much expanded data."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            sizes = [info.file_size for info in archive.infolist()]
    except (zipfile.BadZipFile, OSError, ValueError) as err:
        raise ConversionError(f"the {what} is not a readable zip package") from err
    if max(sizes, default=0) > MAX_UNCOMPRESSED_MEMBER_BYTES:
        raise ConversionError(
            f"the {what} declares a member larger than the "
            f"{MAX_UNCOMPRESSED_MEMBER_BYTES:,}-byte expansion limit"
        )
    if sum(sizes) > MAX_UNCOMPRESSED_TOTAL_BYTES:
        raise ConversionError(
            f"the {what} expands to more than the "
            f"{MAX_UNCOMPRESSED_TOTAL_BYTES:,}-byte expansion limit"
        )
