#!/usr/bin/env bash
# Da de alta el conector 'agente-drive' en Claude Desktop (modo stdio).
# Ejecutar tras haber hecho ./install.sh y tener el .env relleno.
set -e
cd "$(dirname "$0")"
python3 conectar_claude.py
