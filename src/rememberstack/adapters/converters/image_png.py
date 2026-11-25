"""Convert one image to PNG in a separate, resource-bounded process.

The image route converts GIF, TIFF, BMP, HEIC, AVIF and PSD to one PNG for
both lanes. Decoders can need far more memory or time than an input's bytes
or displayed size suggest (frame buffers, tile geometry, coded images larger
than their crop), so the conversion runs in a child process capped at
``memory_bytes`` of address space and ``timeout_s`` seconds. A file that
breaks either cap is a bad file, as is one Pillow cannot read.

Run as ``python -m rememberstack.adapters.converters.image_png``: the input
on stdin, the JSON options as the only argument, the PNG on stdout; exit 2
with a message on stderr for a bad file.
"""

import io
import json
import subprocess
import sys
from typing import Final

from PIL import Image
from PIL import ImageCms
from PIL import ImageOps
from PIL import UnidentifiedImageError

from rememberstack.model import ConversionError

PNG_CONVERSION_MEMORY_BYTES: Final = 1536 * 2**20
"""Address-space cap of the conversion process. Starting point to measure."""

PNG_CONVERSION_TIMEOUT_S: Final = 60.0
"""Wall-clock cap of the conversion process. Starting point to measure."""

_BAD_FILE_EXIT: Final = 2


def decode_isolated(
    *,
    content: bytes,
    formats: tuple[str, ...],
    max_pixels: int,
    max_width: int,
    max_height: int,
) -> None:
    """Prove the image decodes, under the same caps, without converting it.

    For a format sent to the lanes as uploaded: a header check alone lets a
    truncated pixel payload reach both providers.
    """
    _run_child(
        content=content,
        formats=formats,
        max_pixels=max_pixels,
        max_width=max_width,
        max_height=max_height,
        max_png_bytes=0,
        decode_only=True,
    )


def convert_isolated(
    *,
    content: bytes,
    formats: tuple[str, ...],
    max_pixels: int,
    max_width: int,
    max_height: int,
    max_png_bytes: int,
) -> tuple[bytes, bool]:
    """Run :func:`to_png` in a child process under the memory and time caps.

    Returns the PNG and whether the file holds further frames or pages that
    were not read. The child refuses a PNG over ``max_png_bytes`` itself, so
    the parent never receives more than that.
    """
    return _run_child(
        content=content,
        formats=formats,
        max_pixels=max_pixels,
        max_width=max_width,
        max_height=max_height,
        max_png_bytes=max_png_bytes,
        decode_only=False,
    )


