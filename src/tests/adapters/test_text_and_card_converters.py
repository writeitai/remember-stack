"""D138 text and card routes: labels, the head/tail threshold and card fields."""

import dataclasses
import io
import tarfile
from typing import Literal
import zipfile

from PIL import Image
import pytest

from rememberstack.adapters.converters.card import CardConverter
from rememberstack.adapters.converters.card import MAX_LISTED_MEMBERS
from rememberstack.adapters.converters.text import FULL_TEXT_LIMIT_BYTES
from rememberstack.adapters.converters.text import TextConverter
from rememberstack.core import file_card as card_module
from rememberstack.core import FileHintConverter
from rememberstack.core.format_registry import family_for_mime
from rememberstack.model import ConversionError
from rememberstack.model import FileHints

_HINTS = FileHints(file_name="bundle.zip", source_path="exports/2025")


@pytest.mark.parametrize(
    ("mime", "kind"),
    (
        ("text/markdown", "passthrough"),
        ("text/plain", "passthrough"),
        ("text/x-code", "code"),
        ("text/x-config", "config"),
        ("text/x-log", "log"),
        ("text/x-other-text", "other_text"),
    ),
)
def test_text_is_read_in_full_and_labelled_by_family(mime: str, kind: str) -> None:
    """Prose stays passthrough (extracted); search-only text carries its family."""
    content = "def main():\n    return 1\n".encode()
    result = TextConverter().convert(content=content, mime=mime)
    assert result.document_md == content.decode()
    (labeled,) = result.manifest.derivation_ranges
    assert (labeled.derivation_kind, labeled.evidence_mode) == (
        kind,
        "source_expression",
    )
    assert (labeled.start, labeled.end) == (0, len(result.document_md))
    assert result.manifest.coverage.complete


def test_text_routed_from_an_unregistered_mime_is_search_only() -> None:
    """An overlay route (application/x-custom → text) never yields prose."""
    result = TextConverter().convert(content=b"notes\n", mime="application/x-custom")
    (labeled,) = result.manifest.derivation_ranges
    assert labeled.derivation_kind == "other_text"
    html = TextConverter().convert(content=b"<p>x</p>\n", mime="text/html")
    assert html.manifest.derivation_ranges[0].derivation_kind == "other_text"


def test_large_text_validates_the_whole_input_as_utf8() -> None:
    """Invalid bytes outside the shown lines still fail the version."""
    lines = b"".join(f"row {index}\n".encode() for index in range(200_000))
    middle = len(lines) // 2
    corrupt = lines[:middle] + b"\xff" + lines[middle:]
    assert len(corrupt) > FULL_TEXT_LIMIT_BYTES
    with pytest.raises(ConversionError, match="not valid UTF-8"):
        TextConverter().convert(content=corrupt, mime="text/x-log")


def test_empty_text_has_no_ranges_and_invalid_utf8_fails_typed() -> None:
    """An empty file is an empty reading; undecodable bytes fail the version."""
    assert TextConverter().convert(content=b"", mime="text/x-code").document_md == ""
    with pytest.raises(ConversionError, match="not valid UTF-8"):
        TextConverter().convert(content=b"caf\xe9\n", mime="text/plain")


