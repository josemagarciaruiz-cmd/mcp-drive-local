"""
MCP Drive Local — mini-agente puente entre el DISCO del equipo y Google Drive.

Corre en 127.0.0.1 (solo local, no expuesto a la red). Sube y baja archivos y
carpetas enteras entre el ordenador y Drive, con subida resumible (cualquier
tamaño) y SIN base64: los bytes van disco -> agente -> Drive, sin pasar por el
modelo. Complementa al conector alojado "MCP Drive" (la URL del VPS).

Seguridad: solo accede a las carpetas declaradas en ALLOWED_DIRS.

Variables de entorno (ver .env.example):
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN
    ALLOWED_DIRS   (carpetas permitidas, separadas por ':')
    AGENT_HOST     (por defecto 127.0.0.1)
    AGENT_PORT     (por defecto 8765)

Autor: José María García Ruiz · josemaria.ai
"""

import os
import mimetypes

from mcp.server.fastmcp import FastMCP

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive"]
TOKEN_URI = "https://oauth2.googleapis.com/token"

AGENT_HOST = os.environ.get("AGENT_HOST", "127.0.0.1")
AGENT_PORT = int(os.environ.get("AGENT_PORT", "8765"))
ALLOWED = [os.path.realpath(os.path.expanduser(p.strip()))
           for p in os.environ.get("ALLOWED_DIRS", "~").split(":") if p.strip()]

mcp = FastMCP("MCP Drive Local", host=AGENT_HOST, port=AGENT_PORT)

_svc = None


def _drive():
    global _svc
    if _svc is None:
        for n in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
            if not os.environ.get(n):
                raise RuntimeError("Falta la variable de entorno " + n)
        creds = Credentials(
            token=None, refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
            token_uri=TOKEN_URI, client_id=os.environ["GOOGLE_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_CLIENT_SECRET"], scopes=SCOPES)
        _svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _svc


def _safe(path: str) -> str:
    """Devuelve la ruta absoluta si está dentro de ALLOWED_DIRS; si no, error."""
    p = os.path.realpath(os.path.expanduser(path))
    for root in ALLOWED:
        if p == root or p.startswith(root + os.sep):
            return p
    raise PermissionError(
        "Ruta fuera de las carpetas permitidas (" + ", ".join(ALLOWED) + "): " + path)


def _err(action: str, e: Exception) -> dict:
    if isinstance(e, HttpError):
        return {"ok": False, "action": action,
                "error": "Drive API %s: %s" % (e.resp.status, str(e))}
    return {"ok": False, "action": action, "error": str(e)}


# --------------------------------------------------------------------------- #
# Inspección
# --------------------------------------------------------------------------- #

@mcp.tool()
def local_allowed_dirs() -> dict:
    """Muestra las carpetas locales que el agente tiene permitido tocar."""
    return {"ok": True, "allowed": ALLOWED}


@mcp.tool()
def local_list(path: str = "~") -> dict:
    """Lista el contenido de una carpeta local (dentro de las permitidas)."""
    try:
        p = _safe(path)
        items = []
        for name in sorted(os.listdir(p)):
            fp = os.path.join(p, name)
            items.append({"name": name, "is_dir": os.path.isdir(fp),
                          "size": os.path.getsize(fp) if os.path.isfile(fp) else None})
        return {"ok": True, "path": p, "count": len(items), "items": items}
    except Exception as e:
        return _err("local_list", e)


# --------------------------------------------------------------------------- #
# Subir (disco -> Drive)
# --------------------------------------------------------------------------- #

@mcp.tool()
def local_upload_to_drive(local_path: str, drive_parent_id: str = "root",
                          name: str = None) -> dict:
    """Sube un archivo del disco a Drive (resumible, cualquier tamaño)."""
    try:
        p = _safe(local_path)
        if not os.path.isfile(p):
            return {"ok": False, "error": "No es un archivo: " + local_path}
        mime = mimetypes.guess_type(p)[0] or "application/octet-stream"
        media = MediaFileUpload(p, mimetype=mime, resumable=True)
        body = {"name": name or os.path.basename(p), "parents": [drive_parent_id]}
        meta = _drive().files().create(
            body=body, media_body=media,
            fields="id, name, size, webViewLink", supportsAllDrives=True).execute()
        return {"ok": True, "file": meta}
    except Exception as e:
        return _err("local_upload_to_drive", e)


