# Guía sencilla: MCP Drive y Agente Drive

_Última actualización: 1 de agosto de 2026_

## 1. Qué tienes (en cristiano)

Tienes **dos herramientas** que trabajan juntas pero hacen cosas distintas:

- **MCP Drive** → vive en tu servidor de Hostinger. Se usa **por URL** desde cualquier
  ordenador. Hace todo lo que ocurre **dentro** de Google Drive: mover, renombrar, copiar,
  crear carpetas, editar documentos, leer PDF/Word/Excel, papelera, permisos, etc.
  *No puede* ver ni tocar los archivos guardados en el disco de tu ordenador.

- **Agente Drive** → vive **en tu ordenador**. Es un pequeño programa local. Su único
  trabajo es hacer de **puente entre el disco físico y Drive**: subir a Drive un archivo
  que tienes en el ordenador, o bajar a una carpeta del ordenador algo que está en Drive.

Regla de oro para recordarlo:

> **MCP Drive = todo lo de dentro de Drive, desde cualquier sitio (por URL).**
> **Agente Drive = el puente con el disco de una máquina concreta (en local).**

Por eso el Agente Drive no se puede poner "por URL": una dirección remota no puede ver
los archivos de tu ordenador. Tiene que estar instalado en la máquina cuyo disco quieras
conectar.

## 2. Cómo se usa cada uno

**MCP Drive (por URL).** Ya está conectado. Le pides cosas en lenguaje natural:
"mueve tal documento a tal carpeta", "renombra estos archivos", "léeme este PDF de Drive",
"crea una carpeta y comparte tal fichero". Funciona igual en casa y en la oficina porque
va contra el servidor.

**Agente Drive (en local).** Cuando esté conectado, le pides el trasvase de ficheros:
"sube el archivo X de mi carpeta Descargas a tal carpeta de Drive", "bájame tal carpeta de
Drive a mi disco". Solo ve las carpetas que le hayas autorizado (por seguridad).

## 3. Cómo se conecta el Agente Drive a Claude

Importante: el Agente Drive **no se da de alta por URL** (eso pide HTTPS y es para
servidores remotos). Los programas locales se conectan por **stdio**, es decir, como una
entrada en el fichero de configuración de Claude, igual que el resto de tus MCP locales
(Jurisprudenciador, Outlook, etc.). Claude arranca el agente solo cuando lo necesita.

Ya está hecho en este ordenador. Solo queda un paso para activarlo: **cerrar Claude del
todo y volver a abrirlo**. Al reabrir aparece el conector "agente-drive" en la lista.

## 4. Instalar el Agente Drive en un ordenador nuevo (p. ej. la oficina)

1. **Traer el programa.** Clona el repositorio privado o copia la carpeta:
   `git clone https://github.com/josemagarciaruiz-cmd/mcp-drive-local`
2. **Crear el archivo `.env`.** Copia `.env.example` a `.env` y rellena:
   - Las credenciales de Google (las mismas de siempre: CLIENT_ID, CLIENT_SECRET,
     REFRESH_TOKEN).
   - `ALLOWED_DIRS` con las carpetas **reales de esa máquina** que quieras conectar
     (las de la oficina, no las de casa).
3. **Instalar.** Ejecuta `./install.sh` (si diera "permission denied", antes:
   `chmod +x install.sh`). Esto prepara el entorno y las dependencias.
4. **Dar de alta el conector en Claude.** Se añade una entrada `agente-drive` en el fichero
   de configuración de Claude de ese equipo (modo stdio), apuntando al Python del entorno y
   al `agent.py`, con las credenciales y `ALLOWED_DIRS`. (Se puede automatizar con un
   pequeño script para que sea copiar-pegar.)
5. **Reiniciar Claude** en ese ordenador.

La URL de **MCP Drive** no cambia y ya la tienes en la oficina; solo el Agente Drive se
instala máquina por máquina.

## 5. Detalles técnicos (por si hacen falta)

- El Agente Drive puede arrancar de dos formas, según la variable `MCP_TRANSPORT`:
  - `stdio` (por defecto) → conector local en Claude Desktop. Es lo que usamos.
  - `streamable-http` → servicio HTTP en `127.0.0.1:8765` (por si algún día se quiere
    montar como servicio con URL local).
- Las credenciales y las carpetas autorizadas viven en el `.env` del ordenador (y, para el
  conector, en el config de Claude). Nunca se suben a GitHub.
- Herramientas del Agente Drive: `local_allowed_dirs`, `local_list`,
  `local_upload_to_drive`, `local_upload_folder`, `local_download_from_drive`,
  `local_download_folder`.
- Repositorio: https://github.com/josemagarciaruiz-cmd/mcp-drive-local (privado).
