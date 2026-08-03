# Instalador SOLO del AGENTE DRIVE para Windows (PowerShell).
# Registra unicamente 'agente-drive' (el puente disco<->Drive). NO toca el conector,
# que ya usas por URL. Ejecutar con Claude CERRADO (el .bat lo cierra por ti).
Set-Location -Path $PSScriptRoot
$log = Join-Path $PSScriptRoot "install_log.txt"
try { Start-Transcript -Path $log -Force | Out-Null } catch {}

function Fail($msg) {
    Write-Host ""
    Write-Host "============================================================"
    Write-Host "  NO SE PUDO INSTALAR EL AGENTE"
    Write-Host "  $msg"
    Write-Host ""
    Write-Host "  Registro completo en: $log"
    Write-Host "  Sube ese 'install_log.txt' a Drive y me lo dices."
    Write-Host "============================================================"
    try { Stop-Transcript | Out-Null } catch {}
    Read-Host "Pulsa ENTER para cerrar"
    exit 1
}

Write-Host "==> 1/3  Entorno y dependencias del agente..."
python -m venv .venv
if ($LASTEXITCODE -ne 0) { Fail "No se pudo crear el entorno de Python (.venv). Revisa que Python este instalado y marcado 'Add python.exe to PATH'." }

$py  = ".\.venv\Scripts\python.exe"
$pip = ".\.venv\Scripts\pip.exe"

if (Test-Path "wheels") {
    Write-Host "    Instalando librerias incluidas en el paquete (sin necesidad de internet)..."
    & $pip install --no-index --find-links wheels -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Fail "No se pudieron instalar las librerias incluidas (paso 1/3). Revisa install_log.txt." }
} else {
    & $py -m pip install --upgrade pip
    Write-Host "    Descargando librerias..."
    & $pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    Reintento en modo compatible con proxy/antivirus..."
        & $pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.python.org -r requirements.txt
    }
    if ($LASTEXITCODE -ne 0) {
        Fail "No se pudieron descargar las dependencias (paso 1/3). Red de la oficina (proxy/cortafuegos/antivirus) o falta de internet. Prueba con el punto de acceso del movil."
    }
}
& $py -c "import google_auth_oauthlib, googleapiclient, mcp" 2>$null
if ($LASTEXITCODE -ne 0) { Fail "Las dependencias no quedaron completas (paso 1/3). Revisa install_log.txt." }

$hasToken = (Test-Path ".env") -and (Select-String -Path ".env" -Pattern '^GOOGLE_REFRESH_TOKEN=.+' -Quiet)
if (-not $hasToken) {
    Write-Host ""
    Write-Host "==> 2/3  Generando TU token de Google (se abrira el navegador)."
    Write-Host "         Inicia sesion con TU cuenta y pulsa Permitir."
    Write-Host "         (Si sale 'app no verificada': Configuracion avanzada -> continuar.)"
    & $py get_refresh_token.py
    if ($LASTEXITCODE -ne 0) { Fail "No se pudo autorizar con Google (paso 2/3). Comprueba que el client_secret_*.json este DENTRO de esta carpeta, o copia aqui tu .env ya existente. Detalle en install_log.txt." }
    if (-not (Select-String -Path ".env" -Pattern '^ALLOWED_DIRS=' -Quiet)) {
        Add-Content -Path .env -Value "ALLOWED_DIRS=~"
    }
} else {
    Write-Host "==> 2/3  Ya hay token en .env, lo reutilizo (no hace falta navegador)."
}

Write-Host ""
Write-Host "==> 3/3  Cerrando Claude y registrando SOLO el agente..."
taskkill /IM Claude.exe /F /T 2>$null | Out-Null
Start-Sleep -Seconds 3
& $py conectar_claude.py --solo-agente
if ($LASTEXITCODE -ne 0) { Fail "No se pudo registrar el agente (paso 3/3). Detalle en install_log.txt." }

Write-Host ""
Write-Host "LISTO. Abre Claude. Tendras 'agente-drive' (el conector por URL sigue igual)."
try { Stop-Transcript | Out-Null } catch {}
