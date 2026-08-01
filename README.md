# MCP Drive Local — mini-agente (puente disco ↔ Drive)

Complemento **local** del conector alojado **MCP Drive**. Corre en `127.0.0.1`
(no expuesto a la red) y mueve archivos y carpetas entre el ordenador y Google
Drive **sin base64 y de cualquier tamaño** (subida resumible). Los bytes van
disco → agente → Drive, sin pasar por el modelo.

## Herramientas

| Herramienta | Qué hace |
|---|---|
| `local_allowed_dirs` | Muestra las carpetas permitidas |
| `local_list` | Lista una carpeta local |
| `local_upload_to_drive` | Sube un archivo del disco a Drive |
| `local_upload_folder` | Sube una carpeta entera (con estructura) |
| `local_download_from_drive` | Baja un archivo de Drive al disco |
| `local_download_folder` | Baja una carpeta entera al disco |

## Instalación (un comando)

```bash
git clone https://github.com/<tu-cuenta>/mcp-drive-local.git
cd mcp-drive-local
cp .env.example .env      # rellena credenciales y ALLOWED_DIRS
bash install.sh
```

El instalador crea el entorno, deja el agente como servicio (launchd en macOS,
systemd en Linux) y lo arranca. Luego, en Claude:
**Ajustes → Conectores → Añadir conector personalizado → `http://127.0.0.1:8765/mcp`**

## Seguridad

- Solo accede a las carpetas de `ALLOWED_DIRS`. Nada fuera de ahí.
- Escucha solo en `127.0.0.1`: no accesible desde la red.
- Las credenciales viven en `.env` local (nunca en GitHub).

## Licencia

Uso del titular. Sin garantía.
