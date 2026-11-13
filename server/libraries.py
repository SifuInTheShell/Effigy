"""Several library folders, one open at a time (switched in Setup or on the Library page).

settings.json keeps `libraries` (every folder the user added) and `library` (the open one); library.root()
reads it. EFFIGY_LIBRARY, when set, is the only library. Nothing here moves or deletes a file: adding opens a
folder (new, empty, or a library already), forgetting only takes it off the list.
"""

import os
from pathlib import Path

from server import jobs, library, settings, thumbs
from server.library import ApiError

DEFAULT = Path("library")


def _abs(p: Path | str) -> str:
    return os.path.abspath(p)


def _same(a: str, b: str) -> bool:
    return os.path.normcase(a) == os.path.normcase(b)


def _known() -> list[str]:
    out: list[str] = []
    for p in [_abs(DEFAULT), *settings.read().get("libraries", []), _abs(library.root())]:
        if not any(_same(p, q) for q in out):
            out.append(p)
    return out


def _count(p: str) -> int:
    return sum(1 for _ in Path(p).glob("*/asset.json")) if os.path.isdir(p) else 0


def folders() -> dict:
    """The open library's path, who chose it (env/settings/default) and every folder on the list with its
    asset count (`exists` false: a drive not plugged in, a folder deleted outside the app)."""
    now = _abs(library.root())
    env = bool(os.environ.get("EFFIGY_LIBRARY"))
    source = "env" if env else "settings" if settings.read().get("library") else "default"
    return {
        "path": now,
        "source": source,
        "folders": [
            {"path": p, "exists": os.path.isdir(p), "assets": _count(p)} for p in ([now] if env else _known())
        ],
    }


def _busy() -> bool:
    return any(j.status in ("queued", "running") for j in list(jobs.JOBS.values()))


def open_folder(path: str) -> dict:
    """Open `path` as the library (added to the list): a new or empty folder, or a library already."""
    if os.environ.get("EFFIGY_LIBRARY"):
        raise ApiError(409, "env", "EFFIGY_LIBRARY sets the library folder: change it there")
    if not path.strip() or not Path(path.strip()).is_absolute():
        raise ApiError(400, "bad_params", "choose a full path, e.g. E:\\Effigy-library")
    new = Path(_abs(path.strip()))
    if new == Path(new.anchor):
        raise ApiError(400, "bad_params", f"choose a folder on {new}, e.g. {new}Effigy-library")
    if _same(str(new), _abs(library.root())):
        return folders()
    models = Path(_abs(settings.models_root()))
    if new.is_relative_to(models) or models.is_relative_to(new):
        raise ApiError(400, "bad_params", f"{new} overlaps the models folder {models}: choose a folder of its own")
    if _busy():
        raise ApiError(409, "busy", "a job is running; switch the library after it ends")
    if new.is_dir() and any(new.iterdir()) and not _count(str(new)) and not (new / "_trash").is_dir():
        raise ApiError(409, "not_empty", f"{new} has other files: choose an empty, a new or a library folder")
    try:
        new.mkdir(parents=True, exist_ok=True)
        probe = new / ".effigy-write-test"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        raise ApiError(400, "not_writable", f"cannot write to {new}: {e}") from e
    known = _known()
    settings.write(library=str(new), libraries=[*known, str(new)] if not any(_same(str(new), k) for k in known) else known)
    jobs.evict()  # a resident worker started with the old EFFIGY_LIBRARY
    thumbs._failed.clear()  # keyed by asset id: another library may have the same ids
    jobs.publish("library", {"path": str(new)})
    return folders()


def forget(path: str) -> dict:
    """Take a folder off the list; its files stay. The open library cannot be forgotten."""
    p = _abs(path)
    if _same(p, _abs(library.root())):
        raise ApiError(409, "open", "this library is open: open another one first")
    if _same(p, _abs(DEFAULT)):
        raise ApiError(400, "bad_params", "the app's own library folder stays on the list")
    settings.write(libraries=[k for k in settings.read().get("libraries", []) if not _same(k, p)])
    return folders()
