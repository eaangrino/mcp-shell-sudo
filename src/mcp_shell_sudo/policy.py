from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .config import Settings


class PolicyError(ValueError):
    """Raised when a requested execution violates server policy."""


@dataclass(frozen=True, slots=True)
class CommandRequest:
    argv: tuple[str, ...]
    target_argv: tuple[str, ...]
    executable_name: str
    elevated: bool


def validate_command(command: Sequence[str], settings: Settings) -> CommandRequest:
    if not command:
        raise PolicyError("command must contain at least one argv element")

    argv = tuple(_validate_argv_element(value) for value in command)
    elevated = os.path.basename(argv[0]) == "sudo"

    if elevated:
        if settings.work_dir is not None:
            raise PolicyError(
                "sudo is disabled while WORK_DIR is configured because strict filesystem "
                "confinement and host-level privilege escalation are incompatible"
            )
        target_argv = _strip_plain_sudo(argv)
    else:
        target_argv = argv

    executable = target_argv[0]
    executable_name = os.path.basename(executable)
    _validate_allowlisted_executable(executable, executable_name, settings)
    if settings.allow_commands is not None and executable_name == "ssh":
        raise PolicyError(
            "Direct ssh through shell_execute is disabled while ALLOW_COMMANDS is enabled; "
            "use ssh_execute so the remote target command is validated too"
        )

    return CommandRequest(
        argv=argv,
        target_argv=target_argv,
        executable_name=executable_name,
        elevated=elevated,
    )


def validate_remote_command(command: Sequence[str], settings: Settings) -> tuple[str, ...]:
    """Validate an SSH remote command against the same command-name allowlist.

    ssh_execute treats SSH as a transport. When ALLOW_COMMANDS is enabled both the local
    ``ssh`` transport and the remote target executable must be explicitly allowlisted.
    """
    if not command:
        raise PolicyError("remote command must contain at least one argv element")

    argv = tuple(_validate_argv_element(value) for value in command)
    if os.path.basename(argv[0]) == "sudo":
        raise PolicyError("ssh_execute command must exclude sudo; set sudo=true instead")

    executable = argv[0]
    executable_name = os.path.basename(executable)

    if settings.allow_commands is not None:
        if "ssh" not in settings.allow_commands:
            raise PolicyError("Command not allowed by ALLOW_COMMANDS: ssh")
        _validate_allowlisted_executable(executable, executable_name, settings)

    return argv


def resolve_directory(requested: str | None, settings: Settings) -> Path:
    if settings.work_dir is None:
        base = Path.cwd().resolve()
        candidate = base if not requested else Path(requested).expanduser()
        if not candidate.is_absolute():
            candidate = base / candidate
        candidate = candidate.resolve(strict=False)
    else:
        root = settings.work_dir
        if not requested:
            candidate = root
        else:
            raw = Path(requested).expanduser()
            candidate = raw if raw.is_absolute() else root / raw
            candidate = candidate.resolve(strict=False)
            if not _is_within(candidate, root):
                raise PolicyError(f"directory is outside WORK_DIR: {candidate}")

    if not candidate.exists():
        raise PolicyError(f"directory does not exist: {candidate}")
    if not candidate.is_dir():
        raise PolicyError(f"directory is not a directory: {candidate}")
    return candidate


def _validate_argv_element(value: str) -> str:
    if not isinstance(value, str):
        raise PolicyError("every command element must be a string")
    if not value:
        raise PolicyError("command elements cannot be empty strings")
    if "\x00" in value:
        raise PolicyError("command elements cannot contain NUL bytes")
    return value


def _validate_allowlisted_executable(
    executable: str,
    executable_name: str,
    settings: Settings,
) -> None:
    if settings.allow_commands is None:
        return
    if "/" in executable or "\\" in executable:
        raise PolicyError(
            "When ALLOW_COMMANDS is enabled, executables must be invoked by name, not by path"
        )
    if executable_name not in settings.allow_commands:
        raise PolicyError(f"Command not allowed by ALLOW_COMMANDS: {executable_name}")


def _strip_plain_sudo(argv: tuple[str, ...]) -> tuple[str, ...]:
    remaining = argv[1:]
    if remaining and remaining[0] == "--":
        remaining = remaining[1:]
    if not remaining:
        raise PolicyError("sudo must be followed by a command")
    if remaining[0].startswith("-"):
        raise PolicyError(
            "sudo options are intentionally disabled; use ['sudo', 'command', ...] or "
            "['sudo', '--', 'command', ...]"
        )
    return remaining


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
