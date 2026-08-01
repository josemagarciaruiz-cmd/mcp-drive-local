# Instalador del Agente Drive para Windows (PowerShell).
# Uso, con Claude CERRADO, dentro de la carpeta del proyecto:
#   powershell -ExecutionPolicy Bypass -File .\instalar.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "==> 1/3  Entorno y dependencias..."
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -q --upgrade pip
& .\.venv\Scripts\pip.exe install -q -r requirements.txt

if (-not (Test-Path ".env")) {
    Write-Host ""
    Write-Host "==> 2/3  Pega tus credenciales de Google (las mismas de casa):"
    $cid  = Read-Host "   GOOGLE_CLIENT_ID"
    $csec = Read-Host "   GOOGLE_CLIENT_SECRET"
    $rtok = Read-Host "   GOOGLE_REFRESH_TOKEN"
    @(
        "GOOGLE_CLIENT_ID=$cid",
        "GOOGLE_CLIENT_SECRET=$csec",
        "GOOGLE_REFRESH_TOKEN=$rtok",
        "ALLOWED_DIRS=~",
        "AGENT_HOST=127.0.0.1",
        "AGENT_PORT=8765"
    ) | Set-Content -Encoding UTF8 .env
    Write-Host "   .env creado (acceso amplio: toda tu carpeta de usuario)."
} else {
    Write-Host "==> 2/3  Ya existe .env, lo reutilizo."
}

Write-Host ""
Write-Host "==> 3/3  Dando de alta el conector en Claude..."
& .\.venv\Scripts\python.exe conectar_claude.py

Write-Host ""
Write-Host "Si te ha avisado de que Claude esta ABIERTO: cierralo del todo y vuelve a ejecutar."
Write-Host "Cuando termine sin avisos: abre Claude y pide 'lista mis carpetas del Agente Drive'."
