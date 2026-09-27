"""The wall-time limit every D138 document converter runs under (D138 §9).

A parser reading untrusted bytes may hang on a crafted file. The parse runs
in a daemon thread; when it has not finished within the limit the version
fails with a typed ``ConversionError``. The thread cannot be killed, so it is
abandoned and ends with the process; LibreOffice runs as a child process and
is killed instead (``libreoffice.py``).
"""

from collections.abc import Callable
import threading
from typing import Final

from rememberstack.model import ConversionError

CONVERTER_TIME_LIMIT_S: Final = 120.0
"""Wall-time limit per conversion (D138 §9 starting value, 120 s)."""


def run_with_time_limit[T](*, work: Callable[[], T], what: str) -> T:
    """Run ``work`` and return its result, or fail after the time limit.

    An exception raised by ``work`` is re-raised unchanged in the caller.
    """
    results: list[T] = []
    errors: list[BaseException] = []

    def _target() -> None:
        try:
            results.append(work())
        except BaseException as err:  # re-raised in the calling thread
            errors.append(err)

    thread = threading.Thread(target=_target, name=f"convert-{what}", daemon=True)
    thread.start()
    thread.join(timeout=CONVERTER_TIME_LIMIT_S)
    if thread.is_alive():
        raise ConversionError(
            f"{what} did not finish within the {CONVERTER_TIME_LIMIT_S:g}-second "
            "conversion limit"
        )
    if errors:
        raise errors[0]
    return results[0]
