#!/usr/bin/env python3
"""
Da de alta (o actualiza) el conector 'agente-drive' en la configuracion de
Claude Desktop, en modo stdio, leyendo las credenciales del .env de esta carpeta.
Uso:  python3 conectar_claude.py   (o:  ./conectar_claude.sh)
No sube nada a internet; solo edita el fichero local de Claude, con copia previa.
"""
import json, os, sys, shutil, time, platform

HERE = os.path.dirname(os.path.realpath(__file__))
ENVP = os.path.join(HERE, ".env")

def config_path():
    home = os.path.expanduser("~")
    s = platform.system()
    if s == "Darwin":
        return os.path.join(home, "Library/Application Support/Claude/claude_desktop_config.json")
    if s == "Windows":
        return os.path.join(os.environ.get("APPDATA", home), "Claude", "claude_desktop_config.json")
    return os.path.join(home, ".config/Claude/claude_desktop_config.json")

def load_env(p):
    if not os.path.exists(p):
        sys.exit("ERROR: no encuentro el archivo .env en " + p +
                 "\nCopia .env.example a .env y rellena tus datos antes de ejecutar esto.")
    ev = {}
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        ev[k.strip()] = v.strip().strip('"').strip("'")
    return ev

def claude_running():
    import subprocess
    try:
        if platform.system() == "Darwin":
            r = subprocess.run(["pgrep","-f","/Applications/Claude.app"], capture_output=True, text=True)
            return r.returncode == 0 and r.stdout.strip() != ""
        if platform.system() == "Windows":
            r = subprocess.run(["tasklist"], capture_output=True, text=True)
            return "Claude.exe" in r.stdout
        r = subprocess.run(["pgrep","-x","claude"], capture_output=True, text=True)
        return r.returncode == 0
    except Exception:
        return False

def main():
    if claude_running() and "--force" not in sys.argv:
        sys.exit("ATENCION: Claude esta ABIERTO. Cierralo POR COMPLETO (Cmd+Q) y vuelve a\n"
                 "ejecutar este script. Si editas el conector con la app abierta, Claude\n"
                 "sobrescribe el cambio al guardar y el conector NO aparece.\n"
                 "(Si sabes lo que haces: repite con  --force )")
    ev = load_env(ENVP)
    faltan = [k for k in ("GOOGLE_CLIENT_ID","GOOGLE_CLIENT_SECRET","GOOGLE_REFRESH_TOKEN") if not ev.get(k)]
    if faltan:
        sys.exit("ERROR: faltan credenciales en el .env: " + ", ".join(faltan))

    py = os.path.join(HERE, ".venv/bin/python")
    if platform.system() == "Windows":
        py = os.path.join(HERE, ".venv/Scripts/python.exe")
    agent = os.path.join(HERE, "agent.py")
    if not os.path.exists(py):
        sys.exit("ERROR: no existe el entorno virtual. Ejecuta primero ./install.sh")
    if not os.path.exists(agent):
        sys.exit("ERROR: no encuentro agent.py en " + HERE)

    raw = ev.get("ALLOWED_DIRS", "~")
    partes = [os.path.realpath(os.path.expanduser(os.path.expandvars(x))) for x in raw.split(":") if x]
    allowed = ":".join(partes) if platform.system() != "Windows" else ";".join(partes)

    cfg = config_path()
    os.makedirs(os.path.dirname(cfg), exist_ok=True)
    if os.path.exists(cfg):
        shutil.copy2(cfg, cfg + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
        d = json.load(open(cfg, encoding="utf-8"))
    else:
        d = {}

    d.setdefault("mcpServers", {})["agente-drive"] = {
        "command": py,
        "args": [agent],
        "env": {
            "MCP_TRANSPORT": "stdio",
            "GOOGLE_CLIENT_ID": ev["GOOGLE_CLIENT_ID"],
            "GOOGLE_CLIENT_SECRET": ev["GOOGLE_CLIENT_SECRET"],
            "GOOGLE_REFRESH_TOKEN": ev["GOOGLE_REFRESH_TOKEN"],
            "ALLOWED_DIRS": allowed,
        },
    }
    json.dump(d, open(cfg, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("OK. Conector 'agente-drive' dado de alta.")
    print("  Config:        " + cfg)
    print("  Carpetas:      " + allowed)
    print("  Conectores:    " + ", ".join(d["mcpServers"].keys()))
    print("\nUltimo paso: CIERRA Claude por completo y vuelve a abrirlo.")

if __name__ == "__main__":
    main()
