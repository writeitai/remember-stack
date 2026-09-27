"""The D138 format registry: families, detection, and reading limits.

One table names every format family a workspace holds: its extensions, the
MIME type E0 stores for it, the converter that reads it in this build, and
its outcome (prose, search-only text, profile or card). Detection picks the
family for an arriving file in D138 §3 order — extension (including compound
tar extensions, named files and dotfiles), then a specific declared MIME,
then the bytes (UTF-8 without NUL in the first 64 KiB is ``other_text``,
anything else ``binary``). The stored MIME is what conversion routes on.

Families whose D138 converter is not built yet (``converter=None``) are still
detected and stored under their own MIME; they route as any unrouted MIME
does (D117 parking) unless the stock table keeps an older route for them.
"""

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final
from typing import Literal

FamilyOutcome = Literal["prose", "search_only", "profile", "card"]
"""What a family's reading is (D138 §2); only prose is claim-extracted."""

SNIFF_BYTES: Final = 65_536
"""How much of an unrecognized file the content sniff inspects (64 KiB)."""

_DOCX: Final = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PPTX: Final = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
_XLSX: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass(frozen=True, slots=True)
class FormatFamily:
    """One row of the D138 §4 family table."""

    name: str
    mime: str
    """The canonical MIME E0 stores for the family's files."""
    outcome: FamilyOutcome
    converter: str | None
    """The converter route this build ships for the family; None parks it."""
    extensions: frozenset[str]
    reading_limit_bytes: int | None = None
    """Above this size the file gets an oversized card instead of a reading
    (D138 §3 starting values); None means the family has no such limit."""


