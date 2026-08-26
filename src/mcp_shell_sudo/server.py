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
        "Execute local Linux commands using argv arrays. Prefix a command with 'sudo' to request "
        "elevation. If PASSWORD_SUDO is configured, the server supplies it to sudo through stdin. "
        "When WORK_DIR is configured, execution is filesystem-confined to that directory and sudo "
        "is intentionally disabled."
    ),
)


@mcp.tool()
async def shell_execute(
    command: list[str],
    stdin: str | None = None,
    directory: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Execute one command as an argv array.

    Args:
        command: Executable and arguments, for example ["ls", "-la"] or
            ["sudo", "systemctl", "status", "ssh"]. Shell syntax is not interpreted.
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
        return {
            "stdout": "",
            "stderr": str(exc),
            "status": 126,
            "execution_time": 0.0,
            "timed_out": False,
            "output_limited": False,
        }


@mcp.tool()
def shell_config() -> dict[str, object]:
    """Return the effective non-secret shell policy configuration."""
    return {
        "sudo_password_configured": settings.sudo_password_configured,
        "work_dir": str(settings.work_dir) if settings.work_dir else None,
        "strict_work_dir": settings.work_dir is not None,
        "allow_commands": sorted(settings.allow_commands) if settings.allow_commands else None,
        "allow_all_commands": settings.allow_commands is None,
        "sudo_allowed_with_work_dir": False,
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
