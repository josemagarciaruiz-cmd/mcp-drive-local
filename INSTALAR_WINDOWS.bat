@echo off
title Instalar Conector MCP Drive
echo ============================================================
echo   INSTALADOR DEL CONECTOR MCP DRIVE (Windows)
echo ============================================================
echo.
where python >nul 2>&1
if errorlevel 1 (
  echo FALTA Python. Instalalo desde https://www.python.org/downloads/
  echo   IMPORTANTE: marca la casilla "Add python.exe to PATH".
  echo Luego vuelve a hacer doble clic en este archivo.
  echo.
  pause
  exit /b
)
if exist server.py goto INSTALL
where git >nul 2>&1
if errorlevel 1 (
  echo No encuentro el proyecto ni Git.
  echo Descomprime el ZIP y ejecuta este archivo DESDE DENTRO de la carpeta.
  echo.
  pause
  exit /b
)
if not exist mcp-drive-local git clone https://github.com/josemagarciaruiz-cmd/mcp-drive-local
cd mcp-drive-local
:INSTALL
echo Lanzando el instalador. Se abrira el navegador para autorizar con Google.
echo.
powershell -ExecutionPolicy Bypass -File .\instalar.ps1
echo.
echo ============================================================
echo   LISTO. Abre Claude y pide: "usa mcp-drive para listar mi unidad".
echo ============================================================
pause
