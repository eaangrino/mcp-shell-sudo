# mcp-shell-sudo

[English](README.md) | [Español](README.es.md)

> **Advertencia:** Usa este MCP con precaución. Si se configuran credenciales de sudo, puede ejecutar comandos con privilegios tanto en la máquina host como en sistemas remotos conectados por SSH.

MCP server para Linux escrito en Python que ejecuta comandos locales y remotos por SSH mediante arrays argv explícitos, soporta `sudo` local y remoto de forma no interactiva, permite una allowlist opcional de ejecutables con `ALLOW_COMMANDS` y puede confinar las escrituras locales del filesystem a `WORK_DIR` usando Bubblewrap.

## Requisitos

- Linux.
- Python 3.11+.
- `uv` recomendado.
- MCP Python SDK 2.0.0.
- `sudo` si se van a ejecutar comandos elevados.
- Cliente OpenSSH (`ssh`) si se va a usar ejecución remota.
- Autenticación SSH configurada mediante `~/.ssh/config`, una llave o un SSH agent para acceso remoto no interactivo.
- `bubblewrap` si se configura `WORK_DIR`.

En Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y bubblewrap
```

## Variables de entorno

### `PASSWORD_SUDO`

Contraseña usada para `sudo` local. También funciona como contraseña fallback para `sudo` remoto cuando `PASSWORD_SUDO_SSH` no está configurada.

- Vacía o ausente: `sudo` local usa `sudo -n`. `sudo` remoto también usa `sudo -n` salvo que exista `PASSWORD_SUDO_SSH`.
- Con valor: `sudo` local usa `sudo -S` y la contraseña se entrega por **stdin**, nunca por argv ni logs. `sudo` remoto reutiliza este valor solo cuando `PASSWORD_SUDO_SSH` está ausente.

Ejemplo:

```json
"PASSWORD_SUDO": "mi-contraseña"
```

La forma soportada es:

```json
["sudo", "id"]
```

No se aceptan flags propios de `sudo` como `sudo -u postgres ...`; esto evita que la capa de policy tenga que interpretar el grammar completo de `sudo`.

### `PASSWORD_SUDO_SSH`

Override opcional de contraseña usado únicamente para `sudo` en hosts remotos ejecutados mediante `ssh_execute`.

```json
"PASSWORD_SUDO_SSH": "contraseña-sudo-remota"
```

La selección de contraseña para sudo remoto es:

1. `PASSWORD_SUDO_SSH` si está configurada.
2. En caso contrario, `PASSWORD_SUDO`.
3. Si ninguna está configurada, sudo remoto usa `sudo -n` y falla en lugar de quedarse esperando un prompt interactivo.

Esta variable **no** es una contraseña de login SSH. `ssh_execute` no inyecta credenciales de inicio de sesión SSH. La autenticación SSH debe funcionar previamente de forma no interactiva mediante configuración de OpenSSH, una llave o un SSH agent.

### `WORK_DIR`

Directorio de trabajo opcional.

- Vacío o ausente: el MCP puede trabajar desde cualquier directorio existente que se indique en la llamada.
- Con valor: el servidor entra en modo estricto. `directory` debe resolver dentro de `WORK_DIR` y cada proceso se ejecuta dentro de un mount namespace de Bubblewrap donde `/` es read-only, `WORK_DIR` se monta read-write y `/tmp` y `/run` son temporales.

`cwd` por sí solo **no** sería suficiente para garantizar esto; un proceso podría escribir en `/etc`, `/home/...` o seguir symlinks fuera del proyecto. Por eso el servidor falla al arrancar si `WORK_DIR` está definido pero `bwrap` no está disponible.

En este modo se rechazan `sudo` local y toda ejecución SSH. Elevar privilegios en el host rompería la garantía local de confinamiento de escrituras y Bubblewrap local no puede restringir cambios realizados en un host remoto.

### `ALLOW_COMMANDS`

Lista separada por comas de nombres de ejecutables.

```json
"ALLOW_COMMANDS": "git,ls,cat,grep,python,node,pnpm"
```

- Vacía o ausente: se permiten todos los ejecutables.
- Con valor: solo se permiten los nombres exactos de la lista y deben invocarse por nombre, no mediante rutas como `/usr/bin/git`.
- Si un comando local empieza por `sudo`, se valida el ejecutable real posterior a `sudo`.
- Para `ssh_execute`, tanto `ssh` como el ejecutable remoto objetivo deben estar presentes en la allowlist.
- Las llamadas SSH arbitrarias mediante `shell_execute` se rechazan mientras la allowlist esté activa; debe usarse `ssh_execute` para poder validar también el ejecutable remoto. La forma simple de compatibilidad `["ssh", "host", "sudo", "command", ...]` sí se reconoce y valida.

La allowlist es por **ejecutable**, no por argumentos. Permitir `bash`, `python`, `node`, `env` u otra herramienta capaz de ejecutar procesos amplía significativamente lo que el cliente puede hacer.

## Instalación

```bash
uv sync
```

Para desarrollo:

```bash
uv sync --extra dev
```

Para instalar el ejecutable MCP como tool editable del usuario:

```bash
uv tool install --force --editable .
```

El ejecutable normalmente queda instalado como `~/.local/bin/mcp-shell-sudo`.

## Ejecutar manualmente

```bash
uv run mcp-shell-sudo
```

El transporte es `stdio`. No se escribe logging a stdout para no corromper JSON-RPC; los logs van a stderr.

## Configuración MCP

Ejemplo sin restricciones de comandos y con sudo:

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
        "PASSWORD_SUDO": "TU_PASSWORD",
        "WORK_DIR": "",
        "ALLOW_COMMANDS": ""
      }
    }
  }
}
```

