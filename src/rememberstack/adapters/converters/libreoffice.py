"""LibreOffice as a format converter for legacy and OpenDocument files (D138 §7).

``soffice --headless --convert-to`` turns doc, odt and rtf into docx, ppt and
odp into pptx, and ods into xlsx; the office and spreadsheet converters then
read the Office Open XML result. Each call is one process with a fresh
temporary profile directory (``-env:UserInstallation``), a 120-second limit
after which the whole process group is killed, and a temporary working
directory removed afterwards. A deployment without ``soffice`` does not
route these formats at all, so they park under D117 until it is installed.
"""

import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
from typing import Final
from typing import Literal

from rememberstack.adapters.converters.time_limit import CONVERTER_TIME_LIMIT_S
from rememberstack.model import ConversionError

SOFFICE_BINARY: Final = "soffice"


def libreoffice_available() -> bool:
    """Whether ``soffice`` is on this process's PATH."""
    return shutil.which(SOFFICE_BINARY) is not None


def convert_with_libreoffice(
    *, content: bytes, source_extension: str, target: Literal["docx", "pptx", "xlsx"]
) -> bytes:
    """Convert one file with LibreOffice and return the converted bytes.

    Raises ``ConversionError`` when LibreOffice is missing, times out, exits
    with an error or writes no output (a corrupt input).
    """
    soffice = shutil.which(SOFFICE_BINARY)
    if soffice is None:
        raise ConversionError(
            f"LibreOffice ({SOFFICE_BINARY}) is not installed; "
            f"cannot convert .{source_extension} to .{target}"
        )
    with tempfile.TemporaryDirectory(prefix="rememberstack-soffice-") as work:
        work_dir = Path(work)
        source = work_dir / f"input.{source_extension}"
        source.write_bytes(content)
        output_dir = work_dir / "output"
        output_dir.mkdir()
        command = [
            soffice,
            f"-env:UserInstallation={(work_dir / 'profile').as_uri()}",
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--convert-to",
            target,
            "--outdir",
            str(output_dir),
            str(source),
        ]
        with subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        ) as process:
            try:
                _, stderr = process.communicate(timeout=CONVERTER_TIME_LIMIT_S)
            except subprocess.TimeoutExpired as err:
                # soffice starts helper processes; kill the whole group
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass  # the group exited between the timeout and the kill
                process.communicate()
                raise ConversionError(
                    f"LibreOffice did not convert .{source_extension} to .{target} "
                    f"within {CONVERTER_TIME_LIMIT_S:g} seconds"
                ) from err
        converted = output_dir / f"input.{target}"
        if process.returncode != 0 or not converted.is_file():
            detail = stderr.decode("utf-8", errors="replace").strip()[-500:]
            raise ConversionError(
                f"LibreOffice could not convert .{source_extension} to .{target} "
                f"(exit {process.returncode}){': ' + detail if detail else ''}"
            )
        return converted.read_bytes()
