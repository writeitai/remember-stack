"""Conservative byte classification before E0 routing and storage."""

from __future__ import annotations

import codecs
import io
import itertools
import mmap
import re
from typing import Any
from typing import cast
from typing import IO
import zipfile

from rememberstack.core.format_registry import family_for_mime
from rememberstack.core.format_registry import SNIFF_BYTES
from rememberstack.model.content_detection import ContentDetectionError

_OFFICE_MIMES = {
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
}
_OFFICE_FAMILIES = frozenset({"word", "presentation", "spreadsheet"})
"""Registry families whose declared spellings (templates, macro-enabled
documents) are Office packages."""
_TEXT_FAMILIES = frozenset(
    {"markdown", "text", "other_text", "log", "code", "config", "html"}
    | {"delimited", "email", "notebook", "binary"}
)
"""Registry families a text-class declaration may name (``binary`` included:
it decides nothing)."""
_TEXT_HINTS = {"text/markdown", "text/x-markdown"}
_TEXT_APPLICATION_HINTS = {
    "application/javascript",
    "application/ecmascript",
    "application/json",
    "application/ld+json",
    "application/x-ndjson",
    "application/x-yaml",
    "application/yaml",
    "application/toml",
    "application/x-sh",
    "application/rtf",
    "application/x-spss-por",
    "application/x-ipynb+json",
    "message/rfc822",
}
_IMAGE_MIME_ALIASES = {
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "image/x-png": "image/png",
    "image/heif": "image/heic",
}
_AUDIO_MIME_ALIASES = {"audio/x-m4a": "audio/mp4"}

Content = bytes | mmap.mmap
"""A file's whole content: bytes, or a memory map of a file on disk (D147).

Every check reads it by indexing, slicing a bounded window or searching in
place, so a large mapped file is never copied into memory as a whole.
"""

_TEXT_CHUNK_BYTES = 1 << 20
#: Characters after leading whitespace enough to recognise an HTML opener.
_HTML_LOOKAHEAD = 64
_WHITESPACE_RUN = re.compile(r"\s+")
_HTML_OPENER = re.compile(r"(?:<!doctype\s+html\b|<html(?:\s|>))", re.IGNORECASE)
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _starts(content: Content, prefix: bytes) -> bool:
    return content[: len(prefix)] == prefix


def _ends(content: Content, suffix: bytes) -> bool:
    return (
        len(content) >= len(suffix) and content[len(content) - len(suffix) :] == suffix
    )


def _looks_like_bmp(*, content: Content) -> bool:
    """Require BMP file and DIB header fields, not the printable `BM` alone."""
    if len(content) < 26 or not _starts(content, b"BM"):
        return False
    size = int.from_bytes(content[2:6], "little")
    pixel_offset = int.from_bytes(content[10:14], "little")
    dib_size = int.from_bytes(content[14:18], "little")
    return (
        26 <= size <= len(content)
        and content[6:10] == b"\x00" * 4
        and 26 <= pixel_offset <= size
        and 12 <= dib_size <= len(content) - 14
    )


def _looks_like_id3(*, content: Content) -> bool:
    """Require a bounded ID3v2 header and synchsafe tag length."""
    if len(content) < 10 or not _starts(content, b"ID3"):
        return False
    if content[3] not in {2, 3, 4} or content[4] == 0xFF:
        return False
    if any(part & 0x80 for part in content[6:10]):
        return False
    tag_size = 0
    for part in content[6:10]:
        tag_size = (tag_size << 7) | part
    return tag_size <= len(content) - 10


def _looks_like_gif(*, content: Content) -> bool:
    """Require a logical screen descriptor after the printable GIF marker."""
    return (
        len(content) >= 13
        and content[:6] in {b"GIF87a", b"GIF89a"}
        and int.from_bytes(content[6:8], "little") > 0
        and int.from_bytes(content[8:10], "little") > 0
        and (
            any(byte < 32 or byte > 126 for byte in content[10:13])
            or (
                len(content) >= 15
                and content[13] in {0x21, 0x2C}
                and _ends(content, b"\x3b")
            )
        )
    )


