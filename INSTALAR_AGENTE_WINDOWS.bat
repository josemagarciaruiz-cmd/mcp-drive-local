@echo off
title Instalar SOLO el Agente Drive
echo ============================================================
echo   INSTALADOR DEL AGENTE DRIVE (solo el agente)
echo   El conector lo usas por URL: esto NO lo toca.
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
if not exist agent.py (
  echo No encuentro agent.py. Ejecuta este archivo DESDE DENTRO de la carpeta
  echo descomprimida (la que tiene agent.py e instalar_agente.ps1).
  echo.
  pause
  exit /b
)
echo Lanzando el instalador del agente...
echo.
powershell -ExecutionPolicy Bypass -File .\instalar_agente.ps1
if errorlevel 1 (
  echo.
  echo ============================================================
  echo   NO TERMINO. Revisa el mensaje de arriba y el archivo
  echo   install_log.txt que se ha creado en esta carpeta.
  echo ============================================================
  pause
  exit /b
)
echo.
echo ============================================================
echo   LISTO. Abre Claude y pide:
echo   "lista mis carpetas permitidas del Agente Drive".
echo ============================================================
pause
