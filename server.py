"""
MCP Drive — Servidor MCP con control TOTAL de Google Drive.

Cubre lo que el conector oficial de Claude NO hace: mover, renombrar,
enviar a papelera, restaurar, compartir (permisos), copiar, crear carpetas,
subir y descargar, y (con confirmación explícita) borrado definitivo.

Transporte: streamable-http (pensado para desplegar en un VPS, p.ej. Hostinger).
Autenticación: OAuth de usuario mediante refresh token (un solo usuario, el titular).

Variables de entorno necesarias (ver .env.example):
    GOOGLE_CLIENT_ID
    GOOGLE_CLIENT_SECRET
    GOOGLE_REFRESH_TOKEN
    MCP_HOST      (opcional, por defecto 0.0.0.0)
    MCP_PORT      (opcional, por defecto 8000)

Autor: José María García Ruiz · josemaria.ai
"""

import os
import base64
import io
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #

SCOPES = ["https://www.googleapis.com/auth/drive",
          "https://www.googleapis.com/auth/gmail.modify",
          "https://www.googleapis.com/auth/gmail.send",
          "https://www.googleapis.com/auth/calendar",
          "https://www.googleapis.com/auth/tasks",
          "https://www.googleapis.com/auth/contacts.readonly"]
TOKEN_URI = "https://oauth2.googleapis.com/token"

# Campos estándar que devolvemos de cada archivo/carpeta
FILE_FIELDS = (
    "id, name, mimeType, parents, size, modifiedTime, createdTime, "
    "trashed, webViewLink, owners(emailAddress)"
)

MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))

mcp = FastMCP("MCP Drive", host=MCP_HOST, port=MCP_PORT)

_service = None  # cache del cliente de la API
_docs_service = None  # cache del cliente de Docs


def _build_creds():
    """Credenciales OAuth compartidas (Drive y Docs; el scope drive cubre ambas)."""
    for n in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
        if not os.environ.get(n):
            raise RuntimeError("Falta la variable de entorno " + n)
    return Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        token_uri=TOKEN_URI,
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=SCOPES,
    )


def _get_docs():
    """Cliente de la API de Google Docs (edicion del cuerpo de documentos)."""
    global _docs_service
    if _docs_service is None:
        _docs_service = build("docs", "v1", credentials=_build_creds(), cache_discovery=False)
    return _docs_service


def _get_service():
    """Construye (y cachea) el cliente de la Drive API a partir del refresh token."""
    global _service
    if _service is not None:
        return _service

    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN")

    missing = [
        n for n, v in (
            ("GOOGLE_CLIENT_ID", client_id),
            ("GOOGLE_CLIENT_SECRET", client_secret),
            ("GOOGLE_REFRESH_TOKEN", refresh_token),
        ) if not v
    ]
    if missing:
        raise RuntimeError(
            "Faltan variables de entorno: " + ", ".join(missing) +
            ". Configúralas antes de arrancar el servidor (ver .env.example)."
        )

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    _service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _service


def _err(action: str, e: Exception) -> dict:
    """Formatea un error de forma accionable para el agente."""
    if isinstance(e, HttpError):
        try:
            reason = e.error_details
        except Exception:
            reason = str(e)
        return {
            "ok": False,
            "action": action,
            "error": f"La API de Drive devolvió {e.resp.status}: {reason}",
            "sugerencia": "Revisa el fileId/permiso. 404 = no existe o sin acceso; "
                          "403 = falta de permiso o cuota; 401 = token caducado.",
        }
    return {"ok": False, "action": action, "error": str(e)}


def _file(meta: dict) -> dict:
    """Normaliza la metadata de un archivo a un dict limpio."""
    return {
        "id": meta.get("id"),
        "name": meta.get("name"),
        "mimeType": meta.get("mimeType"),
        "parents": meta.get("parents", []),
        "size": meta.get("size"),
        "modifiedTime": meta.get("modifiedTime"),
        "trashed": meta.get("trashed"),
        "link": meta.get("webViewLink"),
    }


# --------------------------------------------------------------------------- #
# AUDITORIA (registro de acciones que modifican Drive)
# --------------------------------------------------------------------------- #

import json as _json
import time as _time
import threading as _threading

AUDIT_PATH = os.environ.get("AUDIT_LOG_PATH") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "audit.jsonl")
_audit_lock = _threading.Lock()


def _audit(action: str, detail: dict) -> None:
    """Anexa una entrada JSONL al registro de auditoria. Nunca lanza."""
    try:
        rec = {"ts": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
               "action": action}
        rec.update(detail or {})
        d = os.path.dirname(AUDIT_PATH)
        if d:
            os.makedirs(d, exist_ok=True)
        with _audit_lock:
            with open(AUDIT_PATH, "a", encoding="utf-8") as f:
                f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# LECTURA
# --------------------------------------------------------------------------- #

