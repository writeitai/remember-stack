"""Process-group supervisor for the SDK's missing wall-clock deadline."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
from typing import Final
from typing import Protocol

from benchmarks.workspacebench.errors import WorkspaceBenchError


@dataclass(frozen=True)
class SupervisedProcessResult:
    """Outcome of one supervised process group."""

    exit_code: int | None
    timed_out: bool
    stdout: bytes
    stderr: bytes


class ProcessGroupRunner(Protocol):
    """Injectable supervisor seam used by tests."""

    def __call__(
        self,
        *,
        argv: Sequence[str],
        cwd: Path,
        timeout_seconds: float,
        grace_seconds: float,
        env: Mapping[str, str] | None = None,
    ) -> SupervisedProcessResult:
        """Run argv in a new process group and kill the group on timeout."""
        ...


def run_process_group(
    *,
    argv: Sequence[str],
    cwd: Path,
    timeout_seconds: float,
    grace_seconds: float,
    env: Mapping[str, str] | None = None,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    killpg: Callable[[int, int], None] = os.killpg,
) -> SupervisedProcessResult:
    """Run argv in a new session; on timeout SIGTERM, then SIGKILL the group.

    ``communicate`` drains both pipes while waiting, so a chatty child cannot
    block on a full pipe. After the grace period the whole group is SIGKILLed
    unconditionally, and the final pipe read is bounded so an orphan that
    still holds a pipe cannot hang the supervisor.
    """
    if timeout_seconds <= 0:
        raise WorkspaceBenchError("timeout_seconds must be positive")
    if grace_seconds <= 0:
        raise WorkspaceBenchError("grace_seconds must be positive")
    proc = popen(
        list(argv),
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=None if env is None else dict(env),
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        pass
    else:
        return SupervisedProcessResult(
            exit_code=proc.returncode, timed_out=False, stdout=stdout, stderr=stderr
        )
    _signal_group(proc=proc, sig=signal.SIGTERM, killpg=killpg)
    drained = _communicate_bounded(proc=proc, seconds=grace_seconds)
    _signal_group(proc=proc, sig=signal.SIGKILL, killpg=killpg)
    if not drained.closed:
        drained = _communicate_bounded(proc=proc, seconds=_FINAL_READ_SEC)
    if not drained.closed:
        _close_pipes(proc)
    try:
        proc.wait(timeout=_FINAL_READ_SEC)
    except subprocess.TimeoutExpired:
        pass
    return SupervisedProcessResult(
        exit_code=proc.poll(),
        timed_out=True,
        stdout=drained.stdout,
        stderr=drained.stderr,
    )


# Starting point: how long to wait for pipes/reaping after SIGKILL.
_FINAL_READ_SEC: Final = 5.0


@dataclass(frozen=True)
class _Drained:
    stdout: bytes
    stderr: bytes
    closed: bool


def _communicate_bounded(*, proc: subprocess.Popen[bytes], seconds: float) -> _Drained:
    """Drain both pipes for at most ``seconds``; keep partial output on timeout."""
    try:
        stdout, stderr = proc.communicate(timeout=seconds)
    except subprocess.TimeoutExpired as error:
        return _Drained(
            stdout=_as_bytes(error.stdout), stderr=_as_bytes(error.stderr), closed=False
        )
    return _Drained(stdout=_as_bytes(stdout), stderr=_as_bytes(stderr), closed=True)


def _as_bytes(value: object) -> bytes:
    return value if isinstance(value, bytes) else b""


def _close_pipes(proc: subprocess.Popen[bytes]) -> None:
    """Stop reading pipes an orphan outside the group still holds open."""
    for pipe in (proc.stdout, proc.stderr):
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass


def _signal_group(
    *, proc: subprocess.Popen[bytes], sig: int, killpg: Callable[[int, int], None]
) -> None:
    try:
        killpg(proc.pid, sig)
    except ProcessLookupError:
        return
    except OSError:
        try:
            proc.terminate() if sig == signal.SIGTERM else proc.kill()
        except OSError:
            return
