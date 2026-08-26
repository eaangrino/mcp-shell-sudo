from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .config import Settings
from .policy import PolicyError, resolve_directory, validate_command

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_TIMEOUT_SECONDS = 600.0
OUTPUT_LIMIT_BYTES = 2 * 1024 * 1024
TRUSTED_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    stdout: str
    stderr: str
    status: int
    execution_time: float
    timed_out: bool = False
    output_limited: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "status": self.status,
            "execution_time": round(self.execution_time, 6),
            "timed_out": self.timed_out,
            "output_limited": self.output_limited,
        }


class ShellExecutor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sudo_path = shutil.which("sudo")
        self.bwrap_path = shutil.which("bwrap")

        if self.settings.work_dir is not None and self.bwrap_path is None:
            raise RuntimeError(
                "WORK_DIR requires bubblewrap (bwrap) for strict filesystem confinement. "
                "Install the 'bubblewrap' package or unset WORK_DIR."
            )

    async def execute(
        self,
        command: Sequence[str],
        *,
        stdin: str | None = None,
        directory: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> ExecutionResult:
        started = time.monotonic()
        timeout = _normalize_timeout(timeout)
        request = validate_command(command, self.settings)
        cwd = resolve_directory(directory, self.settings)

        child_env = _build_child_env(self.settings, cwd)
        process_argv, process_stdin = self._build_process_argv_and_stdin(
            request.target_argv,
            elevated=request.elevated,
            stdin=stdin,
            cwd=cwd,
            child_env=child_env,
        )

        logger.info(
            "Executing command=%s elevated=%s cwd=%s",
            request.executable_name,
            request.elevated,
            cwd,
        )

        process = await asyncio.create_subprocess_exec(
            *process_argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env=child_env,
            start_new_session=True,
        )

        assert process.stdout is not None
        assert process.stderr is not None

        output_limit_event = asyncio.Event()
        stdout_task = asyncio.create_task(
            _read_limited(process.stdout, OUTPUT_LIMIT_BYTES, output_limit_event)
        )
        stderr_task = asyncio.create_task(
            _read_limited(process.stderr, OUTPUT_LIMIT_BYTES, output_limit_event)
        )
        wait_task = asyncio.create_task(process.wait())
        limit_task = asyncio.create_task(output_limit_event.wait())

        if process.stdin is not None:
            process.stdin.write(process_stdin)
            await process.stdin.drain()
            process.stdin.close()

        timed_out = False
        output_limited = False
        try:
            done, _ = await asyncio.wait(
                {wait_task, limit_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if not done:
                timed_out = True
                await _terminate_process_group(process)
            elif limit_task in done and output_limit_event.is_set() and not wait_task.done():
                output_limited = True
                await _terminate_process_group(process)

            if not wait_task.done():
                await wait_task
        finally:
            limit_task.cancel()

        stdout_bytes, stdout_truncated = await stdout_task
        stderr_bytes, stderr_truncated = await stderr_task
        output_limited = output_limited or stdout_truncated or stderr_truncated

        status = process.returncode if process.returncode is not None else 1
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        if timed_out:
            status = 124
            stderr_text = _append_message(stderr_text, f"Command timed out after {timeout:g}s")
        if output_limited:
            status = 125
            stderr_text = _append_message(
                stderr_text,
                f"Output exceeded the {OUTPUT_LIMIT_BYTES}-byte per-stream limit",
            )

        return ExecutionResult(
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_text,
            status=status,
            execution_time=time.monotonic() - started,
            timed_out=timed_out,
            output_limited=output_limited,
        )

    def _build_process_argv_and_stdin(
        self,
        target_argv: tuple[str, ...],
        *,
        elevated: bool,
        stdin: str | None,
        cwd: Path,
        child_env: dict[str, str],
    ) -> tuple[list[str], bytes]:
        payload = (stdin or "").encode()

        if self.settings.work_dir is not None:
            assert self.bwrap_path is not None
            return self._build_bwrap_argv(target_argv, cwd, child_env), payload

        if not elevated:
            return list(target_argv), payload

        if self.sudo_path is None:
            raise PolicyError("sudo was requested but the sudo executable is not available")

        if self.settings.sudo_password is not None:
            password_prefix = (self.settings.sudo_password + "\n").encode()
            argv = [self.sudo_path, "-S", "-p", "", "--", *target_argv]
            return argv, password_prefix + payload

        argv = [self.sudo_path, "-n", "--", *target_argv]
        return argv, payload

    def _build_bwrap_argv(
        self,
        target_argv: tuple[str, ...],
        cwd: Path,
        child_env: dict[str, str],
    ) -> list[str]:
        assert self.bwrap_path is not None
        assert self.settings.work_dir is not None

        argv = [
            self.bwrap_path,
            "--die-with-parent",
            "--new-session",
            "--unshare-pid",
            "--ro-bind",
            "/",
            "/",
            "--bind",
            str(self.settings.work_dir),
            str(self.settings.work_dir),
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/run",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--chdir",
            str(cwd),
            "--clearenv",
        ]

        for key, value in child_env.items():
            argv.extend(["--setenv", key, value])

        argv.extend(["--", *target_argv])
        return argv


def _normalize_timeout(timeout: float) -> float:
    try:
        value = float(timeout)
    except (TypeError, ValueError) as exc:
        raise PolicyError("timeout must be a number") from exc
    if value <= 0:
        raise PolicyError("timeout must be greater than zero")
    return min(value, MAX_TIMEOUT_SECONDS)


def _build_child_env(settings: Settings, cwd: Path) -> dict[str, str]:
    source = os.environ
    home = str(settings.work_dir or Path(source.get("HOME", str(cwd))).expanduser())

    env = {
        "PATH": TRUSTED_PATH,
        "HOME": home,
        "PWD": str(cwd),
        "TMPDIR": "/tmp" if settings.work_dir is not None else source.get("TMPDIR", "/tmp"),
    }

    for key in ("LANG", "LC_ALL", "TERM", "USER", "LOGNAME", "SHELL"):
        value = source.get(key)
        if value:
            env[key] = value

    return env


async def _read_limited(
    stream: asyncio.StreamReader,
    limit: int,
    limit_event: asyncio.Event,
) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    size = 0
    truncated = False

    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break

        remaining = limit - size
        if remaining > 0:
            kept = chunk[:remaining]
            chunks.append(kept)
            size += len(kept)

        if len(chunk) > max(remaining, 0):
            truncated = True
            limit_event.set()

    return b"".join(chunks), truncated


async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


def _append_message(stderr: str, message: str) -> str:
    if not stderr:
        return message
    if stderr.endswith("\n"):
        return stderr + message
    return stderr + "\n" + message