@mcp.tool()
def drive_search(query: str, page_size: int = 50, page_token: Optional[str] = None) -> dict:
    """Busca archivos y carpetas en Drive con la sintaxis de consulta de la API.

    Ejemplos de `query`:
      - "name contains 'nomina'"
      - "'FOLDER_ID' in parents and trashed = false"
      - "mimeType = 'application/vnd.google-apps.folder'"
      - "fullText contains 'despido'"
    Devuelve hasta `page_size` resultados (máx. 1000).
    """
    try:
        service = _get_service()
        res = service.files().list(
            q=query,
            pageSize=max(1, min(page_size, 1000)),
            pageToken=page_token,
            fields=f"nextPageToken, files({FILE_FIELDS})",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files = [_file(f) for f in res.get("files", [])]
        return {"ok": True, "count": len(files), "files": files,
                "next_page_token": res.get("nextPageToken")}
    except Exception as e:
        return _err("drive_search", e)


@mcp.tool()
def drive_list_children(folder_id: str = "root", include_trashed: bool = False, page_token: Optional[str] = None) -> dict:
    """Lista el contenido directo de una carpeta. Usa 'root' para Mi unidad."""
    try:
        service = _get_service()
        q = f"'{folder_id}' in parents"
        if not include_trashed:
            q += " and trashed = false"
        res = service.files().list(
            q=q, pageSize=1000, pageToken=page_token,
            fields=f"nextPageToken, files({FILE_FIELDS})",
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        files = [_file(f) for f in res.get("files", [])]
        return {"ok": True, "folder_id": folder_id, "count": len(files), "files": files,
                "next_page_token": res.get("nextPageToken")}
    except Exception as e:
        return _err("drive_list_children", e)


@mcp.tool()
def drive_get_metadata(file_id: str) -> dict:
    """Devuelve la metadata de un archivo o carpeta por su id."""
    try:
        service = _get_service()
        meta = service.files().get(
            fileId=file_id, fields=FILE_FIELDS, supportsAllDrives=True
        ).execute()
        return {"ok": True, "file": _file(meta)}
    except Exception as e:
        return _err("drive_get_metadata", e)


# --------------------------------------------------------------------------- #
# ORGANIZACIÓN (lo que el conector oficial NO hace)
# --------------------------------------------------------------------------- #

@mcp.tool()
def drive_create_folder(name: str, parent_id: str = "root") -> dict:
    """Crea una carpeta nueva dentro de `parent_id` (por defecto Mi unidad)."""
    try:
        service = _get_service()
        body = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        meta = service.files().create(
            body=body, fields=FILE_FIELDS, supportsAllDrives=True
        ).execute()
        return {"ok": True, "file": _file(meta)}
    except Exception as e:
        return _err("drive_create_folder", e)


@mcp.tool()
def drive_move(file_id: str, new_parent_id: str, dry_run: bool = False) -> dict:
    """Mueve un archivo o carpeta a `new_parent_id` (quita el padre anterior).
    Con dry_run=True informa de lo que haria sin ejecutarlo."""
    try:
        service = _get_service()
        current = service.files().get(
            fileId=file_id, fields="parents, name", supportsAllDrives=True
        ).execute()
        prev_parents = ",".join(current.get("parents", []))
        if dry_run:
            return {"ok": True, "dry_run": True,
                    "haria": "mover '%s' a %s" % (current.get("name"), new_parent_id),
                    "desde": current.get("parents", []), "hacia": new_parent_id}
        meta = service.files().update(
            fileId=file_id,
            addParents=new_parent_id,
            removeParents=prev_parents,
            fields=FILE_FIELDS,
            supportsAllDrives=True,
        ).execute()
        _audit("drive_move", {"file_id": file_id, "name": meta.get("name"),
                              "desde": prev_parents, "hacia": new_parent_id})
        return {"ok": True, "file": _file(meta)}
    except Exception as e:
        return _err("drive_move", e)


@mcp.tool()
def drive_rename(file_id: str, new_name: str, dry_run: bool = False) -> dict:
    """Renombra un archivo o carpeta. Con dry_run=True no ejecuta, solo informa."""
    try:
        service = _get_service()
        if dry_run:
            cur = service.files().get(fileId=file_id, fields="name",
                                      supportsAllDrives=True).execute()
            return {"ok": True, "dry_run": True,
                    "haria": "renombrar '%s' -> '%s'" % (cur.get("name"), new_name)}
        meta = service.files().update(
            fileId=file_id, body={"name": new_name},
            fields=FILE_FIELDS, supportsAllDrives=True,
        ).execute()
        _audit("drive_rename", {"file_id": file_id, "new_name": new_name})
        return {"ok": True, "file": _file(meta)}
    except Exception as e:
        return _err("drive_rename", e)


@mcp.tool()
def drive_copy(file_id: str, new_name: Optional[str] = None,
               parent_id: Optional[str] = None) -> dict:
    """Copia un archivo. Opcionalmente con nombre nuevo y/o carpeta destino."""
    try:
        service = _get_service()
        body: dict[str, Any] = {}
        if new_name:
            body["name"] = new_name
        if parent_id:
            body["parents"] = [parent_id]
        meta = service.files().copy(
            fileId=file_id, body=body, fields=FILE_FIELDS, supportsAllDrives=True
        ).execute()
        return {"ok": True, "file": _file(meta)}
    except Exception as e:
        return _err("drive_copy", e)


# --------------------------------------------------------------------------- #
# PAPELERA Y BORRADO
# --------------------------------------------------------------------------- #

@mcp.tool()
def drive_trash(file_id: str, dry_run: bool = False) -> dict:
    """Envia un archivo o carpeta a la PAPELERA (reversible 30 dias).
    Con dry_run=True no ejecuta, solo informa."""
    try:
        service = _get_service()
        if dry_run:
            cur = service.files().get(fileId=file_id, fields="name",
                                      supportsAllDrives=True).execute()
            return {"ok": True, "dry_run": True,
                    "haria": "enviar a papelera '%s'" % cur.get("name")}
        meta = service.files().update(
            fileId=file_id, body={"trashed": True},
            fields=FILE_FIELDS, supportsAllDrives=True,
        ).execute()
        _audit("drive_trash", {"file_id": file_id, "name": meta.get("name")})
        return {"ok": True, "file": _file(meta)}
    except Exception as e:
        return _err("drive_trash", e)


@mcp.tool()
def drive_untrash(file_id: str) -> dict:
    """Restaura un archivo desde la papelera."""
    try:
        service = _get_service()
        meta = service.files().update(
            fileId=file_id, body={"trashed": False},
            fields=FILE_FIELDS, supportsAllDrives=True,
        ).execute()
        return {"ok": True, "file": _file(meta)}
    except Exception as e:
        return _err("drive_untrash", e)


@mcp.tool()
def drive_delete_permanent(file_id: str, confirm: bool = False) -> dict:
    """Borra DEFINITIVAMENTE un archivo (NO reversible). Requiere confirm=True.

    Úsalo solo con confirmación explícita del usuario; en la mayoría de casos
    es preferible drive_trash.
    """
    if not confirm:
        return {
            "ok": False,
            "action": "drive_delete_permanent",
            "error": "Borrado definitivo bloqueado. Vuelve a llamar con confirm=true "
                     "solo si el usuario lo ha pedido expresamente. Considera drive_trash.",
        }
    try:
        service = _get_service()
        service.files().delete(fileId=file_id, supportsAllDrives=True).execute()
        _audit("drive_delete_permanent", {"file_id": file_id})
        return {"ok": True, "deleted": file_id}
    except Exception as e:
        return _err("drive_delete_permanent", e)


# --------------------------------------------------------------------------- #
# COMPARTIR / PERMISOS
# --------------------------------------------------------------------------- #

@mcp.tool()
def drive_share(file_id: str, role: str = "reader", type: str = "user",
                email: Optional[str] = None, notify: bool = False) -> dict:
    """Crea un permiso sobre un archivo/carpeta.

    role: 'reader' | 'commenter' | 'writer' | 'owner'
    type: 'user' | 'group' | 'domain' | 'anyone'
    email: obligatorio si type es 'user' o 'group'.
    """
    try:
        service = _get_service()
        perm: dict[str, Any] = {"role": role, "type": type}
        if type in ("user", "group"):
            if not email:
                return {"ok": False, "action": "drive_share",
                        "error": "Para type 'user'/'group' hace falta 'email'."}
            perm["emailAddress"] = email
        res = service.permissions().create(
            fileId=file_id, body=perm, sendNotificationEmail=notify,
            fields="id, role, type, emailAddress", supportsAllDrives=True,
        ).execute()
        _audit("drive_share", {"file_id": file_id, "role": role, "type": type,
                               "email": email})
        return {"ok": True, "permission": res}
    except Exception as e:
        return _err("drive_share", e)


@mcp.tool()
def drive_list_permissions(file_id: str) -> dict:
    """Lista los permisos (con quién está compartido) de un archivo/carpeta."""
    try:
        service = _get_service()
        res = service.permissions().list(
            fileId=file_id,
            fields="permissions(id, role, type, emailAddress, displayName)",
            supportsAllDrives=True,
        ).execute()
        return {"ok": True, "permissions": res.get("permissions", [])}
    except Exception as e:
        return _err("drive_list_permissions", e)


# --------------------------------------------------------------------------- #
# SUBIR / DESCARGAR
# --------------------------------------------------------------------------- #

@mcp.tool()
def drive_upload_text(name: str, text_content: str, parent_id: str = "root",
                      mime_type: str = "text/plain", convert_to_doc: bool = False) -> dict:
    """Sube un archivo de texto nuevo con el contenido dado.

    Por defecto lo deja EN CRUDO con `mime_type` (p.ej. 'text/markdown' para poder
    trabajarlo luego con IA). Si `convert_to_doc=True`, Drive convierte el contenido
    (idealmente Markdown) en un Documento de Google FORMATEADO, en el servidor y sin
    base64: úsalo solo cuando quieras el documento «bonito»."""
    try:
        service = _get_service()
        media = MediaIoBaseUpload(
            io.BytesIO(text_content.encode("utf-8")), mimetype=mime_type, resumable=False
        )
        body = {"name": name, "parents": [parent_id]}
        if convert_to_doc:
            body["mimeType"] = "application/vnd.google-apps.document"
        meta = service.files().create(
            body=body, media_body=media, fields=FILE_FIELDS, supportsAllDrives=True
        ).execute()
        return {"ok": True, "file": _file(meta)}
    except Exception as e:
        return _err("drive_upload_text", e)


@mcp.tool()
def drive_upload_base64(name: str, base64_content: str, mime_type: str,
                        parent_id: str = "root") -> dict:
    """Sube un archivo binario nuevo a partir de contenido en base64."""
    try:
        service = _get_service()
        data = base64.b64decode(base64_content)
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=True)
        body = {"name": name, "parents": [parent_id]}
        meta = service.files().create(
            body=body, media_body=media, fields=FILE_FIELDS, supportsAllDrives=True
        ).execute()
        return {"ok": True, "file": _file(meta)}
    except Exception as e:
        return _err("drive_upload_base64", e)


@mcp.tool()
def drive_download_text(file_id: str) -> dict:
    """Descarga el contenido de texto de un archivo. Los documentos de Google
    se exportan a texto plano; el resto se devuelve como UTF-8 si es posible."""
    try:
        service = _get_service()
        meta = service.files().get(
            fileId=file_id, fields="mimeType, name", supportsAllDrives=True
        ).execute()
        mime = meta.get("mimeType", "")
        if mime.startswith("application/vnd.google-apps"):
            export_map = {
                "application/vnd.google-apps.document": "text/plain",
                "application/vnd.google-apps.spreadsheet": "text/csv",
                "application/vnd.google-apps.presentation": "text/plain",
            }
            export_mime = export_map.get(mime, "text/plain")
            data = service.files().export(fileId=file_id, mimeType=export_mime).execute()
            text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
        else:
            request = service.files().get_media(fileId=file_id)
            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            text = buf.getvalue().decode("utf-8", errors="replace")
        return {"ok": True, "name": meta.get("name"), "content": text}
    except Exception as e:
        return _err("drive_download_text", e)


@mcp.tool()
def drive_read_file(file_id: str, max_chars: int = 200000) -> dict:
    """Lee el CONTENIDO en texto de casi cualquier archivo de Drive: PDF, Word
    (.docx), Excel (.xlsx), PowerPoint (.pptx), texto/Markdown/CSV/JSON, Google
    nativo, e imágenes o PDF escaneado (por OCR). Extrae el texto en el servidor,
    sin base64 ni pasar el binario por el modelo. Recorta a `max_chars`."""
    try:
        svc = _get_service()
        meta = svc.files().get(fileId=file_id, fields="name, mimeType",
                               supportsAllDrives=True).execute()
        name = meta.get("name", "")
        mime = meta.get("mimeType", "")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""

        if mime.startswith("application/vnd.google-apps"):
            return drive_download_text(file_id)

        request = svc.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = dl.next_chunk()
        data = buf.getvalue()

        text = ""
        if mime == "application/pdf" or ext == "pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join((p.extract_text() or "") for p in reader.pages).strip()
            if not text:
                return drive_ocr(file_id)
        elif ext == "docx" or "wordprocessingml" in mime:
            import docx
            d = docx.Document(io.BytesIO(data))
            text = "\n".join(p.text for p in d.paragraphs)
        elif ext == "xlsx" or "spreadsheetml" in mime:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            rows = []
            for ws in wb.worksheets:
                rows.append("# " + ws.title)
                for r in ws.iter_rows(values_only=True):
                    rows.append("\t".join("" if c is None else str(c) for c in r))
            text = "\n".join(rows)
        elif ext == "pptx" or "presentationml" in mime:
            from pptx import Presentation
            prs = Presentation(io.BytesIO(data))
            parts = []
            for i, slide in enumerate(prs.slides, 1):
                parts.append("--- Diapositiva %d ---" % i)
                for shape in slide.shapes:
                    if getattr(shape, "has_text_frame", False):
                        parts.append(shape.text_frame.text)
            text = "\n".join(parts)
        elif mime.startswith("text/") or ext in ("txt", "md", "csv", "json", "xml", "html", "log"):
            text = data.decode("utf-8", errors="replace")
        elif mime.startswith("image/"):
            return drive_ocr(file_id)
        else:
            return {"ok": False, "action": "drive_read_file",
                    "error": "Formato no soportado para lectura de texto: " + (mime or ext)}

        return {"ok": True, "name": name, "mimeType": mime,
                "truncated": len(text) > max_chars, "content": text[:max_chars]}
    except Exception as e:
        return _err("drive_read_file", e)


# --------------------------------------------------------------------------- #
# EDICIÓN DE DOCUMENTOS DE GOOGLE (Docs API) — editar el CUERPO de un Doc
# --------------------------------------------------------------------------- #

def _doc_end_index(docs, document_id: str) -> int:
    """Índice de inserción al final del cuerpo (antes del salto de línea final)."""
    doc = docs.documents().get(documentId=document_id).execute()
    content = doc.get("body", {}).get("content", [])
    end = content[-1].get("endIndex", 2) if content else 2
    return end - 1


@mcp.tool()
def drive_doc_get_text(document_id: str) -> dict:
    """Devuelve el texto plano de un Documento de Google (para inspeccionarlo)."""
    try:
        docs = _get_docs()
        doc = docs.documents().get(documentId=document_id).execute()
        out = []
        for el in doc.get("body", {}).get("content", []):
            para = el.get("paragraph")
            if not para:
                continue
            for pe in para.get("elements", []):
                t = pe.get("textRun", {}).get("content")
                if t:
                    out.append(t)
        return {"ok": True, "title": doc.get("title"), "text": "".join(out)}
    except Exception as e:
        return _err("drive_doc_get_text", e)


@mcp.tool()
def drive_doc_append_heading(document_id: str, text: str, level: int = 2) -> dict:
    """Añade un ENCABEZADO (nivel 1-3) al final de un Documento de Google."""
    try:
        docs = _get_docs()
        idx = _doc_end_index(docs, document_id)
        style = {1: "HEADING_1", 2: "HEADING_2", 3: "HEADING_3"}.get(int(level), "HEADING_2")
        reqs = [
            {"insertText": {"location": {"index": idx}, "text": "\n" + text}},
            {"updateParagraphStyle": {
                "range": {"startIndex": idx + 1, "endIndex": idx + 1 + len(text)},
                "paragraphStyle": {"namedStyleType": style},
                "fields": "namedStyleType"}},
        ]
        docs.documents().batchUpdate(documentId=document_id, body={"requests": reqs}).execute()
        return {"ok": True, "document_id": document_id, "heading": text, "level": int(level)}
    except Exception as e:
        return _err("drive_doc_append_heading", e)


@mcp.tool()
def drive_doc_append_text(document_id: str, text: str) -> dict:
    """Añade un PÁRRAFO de texto normal al final de un Documento de Google."""
    try:
        docs = _get_docs()
        idx = _doc_end_index(docs, document_id)
        reqs = [
            {"insertText": {"location": {"index": idx}, "text": "\n" + text}},
            {"updateParagraphStyle": {
                "range": {"startIndex": idx + 1, "endIndex": idx + 1 + len(text)},
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "fields": "namedStyleType"}},
        ]
        docs.documents().batchUpdate(documentId=document_id, body={"requests": reqs}).execute()
        return {"ok": True, "document_id": document_id, "added_chars": len(text)}
    except Exception as e:
        return _err("drive_doc_append_text", e)


@mcp.tool()
def drive_doc_replace_text(document_id: str, find: str, replace_with: str,
                           match_case: bool = True) -> dict:
    """Reemplaza TODAS las apariciones de un texto dentro de un Documento de Google."""
    try:
        docs = _get_docs()
        reqs = [{"replaceAllText": {
            "containsText": {"text": find, "matchCase": bool(match_case)},
            "replaceText": replace_with}}]
        r = docs.documents().batchUpdate(documentId=document_id, body={"requests": reqs}).execute()
        occ = 0
        for rep in r.get("replies", []):
            occ += rep.get("replaceAllText", {}).get("occurrencesChanged", 0) or 0
        return {"ok": True, "document_id": document_id, "replaced": occ}
    except Exception as e:
        return _err("drive_doc_replace_text", e)


# --------------------------------------------------------------------------- #
# PLANTILLAS DE ESCRITOS (copiar plantilla + sustituir marcadores)
# --------------------------------------------------------------------------- #

@mcp.tool()
def drive_doc_from_template(template_id: str, new_name: str,
                            replacements: dict, parent_id: str = "root") -> dict:
    """Crea un documento a partir de una plantilla: copia el Doc `template_id`,
    sustituye los marcadores de `replacements` (p.ej. {"{{cliente}}": "Ana"}) y
    lo guarda como `new_name` en `parent_id`. Ideal para demandas, papeletas, etc."""
    try:
        svc = _get_service()
        meta = svc.files().copy(
            fileId=template_id, body={"name": new_name, "parents": [parent_id]},
            fields=FILE_FIELDS, supportsAllDrives=True,
        ).execute()
        reqs = [{"replaceAllText": {"containsText": {"text": k, "matchCase": True},
                                    "replaceText": str(v)}}
                for k, v in (replacements or {}).items()]
        if reqs:
            _get_docs().documents().batchUpdate(
                documentId=meta["id"], body={"requests": reqs}).execute()
        return {"ok": True, "file": _file(meta), "sustituciones": len(reqs)}
    except Exception as e:
        return _err("drive_doc_from_template", e)


# --------------------------------------------------------------------------- #
# HOJAS DE CÁLCULO (Google Sheets)
# --------------------------------------------------------------------------- #

_sheets_service = None


def _get_sheets():
    global _sheets_service
    if _sheets_service is None:
        _sheets_service = build("sheets", "v4", credentials=_build_creds(), cache_discovery=False)
    return _sheets_service


@mcp.tool()
def drive_sheet_read(spreadsheet_id: str, range_a1: str) -> dict:
    """Lee un rango en notación A1 (p.ej. 'Hoja1!A1:D20') de una hoja de cálculo."""
    try:
        r = _get_sheets().spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=range_a1).execute()
        return {"ok": True, "range": r.get("range"), "values": r.get("values", [])}
    except Exception as e:
        return _err("drive_sheet_read", e)


