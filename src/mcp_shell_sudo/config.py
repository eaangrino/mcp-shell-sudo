from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    """Raised when server environment configuration is invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    sudo_password: str | None
    work_dir: Path | None
    allow_commands: frozenset[str] | None
    ssh_sudo_password: str | None = None

    @property
    def sudo_password_configured(self) -> bool:
        return self.sudo_password is not None

    @property
    def remote_sudo_password(self) -> str | None:
        """Return the SSH sudo password, falling back to the local sudo password."""
        return self.ssh_sudo_password or self.sudo_password

    @property
    def remote_sudo_password_configured(self) -> bool:
        return self.remote_sudo_password is not None

    @property
    def command_allowlist_enabled(self) -> bool:
        return self.allow_commands is not None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env

        password = source.get("PASSWORD_SUDO", "")
        sudo_password = password if password else None

        ssh_password = source.get("PASSWORD_SUDO_SSH", "")
        ssh_sudo_password = ssh_password if ssh_password else None

        raw_work_dir = source.get("WORK_DIR", "").strip()
        work_dir: Path | None = None
        if raw_work_dir:
            candidate = Path(raw_work_dir).expanduser().resolve(strict=False)
            if not candidate.exists():
                raise ConfigurationError(f"WORK_DIR does not exist: {candidate}")
            if not candidate.is_dir():
                raise ConfigurationError(f"WORK_DIR is not a directory: {candidate}")
            work_dir = candidate

        raw_allow_commands = source.get("ALLOW_COMMANDS", "")
        allow_commands = _parse_allow_commands(raw_allow_commands)

        return cls(
            sudo_password=sudo_password,
            work_dir=work_dir,
            allow_commands=allow_commands,
            ssh_sudo_password=ssh_sudo_password,
        )


def _parse_allow_commands(raw: str) -> frozenset[str] | None:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if not values:
        return None

    invalid = [
        item
        for item in values
        if "/" in item or "\\" in item or any(c.isspace() for c in item)
    ]
    if invalid:
        raise ConfigurationError(
            "ALLOW_COMMANDS accepts executable names only, separated by commas. "
            f"Invalid entries: {', '.join(invalid)}"
        )

    return frozenset(values)
