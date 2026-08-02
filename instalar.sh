#!/usr/bin/env bash
# Instalador del conector MCP Drive COMPLETO (para el Drive de quien lo instala).
# Ejecutar con Claude CERRADO:  ./instalar.sh
set -e
cd "$(dirname "$0")"

echo "==> 1/3  Entorno y dependencias..."
python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ] || ! grep -q '^GOOGLE_REFRESH_TOKEN=.\+' .env 2>/dev/null; then
  echo
  echo "==> 2/3  Generando TU token de Google."
  echo "         Se abrira el navegador: inicia sesion con TU cuenta y pulsa Permitir."
  echo "         (Si sale 'app no verificada': Configuracion avanzada -> continuar.)"
  ./.venv/bin/python get_refresh_token.py
  grep -q '^ALLOWED_DIRS=' .env 2>/dev/null || echo 'ALLOWED_DIRS=~' >> .env
else
  echo "==> 2/3  Ya hay token en .env, lo reutilizo."
fi

echo
echo "==> 3/3  Registrando los conectores en Claude..."
./.venv/bin/python conectar_claude.py

echo
echo "LISTO. Abre Claude. Tendras dos conectores: 'mcp-drive' (completo) y 'agente-drive'."