def _run_child(
    *,
    content: bytes,
    formats: tuple[str, ...],
    max_pixels: int,
    max_width: int,
    max_height: int,
    max_png_bytes: int,
    decode_only: bool,
) -> tuple[bytes, bool]:
    """One child run: the PNG and whether images were left out, or a bad file."""
    options = {
        "formats": list(formats),
        "max_pixels": max_pixels,
        "max_width": max_width,
        "max_height": max_height,
        "max_png_bytes": max_png_bytes,
        "memory_bytes": PNG_CONVERSION_MEMORY_BYTES,
        "decode_only": decode_only,
    }
    try:
        done = subprocess.run(
            [sys.executable, "-m", __name__, json.dumps(options)],
            input=content,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=PNG_CONVERSION_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as err:
        raise ConversionError(
            f"converting the {formats[0]} image took longer than "
            f"{PNG_CONVERSION_TIMEOUT_S:g} s"
        ) from err
    if done.returncode == 0 and len(done.stdout) > 1:
        return done.stdout[1:], done.stdout[:1] == b"1"
    if done.returncode == _BAD_FILE_EXIT:
        raise ConversionError(done.stdout[:500].decode("utf-8", "replace").strip())
    raise ConversionError(
        f"converting the {formats[0]} image exceeded its memory or failed "
        f"(exit {done.returncode})"
    )


def to_png(
    *,
    content: bytes,
    formats: tuple[str, ...],
    max_pixels: int,
    max_width: int,
    max_height: int,
) -> tuple[bytes, bool]:
    """Convert one supported image to PNG: the image Pillow opens, upright, in sRGB.

    Pillow opens a GIF on its first frame, a TIFF on its first page, a PSD on
    its composite and a HEIC on its primary image; that image is converted.
    The pixel ceiling is checked from the header before any pixel is decoded.
    An embedded ICC profile of an RGB or CMYK image is applied, then dropped;
    16-bit samples are scaled, not clipped. Colour carried only as metadata
    (HEIC NCLX, AVIF CICP, BMP V5 colour spaces) is not applied. A file Pillow cannot
    read as one of ``formats`` is a bad file.
    """
    if "HEIF" in formats:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    if "TIFF" in formats:
        # libtiff, not Pillow's own reader, decodes every TIFF layout (planar
        # 16-bit RGB among them) correctly.
        from PIL import TiffImagePlugin

        TiffImagePlugin.READ_LIBTIFF = True
    if "PSD" in formats and len(content) >= 26:
        # Pillow locates the composite by its decoded channels, so any other
        # channel count (selections, spot colours) would shift the read.
        channels = int.from_bytes(content[12:14], "big")
        colour_mode = int.from_bytes(content[24:26], "big")
        if channels not in {1: (1,), 2: (1,), 3: (3, 4), 4: (4,)}.get(colour_mode, ()):
            raise ConversionError(
                f"a PSD with {channels} channels in colour mode {colour_mode} "
                "is not read"
            )
        if not _psd_has_real_composite(content=content):
            raise ConversionError(
                "the PSD has no real composite image (saved without maximized "
                "compatibility)"
            )
    try:
        with Image.open(io.BytesIO(content), formats=formats) as source:
            width, height = source.size
            if width * height > max_pixels:
                raise ConversionError(
                    f"image pixel count {width * height} exceeds the configured "
                    f"ceiling of {max_pixels}"
                )
            # Either orientation: EXIF may turn the image before the PNG.
            if not (
                (width <= max_width and height <= max_height)
                or (height <= max_width and width <= max_height)
            ):
                raise ConversionError(
                    f"image dimensions {width}x{height} exceed the configured "
                    f"ceiling of {max_width}x{max_height}"
                )
            tags = getattr(source, "tag_v2", None)
            bits = int(tags.get(258, (16,))[0]) if tags is not None else 16
            white_is_zero = tags is not None and tags.get(262) == 0
            if tags is not None and 2 in tuple(tags.get(339, (1,))):
                raise ConversionError(
                    "signed, 32-bit or floating-point samples have no defined "
                    "display mapping"
                )
            image = ImageOps.exif_transpose(source)
            if image.mode in ("La", "RGBa"):
                # Premultiplied alpha: straight alpha before any transform.
                image = image.convert("LA" if image.mode == "La" else "RGBA")
            icc = image.info.get("icc_profile")
            if icc and image.mode in ("P", "PA"):
                # A palette image's profile describes its RGB colours.
                transparent = image.mode == "PA" or "transparency" in image.info
                image = image.convert("RGBA" if transparent else "RGB")
                image.info["icc_profile"] = icc
            if icc and image.mode in ("RGB", "RGBA", "CMYK"):
                converted = ImageCms.profileToProfile(
                    image,
                    ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                    ImageCms.createProfile("sRGB"),
                    outputMode="RGBA" if image.mode == "RGBA" else "RGB",
                )
                assert converted is not None
                image = converted
            if image.mode in ("I;16", "I;16B", "I;16L", "I;16N"):
                # Unsigned samples of up to 16 bits: scale their own range to
                # 8 bits (not clip at 255), white-is-zero inverted.
                scale = 255 / ((1 << min(max(bits, 1), 16)) - 1)
                image = image.convert("I").point(lambda value: value * scale)
                image = image.convert("L")
                if white_is_zero:
                    image = ImageOps.invert(image)
            elif image.mode in ("I", "F"):
                raise ConversionError(
                    "signed, 32-bit or floating-point samples have no defined "
                    "display mapping"
                )
            # A PSD's fourth channel may be a saved selection, not opacity,
            # and libtiff can lose a planar TIFF's alpha plane: both are read
            # as opaque, so their colours always reach the lanes.
            alpha = source.format not in ("PSD", "TIFF") and (
                image.mode in ("RGBA", "LA", "PA")
                or (image.mode == "P" and "transparency" in image.info)
            )
            image = image.convert("RGBA" if alpha else "RGB")
            image.info.pop("icc_profile", None)
            output = io.BytesIO()
            image.save(output, format="PNG")
            more = _has_more_images(source=source)
            return output.getvalue(), more
    except Image.DecompressionBombError as err:
        raise ConversionError("image exceeds Pillow's pixel safety ceiling") from err
    except (
        UnidentifiedImageError,
        ImageCms.PyCMSError,
        OSError,
        ValueError,
        SyntaxError,
        EOFError,
        KeyError,  # Pillow: an unsupported PSD depth or TIFF compression
    ) as err:
        raise ConversionError(f"input is not a readable {formats[0]} image") from err


def _psd_has_real_composite(*, content: bytes) -> bool:
    """False when the PSD's version info (resource 1057) says it has no composite.

    Without that flag's ``hasRealMergedData`` the composite Pillow reads is a
    placeholder. A file without the resource, or one too short to walk, is
    left to the decoder.
    """
    offset = 26
    if len(content) < offset + 4:
        return True
    offset += 4 + int.from_bytes(content[offset : offset + 4], "big")  # colour data
    if len(content) < offset + 4:
        return True
    end = offset + 4 + int.from_bytes(content[offset : offset + 4], "big")
    offset += 4
    while (
        offset + 12 <= min(end, len(content))
        and content[offset : offset + 4] == b"8BIM"
    ):
        resource = int.from_bytes(content[offset + 4 : offset + 6], "big")
        name_length = content[offset + 6]
        offset += 6 + name_length + 1 + ((name_length + 1) % 2)  # padded name
        size = int.from_bytes(content[offset : offset + 4], "big")
        offset += 4
        if resource == 1057 and size >= 5 and offset + 5 <= len(content):
            return content[offset + 4] != 0
        offset += size + (size % 2)
    return True


def _decode(
    *,
    content: bytes,
    formats: tuple[str, ...],
    max_pixels: int,
    max_width: int,
    max_height: int,
) -> None:
    """Open, bound and decode one static image; nothing is converted or kept."""
    try:
        with Image.open(io.BytesIO(content), formats=formats) as source:
            width, height = source.size
            if width * height > max_pixels or width > max_width or height > max_height:
                raise ConversionError(
                    f"image of {width}x{height} pixels exceeds the configured ceiling"
                )
            if bool(getattr(source, "is_animated", False)):
                raise ConversionError("animated PNG or WebP images are not accepted")
            source.load()
    except Image.DecompressionBombError as err:
        raise ConversionError("image exceeds Pillow's pixel safety ceiling") from err
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError) as err:
        raise ConversionError(f"input is not a readable {formats[0]} image") from err


