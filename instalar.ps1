# Instalador del conector MCP Drive COMPLETO para Windows (PowerShell).
# Ejecutar con Claude CERRADO (el .bat lo cierra por ti).
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "==> 1/3  Entorno y dependencias..."
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -q --upgrade pip
& .\.venv\Scripts\pip.exe install -q -r requirements.txt

$hasToken = (Test-Path ".env") -and (Select-String -Path ".env" -Pattern '^GOOGLE_REFRESH_TOKEN=.+' -Quiet)
if (-not $hasToken) {
    Write-Host ""
    Write-Host "==> 2/3  Generando TU token de Google."
    Write-Host "         Se abrira el navegador: inicia sesion con TU cuenta y pulsa Permitir."
    Write-Host "         (Si sale 'app no verificada': Configuracion avanzada -> continuar.)"
    & .\.venv\Scripts\python.exe get_refresh_token.py
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
& .\.venv\Scripts\python.exe conectar_claude.py

Write-Host ""
Write-Host "LISTO. Abre Claude. Tendras 'mcp-drive' (completo) y 'agente-drive'."