@mcp.tool()
def drive_sheet_write(spreadsheet_id: str, range_a1: str, values: list) -> dict:
    """Escribe una matriz de valores (lista de listas) en un rango A1."""
    try:
        r = _get_sheets().spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=range_a1,
            valueInputOption="USER_ENTERED", body={"values": values}).execute()
        return {"ok": True, "celdas_actualizadas": r.get("updatedCells")}
    except Exception as e:
        return _err("drive_sheet_write", e)


@mcp.tool()
def drive_sheet_append_row(spreadsheet_id: str, range_a1: str, values: list) -> dict:
    """Añade una fila (o varias) al final de la tabla que contiene `range_a1`."""
    try:
        rows = values if (values and isinstance(values[0], list)) else [values]
        r = _get_sheets().spreadsheets().values().append(
            spreadsheetId=spreadsheet_id, range=range_a1,
            valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
            body={"values": rows}).execute()
        return {"ok": True, "actualizacion": r.get("updates", {})}
    except Exception as e:
        return _err("drive_sheet_append_row", e)


# --------------------------------------------------------------------------- #
# NOVEDADES Y AUDITORÍA
# --------------------------------------------------------------------------- #

@mcp.tool()
def drive_novedades(since: str, page_size: int = 50) -> dict:
    """Lista lo creado o modificado desde una fecha (RFC3339, p.ej.
    '2026-07-25T00:00:00Z'). Sirve para saber 'qué hay nuevo' en el Drive."""
    try:
        q = (f"(createdTime > '{since}' or modifiedTime > '{since}') "
             f"and trashed = false")
        res = _get_service().files().list(
            q=q, pageSize=max(1, min(page_size, 1000)), orderBy="modifiedTime desc",
            fields=f"files({FILE_FIELDS})", supportsAllDrives=True,
            includeItemsFromAllDrives=True).execute()
        files = [_file(f) for f in res.get("files", [])]
        return {"ok": True, "desde": since, "count": len(files), "files": files}
    except Exception as e:
        return _err("drive_novedades", e)


@mcp.tool()
def drive_compartidos_externos(page_size: int = 100) -> dict:
    """Lista archivos compartidos por enlace público (cualquiera con el enlace).
    Auditoría de seguridad: útil por los datos de clientes en 01_JURIDICO."""
    try:
        q = ("(visibility = 'anyoneWithLink' or visibility = 'anyoneCanFind') "
             "and trashed = false")
        res = _get_service().files().list(
            q=q, pageSize=max(1, min(page_size, 1000)),
            fields=f"files({FILE_FIELDS})", supportsAllDrives=True,
            includeItemsFromAllDrives=True).execute()
        files = [_file(f) for f in res.get("files", [])]
        return {"ok": True, "count": len(files), "files": files}
    except Exception as e:
        return _err("drive_compartidos_externos", e)


@mcp.tool()
def drive_search_content(text: str, page_size: int = 25) -> dict:
    """Busca por el CONTENIDO (texto interno) de los archivos, no solo el título."""
    try:
        safe = text.replace("'", "\\'")
        q = f"fullText contains '{safe}' and trashed = false"
        res = _get_service().files().list(
            q=q, pageSize=max(1, min(page_size, 1000)),
            fields=f"files({FILE_FIELDS})", supportsAllDrives=True,
            includeItemsFromAllDrives=True).execute()
        files = [_file(f) for f in res.get("files", [])]
        return {"ok": True, "count": len(files), "files": files}
    except Exception as e:
        return _err("drive_search_content", e)


# --------------------------------------------------------------------------- #
# DOCS: viñetas, salto de página, y exportación a PDF
# --------------------------------------------------------------------------- #

@mcp.tool()
def drive_doc_append_bullets(document_id: str, items: list) -> dict:
    """Añade una lista con viñetas (una por cada elemento de `items`)."""
    try:
        docs = _get_docs()
        idx = _doc_end_index(docs, document_id)
        joined = "\n".join(items)
        reqs = [
            {"insertText": {"location": {"index": idx}, "text": "\n" + joined}},
            {"createParagraphBullets": {
                "range": {"startIndex": idx + 1, "endIndex": idx + 1 + len(joined)},
                "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"}},
        ]
        docs.documents().batchUpdate(documentId=document_id, body={"requests": reqs}).execute()
        return {"ok": True, "document_id": document_id, "items": len(items)}
    except Exception as e:
        return _err("drive_doc_append_bullets", e)


@mcp.tool()
def drive_doc_insert_page_break(document_id: str) -> dict:
    """Añade un salto de página al final del documento."""
    try:
        docs = _get_docs()
        idx = _doc_end_index(docs, document_id)
        reqs = [{"insertPageBreak": {"location": {"index": idx}}}]
        docs.documents().batchUpdate(documentId=document_id, body={"requests": reqs}).execute()
        return {"ok": True, "document_id": document_id}
    except Exception as e:
        return _err("drive_doc_insert_page_break", e)


@mcp.tool()
def drive_export_pdf(file_id: str, name: str, parent_id: str = "root") -> dict:
    """Exporta un documento de Google (Doc/Sheet/Slide) a PDF y lo guarda en Drive."""
    try:
        svc = _get_service()
        data = svc.files().export(fileId=file_id, mimeType="application/pdf").execute()
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype="application/pdf", resumable=True)
        meta = svc.files().create(
            body={"name": name, "parents": [parent_id]}, media_body=media,
            fields=FILE_FIELDS, supportsAllDrives=True).execute()
        return {"ok": True, "file": _file(meta)}
    except Exception as e:
        return _err("drive_export_pdf", e)


# --------------------------------------------------------------------------- #
# GESTIÓN AVANZADA (papelera, lotes, accesos directos, color, estrella)
# --------------------------------------------------------------------------- #

@mcp.tool()
def drive_list_trash(page_size: int = 100) -> dict:
    """Lista el contenido de la papelera."""
    try:
        res = _get_service().files().list(
            q="trashed = true", pageSize=max(1, min(page_size, 1000)),
            fields=f"files({FILE_FIELDS})", supportsAllDrives=True,
            includeItemsFromAllDrives=True).execute()
        files = [_file(f) for f in res.get("files", [])]
        return {"ok": True, "count": len(files), "files": files}
    except Exception as e:
        return _err("drive_list_trash", e)


@mcp.tool()
def drive_empty_trash(confirm: bool = False) -> dict:
    """Vacía la papelera DEFINITIVAMENTE (no reversible). Requiere confirm=True."""
    if not confirm:
        return {"ok": False, "action": "drive_empty_trash",
                "error": "Bloqueado. Vaciar la papelera es irreversible; vuelve a "
                         "llamar con confirm=true solo si el usuario lo pide expresamente."}
    try:
        _get_service().files().emptyTrash().execute()
        _audit("drive_empty_trash", {"papelera": "vaciada"})
        return {"ok": True, "papelera": "vaciada"}
    except Exception as e:
        return _err("drive_empty_trash", e)


@mcp.tool()
def drive_batch_move(file_ids: list, new_parent_id: str, dry_run: bool = False) -> dict:
    """Mueve varios archivos/carpetas a `new_parent_id` de una vez.
    Con dry_run=True informa de lo que haria sin mover nada."""
    results = []
    try:
        svc = _get_service()
        for fid in file_ids:
            try:
                cur = svc.files().get(fileId=fid, fields="parents, name", supportsAllDrives=True).execute()
                prev = ",".join(cur.get("parents", []))
                if dry_run:
                    results.append({"id": fid, "name": cur.get("name"),
                                    "dry_run": True, "hacia": new_parent_id})
                    continue
                svc.files().update(fileId=fid, addParents=new_parent_id,
                                   removeParents=prev, fields="id, name",
                                   supportsAllDrives=True).execute()
                _audit("drive_batch_move", {"file_id": fid, "name": cur.get("name"),
                                            "hacia": new_parent_id})
                results.append({"id": fid, "ok": True})
            except Exception as ie:
                results.append({"id": fid, "ok": False, "error": str(ie)})
        return {"ok": True, "dry_run": dry_run, "resultados": results}
    except Exception as e:
        return _err("drive_batch_move", e)


@mcp.tool()
def drive_create_shortcut(target_id: str, name: str, parent_id: str = "root") -> dict:
    """Crea un acceso directo a `target_id` dentro de `parent_id`."""
    try:
        body = {"name": name, "mimeType": "application/vnd.google-apps.shortcut",
                "parents": [parent_id], "shortcutDetails": {"targetId": target_id}}
        meta = _get_service().files().create(
            body=body, fields=FILE_FIELDS, supportsAllDrives=True).execute()
        return {"ok": True, "file": _file(meta)}
    except Exception as e:
        return _err("drive_create_shortcut", e)


@mcp.tool()
def drive_set_star(file_id: str, starred: bool = True) -> dict:
    """Marca o desmarca un archivo como Destacado."""
    try:
        meta = _get_service().files().update(
            fileId=file_id, body={"starred": bool(starred)},
            fields=FILE_FIELDS, supportsAllDrives=True).execute()
        return {"ok": True, "file": _file(meta)}
    except Exception as e:
        return _err("drive_set_star", e)


@mcp.tool()
def drive_set_folder_color(folder_id: str, color_rgb: str) -> dict:
    """Cambia el color de una carpeta (hex, p.ej. '#d50000' rojo, '#3f51b5' azul)."""
    try:
        meta = _get_service().files().update(
            fileId=folder_id, body={"folderColorRgb": color_rgb},
            fields=FILE_FIELDS, supportsAllDrives=True).execute()
        return {"ok": True, "file": _file(meta)}
    except Exception as e:
        return _err("drive_set_folder_color", e)


# --------------------------------------------------------------------------- #
# COMENTARIOS, REVISIONES, OCR Y CUOTA
# --------------------------------------------------------------------------- #