FAMILIES: Final[tuple[FormatFamily, ...]] = (
    FormatFamily(
        name="markdown",
        mime="text/markdown",
        outcome="prose",
        converter="text",
        extensions=frozenset(
            {"md", "markdown", "mdx", "rst", "adoc", "asciidoc", "org", "textile"}
        ),
    ),
    FormatFamily(
        name="text",
        mime="text/plain",
        outcome="prose",
        converter="text",
        extensions=frozenset({"txt", "srt", "vtt"}),
    ),
    FormatFamily(
        name="other_text",
        mime="text/x-other-text",
        outcome="search_only",
        converter="text",
        extensions=frozenset(),
    ),
    FormatFamily(
        name="log",
        mime="text/x-log",
        outcome="search_only",
        converter="text",
        extensions=frozenset({"log", "out", "err", "trace"}),
    ),
    FormatFamily(
        name="code",
        mime="text/x-code",
        outcome="search_only",
        converter="text",
        extensions=frozenset(
            {
                *("py", "pyi", "js", "jsx", "ts", "tsx", "cjs", "mjs", "cts", "mts"),
                *("java", "kt", "kts", "scala", "go", "rs", "c", "h", "cc", "cpp"),
                *("cxx", "hh", "hpp", "hxx", "m", "mm", "cs", "fs", "rb", "php"),
                *("swift", "dart", "zig", "sol", "lua", "pl", "r", "jl", "sh"),
                *("bash", "zsh", "fish", "ps1", "bat", "cmd", "sql", "sas", "sps"),
                *("do", "gradle", "groovy", "cmake", "tf", "hcl", "proto"),
                *("graphql", "gql", "css", "scss", "sass", "less", "vue", "svelte"),
                *("j2", "jinja", "template", "jmx"),
            }
        ),
    ),
    FormatFamily(
        name="config",
        mime="text/x-config",
        outcome="search_only",
        converter="text",
        extensions=frozenset(
            {
                *("json", "jsonc", "json5", "jsonl", "ndjson", "geojson", "yaml"),
                *("yml", "toml", "ini", "cfg", "conf", "cnf", "properties"),
                *("plist", "xml", "svg", "kml", "gpx"),
            }
        ),
    ),
    FormatFamily(
        name="html",
        mime="text/html",
        outcome="prose",
        converter="markitdown",
        extensions=frozenset({"html", "htm", "xhtml"}),
    ),
    FormatFamily(
        name="ebook",
        mime="application/epub+zip",
        outcome="prose",
        converter="markitdown",
        extensions=frozenset({"epub"}),
    ),
    FormatFamily(
        name="notebook",
        mime="application/x-ipynb+json",
        outcome="prose",
        converter="notebook",
        extensions=frozenset({"ipynb"}),
    ),
    FormatFamily(
        name="email",
        mime="message/rfc822",
        outcome="prose",
        converter="email",
        extensions=frozenset({"eml"}),
    ),
    FormatFamily(
        name="word",
        mime=_DOCX,
        outcome="prose",
        converter="office",
        extensions=frozenset({"docx", "docm", "dotx", "doc", "odt", "rtf"}),
        reading_limit_bytes=100_000_000,
    ),
    FormatFamily(
        name="presentation",
        mime=_PPTX,
        outcome="prose",
        converter="office",
        extensions=frozenset({"pptx", "pptm", "ppsx", "potx", "ppt", "odp"}),
        reading_limit_bytes=100_000_000,
    ),
    FormatFamily(
        name="pdf",
        mime="application/pdf",
        outcome="prose",
        converter="pdf",
        extensions=frozenset({"pdf"}),
        reading_limit_bytes=100_000_000,
    ),
    FormatFamily(
        name="spreadsheet",
        mime=_XLSX,
        outcome="profile",
        converter=None,
        extensions=frozenset({"xlsx", "xlsm", "xltx", "xls", "ods"}),
        reading_limit_bytes=200_000_000,
    ),
    FormatFamily(
        name="delimited",
        mime="text/csv",
        outcome="profile",
        converter=None,
        extensions=frozenset({"csv", "tsv", "psv", "tab"}),
    ),
    FormatFamily(
        name="dataset",
        mime="application/vnd.apache.parquet",
        outcome="profile",
        converter=None,
        extensions=frozenset(
            {
                *("parquet", "feather", "arrow", "sav", "por", "xpt", "sas7bdat"),
                *("dta", "sqlite", "sqlite3", "db"),
            }
        ),
    ),
    FormatFamily(
        name="image",
        mime="image/png",
        outcome="card",
        converter="card",
        extensions=frozenset(
            {"png", "jpg", "jpeg", "gif", "webp", "tif", "tiff", "bmp", "heic"}
            | {"avif", "psd"}
        ),
    ),
    FormatFamily(
        name="media",
        mime="audio/mpeg",
        outcome="card",
        converter="card",
        extensions=frozenset(
            {"wav", "mp3", "m4a", "flac", "ogg", "aac", "mp4", "mov", "webm", "mkv"}
            | {"avi"}
        ),
    ),
    FormatFamily(
        name="archive",
        mime="application/zip",
        outcome="card",
        converter="card",
        extensions=frozenset({"zip", "tar", "7z", "rar", "gz", "bz2", "xz", "zst"}),
    ),
    FormatFamily(
        name="binary",
        mime="application/octet-stream",
        outcome="card",
        converter="card",
        extensions=frozenset(
            {"pages", "numbers", "key", "msg", "mbox", "rds", "rdata"}
        ),
    ),
)
"""The D138 §4 family table: the whole per-family contract."""

FORMAT_MIMES: Final[dict[str, str]] = {
    "doc": "application/msword",
    "odt": "application/vnd.oasis.opendocument.text",
    "rtf": "application/rtf",
    "ppt": "application/vnd.ms-powerpoint",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "xls": "application/vnd.ms-excel",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "tsv": "text/tab-separated-values",
    "tab": "text/tab-separated-values",
    "feather": "application/vnd.apache.arrow.file",
    "arrow": "application/vnd.apache.arrow.file",
    "sav": "application/x-spss-sav",
    "por": "application/x-spss-por",
    "xpt": "application/x-sas-xport",
    "sas7bdat": "application/x-sas-data",
    "dta": "application/x-stata-dta",
    "sqlite": "application/vnd.sqlite3",
    "sqlite3": "application/vnd.sqlite3",
    "db": "application/vnd.sqlite3",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "bmp": "image/bmp",
    "heic": "image/heic",
    "avif": "image/avif",
    "psd": "image/vnd.adobe.photoshop",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "aac": "audio/aac",
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "webm": "video/webm",
    "mkv": "video/x-matroska",
    "avi": "video/x-msvideo",
    "tar": "application/x-tar",
    "7z": "application/x-7z-compressed",
    "rar": "application/vnd.rar",
    "gz": "application/gzip",
    "bz2": "application/x-bzip2",
    "xz": "application/x-xz",
    "zst": "application/zstd",
}
"""Extensions whose own format MIME is stored instead of the family's
canonical one: a different container (``.doc`` beside ``.docx``) or a media
type a provider route keys on (``image/jpeg`` for the D115 route). Every
other extension stores its family's canonical MIME."""

