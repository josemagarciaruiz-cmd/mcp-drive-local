#!/usr/bin/env bash
# Instalador del mini-agente MCP Drive Local (macOS / Linux).
# Deja el agente corriendo en 127.0.0.1:8765 y lo arranca al iniciar sesión.
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "==> Creando entorno virtual e instalando dependencias..."
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

if [ ! -f .env ]; then
  echo "==> No hay .env. Copia .env.example a .env y rellena las credenciales antes de arrancar."
  cp .env.example .env
  echo "    Editado necesario: $DIR/.env"
fi

PORT="$(grep -E '^AGENT_PORT=' .env | cut -d= -f2 || echo 8765)"
PORT="${PORT:-8765}"

# Arranque persistente
OS="$(uname -s)"
if [ "$OS" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/ai.josemaria.mcpdrivelocal.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.josemaria.mcpdrivelocal</string>
  <key>ProgramArguments</key>
  <array><string>$DIR/.venv/bin/python</string><string>$DIR/agent.py</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/agent.log</string>
  <key>StandardErrorPath</key><string>$DIR/agent.log</string>
</dict></plist>
EOF
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  echo "==> Agente instalado como servicio (launchd) y arrancado."
else
  # Linux: systemd de usuario
  SVC="$HOME/.config/systemd/user/mcp-drive-local.service"
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$SVC" <<EOF
[Unit]
Description=MCP Drive Local
[Service]
ExecStart=$DIR/.venv/bin/python $DIR/agent.py
WorkingDirectory=$DIR
Restart=always
[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now mcp-drive-local
  echo "==> Agente instalado como servicio (systemd) y arrancado."
fi

echo ""
echo "LISTO. En Claude, añade este conector personalizado:"
echo "    http://127.0.0.1:${PORT}/mcp/"