@mcp.tool()
def drive_list_comments(file_id: str) -> dict:
    """Lista los comentarios de un archivo (Doc, Sheet...)."""
    try:
        res = _get_service().comments().list(
            fileId=file_id,
            fields="comments(id, content, resolved, createdTime, author/displayName)").execute()
        return {"ok": True, "comments": res.get("comments", [])}
    except Exception as e:
        return _err("drive_list_comments", e)


@mcp.tool()
def drive_add_comment(file_id: str, content: str) -> dict:
    """Añade un comentario a un archivo."""
    try:
        res = _get_service().comments().create(
            fileId=file_id, body={"content": content},
            fields="id, content, createdTime").execute()
        return {"ok": True, "comment": res}
    except Exception as e:
        return _err("drive_add_comment", e)


@mcp.tool()
def drive_list_revisions(file_id: str) -> dict:
    """Lista el historial de versiones de un archivo."""
    try:
        res = _get_service().revisions().list(
            fileId=file_id,
            fields="revisions(id, modifiedTime, size, lastModifyingUser/displayName)").execute()
        return {"ok": True, "revisions": res.get("revisions", [])}
    except Exception as e:
        return _err("drive_list_revisions", e)


@mcp.tool()
def drive_ocr(file_id: str, language: str = "es", new_name: str = "OCR") -> dict:
    """Extrae el texto de un PDF o imagen mediante OCR (crea un Doc con el texto
    reconocido y devuelve ese texto). `language` en ISO (es, en...)."""
    try:
        svc = _get_service()
        doc = svc.files().copy(
            fileId=file_id, ocrLanguage=language,
            body={"name": new_name, "mimeType": "application/vnd.google-apps.document"},
            fields="id, name", supportsAllDrives=True).execute()
        data = svc.files().export(fileId=doc["id"], mimeType="text/plain").execute()
        text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
        return {"ok": True, "doc_id": doc["id"], "text": text}
    except Exception as e:
        return _err("drive_ocr", e)


@mcp.tool()
def drive_storage_quota() -> dict:
    """Devuelve el uso de almacenamiento de la cuenta de Drive."""
    try:
        r = _get_service().about().get(fields="storageQuota, user").execute()
        return {"ok": True, "storageQuota": r.get("storageQuota"), "user": r.get("user")}
    except Exception as e:
        return _err("drive_storage_quota", e)


# --------------------------------------------------------------------------- #
# GOBERNANZA: auditoria, ciclo de permisos y remediacion de compartidos
# --------------------------------------------------------------------------- #

@mcp.tool()
def drive_audit_log(n: int = 50) -> dict:
    """Devuelve las ultimas n entradas del registro de auditoria (acciones que
    modifican Drive: mover, renombrar, papelera, borrar, compartir, permisos)."""
    try:
        if not os.path.exists(AUDIT_PATH):
            return {"ok": True, "count": 0, "entries": [], "nota": "sin registro todavia"}
        with open(AUDIT_PATH, encoding="utf-8") as f:
            lines = [l for l in f.readlines() if l.strip()]
        sel = lines[-max(1, min(n, 1000)):]
        return {"ok": True, "count": len(sel), "entries": [_json.loads(l) for l in sel]}
    except Exception as e:
        return _err("drive_audit_log", e)


@mcp.tool()
def drive_remove_permission(file_id: str, permission_id: str) -> dict:
    """Revoca (elimina) un permiso concreto de un archivo/carpeta.
    Usa drive_list_permissions para ver los ids de permiso."""
    try:
        _get_service().permissions().delete(
            fileId=file_id, permissionId=permission_id, supportsAllDrives=True
        ).execute()
        _audit("drive_remove_permission", {"file_id": file_id, "permission_id": permission_id})
        return {"ok": True, "file_id": file_id, "permiso_eliminado": permission_id}
    except Exception as e:
        return _err("drive_remove_permission", e)


@mcp.tool()
def drive_update_permission(file_id: str, permission_id: str, role: str) -> dict:
    """Cambia el rol de un permiso existente (reader | commenter | writer)."""
    try:
        res = _get_service().permissions().update(
            fileId=file_id, permissionId=permission_id, body={"role": role},
            fields="id, role, type, emailAddress", supportsAllDrives=True,
        ).execute()
        _audit("drive_update_permission", {"file_id": file_id,
               "permission_id": permission_id, "role": role})
        return {"ok": True, "permission": res}
    except Exception as e:
        return _err("drive_update_permission", e)