LIBREOFFICE_EXTENSIONS: Final = frozenset({"doc", "odt", "rtf", "ppt", "odp", "ods"})
"""Extensions LibreOffice converts to Office Open XML before they are read
(D138 §7); their routes exist only where ``soffice`` is installed."""

_DECLARED_ALIASES: Final[dict[str, str]] = {
    "text/x-markdown": "markdown",
    "application/xhtml+xml": "html",
    "application/json": "config",
    "application/ld+json": "config",
    "application/xml": "config",
    "text/xml": "config",
    "application/yaml": "config",
    "application/x-yaml": "config",
    "text/yaml": "config",
    "application/toml": "config",
    "image/svg+xml": "config",
    "application/x-ndjson": "config",
    "text/javascript": "code",
    "application/javascript": "code",
    "text/css": "code",
    "text/x-python": "code",
    "application/x-sh": "code",
    "application/sql": "code",
    "application/vnd.ms-word.document.macroenabled.12": "word",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template": ("word"),
    "application/vnd.ms-powerpoint.presentation.macroenabled.12": "presentation",
    "application/vnd.openxmlformats-officedocument.presentationml.slideshow": (
        "presentation"
    ),
    "application/vnd.openxmlformats-officedocument.presentationml.template": (
        "presentation"
    ),
    "application/vnd.ms-excel.sheet.macroenabled.12": "spreadsheet",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.template": (
        "spreadsheet"
    ),
    "application/x-epub+zip": "ebook",
    "audio/x-wav": "media",
    "application/x-zip-compressed": "archive",
    "application/x-gzip": "archive",
    "application/vnd.ms-outlook": "binary",
}
"""Other MIME spellings a caller may declare; they store the family's
canonical MIME."""

_NAMED_FILES: Final[dict[str, str]] = {
    "readme": "text",
    "license": "text",
    "dockerfile": "code",
    "containerfile": "code",
    "makefile": "code",
    "jenkinsfile": "code",
    "procfile": "code",
    "codeowners": "code",
    "build": "code",
    "workspace": "code",
}
"""Extensionless file names with a known family (D138 §3 step 1)."""

_COMPOUND_TAR: Final = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst")
_UNSPECIFIC_DECLARED: Final = frozenset({"", "application/octet-stream", "text/plain"})
"""Declarations that never decide a family: the generic byte type and the
``text/plain`` many clients guess for anything."""

_BY_NAME: Final = {family.name: family for family in FAMILIES}
_BY_EXTENSION: Final = {
    extension: family for family in FAMILIES for extension in family.extensions
}
_BY_MIME: Final = {
    **{alias: _BY_NAME[name] for alias, name in _DECLARED_ALIASES.items()},
    **{mime: _BY_EXTENSION[extension] for extension, mime in FORMAT_MIMES.items()},
    **{family.mime: family for family in FAMILIES},
}
_STORED_MIMES: Final = frozenset(
    {family.mime for family in FAMILIES} | set(FORMAT_MIMES.values())
)


