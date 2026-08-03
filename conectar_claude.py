#!/usr/bin/env python3
"""
Registra en Claude Desktop los DOS conectores locales del MCP Drive:
  - 'mcp-drive'    -> server.py  (conector COMPLETO: 45 comandos sobre tu Drive)
  - 'agente-drive' -> agent.py   (puente entre el disco fisico y Drive)
Ambos por stdio, con las credenciales del .env de esta carpeta.

IMPORTANTE: ejecutar con Claude COMPLETAMENTE CERRADO (si esta abierto, al cerrarse
sobrescribe el cambio). El script lo detecta y se niega si Claude esta abierto.
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
        sys.exit("ERROR: no encuentro el archivo .env en " + p)
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
                 "con Cmd+Q (o cerrar del todo en Windows), espera 3 segundos y repite.\n"
                 "Procesos detectados:\n" + muestra)

    ev = load_env(ENVP)
    faltan = [k for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN") if not ev.get(k)]
    if faltan:
        sys.exit("ERROR: faltan credenciales en el .env: " + ", ".join(faltan) +
                 "\nGenera el token primero (get_refresh_token.py).")

    if platform.system() == "Windows":
        py = os.path.join(HERE, ".venv/Scripts/python.exe")
    else:
        py = os.path.join(HERE, ".venv/bin/python")
    server = os.path.join(HERE, "server.py")
    agent = os.path.join(HERE, "agent.py")
    solo = ("--solo-agente" in sys.argv) or ("--agent-only" in sys.argv)
    requeridos = (py, agent) if solo else (py, server, agent)
    for f in requeridos:
        if not os.path.exists(f):
            sys.exit("ERROR: falta " + f + " (ejecuta antes el instalador).")

    raw = ev.get("ALLOWED_DIRS", "~") or "~"
    sep = ";" if platform.system() == "Windows" else ":"
    partes = [os.path.realpath(os.path.expanduser(os.path.expandvars(x)))
              for x in raw.split(":") if x.strip()]
    allowed = sep.join(partes) if partes else os.path.expanduser("~")

    creds = {
        "GOOGLE_CLIENT_ID": ev["GOOGLE_CLIENT_ID"],
        "GOOGLE_CLIENT_SECRET": ev["GOOGLE_CLIENT_SECRET"],
        "GOOGLE_REFRESH_TOKEN": ev["GOOGLE_REFRESH_TOKEN"],
    }

    cfg = config_path()
    os.makedirs(os.path.dirname(cfg), exist_ok=True)
    if os.path.exists(cfg):
        shutil.copy2(cfg, cfg + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
        d = json.load(open(cfg, encoding="utf-8"))
    else:
        d = {}
    d.setdefault("mcpServers", {})

    if not solo:
        d["mcpServers"]["mcp-drive"] = {
            "command": py, "args": [server],
            "env": dict(creds, MCP_TRANSPORT="stdio"),
        }
    d["mcpServers"]["agente-drive"] = {
        "command": py, "args": [agent],
        "env": dict(creds, MCP_TRANSPORT="stdio", ALLOWED_DIRS=allowed),
    }
    json.dump(d, open(cfg, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("OK. Conectores dados de alta: 'mcp-drive' (completo) y 'agente-drive' (puente).")
    print("  Config:     " + cfg)
    print("  Carpetas:   " + allowed)
    print("  Conectores: " + ", ".join(d["mcpServers"].keys()))
    print("\nAbre Claude y pide, por ejemplo, 'usa mcp-drive para listar mi unidad'.")


if __name__ == "__main__":
    main()
