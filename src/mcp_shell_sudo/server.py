from __future__ import annotations

import logging
import sys

from mcp.server import MCPServer

from mcp_shell_sudo.config import ConfigurationError, Settings
from mcp_shell_sudo.executor import DEFAULT_TIMEOUT_SECONDS, ShellExecutor
from mcp_shell_sudo.policy import PolicyError

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

settings = Settings.from_env()
executor = ShellExecutor(settings)

mcp = MCPServer(
    "mcp-shell-sudo",
    instructions=(
        "Execute local Linux commands using argv arrays with shell_execute. Prefix a local command "
        "with 'sudo' to request local elevation. Use ssh_execute for remote commands over OpenSSH; "
        "set sudo=true when the remote command needs sudo. PASSWORD_SUDO is supplied through "
        "stdin for local sudo and is also the default remote sudo password. PASSWORD_SUDO_SSH may "
        "override the remote sudo password. Direct shell_execute calls in the common form "
        "['ssh', 'host', 'sudo', 'command', ...] are recognized for compatibility. When "
        "WORK_DIR is "
        "configured, local execution is filesystem-confined and both sudo and SSH execution are "
        "intentionally disabled where that confinement cannot be guaranteed."
    ),
)


@mcp.tool()
async def shell_execute(
    command: list[str],
    stdin: str | None = None,
    directory: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Execute one local Linux process using an explicit argv array.

    Use this tool when a local command must actually run. The command executes with the server
    process's permissions and may cause filesystem, process, service, network, or other system
    side effects. Commands prefixed with "sudo" may run with elevated privileges when sudo is
    enabled. Shell operators such as pipes, redirects, &&, ||, and variable expansion are not
    interpreted.

    For remote execution prefer ssh_execute. For compatibility, the simple argv form
    ["ssh", "host", "sudo", "command", ...] is detected and the configured remote sudo password
    is supplied through stdin. Complex SSH option sets should use ssh_execute instead.

    Use shell_config first when execution restrictions such as sudo, SSH, WORK_DIR confinement, or
    command allowlisting are relevant. Policy or configuration rejections return status 126;
    timeout and output-limit conditions are reported in the result.

    Args:
        command: Executable and arguments, for example ["ls", "-la"],
            ["sudo", "systemctl", "status", "ssh"], or the compatibility form
            ["ssh", "server-alias", "sudo", "id"]. Shell syntax is not interpreted locally.
        stdin: Optional text sent to the command's standard input.
        directory: Optional working directory. With WORK_DIR configured, this path must resolve
            inside WORK_DIR. Without WORK_DIR, any existing directory may be used.
        timeout: Execution timeout in seconds. Values above 600 seconds are capped at 600.

    Returns:
        stdout, stderr, exit status, execution time, timeout flag, and output-limit flag.
    """
    try:
        result = await executor.execute(
            command,
            stdin=stdin,
            directory=directory,
            timeout=timeout,
        )
        return result.as_dict()
    except (PolicyError, ConfigurationError, OSError, RuntimeError) as exc:
        logger.warning("Execution rejected: %s", exc)
        return _error_result(exc)


@mcp.tool()
async def ssh_execute(
    host: str,
    command: list[str],
    sudo: bool = False,
    stdin: str | None = None,
    port: int | None = None,
    tty: bool = False,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Execute one command on a remote host through the local OpenSSH client.

    Prefer this tool over shell_execute when the target command runs on another machine. SSH login
    authentication itself must already work non-interactively through the user's SSH config, key,
    or agent. This tool does not inject an SSH login password.

    When sudo=true, the remote command is sent as `sudo -S -p '' -- ...`. PASSWORD_SUDO_SSH is used
    when configured; otherwise PASSWORD_SUDO is reused. The password is sent only through SSH
    stdin and is never placed in process argv. If neither password is configured, remote sudo uses
    `sudo -n` so it fails instead of waiting for an interactive password prompt.

    Remote argv elements are quoted with shlex semantics before OpenSSH passes the command to the
    remote login shell. Set tty=true only when the remote sudo policy requires a TTY. WORK_DIR and
    SSH execution are intentionally incompatible because local bubblewrap confinement cannot
    restrict filesystem changes made on a remote host.

    When ALLOW_COMMANDS is configured, both `ssh` and the remote target executable must be present
    in the allowlist.

    Args:
        host: SSH destination or alias, for example "julanito" or "user@192.168.1.10".
        command: Remote executable and arguments, excluding sudo. For example ["id"] or
            ["systemctl", "restart", "docker"].
        sudo: Request sudo on the remote host.
        stdin: Optional text delivered after the sudo password, if sudo requires one.
        port: Optional SSH TCP port from 1 to 65535.
        tty: Force a remote pseudo-terminal with ssh -tt. Defaults to false.
        timeout: Execution timeout in seconds. Values above 600 seconds are capped at 600.

    Returns:
        stdout, stderr, exit status, execution time, timeout flag, and output-limit flag.
    """
    try:
        result = await executor.execute_ssh(
            host=host,
            command=command,
            sudo=sudo,
            stdin=stdin,
            port=port,
            tty=tty,
            timeout=timeout,
        )
        return result.as_dict()
    except (PolicyError, ConfigurationError, OSError, RuntimeError) as exc:
        logger.warning("SSH execution rejected: %s", exc)
        return _error_result(exc)


@mcp.tool()
def shell_config() -> dict[str, object]:
    """Inspect the effective non-secret shell and SSH execution policy.

    Use this tool before shell_execute or ssh_execute when you need to know whether sudo password
    injection is configured, SSH is available, WORK_DIR confinement is active, or command
    allowlisting applies. This operation is read-only and never exposes either sudo password.

    Returns:
        Effective policy flags and values controlling sudo, SSH, workspace confinement, and command
        allowlisting.
    """
    return {
        "sudo_password_configured": settings.sudo_password_configured,
        "remote_sudo_password_configured": settings.remote_sudo_password_configured,
        "ssh_available": executor.ssh_path is not None,
        "work_dir": str(settings.work_dir) if settings.work_dir else None,
        "strict_work_dir": settings.work_dir is not None,
        "allow_commands": sorted(settings.allow_commands) if settings.allow_commands else None,
        "allow_all_commands": settings.allow_commands is None,
        "sudo_allowed_with_work_dir": False,
        "ssh_allowed_with_work_dir": False,
    }


def _error_result(exc: Exception) -> dict[str, object]:
    return {
        "stdout": "",
        "stderr": str(exc),
        "status": 126,
        "execution_time": 0.0,
        "timed_out": False,
        "output_limited": False,
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