@mcp.tool()
def drive_remediar_externos(page_size: int = 100, dry_run: bool = True) -> dict:
    """Encuentra ficheros compartidos por enlace publico (cualquiera con el enlace)
    y revoca ese permiso. Con dry_run=True (por defecto) solo informa de lo que
    quitaria; con dry_run=False elimina los permisos de tipo 'anyone'. Auditoria
    de seguridad para datos de clientes."""
    try:
        svc = _get_service()
        q = ("(visibility = 'anyoneWithLink' or visibility = 'anyoneCanFind') "
             "and trashed = false")
        res = svc.files().list(q=q, pageSize=max(1, min(page_size, 1000)),
                               fields=f"files({FILE_FIELDS})", supportsAllDrives=True,
                               includeItemsFromAllDrives=True).execute()
        # Recoger TODOS los ficheros publicos (paginando)
        files=[]; token=None
        while True:
            r = svc.files().list(q=q, pageSize=1000, pageToken=token,
                fields=f"nextPageToken, files({FILE_FIELDS})",
                supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            files.extend(r.get("files", []))
            token = r.get("nextPageToken")
            if not token:
                break
        acciones = []
        omitidos = 0
        for f in files:
            try:
                perms = svc.permissions().list(
                    fileId=f["id"], fields="permissions(id, type, role)",
                    supportsAllDrives=True).execute().get("permissions", [])
                for pm in perms:
                    if pm.get("type") == "anyone":
                        if dry_run:
                            acciones.append({"file_id": f["id"], "name": f.get("name"),
                                             "mimeType": f.get("mimeType"),
                                             "quitaria_permiso": pm.get("id"),
                                             "role": pm.get("role")})
                        else:
                            svc.permissions().delete(fileId=f["id"], permissionId=pm["id"],
                                                     supportsAllDrives=True).execute()
                            _audit("drive_remediar_externos",
                                   {"file_id": f["id"], "name": f.get("name"),
                                    "permiso_eliminado": pm.get("id")})
                            acciones.append({"file_id": f["id"], "name": f.get("name"),
                                             "permiso_eliminado": pm.get("id"),
                                             "role": pm.get("role")})
            except Exception:
                omitidos += 1
                continue
        return {"ok": True, "dry_run": dry_run, "num": len(acciones),
                "omitidos_sin_permiso": omitidos, "acciones": acciones}
    except Exception as e:
        return _err("drive_remediar_externos", e)



# --------------------------------------------------------------------------- #
# GMAIL (lectura de correo y volcado de adjuntos a Drive)
# --------------------------------------------------------------------------- #

_gmail_service = None

def _get_gmail():
    global _gmail_service
    if _gmail_service is None:
        _gmail_service = build("gmail", "v1", credentials=_build_creds(), cache_discovery=False)
    return _gmail_service


def _gmail_walk(payload):
    stack = [payload]
    while stack:
        p = stack.pop()
        for c in (p.get("parts") or []):
            stack.append(c)
        yield p


@mcp.tool()
def gmail_buscar(query: str, max_results: int = 20) -> dict:
    """Busca correos en Gmail con la sintaxis de Gmail. Ejemplos de `query`:
    'has:attachment newer_than:7d', 'from:cliente@dominio.com', 'subject:nomina'."""
    try:
        svc = _get_gmail()
        res = svc.users().messages().list(userId="me", q=query,
              maxResults=max(1, min(max_results, 100))).execute()
        out = []
        for m in res.get("messages", []):
            full = svc.users().messages().get(userId="me", id=m["id"], format="metadata",
                   metadataHeaders=["From", "Subject", "Date"]).execute()
            h = {x["name"]: x["value"] for x in full.get("payload", {}).get("headers", [])}
            out.append({"id": m["id"], "from": h.get("From"), "subject": h.get("Subject"),
                        "date": h.get("Date"), "snippet": full.get("snippet", "")[:160]})
        return {"ok": True, "count": len(out), "messages": out}
    except Exception as e:
        return _err("gmail_buscar", e)


@mcp.tool()
def gmail_leer(message_id: str, max_chars: int = 20000) -> dict:
    """Lee un correo de Gmail: remitente, asunto, fecha, texto y lista de adjuntos."""
    try:
        import base64 as _b64
        svc = _get_gmail()
        m = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
        payload = m.get("payload", {})
        h = {x["name"]: x["value"] for x in payload.get("headers", [])}
        text = ""
        adjs = []
        for p in _gmail_walk(payload):
            fn = p.get("filename")
            body = p.get("body", {})
            if fn:
                adjs.append({"filename": fn, "attachmentId": body.get("attachmentId"),
                             "size": body.get("size")})
            elif p.get("mimeType") == "text/plain" and body.get("data"):
                text += _b64.urlsafe_b64decode(body["data"]).decode("utf-8", "replace")
        return {"ok": True, "from": h.get("From"), "subject": h.get("Subject"),
                "date": h.get("Date"), "texto": text[:max_chars], "adjuntos": adjs}
    except Exception as e:
        return _err("gmail_leer", e)


@mcp.tool()
def gmail_guardar_adjuntos(message_id: str, parent_id: str = "root") -> dict:
    """Descarga los adjuntos de un correo de Gmail y los sube a una carpeta de Drive."""
    try:
        import base64 as _b64
        svc = _get_gmail()
        drv = _get_service()
        m = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
        saved = []
        for p in _gmail_walk(m.get("payload", {})):
            fn = p.get("filename")
            body = p.get("body", {})
            if fn and body.get("attachmentId"):
                att = svc.users().messages().attachments().get(
                    userId="me", messageId=message_id, id=body["attachmentId"]).execute()
                data = _b64.urlsafe_b64decode(att["data"])
                media = MediaIoBaseUpload(io.BytesIO(data),
                        mimetype="application/octet-stream", resumable=False)
                r = drv.files().create(body={"name": fn, "parents": [parent_id]},
                    media_body=media, fields="id, name").execute()
                _audit("gmail_guardar_adjuntos", {"message_id": message_id, "file": fn,
                       "drive_id": r["id"]})
                saved.append({"filename": fn, "drive_id": r["id"]})
        return {"ok": True, "guardados": len(saved), "adjuntos": saved}
    except Exception as e:
        return _err("gmail_guardar_adjuntos", e)



# --------------------------------------------------------------------------- #
# CALENDAR / MEET (agenda y reuniones con videollamada de Google Meet)
# --------------------------------------------------------------------------- #

_cal_service = None

def _get_cal():
    global _cal_service
    if _cal_service is None:
        _cal_service = build("calendar", "v3", credentials=_build_creds(), cache_discovery=False)
    return _cal_service


@mcp.tool()
def calendar_listar_eventos(time_min: Optional[str] = None, time_max: Optional[str] = None,
                            max_results: int = 20, calendar_id: str = "primary") -> dict:
    """Lista eventos del calendario. Fechas en RFC3339 (2026-08-05T00:00:00Z). Si no se
    da time_min, usa ahora (próximos eventos)."""
    try:
        import datetime as _dt
        svc = _get_cal()
        if not time_min:
            time_min = _dt.datetime.utcnow().isoformat() + "Z"
        params = dict(calendarId=calendar_id, timeMin=time_min, singleEvents=True,
                      orderBy="startTime", maxResults=max(1, min(max_results, 100)))
        if time_max:
            params["timeMax"] = time_max
        res = svc.events().list(**params).execute()
        out = []
        for e in res.get("items", []):
            st = e.get("start", {}); en = e.get("end", {})
            out.append({"id": e.get("id"), "summary": e.get("summary"),
                        "inicio": st.get("dateTime") or st.get("date"),
                        "fin": en.get("dateTime") or en.get("date"),
                        "meet": e.get("hangoutLink"),
                        "asistentes": [a.get("email") for a in e.get("attendees", [])],
                        "link": e.get("htmlLink")})
        return {"ok": True, "count": len(out), "eventos": out}
    except Exception as e:
        return _err("calendar_listar_eventos", e)


@mcp.tool()
def calendar_crear_evento(summary: str, start: str, end: str, description: str = "",
                          attendees: Optional[list] = None, con_meet: bool = True,
                          calendar_id: str = "primary") -> dict:
    """Crea un evento en el calendario. start/end en RFC3339 con zona
    (2026-08-05T10:00:00+01:00). con_meet=True añade enlace de Google Meet.
    attendees = lista de emails (se les envía invitación)."""
    try:
        import uuid as _uuid
        svc = _get_cal()
        body = {"summary": summary, "description": description,
                "start": {"dateTime": start}, "end": {"dateTime": end}}
        if attendees:
            body["attendees"] = [{"email": a} for a in attendees]
        params = dict(calendarId=calendar_id, body=body, sendUpdates="all")
        if con_meet:
            body["conferenceData"] = {"createRequest": {"requestId": str(_uuid.uuid4()),
                "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
            params["conferenceDataVersion"] = 1
        e = svc.events().insert(**params).execute()
        _audit("calendar_crear_evento", {"summary": summary, "start": start})
        return {"ok": True, "id": e.get("id"), "meet": e.get("hangoutLink"), "link": e.get("htmlLink")}
    except Exception as e:
        return _err("calendar_crear_evento", e)


@mcp.tool()
def calendar_buscar(query: str, max_results: int = 20, calendar_id: str = "primary") -> dict:
    """Busca eventos por texto (nombre, asistente, descripción)."""
    try:
        svc = _get_cal()
        res = svc.events().list(calendarId=calendar_id, q=query, singleEvents=True,
              orderBy="startTime", maxResults=max(1, min(max_results, 100))).execute()
        out = [{"id": e.get("id"), "summary": e.get("summary"),
                "inicio": (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date"),
                "link": e.get("htmlLink")} for e in res.get("items", [])]
        return {"ok": True, "count": len(out), "eventos": out}
    except Exception as e:
        return _err("calendar_buscar", e)


@mcp.tool()
def calendar_disponibilidad(time_min: str, time_max: str, calendar_id: str = "primary") -> dict:
    """Devuelve los tramos OCUPADOS entre dos fechas (RFC3339), para localizar huecos."""
    try:
        svc = _get_cal()
        res = svc.freebusy().query(body={"timeMin": time_min, "timeMax": time_max,
              "items": [{"id": calendar_id}]}).execute()
        busy = res.get("calendars", {}).get(calendar_id, {}).get("busy", [])
        return {"ok": True, "ocupado": busy}
    except Exception as e:
        return _err("calendar_disponibilidad", e)


# ==========================================================================
# BLOQUE: Gmail de ESCRITURA (enviar, responder, borradores, etiquetas)
# ==========================================================================


@mcp.tool()
def gmail_enviar(para: str, asunto: str, cuerpo: str, cc: Optional[str] = None, adjuntos_drive: Optional[list] = None):
    """Envia un correo desde Gmail. Compone un mensaje MIME con destinatario, asunto y cuerpo de texto plano; admite copia (cc). Si se indican fileIds de Google Drive en adjuntos_drive, descarga cada archivo y lo adjunta al correo. Devuelve el id del mensaje enviado."""
    try:
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from email.mime.base import MIMEBase
        from email import encoders
        svc = _get_gmail()
        msg = MIMEMultipart()
        msg['To'] = para
        msg['Subject'] = asunto
        if cc:
            msg['Cc'] = cc
        msg.attach(MIMEText(cuerpo, 'plain', 'utf-8'))
        if adjuntos_drive:
            drive = _get_service()
            for fid in adjuntos_drive:
                meta = drive.files().get(fileId=fid, fields='id, name, mimeType').execute()
                nombre = meta.get('name', str(fid))
                buf = io.BytesIO()
                downloader = MediaIoBaseDownload(buf, drive.files().get_media(fileId=fid))
                done = False
                while not done:
                    _progreso, done = downloader.next_chunk()
                buf.seek(0)
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(buf.read())
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', 'attachment', filename=nombre)
                msg.attach(part)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        enviado = svc.users().messages().send(userId='me', body={'raw': raw}).execute()
        _audit("gmail_enviar", {"para": para, "asunto": asunto, "cc": cc, "adjuntos": adjuntos_drive, "id": enviado.get('id')})
        return {"ok": True, "id": enviado.get('id'), "threadId": enviado.get('threadId')}
    except Exception as e:
        return _err("gmail_enviar", e)


@mcp.tool()
def gmail_responder(message_id: str, texto: str):
    """Responde a un correo existente dentro de su MISMO hilo. Obtiene el mensaje original para extraer el remitente, el asunto y las cabeceras de referencia; compone la respuesta con destinatario el remitente original, asunto con prefijo 'Re:' y cabeceras In-Reply-To y References; y la envia en el mismo threadId. Devuelve el id del mensaje enviado."""
    try:
        from email.mime.text import MIMEText
        svc = _get_gmail()
        original = svc.users().messages().get(
            userId='me', id=message_id, format='metadata',
            metadataHeaders=['From', 'Subject', 'Message-Id', 'References']).execute()
        thread_id = original.get('threadId')
        headers = original.get('payload', {}).get('headers', [])

        def _h(name):
            for h in headers:
                if h.get('name', '').lower() == name.lower():
                    return h.get('value')
            return None

        remitente = _h('From') or ''
        asunto_orig = _h('Subject') or ''
        msg_id_hdr = _h('Message-Id')
        refs = _h('References')
        asunto = asunto_orig if asunto_orig.lower().startswith('re:') else 'Re: ' + asunto_orig
        msg = MIMEText(texto, 'plain', 'utf-8')
        msg['To'] = remitente
        msg['Subject'] = asunto
        if msg_id_hdr:
            msg['In-Reply-To'] = msg_id_hdr
            msg['References'] = (refs + ' ' + msg_id_hdr) if refs else msg_id_hdr
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        enviado = svc.users().messages().send(userId='me', body={'raw': raw, 'threadId': thread_id}).execute()
        _audit("gmail_responder", {"message_id": message_id, "para": remitente, "asunto": asunto, "id": enviado.get('id')})
        return {"ok": True, "id": enviado.get('id'), "threadId": enviado.get('threadId')}
    except Exception as e:
        return _err("gmail_responder", e)


@mcp.tool()
def gmail_crear_borrador(para: str, asunto: str, cuerpo: str):
    """Crea un borrador de correo en Gmail SIN enviarlo. Compone un mensaje MIME con destinatario, asunto y cuerpo de texto plano y lo guarda como borrador. Devuelve el id del borrador creado."""
    try:
        from email.mime.text import MIMEText
        svc = _get_gmail()
        msg = MIMEText(cuerpo, 'plain', 'utf-8')
        msg['To'] = para
        msg['Subject'] = asunto
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        borrador = svc.users().drafts().create(userId='me', body={'message': {'raw': raw}}).execute()
        _audit("gmail_crear_borrador", {"para": para, "asunto": asunto, "id": borrador.get('id')})
        return {"ok": True, "id": borrador.get('id')}
    except Exception as e:
        return _err("gmail_crear_borrador", e)


@mcp.tool()
def gmail_etiquetar(message_id: str, add_labels: Optional[list] = None, remove_labels: Optional[list] = None):
    """Modifica las etiquetas de un mensaje de Gmail. Anade las etiquetas indicadas en add_labels y elimina las de remove_labels (identificadas por sus labelIds). Devuelve el mensaje con sus etiquetas actualizadas."""
    try:
        svc = _get_gmail()
        body = {}
        if add_labels:
            body['addLabelIds'] = add_labels
        if remove_labels:
            body['removeLabelIds'] = remove_labels
        res = svc.users().messages().modify(userId='me', id=message_id, body=body).execute()
        _audit("gmail_etiquetar", {"message_id": message_id, "add": add_labels, "remove": remove_labels})
        return {"ok": True, "id": res.get('id'), "labelIds": res.get('labelIds')}
    except Exception as e:
        return _err("gmail_etiquetar", e)


# =====================================================================
# BLOQUE: Calendar (Google Calendar v3)
# Se APPENDEA a server.py. No añade imports ni redefine nada.
# Usa helpers ya existentes: _get_cal(), _err(), _audit().
# =====================================================================


@mcp.tool()
def calendar_actualizar_evento(
    event_id: str,
    summary: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    description: Optional[str] = None,
    calendar_id: str = "primary",
):
    """Actualiza (patch) un evento del calendario con los campos no nulos.

    start/end se envían como {'dateTime': valor} en formato RFC3339.
    Notifica a todos los asistentes (sendUpdates='all'). Devuelve id,
    summary y enlace del evento actualizado.
    """
    try:
        cal = _get_cal()
        body = {}
        if summary is not None:
            body["summary"] = summary
        if description is not None:
            body["description"] = description
        if start is not None:
            body["start"] = {"dateTime": start}
        if end is not None:
            body["end"] = {"dateTime": end}
        ev = (
            cal.events()
            .patch(
                calendarId=calendar_id,
                eventId=event_id,
                body=body,
                sendUpdates="all",
            )
            .execute()
        )
        _audit(
            "calendar_actualizar_evento",
            f"calendar={calendar_id} event={event_id} campos={list(body.keys())}",
        )
        return {
            "id": ev.get("id"),
            "summary": ev.get("summary"),
            "link": ev.get("htmlLink"),
        }
    except Exception as e:
        return _err("calendar_actualizar_evento", e)


@mcp.tool()
def calendar_cancelar_evento(event_id: str, calendar_id: str = "primary"):
    """Cancela (borra) un evento avisando a los asistentes.

    Ejecuta events().delete con sendUpdates='all' para notificar la
    cancelación. Devuelve un indicador de resultado.
    """
    try:
        cal = _get_cal()
        cal.events().delete(
            calendarId=calendar_id,
            eventId=event_id,
            sendUpdates="all",
        ).execute()
        _audit(
            "calendar_cancelar_evento",
            f"calendar={calendar_id} event={event_id}",
        )
        return {"ok": True, "event_id": event_id}
    except Exception as e:
        return _err("calendar_cancelar_evento", e)


@mcp.tool()
def calendar_responder_invitacion(
    event_id: str,
    respuesta: str,
    calendar_id: str = "primary",
):
    """Responde a una invitación fijando el responseStatus del asistente.

    'respuesta' debe ser 'accepted', 'declined' o 'tentative'. Localiza en
    los asistentes el que tenga self=True (o el email del propio calendario
    vía getProfile) y hace patch de su responseStatus. Devuelve ok.
    """
    try:
        if respuesta not in ("accepted", "declined", "tentative"):
            raise ValueError(
                "respuesta debe ser 'accepted', 'declined' o 'tentative'"
            )
        cal = _get_cal()
        ev = cal.events().get(
            calendarId=calendar_id, eventId=event_id
        ).execute()
        attendees = ev.get("attendees", []) or []
        # Determinar el email propio.
        self_email = None
        for a in attendees:
            if a.get("self"):
                self_email = a.get("email")
                break
        if self_email is None:
            try:
                prof = cal.calendarList().get(
                    calendarId=calendar_id
                ).execute()
                self_email = prof.get("id")
            except Exception:
                self_email = calendar_id if calendar_id != "primary" else None
        encontrado = False
        for a in attendees:
            if a.get("self") or (
                self_email is not None and a.get("email") == self_email
            ):
                a["responseStatus"] = respuesta
                encontrado = True
                break
        if not encontrado:
            if self_email is None:
                raise ValueError(
                    "No se pudo identificar al asistente propio en el evento"
                )
            attendees.append(
                {"email": self_email, "responseStatus": respuesta}
            )
        ev2 = (
            cal.events()
            .patch(
                calendarId=calendar_id,
                eventId=event_id,
                body={"attendees": attendees},
                sendUpdates="all",
            )
            .execute()
        )
        _audit(
            "calendar_responder_invitacion",
            f"calendar={calendar_id} event={event_id} respuesta={respuesta}",
        )
        return {"ok": True, "event_id": ev2.get("id"), "respuesta": respuesta}
    except Exception as e:
        return _err("calendar_responder_invitacion", e)


@mcp.tool()
def calendar_anadir_asistentes(
    event_id: str,
    emails: list,
    calendar_id: str = "primary",
):
    """Añade asistentes nuevos a un evento y notifica el cambio.

    Obtiene el evento, agrega a la lista de asistentes los emails que aún no
    figuren, y hace patch con sendUpdates='all'. Devuelve la lista final de
    asistentes.
    """
    try:
        cal = _get_cal()
        ev = cal.events().get(
            calendarId=calendar_id, eventId=event_id
        ).execute()
        attendees = ev.get("attendees", []) or []
        existentes = {
            (a.get("email") or "").lower() for a in attendees
        }
        nuevos = []
        for em in emails:
            if em and em.lower() not in existentes:
                attendees.append({"email": em})
                existentes.add(em.lower())
                nuevos.append(em)
        ev2 = (
            cal.events()
            .patch(
                calendarId=calendar_id,
                eventId=event_id,
                body={"attendees": attendees},
                sendUpdates="all",
            )
            .execute()
        )
        _audit(
            "calendar_anadir_asistentes",
            f"calendar={calendar_id} event={event_id} nuevos={nuevos}",
        )
        return ev2.get("attendees", []) or []
    except Exception as e:
        return _err("calendar_anadir_asistentes", e)



# =========================================================
# BLOQUE: Slides + Tasks + Contactos (Google Workspace)
# Se APPENDEA a server.py (FastMCP). No redefine nada existente.
# =========================================================


def _get_slides():
    """Devuelve (y cachea) el servicio de Google Slides v1."""
    if not hasattr(_get_slides, "_svc"):
        _get_slides._svc = build(
            "slides", "v1", credentials=_build_creds(), cache_discovery=False
        )
    return _get_slides._svc


def _get_tasks():
    """Devuelve (y cachea) el servicio de Google Tasks v1."""
    if not hasattr(_get_tasks, "_svc"):
        _get_tasks._svc = build(
            "tasks", "v1", credentials=_build_creds(), cache_discovery=False
        )
    return _get_tasks._svc


def _get_people():
    """Devuelve (y cachea) el servicio de Google People v1."""
    if not hasattr(_get_people, "_svc"):
        _get_people._svc = build(
            "people", "v1", credentials=_build_creds(), cache_discovery=False
        )
    return _get_people._svc


# ------------------------- SLIDES -------------------------

@mcp.tool()
def slides_crear(titulo: str, diapositivas: Optional[list] = None) -> dict:
    """Crea una presentación de Google Slides.

    Args:
        titulo: Título de la presentación.
        diapositivas: Lista opcional de dicts {'titulo':.., 'cuerpo':..};
            por cada elemento se añade una diapositiva con layout
            TITLE_AND_BODY rellenando ambos placeholders.

    Devuelve el presentationId y la url de edición.
    """
    try:
        svc = _get_slides()
        pres = svc.presentations().create(body={"title": titulo}).execute()
        pid = pres.get("presentationId")
        if diapositivas:
            requests = []
            for idx, dia in enumerate(diapositivas):
                slide_id = "slide_%d" % idx
                title_id = "title_%d" % idx
                body_id = "body_%d" % idx
                requests.append({
                    "createSlide": {
                        "objectId": slide_id,
                        "slideLayoutReference": {
                            "predefinedLayout": "TITLE_AND_BODY"
                        },
                        "placeholderIdMappings": [
                            {
                                "layoutPlaceholder": {"type": "TITLE", "index": 0},
                                "objectId": title_id,
                            },
                            {
                                "layoutPlaceholder": {"type": "BODY", "index": 0},
                                "objectId": body_id,
                            },
                        ],
                    }
                })
                if dia.get("titulo"):
                    requests.append({
                        "insertText": {
                            "objectId": title_id,
                            "text": str(dia.get("titulo")),
                        }
                    })
                if dia.get("cuerpo"):
                    requests.append({
                        "insertText": {
                            "objectId": body_id,
                            "text": str(dia.get("cuerpo")),
                        }
                    })
            if requests:
                svc.presentations().batchUpdate(
                    presentationId=pid, body={"requests": requests}
                ).execute()
        url = "https://docs.google.com/presentation/d/%s/edit" % pid
        _audit("slides_crear", "%s - %s" % (pid, titulo))
        return {"presentationId": pid, "url": url}
    except Exception as e:
        return _err("slides_crear", e)


@mcp.tool()
def slides_leer(presentation_id: str, max_chars: int = 20000) -> dict:
    """Extrae el texto de una presentación de Google Slides.

    Recorre todas las diapositivas y sus elementos de forma (shapes),
    concatenando el contenido de shape.text.textElements[].textRun.content.

    Args:
        presentation_id: ID de la presentación.
        max_chars: Máximo de caracteres a devolver.

    Devuelve el texto extraído.
    """
    try:
        svc = _get_slides()
        pres = svc.presentations().get(presentationId=presentation_id).execute()
        partes = []
        for slide in pres.get("slides", []):
            for elem in slide.get("pageElements", []):
                shape = elem.get("shape")
                if not shape:
                    continue
                text = shape.get("text")
                if not text:
                    continue
                for te in text.get("textElements", []):
                    tr = te.get("textRun")
                    if tr and tr.get("content"):
                        partes.append(tr["content"])
        texto = "".join(partes)[:max_chars]
        return {"presentationId": presentation_id, "texto": texto}
    except Exception as e:
        return _err("slides_leer", e)


@mcp.tool()
def slides_anadir_diapositiva(
    presentation_id: str, titulo: str, cuerpo: str = ""
) -> dict:
    """Añade una diapositiva (layout TITLE_AND_BODY) a una presentación.

    Args:
        presentation_id: ID de la presentación.
        titulo: Texto para el placeholder de título.
        cuerpo: Texto opcional para el placeholder de cuerpo.

    Devuelve ok.
    """
    try:
        svc = _get_slides()
        suf = base64.urlsafe_b64encode(os.urandom(9)).decode().rstrip("=")
        slide_id = "slide_" + suf
        title_id = "title_" + suf
        body_id = "body_" + suf
        requests = [
            {
                "createSlide": {
                    "objectId": slide_id,
                    "slideLayoutReference": {
                        "predefinedLayout": "TITLE_AND_BODY"
                    },
                    "placeholderIdMappings": [
                        {
                            "layoutPlaceholder": {"type": "TITLE", "index": 0},
                            "objectId": title_id,
                        },
                        {
                            "layoutPlaceholder": {"type": "BODY", "index": 0},
                            "objectId": body_id,
                        },
                    ],
                }
            },
            {"insertText": {"objectId": title_id, "text": titulo}},
        ]
        if cuerpo:
            requests.append(
                {"insertText": {"objectId": body_id, "text": cuerpo}}
            )
        svc.presentations().batchUpdate(
            presentationId=presentation_id, body={"requests": requests}
        ).execute()
        _audit("slides_anadir_diapositiva", "%s - %s" % (presentation_id, titulo))
        return {"ok": True}
    except Exception as e:
        return _err("slides_anadir_diapositiva", e)


# ------------------------- TASKS --------------------------

@mcp.tool()
def tasks_listar(max_results: int = 50) -> dict:
    """Lista las tareas de la lista por defecto (@default).

    Args:
        max_results: Número máximo de tareas a devolver.

    Devuelve id, título, estado y vencimiento de cada tarea.
    """
    try:
        svc = _get_tasks()
        res = svc.tasks().list(
            tasklist="@default", maxResults=max_results
        ).execute()
        items = []
        for t in res.get("items", []):
            items.append({
                "id": t.get("id"),
                "titulo": t.get("title"),
                "estado": t.get("status"),
                "vencimiento": t.get("due"),
            })
        return {"tareas": items}
    except Exception as e:
        return _err("tasks_listar", e)


@mcp.tool()
def tasks_crear(titulo: str, fecha: Optional[str] = None, notas: str = "") -> dict:
    """Crea una tarea en la lista por defecto (@default).

    Args:
        titulo: Título de la tarea.
        fecha: Vencimiento en RFC3339; si llega solo 'AAAA-MM-DD' se
            convierte a 'AAAA-MM-DDT00:00:00.000Z'.
        notas: Notas opcionales.

    Devuelve el id de la tarea creada.
    """
    try:
        svc = _get_tasks()
        due = fecha
        if fecha and len(fecha) == 10 and "T" not in fecha:
            due = fecha + "T00:00:00.000Z"
        body = {"title": titulo, "notes": notas}
        if due:
            body["due"] = due
        t = svc.tasks().insert(tasklist="@default", body=body).execute()
        _audit("tasks_crear", "%s - %s" % (t.get("id"), titulo))
        return {"id": t.get("id")}
    except Exception as e:
        return _err("tasks_crear", e)


@mcp.tool()
def tasks_completar(task_id: str) -> dict:
    """Marca una tarea como completada (status='completed').

    Args:
        task_id: ID de la tarea.

    Devuelve ok.
    """
    try:
        svc = _get_tasks()
        svc.tasks().patch(
            tasklist="@default", task=task_id, body={"status": "completed"}
        ).execute()
        _audit("tasks_completar", task_id)
        return {"ok": True}
    except Exception as e:
        return _err("tasks_completar", e)


# ----------------------- CONTACTOS ------------------------

def _people_a_dict(p: dict) -> dict:
    """Normaliza una persona de People API a nombre/emails/teléfonos."""
    nombre = ""
    if p.get("names"):
        nombre = p["names"][0].get("displayName", "")
    emails = [e.get("value") for e in p.get("emailAddresses", []) if e.get("value")]
    telefonos = [t.get("value") for t in p.get("phoneNumbers", []) if t.get("value")]
    return {"nombre": nombre, "emails": emails, "telefonos": telefonos}


@mcp.tool()
def contactos_buscar(texto: str, max_results: int = 10) -> dict:
    """Busca contactos por texto (People API searchContacts).

    Args:
        texto: Consulta de búsqueda.
        max_results: Número máximo de resultados.

    Devuelve nombre, emails y teléfonos de cada contacto.
    """
    try:
        svc = _get_people()
        res = svc.people().searchContacts(
            query=texto,
            readMask="names,emailAddresses,phoneNumbers",
            pageSize=max_results,
        ).execute()
        contactos = [_people_a_dict(r.get("person", {})) for r in res.get("results", [])]
        return {"contactos": contactos}
    except Exception as e:
        return _err("contactos_buscar", e)


@mcp.tool()
def contactos_listar(max_results: int = 50) -> dict:
    """Lista los contactos del usuario (People API connections.list).

    Args:
        max_results: Número máximo de contactos.

    Devuelve la lista con nombre, emails y teléfonos.
    """
    try:
        svc = _get_people()
        res = svc.people().connections().list(
            resourceName="people/me",
            personFields="names,emailAddresses,phoneNumbers",
            pageSize=max_results,
        ).execute()
        contactos = [_people_a_dict(p) for p in res.get("connections", [])]
        return {"contactos": contactos}
    except Exception as e:
        return _err("contactos_listar", e)




# ============================================================
# BLOQUE: Unidades compartidas + PDF legal
# ============================================================


@mcp.tool()
def drive_unidades_compartidas() -> dict:
    """Lista las unidades compartidas (shared drives) accesibles.

    Devuelve el id y el nombre de cada unidad compartida.
    """
    try:
        service = _get_service()
        resp = service.drives().list(
            pageSize=100,
            fields="drives(id,name)",
        ).execute()
        unidades = [
            {"id": d.get("id"), "nombre": d.get("name")}
            for d in resp.get("drives", [])
        ]
        return {"ok": True, "total": len(unidades), "unidades": unidades}
    except Exception as e:
        return _err("drive_unidades_compartidas", e)


def _pdf_descargar_bytes(file_id: str) -> "io.BytesIO":
    """Descarga un archivo de Drive a un io.BytesIO (uso interno)."""
    service = _get_service()
    buffer = io.BytesIO()
    request = service.files().get_media(fileId=file_id)
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buffer.seek(0)
    return buffer


def _pdf_subir_bytes(buffer: "io.BytesIO", nombre_salida: str, parent_id: str) -> str:
    """Sube un io.BytesIO como PDF a Drive y devuelve el id (uso interno)."""
    service = _get_service()
    buffer.seek(0)
    metadata = {"name": nombre_salida}
    if parent_id:
        metadata["parents"] = [parent_id]
    media = MediaIoBaseUpload(buffer, mimetype="application/pdf", resumable=True)
    creado = service.files().create(
        body=metadata,
        media_body=media,
        fields=FILE_FIELDS,
        supportsAllDrives=True,
    ).execute()
    return creado.get("id")


@mcp.tool()
def drive_pdf_unir(file_ids: list, nombre_salida: str, parent_id: str = "root") -> dict:
    """Une varios PDF de Drive en uno solo y lo sube.

    Descarga cada PDF indicado en 'file_ids', los concatena con pypdf
    en el orden dado y sube el PDF resultante a 'parent_id'.
    Devuelve el id del PDF creado.
    """
    try:
        from pypdf import PdfWriter

        if not file_ids:
            return _err("drive_pdf_unir", ValueError("file_ids vacio"))

        writer = PdfWriter()
        for fid in file_ids:
            origen = _pdf_descargar_bytes(fid)
            writer.append(origen)

        salida = io.BytesIO()
        writer.write(salida)
        writer.close()

        nuevo_id = _pdf_subir_bytes(salida, nombre_salida, parent_id)
        _audit(
            "drive_pdf_unir",
            f"unidos {len(file_ids)} PDF en '{nombre_salida}' (id={nuevo_id})",
        )
        return {"ok": True, "id": nuevo_id, "nombre": nombre_salida}
    except Exception as e:
        return _err("drive_pdf_unir", e)


@mcp.tool()
def drive_pdf_marca_agua(
    file_id: str,
    texto: str,
    nombre_salida: str,
    parent_id: str = "root",
) -> dict:
    """Aplica una marca de agua de texto en diagonal a un PDF y lo sube.

    Genera con reportlab una pagina con 'texto' en diagonal, en gris
    translucido y centrado, y la superpone sobre CADA pagina del PDF
    original. Sube el resultado a 'parent_id'. Devuelve el id creado.
    """
    try:
        from pypdf import PdfReader, PdfWriter
        from reportlab.pdfgen import canvas
        from reportlab.lib.colors import Color

        origen = _pdf_descargar_bytes(file_id)
        lector = PdfReader(origen)
        escritor = PdfWriter()

        for pagina in lector.pages:
            ancho = float(pagina.mediabox.width)
            alto = float(pagina.mediabox.height)

            overlay_buf = io.BytesIO()
            c = canvas.Canvas(overlay_buf, pagesize=(ancho, alto))
            c.saveState()
            c.setFont("Helvetica-Bold", 60)
            c.setFillColor(Color(0.5, 0.5, 0.5, alpha=0.3))
            c.translate(ancho / 2.0, alto / 2.0)
            c.rotate(45)
            c.drawCentredString(0, 0, texto)
            c.restoreState()
            c.save()
            overlay_buf.seek(0)

            overlay_pagina = PdfReader(overlay_buf).pages[0]
            pagina.merge_page(overlay_pagina)
            escritor.add_page(pagina)

        salida = io.BytesIO()
        escritor.write(salida)
        escritor.close()

        nuevo_id = _pdf_subir_bytes(salida, nombre_salida, parent_id)
        _audit(
            "drive_pdf_marca_agua",
            f"marca de agua '{texto}' sobre {file_id} -> '{nombre_salida}' (id={nuevo_id})",
        )
        return {"ok": True, "id": nuevo_id, "nombre": nombre_salida}
    except Exception as e:
        return _err("drive_pdf_marca_agua", e)


@mcp.tool()
def drive_pdf_foliar(
    file_id: str,
    nombre_salida: str,
    parent_id: str = "root",
    prefijo: str = "",
) -> dict:
    """Anade foliado tipo Bates (numeracion de paginas) a un PDF y lo sube.

    Superpone en cada pagina, abajo a la derecha, un sello con el formato
    f"{prefijo}{n:04d}" mediante un overlay de reportlab del tamano de la
    pagina. Sube el resultado a 'parent_id'. Devuelve el id creado.
    """
    try:
        from pypdf import PdfReader, PdfWriter
        from reportlab.pdfgen import canvas

        origen = _pdf_descargar_bytes(file_id)
        lector = PdfReader(origen)
        escritor = PdfWriter()

        for indice, pagina in enumerate(lector.pages, start=1):
            ancho = float(pagina.mediabox.width)
            alto = float(pagina.mediabox.height)
            sello = f"{prefijo}{indice:04d}"

            overlay_buf = io.BytesIO()
            c = canvas.Canvas(overlay_buf, pagesize=(ancho, alto))
            c.setFont("Helvetica", 9)
            c.setFillColorRGB(0, 0, 0)
            c.drawRightString(ancho - 36, 24, sello)
            c.save()
            overlay_buf.seek(0)

            overlay_pagina = PdfReader(overlay_buf).pages[0]
            pagina.merge_page(overlay_pagina)
            escritor.add_page(pagina)

        salida = io.BytesIO()
        escritor.write(salida)
        escritor.close()

        nuevo_id = _pdf_subir_bytes(salida, nombre_salida, parent_id)
        _audit(
            "drive_pdf_foliar",
            f"foliado '{prefijo}' sobre {file_id} -> '{nombre_salida}' (id={nuevo_id})",
        )
        return {"ok": True, "id": nuevo_id, "nombre": nombre_salida}
    except Exception as e:
        return _err("drive_pdf_foliar", e)


# ============================================================================
# BLOQUE: Cálculo de PLAZOS PROCESALES (jurisdicción española)
# ----------------------------------------------------------------------------
# Python puro con datetime. Se APPENDEA a server.py (FastMCP).
# Requiere ya definidos: mcp, Optional (typing) y el helper _err(action, e).
# NOTA: los festivos AUTONÓMICOS y LOCALES no se incluyen aquí; pueden
#       pasarse mediante el parámetro 'festivos_extra' (lista de 'AAAA-MM-DD').
# ADVERTENCIA: herramienta de ayuda; no sustituye la verificación por un
#              profesional del cómputo aplicable a cada procedimiento.
# ============================================================================

# Festivos NACIONALES de España para 2025 y 2026 (fechas fijas + Viernes Santo).
FESTIVOS_NACIONALES_ES = [
    # 2025
    "2025-01-01",  # Año Nuevo
    "2025-01-06",  # Epifanía / Reyes
    "2025-04-18",  # Viernes Santo
    "2025-05-01",  # Fiesta del Trabajo
    "2025-08-15",  # Asunción de la Virgen
    "2025-10-12",  # Fiesta Nacional de España
    "2025-11-01",  # Todos los Santos
    "2025-12-06",  # Día de la Constitución
    "2025-12-08",  # Inmaculada Concepción
    "2025-12-25",  # Natividad del Señor
    # 2026
    "2026-01-01",  # Año Nuevo
    "2026-01-06",  # Epifanía / Reyes
    "2026-04-03",  # Viernes Santo
    "2026-05-01",  # Fiesta del Trabajo
    "2026-08-15",  # Asunción de la Virgen
    "2026-10-12",  # Fiesta Nacional de España
    "2026-11-01",  # Todos los Santos
    "2026-12-06",  # Día de la Constitución
    "2026-12-08",  # Inmaculada Concepción
    "2026-12-25",  # Natividad del Señor
]

# Nombres de los días de la semana en español (0 = lunes ... 6 = domingo).
_DIAS_SEMANA_ES = [
    "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo",
]


def _es_dia_inhabil(d, sabado_inhabil, agosto_inhabil, festivos):
    """Devuelve True si la fecha 'd' (datetime.date) es día inhábil."""
    wd = d.weekday()  # lunes=0 ... domingo=6
    if wd == 6:  # el domingo es SIEMPRE inhábil
        return True
    if sabado_inhabil and wd == 5:  # sábado inhábil si procede
        return True
    if agosto_inhabil and d.month == 8:  # todo agosto (plazos procesales)
        return True
    if d.isoformat() in festivos:  # festivos nacionales + extra
        return True
    return False


def _construir_festivos(festivos_extra):
    """Combina los festivos nacionales con los extra recibidos (o None)."""
    festivos = set(FESTIVOS_NACIONALES_ES)
    if festivos_extra:
        festivos.update(str(f) for f in festivos_extra)
    return festivos


@mcp.tool()
def plazo_dias_habiles(
    fecha_inicio: str,
    dias: int,
    sabado_inhabil: bool = True,
    agosto_inhabil: bool = False,
    festivos_extra: Optional[list] = None,
):
    """Suma días HÁBILES a una fecha para obtener el vencimiento del plazo.

    Partiendo de 'fecha_inicio' (AAAA-MM-DD) suma 'dias' días hábiles: salta
    siempre los domingos; los sábados si 'sabado_inhabil'; los festivos
    nacionales más 'festivos_extra'; y todo el mes de agosto si
    'agosto_inhabil' (propio de plazos procesales). Devuelve la fecha de
    vencimiento (AAAA-MM-DD), el día de la semana y los días naturales
    transcurridos.
    """
    try:
        import datetime
        festivos = _construir_festivos(festivos_extra)
        inicio = datetime.date.fromisoformat(fecha_inicio)
        actual = inicio
        contados = 0
        while contados < dias:
            actual = actual + datetime.timedelta(days=1)
            if not _es_dia_inhabil(actual, sabado_inhabil, agosto_inhabil, festivos):
                contados += 1
        return {
            "fecha_inicio": inicio.isoformat(),
            "dias_habiles_sumados": dias,
            "fecha_vencimiento": actual.isoformat(),
            "dia_semana": _DIAS_SEMANA_ES[actual.weekday()],
            "dias_naturales_transcurridos": (actual - inicio).days,
            "sabado_inhabil": sabado_inhabil,
            "agosto_inhabil": agosto_inhabil,
        }
    except Exception as e:
        return _err("plazo_dias_habiles", e)


@mcp.tool()
def plazo_vencimiento_notificacion(
    fecha_notificacion: str,
    dias_habiles: int,
    agosto_inhabil: bool = False,
    festivos_extra: Optional[list] = None,
):
    """Calcula el vencimiento de un plazo desde la notificación.

    Atajo que aplica la regla habitual: el cómputo empieza el día siguiente
    al de la notificación (AAAA-MM-DD). Los sábados se consideran inhábiles.
    Devuelve la fecha de vencimiento, el día de la semana y una nota
    recordando que es una ayuda y no sustituye la verificación del
    profesional para cada procedimiento concreto.
    """
    try:
        import datetime
        festivos = _construir_festivos(festivos_extra)
        inicio = datetime.date.fromisoformat(fecha_notificacion)
        actual = inicio
        contados = 0
        while contados < dias_habiles:
            actual = actual + datetime.timedelta(days=1)
            if not _es_dia_inhabil(actual, True, agosto_inhabil, festivos):
                contados += 1
        return {
            "fecha_notificacion": inicio.isoformat(),
            "dias_habiles": dias_habiles,
            "fecha_vencimiento": actual.isoformat(),
            "dia_semana": _DIAS_SEMANA_ES[actual.weekday()],
            "nota": (
                "El cómputo se inicia el día siguiente al de la notificación. "
                "Herramienta de ayuda: no sustituye la verificación del cómputo "
                "por el profesional según el procedimiento aplicable."
            ),
        }
    except Exception as e:
        return _err("plazo_vencimiento_notificacion", e)


@mcp.tool()
def dias_habiles_entre(
    fecha_a: str,
    fecha_b: str,
    sabado_inhabil: bool = True,
    agosto_inhabil: bool = False,
    festivos_extra: Optional[list] = None,
):
    """Cuenta los días HÁBILES entre dos fechas (AAAA-MM-DD).

    Cuenta los días hábiles del intervalo abierto por la izquierda y cerrado
    por la derecha (desde el día siguiente a la fecha menor hasta la fecha
    mayor, ambos según los criterios de inhabilidad). Aplica los mismos
    criterios que las demás herramientas (domingos, sábados si procede,
    festivos nacionales + extra y agosto si procede). El orden de las fechas
    es indiferente.
    """
    try:
        import datetime
        festivos = _construir_festivos(festivos_extra)
        d1 = datetime.date.fromisoformat(fecha_a)
        d2 = datetime.date.fromisoformat(fecha_b)
        inicio, fin = (d1, d2) if d1 <= d2 else (d2, d1)
        contados = 0
        actual = inicio
        while actual < fin:
            actual = actual + datetime.timedelta(days=1)
            if not _es_dia_inhabil(actual, sabado_inhabil, agosto_inhabil, festivos):
                contados += 1
        return {
            "fecha_a": inicio.isoformat(),
            "fecha_b": fin.isoformat(),
            "dias_habiles": contados,
            "dias_naturales": (fin - inicio).days,
            "sabado_inhabil": sabado_inhabil,
            "agosto_inhabil": agosto_inhabil,
        }
    except Exception as e:
        return _err("dias_habiles_entre", e)



# --------------------------------------------------------------------------- #
# BLOQUE PREMIUM: Doc-merge por lotes + comandos combinados (360, adjuntos, Meet)
# --------------------------------------------------------------------------- #

_DOC_MIME = "application/vnd.google-apps.document"


@mcp.tool()
def drive_generar_desde_plantilla_lote(template_id: str, filas: list,
                                       parent_id: str = "root",
                                       patron_nombre: str = "Documento") -> dict:
    """Genera un Documento de Google por cada fila a partir de una plantilla (mail-merge).

    Para CADA dict de `filas`: copia el Documento `template_id`, nombra la copia con
    `patron_nombre` formateado con la fila (p.ej. 'Contrato {nombre}' -> patron_nombre.format(**fila)),
    reemplaza en la copia cada marcador '{{clave}}' por su valor mediante replaceAllText,
    y mueve la copia a `parent_id`. Devuelve la lista de documentos creados (id, nombre).

    `filas` es una lista de dicts, p.ej.:
        [{"nombre": "Ana", "puesto": "Analista"}, {"nombre": "Luis", "puesto": "Jefe"}]
    """
    try:
        drv = _get_service()
        docs = _get_docs()
        creados = []
        for fila in filas:
            try:
                nombre = patron_nombre.format(**fila)
            except Exception:
                nombre = patron_nombre
            copia = drv.files().copy(
                fileId=template_id,
                body={"name": nombre},
                fields="id, name, parents",
                supportsAllDrives=True,
            ).execute()
            copia_id = copia["id"]

            requests = []
            for clave, valor in fila.items():
                requests.append({
                    "replaceAllText": {
                        "containsText": {"text": "{{" + str(clave) + "}}", "matchCase": True},
                        "replaceText": str(valor),
                    }
                })
            if requests:
                docs.documents().batchUpdate(
                    documentId=copia_id,
                    body={"requests": requests},
                ).execute()

            prev = ",".join(copia.get("parents", []))
            drv.files().update(
                fileId=copia_id,
                addParents=parent_id,
                removeParents=prev,
                fields="id, parents",
                supportsAllDrives=True,
            ).execute()

            _audit("drive_generar_desde_plantilla_lote",
                   {"template_id": template_id, "doc_id": copia_id, "nombre": nombre,
                    "parent_id": parent_id})
            creados.append({"id": copia_id, "nombre": nombre})
        return {"ok": True, "generados": len(creados), "documentos": creados}
    except Exception as e:
        return _err("drive_generar_desde_plantilla_lote", e)


@mcp.tool()
def cliente_360(nombre_cliente: str, max_items: int = 10) -> dict:
    """Vista 360 de un cliente: reune en un solo golpe su rastro en Drive, Gmail y Calendar.

    Devuelve un dict con tres listas resumidas:
      - `drive`: archivos/carpetas cuyo nombre contiene `nombre_cliente`.
      - `correos`: correos recientes de/para el cliente (From, Subject, Date).
      - `eventos`: proximos eventos de calendario que lo mencionan.
    """
    try:
        tope = max(1, min(max_items, 50))
        resultado = {"cliente": nombre_cliente, "drive": [], "correos": [], "eventos": []}

        # (a) Drive: nombre contiene el cliente
        try:
            drv = _get_service()
            safe = nombre_cliente.replace("'", "\\'")
            q = "name contains '{}' and trashed = false".format(safe)
            res = drv.files().list(q=q, spaces="drive", fields="files({})".format(FILE_FIELDS),
                                   pageSize=tope, supportsAllDrives=True,
                                   includeItemsFromAllDrives=True).execute()
            resultado["drive"] = [_file(f) for f in res.get("files", [])]
        except Exception as e:
            resultado["drive_error"] = str(e)

        # (b) Gmail: correos donde aparezca el cliente
        try:
            gm = _get_gmail()
            gq = 'from:{0} OR to:{0} OR "{0}"'.format(nombre_cliente)
            lst = gm.users().messages().list(userId="me", q=gq, maxResults=tope).execute()
            for m in lst.get("messages", []):
                full = gm.users().messages().get(
                    userId="me", id=m["id"], format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"]).execute()
                h = {x["name"]: x["value"] for x in full.get("payload", {}).get("headers", [])}
                resultado["correos"].append({
                    "id": m["id"], "from": h.get("From"), "to": h.get("To"),
                    "subject": h.get("Subject"), "date": h.get("Date"),
                    "snippet": full.get("snippet", "")[:160]})
        except Exception as e:
            resultado["correos_error"] = str(e)

        # (c) Calendar: proximos eventos que lo mencionan
        try:
            import datetime as _dt
            cal = _get_cal()
            ahora = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            ev = cal.events().list(calendarId="primary", q=nombre_cliente, timeMin=ahora,
                                   singleEvents=True, orderBy="startTime",
                                   maxResults=tope).execute()
            for e in ev.get("items", []):
                st = e.get("start") or {}
                resultado["eventos"].append({
                    "id": e.get("id"), "summary": e.get("summary"),
                    "inicio": st.get("dateTime") or st.get("date"),
                    "link": e.get("htmlLink")})
        except Exception as e:
            resultado["eventos_error"] = str(e)

        resultado["ok"] = True
        return resultado
    except Exception as e:
        return _err("cliente_360", e)


@mcp.tool()
def archivar_adjuntos_recientes(dias: int = 7, parent_id: str = "root") -> dict:
    """Archiva en Drive todos los adjuntos de los correos recientes con adjunto.

    Busca en Gmail 'has:attachment newer_than:{dias}d', descarga cada adjunto y lo sube
    a la carpeta `parent_id` de Drive. Devuelve la lista de adjuntos guardados (nombre, drive_id).
    """
    try:
        gm = _get_gmail()
        drv = _get_service()

        def _walk(payload):
            pila = [payload]
            while pila:
                p = pila.pop()
                for c in (p.get("parts") or []):
                    pila.append(c)
                yield p

        query = "has:attachment newer_than:{}d".format(max(1, dias))
        lst = gm.users().messages().list(userId="me", q=query, maxResults=100).execute()
        guardados = []
        for m in lst.get("messages", []):
            full = gm.users().messages().get(userId="me", id=m["id"], format="full").execute()
            for p in _walk(full.get("payload", {})):
                fn = p.get("filename")
                body = p.get("body", {})
                if fn and body.get("attachmentId"):
                    att = gm.users().messages().attachments().get(
                        userId="me", messageId=m["id"], id=body["attachmentId"]).execute()
                    data = base64.urlsafe_b64decode(att["data"])
                    media = MediaIoBaseUpload(io.BytesIO(data),
                                              mimetype="application/octet-stream",
                                              resumable=False)
                    r = drv.files().create(body={"name": fn, "parents": [parent_id]},
                                           media_body=media, fields="id, name",
                                           supportsAllDrives=True).execute()
                    _audit("archivar_adjuntos_recientes",
                           {"message_id": m["id"], "file": fn, "drive_id": r["id"],
                            "parent_id": parent_id})
                    guardados.append({"nombre": fn, "drive_id": r["id"]})
        return {"ok": True, "guardados": len(guardados), "adjuntos": guardados}
    except Exception as e:
        return _err("archivar_adjuntos_recientes", e)


@mcp.tool()
def agendar_reunion_cliente(summary: str, start: str, end: str, emails: list,
                            descripcion: str = "", calendar_id: str = "primary") -> dict:
    """Agenda una reunion con enlace de Google Meet e invita a los asistentes.

    Crea un evento con videollamada de Google Meet (conferenceData/hangoutsMeet) e invita
    a `emails` enviando la invitacion (sendUpdates='all'). start/end en RFC3339 con zona
    (2026-08-05T10:00:00+01:00). Devuelve el id del evento y el enlace de Meet.
    """
    try:
        import uuid as _uuid
        cal = _get_cal()
        body = {
            "summary": summary,
            "description": descripcion,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
            "attendees": [{"email": a} for a in (emails or [])],
            "conferenceData": {
                "createRequest": {
                    "requestId": str(_uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }
        e = cal.events().insert(calendarId=calendar_id, body=body,
                                conferenceDataVersion=1, sendUpdates="all").execute()
        _audit("agendar_reunion_cliente",
               {"summary": summary, "start": start, "event_id": e.get("id"),
                "emails": list(emails or [])})
        return {"ok": True, "id": e.get("id"), "meet": e.get("hangoutLink"),
                "link": e.get("htmlLink")}
    except Exception as e:
        return _err("agendar_reunion_cliente", e)


# --------------------------------------------------------------------------- #
# Arranque
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    _t = os.environ.get("MCP_TRANSPORT", "stdio")
    if _t == "streamable-http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