Ejemplo usando el ejecutable instalado con contraseñas sudo local y remota diferentes:

```json
{
  "mcpServers": {
    "mcp-shell-sudo": {
      "command": "/home/user/.local/bin/mcp-shell-sudo",
      "env": {
        "PASSWORD_SUDO": "PASSWORD_SUDO_LOCAL",
        "PASSWORD_SUDO_SSH": "PASSWORD_SUDO_REMOTA"
      }
    }
  }
}
```

Si sudo local y remoto usan la misma contraseña, omite `PASSWORD_SUDO_SSH`; `PASSWORD_SUDO` se reutiliza automáticamente para sudo remoto.

Ejemplo confinado a un proyecto:

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
        "WORK_DIR": "/home/user/proyectos/app",
        "ALLOW_COMMANDS": "git,ls,cat,grep,find,python,node,pnpm"
      }
    }
  }
}
```

## Tool `shell_execute`

Entrada básica:

```json
{
  "command": ["ls", "-la"]
}
```

Con directorio:

```json
{
  "command": ["git", "status"],
  "directory": "backend"
}
```

Con stdin:

```json
{
  "command": ["cat"],
  "stdin": "hola\n"
}
```

Con sudo:

```json
{
  "command": ["sudo", "id"]
}
```

Respuesta:

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

## Tool `ssh_execute`

Usa `ssh_execute` cuando el comando deba ejecutarse en otra máquina. El array `command` debe contener el ejecutable remoto y sus argumentos **sin** `sudo`; la elevación se solicita con `sudo: true`.

Ejecución remota básica:

```json
{
  "host": "alias-servidor",
  "command": ["id"]
}
```

Sudo remoto:

```json
{
  "host": "alias-servidor",
  "command": ["systemctl", "status", "docker", "--no-pager"],
  "sudo": true
}
```

Puerto SSH y TTY opcionales:

```json
{
  "host": "usuario@example.com",
  "command": ["id"],
  "sudo": true,
  "port": 2222,
  "tty": true,
  "timeout": 120
}
```

- `host`: destino o alias de OpenSSH definido, por ejemplo, en `~/.ssh/config`.
- `command`: argv remoto excluyendo `sudo`.
- `sudo`: con `true`, ejecuta el comando remoto como `sudo -S -p '' -- ...` cuando existe contraseña sudo remota; de lo contrario usa `sudo -n -- ...`.
- `stdin`: datos opcionales entregados al comando remoto después de la contraseña sudo.
- `port`: puerto TCP opcional entre 1 y 65535.
- `tty`: usa `ssh -tt`; actívalo únicamente si la policy sudo remota exige TTY.
- `timeout`: 60 segundos por defecto, con máximo de 600 segundos.

La autenticación de login SSH la maneja el cliente OpenSSH local. El servidor no inyecta una contraseña de inicio de sesión SSH.

Por compatibilidad, `shell_execute` también reconoce la forma simple común:

```json
{
  "command": ["ssh", "alias-servidor", "sudo", "id"]
}
```

Para ejecución remota explícita o requisitos SSH más complejos, usa preferiblemente `ssh_execute`.

## Tool `shell_config`

Devuelve la configuración efectiva que no es secreta. Nunca devuelve `PASSWORD_SUDO` ni `PASSWORD_SUDO_SSH`; solo informa estado no sensible, como si existe inyección de contraseña sudo local/remota, si `ssh` está disponible, si `WORK_DIR` está activo y cuál es la allowlist efectiva.

## Seguridad implementada

- `create_subprocess_exec`: no usa `shell=True`.
- `command` es un argv explícito.
- Las contraseñas sudo local y remota no aparecen en argv ni en el environment del proceso hijo; además, los secretos configurados se redactan de stdout/stderr antes de devolver resultados.
- Si no existe una contraseña sudo aplicable, `sudo -n` evita que el proceso se quede esperando un prompt interactivo.
- La autenticación de login SSH queda delegada a la configuración, llaves y agents de OpenSSH; el servidor MCP no maneja contraseñas de login SSH.
- `SSH_AUTH_SOCK` se reenvía cuando existe para que OpenSSH pueda usar el SSH agent actual.
- Los elementos argv remotos se citan con semántica `shlex` antes de entregarse a OpenSSH.
- `ALLOW_COMMANDS` valida el ejecutable local después de un `sudo` simple y, para `ssh_execute`, valida tanto `ssh` como el ejecutable remoto objetivo.
- PATH fijo y reducido para evitar resolver ejecutables desde `.` o rutas arbitrarias heredadas.
- Entorno hijo reducido; no se hereda todo el environment del proceso MCP.
- Timeout por llamada: 60 s por defecto, máximo 600 s.
- Límite de salida: 2 MiB por stream; al excederlo se termina el grupo de procesos.
- Los procesos corren en una sesión nueva y el grupo completo se termina en timeout.
- `WORK_DIR` usa Bubblewrap y falla cerrado si no puede ofrecer aislamiento. SSH y sudo se deshabilitan mientras `WORK_DIR` está activo cuando no puede garantizarse el confinamiento.

## Límites importantes

1. `ALLOW_COMMANDS=""` significa ejecución arbitraria con los permisos del usuario que ejecuta el MCP. Si además existe una contraseña sudo, el cliente puede solicitar acciones root localmente y, mediante `ssh_execute`, en hosts SSH para los que ya exista autenticación. Úsalo solo con un cliente/modelo que controles.
2. Una allowlist de ejecutables no valida semánticamente sus argumentos. `python`, `node`, `bash`, `sh`, `perl`, etc. son equivalentes a permitir ejecución de código.
3. `WORK_DIR` es un mecanismo de confinamiento local del filesystem, no una VM ni un sandbox de red. Como no puede limitar cambios remotos, SSH se deshabilita intencionalmente cuando `WORK_DIR` está configurado.
4. La ejecución remota puede modificar otra máquina con los permisos de la cuenta SSH o como root cuando `sudo: true` tiene éxito. La verificación y confianza del host permanecen bajo la configuración OpenSSH del usuario y `known_hosts`.
5. `PASSWORD_SUDO` y `PASSWORD_SUDO_SSH` en la configuración del cliente MCP siguen siendo secretos almacenados en ese archivo. Restringe sus permisos (`chmod 600`) y evita versionarlos.

## Pruebas

```bash
uv run --extra dev pytest
uv run --extra dev ruff check .
```