def _has_more_images(*, source: Image.Image) -> bool:
    """Whether a frame, page or image beyond the converted one exists.

    A PSD's further "frames" are its layers, already in the composite. A
    later frame or page that cannot be read still counts as left out; it
    never fails the first one.
    """
    if source.format == "PSD":
        return False
    try:
        return bool(getattr(source, "is_animated", False))
    except Exception:  # error-contract: expected (a later page)
        return True


def _limit_memory(*, memory_bytes: int) -> None:
    """Cap this process's address space, never above an inherited cap.

    Linux must install the cap; other platforms (macOS) may not support it.
    """
    try:
        import resource
    except ImportError:
        return
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    limits = [memory_bytes] + [
        value for value in (soft, hard) if value != resource.RLIM_INFINITY
    ]
    cap = min(limits)
    try:
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    except (ValueError, OSError):
        if sys.platform.startswith("linux"):
            raise


def main() -> int:
    """Child entry point: options in argv, image on stdin, PNG on stdout."""
    options = json.loads(sys.argv[1])
    _limit_memory(memory_bytes=int(options["memory_bytes"]))  # fails closed on Linux
    if options.get("decode_only"):
        try:
            _decode(
                content=sys.stdin.buffer.read(),
                formats=tuple(options["formats"]),
                max_pixels=int(options["max_pixels"]),
                max_width=int(options["max_width"]),
                max_height=int(options["max_height"]),
            )
        except ConversionError as err:
            sys.stdout.write(str(err)[:500])
            return _BAD_FILE_EXIT
        sys.stdout.buffer.write(b"0-")  # decoded; the bytes go on unchanged
        return 0
    try:
        png, more = to_png(
            content=sys.stdin.buffer.read(),
            formats=tuple(options["formats"]),
            max_pixels=int(options["max_pixels"]),
            max_width=int(options["max_width"]),
            max_height=int(options["max_height"]),
        )
    except ConversionError as err:
        sys.stdout.write(str(err)[:500])
        return _BAD_FILE_EXIT
    if len(png) > int(options["max_png_bytes"]):
        sys.stdout.write(
            f"image of {len(png)} bytes as PNG exceeds the configured "
            f"image_ocr_description ceiling of {options['max_png_bytes']}"
        )
        return _BAD_FILE_EXIT
    sys.stdout.buffer.write((b"1" if more else b"0") + png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
