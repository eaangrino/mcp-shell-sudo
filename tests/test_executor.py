import asyncio
from pathlib import Path


from mcp_shell_sudo.config import Settings
from mcp_shell_sudo.executor import ShellExecutor


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