@mcp.tool()
def local_upload_folder(local_path: str, drive_parent_id: str = "root") -> dict:
    """Sube una carpeta ENTERA del disco a Drive, recreando su estructura."""
    try:
        base = _safe(local_path)
        if not os.path.isdir(base):
            return {"ok": False, "error": "No es una carpeta: " + local_path}
        svc = _drive()
        top = svc.files().create(
            body={"name": os.path.basename(base.rstrip(os.sep)),
                  "mimeType": "application/vnd.google-apps.folder",
                  "parents": [drive_parent_id]},
            fields="id", supportsAllDrives=True).execute()
        mapping = {base: top["id"]}
        count = 0
        for root, dirs, files in os.walk(base):
            parent = mapping[root]
            for dname in dirs:
                fo = svc.files().create(
                    body={"name": dname, "mimeType": "application/vnd.google-apps.folder",
                          "parents": [parent]}, fields="id", supportsAllDrives=True).execute()
                mapping[os.path.join(root, dname)] = fo["id"]
            for fname in files:
                fp = os.path.join(root, fname)
                mime = mimetypes.guess_type(fp)[0] or "application/octet-stream"
                media = MediaFileUpload(fp, mimetype=mime, resumable=True)
                svc.files().create(
                    body={"name": fname, "parents": [parent]}, media_body=media,
                    fields="id", supportsAllDrives=True).execute()
                count += 1
        return {"ok": True, "drive_folder_id": top["id"], "archivos_subidos": count}
    except Exception as e:
        return _err("local_upload_folder", e)


# --------------------------------------------------------------------------- #
# Bajar (Drive -> disco)
# --------------------------------------------------------------------------- #

def _download_one(svc, meta, dest_dir) -> str:
    mime = meta.get("mimeType", "")
    name = meta["name"]
    if mime.startswith("application/vnd.google-apps"):
        data = svc.files().export(fileId=meta["id"], mimeType="application/pdf").execute()
        out = os.path.join(dest_dir, name + ".pdf")
        with open(out, "wb") as f:
            f.write(data)
    else:
        req = svc.files().get_media(fileId=meta["id"])
        out = os.path.join(dest_dir, name)
        with open(out, "wb") as f:
            dl = MediaIoBaseDownload(f, req)
            done = False
            while not done:
                _, done = dl.next_chunk()
    return out


@mcp.tool()
def local_download_from_drive(file_id: str, local_dir: str) -> dict:
    """Descarga un archivo de Drive al disco. Los documentos de Google se
    guardan como PDF."""
    try:
        d = _safe(local_dir)
        os.makedirs(d, exist_ok=True)
        svc = _drive()
        meta = svc.files().get(fileId=file_id, fields="id, name, mimeType",
                               supportsAllDrives=True).execute()
        out = _download_one(svc, meta, d)
        return {"ok": True, "path": out}
    except Exception as e:
        return _err("local_download_from_drive", e)


@mcp.tool()
def local_download_folder(folder_id: str, local_dir: str) -> dict:
    """Descarga una carpeta ENTERA de Drive al disco, recreando su estructura."""
    try:
        base = _safe(local_dir)
        svc = _drive()
        meta = svc.files().get(fileId=folder_id, fields="name", supportsAllDrives=True).execute()
        root = os.path.join(base, meta["name"])
        os.makedirs(root, exist_ok=True)
        count = [0]

        def walk(fid, path):
            res = svc.files().list(
                q="'%s' in parents and trashed = false" % fid,
                fields="files(id, name, mimeType)", pageSize=1000,
                supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            for f in res.get("files", []):
                if f["mimeType"] == "application/vnd.google-apps.folder":
                    sub = os.path.join(path, f["name"])
                    os.makedirs(sub, exist_ok=True)
                    walk(f["id"], sub)
                else:
                    _download_one(svc, f, path)
                    count[0] += 1

        walk(folder_id, root)
        return {"ok": True, "path": root, "archivos": count[0]}
    except Exception as e:
        return _err("local_download_folder", e)


if __name__ == "__main__":
    # Transporte seleccionable por entorno:
    #   MCP_TRANSPORT=stdio            -> conector local en Claude Desktop (por defecto)
    #   MCP_TRANSPORT=streamable-http  -> servicio HTTP (escenario oficina por URL)
    _t = os.environ.get("MCP_TRANSPORT", "stdio")
    if _t == "streamable-http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
