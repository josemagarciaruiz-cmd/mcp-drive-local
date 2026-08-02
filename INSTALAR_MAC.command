#!/bin/bash
cd "$(dirname "$0")"
echo "Instalando el conector MCP Drive..."
./instalar.sh
echo ""
echo "Puedes cerrar esta ventana. Abre Claude y prueba: 'usa mcp-drive para listar mi unidad'."
read -n 1 -s -r -p "Pulsa una tecla para salir..."
