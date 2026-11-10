"""The user's own settings (IMPLEMENTATION.md M35c): settings.json in the app folder, never committed.

`models`: the folder the model files go into, chosen in the first run or moved later in Setup (EFFIGY_MODELS,
when set, wins: a developer's shell, the tests). `welcomed`: the user started an install from the guided
setup or skipped it; until then, and while nothing is installed, the app opens it by itself. `port`: the port
the app serves on, chosen in Setup and read at start (EFFIGY_PORT, when set, wins). `blender_start`,
`blender_exe`: Open in Blender starts that Blender (the user's own, with the MCP add-on) when none answers
(server/blender_client.ensure). `developer`: Setup's Developer options (the workspace shows Run any step). EFFIGY_SETTINGS points at another file (the UI tests use their own).
"""

import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILE = Path(os.environ.get("EFFIGY_SETTINGS") or ROOT / "settings.json")
_cache: dict | None = None


def read() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {}
    return _cache


_lock = (
    threading.Lock()
)  # one write at a time: two at once lost one's values (audit 2026-10-10, BE-004)


def write(**values) -> None:
    global _cache
    with _lock:
        data = read() | values
        tmp = FILE.with_name(f"{FILE.name}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(FILE)  # readers never see half a file
        _cache = data


PORT = 8000  # the default; Unreal's MCP plugin uses it too, hence the choice in Setup


def port() -> int:
    """The port the app serves on, read at start: EFFIGY_PORT, else the one chosen in Setup, else 8000."""
    return int(os.environ.get("EFFIGY_PORT") or read().get("port") or PORT)


def port_source() -> str:
    """Who chose the port: env (EFFIGY_PORT), settings (the user, in Setup) or default."""
    return (
        "env" if os.environ.get("EFFIGY_PORT") else "settings" if read().get("port") else "default"
    )


def models_root() -> Path:
    if env := os.environ.get("EFFIGY_MODELS"):
        return Path(env)
    return Path(read().get("models") or ROOT / "models")


def models_source() -> str:
    """Who chose the models folder: env (EFFIGY_MODELS), settings (the user, in the app) or default."""
    return (
        "env"
        if os.environ.get("EFFIGY_MODELS")
        else "settings"
        if read().get("models")
        else "default"
    )
