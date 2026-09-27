"""Deterministic upload MIME inference for the client surfaces.

The upload's MIME type decides which converter a deployment routes the file
to. ``mimetypes.guess_type`` reads the host's MIME database, which differs
between Python builds: some (for example the python.org 3.12 macOS build) do
not know ``.md`` at all, so a Markdown note would be sent as
``application/octet-stream`` and a self-hosted engine would park it
unprocessed. The formats the engine converts are therefore mapped here by
extension, identically on every machine; only other extensions fall back to
the host database.
"""

from __future__ import annotations

import mimetypes
from pathlib import PurePath
from typing import Final

#: Extension → MIME for every format a deployment can route to a converter.
KNOWN_UPLOAD_MIME_TYPES: Final[dict[str, str]] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def infer_upload_mime(name: str) -> str | None:
    """Return the MIME type for a file name, or ``None`` when it is unknown.

    Known engine formats are matched case-insensitively by extension; any
    other extension is looked up in the host MIME database.
    """
    known = KNOWN_UPLOAD_MIME_TYPES.get(PurePath(name).suffix.lower())
    if known is not None:
        return known
    return mimetypes.guess_type(name)[0]
