from pathlib import Path

import pytest

from mcp_shell_sudo.config import ConfigurationError, Settings
from mcp_shell_sudo.policy import (
    PolicyError,
    resolve_directory,
    validate_command,
    validate_remote_command,
)


def settings(**kwargs):
    return Settings(
        sudo_password=kwargs.get("sudo_password"),
        work_dir=kwargs.get("work_dir"),
        allow_commands=kwargs.get("allow_commands"),
        ssh_sudo_password=kwargs.get("ssh_sudo_password"),
    )


def test_empty_allow_commands_means_allow_all():
    cfg = Settings.from_env({"ALLOW_COMMANDS": ""})
    assert cfg.allow_commands is None


def test_allow_commands_are_trimmed():
    cfg = Settings.from_env({"ALLOW_COMMANDS": "ls, cat ,pwd"})
    assert cfg.allow_commands == frozenset({"ls", "cat", "pwd"})


def test_allow_commands_reject_paths():
    with pytest.raises(ConfigurationError):
        Settings.from_env({"ALLOW_COMMANDS": "/bin/ls"})


def test_remote_sudo_password_falls_back_to_password_sudo():
    cfg = Settings.from_env({"PASSWORD_SUDO": "local-secret"})
    assert cfg.remote_sudo_password == "local-secret"
    assert cfg.remote_sudo_password_configured is True


def test_password_sudo_ssh_overrides_local_password():
    cfg = Settings.from_env(
        {
            "PASSWORD_SUDO": "local-secret",
            "PASSWORD_SUDO_SSH": "remote-secret",
        }
    )
    assert cfg.remote_sudo_password == "remote-secret"


def test_allowlist_rejects_unlisted_command():
    cfg = settings(allow_commands=frozenset({"ls"}))
    with pytest.raises(PolicyError):
        validate_command(["cat", "/etc/passwd"], cfg)


def test_sudo_validates_target_command():
    cfg = settings(allow_commands=frozenset({"id"}))
    request = validate_command(["sudo", "id"], cfg)
    assert request.elevated is True
    assert request.target_argv == ("id",)


def test_sudo_options_are_rejected():
    cfg = settings()
    with pytest.raises(PolicyError):
        validate_command(["sudo", "-u", "postgres", "id"], cfg)


def test_sudo_is_rejected_when_workdir_is_set(tmp_path: Path):
    cfg = settings(work_dir=tmp_path)
    with pytest.raises(PolicyError):
        validate_command(["sudo", "id"], cfg)


def test_direct_ssh_is_rejected_with_allowlist(ssh_test_host: str):
    cfg = settings(allow_commands=frozenset({"ssh"}))
    with pytest.raises(PolicyError, match="Direct ssh through shell_execute"):
        validate_command(["ssh", ssh_test_host, "id"], cfg)


def test_ssh_execute_command_must_exclude_sudo():
    cfg = settings()
    with pytest.raises(PolicyError, match="must exclude sudo"):
        validate_remote_command(["sudo", "id"], cfg)


def test_remote_allowlist_requires_ssh_transport():
    cfg = settings(allow_commands=frozenset({"id"}))
    with pytest.raises(PolicyError, match="ALLOW_COMMANDS: ssh"):
        validate_remote_command(["id"], cfg)


def test_remote_allowlist_requires_target_command():
    cfg = settings(allow_commands=frozenset({"ssh", "id"}))
    with pytest.raises(PolicyError, match="ALLOW_COMMANDS: whoami"):
        validate_remote_command(["whoami"], cfg)


def test_remote_allowlist_accepts_ssh_and_target():
    cfg = settings(allow_commands=frozenset({"ssh", "id"}))
    assert validate_remote_command(["id"], cfg) == ("id",)


def test_directory_must_stay_inside_workdir(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    cfg = settings(work_dir=root)

    assert resolve_directory(None, cfg) == root.resolve()
    with pytest.raises(PolicyError):
        resolve_directory("..", cfg)