def test_the_one_megabyte_threshold_switches_to_the_head_tail_profile() -> None:
    """Exactly 1 MB is read in full; one byte more is described, not read."""
    line = b"x" * 99 + b"\n"
    full = line * (FULL_TEXT_LIMIT_BYTES // len(line))
    assert len(full) == FULL_TEXT_LIMIT_BYTES
    read = TextConverter().convert(content=full, mime="text/plain")
    assert read.manifest.derivation_ranges[0].derivation_kind == "passthrough"
    profiled = TextConverter().convert(content=full + b"y", mime="text/plain")
    assert {r.derivation_kind for r in profiled.manifest.derivation_ranges} == {
        "large_text"
    }


def test_head_tail_profile_counts_and_cuts_lines() -> None:
    """Line count, byte size, first 50 and last 20 lines, each cut to 500 chars."""
    lines = [f"row {index:05d}\t" + "v" * 700 for index in range(1, 2001)]
    content = ("\n".join(lines) + "\n").encode()
    assert len(content) > FULL_TEXT_LIMIT_BYTES
    result = TextConverter().convert(content=content, mime="text/plain")
    document = result.document_md
    assert f"2,000 lines, {len(content):,} bytes" in document
    assert "row 00001" in document and "row 00050" in document
    assert "row 00051" not in document and "row 01980" not in document
    assert "row 01981" in document and "row 02000" in document
    shown = [row for row in document.splitlines() if row.startswith("row ")]
    assert len(shown) == 70
    assert all(len(row) == 500 for row in shown)
    summary, body = result.manifest.derivation_ranges
    assert (summary.derivation_kind, summary.evidence_mode) == (
        "large_text",
        "computed",
    )
    assert (body.derivation_kind, body.evidence_mode) == (
        "large_text",
        "source_expression",
    )
    assert body.end == len(document)
    assert result.manifest.coverage.complete is False
    assert result.manifest.coverage.gaps == ("1,930 middle lines not represented",)


def test_head_tail_profile_reads_one_enormous_line_without_decoding_it() -> None:
    """A 2 MB single-line JSON export becomes one cut line, not 2 MB of text."""
    content = b'{"rows": [' + b'"value", ' * 250_000 + b'"end"]}'
    result = TextConverter().convert(content=content, mime="text/x-config")
    assert "1 lines" in result.document_md
    assert "All 1 lines are shown" in result.document_md
    assert "First 1 lines" in result.document_md
    assert "Last" not in result.document_md
    assert len(result.document_md) < 2_000


def test_card_is_a_file_hint_converter_and_labels_the_whole_card() -> None:
    """The card names the file and is file_card/computed, policy card, incomplete."""
    converter = CardConverter()
    assert isinstance(converter, FileHintConverter)
    assert not isinstance(TextConverter(), FileHintConverter)
    result = converter.convert(
        content=b"\x00\x01binary",
        mime="application/octet-stream",
        hints=FileHints(file_name="save.dat", source_path="games/saves"),
    )
    document = result.document_md
    assert document.startswith("# save.dat\n")
    for field in (
        "- File name: save.dat",
        "- Source path: games/saves",
        "- Family: binary",
        "- Format: application/octet-stream",
        "- Byte size: 8 bytes",
    ):
        assert field in document
    (labeled,) = result.manifest.derivation_ranges
    assert (labeled.derivation_kind, labeled.evidence_mode) == ("file_card", "computed")
    assert (labeled.start, labeled.end) == (0, len(document))
    assert result.manifest.coverage.policy == "card"
    assert result.manifest.coverage.complete is False


def test_image_card_reads_dimensions_from_the_header() -> None:
    """Width, height and format come from Pillow's header read."""
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48)).save(buffer, format="PNG")
    result = CardConverter().convert(
        content=buffer.getvalue(),
        mime="image/png",
        hints=FileHints(file_name="chart.png", source_path=None),
    )
    assert "- Family: image" in result.document_md
    assert "- Dimensions: 64 × 48 px, PNG" in result.document_md
    assert "- Source path: unknown" in result.document_md


def test_unreadable_image_formats_are_carded_but_corrupt_ones_fail() -> None:
    """HEIC is unreadable by design (warned); a bad PNG header contradicts it."""
    result = CardConverter().convert(content=b"not an image", mime="image/heic")
    assert "- Dimensions: not read (this runtime cannot read" in result.document_md
    assert result.warnings and "image dimensions not read" in result.warnings[0]
    with pytest.raises(ConversionError, match="not a readable image"):
        CardConverter().convert(content=b"not an image", mime="image/png")


def test_media_card_has_only_the_common_fields() -> None:
    """Media is described by name, family, format and size alone."""
    result = CardConverter().convert(content=b"ID3....", mime="audio/mpeg")
    assert "- Family: media" in result.document_md
    assert "## Members" not in result.document_md
    assert "Dimensions" not in result.document_md


