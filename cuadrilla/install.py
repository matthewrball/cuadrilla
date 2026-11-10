"""One command: binary, skill, MCP, launchd. Agents then see Cuadrilla."""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from cuadrilla.store import home

SOURCE = Path(__file__).resolve().parents[1]
SKILL = SOURCE / "skills" / "cuadrilla" / "SKILL.md"
LAUNCH_LABEL = "com.cuadrilla.serve"


def install(user_home: Path | None = None) -> int:
    base = user_home or Path.home()
    lib = home() if user_home is None else user_home / ".cuadrilla"
    # CUADRILLA_HOME redirects state. The install target is always the real home
    # unless the caller passes user_home for tests.
    if user_home is None:
        lib = Path.home() / ".cuadrilla"
    lib.mkdir(parents=True, exist_ok=True)
    dest = lib / "lib" / "cuadrilla"
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE / "cuadrilla", dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    skill_src = SKILL if SKILL.exists() else dest.parent.parent / "skills" / "cuadrilla" / "SKILL.md"
    installed_skill = lib / "skill" / "SKILL.md"
    installed_skill.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(skill_src, installed_skill)
    bin_dir = base / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    launcher = bin_dir / "cuadrilla"
    launcher.write_text(
        "#!/bin/sh\n"
        f'export PYTHONPATH="{lib / "lib"}"\n'
        'exec python3 -m cuadrilla "$@"\n'
    )
    launcher.chmod(0o755)
    skill_homes = [
        base / ".agents" / "skills" / "cuadrilla",
        base / ".claude" / "skills" / "cuadrilla",
        base / ".codex" / "skills" / "cuadrilla",
        base / ".hermes" / "skills" / "cuadrilla",
        base / ".hermes" / "external-skills" / "cuadrilla",
    ]
    for folder in skill_homes:
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(installed_skill, folder / "SKILL.md")
    notes = [f"launcher {launcher}"]
    notes.append(add_claude(base / ".claude.json", launcher))
    notes.append(add_codex(base / ".codex" / "config.toml", launcher))
    notes.append(add_cursor(base / ".cursor" / "mcp.json", launcher))
    notes.append(add_hermes(base / ".hermes" / "config.yaml", launcher))
    if sys.platform == "darwin" and user_home is None:
        notes.append(install_launchd(launcher, lib))
    print("\n".join(note for note in notes if note))
    return 0


def uninstall(user_home: Path | None = None) -> int:
    base = user_home or Path.home()
    launcher = base / ".local" / "bin" / "cuadrilla"
    if launcher.exists():
        launcher.unlink()
    for folder in (
        base / ".agents" / "skills" / "cuadrilla",
        base / ".claude" / "skills" / "cuadrilla",
        base / ".codex" / "skills" / "cuadrilla",
        base / ".hermes" / "skills" / "cuadrilla",
        base / ".hermes" / "external-skills" / "cuadrilla",
    ):
        if folder.exists():
            shutil.rmtree(folder)
    remove_claude(base / ".claude.json")
    remove_codex(base / ".codex" / "config.toml")
    remove_cursor(base / ".cursor" / "mcp.json")
    if sys.platform == "darwin" and user_home is None:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCH_LABEL}"], check=False, capture_output=True)
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_LABEL}.plist"
        if plist.exists():
            plist.unlink()
    print("removed cuadrilla launcher, skills, and MCP entries")
    return 0


def add_claude(path: Path, launcher: Path) -> str:
    if not path.exists():
        return "claude: no ~/.claude.json"
    _backup(path)
    data = json.loads(path.read_text())
    servers = data.setdefault("mcpServers", {})
    servers["cuadrilla"] = {"command": str(launcher), "args": ["mcp"]}
    path.write_text(json.dumps(data, indent=2) + "\n")
    return "claude: mcpServers.cuadrilla"