def detect_mime(
    *, file_name: str, declared_mime: str, content: bytes, routed_mimes: Collection[str]
) -> str:
    """Return the MIME E0 stores for one arriving file (D138 §3).

    Extension first, a specific declared MIME second, the bytes last. A
    declared MIME is known when the registry lists it or the deployment's
    route table names it (the route overlay adds entries to the registry);
    a routed or registry-stored MIME is kept as declared, another registry
    spelling stores its family's canonical MIME.
    """
    by_name = _mime_for_file_name(file_name=file_name)
    if by_name is not None:
        return by_name
    declared = _canonical(mime=declared_mime)
    if declared not in _UNSPECIFIC_DECLARED:
        if declared in _STORED_MIMES or declared in routed_mimes:
            return declared
        if declared in _BY_MIME:
            return _BY_MIME[declared].mime
    if _looks_like_text(content=content):
        return _BY_NAME["other_text"].mime
    return _BY_NAME["binary"].mime


def family_for_mime(*, mime: str) -> FormatFamily:
    """The family a stored MIME belongs to; unknown types fall back by prefix.

    ``image/*`` is ``image``, ``audio/*`` and ``video/*`` are ``media``, other
    ``text/*`` is ``other_text``, and anything else is ``binary``. Parameters
    (``; charset=…``) and case are ignored.
    """
    canonical = _canonical(mime=mime)
    known = _BY_MIME.get(canonical)
    if known is not None:
        return known
    for prefix, name in (
        ("image/", "image"),
        ("audio/", "media"),
        ("video/", "media"),
        ("text/", "other_text"),
    ):
        if canonical.startswith(prefix):
            return _BY_NAME[name]
    return _BY_NAME["binary"]


def family_named(*, name: str) -> FormatFamily:
    """Look one family up by its name."""
    return _BY_NAME[name]


def stock_route_names(*, libreoffice_available: bool) -> dict[str, str]:
    """The engine's default MIME → converter table, derived from the registry.

    Every stored MIME of a family whose converter this build ships routes to
    it. The formats LibreOffice converts first route only when it is
    installed; without it they park (D117). The Office Open XML workbook
    keeps its markitdown route until the D138 spreadsheet converter exists.
    """
    routes = {
        mime: family.converter
        for extension, mime in FORMAT_MIMES.items()
        if (family := _BY_EXTENSION[extension]).converter is not None
        and (libreoffice_available or extension not in LIBREOFFICE_EXTENSIONS)
    }
    routes.update(
        {family.mime: family.converter for family in FAMILIES if family.converter}
    )
    routes.update({_XLSX: "markitdown"})
    return routes


def exceeds_reading_limit(*, mime: str, byte_size: int) -> bool:
    """Whether a file is over its family's reading limit (oversized card)."""
    limit = family_for_mime(mime=mime).reading_limit_bytes
    return limit is not None and byte_size > limit


def _mime_for_file_name(*, file_name: str) -> str | None:
    """Step 1: named files, dotfiles, compound tar and the extension."""
    name = PurePosixPath(file_name).name.lower()
    named = _NAMED_FILES.get(name)
    if named is not None:
        return _BY_NAME[named].mime
    if name.startswith(".env"):
        return _BY_NAME["config"].mime
    if name.endswith(_COMPOUND_TAR):
        return FORMAT_MIMES["tar"]
    suffix = PurePosixPath(name).suffix.removeprefix(".")
    family = _BY_EXTENSION.get(suffix)
    if family is not None:
        return FORMAT_MIMES.get(suffix, family.mime)
    if name.startswith(".") and not suffix:
        # dotfiles such as .gitignore, .editorconfig and .npmrc
        return _BY_NAME["config"].mime
    return None


def _looks_like_text(*, content: bytes) -> bool:
    """Step 3: valid UTF-8 with no NUL byte in the first 64 KiB."""
    head = content[:SNIFF_BYTES]
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as error:
        # a multi-byte character cut by the 64 KiB window is still text
        return (
            len(content) > SNIFF_BYTES
            and error.reason == "unexpected end of data"
            and error.end == len(head)
        )
    return True


def _canonical(*, mime: str) -> str:
    """Lower-case the MIME and drop its parameters."""
    return mime.split(";", 1)[0].strip().lower()
