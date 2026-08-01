@echo off
title Instalar Agente Drive
echo ============================================================
echo   INSTALADOR DEL AGENTE DRIVE (Windows)
echo ============================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo FALTA Python. Instalalo desde https://www.python.org/downloads/
  echo   IMPORTANTE: marca la casilla "Add python.exe to PATH".
  echo Luego vuelve a ejecutar este archivo.
  echo.
  pause
  exit /b
)
where git >nul 2>&1
if errorlevel 1 (
  echo FALTA Git. Instalalo desde https://git-scm.com/download/win
  echo Luego vuelve a ejecutar este archivo.
  echo.
  pause
  exit /b
)

if not exist mcp-drive-local (
  echo Descargando el proyecto...
  git clone https://github.com/josemagarciaruiz-cmd/mcp-drive-local
)
cd mcp-drive-local

echo.
echo Lanzando el instalador. Te pedira 3 claves de Google (copialas del
echo documento CREDENCIALES_GOOGLE de tu Drive). Claude se cerrara solo.
echo.
powershell -ExecutionPolicy Bypass -File .\instalar.ps1

echo.
echo ============================================================
echo   LISTO. Abre Claude y pide: "lista mis carpetas del Agente Drive".
echo ============================================================
pause