def remove_claude(path: Path) -> None:
    if not path.exists():
        return
    data = json.loads(path.read_text())
    servers = data.get("mcpServers")
    if isinstance(servers, dict) and "cuadrilla" in servers:
        servers.pop("cuadrilla")
        path.write_text(json.dumps(data, indent=2) + "\n")


def add_codex(path: Path, launcher: Path) -> str:
    if not path.exists():
        return "codex: no config.toml"
    text = path.read_text()
    if "[mcp_servers.cuadrilla]" in text:
        return "codex: already registered"
    _backup(path)
    block = (
        "\n[mcp_servers.cuadrilla]\n"
        f'command = "{launcher}"\n'
        'args = ["mcp"]\n'
    )
    path.write_text(text.rstrip() + "\n" + block)
    return "codex: mcp_servers.cuadrilla"


def remove_codex(path: Path) -> None:
    if not path.exists():
        return
    lines = path.read_text().splitlines(keepends=True)
    out = []
    skipping = False
    for line in lines:
        if line.startswith("[mcp_servers.cuadrilla]"):
            skipping = True
            continue
        if skipping and line.startswith("["):
            skipping = False
        if skipping:
            continue
        out.append(line)
    path.write_text("".join(out))


def add_cursor(path: Path, launcher: Path) -> str:
    if not path.exists():
        return "cursor: no mcp.json"
    _backup(path)
    data = json.loads(path.read_text())
    servers = data.setdefault("mcpServers", {})
    servers["cuadrilla"] = {"command": str(launcher), "args": ["mcp"]}
    path.write_text(json.dumps(data, indent=2) + "\n")
    return "cursor: mcpServers.cuadrilla"


def remove_cursor(path: Path) -> None:
    if not path.exists():
        return
    data = json.loads(path.read_text())
    servers = data.get("mcpServers")
    if isinstance(servers, dict) and "cuadrilla" in servers:
        servers.pop("cuadrilla")
        path.write_text(json.dumps(data, indent=2) + "\n")


def add_hermes(path: Path, launcher: Path) -> str:
    if not path.exists():
        return "hermes: no config.yaml"
    text = path.read_text()
    if "command: " in text and "cuadrilla" in text and "mcp" in text:
        return "hermes: already registered"
    _backup(path)
    block = (
        "\n# Cuadrilla crew. Added by cuadrilla install.\n"
        "mcp_servers:\n"
        "  cuadrilla:\n"
        f"    command: \"{launcher}\"\n"
        "    args: [\"mcp\"]\n"
    )
    path.write_text(text.rstrip() + "\n" + block)
    return "hermes: mcp_servers.cuadrilla"


def install_launchd(launcher: Path, lib: Path) -> str:
    agents = Path.home() / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    plist_path = agents / f"{LAUNCH_LABEL}.plist"
    plist = {
        "Label": LAUNCH_LABEL,
        "ProgramArguments": [str(launcher), "serve"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(lib / "serve.log"),
        "StandardErrorPath": str(lib / "serve.log"),
        "EnvironmentVariables": {"PATH": f"{Path.home() / '.local' / 'bin'}:/opt/homebrew/bin:/usr/bin:/bin"},
    }
    plist_path.write_bytes(plistlib.dumps(plist))
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LAUNCH_LABEL}"], check=False, capture_output=True)
    lock = lib / "serve.lock"
    if lock.exists():
        pid = lock.read_text().strip()
        if pid.isdigit():
            subprocess.run(["kill", pid], check=False, capture_output=True)
    sock = lib / "cuadrilla.sock"
    if sock.exists():
        sock.unlink()
    boot = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], check=False, capture_output=True, text=True)
    if boot.returncode != 0:
        return f"launchd: {boot.stderr.strip() or boot.returncode}"
    return "launchd: com.cuadrilla.serve"


def _backup(path: Path) -> None:
    dest = Path.home() / ".cuadrilla" / "backups"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest / (path.name + ".bak"))
