# mcp-shell-sudo

MCP server for Linux written in Python that executes local commands using argv, supports non-interactive `sudo` through `PASSWORD_SUDO`, provides an optional command allowlist through `ALLOW_COMMANDS`, and can confine filesystem writes to `WORK_DIR` using Bubblewrap.

## Requirements

- Linux.
- Python 3.11+.
- `uv` recommended.
- MCP Python SDK 2.0.0.
- `sudo` if elevated commands will be executed.
- `bubblewrap` if `WORK_DIR` is configured.

On Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y bubblewrap
```

## Environment Variables

### `PASSWORD_SUDO`

Password of the user running the MCP server.

- Empty or unset: the server never invents or interactively requests a password. Commands prefixed with `sudo` are executed using `sudo -n` and will only work if a valid cached credential already exists or a `NOPASSWD` rule is configured.
- Set: `sudo` is executed with `-S`, and the password is provided through **stdin**, never through argv or logs.

Example:

```json
"PASSWORD_SUDO": "my-password"
```

The supported form is:

```json
["sudo", "id"]
```

Custom `sudo` flags such as `sudo -u postgres ...` are not accepted. This prevents the policy layer from having to interpret the full `sudo` command grammar.

### `WORK_DIR`

Optional working directory.

- Empty or unset: the MCP server may work from any existing directory specified in the tool call.
- Set: the server enters strict mode. `directory` must resolve inside `WORK_DIR`, and every process runs inside a Bubblewrap mount namespace where `/` is read-only, `WORK_DIR` is mounted read-write, and `/tmp` and `/run` are temporary.

Using `cwd` alone would **not** be sufficient to guarantee this isolation. A process could still write to `/etc`, `/home/...`, or follow symlinks outside the project. For this reason, the server fails at startup if `WORK_DIR` is configured but `bwrap` is unavailable.

In this mode, `sudo` is rejected because host-level privilege escalation would be incompatible with the guarantee that filesystem writes remain confined to the workspace.

### `ALLOW_COMMANDS`

Comma-separated list of executable names.

```json
"ALLOW_COMMANDS": "git,ls,cat,grep,python,node,pnpm"
```

- Empty or unset: all executables are allowed.
- Set: only exact executable names from the list are allowed, and they must be invoked by name rather than through paths such as `/usr/bin/git`.
- If the command starts with `sudo`, the actual executable following `sudo` is validated.

The allowlist applies to **executables**, not arguments. Allowing `bash`, `python`, `node`, `env`, or another tool capable of executing processes significantly expands what the MCP client can do.

## Installation

```bash
uv sync
```

For development:

```bash
uv sync --extra dev
```

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

## `shell_config` Tool

Returns the effective non-secret configuration.

It never returns `PASSWORD_SUDO`; it only indicates whether a sudo password has been configured.

## Implemented Security Measures

- Uses `create_subprocess_exec`; it does not use `shell=True`.
- `command` is passed as an explicit argv array.
- The sudo password never appears in argv, responses, logs, or the child process environment.
- If `PASSWORD_SUDO` is not configured, `sudo -n` prevents the process from hanging while waiting for an interactive password prompt.
- `ALLOW_COMMANDS` validates the actual executable following a simple `sudo` invocation.
- Uses a fixed and reduced `PATH` to avoid resolving executables from `.` or arbitrary inherited paths.
- Uses a reduced child environment instead of inheriting the entire MCP server environment.
- Per-call timeout: 60 seconds by default, with a maximum of 600 seconds.
- Output limit: 2 MiB per stream. If the limit is exceeded, the entire process group is terminated.
- Processes run in a new session, and the entire process group is terminated on timeout.
- `WORK_DIR` uses Bubblewrap and fails closed if isolation cannot be provided.

## Important Limitations

1. `ALLOW_COMMANDS=""` means arbitrary command execution with the permissions of the user running the MCP server. If `PASSWORD_SUDO` is also configured, the MCP client can request actions as root. Use this configuration only with a client and model you control.

2. An executable allowlist does not semantically validate command arguments. `python`, `node`, `bash`, `sh`, `perl`, and similar tools are effectively equivalent to allowing arbitrary code execution.

3. `WORK_DIR` confines the filesystem locations that are writable, but it is not intended to behave as a VM or network sandbox. A command with network access may still modify remote resources for which it has valid credentials.

4. `PASSWORD_SUDO` stored in the MCP client configuration remains a secret stored in that file. Restrict the file permissions with `chmod 600` and never commit it to version control.

## Tests

```bash
uv run --extra dev pytest
uv run --extra dev ruff check .
```
