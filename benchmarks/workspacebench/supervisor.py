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
import time
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
    clock: Callable[[], float] = time.monotonic,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    killpg: Callable[[int, int], None] = os.killpg,
) -> SupervisedProcessResult:
    """Start a new session, wait, SIGTERM the group, then SIGKILL after grace."""
    if timeout_seconds <= 0:
        raise WorkspaceBenchError("timeout_seconds must be positive")
    if grace_seconds <= 0:
        raise WorkspaceBenchError("grace_seconds must be positive")
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    proc = popen(
        list(argv),
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=None if env is None else dict(env),
        start_new_session=True,
    )
    deadline = clock() + timeout_seconds
    timed_out = False
    try:
        while proc.poll() is None:
            remaining = deadline - clock()
            if remaining <= 0:
                timed_out = True
                _terminate_group(
                    proc=proc, grace_seconds=grace_seconds, killpg=killpg, clock=clock
                )
                break
            try:
                proc.wait(timeout=min(0.25, remaining))
            except subprocess.TimeoutExpired:
                continue
    finally:
        stdout_chunks.append(_read_pipe(proc.stdout))
        stderr_chunks.append(_read_pipe(proc.stderr))
    return SupervisedProcessResult(
        exit_code=proc.poll(),
        timed_out=timed_out,
        stdout=b"".join(stdout_chunks),
        stderr=b"".join(stderr_chunks),
    )


def _terminate_group(
    *,
    proc: subprocess.Popen[bytes],
    grace_seconds: float,
    killpg: Callable[[int, int], None],
    clock: Callable[[], float],
) -> None:
    if proc.poll() is not None:
        return
    _signal_group(proc=proc, sig=signal.SIGTERM, killpg=killpg)
    grace_deadline = clock() + grace_seconds
    while proc.poll() is None and clock() < grace_deadline:
        try:
            proc.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            continue
    if proc.poll() is None:
        _signal_group(proc=proc, sig=signal.SIGKILL, killpg=killpg)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _signal_group(
    *, proc: subprocess.Popen[bytes], sig: int, killpg: Callable[[int, int], None]
) -> None:
    if proc.pid is None:
        return
    try:
        killpg(proc.pid, sig)
    except ProcessLookupError:
        return
    except OSError:
        try:
            proc.terminate() if sig == signal.SIGTERM else proc.kill()
        except OSError:
            return


def _read_pipe(pipe: object) -> bytes:
    reader = getattr(pipe, "read", None)
    if not callable(reader):
        return b""
    try:
        data = reader()
    except OSError:
        return b""
    return data if isinstance(data, bytes) else b""
