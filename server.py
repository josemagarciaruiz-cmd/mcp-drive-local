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

SCOPES = ["https://www.googleapis.com/auth/drive"]
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
        acciones = []
        for f in res.get("files", []):
            perms = svc.permissions().list(
                fileId=f["id"], fields="permissions(id, type, role)",
                supportsAllDrives=True).execute().get("permissions", [])
            for pm in perms:
                if pm.get("type") == "anyone":
                    if dry_run:
                        acciones.append({"file_id": f["id"], "name": f.get("name"),
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
        return {"ok": True, "dry_run": dry_run, "num": len(acciones), "acciones": acciones}
    except Exception as e:
        return _err("drive_remediar_externos", e)


# --------------------------------------------------------------------------- #
# Arranque
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    _t = os.environ.get("MCP_TRANSPORT", "stdio")
    if _t == "streamable-http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