def _looks_like_psd(*, content: Content) -> bool:
    """Require Photoshop header fields beyond the printable signature."""
    if (
        len(content) < 26
        or content[:6] != b"8BPS\x00\x01"
        or content[6:12] != b"\x00" * 6
    ):
        return False
    channels = int.from_bytes(content[12:14], "big")
    height = int.from_bytes(content[14:18], "big")
    width = int.from_bytes(content[18:22], "big")
    depth = int.from_bytes(content[22:24], "big")
    mode = int.from_bytes(content[24:26], "big")
    return (
        1 <= channels <= 56
        and 1 <= height <= 300_000
        and 1 <= width <= 300_000
        and depth in {1, 8, 16, 32}
        and mode <= 9
    )


def _looks_like_flac(*, content: Content) -> bool:
    """Require the mandatory 34-byte STREAMINFO block after the marker."""
    return (
        len(content) >= 42
        and _starts(content, b"fLaC")
        and content[4] & 0x7F == 0
        and int.from_bytes(content[5:8], "big") == 34
    )


def _looks_like_ogg(*, content: Content) -> bool:
    """Require an Ogg page header and its segment table."""
    return (
        len(content) >= 27
        and _starts(content, b"OggS\x00")
        and len(content) >= 27 + content[26]
    )


def _looks_like_mpeg_frame(*, content: Content) -> bool:
    """Accept a complete MPEG audio frame with non-reserved header fields."""
    if len(content) < 24 or content[0] != 0xFF or content[1] & 0xE0 != 0xE0:
        return False
    version = (content[1] >> 3) & 0x03
    layer = (content[1] >> 1) & 0x03
    bitrate_index = content[2] >> 4
    sample_index = (content[2] >> 2) & 0x03
    if version == 1 or layer == 0 or bitrate_index in {0, 15} or sample_index == 3:
        return False
    rates = {
        3: (44100, 48000, 32000),
        2: (22050, 24000, 16000),
        0: (11025, 12000, 8000),
    }
    bitrates = {
        (True, 3): (
            0,
            32,
            64,
            96,
            128,
            160,
            192,
            224,
            256,
            288,
            320,
            352,
            384,
            416,
            448,
        ),
        (True, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
        (True, 1): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
        (False, 3): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
        (False, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
        (False, 1): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    }
    bitrate = bitrates[(version == 3, layer)][bitrate_index] * 1000
    rate = rates[version][sample_index]
    padding = (content[2] >> 1) & 1
    if layer == 3:
        frame_size = ((12 * bitrate // rate) + padding) * 4
    else:
        factor = 72 if layer == 1 and version != 3 else 144
        frame_size = factor * bitrate // rate + padding
    return frame_size <= len(content)


def _looks_like_adts(*, content: Content) -> bool:
    """Require a complete AAC ADTS frame rather than a two-byte sync guess."""
    if len(content) < 7 or content[0] != 0xFF or content[1] & 0xF6 != 0xF0:
        return False
    if (content[2] >> 2) & 0x0F == 0x0F:
        return False
    frame_size = ((content[3] & 0x03) << 11) | (content[4] << 3) | (content[5] >> 5)
    return 7 <= frame_size <= len(content)


def _bmff_mime(*, content: Content) -> str | None:
    """Classify ISO BMFF by validated major and compatible file-type brands."""
    if len(content) < 16 or content[4:8] != b"ftyp":
        return None
    size = int.from_bytes(content[:4], "big")
    if not 16 <= size <= len(content):
        return None
    # The major brand, then each compatible brand, read one at a time.
    seen: set[str] = set()
    for offset in itertools.chain((8,), range(16, size, 4)):
        kind = _BMFF_BRANDS.get(content[offset : offset + 4])
        if kind is not None:
            seen.add(kind)
    for kind in ("image/heic", "image/avif", "audio/mp4"):
        if kind in seen:
            return kind
    return "video/mp4"


_BMFF_BRANDS: dict[bytes, str] = {
    **dict.fromkeys(
        (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1"), "image/heic"
    ),
    **dict.fromkeys((b"avif", b"avis"), "image/avif"),
    **dict.fromkeys((b"M4A ", b"M4B ", b"M4P "), "audio/mp4"),
}


def has_pdf_body(*, content: Content) -> bool:
    """Find a bounded PDF header, complete object, and trailing end marker."""
    header = re.search(rb"%PDF-[12]\.\d", content[:8200])
    if header is None or header.start() > 8192:
        return False
    preamble = content[: header.start()]
    try:
        preamble_text = preamble.decode("utf-8")
    except UnicodeDecodeError:
        preamble_text = ""
    if len(preamble_text.split()) >= 2:
        # A prose sentence quoting a PDF header is not a PDF preamble.
        return False
    # Searched in place from the header's end rather than on a copied body;
    # each pattern's "start of the body" alternative is tried as an anchored
    # match at that position first, which is what ``^`` meant on the copy.
    object_match = _search_from(
        content, header.end(), at_start=_PDF_OBJECT_AT, anywhere=_PDF_OBJECT
    )
    return bool(
        object_match is not None
        and _search_from(
            content, object_match.end(), at_start=_PDF_EOF_AT, anywhere=_PDF_EOF
        )
    )


_PDF_OBJECT_BODY = (
    rb"[ \t]*\d+[ \t]+\d+[ \t]+obj[ \t]*(?:\r\n|[\r\n])"
    rb".*?(?:^|[\r\n])[ \t]*endobj[ \t]*(?:\r\n|[\r\n])"
)
_PDF_OBJECT_AT = re.compile(_PDF_OBJECT_BODY, re.DOTALL)
_PDF_OBJECT = re.compile(rb"[\r\n]" + _PDF_OBJECT_BODY, re.DOTALL)
_PDF_EOF_BODY = rb"[ \t]*%%EOF(?:[ \t]*[\r\n]|[ \t]*$)"
_PDF_EOF_AT = re.compile(_PDF_EOF_BODY)
_PDF_EOF = re.compile(rb"[\r\n]" + _PDF_EOF_BODY)


def _search_from(
    content: Content,
    position: int,
    *,
    at_start: re.Pattern[bytes],
    anywhere: re.Pattern[bytes],
) -> re.Match[bytes] | None:
    """``re.search(pattern, content[position:])`` without copying the tail."""
    return at_start.match(content, position) or anywhere.search(content, position)


_MIMETYPE_READ_BYTES = 129
"""One byte past the largest ``mimetype`` member the check accepts (128)."""

_MAPPED_ZIP_READ_LIMIT_BYTES = 64 << 20
"""Largest single read the ZIP check makes on a memory-mapped file (its
central directory), about 700,000 entries. Starting point. Bytes already in
memory are read without a limit, exactly as before."""


class _BoundedReader(io.RawIOBase):
    """A seekable, read-only view of the content for ``zipfile`` (D147).

    Reads come straight from the bytes or the map. On a mapped file one read
    larger than :data:`_MAPPED_ZIP_READ_LIMIT_BYTES` fails, so a crafted
    central directory cannot pull a large stored object into memory; such a
    file is then not recognised as an office package.
    """

    def __init__(self, content: Content) -> None:
        self._content = content
        self._position = 0
        self._limit = (
            None if isinstance(content, bytes) else _MAPPED_ZIP_READ_LIMIT_BYTES
        )

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        """Seek exactly as ``io.BytesIO`` does, so malformed input fails alike."""
        if whence == io.SEEK_SET and offset < 0:
            raise ValueError(f"negative seek value {offset}")
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._position}.get(
            whence, len(self._content)
        )
        self._position = max(0, base + offset)
        return self._position

    def read(self, size: int | None = -1) -> bytes:
        end = len(self._content) if size is None or size < 0 else self._position + size
        if self._limit is not None and end - self._position > self._limit:
            raise OSError("ZIP read over the detection limit")
        chunk = self._content[self._position : end]
        self._position += len(chunk)
        return chunk

    def readinto(self, buffer: Any) -> int:
        chunk = self.read(len(buffer))
        buffer[: len(chunk)] = chunk
        return len(chunk)


def _office_zip_mime(*, content: Content) -> str | None:
    """Recognize document package members without decompressing untrusted payloads."""
    try:
        with zipfile.ZipFile(cast("IO[bytes]", _BoundedReader(content))) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" in names:
                for body, mime in (
                    (
                        "word/document.xml",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ),
                    (
                        "xl/workbook.xml",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                    (
                        "ppt/presentation.xml",
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    ),
                ):
                    if body in names:
                        return mime
            if "mimetype" in names and "META-INF/manifest.xml" in names:
                member = archive.getinfo("mimetype")
                bounded = isinstance(content, bytes) or member.compress_type in {
                    zipfile.ZIP_STORED,
                    zipfile.ZIP_DEFLATED,
                }
                if member.file_size <= 128 and bounded:
                    # Bounded: a declared size is untrusted, and read() would
                    # inflate the whole stream before truncating it. Reading
                    # past the declared size still ends the member and checks
                    # its CRC, so the verdict is unchanged. Only stored and
                    # DEFLATE output can be bounded this way, so on a mapped
                    # file (D147) another method is not read; ODF requires a
                    # stored ``mimetype`` anyway.
                    with archive.open(member) as stream:
                        raw = stream.read(_MIMETYPE_READ_BYTES)
                    mime = raw.decode("ascii", errors="ignore")
                    if mime in _OFFICE_MIMES:
                        return mime
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
        pass
    return None


_CFB_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_CFB_STREAM_MIMES = {
    "WordDocument": "application/msword",
    "Workbook": "application/vnd.ms-excel",
    "Book": "application/vnd.ms-excel",
    "PowerPoint Document": "application/vnd.ms-powerpoint",
}
"""Root streams that name a legacy Office subtype (MS-DOC, MS-XLS, MS-PPT)."""
_CFB_LAST_SECTOR = 0xFFFFFFFA
_CFB_END_OF_CHAIN = 0xFFFFFFFE
_CFB_MAX_SECTORS = 4096
"""Directory, FAT and DIFAT sectors read at most: a bound on crafted chains."""


def _u32(data: bytes, offset: int) -> int:
    """A little-endian unsigned 32-bit field."""
    return int.from_bytes(data[offset : offset + 4], "little")


def _ole_office_mime(*, content: Content) -> str | None:
    """The legacy Office subtype an OLE file's root streams name, or None.

    Reads the compound file's header, the FAT and DIFAT sectors it needs and
    the directory chain (MS-CFB), bounded by :data:`_CFB_MAX_SECTORS`. Any
    malformed chain (a sector outside the file or past the header's FAT
    count, a repeated sector, a chain not ended by ENDOFCHAIN) gives None.
    Only the root storage's own children count, so an embedded object's
    streams do not; None when no subtype or more than one is named.
    """
    header = bytes(content[:512])
    if len(header) < 512 or not header.startswith(_CFB_SIGNATURE):
        return None
    shift = int.from_bytes(header[30:32], "little")
    if shift not in (9, 12):
        return None
    size = 1 << shift
    per_sector = size // 4
    fat_sectors = _u32(header, 44)
    difat = [_u32(header, 76 + 4 * index) for index in range(109)]
    next_difat = _u32(header, 68)
    difat_seen: set[int] = set()
    reads = 0

    def sector(sid: int) -> bytes | None:
        nonlocal reads
        reads += 1
        if reads > _CFB_MAX_SECTORS or sid > _CFB_LAST_SECTOR:
            return None
        start = (sid + 1) * size
        data = bytes(content[start : start + size])
        return data if len(data) == size else None

    def next_sector(sid: int) -> int | None:
        nonlocal next_difat
        index, slot = divmod(sid, per_sector)
        if index >= fat_sectors:
            return None
        while index >= len(difat):
            if next_difat > _CFB_LAST_SECTOR or next_difat in difat_seen:
                return None
            difat_seen.add(next_difat)
            block = sector(next_difat)
            if block is None:
                return None
            difat.extend(_u32(block, 4 * i) for i in range(per_sector - 1))
            next_difat = _u32(block, size - 4)
        fat = sector(difat[index])
        return None if fat is None else _u32(fat, 4 * slot)

    entries: list[bytes] = []
    directory_seen: set[int] = set()
    sid = _u32(header, 48)
    while sid != _CFB_END_OF_CHAIN:
        if sid in directory_seen:
            return None
        directory_seen.add(sid)
        data = sector(sid)
        following = None if data is None else next_sector(sid)
        if data is None or following is None:
            return None
        entries.extend(data[offset : offset + 128] for offset in range(0, size, 128))
        sid = following
    if not entries or entries[0][66] != 5:
        return None
    found: set[str] = set()
    pending = [_u32(entries[0], 76)]
    seen: set[int] = set()
    while pending:
        index = pending.pop()
        if index >= len(entries) or index in seen:
            continue
        seen.add(index)
        entry = entries[index]
        length = int.from_bytes(entry[64:66], "little")
        if entry[66] == 2 and 2 <= length <= 64:
            name = entry[: length - 2].decode("utf-16-le", errors="replace")
            if name in _CFB_STREAM_MIMES:
                found.add(_CFB_STREAM_MIMES[name])
        pending.extend((_u32(entry, 68), _u32(entry, 72)))
    return found.pop() if len(found) == 1 else None


_RFC822_HEADER = re.compile(rb"[\x21-\x39\x3b-\x7e]+:")
_RFC822_MAIL_FIELDS = frozenset(
    {b"to", b"date", b"subject", b"message-id", b"received", b"mime-version"}
)
"""Fields besides ``From`` that mark a header block as an Internet message."""
_DELIMITERS = (",", "\t", ";", "|")
_UTF16_TEXT_MIMES = frozenset({"text/csv", "text/tab-separated-values"})
"""Text the delimited reader decodes from a UTF-16 byte-order mark."""


def _looks_like_utf16_text(*, content: Content) -> bool:
    """Delimited text in UTF-16 with a byte-order mark.

    The sample decodes strictly (to the end when the file fits in it), has
    no control characters, and its first line holds a delimiter.
    """
    head = bytes(content[:SNIFF_BYTES])
    if not head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return False
    whole = len(content) <= SNIFF_BYTES
    try:
        text = codecs.getincrementaldecoder("utf-16")().decode(
            head if whole else head[: len(head) & ~1], final=whole
        )
    except UnicodeDecodeError:
        return False
    first_line = text.split("\n", 1)[0]
    return not _CONTROL_CHARACTERS.search(text) and any(
        delimiter in first_line for delimiter in _DELIMITERS
    )


def _looks_like_rfc822(*, content: Content) -> bool:
    """An Internet message: mail header fields, a blank line, a text body.

    For a message whose body is not UTF-8 (an 8-bit transfer encoding); a
    UTF-8 message is already text. The header block holds a ``From`` with an
    address and another mail field, and has no NUL; the sampled body has no
    binary control characters.
    """
    head = bytes(content[:SNIFF_BYTES]).replace(b"\r\n", b"\n")
    block, separator, body = head.partition(b"\n\n")
    if not separator or b"\x00" in block:
        return False
    names: set[bytes] = set()
    sender = False
    for line in block.split(b"\n"):
        if line[:1] in {b" ", b"\t"} and names:
            continue
        if not _RFC822_HEADER.match(line):
            return False
        name, _, value = line.partition(b":")
        names.add(name.lower())
        sender = sender or (name.lower() == b"from" and b"@" in value)
    return (
        sender
        and bool(names & _RFC822_MAIL_FIELDS)
        and not _CONTROL_CHARACTERS.search(body.decode("latin-1"))
    )


def sniffed_text_mimes(*, content: Content) -> frozenset[str]:
    """Stored types a non-UTF-8 text body may take, from its bytes alone.

    UTF-16 text (with its byte-order mark) may be a delimited file, which
    the table reader decodes; an Internet message with an 8-bit body may be
    an email. Other bytes that are not UTF-8 take none.
    """
    if _looks_like_utf16_text(content=content):
        return _UTF16_TEXT_MIMES
    if _looks_like_rfc822(content=content):
        return frozenset({"message/rfc822"})
    return frozenset()


def _detected_mime(*, content: Content) -> str:
    """Choose a class from signatures, package structure, or strict UTF-8."""
    if has_pdf_body(content=content):
        return "application/pdf"
    if (
        len(content) >= 13
        and _starts(content, b"PAR1")
        and _ends(content, b"PAR1")
        and 0 < int.from_bytes(content[-8:-4], "little") <= len(content) - 12
    ):
        return "application/vnd.apache.parquet"
    for signature, mime in (
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"II*\x00", "image/tiff"),
        (b"MM\x00*", "image/tiff"),
        (b"\x1a\x45\xdf\xa3", "video/webm"),
    ):
        if _starts(content, signature):
            return mime
    if _looks_like_bmp(content=content):
        return "image/bmp"
    if _looks_like_gif(content=content):
        return "image/gif"
    if _looks_like_psd(content=content):
        return "image/vnd.adobe.photoshop"
    if _looks_like_flac(content=content):
        return "audio/flac"
    if _looks_like_ogg(content=content):
        return "audio/ogg"
    if _looks_like_id3(content=content):
        return "audio/mpeg"
    if _starts(content, b"RIFF") and len(content) >= 12:
        riff_size = int.from_bytes(content[4:8], "little") + 8
        if 12 <= riff_size <= len(content):
            if content[8:12] == b"WAVE":
                return "audio/wav"
            if content[8:12] == b"AVI ":
                return "video/x-msvideo"
            if content[8:12] == b"WEBP":
                return "image/webp"
    bmff = _bmff_mime(content=content)
    if bmff is not None:
        return bmff
    if _looks_like_adts(content=content):
        return "audio/aac"
    if _looks_like_mpeg_frame(content=content):
        return "audio/mpeg"
    if _starts(content, b"PK\x03\x04"):
        office = _office_zip_mime(content=content)
        if office is not None:
            return office
        return "application/octet-stream"
    if _starts(content, _CFB_SIGNATURE):
        return _ole_office_mime(content=content) or "application/x-ole-office"
    # Strict UTF-8 without control characters, decoded a window at a time.
    # For the HTML opener only the first characters after the leading
    # whitespace matter; collapsing each whitespace run to one space keeps
    # every match of the pattern (its only repetition is ``\s+``) while the
    # retained head stays small however long the whitespace is.
    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    started = False
    head = ""
    for start in range(0, max(len(content), 1), _TEXT_CHUNK_BYTES):
        window = content[start : start + _TEXT_CHUNK_BYTES]
        try:
            text = decoder.decode(window, final=start + len(window) >= len(content))
        except UnicodeDecodeError:
            return "application/octet-stream"
        if _CONTROL_CHARACTERS.search(text):
            return "application/octet-stream"
        if not started:
            text = text.lstrip("\ufeff \t\r\n")
            started = bool(text)
        if started and len(head) < _HTML_LOOKAHEAD:
            head = _WHITESPACE_RUN.sub(" ", head + text)[:_HTML_LOOKAHEAD]
    if _HTML_OPENER.match(head):
        return "text/html"
    return "text/plain"


def detect_content_mime(*, content: Content, declared_mime: str) -> str:
    """Return byte-authoritative MIME, refusing a contradictory declaration."""
    detected = _detected_mime(content=content)
    declared = declared_mime.partition(";")[0].strip().lower()
    declared = _IMAGE_MIME_ALIASES.get(declared, declared)
    declared = _AUDIO_MIME_ALIASES.get(declared, declared)
    if detected == "application/octet-stream":
        if declared in sniffed_text_mimes(content=content):
            return declared
        if (
            declared.startswith(("text/", "image/", "audio/", "video/"))
            or declared == "application/pdf"
            or declared in _OFFICE_MIMES
            or family_for_mime(mime=declared).name in _OFFICE_FAMILIES
        ):
            raise ContentDetectionError(code="content_type_mismatch")
        return detected
    if detected.startswith("text/"):
        if detected == "text/html":
            if (
                declared not in {"", "application/octet-stream", "text/plain"}
                and family_for_mime(mime=declared).name != "html"
            ):
                raise ContentDetectionError(code="content_type_mismatch")
            return detected
        if (
            declared not in {"", "application/octet-stream"}
            and declared not in _TEXT_APPLICATION_HINTS
            and not declared.startswith("text/")
            and family_for_mime(mime=declared).name not in _TEXT_FAMILIES
        ):
            raise ContentDetectionError(code="content_type_mismatch")
        return "text/markdown" if declared in _TEXT_HINTS else "text/plain"
    detected_class = (
        "office"
        if detected in _OFFICE_MIMES or detected == "application/x-ole-office"
        else detected.split("/", 1)[0]
    )
    declared_class = (
        "office"
        if declared in _OFFICE_MIMES
        or family_for_mime(mime=declared).name in _OFFICE_FAMILIES
        else declared.split("/", 1)[0]
    )
    if (
        declared not in {"", "application/octet-stream"}
        and declared_class != detected_class
        and not (
            detected_class == "office"
            and declared in {"application/zip", "application/x-zip-compressed"}
        )
    ):
        raise ContentDetectionError(code="content_type_mismatch")
    if detected == "application/pdf" and declared not in {
        "",
        "application/octet-stream",
        detected,
    }:
        raise ContentDetectionError(code="content_type_mismatch")
    if (
        detected.startswith("image/")
        and declared.startswith("image/")
        and declared != detected
    ):
        raise ContentDetectionError(code="content_type_mismatch")
    if detected == "application/x-ole-office":
        # Its root streams named no Word, Excel or PowerPoint document (or
        # more than one); a declaration does not decide what the bytes do not.
        raise ContentDetectionError(code="ambiguous_office_format")
    return detected
