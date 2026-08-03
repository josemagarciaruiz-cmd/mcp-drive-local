"""
Ayudante de un solo uso para obtener el refresh token de Google Drive
y escribirlo (junto al client_id/secret) en el archivo .env local.

El token NUNCA se imprime en pantalla: se guarda directamente en .env,
que está excluido de git. Requiere un OAuth Client de tipo *Desktop app*.

Uso:
    python get_refresh_token.py /ruta/al/client_secret.json

Si no pasas ruta, busca el más reciente en ~/Downloads.
"""

import glob
import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive",
          "https://www.googleapis.com/auth/gmail.modify",
          "https://www.googleapis.com/auth/gmail.send",
          "https://www.googleapis.com/auth/gmail.settings.basic",
          "https://www.googleapis.com/auth/calendar",
          "https://www.googleapis.com/auth/tasks",
          "https://www.googleapis.com/auth/contacts",
          "https://www.googleapis.com/auth/forms.body",
          "https://www.googleapis.com/auth/forms.responses.readonly"]
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def find_client_secret() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    here = os.path.dirname(os.path.abspath(__file__))
    matches = sorted(
        glob.glob(os.path.join(here, "client_secret_*.json")) +
        glob.glob(os.path.expanduser("~/Downloads/client_secret_*.json")),
        key=os.path.getmtime,
        reverse=True,
    )
    if not matches:
        sys.exit("No encuentro ningún client_secret_*.json en ~/Downloads. "
                 "Pásame la ruta como argumento.")
    return matches[0]


def main() -> None:
    cs = find_client_secret()
    print(f"Usando credenciales: {cs}")
    flow = InstalledAppFlow.from_client_secrets_file(cs, scopes=SCOPES)
    # Abre el navegador; el titular inicia sesión y consiente. La librería
    # captura el token por loopback: este script nunca lo muestra.
    creds = flow.run_local_server(port=0, prompt="consent")

    lines = {
        "GOOGLE_CLIENT_ID": creds.client_id,
        "GOOGLE_CLIENT_SECRET": creds.client_secret,
        "GOOGLE_REFRESH_TOKEN": creds.refresh_token,
        "MCP_HOST": "0.0.0.0",
        "MCP_PORT": "8000",
    }
    with open(ENV_PATH, "w") as fh:
        for k, v in lines.items():
            fh.write(f"{k}={v}\n")
    os.chmod(ENV_PATH, 0o600)
    print(f"\nListo. Credenciales escritas en {ENV_PATH} (permisos 600).")
    print("Ya puedes arrancar el servidor: python server.py")


if __name__ == "__main__":
    main()
