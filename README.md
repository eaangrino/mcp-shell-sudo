# mcp-shell-sudo

[English](README.md) | [Español](README.es.md)

> **Warning:** Use this MCP with caution. If sudo credentials are configured, it can execute privileged commands both on the host machine and on SSH-connected remote systems.

MCP server for Linux written in Python that executes local and remote SSH commands using explicit argv arrays, supports non-interactive local and remote `sudo`, provides an optional executable allowlist through `ALLOW_COMMANDS`, and can confine local filesystem writes to `WORK_DIR` using Bubblewrap.

## Requirements

- Linux.
- Python 3.11+.
- `uv` recommended.
- MCP Python SDK 2.0.0.
- `sudo` if elevated commands will be executed.
- OpenSSH client (`ssh`) if remote execution will be used.
- SSH login authentication configured through `~/.ssh/config`, a key, or an SSH agent for non-interactive remote access.
- `bubblewrap` if `WORK_DIR` is configured.

On Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y bubblewrap
```

## Environment Variables

### `PASSWORD_SUDO`

Password used for local `sudo`. It is also the fallback password for remote `sudo` when `PASSWORD_SUDO_SSH` is not configured.

- Empty or unset: local `sudo` uses `sudo -n`. Remote `sudo` also uses `sudo -n` unless `PASSWORD_SUDO_SSH` is configured.
- Set: local `sudo` uses `sudo -S`, and the password is provided through **stdin**, never through argv or logs. Remote `sudo` reuses this value only when `PASSWORD_SUDO_SSH` is absent.

Example:

```json
"PASSWORD_SUDO": "my-password"
```

The supported form is:

```json
["sudo", "id"]
```

Custom `sudo` flags such as `sudo -u postgres ...` are not accepted. This prevents the policy layer from having to interpret the full `sudo` command grammar.

### `PASSWORD_SUDO_SSH`

Optional password override used only for `sudo` on remote hosts executed through `ssh_execute`.

```json
"PASSWORD_SUDO_SSH": "remote-sudo-password"
```

Password selection for remote sudo is:

1. `PASSWORD_SUDO_SSH` when configured.
2. Otherwise `PASSWORD_SUDO`.
3. If neither is configured, remote sudo uses `sudo -n` and fails instead of waiting for an interactive password prompt.

This variable is **not** an SSH login password. `ssh_execute` does not inject SSH login credentials. SSH authentication must already work non-interactively through OpenSSH configuration, a key, or an SSH agent.

### `WORK_DIR`

Optional working directory.

- Empty or unset: the MCP server may work from any existing directory specified in the tool call.
- Set: the server enters strict mode. `directory` must resolve inside `WORK_DIR`, and every process runs inside a Bubblewrap mount namespace where `/` is read-only, `WORK_DIR` is mounted read-write, and `/tmp` and `/run` are temporary.

Using `cwd` alone would **not** be sufficient to guarantee this isolation. A process could still write to `/etc`, `/home/...`, or follow symlinks outside the project. For this reason, the server fails at startup if `WORK_DIR` is configured but `bwrap` is unavailable.

In this mode, local `sudo` and all SSH execution are rejected. Host-level privilege escalation would break the local write-confinement guarantee, and local Bubblewrap confinement cannot restrict changes made on a remote host.

### `ALLOW_COMMANDS`

Comma-separated list of executable names.

```json
"ALLOW_COMMANDS": "git,ls,cat,grep,python,node,pnpm"
```

- Empty or unset: all executables are allowed.
- Set: only exact executable names from the list are allowed, and they must be invoked by name rather than through paths such as `/usr/bin/git`.
- If a local command starts with `sudo`, the actual executable following `sudo` is validated.
- For `ssh_execute`, both `ssh` and the remote target executable must be present in the allowlist.
- Direct arbitrary `ssh` calls through `shell_execute` are rejected while an allowlist is active; use `ssh_execute` so the remote executable can also be validated. The simple compatibility form `["ssh", "host", "sudo", "command", ...]` is recognized and validated.

The allowlist applies to **executables**, not arguments. Allowing `bash`, `python`, `node`, `env`, or another tool capable of executing processes significantly expands what the MCP client can do.

## Installation

```bash
uv sync
```

For development:

```bash
uv sync --extra dev
```

To install the MCP executable as an editable user tool:

```bash
uv tool install --force --editable .
```

The executable is typically installed as `~/.local/bin/mcp-shell-sudo`.

## Manual Execution

```bash
uv run mcp-shell-sudo
```

The transport is `stdio`. Logging is never written to stdout in order to avoid corrupting JSON-RPC messages; logs are written to stderr instead.

## MCP Configuration

Example with unrestricted commands and sudo enabled:

```json
{
  "mcpServers": {
    "shell": {
      "command": "uv",
      "args": [
        "--directory",
        "/ABSOLUTE/PATH/mcp-shell-sudo",
        "run",
        "mcp-shell-sudo"
      ],
      "env": {
        "PASSWORD_SUDO": "YOUR_PASSWORD",
        "WORK_DIR": "",
        "ALLOW_COMMANDS": ""
      }
    }
  }
}
```

Example using the installed executable with separate local and remote sudo passwords:

```json
{
  "mcpServers": {
    "mcp-shell-sudo": {
      "command": "/home/user/.local/bin/mcp-shell-sudo",
      "env": {
        "PASSWORD_SUDO": "LOCAL_SUDO_PASSWORD",
        "PASSWORD_SUDO_SSH": "REMOTE_SUDO_PASSWORD"
      }
    }
  }
}
```

If local and remote sudo use the same password, omit `PASSWORD_SUDO_SSH`; `PASSWORD_SUDO` is reused automatically for remote sudo.

Example confined to a specific project:

```json
{
  "mcpServers": {
    "shell-project": {
      "command": "uv",
      "args": [
        "--directory",
        "/ABSOLUTE/PATH/mcp-shell-sudo",
        "run",
        "mcp-shell-sudo"
      ],
      "env": {
        "PASSWORD_SUDO": "",
        "WORK_DIR": "/home/user/projects/app",
        "ALLOW_COMMANDS": "git,ls,cat,grep,find,python,node,pnpm"
      }
    }
  }
}
```

## `shell_execute` Tool

Basic input:

```json
{
  "command": ["ls", "-la"]
}
```

With a working directory:

```json
{
  "command": ["git", "status"],
  "directory": "backend"
}
```

With stdin:

```json
{
  "command": ["cat"],
  "stdin": "hello\n"
}
```

With sudo:

```json
{
  "command": ["sudo", "id"]
}
```

Response:

```json
{
  "stdout": "uid=0(root) gid=0(root) groups=0(root)\n",
  "stderr": "",
  "status": 0,
  "execution_time": 0.031,
  "timed_out": false,
  "output_limited": false
}
```

## `ssh_execute` Tool

Use `ssh_execute` when the command must run on another machine. The `command` array must contain the remote executable and arguments **without** `sudo`; request elevation with `sudo: true`.

Basic remote execution:

```json
{
  "host": "server-alias",
  "command": ["id"]
}
```

Remote sudo:

```json
{
  "host": "server-alias",
  "command": ["systemctl", "status", "docker", "--no-pager"],
  "sudo": true
}
```

Optional SSH port and TTY:

```json
{
  "host": "user@example.com",
  "command": ["id"],
  "sudo": true,
  "port": 2222,
  "tty": true,
  "timeout": 120
}
```

- `host`: an OpenSSH destination or alias from `~/.ssh/config`.
- `command`: remote argv excluding `sudo`.
- `sudo`: when true, runs the remote command as `sudo -S -p '' -- ...` when a remote sudo password is configured, otherwise as `sudo -n -- ...`.
- `stdin`: optional data delivered to the remote command after the sudo password.
- `port`: optional TCP port from 1 to 65535.
- `tty`: uses `ssh -tt`; enable it only if the remote sudo policy requires a TTY.
- `timeout`: 60 seconds by default, capped at 600 seconds.

SSH login authentication is handled by the local OpenSSH client. The server does not inject an SSH login password.

For compatibility, `shell_execute` also recognizes the common simple form:

```json
{
  "command": ["ssh", "server-alias", "sudo", "id"]
}
```

For explicit remote execution or more complex SSH requirements, prefer `ssh_execute`.

## `shell_config` Tool

Returns the effective non-secret configuration.

It never returns `PASSWORD_SUDO` or `PASSWORD_SUDO_SSH`. It reports only non-secret state such as whether local/remote sudo password injection is configured, whether `ssh` is available, whether `WORK_DIR` confinement is active, and the effective allowlist.

## Implemented Security Measures

- Uses `create_subprocess_exec`; it does not use `shell=True`.
- `command` is passed as an explicit argv array.
- Local and remote sudo passwords never appear in process argv or the child process environment; configured secrets are also redacted from stdout/stderr before results are returned.
- If no applicable sudo password is configured, `sudo -n` prevents the process from hanging while waiting for an interactive password prompt.
- SSH login authentication remains delegated to OpenSSH configuration, keys, and agents; the MCP server does not handle SSH login passwords.
- `SSH_AUTH_SOCK` is forwarded when present so OpenSSH can use the current SSH agent.
- Remote argv elements are quoted with `shlex` semantics before being passed to OpenSSH.
- `ALLOW_COMMANDS` validates the local executable after a simple `sudo` invocation and, for `ssh_execute`, validates both `ssh` and the remote target executable.
- Uses a fixed and reduced `PATH` to avoid resolving executables from `.` or arbitrary inherited paths.
- Uses a reduced child environment instead of inheriting the entire MCP server environment.
- Per-call timeout: 60 seconds by default, with a maximum of 600 seconds.
- Output limit: 2 MiB per stream. If the limit is exceeded, the entire process group is terminated.
- Processes run in a new session, and the entire process group is terminated on timeout.
- `WORK_DIR` uses Bubblewrap and fails closed if isolation cannot be provided. SSH execution and sudo are disabled while `WORK_DIR` is active where confinement cannot be guaranteed.

## Important Limitations

1. `ALLOW_COMMANDS=""` means arbitrary command execution with the permissions of the user running the MCP server. If a sudo password is also configured, the MCP client can request root actions locally and, through `ssh_execute`, on SSH hosts for which authentication is already available. Use this configuration only with a client and model you control.

2. An executable allowlist does not semantically validate command arguments. `python`, `node`, `bash`, `sh`, `perl`, and similar tools are effectively equivalent to allowing arbitrary code execution.

3. `WORK_DIR` is a local filesystem confinement mechanism, not a VM or network sandbox. Because it cannot constrain remote filesystem changes, SSH execution is intentionally disabled whenever `WORK_DIR` is configured.

4. Remote execution can modify another machine with the permissions of the SSH account or root when `sudo: true` succeeds. OpenSSH host verification and trust remain governed by the user's SSH configuration and `known_hosts`.

5. `PASSWORD_SUDO` and `PASSWORD_SUDO_SSH` stored in the MCP client configuration remain secrets stored in that file. Restrict the file permissions with `chmod 600` and never commit it to version control.

## Tests

```bash
uv run --extra dev pytest
uv run --extra dev ruff check .
```
