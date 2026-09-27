"""The D138 ``text`` route: Markdown, plain text, code, configuration and logs.

A text file up to 1 MB is its own reading. Its family decides the range
label: prose families (``markdown``, ``text``) are ``passthrough`` and are
claim-extracted; search-only families label the text with their own name
(``code``, ``config``, ``log``, ``other_text``), which the extraction
eligibility policy excludes from Selection. A larger file is described by the
head/tail profile (D138 §5.2): its line and byte counts, the first 50 and the
last 20 lines, each cut to 500 characters, labelled ``large_text``.
"""

import codecs
from typing import Final

from rememberstack.core.format_registry import family_for_mime
from rememberstack.model import ConversionCoverage
from rememberstack.model import ConversionError
from rememberstack.model import ConversionResult
from rememberstack.model import ConverterManifest
from rememberstack.model import DerivationRange
from rememberstack.model import ManifestComponent

TEXT_CONVERTER_VERSION: Final = "text-2026.09"
"""Pins the text route: strict UTF-8, family labels, the head/tail profile."""

FULL_TEXT_LIMIT_BYTES: Final = 1_000_000
"""Text up to this size is read in full (D138 §4 starting value, 1 MB)."""

HEAD_LINES: Final = 50
TAIL_LINES: Final = 20
LINE_CHARS: Final = 500
"""The head/tail profile's line counts and per-line cut (D138 §5.2)."""

_LINE_BYTES: Final = LINE_CHARS * 4
"""The most bytes 500 UTF-8 characters can take; longer lines are cut
before decoding so a single enormous line is never decoded whole."""


class TextConverter:
    """Read a text-family file: in full up to 1 MB, else its head/tail profile."""

    @property
    def name(self) -> str:
        """The route name recorded on representations."""
        return "text"

    @property
    def version(self) -> str:
        """The pinned text route version (D38)."""
        return TEXT_CONVERTER_VERSION

    def convert(self, *, content: bytes, mime: str) -> ConversionResult:
        """Decode the bytes as UTF-8; undecodable input is a typed failure."""
        if len(content) > FULL_TEXT_LIMIT_BYTES:
            return _head_tail_profile(content=content, mime=mime)
        try:
            document_md = content.decode("utf-8")
        except UnicodeDecodeError as err:
            raise ConversionError(
                f"input stored as {mime!r} is not valid UTF-8 text"
            ) from err
        family = family_for_mime(mime=mime)
        kind = "passthrough" if family.outcome == "prose" else family.name
        return ConversionResult(
            document_md=document_md,
            manifest=ConverterManifest(
                components=(_component(),),
                coverage=ConversionCoverage(policy="full-text", complete=True),
                derivation_ranges=(
                    (
                        DerivationRange(
                            start=0,
                            end=len(document_md),
                            derivation_kind=kind,
                            evidence_mode="source_expression",
                        ),
                    )
                    if document_md
                    else ()
                ),
            ),
        )


def _head_tail_profile(*, content: bytes, mime: str) -> ConversionResult:
    """Describe a large text file by its counts, first and last lines."""
    line_count = content.count(b"\n") + (0 if content.endswith(b"\n") else 1)
    head = _first_lines(content=content, count=HEAD_LINES)
    tail = _last_lines(content=content, count=min(TAIL_LINES, line_count - len(head)))
    family = family_for_mime(mime=mime).name
    summary = (
        f"Large {family} file: {line_count:,} lines, {len(content):,} bytes. "
        f"Only the first {len(head)} and last {len(tail)} lines are shown, "
        f"each cut to {LINE_CHARS} characters; open the original for the rest.\n\n"
    )
    body = f"## First {len(head)} lines\n\n{_fenced(lines=head)}"
    if tail:
        body += f"\n## Last {len(tail)} lines\n\n{_fenced(lines=tail)}"
    document_md = summary + body
    omitted = line_count - len(head) - len(tail)
    return ConversionResult(
        document_md=document_md,
        manifest=ConverterManifest(
            components=(_component(),),
            coverage=ConversionCoverage(
                policy="head-tail",
                complete=False,
                gaps=(
                    (f"{omitted:,} middle lines not represented",)
                    if omitted > 0
                    else ("lines longer than 500 characters are cut",)
                ),
            ),
            derivation_ranges=(
                DerivationRange(
                    start=0,
                    end=len(summary),
                    derivation_kind="large_text",
                    evidence_mode="computed",
                ),
                DerivationRange(
                    start=len(summary),
                    end=len(document_md),
                    derivation_kind="large_text",
                    evidence_mode="source_expression",
                ),
            ),
        ),
    )


def _first_lines(*, content: bytes, count: int) -> list[str]:
    """The first ``count`` lines, each decoded and cut."""
    lines: list[str] = []
    start = 0
    while len(lines) < count and start < len(content):
        end = content.find(b"\n", start)
        end = len(content) if end == -1 else end
        lines.append(_cut_line(raw=content[start : min(end, start + _LINE_BYTES)]))
        start = end + 1
    return lines


def _last_lines(*, content: bytes, count: int) -> list[str]:
    """The last ``count`` lines, each decoded and cut, in file order."""
    lines: list[str] = []
    end = len(content) - 1 if content.endswith(b"\n") else len(content)
    while len(lines) < count and end > 0:
        start = content.rfind(b"\n", 0, end) + 1
        lines.append(_cut_line(raw=content[start : min(end, start + _LINE_BYTES)]))
        end = start - 1
    return lines[::-1]


def _cut_line(*, raw: bytes) -> str:
    """Decode one (possibly byte-cut) line strictly and cut it to 500 chars."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    try:
        # final=False: a character split by the byte cut is dropped, not an error
        text = decoder.decode(raw, final=len(raw) < _LINE_BYTES)
    except UnicodeDecodeError as err:
        raise ConversionError("large text file is not valid UTF-8 text") from err
    return text.rstrip("\r")[:LINE_CHARS]


def _fenced(*, lines: list[str]) -> str:
    """A code fence longer than any backtick run inside the lines."""
    longest = max((_longest_backtick_run(line=line) for line in lines), default=0)
    fence = "`" * max(3, longest + 1)
    return fence + "text\n" + "\n".join(lines) + "\n" + fence + "\n"


def _longest_backtick_run(*, line: str) -> int:
    """The longest run of consecutive backticks in one line."""
    longest = current = 0
    for character in line:
        current = current + 1 if character == "`" else 0
        longest = max(longest, current)
    return longest


def _component() -> ManifestComponent:
    """The route's single local component."""
    return ManifestComponent(
        name="text", version=TEXT_CONVERTER_VERSION, execution="library-local"
    )
