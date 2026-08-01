#!/usr/bin/env bash
# Instalador unico del Agente Drive (puente disco <-> Drive).
# Uso, con Claude CERRADO:  ./instalar.sh
# Hace: entorno + dependencias, crea el .env (pregunta las 3 claves si falta) y
# da de alta el conector 'agente-drive' en Claude.
set -e
cd "$(dirname "$0")"

echo "==> 1/3  Preparando entorno y dependencias..."
python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  echo
  echo "==> 2/3  No hay .env. Pega tus credenciales de Google (las mismas de casa):"
  read -r -p "   GOOGLE_CLIENT_ID: " CID
  read -r -p "   GOOGLE_CLIENT_SECRET: " CSEC
  read -r -p "   GOOGLE_REFRESH_TOKEN: " RTOK
  cat > .env <<ENV
GOOGLE_CLIENT_ID=$CID
GOOGLE_CLIENT_SECRET=$CSEC
GOOGLE_REFRESH_TOKEN=$RTOK
ALLOWED_DIRS=\$HOME
AGENT_HOST=127.0.0.1
AGENT_PORT=8765
ENV
  echo "   .env creado (acceso amplio: toda tu carpeta de usuario)."
else
  echo "==> 2/3  Ya existe .env, lo reutilizo."
fi

echo
echo "==> 3/3  Dando de alta el conector en Claude..."
python3 conectar_claude.py || {
  echo
  echo "   (Si te ha avisado de que Claude esta abierto: cierralo con Cmd+Q,"
  echo "    espera 3 segundos y ejecuta de nuevo:  ./conectar_claude.sh )"
  exit 0
}
echo
echo "LISTO. Abre Claude y pide: 'lista mis carpetas del Agente Drive'."
