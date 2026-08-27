from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
import signal
import time
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .policy import PolicyError, resolve_directory, validate_command, validate_remote_command

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


@dataclass(frozen=True, slots=True)
class SSHSudoCompatRequest:
    host: str
    command: tuple[str, ...]
    port: int | None
    tty: bool


class ShellExecutor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sudo_path = shutil.which("sudo")
        self.ssh_path = shutil.which("ssh")
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
        compat = _parse_ssh_sudo_compat(command)
        if compat is not None:
            if directory is not None:
                raise PolicyError(
                    "directory is not supported for SSH sudo compatibility mode; "
                    "use ssh_execute for remote commands"
                )
            return await self.execute_ssh(
                host=compat.host,
                command=compat.command,
                sudo=True,
                stdin=stdin,
                port=compat.port,
                tty=compat.tty,
                timeout=timeout,
            )

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

        return await self._run_process(
            process_argv,
            process_stdin,
            cwd=cwd,
            child_env=child_env,
            timeout=timeout,
            started=started,
        )

    async def execute_ssh(
        self,
        *,
        host: str,
        command: Sequence[str],
        sudo: bool = False,
        stdin: str | None = None,
        port: int | None = None,
        tty: bool = False,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> ExecutionResult:
        """Execute one remote argv command through the local OpenSSH client."""
        if self.settings.work_dir is not None:
            raise PolicyError(
                "SSH execution is disabled while WORK_DIR is configured because local filesystem "
                "confinement cannot constrain changes made on a remote host"
            )
        if self.ssh_path is None:
            raise PolicyError("ssh was requested but the ssh executable is not available")

        started = time.monotonic()
        timeout = _normalize_timeout(timeout)
        host = _validate_ssh_host(host)
        port = _normalize_ssh_port(port)
        target_argv = validate_remote_command(command, self.settings)
        cwd = Path.cwd().resolve()
        child_env = _build_child_env(self.settings, cwd)

        process_argv, process_stdin = self._build_ssh_argv_and_stdin(
            host=host,
            target_argv=target_argv,
            sudo=sudo,
            stdin=stdin,
            port=port,
            tty=tty,
        )

        logger.info(
            "Executing SSH host=%s command=%s elevated=%s tty=%s",
            host,
            os.path.basename(target_argv[0]),
            sudo,
            tty,
        )

        return await self._run_process(
            process_argv,
            process_stdin,
            cwd=cwd,
            child_env=child_env,
            timeout=timeout,
            started=started,
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

    def _build_ssh_argv_and_stdin(
        self,
        *,
        host: str,
        target_argv: tuple[str, ...],
        sudo: bool,
        stdin: str | None,
        port: int | None,
        tty: bool,
    ) -> tuple[list[str], bytes]:
        assert self.ssh_path is not None

        payload = (stdin or "").encode()
        remote_argv = list(target_argv)

        if sudo:
            remote_password = self.settings.remote_sudo_password
            if remote_password is not None:
                remote_argv = ["sudo", "-S", "-p", "", "--", *target_argv]
                payload = (remote_password + "\n").encode() + payload
            else:
                remote_argv = ["sudo", "-n", "--", *target_argv]

        argv = [self.ssh_path, "-tt" if tty else "-T"]
        if port is not None:
            argv.extend(["-p", str(port)])
        argv.extend([host, shlex.join(remote_argv)])
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

    async def _run_process(
        self,
        process_argv: Sequence[str],
        process_stdin: bytes,
        *,
        cwd: Path,
        child_env: dict[str, str],
        timeout: float,
        started: float,
    ) -> ExecutionResult:
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
            with suppress(BrokenPipeError, ConnectionResetError):
                if process_stdin:
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

        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stdout_text = _redact_configured_secrets(stdout_text, self.settings)
        stderr_text = _redact_configured_secrets(stderr_text, self.settings)

        return ExecutionResult(
            stdout=stdout_text,
            stderr=stderr_text,
            status=status,
            execution_time=time.monotonic() - started,
            timed_out=timed_out,
            output_limited=output_limited,
        )


def _parse_ssh_sudo_compat(command: Sequence[str]) -> SSHSudoCompatRequest | None:
    """Recognize the common ``ssh [safe options] host sudo command`` argv form.

    This compatibility path intentionally supports only a small, unambiguous subset of SSH
    options. Complex SSH invocations should use ssh_execute so the remote command stays explicit.
    """
    if not command or not all(isinstance(value, str) for value in command):
        return None

    argv = tuple(command)
    if os.path.basename(argv[0]) != "ssh":
        return None

    index = 1
    port: int | None = None
    tty = False

    while index < len(argv):
        value = argv[index]
        if value == "-p":
            if index + 1 >= len(argv):
                return None
            try:
                port = _normalize_ssh_port(int(argv[index + 1]))
            except (PolicyError, ValueError):
                return None
            index += 2
            continue
        if value.startswith("-p") and len(value) > 2:
            try:
                port = _normalize_ssh_port(int(value[2:]))
            except (PolicyError, ValueError):
                return None
            index += 1
            continue
        if value in {"-t", "-tt"}:
            tty = True
            index += 1
            continue
        if value == "-T":
            tty = False
            index += 1
            continue
        if value.startswith("-"):
            return None
        break

    if index >= len(argv):
        return None

    host = argv[index]
    remote = argv[index + 1 :]
    if len(remote) < 2 or os.path.basename(remote[0]) != "sudo":
        return None

    remaining = remote[1:]
    if remaining and remaining[0] == "--":
        remaining = remaining[1:]
    if not remaining or remaining[0].startswith("-"):
        return None

    return SSHSudoCompatRequest(
        host=host,
        command=remaining,
        port=port,
        tty=tty,
    )


def _validate_ssh_host(host: str) -> str:
    if not isinstance(host, str) or not host:
        raise PolicyError("host must be a non-empty string")
    if "\x00" in host or any(char.isspace() for char in host):
        raise PolicyError("host cannot contain whitespace or NUL bytes")
    if host.startswith("-"):
        raise PolicyError("host cannot start with '-'")
    return host


def _normalize_ssh_port(port: int | None) -> int | None:
    if port is None:
        return None
    try:
        value = int(port)
    except (TypeError, ValueError) as exc:
        raise PolicyError("port must be an integer") from exc
    if not 1 <= value <= 65535:
        raise PolicyError("port must be between 1 and 65535")
    return value


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

    for key in ("LANG", "LC_ALL", "TERM", "USER", "LOGNAME", "SHELL", "SSH_AUTH_SOCK"):
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
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


def _redact_configured_secrets(text: str, settings: Settings) -> str:
    for secret in {settings.sudo_password, settings.ssh_sudo_password}:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def _append_message(stderr: str, message: str) -> str:
    if not stderr:
        return message
    if stderr.endswith("\n"):
        return stderr + message
    return stderr + "\n" + message
