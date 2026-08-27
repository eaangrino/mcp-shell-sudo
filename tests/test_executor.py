import asyncio
from pathlib import Path

import pytest

from mcp_shell_sudo.config import Settings
from mcp_shell_sudo.executor import ShellExecutor, _parse_ssh_sudo_compat
from mcp_shell_sudo.policy import PolicyError


def test_executes_argv_without_shell_interpretation():
    executor = ShellExecutor(Settings(None, None, frozenset({"printf"})))
    result = asyncio.run(executor.execute(["printf", "%s", "a;b"]))
    assert result.status == 0
    assert result.stdout == "a;b"


def test_sudo_password_is_passed_via_stdin_not_argv(monkeypatch):
    executor = ShellExecutor(Settings("secret", None, None))
    monkeypatch.setattr(executor, "sudo_path", "/usr/bin/sudo")

    argv, stdin = executor._build_process_argv_and_stdin(
        ("id",),
        elevated=True,
        stdin="payload",
        cwd=Path("/tmp"),
        child_env={"PATH": "/usr/bin:/bin"},
    )

    assert argv == ["/usr/bin/sudo", "-S", "-p", "", "--", "id"]
    assert "secret" not in " ".join(argv)
    assert stdin == b"secret\npayload"


def test_no_password_uses_noninteractive_sudo(monkeypatch):
    executor = ShellExecutor(Settings(None, None, None))
    monkeypatch.setattr(executor, "sudo_path", "/usr/bin/sudo")

    argv, stdin = executor._build_process_argv_and_stdin(
        ("id",),
        elevated=True,
        stdin=None,
        cwd=Path("/tmp"),
        child_env={"PATH": "/usr/bin:/bin"},
    )

    assert argv == ["/usr/bin/sudo", "-n", "--", "id"]
    assert stdin == b""


def test_ssh_sudo_password_is_passed_via_stdin_not_argv(monkeypatch, ssh_test_host: str):
    executor = ShellExecutor(
        Settings("local-secret", None, None, "remote-secret"))
    monkeypatch.setattr(executor, "ssh_path", "/usr/bin/ssh")

    argv, stdin = executor._build_ssh_argv_and_stdin(
        host=ssh_test_host,
        target_argv=("systemctl", "status", "docker"),
        sudo=True,
        stdin="payload",
        port=None,
        tty=False,
    )

    assert argv == [
        "/usr/bin/ssh",
        "-T",
        ssh_test_host,
        "sudo -S -p '' -- systemctl status docker",
    ]
    assert "remote-secret" not in " ".join(argv)
    assert "local-secret" not in " ".join(argv)
    assert stdin == b"remote-secret\npayload"


def test_ssh_sudo_falls_back_to_local_password(monkeypatch, ssh_test_host: str):
    executor = ShellExecutor(Settings("local-secret", None, None))
    monkeypatch.setattr(executor, "ssh_path", "/usr/bin/ssh")

    argv, stdin = executor._build_ssh_argv_and_stdin(
        host=ssh_test_host,
        target_argv=("id",),
        sudo=True,
        stdin=None,
        port=2222,
        tty=True,
    )

    assert argv == [
        "/usr/bin/ssh",
        "-tt",
        "-p",
        "2222",
        ssh_test_host,
        "sudo -S -p '' -- id",
    ]
    assert stdin == b"local-secret\n"


def test_ssh_sudo_without_password_uses_noninteractive_sudo(monkeypatch, ssh_test_host: str):
    executor = ShellExecutor(Settings(None, None, None))
    monkeypatch.setattr(executor, "ssh_path", "/usr/bin/ssh")

    argv, stdin = executor._build_ssh_argv_and_stdin(
        host=ssh_test_host,
        target_argv=("id",),
        sudo=True,
        stdin=None,
        port=None,
        tty=False,
    )

    assert argv == ["/usr/bin/ssh", "-T", ssh_test_host, "sudo -n -- id"]
    assert stdin == b""


def test_ssh_remote_argv_is_shell_quoted(monkeypatch, ssh_test_host: str):
    executor = ShellExecutor(Settings(None, None, None))
    monkeypatch.setattr(executor, "ssh_path", "/usr/bin/ssh")

    argv, _ = executor._build_ssh_argv_and_stdin(
        host=ssh_test_host,
        target_argv=("printf", "%s", "a;b $(id)"),
        sudo=False,
        stdin=None,
        port=None,
        tty=False,
    )

    assert argv[-1] == "printf %s 'a;b $(id)'"


def test_simple_ssh_sudo_is_detected_for_compatibility(ssh_test_host: str):
    request = _parse_ssh_sudo_compat(
        ["ssh", "-p", "2222", ssh_test_host, "sudo", "id"])
    assert request is not None
    assert request.host == ssh_test_host
    assert request.command == ("id",)
    assert request.port == 2222
    assert request.tty is False


def test_complex_ssh_options_are_not_rewritten_by_compatibility(ssh_test_host: str):
    request = _parse_ssh_sudo_compat(
        ["ssh", "-o", "ProxyJump=jump", ssh_test_host, "sudo", "id"]
    )
    assert request is None


def test_ssh_execution_rejects_work_dir(
    tmp_path: Path, monkeypatch, ssh_test_host: str
):
    monkeypatch.setattr("mcp_shell_sudo.executor.shutil.which",
                        lambda name: f"/usr/bin/{name}")
    executor = ShellExecutor(Settings(None, tmp_path, None))
    with pytest.raises(PolicyError, match="SSH execution is disabled while WORK_DIR"):
        asyncio.run(executor.execute_ssh(host=ssh_test_host, command=["id"]))


def test_command_output_redacts_configured_password():
    executor = ShellExecutor(
        Settings("secret-value", None, frozenset({"printf"})))
    result = asyncio.run(executor.execute(["printf", "%s", "secret-value"]))
    assert result.status == 0
    assert result.stdout == "[REDACTED]"
