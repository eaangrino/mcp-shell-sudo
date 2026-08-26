# mcp-shell-sudo

MCP server para Linux escrito en Python que ejecuta comandos locales mediante argv, soporta `sudo` de forma no interactiva usando `PASSWORD_SUDO`, permite una allowlist opcional con `ALLOW_COMMANDS` y puede confinar las escrituras del filesystem a `WORK_DIR` usando Bubblewrap.

## Requisitos

- Linux.
- Python 3.11+.
- `uv` recomendado.
- MCP Python SDK 2.0.0.
- `sudo` si se van a ejecutar comandos elevados.
- `bubblewrap` si se configura `WORK_DIR`.

En Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y bubblewrap
```

## Variables de entorno

### `PASSWORD_SUDO`

Contraseña del usuario que ejecuta el MCP.

- Vacía o ausente: el servidor nunca inventa ni solicita interactivamente una contraseña. Los comandos con prefijo `sudo` se lanzan con `sudo -n` y solo funcionarán si existe una credencial ya válida o una regla `NOPASSWD`.
- Con valor: `sudo` se ejecuta con `-S` y la contraseña se entrega por **stdin**, nunca por argv ni por logs.

Ejemplo:

```json
"PASSWORD_SUDO": "mi-contraseña"
```

La forma soportada es:

```json
["sudo", "id"]
```

No se aceptan flags propios de `sudo` como `sudo -u postgres ...`; esto evita que la capa de policy tenga que interpretar el grammar completo de `sudo`.

### `WORK_DIR`

Directorio de trabajo opcional.

- Vacío o ausente: el MCP puede trabajar desde cualquier directorio existente que se indique en la llamada.
- Con valor: el servidor entra en modo estricto. `directory` debe resolver dentro de `WORK_DIR` y cada proceso se ejecuta dentro de un mount namespace de Bubblewrap donde `/` es read-only, `WORK_DIR` se monta read-write y `/tmp` y `/run` son temporales.

`cwd` por sí solo **no** sería suficiente para garantizar esto; un proceso podría escribir en `/etc`, `/home/...` o seguir symlinks fuera del proyecto. Por eso el servidor falla al arrancar si `WORK_DIR` está definido pero `bwrap` no está disponible.

En este modo se rechaza `sudo`: elevar privilegios en el host sería incompatible con la garantía de que las escrituras permanezcan confinadas al workspace.

### `ALLOW_COMMANDS`

Lista separada por comas de nombres de ejecutables.

```json
"ALLOW_COMMANDS": "git,ls,cat,grep,python,node,pnpm"
```

- Vacía o ausente: se permiten todos los ejecutables.
- Con valor: solo se permiten los nombres exactos de la lista y deben invocarse por nombre, no mediante rutas como `/usr/bin/git`.
- Si el comando empieza por `sudo`, se valida el ejecutable real posterior a `sudo`.

La allowlist es por **ejecutable**, no por argumentos. Permitir `bash`, `python`, `node`, `env` u otra herramienta capaz de ejecutar procesos amplía significativamente lo que el cliente puede hacer.

## Instalación

```bash
uv sync
```

Para desarrollo:

```bash
uv sync --extra dev
```

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

## Tool `shell_config`

Devuelve la configuración efectiva que no es secreta. Nunca devuelve `PASSWORD_SUDO`; solo indica si existe una contraseña configurada.

## Seguridad implementada

- `create_subprocess_exec`: no usa `shell=True`.
- `command` es un argv explícito.
- La contraseña de sudo no aparece en argv, respuesta, logs ni variables de entorno del proceso hijo.
- Si no hay `PASSWORD_SUDO`, `sudo -n` evita que el proceso se quede esperando un prompt.
- `ALLOW_COMMANDS` valida el ejecutable real después de un `sudo` simple.
- PATH fijo y reducido para evitar resolver ejecutables desde `.` o rutas arbitrarias heredadas.
- Entorno hijo reducido; no se hereda todo el environment del proceso MCP.
- Timeout por llamada: 60 s por defecto, máximo 600 s.
- Límite de salida: 2 MiB por stream; al excederlo se termina el grupo de procesos.
- Los procesos corren en una sesión nueva y el grupo completo se termina en timeout.
- `WORK_DIR` usa Bubblewrap y falla cerrado si no puede ofrecer aislamiento.

## Límites importantes

1. `ALLOW_COMMANDS=""` significa ejecución arbitraria con los permisos del usuario. Si además se configura `PASSWORD_SUDO`, el cliente MCP puede solicitar acciones como root. Úsalo solo con un cliente/modelo que controles.
2. Una allowlist de ejecutables no valida semánticamente sus argumentos. `python`, `node`, `bash`, `sh`, `perl`, etc. son equivalentes a permitir ejecución de código.
3. `WORK_DIR` confina el filesystem visible como writable, pero no pretende ser una VM ni un sandbox de red. Un comando con acceso de red todavía puede modificar recursos remotos para los que tenga credenciales.
4. `PASSWORD_SUDO` en la configuración del cliente MCP sigue siendo un secreto almacenado en ese archivo. Restringe sus permisos (`chmod 600`) y evita versionarlo.

## Pruebas

```bash
uv run --extra dev pytest
uv run --extra dev ruff check .
```
