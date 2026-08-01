#!/usr/bin/env python3
"""
Da de alta (o actualiza) el conector 'agente-drive' en la configuracion de
Claude Desktop, en modo stdio, leyendo las credenciales del .env de esta carpeta.
Uso:  python3 conectar_claude.py   (o:  ./conectar_claude.sh)

IMPORTANTE: hazlo con Claude COMPLETAMENTE CERRADO (Cmd+Q). Si la app esta abierta
sobrescribe el cambio al guardar y el conector no aparece. El script lo detecta y
se niega a actuar (excluyendo 'chrome-native-host' y 'crashpad', que no cuentan).
No sube nada a internet; solo edita el fichero local de Claude, con copia previa.
"""
import json, os, sys, shutil, time, platform, subprocess

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


def claude_procs():
    """Procesos de la APP de escritorio Claude que estan vivos. Excluye
    'chrome-native-host' (lo lanza Chrome, sobrevive al Cmd+Q) y el 'crashpad'."""
    procs = []
    try:
        sysname = platform.system()
        if sysname == "Windows":
            out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout
            return ["Claude.exe"] if "Claude.exe" in out else []
        out = subprocess.run(["ps", "-Ao", "command="], capture_output=True, text=True).stdout
        for line in out.splitlines():
            l = line.strip()
            if sysname == "Darwin":
                if "/Applications/Claude.app" not in l:
                    continue
            else:
                if "claude" not in l.lower() or ".app" not in l.lower():
                    continue
            if "chrome-native-host" in l or "crashpad" in l or "conectar_claude" in l:
                continue
            procs.append(l[:70])
    except Exception:
        pass
    return procs


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


def main():
    procs = claude_procs()
    if procs and "--force" not in sys.argv:
        muestra = "\n".join("   - " + x for x in procs[:6])
        sys.exit("ATENCION: Claude (app de escritorio) sigue ABIERTO. Cierralo del todo\n"
                 "con Cmd+Q, espera 3 segundos y repite. Procesos detectados:\n"
                 + muestra +
                 "\n(chrome-native-host y crashpad ya se ignoran. Con --force forzarias,\n"
                 " pero perderias el cambio al cerrar Claude.)")

    ev = load_env(ENVP)
    faltan = [k for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN") if not ev.get(k)]
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
    sep = ";" if platform.system() == "Windows" else ":"
    partes = [os.path.realpath(os.path.expanduser(os.path.expandvars(x))) for x in raw.split(":") if x]
    allowed = sep.join(partes)

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
    print("  Config:      " + cfg)
    print("  Carpetas:    " + allowed)
    print("  Conectores:  " + ", ".join(d["mcpServers"].keys()))
    print("\nUltimo paso: abre Claude y, en un chat nuevo, pide 'lista mis carpetas del Agente Drive'.")


if __name__ == "__main__":
    main()
