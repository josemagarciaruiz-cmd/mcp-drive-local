# Instalador del conector MCP Drive para Windows (PowerShell) - v2
# Ejecutar con Claude CERRADO (el .bat lo cierra por ti).
# Novedades v2: registro completo en install_log.txt, parada clara si falla la
# descarga de dependencias, y reintento compatible con proxy/antivirus (trusted-host).
Set-Location -Path $PSScriptRoot
$log = Join-Path $PSScriptRoot "install_log.txt"
try { Start-Transcript -Path $log -Force | Out-Null } catch {}

function Fail($msg) {
    Write-Host ""
    Write-Host "============================================================"
    Write-Host "  NO SE PUDO COMPLETAR LA INSTALACION"
    Write-Host "  $msg"
    Write-Host ""
    Write-Host "  Se ha guardado un registro completo en:"
    Write-Host "  $log"
    Write-Host "  Sube ese archivo 'install_log.txt' a Drive y me lo dices;"
    Write-Host "  con el sabremos el motivo exacto."
    Write-Host "============================================================"
    try { Stop-Transcript | Out-Null } catch {}
    Read-Host "Pulsa ENTER para cerrar"
    exit 1
}

Write-Host "==> 1/3  Entorno y dependencias..."
$PYARG=$null
foreach($v in @("3.13","3.12","3.11")){
  & py "-$v" -c "import sys" 2>$null
  if($LASTEXITCODE -eq 0){ $PYARG="-$v"; break }
}
if(-not $PYARG){ Fail "No hay una version de Python compatible instalada (hace falta 3.11, 3.12 o 3.13). Instala Python 3.13 desde https://www.python.org/downloads/ marcando 'Add python.exe to PATH' y repite." }
& py $PYARG -m venv .venv
if ($LASTEXITCODE -ne 0) { Fail "No se pudo crear el entorno de Python (.venv)." }

$py  = ".\.venv\Scripts\python.exe"
$pip = ".\.venv\Scripts\pip.exe"

& $py -m pip install --upgrade pip

Write-Host "    Descargando librerias (puede tardar 1-2 minutos)..."
& $pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "    Primer intento fallido. Reintento en modo compatible con proxy/antivirus..."
    & $pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.python.org -r requirements.txt
}
if ($LASTEXITCODE -ne 0) {
    Fail "No se pudieron descargar las dependencias (paso 1/3). Casi siempre es la RED de la oficina (proxy, cortafuegos o antivirus interceptando SSL) bloqueando pypi.org, o falta de internet. Prueba con OTRA red (por ejemplo el punto de acceso del movil) o pide a sistemas que permitan pypi.org y files.pythonhosted.org."
}

# Comprobacion real de que las librerias criticas quedaron instaladas
& $py -c "import google_auth_oauthlib, googleapiclient, mcp" 2>$null
if ($LASTEXITCODE -ne 0) { Fail "Las dependencias no quedaron completas (paso 1/3). Revisa install_log.txt: probablemente la descarga se corto a medias por la red." }

$hasToken = (Test-Path ".env") -and (Select-String -Path ".env" -Pattern '^GOOGLE_REFRESH_TOKEN=.+' -Quiet)
if (-not $hasToken) {
    Write-Host ""
    Write-Host "==> 2/3  Generando TU token de Google."
    Write-Host "         Se abrira el navegador: inicia sesion con TU cuenta y pulsa Permitir."
    Write-Host "         (Si sale 'app no verificada': Configuracion avanzada -> continuar.)"
    & $py get_refresh_token.py
    if ($LASTEXITCODE -ne 0) { Fail "No se pudo autorizar con Google (paso 2/3). Comprueba que el archivo client_secret_*.json este DENTRO de esta carpeta (o en Descargas) y que pulsaste Permitir. Detalle en install_log.txt." }
    if (-not (Select-String -Path ".env" -Pattern '^ALLOWED_DIRS=' -Quiet)) {
        Add-Content -Path .env -Value "ALLOWED_DIRS=~"
    }
} else {
    Write-Host "==> 2/3  Ya hay token en .env, lo reutilizo."
}

Write-Host ""
Write-Host "==> 3/3  Cerrando Claude y registrando los conectores..."
taskkill /IM Claude.exe /F /T 2>$null | Out-Null
Start-Sleep -Seconds 3
& $py conectar_claude.py
if ($LASTEXITCODE -ne 0) { Fail "No se pudieron registrar los conectores (paso 3/3). Detalle en install_log.txt." }

Write-Host ""
Write-Host "LISTO. Abre Claude. Tendras 'mcp-drive' (completo) y 'agente-drive'."
try { Stop-Transcript | Out-Null } catch {}