def test_zip_card_counts_every_member_and_lists_the_first_200() -> None:
    """The central directory gives the full count; the listing is capped."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for index in range(MAX_LISTED_MEMBERS + 5):
            archive.writestr(f"data/file-{index:03d}.txt", "x" * index)
    result = CardConverter().convert(
        content=buffer.getvalue(), mime="application/zip", hints=_HINTS
    )
    document = result.document_md
    assert "## Members (205)" in document
    assert "The first 200 members are listed." in document
    assert "- data/file-000.txt (0 bytes)" in document
    assert "- data/file-199.txt (199 bytes)" in document
    assert "file-200.txt" not in document


def _tar(
    *, members: int, compression: Literal["", "gz", "bz2"], member_size: int = 3
) -> bytes:
    """A generated tar archive with numbered members of a fixed size."""
    modes: dict[str, Literal["w", "w:gz", "w:bz2"]] = {
        "": "w",
        "gz": "w:gz",
        "bz2": "w:bz2",
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=modes[compression]) as archive:
        for index in range(members):
            info = tarfile.TarInfo(name=f"logs/{index:03d}.log")
            info.size = member_size
            archive.addfile(info, io.BytesIO(b"x" * member_size))
    return buffer.getvalue()


def test_compressed_tar_card_lists_every_member_when_under_the_limits() -> None:
    """A .tar.gz is listed by a streamed read; a complete listing is not partial."""
    result = CardConverter().convert(
        content=_tar(members=3, compression="gz"), mime="application/x-tar"
    )
    assert "## Members (3)" in result.document_md
    assert "- logs/002.log (3 bytes)" in result.document_md
    assert "partial" not in result.document_md


def test_tar_card_stops_after_200_members_and_says_it_is_partial() -> None:
    """The streamed read stops at the member cap and marks the list partial."""
    result = CardConverter().convert(
        content=_tar(members=MAX_LISTED_MEMBERS + 10, compression=""),
        mime="application/x-tar",
    )
    assert "## Members (at least 200; the listing is partial)" in result.document_md
    assert "- logs/199.log" in result.document_md
    assert "logs/200.log" not in result.document_md


def test_tar_card_stops_once_the_scan_passes_the_byte_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A member reaching past the scan limit ends the listing, marked partial."""
    monkeypatch.setattr(card_module, "MAX_TAR_SCAN_BYTES", 4_000)
    result = CardConverter().convert(
        content=_tar(members=5, compression="bz2", member_size=1_500),
        mime="application/x-tar",
    )
    assert "## Members (at least 2; the listing is partial)" in result.document_md
    assert "logs/002.log" not in result.document_md


def test_corrupt_archives_fail_and_zstd_tar_is_carded_unlisted() -> None:
    """A zip or tar that is not one fails; zstd tar cannot be read here."""
    with pytest.raises(ConversionError, match="not one"):
        CardConverter().convert(content=b"PK-but-broken", mime="application/zip")
    with pytest.raises(ConversionError, match="not a readable one"):
        CardConverter().convert(content=b"\x1f\x8bgarbage", mime="application/x-tar")
    zstd = CardConverter().convert(
        content=b"\x28\xb5\x2f\xfd rest", mime="application/x-tar"
    )
    assert "Not listed: zstd-compressed tar" in zstd.document_md
    assert zstd.warnings


def test_a_compressed_pax_header_cannot_expand_past_the_scan_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bound applies to decompressed bytes before tarfile parses a header."""
    monkeypatch.setattr(card_module, "MAX_TAR_SCAN_BYTES", 50_000)
    buffer = io.BytesIO()
    with tarfile.open(
        fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT
    ) as archive:
        info = tarfile.TarInfo(name="p" * 400_000)
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    probe = buffer.getvalue()
    assert len(probe) < 5_000
    result = CardConverter().convert(content=probe, mime="application/x-tar")
    assert "the listing is partial" in result.document_md
    assert len(result.document_md) < 2_000


def test_member_paths_and_names_are_cut_and_kept_on_one_line() -> None:
    """Long member paths are cut to 300 chars; newlines never leave a line."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("d/" + "n" * 1_000, "x")
        archive.writestr("evil\n# heading", "x")
    result = CardConverter().convert(
        content=buffer.getvalue(),
        mime="application/zip",
        hints=FileHints(file_name="a\r\n# b.zip", source_path="x\ny"),
    )
    lines = result.document_md.splitlines()
    assert lines[0] == "# a  # b.zip"
    assert "- Source path: x y" in lines
    assert "- d/" + "n" * 298 + " (1 bytes)" in lines
    assert "- evil # heading (1 bytes)" in lines
    assert not any(line.startswith("# heading") for line in lines)


def test_other_archive_formats_get_the_card_without_a_listing() -> None:
    """7z, rar and single-stream compression are carded without members."""
    result = CardConverter().convert(
        content=b"7z\xbc\xaf'\x1c", mime="application/x-7z-compressed"
    )
    assert "- Family: archive" in result.document_md
    assert "## Members" not in result.document_md


def test_oversized_file_card_states_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """A file over its family's reading limit is carded with the limit named."""
    small_limit = dataclasses.replace(
        family_for_mime(mime="application/pdf"), reading_limit_bytes=10
    )
    monkeypatch.setattr(card_module, "family_for_mime", lambda *, mime: small_limit)
    result = CardConverter().convert(
        content=b"%PDF-1.7 more than ten bytes",
        mime="application/pdf",
        hints=FileHints(file_name="scan.pdf", source_path="archive"),
    )
    assert "- Family: pdf" in result.document_md
    assert (
        "- Not read: the file is larger than the 10-byte reading limit for pdf files"
        in result.document_md
    )
