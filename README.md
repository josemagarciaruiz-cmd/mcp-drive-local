# MCP Drive — instalación local (conector completo + puente de disco)

Instala en el ordenador de una persona DOS conectores para Claude, funcionando
sobre SU propio Google Drive con SUS credenciales:

- **mcp-drive**  — conector completo: 45 comandos (mover, renombrar, copiar, crear
  carpetas, editar Documentos de Google, leer PDF/Word/Excel/PowerPoint, papelera,
  permisos, remediación de compartidos, auditoría, etc.).
- **agente-drive** — puente entre el disco físico del equipo y Drive (subir/bajar).

## Instalación

Con Claude CERRADO:

- **Mac/Linux:** `./instalar.sh`
- **Windows:** doble clic en `INSTALAR_WINDOWS.bat`

El instalador prepara el entorno, genera el token de Google de la persona (se abre
el navegador para que pulse "Permitir") y registra los dos conectores. Necesita el
`client_secret_*.json` (tipo *Aplicación de escritorio*) en esta carpeta o en
Descargas.

Requisitos: Python 3 y Git instalados.

Los secretos (`.env`, `client_secret_*.json`) nunca se suben a git.
El protocolo de instalación paso a paso está en el Drive del proyecto.
